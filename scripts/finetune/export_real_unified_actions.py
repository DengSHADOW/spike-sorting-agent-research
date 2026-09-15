"""Export ordered real-data action samples whose action log matches the MAT terminal labels.

This exporter is intentionally conservative:

* it starts from the unmodified ``hierarchy.assigns`` state;
* it applies Excel actions in their original row order;
* it performs no prefiltering, automatic discard, synthetic KEEP, or action reordering;
* it fails on a missing cluster or a no-op action;
* it verifies the complete replay against ``curation.assigns`` even when only a
  small prefix is rendered as images.

At present only CH3 and CH31 meet the unified action-plus-terminal criterion.
The source MAT files are opened read-only and fingerprinted before and after the run.
No model or external API is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import adjusted_rand_score

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.analysis.audit_real_action_sources import load_xlsx_sheets, parse_action
from scripts.finetune.build_finetune_dataset import FineTuneDatasetBuilder
from src.cluster.manager import ClusterManager
from src.io.matlab_loader import load_matlab_spikes


CHANNEL_SPECS = {
    "CH3": {
        "sheet": "cM2-e004_004-006_CH3",
        "recording_block": "cM2-e004_004-006",
    },
    "CH31": {
        "sheet": "cM2-e004_004-006_CH31",
        "recording_block": "cM2-e004_004-006",
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_assigns(assigns: np.ndarray) -> str:
    array = np.ascontiguousarray(assigns)
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode("ascii"))
    digest.update(str(array.shape).encode("ascii"))
    digest.update(array.tobytes())
    return digest.hexdigest()


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def apply_action(
    manager: ClusterManager,
    action_type: str,
    source_id: int,
    target_id: int | None,
) -> dict[str, Any]:
    if manager.get_cluster_info(source_id) is None:
        raise RuntimeError(f"source cluster {source_id} is not active")
    if target_id is not None and manager.get_cluster_info(target_id) is None:
        raise RuntimeError(f"target cluster {target_id} is not active")

    assigns_before = manager.assigns.copy()
    if action_type == "split":
        result: Any = manager.split_last_merge(source_id)
    elif action_type == "discard":
        manager.discard_cluster(source_id)
        result = None
    elif action_type == "merge":
        result = manager.merge_clusters([source_id, int(target_id)], target_id=int(target_id))
    else:
        raise ValueError(f"unsupported action type: {action_type}")

    if np.array_equal(assigns_before, manager.assigns):
        raise RuntimeError(f"{action_type} on cluster {source_id} did not change assignments")

    operation = manager.history[-1]
    return {
        "return_value": jsonable(result),
        "operation_type": operation.op_type,
        "operation_details": jsonable(operation.details),
    }


def render_sample(
    builder: FineTuneDatasetBuilder,
    channel: str,
    manager: ClusterManager,
    sampling_rate: float,
    raw_action: str,
    reasoning: str,
    action_type: str,
    source_id: int,
    target_id: int | None,
) -> dict[str, Any]:
    label = action_type.upper()
    if action_type in {"split", "discard"}:
        sample = builder._build_split_sample(
            channel=channel,
            manager=manager,
            fs=sampling_rate,
            cluster_id=source_id,
            label_action=label,
            label_reason=reasoning,
            source="expert_excel_ordered",
            expert_action_raw=raw_action,
        )
    else:
        sample = builder._build_merge_sample(
            channel=channel,
            manager=manager,
            fs=sampling_rate,
            small_cluster_id=source_id,
            large_cluster_id=int(target_id),
            label_action=label,
            label_reason=reasoning,
            source="expert_excel_ordered",
            expert_action_raw=raw_action,
        )
    if sample is None:
        raise RuntimeError(f"could not render {channel} action {raw_action!r}")
    # The legacy builder profile selects the strict reasoned-JSON prompt only;
    # the exported real-data artifact itself is model-agnostic.
    sample["profile"] = "real_ordered_reasoned"
    sample["output_mode"] = "reasoned"
    return sample


def export_channel(
    channel: str,
    rows: list[tuple[str, str]],
    mat_path: Path,
    workbook_path: Path,
    output_dir: Path,
    builder: FineTuneDatasetBuilder,
    max_rendered_actions: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    spec = CHANNEL_SPECS[channel]
    source_hash_before = sha256_file(mat_path)
    workbook_hash_before = sha256_file(workbook_path)
    data = load_matlab_spikes(str(mat_path))
    if data.get("curation_assigns") is None:
        raise RuntimeError(f"{channel} has no curation.assigns terminal target")

    manager = ClusterManager(
        initial_assigns=data["hierarchy_assigns"],
        overcluster_assigns=data["overcluster_assigns"],
        hierarchy_tree=data["hierarchy_tree"].copy(),
        spike_times=data["spiketimes"],
        waveforms=data["waveforms"],
    )
    initial_state_hash = sha256_assigns(manager.assigns)
    samples: list[dict[str, Any]] = []
    action_counts: dict[str, int] = {}

    for step_index, (raw_action, reasoning) in enumerate(rows, start=1):
        action_type, source_id, target_id = parse_action(raw_action)
        action_counts[action_type.upper()] = action_counts.get(action_type.upper(), 0) + 1
        active_before = manager.get_active_clusters()
        state_before_hash = sha256_assigns(manager.assigns)
        source_info = manager.get_cluster_info(source_id)
        if source_info is None:
            raise RuntimeError(
                f"{channel} step {step_index}: source cluster {source_id} is not active"
            )

        sample: dict[str, Any] | None = None
        if step_index <= max_rendered_actions:
            sample = render_sample(
                builder=builder,
                channel=channel,
                manager=manager,
                sampling_rate=float(data["Fs"]),
                raw_action=raw_action,
                reasoning=reasoning,
                action_type=action_type,
                source_id=source_id,
                target_id=target_id,
            )

        application = apply_action(manager, action_type, source_id, target_id)
        state_after_hash = sha256_assigns(manager.assigns)

        if sample is not None:
            sample.update(
                {
                    "schema_version": "real-unified-action-v1",
                    "trajectory_step": step_index,
                    "recording_block": spec["recording_block"],
                    "ground_truth_scope": "unified_action_and_terminal",
                    "mat_source": {
                        "path": str(mat_path),
                        "sha256": source_hash_before,
                    },
                    "annotation_source": {
                        "type": "excel",
                        "path": str(workbook_path),
                        "sha256": workbook_hash_before,
                        "sheet": spec["sheet"],
                        "row_order_preserved": True,
                    },
                    "preprocessing": {
                        "prefilter_small_overclusters": False,
                        "automatic_size_filter": False,
                        "synthetic_actions": False,
                        "action_reordering": False,
                    },
                    "expert_action": {
                        "raw": raw_action,
                        "type": action_type,
                        "source_cluster": source_id,
                        "target_cluster": target_id,
                        "reasoning": reasoning,
                    },
                    "state_before": {
                        "assigns_sha256": state_before_hash,
                        "n_active_clusters": len(active_before),
                        "active_clusters": active_before,
                        "source_n_spikes": int(source_info["n_spikes"]),
                    },
                    "state_after": {
                        "assigns_sha256": state_after_hash,
                        "n_active_clusters": len(manager.get_active_clusters()),
                    },
                    "application": application,
                }
            )
            samples.append(sample)

    terminal = np.asarray(data["curation_assigns"])
    exact_match = bool(np.array_equal(terminal, manager.assigns))
    terminal_ari = float(adjusted_rand_score(terminal, manager.assigns))
    if not exact_match:
        raise RuntimeError(
            f"{channel}: full Excel replay does not exactly match curation.assigns "
            f"(ARI={terminal_ari:.6f})"
        )

    source_hash_after = sha256_file(mat_path)
    workbook_hash_after = sha256_file(workbook_path)
    if source_hash_before != source_hash_after or workbook_hash_before != workbook_hash_after:
        raise RuntimeError(f"{channel}: source file fingerprint changed during read-only export")

    summary = {
        "channel": channel,
        "recording_block": spec["recording_block"],
        "mat_path": str(mat_path),
        "mat_sha256": source_hash_before,
        "annotation_path": str(workbook_path),
        "annotation_sha256": workbook_hash_before,
        "annotation_sheet": spec["sheet"],
        "n_source_actions": len(rows),
        "n_rendered_actions": len(samples),
        "render_is_prefix": len(samples) < len(rows),
        "n_reasoning": sum(bool(reasoning) for _, reasoning in rows),
        "action_counts": dict(sorted(action_counts.items())),
        "initial_assigns_sha256": initial_state_hash,
        "replay_assigns_sha256": sha256_assigns(manager.assigns),
        "terminal_assigns_sha256": sha256_assigns(terminal),
        "full_replay_vs_terminal_ari": terminal_ari,
        "full_replay_exact_match": exact_match,
        "source_files_unchanged": True,
        "api_calls": 0,
    }
    return samples, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channels", nargs="+", choices=sorted(CHANNEL_SPECS), default=["CH3", "CH31"])
    parser.add_argument("--mat-root", type=Path, default=Path("Tianmin_Annotated_data/data"))
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path("Jacob Bedke-Annotated_spike_sorting_data_w_chronux/action_sheet.xlsx"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/real_unified_action_smoke_20260903"),
    )
    parser.add_argument(
        "--max-rendered-actions",
        type=int,
        default=3,
        help="Render this many leading steps per channel; the complete log is still replayed and verified.",
    )
    args = parser.parse_args()
    if args.max_rendered_actions < 1:
        parser.error("--max-rendered-actions must be at least 1")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    workbook = load_xlsx_sheets(args.workbook)
    builder = FineTuneDatasetBuilder(
        output_dir=args.output_dir,
        seed=0,
        small_cluster_threshold=4000,
        final_minimum_threshold=500,
        profile="gemma4_train_reasoned",
        expert_only=True,
        fixed_target_format="reasoned_json",
        output_jsonl_name="samples.jsonl",
        summary_name="summary.json",
    )

    all_samples: list[dict[str, Any]] = []
    channel_summaries: list[dict[str, Any]] = []
    for channel in args.channels:
        spec = CHANNEL_SPECS[channel]
        sheet = spec["sheet"]
        if sheet not in workbook:
            raise KeyError(f"workbook sheet not found: {sheet}")
        print(f"Exporting {channel}: {len(workbook[sheet])} ordered Excel actions", flush=True)
        samples, summary = export_channel(
            channel=channel,
            rows=workbook[sheet],
            mat_path=args.mat_root / f"{channel}_spikes.mat",
            workbook_path=args.workbook,
            output_dir=args.output_dir,
            builder=builder,
            max_rendered_actions=args.max_rendered_actions,
        )
        all_samples.extend(samples)
        channel_summaries.append(summary)
        print(
            f"  rendered={len(samples)}, full_replay_exact={summary['full_replay_exact_match']}, "
            f"ARI={summary['full_replay_vs_terminal_ari']:.3f}",
            flush=True,
        )

    jsonl_path = args.output_dir / "samples.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for sample in all_samples:
            handle.write(json.dumps(jsonable(sample), ensure_ascii=False) + "\n")

    run_summary = {
        "schema_version": "real-unified-action-export-summary-v1",
        "scope": "real_data_only",
        "channels": channel_summaries,
        "n_rendered_samples": len(all_samples),
        "full_replay_validation_performed": True,
        "all_full_replays_exact": all(
            item["full_replay_exact_match"] for item in channel_summaries
        ),
        "source_files_unchanged": all(item["source_files_unchanged"] for item in channel_summaries),
        "api_calls": 0,
        "notes": [
            "Rendered samples are an ordered prefix; terminal validation uses the complete action log.",
            "No prefilter, automatic discard, synthetic action, or action reordering was applied.",
            "The exported prompt/target records are model-agnostic; no student model is selected here.",
        ],
    }
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(run_summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Saved {jsonl_path} and {summary_path}", flush=True)


if __name__ == "__main__":
    main()
