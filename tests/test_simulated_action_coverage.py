"""Regression checks for simulated trajectory action-coverage auditing."""

from __future__ import annotations

import json

from src.actions.coverage import audit_trajectory_coverage


def _write_trajectory(root, channel_id: str, actions: list[str]) -> None:
    path = root / "setting_test" / channel_id / "trajectory" / "trajectory.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps({"gt_action": action}) + "\n" for action in actions))


def _write_actions(root, channel_id: str, actions: list[str]) -> None:
    path = root / "setting_test" / channel_id / "actions" / "actions.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps({"action_type": action}) + "\n" for action in actions))


def test_coverage_separates_canonical_and_stage4_supervised_actions(tmp_path) -> None:
    _write_trajectory(tmp_path, "ch_000", ["KEEP", "SPLIT"])
    _write_trajectory(tmp_path, "ch_001", ["MERGE", "DISCARD"])
    adapter_dir = tmp_path / "setting_test" / "adapters" / "run_a"
    adapter_dir.mkdir(parents=True)
    (adapter_dir / "eval_dataset.jsonl").write_text(
        json.dumps({"train_channel_ids": ["ch_000"], "eval_channel_ids": ["ch_001"]})
    )

    report = audit_trajectory_coverage("setting_test", tmp_path)

    assert report["trajectory_action_counts"] == {
        "KEEP": 1,
        "SPLIT": 1,
        "MERGE": 1,
        "DISCARD": 1,
    }
    assert report["stage2_action_counts"] == {
        "KEEP": 0,
        "SPLIT": 0,
        "MERGE": 0,
        "DISCARD": 0,
    }
    assert report["stage3_gt_action_counts"] == report["trajectory_action_counts"]
    assert report["missing_trajectory_actions"] == []
    assert report["train_supervised_action_counts"] == {
        "KEEP": 0,
        "SPLIT": 1,
        "MERGE": 0,
        "DISCARD": 0,
    }
    assert report["missing_train_supervised_actions"] == ["MERGE", "DISCARD"]


def test_coverage_uses_stage2_actions_before_stage3_exists(tmp_path) -> None:
    _write_actions(tmp_path, "ch_000", ["KEEP", "SPLIT"])
    _write_actions(tmp_path, "ch_001", ["MERGE", "DISCARD"])

    report = audit_trajectory_coverage("setting_test", tmp_path)

    assert report["trajectory_action_counts"] == {
        "KEEP": 1,
        "SPLIT": 1,
        "MERGE": 1,
        "DISCARD": 1,
    }
    assert report["stage2_action_counts"] == report["trajectory_action_counts"]
    assert report["stage3_gt_action_counts"] == {
        "KEEP": 0,
        "SPLIT": 0,
        "MERGE": 0,
        "DISCARD": 0,
    }
    assert report["trajectory_source_by_channel"] == {
        "ch_000": "stage2_actions",
        "ch_001": "stage2_actions",
    }
