"""Regression tests for the simulated Stage 2 action trajectory."""

from __future__ import annotations

import json

import numpy as np

from src.actions.trajectory import build_gt_trajectory
from src.actions.validator import validate_trajectory


def _write_raw_arrays(raw_dir, *, overclusters: np.ndarray) -> None:
    raw_dir.mkdir(parents=True)
    n_spikes = len(overclusters)
    np.save(raw_dir / "waveforms.npy", np.zeros((n_spikes, 8), dtype=np.float32))
    np.save(raw_dir / "spike_times.npy", np.arange(n_spikes, dtype=float) / 1000.0)
    np.save(raw_dir / "overcluster_assigns.npy", overclusters)
    np.save(raw_dir / "hierarchy_assigns.npy", np.full(n_spikes, 5, dtype=int))
    np.save(raw_dir / "hierarchy_tree.npy", np.empty((4, 0), dtype=float))
    np.save(
        raw_dir / "gt_assigns.npy",
        np.array([1] * (n_spikes // 2) + [2] * (n_spikes - n_spikes // 2)),
    )


def test_unsplittable_cluster_stops_without_recording_fake_action(tmp_path) -> None:
    raw_dir = tmp_path / "setting_test" / "ch_017" / "raw"
    output_dir = tmp_path / "actions"
    _write_raw_arrays(raw_dir, overclusters=np.full(10, 5, dtype=int))

    steps = build_gt_trajectory(
        raw_dir,
        output_dir=output_dir,
        auto_discard_threshold=0,
        max_steps=10,
        force=True,
    )

    assert steps == []
    summary = json.loads((output_dir / "trajectory_summary.json").read_text())
    assert summary["completed"] is False
    assert summary["termination_reason"] == "split_no_progress"
    assert summary["blocked_action"]["action_type"] == "SPLIT"
    assert summary["blocked_action"]["n_overclusters"] == 1
    assert (output_dir / "actions.jsonl").read_text() == ""


def test_valid_overcluster_split_still_completes(tmp_path) -> None:
    raw_dir = tmp_path / "setting_test" / "ch_valid" / "raw"
    output_dir = tmp_path / "actions"
    _write_raw_arrays(
        raw_dir,
        overclusters=np.array([5, 5, 5, 7, 7, 7], dtype=int),
    )

    steps = build_gt_trajectory(
        raw_dir,
        output_dir=output_dir,
        auto_discard_threshold=0,
        max_steps=10,
        force=True,
    )

    assert [step["action_type"] for step in steps] == ["SPLIT", "KEEP"]
    assert steps[0]["n_active_before"] == 1
    assert steps[0]["n_active_after"] == 2
    summary = json.loads((output_dir / "trajectory_summary.json").read_text())
    assert summary["completed"] is True
    assert summary["termination_reason"] == "all_clusters_keep"
    assert validate_trajectory(steps).valid is True


def test_validator_rejects_legacy_no_progress_split() -> None:
    result = validate_trajectory(
        [
            {
                "step": 0,
                "action_type": "SPLIT",
                "cluster_id": 5,
                "target_id": None,
                "n_active_before": 3,
                "n_active_after": 3,
            }
        ]
    )

    assert result.valid is False
    assert "SPLIT made no progress" in result.errors[0]
