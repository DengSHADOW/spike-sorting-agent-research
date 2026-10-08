"""Offline collection of completed stage-6 terminals; never grade a stopped state as final."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import spikeinterface as si
from scripts.run import run_curation_stage6 as run
from src.eval.metrics import match_clusters_to_ground_truth, compute_overall_performance
from src.pipeline.curation_engineering_v1 import arrays_hash, write_json


def native(value):
    if isinstance(value, dict):
        return {str(k): native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [native(v) for v in value]
    return value.item() if isinstance(value, np.generic) else value


def evaluate(assigns, gt, times, fs):
    if assigns.shape != gt.shape or assigns.shape != times.shape:
        raise ValueError("Spike-index alignment mismatch")
    frames = (times * fs).astype(np.int64)
    def sorting(labels):
        return si.NumpySorting.from_unit_dict({int(c): frames[labels == c]
            for c in np.unique(labels) if c > 0}, sampling_frequency=fs)
    matched, total = match_clusters_to_ground_truth(sorting(assigns), sorting(gt), assigns, gt, fs)
    metrics = native(compute_overall_performance(matched, total))
    metrics.update(n_retained_spikes=int(np.count_nonzero(assigns > 0)),
        expert_spikes_discarded=int(np.count_nonzero((gt > 0) & (assigns == 0))),
        noise_spikes_retained=int(np.count_nonzero((gt == 0) & (assigns > 0))))
    return metrics


def deletion_audit(folder, events, gt):
    """Archive observable losses + model rationale; do not invent causal explanations."""
    operation, decision, records = 0, None, []
    for event in events:
        if event["event"] == "decision":
            decision = event
        if event["event"] != "executed":
            continue
        operation += 1
        if event["action"] != "DISCARD":
            continue
        path = folder / f"operation_{operation:05d}_before.npz"
        meta = run.replay.read_json(path.with_suffix(".json"))
        if run.sha256(path) != meta["sha256"]:
            raise ValueError("Deletion checkpoint hash mismatch")
        with np.load(path, allow_pickle=False) as saved:
            labels = gt[saved["assigns"] == event["cid"]]
        expert = int(np.count_nonzero(labels > 0))
        if expert:
            records.append(dict(cluster_id=event["cid"], operation=operation, n_spikes=len(labels),
                expert_spikes_deleted=expert, noise_spikes_deleted=len(labels)-expert,
                gt_composition={str(int(k)): int(v) for k, v in zip(*np.unique(labels, return_counts=True))},
                phase=decision["phase"] if decision else None,
                model_rationale=decision["rationale"] if decision else None,
                step=f"step_{decision['calls']:05d}" if decision else None,
                checkpoint=str(path.name), attribution="Observed expert-spike loss; rationale is model explanation, not verified cause"))
    return records


def collect(source, out):
    manifest = run.verify(source)
    status = run.replay.read_json(source / "execution_status.json")
    out.mkdir(parents=True, exist_ok=False)
    results = []
    for row in manifest["datasets"]:
        dataset = row["dataset_id"]
        folder = source / "datasets" / dataset
        summary_path = folder / "summary.json"
        if not summary_path.exists():
            results.append(dict(dataset_id=dataset, status="not-started-or-interrupted", final_metrics=None))
            continue
        summary = run.replay.read_json(summary_path)
        record = dict(dataset_id=dataset, **summary, final_metrics=None)
        events_path = folder / "events.jsonl"
        events = run.replay.read_jsonl(events_path) if events_path.exists() else []
        record["actions"] = dict(Counter(e["action"] for e in events if e["event"] == "decision"))
        if summary["status"] == "completed" and summary["terminal_evaluation_allowed"]:
            checkpoint = folder / "terminal.npz"
            metadata = run.replay.read_json(checkpoint.with_suffix(".json"))
            if metadata["sha256"] != run.sha256(checkpoint):
                raise ValueError("Terminal checkpoint hash mismatch")
            with np.load(checkpoint, allow_pickle=False) as saved:
                final = saved["assigns"].copy()
                if arrays_hash(final, saved["tree"]) != summary["final_sha256"]:
                    raise ValueError("Terminal state mismatch")
            # Ground truth is accessed only here, after completion, outside provider.
            data = run.load_matlab_spikes(str(ROOT / row["mat_path"]))
            gt = data["curation_assigns"]
            if gt is None:
                record["evaluation_status"] = "missing-ground-truth"
            else:
                record["final_metrics"] = evaluate(final, gt, data["spiketimes"], data["Fs"])
                record["no_curation_metrics"] = evaluate(data["hierarchy_assigns"], gt, data["spiketimes"], data["Fs"])
                record["delta_f1"] = record["final_metrics"]["overall_f1_score"] - record["no_curation_metrics"]["overall_f1_score"]
                record["deletions_containing_expert_spikes"] = deletion_audit(folder, events, gt)
            record["terminal_file_sha256"] = run.sha256(checkpoint)
            del data
        results.append(record)
    report = dict(status=status, datasets=results, source=str(source.relative_to(ROOT)),
        manifest_sha256=run.sha256(source / "manifest.json"),
        note="Development rollout; partial runs not final. Direct spike-index many-to-one best-overlap matching, not Hungarian/time matching. Fragmentation can be under-penalized; not a SOTA metric claim.")
    write_json(out / "score.json", report)
    lines = ["# Stage 6 development rollout", "", report["note"], "",
             f"Campaign status: {status['status']}; calls: {status['inference_calls']}; "
             f"known-response standard estimate: ${status['standard_estimate_known_responses_usd']:.6f}; "
             f"conservative cost/uncertain reservation: ${status['conservative_charged_or_reserved_usd']:.6f}.", "",
             "| Dataset | Status | Units | Spikes | Precision | Recall | F1 | No-curation F1 |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        m = r["final_metrics"]
        cells = (f"{m['n_curated_clusters']} | {m['n_retained_spikes']} | {m['overall_precision']:.4f} | "
                 f"{m['overall_recall']:.4f} | {m['overall_f1_score']:.4f} | {r['no_curation_metrics']['overall_f1_score']:.4f}"
                 if m is not None else "— | — | — | — | — | —")
        lines.append(f"| {r['dataset_id']} | {r['status']} | {cells} |")
    lines += ["", "No pooled action accuracy: autonomous trajectories diverge from expert logs.",
              "No automatic skill adoption, no automatic next-stage execution.",
              "Failure attribution requires inspecting saved per-step observations, reasons and checkpoints; aggregate counts alone do not establish cause."]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    collect(run.output_path(args.run_dir), run.output_path(args.output_root))


if __name__ == "__main__":
    main()
