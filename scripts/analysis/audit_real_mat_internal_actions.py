"""Audit internal MATLAB curation logs against their own final assignments.

This is a local, no-API, read-only audit of the source MAT files.  It replays
``spikes.curation.action(s)`` from ``hierarchy.assigns`` and compares the
result with ``spikes.curation.assigns``.  The report deliberately classifies
non-matching action and final-label supervision as separate usable targets
instead of assuming that they form one canonical annotation set.

Example:
    uv run python scripts/analysis/audit_real_mat_internal_actions.py \
        --data-root Jacob\ Bedke-Annotated_spike_sorting_data_w_chronux \
        --output-dir output/real_mat_internal_action_audit_20260902
"""

from __future__ import annotations

import argparse
import csv
import hashlib
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


def parse_action(action: str) -> tuple[str, int, Optional[int]]:
    """Parse the MATLAB curation action syntax used by the available files."""
    text = str(action).strip().strip("'\"")
    split_match = re.fullmatch(r"s\s+(\d+)", text, flags=re.IGNORECASE)
    if split_match:
        return "split", int(split_match.group(1)), None

    merge_match = re.fullmatch(r"m\s+(\d+)\s+(\d+)", text, flags=re.IGNORECASE)
    if merge_match:
        target, source = map(int, merge_match.groups())
        return ("discard", source, None) if target == 0 else ("merge", source, target)

    raise ValueError(f"Cannot parse action: {action!r}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dataset_id(path: Path) -> str:
    """Return a stable identifier that keeps recording and channel together."""
    parent = path.parent.name
    if parent == "not_annotated":
        parent = path.stem
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", parent)


def audit_mat(mat_path: Path, output_dir: Path) -> dict[str, object]:
    data = load_matlab_spikes(str(mat_path))
    actions = list(data.get("curation_actions") or [])
    reasoning = list(data.get("curation_action_reasoning") or [])
    manager = ClusterManager(
        initial_assigns=data["hierarchy_assigns"],
        overcluster_assigns=data["overcluster_assigns"],
        hierarchy_tree=data["hierarchy_tree"].copy(),
        spike_times=data["spiketimes"],
        waveforms=data["waveforms"],
    )

    rows: list[dict[str, object]] = []
    action_counts: Counter[str] = Counter()
    for step, raw_action in enumerate(actions, start=1):
        row: dict[str, object] = {
            "step": step,
            "raw_action": raw_action,
            "reasoning_present": step <= len(reasoning) and bool(reasoning[step - 1].strip()),
            "action_type": "",
            "source_cluster": "",
            "target_cluster": "",
            "status": "",
            "detail": "",
        }
        try:
            action_type, source, target = parse_action(raw_action)
            action_counts[action_type.upper()] += 1
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
        except Exception as exc:
            row.update(status="apply_error", detail=str(exc))
        rows.append(row)

    curation_assigns = data.get("curation_assigns")
    status_counts = Counter(str(row["status"]) for row in rows)
    has_final = curation_assigns is not None
    exact_match = bool(has_final and np.array_equal(curation_assigns, manager.assigns))
    initial_vs_curation_ari = (
        float(adjusted_rand_score(curation_assigns, data["hierarchy_assigns"]))
        if has_final else None
    )

    if not has_final:
        usability = "initial_state_only"
    elif not actions:
        usability = "cluster_level_only"
    elif status_counts.get("applied", 0) == len(actions) and exact_match:
        usability = "unified_action_and_cluster_ground_truth"
    else:
        usability = "separate_action_and_cluster_targets"

    summary: dict[str, object] = {
        "dataset_id": dataset_id(mat_path),
        "channel": mat_path.stem.removesuffix("_spikes"),
        "mat_path": str(mat_path),
        "file_size_bytes": mat_path.stat().st_size,
        "sha256": sha256_file(mat_path),
        "n_spikes": int(len(data["spiketimes"])),
        "sampling_rate_hz": float(data["Fs"]),
        "waveform_samples": int(data["waveforms"].shape[1]),
        "initial_active_clusters": int(np.count_nonzero(np.unique(data["hierarchy_assigns"]))),
        "has_curation_assigns": has_final,
        "curation_active_clusters": (
            int(np.count_nonzero(np.unique(curation_assigns))) if has_final else None
        ),
        "initial_vs_curation_ari": initial_vs_curation_ari,
        "n_internal_actions": len(actions),
        "n_reasoning_entries": sum(bool(text.strip()) for text in reasoning),
        "action_counts": dict(sorted(action_counts.items())),
        "n_applied": status_counts.get("applied", 0),
        "status_counts": dict(sorted(status_counts.items())),
        "replayed_active_clusters": len(manager.get_active_clusters()),
        "replay_vs_curation_ari": (
            float(adjusted_rand_score(curation_assigns, manager.assigns)) if has_final else None
        ),
        "replay_assigns_exact_match": exact_match if has_final else None,
        "recommended_usability": usability,
    }

    detail_dir = output_dir / "steps"
    detail_dir.mkdir(parents=True, exist_ok=True)
    detail_path = detail_dir / f"{summary['dataset_id']}_steps.csv"
    with detail_path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = list(rows[0]) if rows else [
            "step", "raw_action", "reasoning_present", "action_type",
            "source_cluster", "target_cluster", "status", "detail",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return summary


def discover_mat_files(data_root: Path) -> list[Path]:
    return sorted(
        path for path in data_root.rglob("*_spikes.mat")
        if "oldFormat" not in path.parts and "chronux_spikesort" not in path.parts
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    mat_files = discover_mat_files(args.data_root)
    if not mat_files:
        parser.error(f"No current-format *_spikes.mat files found under {args.data_root}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, object]] = []
    for position, mat_path in enumerate(mat_files, start=1):
        print(f"[{position}/{len(mat_files)}] Auditing {mat_path}", flush=True)
        summary = audit_mat(mat_path, args.output_dir)
        summaries.append(summary)
        print(
            f"  {summary['recommended_usability']}: "
            f"{summary['n_applied']}/{summary['n_internal_actions']} applied; "
            f"ARI={summary['replay_vs_curation_ari']}",
            flush=True,
        )

    summary_json = args.output_dir / "summary.json"
    summary_json.write_text(json.dumps(summaries, indent=2), encoding="utf-8")

    summary_csv = args.output_dir / "summary.csv"
    scalar_fields = [
        key for key, value in summaries[0].items()
        if not isinstance(value, dict)
    ]
    with summary_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=scalar_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summaries)

    print(f"Saved {summary_json}", flush=True)
    print(f"Saved {summary_csv}", flush=True)


if __name__ == "__main__":
    main()
