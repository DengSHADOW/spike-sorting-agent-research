"""Minimal-prompt stepwise AND real-data rollout. Offline preflight unless --execute.

Only the approved text files become instructions. No legacy system/skill
builder is used. State transitions/scheduling inherit the audited controller;
request assembly, input allowlists, provenance and authorization are separate.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
from pathlib import Path
import signal
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import h5py
import numpy as np
from scipy.io import loadmat
from scripts.run import run_curation_stage6 as infra
from scripts.run import run_skill_replay as accounting
from src.agent.curation_observation_v1 import observe
from src.cluster.manager import ClusterManager
from src.pipeline.curation_engineering_v1 import CurationController, ControllerStop, arrays_hash, digest, write_json

BASE = ROOT / "zero-shot baseline"
PROMPTS = BASE / "prompts"
DATASETS = ("cM2-e004_004-006_CH31", "cM2-e004_004-006_CH3",
            "cM2-e004_011-015_CH20", "cM2-e008_021-028_CH30")
INDEX = infra.INDEX
SCOPE = "minimal-prompt-four-channel-stepwise-and-rollout-v1"
ACTIONS = {"phase1": ["KEEP", "SPLIT", "DISCARD"], "phase2": ["MERGE", "NOT_MERGE", "DISCARD"]}
COMMON = {"sampling_rate_hz", "waveform_window_ms", "refractory_ms"}
FIELDS = {"phase1": COMMON | {"cluster_id", "n_spikes", "n_overclusters", "isi_violation_rate", "amplitude_cv"},
          "phase2": COMMON | {"small_cluster_id", "large_cluster_id", "n_small", "n_large",
                               "small_isi_rate", "large_isi_rate", "merged_isi_rate", "correlation"}}
VIEWS = {"phase1": ["waveform", "isi", "amplitude", "tree"],
         "phase2": ["small_waveform", "large_waveform", "merged_isi"]}
sha256 = infra.sha256


def require_dataset(dataset):
    if dataset not in DATASETS:
        raise ValueError("Outside the four approved real-data channels")


def prompt_hashes():
    rows = [line.split() for line in (PROMPTS / "SHA256SUMS").read_text().splitlines() if line.strip()]
    if len(rows) != 2 or {r[1] for r in rows} != {"phase1.txt", "phase2.txt"}:
        raise ValueError("Unexpected prompt manifest")
    hashes = {name: sha256(PROMPTS / name) for _, name in rows}
    if hashes != {name: h for h, name in rows}:
        raise ValueError("Approved prompt hash mismatch")
    return hashes


def build_request(phase, obs, urls):
    prompt_hashes()
    if phase not in FIELDS or set(obs) != FIELDS[phase]:
        raise ValueError("Missing/unexpected observation field (labels are prohibited)")
    if len(urls) != len(VIEWS[phase]) or any(not u.startswith("data:image/png;base64,") for u in urls):
        raise ValueError("Expected local PNGs in the declared order")
    normalized = {}
    for key, value in sorted(obs.items()):
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError("Non-numeric observation")
        if key.endswith("_rate") and value is not None and not 0 <= value <= 1:
            raise ValueError("Invalid ISI fraction")
        normalized[key] = value
    if phase == "phase2" and obs["small_cluster_id"] == obs["large_cluster_id"]:
        raise ValueError("Self merge")
    definitions = {
        "ISI rates": "Fraction of adjacent sorted spike-time intervals shorter than refractory_ms; denominator N-1. null means unavailable.",
        "sampling_rate_hz": "Samples per second.",
        "waveform_window_ms": "Waveform window duration in milliseconds.",
        "waveform_amplitude": "Source units; not assumed to be microvolts.",
    }
    if phase == "phase1":
        definitions.update(amplitude_cv="Population standard deviation / mean of per-spike peak-to-trough amplitudes.",
                           n_overclusters="Number of initial subclusters currently represented.")
    else:
        definitions["correlation"] = "Pearson correlation of the two mean waveforms."
    observation = json.dumps(dict(metrics=normalized, definitions=definitions), sort_keys=True, allow_nan=False)
    schema = {"type": "object", "properties": {"action": {"type": "string", "enum": ACTIONS[phase]},
               "rationale": {"type": "string"}}, "required": ["action", "rationale"], "additionalProperties": False}
    return dict(model="gpt-6-astra", instructions=(PROMPTS / f"{phase}.txt").read_text(),
        input=[{"role": "user", "content": [{"type": "input_text", "text": observation},
               *[{"type": "input_image", "image_url": u, "detail": "high"} for u in urls]]}],
        reasoning={"effort": "high"}, max_output_tokens=4000, service_tier="default",
        text={"format": {"type": "json_schema", "name": "minimal_curation_decision", "strict": True, "schema": schema}})


def save_request(folder, request, pngs, display):
    """Save every model-visible character; replace base64 with hashed local PNG references."""
    clean = copy.deepcopy(request)
    for part, png, meta in zip(clean["input"][0]["content"][1:], pngs, display["images"]):
        if base64.b64decode(part["image_url"].split(",", 1)[1], validate=True) != png:
            raise ValueError("Payload PNG differs from observation")
        if hashlib.sha256(png).hexdigest() != meta["sha256"]:
            raise ValueError("Observation image hash mismatch")
        name = meta["name"] + ".png"
        with (folder / name).open("xb") as stream:
            stream.write(png)
        part["image_url"] = {"local_file": name, "sha256": meta["sha256"]}
    write_json(folder / "request_payload.json", clean)


def selected_rows(datasets):
    if not datasets or len(set(datasets)) != len(datasets):
        raise ValueError("Empty or duplicate datasets")
    for dataset in datasets:
        require_dataset(dataset)  # Reject scope before any data file is opened.
    index = {r["dataset_id"]: r for r in accounting.read_json(INDEX)["datasets"]}
    rows = []
    for dataset in datasets:
        row = {k: index[dataset][k] for k in ("dataset_id", "mat_path", "mat_sha256")}
        path = ROOT / row["mat_path"]
        if not path.resolve().is_relative_to(ROOT) or path.parent.name != dataset or sha256(path) != row["mat_sha256"]:
            raise ValueError("MAT provenance mismatch")
        rows.append(row)
    return rows


def load_actor(row):
    """Return only six allowed fields. MAT v5 decompression loads the struct locally,
    but its curation fields are never accessed or passed to the actor.
    """
    require_dataset(row["dataset_id"])
    path = ROOT / row["mat_path"]
    if sha256(path) != row["mat_sha256"]:
        raise ValueError("MAT changed")
    if h5py.is_hdf5(path):
        with h5py.File(path, "r") as f:
            fs = float(f["spikes/Fs"][0, 0])
            times = f["spikes/spiketimes"][:].flatten()
            waves = f["spikes/waveforms"][:]
            over = f["spikes/overcluster/assigns"][:].flatten()
            assigns = f["spikes/hierarchy/assigns"][:].flatten()
            tree = f["spikes/hierarchy/tree"][:]
    else:
        s = loadmat(str(path), struct_as_record=False, squeeze_me=True, variable_names=['spikes'])['spikes']
        fs = float(np.asarray(s.Fs).squeeze())
        times = np.asarray(s.spiketimes).reshape(-1).astype(float)
        waves = np.asarray(s.waveforms)
        over = np.asarray(s.overcluster.assigns).reshape(-1)
        assigns = np.asarray(s.hierarchy.assigns).reshape(-1)
        tree = np.asarray(s.hierarchy.tree)
        del s
    if waves.shape[0] != len(times):
        waves = waves.T
    if tree.shape[0] != 4 and tree.shape[1] == 4:
        tree = tree.T
    if waves.shape[0] != len(times) or assigns.shape != times.shape or over.shape != times.shape:
        raise ValueError("Array alignment mismatch")
    if not np.isfinite(fs) or fs <= 0 or not len(times):
        raise ValueError("Invalid sampling rate or empty input")
    for a in (assigns, over):
        if not np.isfinite(a).all() or np.any(a < 0) or np.any(a != np.floor(a)):
            raise ValueError("Invalid input assignments")
    return ClusterManager(assigns, over, tree, times, waves), fs


def sources():
    paths = list((ROOT / "src").rglob("*.py")) + [Path(__file__),
        ROOT / "scripts/run/run_skill_replay.py", ROOT / "scripts/run/run_curation_stage6.py",
        ROOT / "scripts/analysis/collect_curation_stage6.py", ROOT / "scripts/analysis/minimal_stepwise.py"]
    return {str(p.relative_to(ROOT)): sha256(p) for p in sorted(set(paths))}


class MinimalController(CurationController):
    """Inherit ONLY scheduler/actions/checkpoints; do not call the legacy initializer or ask."""
    def __init__(self, manager, dataset_id, output_root, *, provider, budget_usd, sampling_rate, max_calls=10000):
        require_dataset(dataset_id)
        if manager.history or not math.isfinite(budget_usd) or budget_usd < 0 or type(max_calls) is not int or max_calls < 1:
            raise ValueError("Fresh state and valid limits required")
        self.manager, self.provider, self.out = manager, provider, Path(output_root)
        self.out.mkdir(parents=True, exist_ok=False)
        self.dataset_id, self.fs, self.view = dataset_id, sampling_rate, "raw_member_v1"
        self.budget, self.max_calls = budget_usd, max_calls
        self.charged, self.calls, self.operations, self.seen = 0., 0, 0, set()
        self.status = "ready"
        self.input_hash = arrays_hash(manager.overcluster_assigns, manager.spike_times, manager.waveforms)
        self.frozen = prompt_hashes()
        write_json(self.out / "manifest.json", dict(version=SCOPE, dataset_id=dataset_id,
            prompt_hashes=self.frozen, input_sha256=self.input_hash, view=self.view, sampling_rate=sampling_rate,
            budget_usd=budget_usd, max_calls=max_calls, skill=None, legacy_system=False,
            auto_discard_threshold=0, final_minimum_threshold=0, small_cluster_threshold=4000,
            phase2_prevalidation="Inherited: same phase1 request; KEEP eligible, DISCARD applied, SPLIT preserved/ineligible"))
        self.checkpoint("initial")

    def ask(self, phase, cid, target=None, *, context=None):
        if self.status in {"stopped", "completed"}:
            raise ControllerStop("Controller finished")
        self.status = "running"
        try:
            key = (context or phase, int(cid), target, self.state_hash())
            if key in self.seen or self.calls >= self.max_calls:
                raise ControllerStop("Repeated state or safety call limit")
            if prompt_hashes() != self.frozen:
                raise ControllerStop("Prompt changed during run")
            obs, urls, pngs, display = observe(self.manager, phase, cid, target, sampling_rate=self.fs, view=self.view)
            request = build_request(phase, obs, urls)
            folder = self.out / f"step_{self.calls + 1:05d}"
            folder.mkdir()
            save_request(folder, request, pngs, display)
            write_json(folder / "request.json", dict(phase=phase, context=context, observation=obs,
                display=display, request_sha256=digest(request), state_sha256=key[-1]))
            n = self.provider.count(request)
            held = accounting.reserve(self.charged, self.budget, n, 4000)
            self.charged += held
            self.log("reserved", amount_usd=held, step=folder.name)
            self.calls += 1
            self.seen.add(key)
            raw = self.provider.respond(request)
            write_json(folder / "response.json", raw)
            cost = accounting.usage_cost(raw)
            self.charged += cost["conservative_usd"] - held
            self.log("usage", **cost, step=folder.name)
            if cost["conservative_usd"] > held or self.charged > self.budget:
                raise ControllerStop("Usage exceeded reservation")
            decision = accounting.decision_from_response(raw, phase)
            self.log("decision", phase=phase, cid=int(cid), target=target, **decision)
            print(f"{self.dataset_id} {self.calls} {phase} {cid}->{target} {decision['action']} ${self.charged:.4f}", flush=True)
            return decision
        except BaseException as exc:
            self.status = "stopped"
            self.log("ABSTAIN_stop", error_type=type(exc).__name__, phase=phase, cid=int(cid),
                     state_sha256=self.state_hash(), reason="No fallback, retry, or substitute action")
            raise ControllerStop(f"Stopped: {type(exc).__name__}") from exc


def output_path(value):
    path = Path(value).absolute()
    if path.resolve() != path or path.parent != BASE / "runs":
        raise ValueError("Use a new direct child of zero-shot baseline/runs")
    return path


def prepare(out, datasets=DATASETS):
    hashes, rows = prompt_hashes(), selected_rows(datasets)
    out.mkdir(parents=True, exist_ok=False)
    records = []
    for row in rows:
        m, fs = load_actor(row)
        active = list(map(int, m.get_active_clusters()))
        record = dict(row, initial_clusters=len(active), n_spikes=len(m.assigns), sampling_rate_hz=fs)
        preview = out / "preflight" / row["dataset_id"]
        preview.mkdir(parents=True)
        if active:
            obs, urls, pngs, display = observe(m, "phase1", active[0], sampling_rate=fs)
            request = build_request("phase1", obs, urls)
            save_request(preview, request, pngs, display)
            record["first_request_sha256"] = digest(request)
            write_json(preview / "observation.json", dict(observation=obs, display=display, request_sha256=digest(request)))
        records.append(record)
        print(f"PREFLIGHT {row['dataset_id']}: {len(active)} groups; no API", flush=True)
        del m
    from scripts.analysis import minimal_stepwise
    stepwise = minimal_stepwise.prepare(out, rows, sys.modules[__name__])
    manifest = dict(version=SCOPE, created_at=accounting.now(), datasets=records, prompt_hashes=hashes,
        stepwise=stepwise, execution_order=f"all {stepwise['total']} eligible expert-state decisions, then CH31/CH3/CH20/CH30 autonomous rollouts",
        code_hashes=sources(), environment=infra.environment(), index_sha256=sha256(INDEX),
        model="gpt-6-astra", effort="high", max_output_tokens=4000, service_tier="default", retries=0,
        skill=None, legacy_system=False, observation="raw_member_v1", max_calls_per_dataset=10000,
        scheduler="Inherited engineering_v1: recursive split, phase2 prevalidation and all eligible targets; no top-k/cache changes",
        auto_discard_threshold=0, final_minimum_threshold=0, small_cluster_threshold=4000,
        actor_reads="Fs, spiketimes, waveforms, overcluster/assigns, hierarchy/assigns, hierarchy/tree only",
        mat_v5_note="scipy decompresses the spikes struct locally; only the six allowlisted fields enter the actor; curation fields are not accessed",
        pricing=dict(accounting.PRICES, checked_on="2026-10-07"),
        evaluation="Completed terminals only; existing many-to-one spike matching. Exposed development data, not independent test.")
    write_json(out / "manifest.json", manifest)
    write_json(out / "preflight.json", dict(manifest_sha256=sha256(out / "manifest.json"), api_calls=0, status="offline-passed"))
    return manifest


def verify(out):
    m = accounting.read_json(out / "manifest.json")
    if m["version"] != SCOPE or accounting.read_json(out / "preflight.json")["manifest_sha256"] != sha256(out / "manifest.json"):
        raise ValueError("Manifest changed")
    if m["prompt_hashes"] != prompt_hashes() or m["code_hashes"] != sources() or m["environment"] != infra.environment():
        raise ValueError("Frozen prompt/code/dependencies changed")
    if m["index_sha256"] != sha256(INDEX):
        raise ValueError("Dataset index changed")
    rows = selected_rows([r["dataset_id"] for r in m["datasets"]])
    for name, h in m["stepwise"]["sources"].items():
        if sha256(ROOT / name) != h: raise ValueError("Stepwise source changed")
    if sha256(out / "stepwise_targets.jsonl") != m["stepwise"]["targets_sha256"]:
        raise ValueError("Stepwise scoring targets changed")
    if any(any(r[k] != saved[k] for k in r) for r, saved in zip(rows, m["datasets"])):
        raise ValueError("Dataset changed")
    return m


def authorize(out, budget):
    if type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0:
        raise ValueError("Explicit finite budget required")
    if (out / "execution_started.json").exists():
        raise ValueError("Already started; no automatic restart")
    path = out / "authorize_execute.json"
    if path.is_symlink():
        raise ValueError("Authorization symlink prohibited")
    a = accounting.read_json(path)
    if (type(a.get("budget_usd")) not in (int, float) or a["budget_usd"] != budget or a.get("scope") != SCOPE
            or a.get("manifest_sha256") != sha256(out / "manifest.json")):
        raise ValueError("Authorization mismatch")


def collect(out):
    from scripts.analysis.collect_curation_stage6 import evaluate, deletion_audit
    from scripts.analysis import minimal_stepwise
    m = verify(out)
    status = accounting.read_json(out / "execution_status.json")
    results = []
    for row in m["datasets"]:
        folder = out / "datasets" / row["dataset_id"]
        r = dict(dataset_id=row["dataset_id"], status="not-started", final_metrics=None)
        if (folder / "summary.json").exists():
            r.update(accounting.read_json(folder / "summary.json"))
            if r["status"] == "completed" and r["terminal_evaluation_allowed"]:
                checkpoint = folder / "terminal.npz"
                if sha256(checkpoint) != accounting.read_json(checkpoint.with_suffix(".json"))["sha256"]:
                    raise ValueError("Terminal corrupted")
                with np.load(checkpoint, allow_pickle=False) as a:
                    final = a["assigns"].copy()
                    if arrays_hash(final, a["tree"]) != r["final_sha256"]:
                        raise ValueError("Final state mismatch")
                actor, fs = load_actor(row)
                path = ROOT / row["mat_path"]
                if h5py.is_hdf5(path):
                    with h5py.File(path, "r") as f:
                        gt = f["spikes/curation/assigns"][:].flatten()
                else:
                    gt = np.asarray(loadmat(str(path), struct_as_record=False, squeeze_me=True,
                        variable_names=['spikes'])['spikes'].curation.assigns).reshape(-1)
                r["final_metrics"] = evaluate(final, gt, actor.spike_times, fs)
                r["no_curation_metrics"] = evaluate(actor.assigns, gt, actor.spike_times, fs)
                events = accounting.read_jsonl(folder / "events.jsonl")
                r["deletions_containing_expert_spikes"] = deletion_audit(folder, events, gt)
                del actor
        results.append(r)
    dest = BASE / "results" / out.name
    dest.mkdir(parents=True, exist_ok=False)
    stepwise = minimal_stepwise.collect(out, m["stepwise"], sys.modules[__name__])
    write_json(out / "score.json", dict(status=status, stepwise=stepwise, datasets=results, manifest_sha256=sha256(out / "manifest.json")))
    lines = ["# Minimal-prompt baseline", "", f"Run: `{out.name}`; status: **{status['status']}**.",
        "", "Astra / high; approved minimal prompts; no domain skill, examples or feedback. Exposed development channels.",
        "Existing controller scheduling, four Phase 1 images / three Phase 2 images; automatic size deletion disabled.",
        f"Manifest SHA256: `{sha256(out / 'manifest.json')}`.",
        f"Calls: {status['inference_calls']}; known standard estimate: ${status['standard_estimate_known_responses_usd']:.6f}; conservative usage/reservations: ${status['conservative_charged_or_reserved_usd']:.6f}.",
        "", "## Stepwise action evaluation", "",
        "Given expert pre-action states and source/target objects; predictions never advance this trajectory. No KEEP/NOT_MERGE reference labels; MERGE positives only.",
        f"Reference actions: {m['stepwise']['reference_steps']}; eligible: {m['stepwise']['total']}; pre-registered exclusions: {json.dumps(m['stepwise']['excluded'], ensure_ascii=False)}",
        "", "| Dataset | Valid/planned | Correct | Full accuracy | Valid-only accuracy |", "|---|---:|---:|---:|---:|"]
    for d, s in stepwise["by_dataset"].items():
        lines.append(f"| {d} | {s['valid']}/{s['planned']} | {s['correct']} | {s['accuracy']} | {s['accuracy_on_valid']} |")
    for phase, s in stepwise["by_phase"].items():
        lines += ["", f"### {phase}", "", f"Coverage: {s['valid']}/{s['planned']}; accuracy: {s['accuracy']}",
                  "", "| Action | Evaluated support | Precision | Recall | F1 |", "|---|---:|---:|---:|---:|"]
        for a, c in s["per_class"].items():
            lines.append(f"| {a} | {c['evaluated_support']} | {c['precision']} | {c['recall']} | {c['f1']} |")
        labels = list(s["confusion_matrix"])
        lines += ["", "Confusion matrix (expert rows, model columns):", "", "| Expert / Model | " + " | ".join(labels) + " |",
                  "|---|" + "---:|"*len(labels)]
        for a in labels: lines.append("| " + a + " | " + " | ".join(str(s["confusion_matrix"][a][b]) for b in labels) + " |")
    lines += ["", "## Autonomous rollout", "", "| Dataset | Status | Units | Spikes | P | R | F1 | Initial F1 |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        s = r["final_metrics"]
        values = (f"{s['n_curated_clusters']} | {s['n_retained_spikes']} | {s['overall_precision']:.4f} | {s['overall_recall']:.4f} | {s['overall_f1_score']:.4f} | {r['no_curation_metrics']['overall_f1_score']:.4f}" if s else "— | — | — | — | — | —")
        lines.append(f"| {r['dataset_id']} | {r['status']} | {values} |")
    lines += ["", f"Elapsed seconds: {status['seconds']:.1f}. Stop/error: `{status['error']}`.",
        f"Stepwise accounting at completion/stop: `{status.get('stepwise_accounting')}`. Remaining accounting belongs to rollout; shared total budget.",
        "Partial runs are not final scores. Matching is many-to-one; fragmentation can be under-penalized. Not a SOTA claim.",
        f"Local detailed records: `../../runs/{out.name}/score.json` and per-step images, responses, checkpoints and cost ledger.",
        "No comparison to old conditions is a prompt-only causal result. No automatic prompt update or retry."]
    with (dest / "REPORT.md").open("x") as f:
        f.write("\n".join(lines) + "\n")
    return results


def execute(out, budget):
    m = verify(out)
    authorize(out, budget)
    write_json(out / "execution_started.json", dict(at=accounting.now(), budget_usd=budget,
        manifest_sha256=sha256(out / "manifest.json"), authorization_sha256=sha256(out / "authorize_execute.json")))
    (out / "responses").mkdir()
    provider, completed, status, error = None, [], "stopped", None
    stepwise_accounting = None
    started = monotonic()
    try:
        provider = infra.LiveProvider(infra.make_client(), out, budget)
        from scripts.analysis import minimal_stepwise
        try:
            minimal_stepwise.execute(out, m["stepwise"], provider, sys.modules[__name__])
        finally:
            stepwise_accounting = dict(calls=provider.calls, conservative_usd=provider.charged,
                standard_usd=provider.standard, tokens=copy.deepcopy(provider.tokens), seconds=monotonic()-started)
        provider.expected_first = {r["dataset_id"]: r["first_request_sha256"] for r in m["datasets"] if "first_request_sha256" in r}
        for row in m["datasets"]:
            verify(out)
            actor, fs = load_actor(row)
            provider.dataset = row["dataset_id"]
            c = MinimalController(actor, row["dataset_id"], out / "datasets" / row["dataset_id"],
                provider=provider, budget_usd=max(0., budget-provider.charged), sampling_rate=fs)
            c.run()
            completed.append(row["dataset_id"])
            print(f"COMPLETE {row['dataset_id']}; campaign ${provider.charged:.6f}", flush=True)
            del c, actor
        status = "completed"
    except BaseException as exc:
        error = dict(type=type(exc).__name__, cause=type(exc.__cause__).__name__ if exc.__cause__ else None)
        raise
    finally:
        write_json(out / "execution_status.json", dict(status=status, error=error, completed_datasets=completed,
            inference_calls=provider.calls if provider else 0, tokens=provider.tokens if provider else {},
            conservative_charged_or_reserved_usd=provider.charged if provider else 0,
            standard_estimate_known_responses_usd=provider.standard if provider else 0,
            stepwise_accounting=stepwise_accounting, seconds=monotonic()-started, at=accounting.now()))
        collect(out)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-root", required=True)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--budget-usd", type=float)
    args = p.parse_args()
    out = output_path(args.output_root)
    if args.execute:
        def stop(signum, frame):
            raise KeyboardInterrupt("Explicit process stop")
        signal.signal(signal.SIGTERM, stop)
        execute(out, args.budget_usd)
    else:
        if args.budget_usd is not None:
            raise ValueError("Dry run needs no budget")
        prepare(out)
        print(f"OFFLINE PASSED; manifest {sha256(out / 'manifest.json')}")


if __name__ == "__main__":
    main()
