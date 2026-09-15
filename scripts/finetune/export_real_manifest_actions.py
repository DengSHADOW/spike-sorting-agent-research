"""Export ordered action observations for every labeled real MAT in a manifest.

The exporter preserves source order and provenance. Unified action/terminal
datasets must replay exactly; separate-target datasets retain both targets but
never claim that they describe one identical curation pass. No model/API call
is made and source files are opened read-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import adjusted_rand_score

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.analysis.audit_real_action_sources import load_xlsx_sheets, parse_action
from scripts.finetune.build_finetune_dataset import FineTuneDatasetBuilder
from scripts.finetune.export_real_unified_actions import (
    apply_action,
    jsonable,
    sha256_assigns,
    sha256_file,
)
from src.cluster.manager import ClusterManager
from src.io.matlab_loader import load_matlab_spikes


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(payload), indent=2, ensure_ascii=False) + "\n")


def _normalize_supervision(sample: dict[str, Any]) -> dict[str, Any]:
    """Keep rationale as metadata without inventing one when it is absent."""
    action = str(sample["label_action"]).strip().upper()
    rationale = str(sample.get("label_reason", "")).strip()
    target: dict[str, str] = {"action": action}
    if rationale:
        target["rationale"] = rationale
        sample["target_format"] = "reasoned_json"
        sample["output_mode"] = "reasoned"
    else:
        sample["target_format"] = "action_json"
        sample["output_mode"] = "action_only"
    sample["target"] = json.dumps(target, ensure_ascii=False)
    return sample


def _normalize_jsonl(path: Path) -> None:
    """Upgrade resumable outputs to the current supervision schema in place."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    normalized = [_normalize_supervision(row) for row in rows]
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in normalized:
            handle.write(json.dumps(jsonable(row), ensure_ascii=False) + "\n")
    tmp_path.replace(path)


def _source_rows(
    spec: dict[str, Any],
    data: dict[str, Any],
    workbook: dict[str, list[tuple[str, str]]],
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    source = str(spec["action_source"])
    if source == "excel":
        sheet = str(spec["dataset_id"])
        if sheet not in workbook:
            raise KeyError(f"Workbook sheet missing: {sheet}")
        return workbook[sheet], {"type": "excel", "sheet": sheet}
    if source == "mat_internal":
        actions = [str(item) for item in data.get("curation_actions", [])]
        reasons = [str(item) for item in data.get("curation_action_reasoning", [])]
        reasons.extend([""] * max(0, len(actions) - len(reasons)))
        return list(zip(actions, reasons)), {"type": "mat_internal", "field": "spikes.curation.action(s)"}
    raise ValueError(f"No supervised action source for {spec['dataset_id']}")


def _render_action(
    builder: FineTuneDatasetBuilder,
    dataset_id: str,
    channel: str,
    manager: ClusterManager,
    sampling_rate: float,
    raw_action: str,
    rationale: str,
    action_type: str,
    source_id: int,
    target_id: int | None,
    source_name: str,
) -> dict[str, Any]:
    if action_type in {"split", "discard"}:
        sample = builder._build_split_sample(
            channel=dataset_id,
            manager=manager,
            fs=sampling_rate,
            cluster_id=source_id,
            label_action=action_type.upper(),
            label_reason=rationale,
            source=source_name,
            expert_action_raw=raw_action,
        )
    else:
        sample = builder._build_merge_sample(
            channel=dataset_id,
            manager=manager,
            fs=sampling_rate,
            small_cluster_id=source_id,
            large_cluster_id=int(target_id),
            label_action="MERGE",
            label_reason=rationale,
            source=source_name,
            expert_action_raw=raw_action,
        )
    if sample is None:
        raise RuntimeError(f"Could not render {dataset_id} action {raw_action!r}")
    sample["channel"] = channel
    sample["dataset_id"] = dataset_id
    sample["profile"] = "real_manifest_ordered"
    return _normalize_supervision(sample)


def export_dataset(
    spec: dict[str, Any],
    workbook_path: Path,
    workbook: dict[str, list[tuple[str, str]]],
    dataset_dir: Path,
    root_output: Path,
    max_rendered_actions: int,
) -> dict[str, Any]:
    dataset_id = str(spec["dataset_id"])
    mat_path = Path(spec["mat_path"])
    mat_hash_before = sha256_file(mat_path)
    if mat_hash_before != spec["mat_sha256"]:
        raise RuntimeError(f"Manifest hash mismatch for {dataset_id}")
    data = load_matlab_spikes(str(mat_path))
    if data.get("curation_assigns") is None:
        raise RuntimeError(f"{dataset_id} has no terminal assignments")

    rows, annotation = _source_rows(spec, data, workbook)
    if len(rows) != int(spec["n_actions"]):
        raise RuntimeError(
            f"{dataset_id} action count changed: manifest={spec['n_actions']} current={len(rows)}"
        )
    if annotation["type"] == "excel":
        annotation.update({"path": str(workbook_path), "sha256": sha256_file(workbook_path)})
    else:
        annotation.update({"path": str(mat_path), "sha256": mat_hash_before})

    manager = ClusterManager(
        initial_assigns=data["hierarchy_assigns"],
        overcluster_assigns=data["overcluster_assigns"],
        hierarchy_tree=np.asarray(data["hierarchy_tree"]).copy(),
        spike_times=data["spiketimes"],
        waveforms=data["waveforms"],
    )
    builder = FineTuneDatasetBuilder(
        output_dir=dataset_dir,
        seed=0,
        small_cluster_threshold=4000,
        final_minimum_threshold=0,
        profile="gemma4_train_reasoned",
        expert_only=True,
        fixed_target_format="reasoned_json",
        output_jsonl_name="samples.jsonl",
        summary_name="builder_summary.json",
    )
    samples: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    initial_hash = sha256_assigns(manager.assigns)
    render_limit = len(rows) if max_rendered_actions <= 0 else min(max_rendered_actions, len(rows))

    for step_index, (raw_action, rationale) in enumerate(rows, start=1):
        action_type, source_id, target_id = parse_action(raw_action)
        counts[action_type.upper()] = counts.get(action_type.upper(), 0) + 1
        source_info = manager.get_cluster_info(source_id)
        if source_info is None:
            raise RuntimeError(f"{dataset_id} step {step_index}: inactive source {source_id}")
        active_before = manager.get_active_clusters()
        state_before = sha256_assigns(manager.assigns)
        sample = None
        if step_index <= render_limit:
            sample = _render_action(
                builder=builder,
                dataset_id=dataset_id,
                channel=str(spec["channel"]),
                manager=manager,
                sampling_rate=float(data["Fs"]),
                raw_action=raw_action,
                rationale=rationale,
                action_type=action_type,
                source_id=source_id,
                target_id=target_id,
                source_name=f"expert_{annotation['type']}_ordered",
            )
        application = apply_action(manager, action_type, source_id, target_id)
        if sample is not None:
            prefix = dataset_dir.relative_to(root_output)
            sample["images"] = {
                key: str(prefix / value) for key, value in sample["images"].items()
            }
            sample.update(
                {
                    "schema_version": "real-manifest-action-v1",
                    "trajectory_step": step_index,
                    "recording_block": spec["recording_block"],
                    "ground_truth_scope": spec["target_relation"],
                    "mat_source": {"path": str(mat_path), "sha256": mat_hash_before},
                    "annotation_source": annotation,
                    "preprocessing": {
                        "prefilter_small_overclusters": False,
                        "automatic_size_filter": False,
                        "synthetic_actions": False,
                        "action_reordering": False,
                    },
                    "state_before": {
                        "assigns_sha256": state_before,
                        "n_active_clusters": len(active_before),
                        "active_clusters": active_before,
                        "source_n_spikes": int(source_info["n_spikes"]),
                    },
                    "state_after": {
                        "assigns_sha256": sha256_assigns(manager.assigns),
                        "n_active_clusters": len(manager.get_active_clusters()),
                    },
                    "application": application,
                }
            )
            samples.append(sample)

    terminal = np.asarray(data["curation_assigns"])
    terminal_ari = float(adjusted_rand_score(terminal, manager.assigns))
    exact = bool(np.array_equal(terminal, manager.assigns))
    if spec["target_relation"] == "unified_action_and_terminal" and not exact:
        raise RuntimeError(f"Unified dataset {dataset_id} no longer replays exactly")
    if sha256_file(mat_path) != mat_hash_before:
        raise RuntimeError(f"Source MAT changed during export: {dataset_id}")

    sample_path = dataset_dir / "samples.jsonl"
    with sample_path.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(jsonable(sample), ensure_ascii=False) + "\n")
    summary = {
        "dataset_id": dataset_id,
        "channel": spec["channel"],
        "recording_block": spec["recording_block"],
        "target_relation": spec["target_relation"],
        "mat_path": str(mat_path),
        "mat_sha256": mat_hash_before,
        "annotation_source": annotation,
        "n_source_actions": len(rows),
        "n_rendered_actions": len(samples),
        "n_reasoning": sum(bool(reason) for _, reason in rows),
        "action_counts": dict(sorted(counts.items())),
        "initial_assigns_sha256": initial_hash,
        "replay_assigns_sha256": sha256_assigns(manager.assigns),
        "terminal_assigns_sha256": sha256_assigns(terminal),
        "replay_vs_terminal_ari": terminal_ari,
        "replay_exact_terminal": exact,
        "source_files_unchanged": True,
        "api_calls": 0,
    }
    _write_json(dataset_dir / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("output/real_split_manifest_20260903/manifest.json"),
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path("Jacob Bedke-Annotated_spike_sorting_data_w_chronux/action_sheet.xlsx"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/real_manifest_action_dataset_20260914"),
    )
    parser.add_argument("--dataset-id", action="append", default=[])
    parser.add_argument("--max-rendered-actions", type=int, default=0, help="0 renders all actions")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text())
    workbook = load_xlsx_sheets(args.workbook)
    requested = set(args.dataset_id)
    specs = [
        row
        for row in manifest["datasets"]
        if row["has_terminal_assigns"]
        and int(row["n_actions"]) > 0
        and (not requested or row["dataset_id"] in requested)
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)

    summaries: list[dict[str, Any]] = []
    for index, spec in enumerate(specs, start=1):
        dataset_id = str(spec["dataset_id"])
        dataset_dir = args.output_dir / "datasets" / dataset_id
        summary_path = dataset_dir / "summary.json"
        if args.resume and summary_path.exists():
            summaries.append(json.loads(summary_path.read_text()))
            print(f"[{index}/{len(specs)}] resume {dataset_id}", flush=True)
            continue
        print(f"[{index}/{len(specs)}] export {dataset_id}", flush=True)
        summaries.append(
            export_dataset(
                spec=spec,
                workbook_path=args.workbook,
                workbook=workbook,
                dataset_dir=dataset_dir,
                root_output=args.output_dir,
                max_rendered_actions=args.max_rendered_actions,
            )
        )

    combined_path = args.output_dir / "samples.jsonl"
    n_combined_samples = 0
    n_image_references = 0
    target_modes: dict[str, int] = {}
    with combined_path.open("w", encoding="utf-8") as output:
        for summary in summaries:
            path = args.output_dir / "datasets" / summary["dataset_id"] / "samples.jsonl"
            _normalize_jsonl(path)
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                sample = json.loads(line)
                mode = str(sample["output_mode"])
                target_modes[mode] = target_modes.get(mode, 0) + 1
                for image_path in sample["images"].values():
                    n_image_references += 1
                    if not (args.output_dir / image_path).is_file():
                        raise FileNotFoundError(f"Missing sample image: {image_path}")
                output.write(json.dumps(jsonable(sample), ensure_ascii=False) + "\n")
                n_combined_samples += 1

    total_counts: dict[str, int] = {}
    for summary in summaries:
        for action, count in summary["action_counts"].items():
            total_counts[action] = total_counts.get(action, 0) + int(count)
    run_summary = {
        "schema_version": "real-manifest-action-export-summary-v1",
        "scope": "real_data_only",
        "manifest": str(args.manifest),
        "excluded_unlabeled": [
            row["dataset_id"] for row in manifest["datasets"] if not row["has_terminal_assigns"]
        ],
        "n_datasets": len(summaries),
        "n_source_actions": sum(row["n_source_actions"] for row in summaries),
        "n_rendered_samples": sum(row["n_rendered_actions"] for row in summaries),
        "n_reasoning": sum(row["n_reasoning"] for row in summaries),
        "target_modes": dict(sorted(target_modes.items())),
        "n_image_references": n_image_references,
        "all_image_references_exist": True,
        "action_counts": dict(sorted(total_counts.items())),
        "n_unified": sum(row["target_relation"] == "unified_action_and_terminal" for row in summaries),
        "n_separate": sum(row["target_relation"] == "separate_action_and_terminal" for row in summaries),
        "source_files_unchanged": all(row["source_files_unchanged"] for row in summaries),
        "api_calls": 0,
        "datasets": summaries,
    }
    if n_combined_samples != run_summary["n_rendered_samples"]:
        raise RuntimeError(
            f"Combined sample count mismatch: {n_combined_samples} != {run_summary['n_rendered_samples']}"
        )
    _write_json(args.output_dir / "summary.json", run_summary)
    print(
        f"Saved {len(summaries)} datasets / {run_summary['n_rendered_samples']} samples to {args.output_dir}",
        flush=True,
    )


if __name__ == "__main__":
    main()
