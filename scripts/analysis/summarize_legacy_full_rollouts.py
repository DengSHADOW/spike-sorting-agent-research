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
    parser.add_argument(
        "--fresh-dir-template",
        default=(
            "output/legacy_reproduction/"
            "gpt51_{channel_lower}_artifact_recovered_full_rollout_20260922"
        ),
        help="Repository-relative format string with {channel} and {channel_lower}.",
    )
    parser.add_argument(
        "--fresh-dir",
        action="append",
        default=[],
        metavar="CHANNEL=PATH",
        help=(
            "Override the fresh result directory for one channel; repeat for "
            "multiple channels. This is useful when a recovered run has a "
            "retry suffix."
        ),
    )
    parser.add_argument(
        "--historical-dir-template",
        default="output/main_gpt-5.1/{channel}",
        help="Repository-relative format string with {channel} and {channel_lower}.",
    )
    args = parser.parse_args()

    fresh_dir_overrides: dict[str, str] = {}
    for value in args.fresh_dir:
        channel, separator, path = value.partition("=")
        channel = channel.upper()
        if not separator or channel not in CHANNELS or not path:
            parser.error(
                f"invalid --fresh-dir {value!r}; expected one of "
                f"{', '.join(CHANNELS)}=PATH"
            )
        fresh_dir_overrides[channel] = path

    repo_root = Path(__file__).resolve().parents[2]
    output_dir = (repo_root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []

    for channel in CHANNELS:
        format_values = {"channel": channel, "channel_lower": channel.lower()}
        fresh_dir = repo_root / fresh_dir_overrides.get(
            channel, args.fresh_dir_template.format(**format_values)
        )
        historical_dir = repo_root / args.historical_dir_template.format(
            **format_values
        )
        manifest = load_json(fresh_dir / "run_manifest.json")
        fresh = load_json(fresh_dir / "evaluation_report.json")["overall_performance"]
        historical = load_json(historical_dir / "evaluation_report.json")[
            "overall_performance"
        ]
        with (fresh_dir / "action_log.csv").open(encoding="utf-8") as handle:
            action_rows = list(csv.DictReader(handle))
        actions = Counter(row["Action"] for row in action_rows)
        fresh_assigns = np.load(fresh_dir / "final_assigns.npy")
        historical_assigns = np.load(historical_dir / "final_assigns.npy")
        usage = manifest.get("usage", {})
        uncached_input_tokens = int(usage.get("uncached_input_tokens", 0))
        cached_input_tokens = int(usage.get("cached_input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        estimated_api_cost_usd = (
            uncached_input_tokens * 1.25
            + cached_input_tokens * 0.125
            + output_tokens * 10.0
        ) / 1_000_000
        records.append(
            {
                "channel": channel,
                "status": manifest["status"],
                "protocol": manifest.get("protocol"),
                "requested_model": manifest.get("requested_model"),
                "actual_models": ";".join(manifest.get("actual_models", [])),
                "provider_calls": manifest.get("n_successful_provider_calls", 0),
                "vlm_attempts": manifest.get("n_vlm_attempts", 0),
                "uncached_input_tokens": uncached_input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": usage.get("total_tokens", 0),
                "estimated_api_cost_usd": estimated_api_cost_usd,
                "keep_actions": actions.get("KEEP", 0),
                "split_actions": actions.get("SPLIT", 0),
                "merge_actions": actions.get("MERGE", 0),
                "discard_actions": actions.get("DISCARD", 0),
                "abstain_actions": actions.get("ABSTAIN", 0),
                "phase1_discard_guards": sum(
                    "DISCARD protected for a large real-data cluster"
                    in row["Reason"]
                    for row in action_rows
                ),
                "phase2_discard_guards": sum(
                    "Phase-2 DISCARD protected" in row["Reason"]
                    for row in action_rows
                ),
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
        "vlm_attempts": int(frame["vlm_attempts"].sum()),
        "uncached_input_tokens": int(frame["uncached_input_tokens"].sum()),
        "cached_input_tokens": int(frame["cached_input_tokens"].sum()),
        "output_tokens": int(frame["output_tokens"].sum()),
        "total_tokens": int(frame["total_tokens"].sum()),
        "estimated_api_cost_usd": float(frame["estimated_api_cost_usd"].sum()),
        "fresh_mean_precision": float(frame["fresh_precision"].mean()),
        "fresh_mean_recall": float(frame["fresh_recall"].mean()),
        "fresh_mean_f1": float(frame["fresh_f1"].mean()),
        "historical_mean_precision": float(frame["historical_precision"].mean()),
        "historical_mean_recall": float(frame["historical_recall"].mean()),
        "historical_mean_f1": float(frame["historical_f1"].mean()),
        "exact_assignment_matches": int(frame["exact_assignment_match"].sum()),
    }
    manifest = {
        "schema_version": "legacy-full-rollout-summary-v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_calls": 0,
        "channels": list(CHANNELS),
        "records": records,
        "aggregate": aggregate,
        "cost_assumptions_usd_per_million_tokens": {
            "uncached_input": 1.25,
            "cached_input": 0.125,
            "output_including_reasoning": 10.0,
        },
    }
    (output_dir / "summary.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(frame.to_string(index=False))
    print(json.dumps(aggregate, indent=2))


if __name__ == "__main__":
    main()
