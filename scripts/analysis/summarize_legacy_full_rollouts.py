"""Summarize completed legacy GPT-5.1 rollouts without making model calls."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


CHANNELS = ("CH3", "CH20", "CH30", "CH31")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="output/legacy_reproduction/full_rollout_summary_20260922",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    output_dir = (repo_root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []

    for channel in CHANNELS:
        fresh_dir = (
            repo_root
            / "output"
            / "legacy_reproduction"
            / f"gpt51_{channel.lower()}_artifact_recovered_full_rollout_20260922"
        )
        historical_dir = repo_root / "output" / "main_gpt-5.1" / channel
        manifest = load_json(fresh_dir / "run_manifest.json")
        fresh = load_json(fresh_dir / "evaluation_report.json")["overall_performance"]
        historical = load_json(historical_dir / "evaluation_report.json")[
            "overall_performance"
        ]
        with (fresh_dir / "action_log.csv").open(encoding="utf-8") as handle:
            actions = Counter(row["Action"] for row in csv.DictReader(handle))
        fresh_assigns = np.load(fresh_dir / "final_assigns.npy")
        historical_assigns = np.load(historical_dir / "final_assigns.npy")
        records.append(
            {
                "channel": channel,
                "status": manifest["status"],
                "provider_calls": manifest.get("n_successful_provider_calls", 0),
                "vlm_attempts": manifest.get("n_vlm_attempts", 0),
                "total_tokens": manifest.get("usage", {}).get("total_tokens", 0),
                "keep_actions": actions.get("KEEP", 0),
                "split_actions": actions.get("SPLIT", 0),
                "merge_actions": actions.get("MERGE", 0),
                "discard_actions": actions.get("DISCARD", 0),
                "abstain_actions": actions.get("ABSTAIN", 0),
                "fresh_clusters": fresh["n_curated_clusters"],
                "fresh_assigned_spikes": int(np.sum(fresh_assigns > 0)),
                "fresh_precision": fresh["overall_precision"],
                "fresh_recall": fresh["overall_recall"],
                "fresh_f1": fresh["overall_f1_score"],
                "historical_clusters": historical["n_curated_clusters"],
                "historical_assigned_spikes": int(np.sum(historical_assigns > 0)),
                "historical_precision": historical["overall_precision"],
                "historical_recall": historical["overall_recall"],
                "historical_f1": historical["overall_f1_score"],
                "exact_assignment_match": bool(
                    np.array_equal(fresh_assigns, historical_assigns)
                ),
                "positive_mask_match": bool(
                    np.array_equal(fresh_assigns > 0, historical_assigns > 0)
                ),
                "fresh_output_dir": str(fresh_dir.relative_to(repo_root)),
            }
        )

    frame = pd.DataFrame(records)
    frame.to_csv(output_dir / "per_channel.csv", index=False)
    aggregate = {
        "n_channels": len(records),
        "all_complete": bool((frame["status"] == "complete").all()),
        "provider_calls": int(frame["provider_calls"].sum()),
        "total_tokens": int(frame["total_tokens"].sum()),
        "fresh_mean_precision": float(frame["fresh_precision"].mean()),
        "fresh_mean_recall": float(frame["fresh_recall"].mean()),
        "fresh_mean_f1": float(frame["fresh_f1"].mean()),
        "historical_mean_precision": float(frame["historical_precision"].mean()),
        "historical_mean_recall": float(frame["historical_recall"].mean()),
        "historical_mean_f1": float(frame["historical_f1"].mean()),
        "exact_assignment_matches": int(frame["exact_assignment_match"].sum()),
    }
    manifest = {
        "schema_version": "legacy-full-rollout-summary-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_calls": 0,
        "channels": list(CHANNELS),
        "records": records,
        "aggregate": aggregate,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(frame.to_string(index=False))
    print(json.dumps(aggregate, indent=2))


if __name__ == "__main__":
    main()
