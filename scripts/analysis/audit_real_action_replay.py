"""Audit whether CSV human-action sheets replay against their MATLAB state.

This is a local, no-VLM diagnostic.  It reads a MATLAB channel, replays the
CSV actions using the same ClusterManager operations as the legacy VLM test,
and compares the resulting partition with ``curation.assigns`` in the MAT.

Example:
    uv run python scripts/analysis/audit_real_action_replay.py \
        --data-root Tianmin_Annotated_data/data \
        --output-dir output/real_annotation_replay_audit_20260821
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.metrics import adjusted_rand_score

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.cluster.manager import ClusterManager
from src.io.matlab_loader import load_matlab_spikes


DEFAULT_CHANNELS = ("CH3", "CH20", "CH30", "CH31")


def parse_action(action: str) -> tuple[str, int, Optional[int]]:
    """Parse the legacy MATLAB-style action string used by action sheets."""
    text = action.strip().strip("'\"")
    split_match = re.fullmatch(r"s\s+(\d+)", text)
    if split_match:
        return "split", int(split_match.group(1)), None

    merge_match = re.fullmatch(r"m\s+(\d+)\s+(\d+)", text)
    if merge_match:
        target, source = map(int, merge_match.groups())
        return ("discard", source, None) if target == 0 else ("merge", source, target)

    raise ValueError(f"Cannot parse action: {action!r}")


def load_actions(path: Path) -> list[tuple[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [
            (row.get("Actions", "").strip(), row.get("Action Reasoning", "").strip())
            for row in reader
            if row.get("Actions", "").strip()
        ]


def audit_channel(channel: str, data_root: Path, output_dir: Path) -> dict[str, object]:
    mat_path = data_root / f"{channel}_spikes.mat"
    actions_path = data_root / "action_sheets" / f"{channel}.csv"
    data = load_matlab_spikes(str(mat_path))
    manager = ClusterManager(
        initial_assigns=data["hierarchy_assigns"],
        overcluster_assigns=data["overcluster_assigns"],
        hierarchy_tree=data["hierarchy_tree"].copy(),
        spike_times=data["spiketimes"],
        waveforms=data["waveforms"],
    )

    rows: list[dict[str, object]] = []
    for step, (raw_action, reasoning) in enumerate(load_actions(actions_path), start=1):
        row: dict[str, object] = {
            "step": step,
            "raw_action": raw_action,
            "reasoning_present": bool(reasoning),
            "action_type": "",
            "source_cluster": "",
            "target_cluster": "",
            "status": "",
            "detail": "",
        }
        try:
            action_type, source, target = parse_action(raw_action)
            row.update(
                action_type=action_type,
                source_cluster=source,
                target_cluster="" if target is None else target,
            )
        except ValueError as exc:
            row.update(status="parse_error", detail=str(exc))
            rows.append(row)
            continue

        if manager.get_cluster_info(source) is None:
            row.update(status="missing_source", detail=f"cluster {source} is not active")
            rows.append(row)
            continue
        if target is not None and manager.get_cluster_info(target) is None:
            row.update(status="missing_target", detail=f"cluster {target} is not active")
            rows.append(row)
            continue

        try:
            if action_type == "split":
                manager.split_last_merge(source)
            elif action_type == "discard":
                manager.discard_cluster(source)
            else:
                manager.merge_clusters([source, int(target)], target_id=int(target))
            row["status"] = "applied"
        except Exception as exc:  # Record incompatibilities instead of stopping the audit.
            row.update(status="apply_error", detail=str(exc))
        rows.append(row)

    curation_assigns = data.get("curation_assigns")
    summary: dict[str, object] = {
        "channel": channel,
        "mat_path": str(mat_path),
        "action_sheet_path": str(actions_path),
        "n_spikes": int(len(data["spiketimes"])),
        "n_actions": len(rows),
        "n_applied": sum(row["status"] == "applied" for row in rows),
        "status_counts": dict(Counter(str(row["status"]) for row in rows)),
        "initial_active_clusters": int(np.count_nonzero(np.unique(data["hierarchy_assigns"]))),
        "replayed_active_clusters": len(manager.get_active_clusters()),
    }
    if curation_assigns is not None:
        summary["curation_active_clusters"] = int(
            np.count_nonzero(np.unique(curation_assigns))
        )
        summary["replay_vs_curation_ari"] = float(
            adjusted_rand_score(curation_assigns, manager.assigns)
        )
        summary["replay_assigns_exact_match"] = bool(
            np.array_equal(curation_assigns, manager.assigns)
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / f"{channel}_replay_steps.csv"
    with detail_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("Tianmin_Annotated_data/data"))
    parser.add_argument("--output-dir", type=Path, default=Path("output/real_annotation_replay_audit"))
    parser.add_argument("--channels", nargs="+", default=DEFAULT_CHANNELS)
    args = parser.parse_args()

    summaries = [audit_channel(channel, args.data_root, args.output_dir) for channel in args.channels]
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    for summary in summaries:
        print(
            f"{summary['channel']}: {summary['n_applied']}/{summary['n_actions']} actions applied; "
            f"ARI={summary.get('replay_vs_curation_ari', 'n/a')}"
        )
    print(f"Saved audit to {summary_path}")


if __name__ == "__main__":
    main()
