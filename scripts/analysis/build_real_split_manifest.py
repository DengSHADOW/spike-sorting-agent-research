"""Build a leakage-aware manifest for the audited real-data MAT collection.

The grouping is inferred from the recording-block component in each directory
name because provider-confirmed session metadata are unavailable.  The script
does not open or modify any MAT file; it derives the manifest from the two
read-only audit summaries and preserves their recorded SHA-256 fingerprints.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


PRIMARY_SOURCES = {
    "cM2-e004_004-006_CH3": ("excel", "unified_action_and_terminal"),
    "cM2-e004_004-006_CH31": ("excel", "unified_action_and_terminal"),
    "cM2-e004_011-015_CH20": ("mat_internal", "separate_action_and_terminal"),
    "cM2-e008_021-028_CH30": ("excel", "separate_action_and_terminal"),
}


def recording_block(dataset_id: str) -> str:
    match = re.fullmatch(r"(.+)_CH\d+", dataset_id)
    if match is None:
        raise ValueError(f"cannot infer recording block from {dataset_id!r}")
    return match.group(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--internal-audit",
        type=Path,
        default=Path("output/real_mat_internal_action_audit_20260902/summary.json"),
    )
    parser.add_argument(
        "--source-audit",
        type=Path,
        default=Path("output/real_action_source_comparison_20260902/summary.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/real_split_manifest_20260903/manifest.json"),
    )
    args = parser.parse_args()

    internal_rows: list[dict[str, Any]] = json.loads(args.internal_audit.read_text())
    source_rows: list[dict[str, Any]] = json.loads(args.source_audit.read_text())
    source_lookup = {
        (str(row["channel"]), str(row["source"])): row for row in source_rows
    }

    datasets: list[dict[str, Any]] = []
    for row in internal_rows:
        dataset_id = str(row["dataset_id"])
        block = recording_block(dataset_id)
        has_terminal = bool(row["has_curation_assigns"])

        if dataset_id in PRIMARY_SOURCES:
            action_source, target_relation = PRIMARY_SOURCES[dataset_id]
            selected = source_lookup[(str(row["channel"]), action_source)]
            n_actions = int(selected["n_actions"])
            n_reasoning = int(selected["n_reasoning"])
            valid_prefix = int(selected["valid_prefix_length"])
            counts = dict(selected["action_counts"])
            replay_ari = selected["replay_vs_curation_ari"]
            replay_exact = selected["replay_assigns_exact_match"]
        elif int(row["n_internal_actions"]) > 0:
            action_source = "mat_internal"
            target_relation = "separate_action_and_terminal"
            n_actions = int(row["n_internal_actions"])
            n_reasoning = int(row["n_reasoning_entries"])
            valid_prefix = int(row["n_applied"])
            counts = dict(row["action_counts"])
            replay_ari = row["replay_vs_curation_ari"]
            replay_exact = row["replay_assigns_exact_match"]
        else:
            action_source = "none"
            target_relation = "input_only"
            n_actions = 0
            n_reasoning = 0
            valid_prefix = 0
            counts = {}
            replay_ari = None
            replay_exact = None

        if valid_prefix != n_actions:
            raise RuntimeError(
                f"selected source for {dataset_id} is not fully executable: "
                f"{valid_prefix}/{n_actions}"
            )
        if target_relation == "unified_action_and_terminal" and replay_exact is not True:
            raise RuntimeError(f"{dataset_id} was marked unified but replay is not exact")

        datasets.append(
            {
                "dataset_id": dataset_id,
                "recording_block": block,
                "channel": row["channel"],
                "mat_path": row["mat_path"],
                "mat_sha256": row["sha256"],
                "file_size_bytes": row["file_size_bytes"],
                "n_spikes": row["n_spikes"],
                "action_source": action_source,
                "n_actions": n_actions,
                "n_reasoning": n_reasoning,
                "action_counts": counts,
                "actions_fully_executable": valid_prefix == n_actions and n_actions > 0,
                "has_terminal_assigns": has_terminal,
                "target_relation": target_relation,
                "replay_vs_terminal_ari": replay_ari,
                "replay_exact_terminal": replay_exact,
                "allowed_uses": (
                    ["action_level", "cluster_level", "joint_action_cluster"]
                    if target_relation == "unified_action_and_terminal"
                    else ["action_level", "cluster_level_separately"]
                    if target_relation == "separate_action_and_terminal"
                    else ["unlabeled_inference", "manual_annotation_pool"]
                ),
            }
        )

    blocks = sorted({str(row["recording_block"]) for row in datasets})
    folds: list[dict[str, Any]] = []
    for holdout in blocks:
        train_ids = [
            str(row["dataset_id"])
            for row in datasets
            if row["recording_block"] != holdout and int(row["n_actions"]) > 0
        ]
        eval_action_ids = [
            str(row["dataset_id"])
            for row in datasets
            if row["recording_block"] == holdout and int(row["n_actions"]) > 0
        ]
        eval_cluster_ids = [
            str(row["dataset_id"])
            for row in datasets
            if row["recording_block"] == holdout and bool(row["has_terminal_assigns"])
        ]
        unlabeled_ids = [
            str(row["dataset_id"])
            for row in datasets
            if row["recording_block"] == holdout and not bool(row["has_terminal_assigns"])
        ]
        folds.append(
            {
                "fold_id": f"holdout_{holdout}",
                "holdout_recording_block": holdout,
                "train_action_dataset_ids": train_ids,
                "eval_action_dataset_ids": eval_action_ids,
                "eval_cluster_dataset_ids": eval_cluster_ids,
                "unlabeled_inference_dataset_ids": unlabeled_ids,
                "recording_block_overlap": False,
            }
        )

    action_counts: Counter[str] = Counter()
    for row in datasets:
        action_counts.update({key: int(value) for key, value in row["action_counts"].items()})
    relation_counts = Counter(str(row["target_relation"]) for row in datasets)
    summary = {
        "n_datasets": len(datasets),
        "n_recording_blocks": len(blocks),
        "recording_blocks": blocks,
        "n_spikes": sum(int(row["n_spikes"]) for row in datasets),
        "n_action_labels": sum(int(row["n_actions"]) for row in datasets),
        "n_reasoning_labels": sum(int(row["n_reasoning"]) for row in datasets),
        "action_counts": dict(sorted(action_counts.items())),
        "target_relation_counts": dict(sorted(relation_counts.items())),
    }
    manifest = {
        "schema_version": "real-recording-block-split-v1",
        "scope": "real_data_only",
        "grouping_status": "provisional_filename_inference",
        "grouping_rule": "directory dataset_id prefix before _CH is one recording block",
        "leakage_rule": "all channels from one inferred recording block stay in the same fold",
        "source_audits": [str(args.internal_audit), str(args.source_audit)],
        "excluded": {
            "Tianmin_Annotated_data": "SHA-256 duplicates of the four corresponding main MAT files",
            "oldFormat": "legacy representation, not mixed with current-format MAT files",
            "chronux_sampledata": "toolbox examples, not project human annotations",
        },
        "limitations": [
            "Recording/session identity is inferred from filenames, not confirmed by the provider.",
            "CH3 and CH31 unified ground truth are in the same recording block and cannot be split across train/eval.",
            "For separate_action_and_terminal datasets, action-level and cluster-level scores must be reported separately.",
        ],
        "summary": summary,
        "datasets": datasets,
        "leave_one_recording_block_out_folds": folds,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(
        f"Saved {args.output}: {summary['n_datasets']} datasets, "
        f"{summary['n_recording_blocks']} blocks, {summary['n_action_labels']} actions",
        flush=True,
    )


if __name__ == "__main__":
    main()
