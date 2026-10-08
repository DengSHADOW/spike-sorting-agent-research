"""Stage 6: offline preflight by default; explicit, hash-bound paid rollout.

This wraps (does not alter) the stage-4 controller. No labels enter its provider.
An authorization file binds a NEW budget to this manifest, not past replay runs.
No automatic restart/resume, provider retry, mock fallback, or channel rescue.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
from scripts.run import run_skill_replay as replay
from src.agent.curation_contract import build_request, config_hashes, load_protocol, require_development, sha256
from src.agent.curation_observation_v1 import observe
from src.cluster.manager import ClusterManager
from src.io.matlab_loader import load_matlab_spikes
from src.pipeline.curation_engineering_v1 import CurationController, digest, write_json

DATASETS = ("cM2-e004_004-006_CH1", "cM2-e004_004-006_CH3", "cM2-e004_004-006_CH31",
            "cM2-e004_011-015_CH17", "cM2-e004_011-015_CH20", "cM2-e008_021-028_CH30")
INDEX = ROOT / "output/real_split_manifest_20260903/manifest.json"
PACKAGES = ("numpy", "scipy", "matplotlib", "h5py", "spikeinterface", "openai", "httpx2", "python-dotenv")


def environment():
    return dict(python=platform.python_version(), packages={p: importlib.metadata.version(p) for p in PACKAGES})


def sources():
    paths = list((ROOT / "src").rglob("*.py")) + [Path(__file__),
        ROOT / "scripts/run/run_skill_replay.py", ROOT / "scripts/analysis/collect_curation_stage6.py"]
    return {str(p.relative_to(ROOT)): sha256(p) for p in sorted(set(paths))}


def output_path(value):
    path = Path(value).absolute()
    if path.resolve() != path or path == ROOT / "output" or not path.is_relative_to(ROOT / "output"):
        raise ValueError("Use a non-symlink child of project output/")
    return path


def selected_rows(datasets):
    if not datasets or len(set(datasets)) != len(datasets):
        raise ValueError("Empty or duplicate dataset selection")
    # Validate ALL scopes before opening ANY MAT or sample.
    for dataset in datasets:
        require_development(dataset)
        if dataset not in DATASETS:
            raise ValueError("Dataset outside this stage-6 development campaign")
    rows = {r["dataset_id"]: r for r in replay.read_json(INDEX)["datasets"]}
    result = []
    for dataset in datasets:
        row = rows[dataset]
        path = ROOT / row["mat_path"]
        if not path.resolve().is_relative_to(ROOT) or path.resolve().parent.name != dataset:
            raise ValueError("MAT path does not match allowed dataset")
        if sha256(path) != row["mat_sha256"]:
            raise ValueError("MAT hash mismatch")
        result.append({k: row[k] for k in ("dataset_id", "mat_path", "mat_sha256")})
    return result


def load_actor(row):
    require_development(row["dataset_id"])
    path = ROOT / row["mat_path"]
    if sha256(path) != row["mat_sha256"]:
        raise ValueError("MAT changed")
    data = load_matlab_spikes(str(path))
    # Loader reads curation fields, but none are returned to actor/controller.
    m = ClusterManager(data["hierarchy_assigns"], data["overcluster_assigns"],
                       data["hierarchy_tree"].copy(), data["spiketimes"], data["waveforms"])
    if (not len(m.assigns) or not np.isfinite(m.assigns).all()
            or np.any(m.assigns < 0) or np.any(m.assigns != np.floor(m.assigns))):
        raise ValueError("Invalid initial assignments")
    return m, float(data["Fs"])


def prepare(out, datasets=DATASETS, max_calls=10000):
    if type(max_calls) is not int or max_calls < 1:
        raise ValueError("Positive per-dataset safety call limit required")
    rows = selected_rows(datasets)
    out.mkdir(parents=True, exist_ok=False)
    records = []
    for row in rows:
        m, fs = load_actor(row)
        active = list(map(int, m.get_active_clusters()))
        record = dict(row, n_spikes=len(m.assigns), initial_clusters=len(active), sampling_rate_hz=fs)
        # Only preview FIRST initial state; never invent a future model trajectory.
        if active:
            obs, urls, pngs, display = observe(m, "phase1", active[0], sampling_rate=fs, view="raw_member_v1")
            req = build_request("phase1", obs, urls, skill_version="v0")
            req["service_tier"] = "default"
            preview = out / "preflight" / row["dataset_id"]
            preview.mkdir(parents=True)
            for item, png in zip(display["images"], pngs):
                (preview / f"{item['name']}.png").write_bytes(png)
            record["first_request_sha256"] = digest(req)
            write_json(preview / "observation.json", dict(observation=obs, display=display,
                       request_sha256=digest(req), sendable=False))
        records.append(record)
        print(f"PREFLIGHT {row['dataset_id']}: {len(active)} initial clusters; no API", flush=True)
        del m
    manifest = dict(version="stage6-development-rollout-v1", mode="prepared-not-executed",
        datasets=records, dataset_order=list(datasets), config_hashes=config_hashes(), code_hashes=sources(),
        index_sha256=sha256(INDEX), environment=environment(), model=load_protocol()["model"],
        skill="v0", view="raw_member_v1", service_tier="default", max_calls_per_dataset=max_calls,
        pricing=replay.PRICES, automatic_retries=0, api_calls=0,
        execution_contract={"auto_discard_threshold": 0, "final_minimum_threshold": 0,
            "small_cluster_threshold": 4000, "not_merge": "preserve", "channel_specific_guards": False,
            "phase1_images": 4, "phase2_images": 3, "ground_truth_to_actor": False,
            "stop_campaign_on_any_failure": True, "budget_scope": "all listed datasets combined",
            "resume": "not implemented; never rerun a started directory",
            "note": "protocol_v1 retains historical preparation metadata; this manifest defines live rollout/regenerated observations"},
        evaluation="Completed terminal states only; same-index max-overlap many-to-one P/R/F1; no-curation comparison; not action accuracy",
        limitations=["Development data, not untouched test data", "Full request count and cost are trajectory-dependent",
            "raw_member_v1 selected without evidence of summary superiority; not proven optimal",
            "All source code/config/dependencies fixed; no tuning inside a run"])
    write_json(out / "manifest.json", manifest)
    write_json(out / "preflight.json", dict(status="passed-offline", api_calls=0, cost_usd=0,
        manifest_sha256=sha256(out / "manifest.json"), datasets=len(records),
        n_spikes=sum(r["n_spikes"] for r in records), initial_clusters=sum(r["initial_clusters"] for r in records)))
    return manifest


def verify(out):
    manifest = replay.read_json(out / "manifest.json")
    if replay.read_json(out / "preflight.json")["manifest_sha256"] != sha256(out / "manifest.json"):
        raise ValueError("Manifest changed")
    if manifest["config_hashes"] != config_hashes() or manifest["code_hashes"] != sources():
        raise ValueError("Frozen config/code changed; prepare a new run")
    if manifest["environment"] != environment() or manifest["index_sha256"] != sha256(INDEX):
        raise ValueError("Dependencies or dataset index changed")
    rows = selected_rows(manifest["dataset_order"])
    for a, b in zip(rows, manifest["datasets"]):
        if any(a[k] != b[k] for k in a):
            raise ValueError("Dataset provenance mismatch")
    return manifest


def authorize(out, budget):
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget) or budget <= 0:
        raise ValueError("An explicit positive, finite total budget is required")
    if (out / "execution_started.json").exists():
        raise ValueError("Run already started; no automatic resume or restart")
    auth = replay.read_json(out / "authorize_execute.json")
    if (type(auth.get("budget_usd")) not in (int, float) or auth["budget_usd"] != budget
            or auth.get("manifest_sha256") != sha256(out / "manifest.json")
            or auth.get("scope") != "stage6-development-full-rollout"):
        raise ValueError("Missing/mismatched stage-6 budget authorization")


def append(out, row):
    with (out / "provider_ledger.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


class LiveProvider:
    """Controller reserves per channel; this second durable gate caps the campaign."""
    kind = "OPENAI_RESPONSES_LIVE_NO_RETRIES"

    def __init__(self, client, out, budget):
        self.client, self.out, self.budget = client, out, budget
        self.charged, self.standard, self.calls = 0., 0., 0
        self.tokens = dict(input_tokens=0, output_tokens=0, cached_input_tokens=0)
        self.pending = None
        self.dataset = None
        self.expected_first = {}
        self.started_datasets = set()

    def count(self, request):
        if self.pending is not None:
            raise ValueError("Unsettled previous request")
        if request.get("model") != "gpt-6-astra" or request.get("reasoning") != {"effort": "high"}:
            raise ValueError("Unexpected model/effort")
        if request.get("service_tier") != "default" or request.get("max_output_tokens") != 4000:
            raise ValueError("Unexpected tier/output limit")
        if self.dataset not in self.started_datasets and self.dataset in self.expected_first:
            if digest(request) != self.expected_first[self.dataset]:
                raise ValueError("Initial live request differs from offline preflight")
        started = monotonic()
        n = replay.token_count(self.client, request)
        held = replay.reserve(self.charged, self.budget, n, request["max_output_tokens"])
        self.pending = (digest(request), held)
        append(self.out, dict(event="count", dataset_id=self.dataset, request_sha256=digest(request),
                             input_tokens=n, seconds=monotonic()-started, at=replay.now()))
        return n

    def respond(self, request):
        if self.pending is None or self.pending[0] != digest(request):
            raise ValueError("Request differs from counted request")
        request_hash, held = self.pending
        self.charged += held
        self.calls += 1
        self.started_datasets.add(self.dataset)
        append(self.out, dict(event="reserved", dataset_id=self.dataset, call=self.calls,
            request_sha256=request_hash, reserved_usd=held, cumulative_usd=self.charged, at=replay.now()))
        started = monotonic()
        try:
            raw = self.client.responses.create(**request).model_dump(mode="json")
            # Persist BEFORE parsing/settlement; preserve incomplete responses too.
            write_json(self.out / "responses" / f"{self.calls:06d}.json", raw)
            cost = replay.usage_cost(raw)
            self.charged += cost["conservative_usd"] - held
            self.standard += cost["standard_estimate_usd"]
            for key in self.tokens:
                self.tokens[key] += cost[key]
            self.pending = None
            append(self.out, dict(event="usage", dataset_id=self.dataset, call=self.calls,
                **cost, cumulative_usd=self.charged, seconds=monotonic()-started, at=replay.now()))
            if cost["conservative_usd"] > held or self.charged > self.budget:
                raise ValueError("Reported usage exceeded reservation; stopping")
            return raw
        except BaseException as exc:
            append(self.out, dict(event="failure", dataset_id=self.dataset, call=self.calls,
                error_type=type(exc).__name__, status_code=getattr(exc, "status_code", None),
                seconds=monotonic()-started, at=replay.now(), automatic_retries=0))
            raise


def make_client():
    # Never used on imports, dry-run, verification or failed authorization.
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    return replay.client_factory()


def execute(out, budget):
    manifest = verify(out)
    authorize(out, budget)  # all local gates BEFORE keys/client/network
    write_json(out / "execution_started.json", dict(at=replay.now(), budget_usd=budget,
        manifest_sha256=sha256(out / "manifest.json"), authorization_sha256=sha256(out / "authorize_execute.json")))
    (out / "responses").mkdir()
    provider = None
    completed = []
    status, error = "stopped", None
    started = monotonic()
    try:
        provider = LiveProvider(make_client(), out, budget)
        provider.expected_first = {r["dataset_id"]: r["first_request_sha256"]
                                   for r in manifest["datasets"] if "first_request_sha256" in r}
        for row in manifest["datasets"]:
            verify(out)
            m, fs = load_actor(row)
            provider.dataset = row["dataset_id"]
            controller = CurationController(m, row["dataset_id"], out / "datasets" / row["dataset_id"],
                provider=provider, budget_usd=max(0., budget-provider.charged),
                max_calls=manifest["max_calls_per_dataset"], sampling_rate=fs,
                skill=manifest["skill"], view=manifest["view"])
            controller.run()
            completed.append(row["dataset_id"])
            print(f"COMPLETE {row['dataset_id']}: {controller.calls} calls; campaign ${provider.charged:.6f}", flush=True)
            del controller, m
        status = "completed"
    except BaseException as exc:
        error = dict(error_type=type(exc).__name__, cause_type=type(exc.__cause__).__name__ if exc.__cause__ else None,
                     status_code=getattr(exc.__cause__ or exc, "status_code", None))
        raise
    finally:
        write_json(out / "execution_status.json", dict(status=status, error=error, completed_datasets=completed,
            planned_datasets=manifest["dataset_order"], inference_calls=provider.calls if provider else 0,
            conservative_charged_or_reserved_usd=provider.charged if provider else 0,
            standard_estimate_known_responses_usd=provider.standard if provider else 0,
            tokens=provider.tokens if provider else {}, seconds=monotonic()-started, at=replay.now(),
            all_datasets_terminal=status == "completed", automatic_retries=0))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--datasets", nargs="+", default=list(DATASETS))
    parser.add_argument("--max-calls-per-dataset", type=int, default=10000)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--budget-usd", type=float)
    args = parser.parse_args(argv)
    out = output_path(args.output_root)
    if args.execute:
        if args.datasets != list(DATASETS) or args.max_calls_per_dataset != 10000:
            raise ValueError("Execution uses prepared manifest scope/limit; omit preparation overrides")
        try:
            execute(out, args.budget_usd)
        finally:
            # Offline postprocessing even when the campaign stops early.
            if (out / "execution_status.json").exists() and not (out / "analysis").exists():
                from scripts.analysis.collect_curation_stage6 import collect
                collect(out, out / "analysis")
    else:
        if args.budget_usd is not None:
            raise ValueError("Budget does not authorize dry-run inference; use explicit --execute")
        prepare(out, args.datasets, args.max_calls_per_dataset)
        print(json.dumps(dict(status="prepared-not-executed", output=str(out),
            manifest_sha256=sha256(out / "manifest.json"), api_calls=0)))


if __name__ == "__main__":
    main()
