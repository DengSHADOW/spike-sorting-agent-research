"""Re-evaluate retained legacy rollout assignments with one metric implementation.

This script makes no model/API calls and never modifies the source MAT files or
the retained legacy result directories.  It writes a provenance-rich copy of
all reports beneath a new output root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import spikeinterface as si

from src.eval.metrics import generate_full_evaluation_report
from src.io.matlab_loader import load_matlab_spikes


DEFAULT_METHODS = ("main_gpt-4.1", "main_gpt-5.1", "ablation_no_metrics")
DEFAULT_CHANNELS = ("CH3", "CH20", "CH30", "CH31")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def evaluate_one(
    *, method: str, channel: str, output_root: Path, repo_root: Path
) -> dict[str, Any]:
    mat_path = repo_root / "data" / f"{channel}_spikes.mat"
    assigns_path = repo_root / "output" / method / channel / "final_assigns.npy"
    if not mat_path.exists():
        raise FileNotFoundError(mat_path)
    if not assigns_path.exists():
        raise FileNotFoundError(assigns_path)

    meta = load_matlab_spikes(str(mat_path))
    gt_assigns = meta.get("curation_assigns")
    if gt_assigns is None:
        raise ValueError(f"{mat_path} has no curation.assigns")
    final_assigns = np.load(assigns_path)
    if final_assigns.shape != gt_assigns.shape:
        raise ValueError(
            f"assignment length mismatch for {method}/{channel}: "
            f"{final_assigns.shape} != {gt_assigns.shape}"
        )

    fs = float(meta["Fs"])
    spike_frames = (meta["spiketimes"] * fs).astype(np.int64)
    final_ids = np.unique(final_assigns[final_assigns > 0])
    gt_ids = np.unique(gt_assigns[gt_assigns > 0])
    curated = si.NumpySorting.from_unit_dict(
        {int(cid): spike_frames[final_assigns == cid] for cid in final_ids},
        sampling_frequency=fs,
    )
    ground_truth = si.NumpySorting.from_unit_dict(
        {int(cid): spike_frames[gt_assigns == cid] for cid in gt_ids},
        sampling_frequency=fs,
    )

    report_dir = output_root / method / channel
    report_dir.mkdir(parents=True, exist_ok=True)
    report = generate_full_evaluation_report(
        curated_sorting=curated,
        waveforms=meta["waveforms"],
        spike_times=meta["spiketimes"],
        assigns=final_assigns,
        ground_truth_sorting=ground_truth,
        gt_assigns=gt_assigns,
        sampling_frequency=fs,
        output_dir=report_dir,
    )
    performance = json_safe(report["overall_performance"])
    record = {
        "method": method,
        "channel": channel,
        "mat_path": str(mat_path.relative_to(repo_root)),
        "mat_sha256": sha256_file(mat_path.resolve()),
        "assigns_path": str(assigns_path.relative_to(repo_root)),
        "assigns_sha256": sha256_file(assigns_path),
        "n_final_clusters": int(len(final_ids)),
        **performance,
    }
    (report_dir / "provenance.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--methods", nargs="+", default=list(DEFAULT_METHODS))
    parser.add_argument("--channels", nargs="+", default=list(DEFAULT_CHANNELS))
    parser.add_argument(
        "--output-root",
        default="output/legacy_rollout_reevaluation_20260922",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    output_root = (repo_root / args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    for method in args.methods:
        for channel in args.channels:
            print(f"Re-evaluating {method}/{channel}...", flush=True)
            records.append(
                evaluate_one(
                    method=method,
                    channel=channel,
                    output_root=output_root,
                    repo_root=repo_root,
                )
            )

    frame = pd.DataFrame(records)
    frame.to_csv(output_root / "per_channel_metrics.csv", index=False)
    aggregate_rows = []
    for method, group in frame.groupby("method", sort=False):
        aggregate_rows.append(
            {
                "method": method,
                "n_channels": int(len(group)),
                "mean_precision": float(group["overall_precision"].mean()),
                "mean_recall": float(group["overall_recall"].mean()),
                "mean_f1": float(group["overall_f1_score"].mean()),
                "std_precision": float(group["overall_precision"].std(ddof=1)),
                "std_recall": float(group["overall_recall"].std(ddof=1)),
                "std_f1": float(group["overall_f1_score"].std(ddof=1)),
            }
        )
    aggregate = pd.DataFrame(aggregate_rows)
    aggregate.to_csv(output_root / "aggregate_metrics.csv", index=False)

    manifest = {
        "schema_version": "legacy-rollout-reevaluation-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_calls": 0,
        "metric_implementation": "src.eval.metrics.generate_full_evaluation_report",
        "methods": list(args.methods),
        "channels": list(args.channels),
        "records": records,
        "aggregate": aggregate_rows,
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(aggregate.to_string(index=False))
    print(f"Saved to {output_root}")


if __name__ == "__main__":
    main()
