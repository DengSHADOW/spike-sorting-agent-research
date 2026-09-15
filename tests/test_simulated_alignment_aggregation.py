"""Regression tests for simulated Stage 5 report aggregation."""

from __future__ import annotations

import json

from src.alignment.report import aggregate_setting_reports


def _write_report(root, channel_id: str, accuracy: float) -> None:
    report_dir = root / "setting_test" / channel_id / "eval"
    report_dir.mkdir(parents=True)
    (report_dir / "alignment_report.json").write_text(
        json.dumps(
            {
                "channel_id": channel_id,
                "setting_id": "setting_test",
                "action_accuracy": accuracy,
                "edit_distance": 1,
                "reasoning_cosine_sim": 0.5,
            }
        )
    )


def test_aggregate_only_includes_requested_channels(tmp_path) -> None:
    _write_report(tmp_path, "ch_000", 0.1)
    _write_report(tmp_path, "ch_001", 0.5)
    _write_report(tmp_path, "ch_002", 0.9)

    summary = aggregate_setting_reports(
        setting_id="setting_test",
        output_dir=tmp_path,
        channel_ids=["ch_001", "ch_002", "ch_missing"],
    )

    assert summary["n_channels"] == 2
    assert summary["channel_ids"] == ["ch_001", "ch_002"]
    assert summary["missing_channel_ids"] == ["ch_missing"]
    assert summary["mean_action_accuracy"] == 0.7
