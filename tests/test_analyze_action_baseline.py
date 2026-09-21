import csv
import importlib.util
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "analysis"
    / "analyze_action_baseline.py"
)
SPEC = importlib.util.spec_from_file_location("analyze_action_baseline", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_action_metrics_and_format_accounting() -> None:
    predictions = [
        {
            "idx": "1", "id": "a", "stage": "split",
            "allowed_actions": "KEEP,DISCARD,SPLIT", "gt_action": "DISCARD",
            "pred_action": "DISCARD", "raw_response": "DISCARD",
        },
        {
            "idx": "2", "id": "b", "stage": "split",
            "allowed_actions": "KEEP,DISCARD,SPLIT", "gt_action": "SPLIT",
            "pred_action": "DISCARD", "raw_response": '{"action":"DISCARD"}',
        },
        {
            "idx": "3", "id": "c", "stage": "merge",
            "allowed_actions": "MERGE,NOT_MERGE,DISCARD", "gt_action": "MERGE",
            "pred_action": "MERGE", "raw_response": '{"action":"MERGE"}',
        },
        {
            "idx": "4", "id": "d", "stage": "split",
            "allowed_actions": "KEEP,DISCARD,SPLIT", "gt_action": "SPLIT",
            "pred_action": "INVALID_ACTION", "raw_response": '{"action":"SPLIT"',
        },
    ]
    samples = {
        row_id: {"id": row_id, "metrics": {"n_spikes": n}}
        for row_id, n in zip("abcd", (10, 20, 30, 40))
    }

    report, errors = MODULE.analyze_predictions(predictions, samples)

    assert report["n_samples"] == 4
    assert report["correct"] == 2
    assert report["accuracy"] == 0.5
    assert report["per_action"]["DISCARD"]["precision"] == 0.5
    assert report["per_action"]["SPLIT"]["recall"] == 0.0
    assert report["format"]["parsed_valid"] == 3
    assert report["format"]["valid_complete_json_action"] == 2
    assert report["format"]["strict_single_action"] == 1
    assert report["format"]["recoverable_invalid_action_prefix"] == 1
    assert report["format"]["recoverable_invalid_correct"] == 1
    assert report["joined_sample_count"] == 4
    assert len(errors) == 2


def test_writes_confusion_and_error_tables(tmp_path: Path) -> None:
    predictions = [
        {
            "idx": "1", "id": "a", "stage": "split",
            "allowed_actions": "DISCARD,SPLIT", "gt_action": "SPLIT",
            "pred_action": "DISCARD", "raw_response": "DISCARD",
        }
    ]
    report, errors = MODULE.analyze_predictions(predictions)
    confusion_path = tmp_path / "confusion.csv"
    errors_path = tmp_path / "errors.csv"
    MODULE._write_confusion_csv(confusion_path, report)
    MODULE._write_errors_csv(errors_path, errors)

    with confusion_path.open() as stream:
        confusion_rows = list(csv.reader(stream))
    assert confusion_rows[0] == ["gt_action", "DISCARD"]
    assert confusion_rows[1] == ["SPLIT", "1"]
    assert "metrics_json" in errors_path.read_text()
