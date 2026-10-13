"""CH1-only recovery. Offline by default; completed responses never re-billed."""
from __future__ import annotations

import argparse
import base64
import copy
import json
import math
from pathlib import Path
import signal
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import numpy as np
from scripts.run import run_curation_stage6 as base
from scripts.analysis.audit_curation_stage6_partial import audit_ledger
from scripts.analysis.collect_curation_stage6 import evaluate, deletion_audit
from scripts.analysis import curation_one_to_one
from src.pipeline.curation_engineering_v1 import CurationController, ControllerStop, arrays_hash

DATASET = "cM2-e004_004-006_CH1"
SCOPE = "stage6-ch1-explicit-interruption-recovery-v1"
read = base.replay.read_json


class ReplayBoundary(Exception):
    pass


def original(source):
    """Only CH1 sample files are opened; other channels never execute."""
    m = read(source / "manifest.json")
    if m["version"] != "stage6-development-rollout-v1":
        raise ValueError("Expected original Stage 6 campaign")
    if read(source / "preflight.json")["manifest_sha256"] != base.sha256(source / "manifest.json"):
        raise ValueError("Original manifest changed")
    if any(base.sha256(ROOT / p) != h for p, h in m["code_hashes"].items()):
        raise ValueError("Original code changed")
    config = base.config_hashes()
    if any(config.get(p) != h for p, h in m["config_hashes"].items()):
        raise ValueError("Original config changed")
    if base.environment() != m["environment"] or base.sha256(base.INDEX) != m["index_sha256"]:
        raise ValueError("Original environment/index changed")
    row = next(x for x in m["datasets"] if x["dataset_id"] == DATASET)
    selected = base.selected_rows([DATASET])[0]
    if any(row[k] != selected[k] for k in selected):
        raise ValueError("CH1 data provenance changed")
    old = audit_ledger(source)
    n = old["settled_calls"]
    if (not n or old["reserved_calls"] != n+1 or old["unresolved_calls"] != [n+1]
            or old["saved_without_usage"] or old["failed_calls"] or old["count_without_reservation"]):
        raise ValueError("Expected exactly one unresolved trailing request")
    rows = base.replay.read_jsonl(source / "provider_ledger.jsonl")
    if any(x["dataset_id"] != DATASET for x in rows):
        raise ValueError("Only a CH1-only original prefix is supported")
    usages = {x["call"]: x for x in rows if x["event"] == "usage"}
    reservations = {x["call"]: x for x in rows if x["event"] == "reserved"}
    folder = source / "datasets" / DATASET
    decisions = [x for x in base.replay.read_jsonl(folder / "events.jsonl") if x["event"] == "decision"]
    if [x["calls"] for x in decisions] != list(range(1, n+1)):
        raise ValueError("Non-contiguous saved decisions")
    records = []
    for number in range(1, n+2):
        step = folder / f"step_{number:05d}"
        saved = read(step / "request.json")
        urls = []
        for item in saved["display"]["images"]:
            png = step / (item["name"] + ".png")
            if base.sha256(png) != item["sha256"]:
                raise ValueError("Original image hash mismatch")
            urls.append("data:image/png;base64," + base64.b64encode(png.read_bytes()).decode())
        request = base.build_request(saved["phase"], saved["observation"], urls, skill_version=m["skill"])
        request["service_tier"] = "default"
        if (base.digest(request) != saved["request_sha256"]
                or reservations[number]["request_sha256"] != saved["request_sha256"]):
            raise ValueError("Saved request/ledger hash mismatch")
        raw = None
        if number <= n:
            raw = read(step / "response.json")
            if raw != read(source / "responses" / f"{number:06d}.json"):
                raise ValueError("Saved response copies disagree")
            parsed = base.replay.decision_from_response(raw, saved["phase"])
            if any(decisions[number-1].get(k) != v for k, v in parsed.items()):
                raise ValueError("Saved decision differs from raw response")
            if any(not math.isclose(usages[number][k], v, abs_tol=1e-9)
                   for k, v in base.replay.usage_cost(raw).items()):
                raise ValueError("Saved usage differs from raw response")
        elif (step / "response.json").exists() or (source / "responses" / f"{number:06d}.json").exists():
            raise ValueError("Unresolved request unexpectedly has a response")
        records.append(dict(request=saved, raw=raw))
    old["original_budget_usd"] = read(source / "execution_started.json")["budget_usd"]
    old["tokens"] = {k: sum(x[k] for x in usages.values())
                    for k in ("input_tokens", "output_tokens", "cached_input_tokens")}
    return m, row, old, records


def inventory(source):
    paths = [source / p for p in ("manifest.json", "preflight.json", "execution_started.json", "provider_ledger.jsonl")]
    paths += list((source / "responses").glob("*.json"))
    paths += [p for p in (source / "datasets" / DATASET).rglob("*") if p.is_file()]
    return {str(p.relative_to(source)): base.sha256(p) for p in sorted(paths)}


class PrefixProvider:
    kind = "VERIFIED_LOCAL_PREFIX_THEN_CH1_LIVE"

    def __init__(self, records, live_factory=None):
        self.records, self.live_factory = records, live_factory
        self.live, self.index, self.pending = None, 0, None
        self.boundary_verified = False

    def count(self, request):
        if self.pending is not None:
            raise ValueError("Unsettled cached request")
        if self.index < len(self.records):
            record = self.records[self.index]
            if base.digest(request) != record["request"]["request_sha256"]:
                raise ValueError("Reconstructed request differs from saved request")
            if record["raw"] is not None:
                self.pending = base.digest(request)
                return record["raw"]["usage"]["input_tokens"]
            self.boundary_verified = True
            if self.live_factory is None:
                raise ReplayBoundary("Offline prefix and unresolved request verified")
        if self.live is None:
            if self.live_factory is None or not self.boundary_verified:
                raise ValueError("Live access before verified replay boundary")
            self.live = self.live_factory()
        return self.live.count(request)

    def respond(self, request):
        if self.index < len(self.records) and self.records[self.index]["raw"] is not None:
            if self.pending != base.digest(request):
                raise ValueError("Cached request changed after count")
            raw = copy.deepcopy(self.records[self.index]["raw"])
            self.pending = None
        else:
            if self.live is None:
                raise ValueError("No live provider")
            raw = self.live.respond(request)
            print(f"CH1 decision {self.index+1}; new API calls {self.live.calls-len(self.records)}; "
                  f"campaign USD {self.live.charged:.6f}", flush=True)
        self.index += 1
        return raw


class PrefixController(CurationController):
    def ask(self, phase, cid, target=None, *, context=None):
        p = self.provider
        if p.index < len(p.records):
            saved = p.records[p.index]["request"]
            obs = saved["observation"]
            if (phase, int(cid), target, context, self.state_hash()) != (
                    saved["phase"], obs.get("cluster_id", obs.get("small_cluster_id")),
                    obs.get("large_cluster_id"), saved.get("context"), saved["state_sha256"]):
                raise ValueError("Reconstructed scheduler/state differs from saved prefix")
        result = super().ask(phase, cid, target, context=context)
        if self.calls <= len(p.records)-1 and self.calls % 50 == 0:
            print(f"LOCAL REPLAY {self.calls}/{len(p.records)-1}; no API", flush=True)
        return result


def controller(row, folder, provider, budget, old, manifest):
    actor, fs = base.load_actor(row)
    # Controller accounts for prefix virtually once; live ledger carries old cost.
    return PrefixController(actor, DATASET, folder, provider=provider,
        budget_usd=budget-old["unknown_reservation_usd"], max_calls=manifest["max_calls_per_dataset"],
        sampling_rate=fs, skill=manifest["skill"], view=manifest["view"])


def new_code_hashes():
    return {str(p.relative_to(ROOT)): base.sha256(p) for p in (
        Path(__file__), ROOT / "scripts/analysis/audit_curation_stage6_partial.py",
        ROOT / "scripts/analysis/curation_one_to_one.py")}


def prepare(source, out):
    m, row, old, records = original(source)
    before = inventory(source)
    out.mkdir(parents=True, exist_ok=False)
    p = PrefixProvider(records)
    c = controller(row, out / "offline_prefix", p, old["original_budget_usd"], old, m)
    try:
        c.run()
    except ControllerStop as exc:
        if not isinstance(exc.__cause__, ReplayBoundary):
            raise
    else:
        raise ValueError("Expected unresolved decision boundary")
    if p.index != old["settled_calls"] or not p.boundary_verified:
        raise ValueError("Prefix reconstruction incomplete")
    checkpoint = source / "datasets" / DATASET / f"operation_{c.operations:05d}_after.npz"
    if base.sha256(checkpoint) != read(checkpoint.with_suffix(".json"))["sha256"]:
        raise ValueError("Original checkpoint changed")
    with np.load(checkpoint, allow_pickle=False) as a, np.load(c.out / "stopped.npz", allow_pickle=False) as b:
        if set(a.files) != set(b.files) or any(not np.array_equal(a[k], b[k]) for k in a.files):
            raise ValueError("Full recovered state differs from original checkpoint")
    if before != inventory(source):
        raise ValueError("Source changed during preparation")
    base.write_json(out / "manifest.json", dict(version=SCOPE, source=str(source), source_files=before,
        recovery_code_hashes=new_code_hashes(), config_hashes=base.config_hashes(), dataset_order=[DATASET],
        cumulative_budget_usd=old["original_budget_usd"], inherited=old, prefix_calls_reused=p.index,
        explicitly_reissued_unresolved_call=p.index+1, first_new_provider_call=old["reserved_calls"]+1,
        model=m["model"], skill=m["skill"], view=m["view"], boundary_state_sha256=c.state_hash(),
        boundary_request_sha256=records[-1]["request"]["request_sha256"], automatic_retries=0,
        proof="Every saved request/state reconstructed identically; full checkpoint arrays identical. Old unknown reserve retained.",
        evaluation="Historical many-to-one plus separately labeled frozen one-to-one; completed CH1 only",
        remaining_original_channels="excluded by user; not started"))
    base.write_json(out / "preflight.json", dict(status="passed-offline", api_calls=0,
        manifest_sha256=base.sha256(out / "manifest.json")))
    print(f"OFFLINE RECOVERY PASSED: {p.index} responses reused; CH1 only", flush=True)


def verify(out):
    m = read(out / "manifest.json")
    if m["version"] != SCOPE or m["dataset_order"] != [DATASET]:
        raise ValueError("Unexpected recovery scope")
    if read(out / "preflight.json")["manifest_sha256"] != base.sha256(out / "manifest.json"):
        raise ValueError("Recovery manifest changed")
    source = base.output_path(m["source"])
    if (new_code_hashes() != m["recovery_code_hashes"] or base.config_hashes() != m["config_hashes"]
            or inventory(source) != m["source_files"]):
        raise ValueError("Frozen recovery code/config/source changed")
    return m, source, original(source)


def authorize(out, budget, m):
    if type(budget) not in (int, float) or budget != m["cumulative_budget_usd"]:
        raise ValueError("Must retain the original cumulative budget")
    if (out / "execution_started.json").exists():
        raise ValueError("Recovery already started; no automatic rerun")
    a = read(out / "authorize_execute.json")
    if (a.get("scope") != SCOPE or a.get("dataset_id") != DATASET or a.get("budget_usd") != budget
            or a.get("manifest_sha256") != base.sha256(out / "manifest.json")
            or a.get("retry_unresolved_call") != m["explicitly_reissued_unresolved_call"]):
        raise ValueError("Recovery authorization mismatch")


def collect(out, row, status):
    folder = out / "datasets" / DATASET
    summary = read(folder / "summary.json") if (folder / "summary.json").exists() else {"status": "not-started"}
    result = dict(status=status, dataset_id=DATASET, controller=summary, final_metrics=None,
                  note="CH1 development validation only; other five channels excluded.")
    if summary["status"] == "completed" and summary["terminal_evaluation_allowed"]:
        checkpoint = folder / "terminal.npz"
        if base.sha256(checkpoint) != read(checkpoint.with_suffix(".json"))["sha256"]:
            raise ValueError("Terminal checkpoint changed")
        with np.load(checkpoint, allow_pickle=False) as a:
            final = a["assigns"].copy()
            if arrays_hash(final, a["tree"]) != summary["final_sha256"]:
                raise ValueError("Terminal state mismatch")
        data = base.load_matlab_spikes(str(ROOT / row["mat_path"]))
        gt = data["curation_assigns"]
        result["final_metrics"] = evaluate(final, gt, data["spiketimes"], data["Fs"])
        result["no_curation_metrics"] = evaluate(data["hierarchy_assigns"], gt, data["spiketimes"], data["Fs"])
        result["one_to_one"] = curation_one_to_one.evaluate(final, gt)
        result["no_curation_one_to_one"] = curation_one_to_one.evaluate(data["hierarchy_assigns"], gt)
        result["deletions_containing_expert_spikes"] = deletion_audit(
            folder, base.replay.read_jsonl(folder / "events.jsonl"), gt)
    base.write_json(out / "score.json", result)
    lines = ["# Stage 6 CH1 continuation", "", result["note"], "",
        f"Status: {status['status']}; reused decisions: {status['reused_decisions']}; new inference attempts: {status['new_inference_calls']}.",
        f"New conservative charges/reservations: USD {status['new_conservative_usd']:.6f}; cumulative: USD {status['conservative_charged_or_reserved_usd']:.6f} / {status['budget_usd']:.2f}.",
        f"Inherited unknown reserve: USD {status['inherited_unknown_reservation_usd']:.6f} (not a confirmed charge); standard usage estimate: USD {status['standard_estimate_known_responses_usd']:.6f}.", "",
        "Saved completed decisions were not re-billed. " +
        ("The original missing response was explicitly reissued once; the interruption is disclosed."
         if status["new_inference_calls"] else "No new inference was sent in this segment.")]
    if result["final_metrics"] is not None:
        f, n = result["final_metrics"], result["no_curation_metrics"]
        o, no = result["one_to_one"], result["no_curation_one_to_one"]
        lines += ["", "| Matching | Initial P / R / F1 | Final P / R / F1 |", "|---|---|---|",
            f"| Historical many-to-one | {n['overall_precision']:.4f} / {n['overall_recall']:.4f} / {n['overall_f1_score']:.4f} | {f['overall_precision']:.4f} / {f['overall_recall']:.4f} / {f['overall_f1_score']:.4f} |",
            "| One-to-one, IoU >= 0.5, all outputs | " + " / ".join(f"{no['full_output'][k]:.4f}" for k in ('precision','recall','f1')) + " | " + " / ".join(f"{o['full_output'][k]:.4f}" for k in ('precision','recall','f1')) + " |", "",
            f"Final units: {f['n_curated_clusters']}; retained spikes: {f['n_retained_spikes']}; expert spikes discarded: {f['expert_spikes_discarded']}; reference noise retained: {f['noise_spikes_retained']}.",
            f"Matched expert units: {o['n_matched_units']}/{o['n_expert_units']}; unmatched predictions: {o['n_unmatched_predicted_units']}.",
            "No stepwise accuracy inferred from autonomous rollout. Model reasons are not verified causal explanations."]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def execute(out, budget):
    m, source, (manifest, row, old, records) = verify(out)
    authorize(out, budget, m)
    base.write_json(out / "execution_started.json", dict(at=base.replay.now(), budget_usd=budget,
        manifest_sha256=base.sha256(out / "manifest.json"), authorization_sha256=base.sha256(out / "authorize_execute.json")))
    (out / "responses").mkdir()
    def make_live():
        live = base.LiveProvider(base.make_client(), out, budget)
        live.dataset = DATASET
        live.charged = old["known_plus_reserved_usd"]
        live.standard = old["standard_known_usd"]
        live.tokens = copy.deepcopy(old["tokens"])
        live.calls = old["reserved_calls"]
        live.expected_first = {DATASET: m["boundary_request_sha256"]}
        return live
    p = PrefixProvider(records, make_live)
    started, status, error = monotonic(), "stopped", None
    try:
        c = controller(row, out / "datasets" / DATASET, p, budget, old, manifest)
        c.run()
        status = "completed"
    except BaseException as exc:
        error = dict(type=type(exc).__name__, cause=type(exc.__cause__).__name__ if exc.__cause__ else None,
                     status_code=getattr(exc.__cause__ or exc, "status_code", None))
        raise
    finally:
        live = p.live
        total = live.charged if live else old["known_plus_reserved_usd"]
        events_path = out / "datasets" / DATASET / "events.jsonl"
        decisions = sum(x["event"] == "decision" for x in base.replay.read_jsonl(events_path)) if events_path.exists() else 0
        result = dict(status=status, error=error, dataset_id=DATASET, budget_usd=budget,
            reused_decisions=min(decisions, old["settled_calls"]), successful_decisions=decisions,
            new_inference_calls=live.calls-old["reserved_calls"] if live else 0,
            total_inference_attempts=live.calls if live else old["reserved_calls"],
            conservative_charged_or_reserved_usd=total, new_conservative_usd=total-old["known_plus_reserved_usd"],
            inherited_unknown_reservation_usd=old["unknown_reservation_usd"],
            standard_estimate_known_responses_usd=live.standard if live else old["standard_known_usd"],
            tokens=live.tokens if live else old["tokens"], seconds_this_segment=monotonic()-started,
            automatic_retries=0, other_channels_started=0, at=base.replay.now())
        base.write_json(out / "execution_status.json", result)
        collect(out, row, result)
        print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--budget-usd", type=float)
    args = parser.parse_args()
    source, out = base.output_path(args.source), base.output_path(args.output_root)
    if source == out or out.is_relative_to(source) or source.is_relative_to(out):
        raise ValueError("Use a separate sibling recovery directory")
    if args.execute:
        if read(out / "manifest.json")["source"] != str(source):
            raise ValueError("Source argument mismatch")
        def stop(signum, frame):
            raise KeyboardInterrupt("Explicit stop")
        signal.signal(signal.SIGTERM, stop)
        execute(out, args.budget_usd)
    else:
        if args.budget_usd is not None:
            raise ValueError("Preparation does not authorize inference")
        prepare(source, out)


if __name__ == "__main__":
    main()
