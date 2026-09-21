import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "analysis"
    / "run_numeric_action_baseline.py"
)
SPEC = importlib.util.spec_from_file_location("run_numeric_action_baseline", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _split_row(**overrides):
    row = {
        "id": "sample",
        "stage": "split",
        "label_action": "SPLIT",
        "recording_block": "train-a",
        "metrics": {
            "n_spikes": 99,
            "n_overclusters": 9,
            "isi_violation_rate": 0.02,
            "amplitude_cv": 0.1,
        },
        "preprocessing": {"synthetic_actions": False},
    }
    row.update(overrides)
    return row


def test_split_features_apply_fixed_log_transforms() -> None:
    features = MODULE.split_features(_split_row())
    assert features[0] == pytest.approx(4.605170186)
    assert features[1] == pytest.approx(2.302585093)
    assert features[2:] == [0.02, 0.1]


def test_split_features_reject_missing_metric() -> None:
    row = _split_row()
    del row["metrics"]["amplitude_cv"]
    with pytest.raises(ValueError, match="amplitude_cv"):
        MODULE.split_features(row)


def test_recording_block_overlap_is_rejected() -> None:
    with pytest.raises(ValueError, match="leakage"):
        MODULE._validate_split(("block-a", "block-b"), ("block-b",))


def test_synthetic_rows_are_excluded() -> None:
    real = _split_row(id="real")
    synthetic = _split_row(
        id="synthetic",
        preprocessing={"synthetic_actions": True},
    )
    selected = MODULE._select_rows([real, synthetic], ("train-a",))
    assert [row["id"] for row in selected] == ["real"]


def test_metrics_use_fixed_label_order_and_zero_division() -> None:
    report = MODULE._metrics(
        ["DISCARD", "SPLIT", "SPLIT"],
        ["SPLIT", "SPLIT", "SPLIT"],
        MODULE.SPLIT_LABELS,
    )
    assert report["accuracy"] == pytest.approx(2 / 3)
    assert report["per_action"]["DISCARD"]["recall"] == 0.0
    assert report["confusion_matrix"]["DISCARD"]["SPLIT"] == 1
