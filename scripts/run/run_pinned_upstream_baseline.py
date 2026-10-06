"""Run the pinned upstream curation code with auditable, isolated artifacts.

No local src modules are imported. Source files are extracted from a Git commit
and checked against their recorded hashes before each fresh worker starts.
Transport/parse exhaustion stops an experiment instead of accepting mock or
parse-default decisions. Successful decisions retain upstream semantics.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

COMMIT = "bfcca625e4c637f27f869c07196e5a4d30270e08"
REPO = Path(__file__).resolve().parents[2]
CHANNELS = ("CH20", "CH3", "CH30", "CH31")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    # Atomic replacement prevents a disconnected reader from seeing partial JSON.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def append_json(path, value):
    with path.open("a") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


class ExecutionStop(BaseException):
    """Must bypass upstream's broad Exception -> mock fallback."""


def apply_output_budget(request, budget):
    """Change only the approved budget; reject unexpected upstream drift."""
    if request.get("max_output_tokens") != 1000 or budget not in (1000, 4000):
        raise ExecutionStop("Unexpected upstream or approved output budget")
    return dict(request, max_output_tokens=budget)


def prepare(root, budget=1000):
    root.mkdir(parents=True, exist_ok=False)
    source = root / "upstream"
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", COMMIT, "src"], cwd=REPO,
        text=True,
    ).splitlines()
    hashes = {}
    for name in names:
        if not name.endswith(".py"):
            continue
        content = subprocess.check_output(["git", "show", f"{COMMIT}:{name}"], cwd=REPO)
        dest = source / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        hashes[name] = digest(content)
    mats = {}
    for channel in CHANNELS:
        path = (REPO / "data" / f"{channel}_spikes.mat").resolve(strict=True)
        mats[channel] = {"path": str(path), "sha256": file_hash(path)}
    packages = {}
    for name in ("numpy", "matplotlib", "scipy", "spikeinterface", "openai", "h5py"):
        packages[name] = importlib.metadata.version(name)
    write_json(root / "protocol.json", {
        "created_at": now(), "upstream_commit": COMMIT, "source_hashes": hashes,
        "harness_sha256": file_hash(__file__), "mat_files": mats,
        "channels": CHANNELS, "model": "gpt-5.1", "reasoning_effort": "medium",
        "response_schema": "upstream required action+rationale, strict JSON schema",
        "max_output_tokens_per_request": budget, "total_call_cap": None,
        "protocol_variant": "upstream-original-budget" if budget == 1000 else "upstream-budget-only-4000",
        "numpy_seed_per_channel": 0, "waveform_cap": 5000,
        "phase1_images": ["waveform", "isi", "tree"],
        "phase2_images": ["small_waveform", "large_waveform", "merged_isi"],
        "thresholds": {"auto_discard": 500, "small_cluster": 4000, "final_minimum": 5000},
        "execution_changes": (["SDK max_output_tokens changed from 1000 to 4000; nothing else in the request changes"] if budget != 1000 else []) + [
            "provider/mock or exhausted parse failure stops run; no substitute decision",
            "record request/response/state artifacts and deterministic NumPy seed 0",
            "empty terminal output receives explicit zero metrics",
        ],
        "python": sys.version, "packages": packages,
        "scope": "fresh full rollout using current upstream, not historical action replay",
    })
    (root / "harness_snapshot.py").write_bytes(Path(__file__).read_bytes())


def load_source(root):
    protocol = json.loads((root / "protocol.json").read_text())
    if file_hash(__file__) != protocol["harness_sha256"]:
        raise RuntimeError("Harness changed after protocol freeze")
    for name, expected in protocol["source_hashes"].items():
        if file_hash(root / "upstream" / name) != expected:
            raise RuntimeError(f"Pinned source changed: {name}")
    if any(name == "src" or name.startswith("src.") for name in sys.modules):
        raise RuntimeError("A local src module was imported before source isolation")
    sys.path.insert(0, str(root / "upstream"))
    return protocol


def verify_failed_input(root, source):
    """One paid budget check on saved bytes, not an autonomous rollout score."""
    protocol = load_source(root)
    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv(REPO / ".env", override=True)
    if os.getenv("OPENAI_BASE_URL", "").strip() or os.getenv("VLM_EXTRA_BODY_JSON", "").strip():
        raise RuntimeError("Custom API overrides are outside this protocol")
    old = [json.loads(x) for x in (source / "requests.jsonl").read_text().splitlines()][-1]
    previous = [json.loads(x) for x in (source / "responses.jsonl").read_text().splitlines()][-1]
    if previous["call_id"] != old["call_id"] or previous["response"].get("incomplete_details") != {"reason": "max_output_tokens"}:
        raise RuntimeError("Source is not the recorded output-budget failure")
    images = {file_hash(p): p for p in (source / "vlm_inputs").glob("*.png")}
    content = [{"type": "input_text", "text": old["prompt"]}]
    for h in old["image_sha256_in_order"]:
        content.append({"type": "input_image", "detail": "high", "image_url":
                        "data:image/png;base64," + base64.b64encode(images[h].read_bytes()).decode()})
    request = apply_output_budget({"model": old["model"], "reasoning": old["reasoning"],
        "text": {"format": old["text_format"]}, "max_output_tokens": old["max_output_tokens"],
        "input": [{"role": "user", "content": content}]}, protocol["max_output_tokens_per_request"])
    out = root / "budget_check"
    out.mkdir(exist_ok=False)
    write_json(out / "request_manifest.json", dict(old, source=str(source),
               max_output_tokens=request["max_output_tokens"], purpose="single saved-input budget check, not rollout"))
    response = OpenAI().responses.create(**request)
    write_json(out / "response.json", response.model_dump(mode="json"))
    valid = False
    try:
        decision = json.loads(response.output_text)
        valid = (response.status == "completed" and set(decision) == {"action", "rationale"}
                 and decision["action"] in old["text_format"]["schema"]["properties"]["action"]["enum"]
                 and isinstance(decision["rationale"], str))
    except (ValueError, TypeError):
        decision = None
    write_json(out / "status.json", {"status": "passed" if valid else "failed", "at": now(),
        "response_status": response.status, "decision": decision, "actual_model": response.model,
        "usage": response.model_dump(mode="json").get("usage"),
        "source_call_id": old["call_id"], "not_a_rollout": True})
    print(json.dumps({"budget_check_passed": valid, "response_status": response.status,
                      "action": decision.get("action") if decision else None}))
    if not valid:
        raise SystemExit("Budget check failed; do not start batch")


def worker(root, channel, dry=False, experiment=None):
    protocol = load_source(root)
    import numpy as np
    from dotenv import load_dotenv
    from src.agent import api, runner
    from src.cluster.manager import ClusterManager
    from src.io.matlab_loader import load_matlab_spikes
    from src.pipeline import pure
    from src.eval.metrics import match_clusters_to_ground_truth, compute_overall_performance

    for module in (api, runner, pure):
        assert Path(module.__file__).resolve().is_relative_to(root / "upstream")
    if experiment is not None:
        pure = experiment.controller(pure)
    load_dotenv(REPO / ".env", override=True)
    if os.getenv("VLM_EXTRA_BODY_JSON", "").strip():
        raise RuntimeError("VLM_EXTRA_BODY_JSON must be unset for the pinned protocol")
    if os.getenv("OPENAI_BASE_URL", "").strip():
        raise RuntimeError("Custom OPENAI_BASE_URL is outside the pinned protocol")
    if not dry and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY unavailable")
    out = root / ((f"preflight_{channel}" if experiment is not None else "preflight") if dry else channel)
    out.mkdir(exist_ok=False)
    (out / "states").mkdir()
    mat = protocol["mat_files"][channel]
    if file_hash(mat["path"]) != mat["sha256"]:
        raise RuntimeError("MAT changed after preflight")
    meta = load_matlab_spikes(mat["path"])
    manager = ClusterManager(
        meta["hierarchy_assigns"].copy(), meta["overcluster_assigns"].copy(),
        meta["hierarchy_tree"].copy(), meta["spiketimes"], meta["waveforms"],
    )
    np.random.seed(0)
    status = {"status": "running", "channel": channel, "started_at": now(),
              "upstream_commit": COMMIT, "dry_run": dry, "api_calls": 0,
              "accepted_decisions": 0, "input_tokens": 0, "output_tokens": 0,
              "cached_input_tokens": 0, "actual_models": [], "incomplete_responses": 0}
    write_json(out / "status.json", status)
    pipeline = None
    last_response = {}
    original_factory = api.OpenAI

    def checkpoint(call_id):
        np.savez_compressed(out / "states" / f"call_{call_id:05d}.npz",
                            assigns=manager.assigns, hierarchy_tree=manager.hierarchy_tree)
        if pipeline is not None:
            pipeline.save_action_log(out / "action_log.partial.csv")

    class ObservedResponses:
        def __init__(self, real):
            self.real = real

        def create(self, **request):
            nonlocal last_response
            call_id = status["api_calls"] + 1
            schema = request.get("text", {}).get("format", {})
            if not (request["model"] == "gpt-5.1"
                    and request["reasoning"] == {"effort": "medium"}
                    and request["max_output_tokens"] == 1000
                    and schema.get("type") == "json_schema" and schema.get("strict") is True
                    and set(schema["schema"]["required"]) == {"action", "rationale"}):
                raise ExecutionStop("Actual SDK request does not match frozen upstream contract")
            content = request["input"][0]["content"]
            text = content[0]["text"]
            images = content[1:]
            if len(images) != 3 or any(x.get("detail") != "high" for x in images):
                raise ExecutionStop("Image request does not match upstream three-view contract")
            request = apply_output_budget(request, protocol["max_output_tokens_per_request"])
            if experiment is not None:
                request = experiment.request(request)
            checkpoint(call_id)
            image_hashes = [digest(base64.b64decode(x["image_url"].split(",", 1)[1])) for x in images]
            record = {"call_id": call_id, "at": now(), "model": request["model"],
                      "prompt": text, "prompt_sha256": digest(text.encode()),
                      "image_sha256_in_order": image_hashes, "text_format": schema,
                      "reasoning": request["reasoning"], "max_output_tokens": request["max_output_tokens"],
                      "dry_run": dry}
            append_json(out / "requests.jsonl", record)
            status["api_calls"] = call_id
            write_json(out / "status.json", status)
            if dry:
                from types import SimpleNamespace
                response = SimpleNamespace(
                    output_text='{"action":"SPLIT","rationale":"OFFLINE REQUEST PREFLIGHT"}',
                    model="offline-preflight", status="completed", usage=None,
                    model_dump=lambda **kw: {"status": "completed", "model": "offline-preflight",
                                             "output_text": "OFFLINE REQUEST PREFLIGHT"},
                )
            else:
                try:
                    response = (experiment.send(self.real, request, out, call_id)
                                if experiment is not None else self.real.create(**request))
                except Exception as exc:
                    append_json(out / "responses.jsonl", {"call_id": call_id,
                        "error_type": type(exc).__name__, "status_code": getattr(exc, "status_code", None)})
                    raise
            last_response = response.model_dump(mode="json")
            append_json(out / "responses.jsonl", {"call_id": call_id, "response": last_response})
            usage = last_response.get("usage") or {}
            for key in ("input_tokens", "output_tokens"):
                status[key] += usage.get(key, 0)
            status["cached_input_tokens"] += (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
            if response.model not in status["actual_models"]:
                status["actual_models"].append(response.model)
            if response.status != "completed":
                status["incomplete_responses"] += 1
            write_json(out / "status.json", status)
            if experiment is not None and not dry:
                experiment.validate_response(last_response, response.output_text, schema)
            return response

    class ObservedClient:
        def __init__(self, *args, **kwargs):
            if experiment is not None:
                kwargs.update(max_retries=0, timeout=180)
            self.real = None if dry else original_factory(*args, **kwargs)
            self.responses = ObservedResponses(None if dry else self.real.responses)

    api.OpenAI = ObservedClient
    original_call = runner.call_vlm_api

    def forbid_mock(*args, **kwargs):
        if kwargs.get("use_mock") or not runner.VLM_AVAILABLE:
            raise ExecutionStop("Upstream attempted mock fallback; experiment stopped")
        return original_call(*args, **kwargs)

    runner.call_vlm_api = forbid_mock

    def observe_decision(function, stage):
        def wrapped(**kwargs):
            decision = function(**kwargs)
            if (last_response.get("status") != "completed"
                    or "JSON parse error" in decision.get("rationale", "")):
                raise ExecutionStop("Provider output was incomplete/unparseable after upstream retries")
            allowed = {"KEEP", "DISCARD", "SPLIT"} if stage == "phase1" else {"MERGE", "NOT_MERGE", "DISCARD"}
            if decision.get("action") not in allowed:
                raise ExecutionStop("Invalid upstream decision")
            status["accepted_decisions"] += 1
            entity = {k: kwargs[k] for k in ("cluster_id", "small_cluster_id", "large_cluster_id") if k in kwargs}
            append_json(out / "decisions.jsonl", {"decision_index": status["accepted_decisions"],
                        "api_call_id": status["api_calls"], "stage": stage, **entity,
                        "action": decision["action"], "rationale": decision["rationale"]})
            if experiment is not None:
                decision = experiment.protect(decision, stage, kwargs, out, status["api_calls"])
            write_json(out / "status.json", status)
            if experiment is None and not dry and channel == "CH20" and status["accepted_decisions"] == 2:
                write_json(out / "early_checkpoint.json", {"entity": entity, "decision": decision,
                           "state_spikes": len(kwargs["spike_times"]), "at": now()})
                status["status"] = "awaiting_local_input_audit"
                write_json(out / "status.json", status)
                print("CH20 EARLY CHECKPOINT: waiting for continue_after_ch20_check.json", flush=True)
                while not (root / "continue_after_ch20_check.json").exists():
                    time.sleep(1)
                status["status"] = "running"
            return decision
        return wrapped

    pure.vlm_phase1_cluster_decision = observe_decision(runner.vlm_phase1_cluster_decision, "phase1")
    pure.vlm_phase2_merge_decision = observe_decision(runner.vlm_phase2_merge_decision, "phase2")
    pipeline = pure.PureVLMCurationPipeline(
        manager, None, sampling_rate=float(meta["Fs"]),
        auto_discard_threshold=0 if experiment is not None and experiment.protected else 500,
        small_cluster_threshold=4000,
        final_minimum_threshold=0 if experiment is not None and experiment.protected else 5000, provider="gpt4o",
        model="gpt-5.1", use_mock=False, temperature=0.0, reasoning_effort="medium", output_dir=out,
    )
    try:
        if dry:
            for _ in range(2):
                info = manager.get_cluster_info(1)
                pure.vlm_phase1_cluster_decision(
                    cluster_id=1, waveforms=info["waveforms"], spike_times=info["spike_times"],
                    overcluster_composition=info["overclusters"], hierarchy_tree=manager.hierarchy_tree,
                    sampling_rate=float(meta["Fs"]), provider="gpt4o", model="gpt-5.1",
                    use_mock=False, temperature=0.0, reasoning_effort="medium", output_dir=out,
                )
                manager.split_last_merge(1)
            status["status"] = "preflight_passed"
        else:
            final_ids = pipeline.run_full_pipeline()
            np.save(out / "final_assigns.npy", manager.assigns)
            np.save(out / "final_hierarchy_tree.npy", manager.hierarchy_tree)
            np.save(out / "overcluster_assigns.npy", manager.overcluster_assigns)
            pipeline.save_action_log(out / "action_log.csv")
            gt = meta["curation_assigns"]
            class UnitIds:
                def __init__(self, a): self.ids = np.unique(a[a > 0])
                def get_unit_ids(self): return self.ids
            if final_ids:
                comparison, total = match_clusters_to_ground_truth(
                    UnitIds(manager.assigns), UnitIds(gt), manager.assigns, gt)
                performance = compute_overall_performance(comparison, total)
                performance = {k: v.item() if isinstance(v, np.generic) else v for k, v in performance.items()}
                comparison.to_csv(out / "ground_truth_comparison.csv", index=False)
            else:
                performance = {"n_curated_clusters": 0, "n_matched_clusters": 0,
                    "total_gt_spikes": int(np.sum(gt > 0)), "total_tp": 0, "total_fp": 0,
                    "total_fn": int(np.sum(gt > 0)), "overall_precision": 0.0,
                    "overall_recall": 0.0, "overall_f1_score": 0.0}
            write_json(out / "evaluation_report.json", {"overall_performance": performance,
                       "metric_source": COMMIT + ":src/eval/metrics.py"})
            status.update(status="complete", final_units=len(final_ids),
                          assigned_spikes=int(np.sum(manager.assigns > 0)), performance=performance)
    except BaseException as exc:
        status.update(status="failed", error_type=type(exc).__name__,
                      error=str(exc) if isinstance(exc, ExecutionStop) else type(exc).__name__)
        pipeline.save_action_log(out / "action_log.partial.csv")
        np.save(out / "assigns.partial.npy", manager.assigns)
        raise
    finally:
        status["updated_at"] = now()
        write_json(out / "status.json", status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--worker", choices=CHANNELS)
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--max-output-tokens", type=int, choices=(1000, 4000), default=1000,
                        help="Frozen during --prepare; workers read protocol.json")
    parser.add_argument("--verify-failed-input", type=Path)
    args = parser.parse_args()
    root = args.output_root.resolve()
    if not root.is_relative_to(REPO / "output"):
        parser.error("Experiment root must be under repository output/")
    os.environ.setdefault("MPLCONFIGDIR", str(root / "mpl-cache"))
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    if args.worker:
        worker(root, args.worker, args.dry)
        return
    if args.prepare:
        prepare(root, args.max_output_tokens)
        subprocess.run([sys.executable, __file__, "--output-root", str(root), "--worker", "CH20", "--dry"], check=True)
        return
    if args.verify_failed_input:
        verify_failed_input(root, args.verify_failed_input.resolve())
        return
    if not args.execute:
        parser.error("Specify --prepare or --execute")
    if json.loads((root / "preflight/status.json").read_text())["status"] != "preflight_passed":
        raise RuntimeError("Offline preflight has not passed")
    protocol = json.loads((root / "protocol.json").read_text())
    if protocol["max_output_tokens_per_request"] == 4000:
        if json.loads((root / "budget_check/status.json").read_text())["status"] != "passed":
            raise RuntimeError("Saved-input budget check has not passed")
    records = []
    for channel in CHANNELS:
        with (root / f"{channel}.log").open("x") as log:
            process = subprocess.Popen([sys.executable, "-u", __file__, "--output-root", str(root),
                                        "--worker", channel], stdout=log, stderr=subprocess.STDOUT)
            write_json(root / "batch_status.json", {"status": "running", "channel": channel,
                       "worker_pid": process.pid, "parent_pid": os.getpid(), "at": now()})
            code = process.wait()
        status = json.loads((root / channel / "status.json").read_text()) if (root / channel / "status.json").exists() else {"status": "failed"}
        records.append(status)
        subprocess.run([sys.executable, str(REPO / "scripts/analysis/summarize_pinned_upstream_baseline.py"), str(root)], check=True)
        summary = {"at": now(), "channels": records, "status": "running"}
        if code != 0 or status["status"] != "complete":
            summary["status"] = "stopped_on_execution_failure"
            write_json(root / "summary.json", summary)
            write_json(root / "batch_status.json", summary)
            raise SystemExit(1)
        write_json(root / "summary.json", summary)
    summary["status"] = "complete"
    summary["macro_mean"] = {key: sum(r["performance"][key] for r in records) / len(records)
                             for key in ("overall_precision", "overall_recall", "overall_f1_score")}
    write_json(root / "summary.json", summary)
    write_json(root / "batch_status.json", summary)


if __name__ == "__main__":
    main()
