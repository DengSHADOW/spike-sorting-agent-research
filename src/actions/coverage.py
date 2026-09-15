"""Coverage audit for simulated curation trajectories and Stage 4 datasets."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional


ACTION_TYPES = ("KEEP", "SPLIT", "MERGE", "DISCARD")


def _empty_counts() -> dict[str, int]:
    return {action: 0 for action in ACTION_TYPES}


def _normalise_counts(counts: Counter[str]) -> dict[str, int]:
    result = _empty_counts()
    for action, count in counts.items():
        result[action] = int(count)
    return result


def _count_action_rows(path: Path) -> Counter[str]:
    """Count labels from either a Stage 2 or Stage 3 JSONL artifact."""
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return Counter(
        str(row.get("gt_action", row.get("action_type", ""))).upper()
        for row in rows
    )


def _load_split(
    setting_dir: Path,
    adapter_run_id: Optional[str],
) -> tuple[set[str], set[str], Optional[str]]:
    adapters_dir = setting_dir / "adapters"
    if not adapters_dir.exists():
        return set(), set(), None

    if adapter_run_id is None:
        candidates = sorted(adapters_dir.glob("*/eval_dataset.jsonl"))
        if not candidates:
            return set(), set(), None
        split_path = candidates[-1]
    else:
        split_path = adapters_dir / adapter_run_id / "eval_dataset.jsonl"
        if not split_path.exists():
            raise FileNotFoundError(f"Adapter split metadata not found: {split_path}")

    with open(split_path, encoding="utf-8") as f:
        data = json.load(f)
    return (
        set(data.get("train_channel_ids", [])),
        set(data.get("eval_channel_ids", [])),
        split_path.parent.name,
    )


def audit_trajectory_coverage(
    setting_id: str,
    output_dir: str | Path,
    adapter_run_id: Optional[str] = None,
    required_actions: Iterable[str] = ACTION_TYPES,
) -> dict:
    """Summarise action-class coverage without changing any trajectories.

    Stage 4 deliberately excludes KEEP from SFT examples, so the report keeps
    canonical trajectory coverage and supervised-dataset coverage separate.
    """
    setting_dir = Path(output_dir) / setting_id
    train_ids, eval_ids, selected_adapter_run = _load_split(setting_dir, adapter_run_id)
    required = tuple(required_actions)

    total_counts: Counter[str] = Counter()
    split_counts: dict[str, Counter[str]] = {
        "train": Counter(),
        "eval": Counter(),
        "unused": Counter(),
    }
    supervised_counts: Counter[str] = Counter()
    channels_with_trajectory: list[str] = []
    trajectory_source_by_channel: dict[str, str] = {}
    stage2_counts: Counter[str] = Counter()
    stage3_counts: Counter[str] = Counter()
    stage2_channels: list[str] = []
    stage3_channels: list[str] = []

    # Stage 3 rows carry the teacher/student decision in ``gt_action``.  Before
    # Stage 3 has been run, the canonical Stage 2 rows are the only available
    # evidence of action coverage and carry the same label as ``action_type``.
    # Prefer Stage 3 where it exists so later reports describe the trajectory
    # actually used for adaptation/evaluation.
    paths_by_channel: dict[str, tuple[Path, str]] = {}
    for path in sorted(setting_dir.glob("ch_*/actions/actions.jsonl")):
        channel_id = path.parents[1].name
        paths_by_channel[channel_id] = (path, "stage2_actions")
        stage2_channels.append(channel_id)
        stage2_counts.update(_count_action_rows(path))
    for path in sorted(setting_dir.glob("ch_*/trajectory/trajectory.jsonl")):
        channel_id = path.parents[1].name
        paths_by_channel[channel_id] = (path, "stage3_trajectory")
        stage3_channels.append(channel_id)
        stage3_counts.update(_count_action_rows(path))

    for channel_id, (path, source) in sorted(paths_by_channel.items()):
        channels_with_trajectory.append(channel_id)
        trajectory_source_by_channel[channel_id] = source
        split_name = "train" if channel_id in train_ids else "eval" if channel_id in eval_ids else "unused"
        counts = _count_action_rows(path)
        total_counts.update(counts)
        split_counts[split_name].update(counts)
        if split_name == "train":
            supervised_counts.update(action for action in counts.elements() if action != "KEEP")

    all_channels = sorted(path.name for path in setting_dir.glob("ch_*") if path.is_dir())
    missing_trajectory_channels = sorted(set(all_channels) - set(channels_with_trajectory))
    total_normalised = _normalise_counts(total_counts)
    supervised_normalised = _normalise_counts(supervised_counts)

    return {
        "setting_id": setting_id,
        "adapter_run_id": selected_adapter_run,
        "n_trajectory_channels": len(channels_with_trajectory),
        "trajectory_source_by_channel": trajectory_source_by_channel,
        "missing_trajectory_channels": missing_trajectory_channels,
        "n_stage2_channels": len(stage2_channels),
        "stage2_action_counts": _normalise_counts(stage2_counts),
        "n_stage3_channels": len(stage3_channels),
        "stage3_gt_action_counts": _normalise_counts(stage3_counts),
        "trajectory_action_counts": total_normalised,
        "trajectory_action_counts_by_split": {
            name: _normalise_counts(counts) for name, counts in split_counts.items()
        },
        "missing_trajectory_actions": [action for action in required if total_normalised.get(action, 0) == 0],
        "train_supervised_action_counts": supervised_normalised,
        "missing_train_supervised_actions": [
            action for action in required if action != "KEEP" and supervised_normalised.get(action, 0) == 0
        ],
        "note": "Stage 4 excludes KEEP examples by design; KEEP is audited only in trajectory coverage.",
    }
