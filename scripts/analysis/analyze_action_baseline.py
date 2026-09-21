"""Create a consistent action-level report from fixed-state VLM predictions.

The report keeps model actions separate from output-format failures and can join
the exported sample manifest to summarize numeric features and expert reasons.
It intentionally produces tables/JSON only; diagnostic images and raw datasets
are never copied.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


INVALID_PREDICTIONS = {"INVALID_ACTION", "PARSE_ERROR", "MISSING_IMAGE", "ABSTAIN"}
PREFERRED_ACTION_ORDER = ("DISCARD", "MERGE", "SPLIT", "KEEP", "NOT_MERGE")


def _ordered_labels(labels: Iterable[str]) -> list[str]:
    unique = {str(label).upper() for label in labels}
    preferred = [label for label in PREFERRED_ACTION_ORDER if label in unique]
    return preferred + sorted(unique.difference(preferred))


def _load_predictions(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    required = {"id", "stage", "gt_action", "pred_action", "raw_response"}
    if not rows:
        raise ValueError(f"No predictions found in {path}")
    missing = required.difference(rows[0])
    if missing:
        raise ValueError(f"Missing prediction columns: {sorted(missing)}")
    return rows


def _load_samples(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    samples: dict[str, dict[str, Any]] = {}
    with path.open() as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                samples[str(row["id"])] = row
    return samples


def _safe_ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _allowed_actions(row: dict[str, str]) -> set[str]:
    return {part.strip().upper() for part in row.get("allowed_actions", "").split(",") if part.strip()}


def _json_action(raw: str) -> str | None:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or "action" not in value:
        return None
    return str(value["action"]).strip().upper()


def _recover_action_prefix(raw: str) -> str | None:
    match = re.search(r'["\']action["\']\s*:\s*["\']([A-Za-z_]+)', raw or "", re.IGNORECASE)
    return match.group(1).upper() if match else None


def _numeric_summary(samples: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    values: dict[str, list[float]] = defaultdict(list)
    for sample in samples:
        for key, value in (sample.get("metrics") or {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values[str(key)].append(float(value))
    summary: dict[str, dict[str, float | int]] = {}
    for key, numbers in sorted(values.items()):
        summary[key] = {
            "n": len(numbers),
            "min": min(numbers),
            "median": statistics.median(numbers),
            "mean": statistics.mean(numbers),
            "max": max(numbers),
        }
    return summary


def analyze_predictions(
    predictions: list[dict[str, str]],
    samples_by_id: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    samples_by_id = samples_by_id or {}
    gt_labels = _ordered_labels(row["gt_action"] for row in predictions)
    pred_labels = _ordered_labels(row["pred_action"] for row in predictions)
    confusion = {
        gt: {pred: 0 for pred in pred_labels}
        for gt in gt_labels
    }
    errors: list[dict[str, Any]] = []
    strict_count = 0
    json_valid_count = 0
    parsed_valid_count = 0
    recoverable_invalid_count = 0
    recoverable_invalid_correct = 0

    for row in predictions:
        gt = row["gt_action"].upper()
        pred = row["pred_action"].upper()
        raw = row.get("raw_response", "")
        allowed = _allowed_actions(row)
        confusion[gt][pred] += 1
        if pred not in INVALID_PREDICTIONS:
            parsed_valid_count += 1
        if raw.strip().upper() == pred and pred in allowed:
            strict_count += 1
        parsed_json_action = _json_action(raw)
        if parsed_json_action is not None and parsed_json_action in allowed:
            json_valid_count += 1
        if pred in INVALID_PREDICTIONS:
            recovered = _recover_action_prefix(raw)
            if recovered in allowed:
                recoverable_invalid_count += 1
                recoverable_invalid_correct += int(recovered == gt)
        if pred != gt:
            sample = samples_by_id.get(str(row["id"]), {})
            errors.append(
                {
                    "idx": row.get("idx", ""),
                    "id": row["id"],
                    "stage": row["stage"],
                    "gt_action": gt,
                    "pred_action": pred,
                    "label_reason": sample.get("label_reason", ""),
                    "metrics": sample.get("metrics", {}),
                    "raw_response": raw,
                }
            )

    per_action: dict[str, dict[str, float | int]] = {}
    for action in gt_labels:
        tp = confusion[action].get(action, 0)
        fp = sum(confusion[other].get(action, 0) for other in gt_labels if other != action)
        fn = sum(count for pred, count in confusion[action].items() if pred != action)
        precision = _safe_ratio(tp, tp + fp)
        recall = _safe_ratio(tp, tp + fn)
        per_action[action] = {
            "support": tp + fn,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "f1": _safe_ratio(2 * precision * recall, precision + recall),
        }

    n = len(predictions)
    correct = sum(int(row["gt_action"].upper() == row["pred_action"].upper()) for row in predictions)
    stage_gt_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in predictions:
        stage_gt_counts[row["stage"]][row["gt_action"].upper()] += 1
    same_set_stage_majority = {
        stage: counts.most_common(1)[0][0]
        for stage, counts in stage_gt_counts.items()
    }
    stage_majority_correct = sum(
        int(same_set_stage_majority[row["stage"]] == row["gt_action"].upper())
        for row in predictions
    )

    samples_by_gt: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in predictions:
        sample = samples_by_id.get(str(row["id"]))
        if sample is not None:
            samples_by_gt[row["gt_action"].upper()].append(sample)

    report = {
        "schema_version": "action-baseline-analysis-v1",
        "n_samples": n,
        "correct": correct,
        "accuracy": _safe_ratio(correct, n),
        "macro_f1": statistics.mean(float(item["f1"]) for item in per_action.values()),
        "gt_counts": dict(Counter(row["gt_action"].upper() for row in predictions)),
        "prediction_counts": dict(Counter(row["pred_action"].upper() for row in predictions)),
        "per_action": per_action,
        "confusion_matrix": confusion,
        "format": {
            "parsed_valid": parsed_valid_count,
            "parsed_valid_rate": _safe_ratio(parsed_valid_count, n),
            "valid_complete_json_action": json_valid_count,
            "valid_complete_json_action_rate": _safe_ratio(json_valid_count, n),
            "strict_single_action": strict_count,
            "strict_single_action_rate": _safe_ratio(strict_count, n),
            "recoverable_invalid_action_prefix": recoverable_invalid_count,
            "recoverable_invalid_correct": recoverable_invalid_correct,
        },
        "same_evaluation_set_stage_majority_reference": {
            "warning": "Diagnostic only: majority actions were selected on this evaluation set.",
            "action_by_stage": same_set_stage_majority,
            "correct": stage_majority_correct,
            "accuracy": _safe_ratio(stage_majority_correct, n),
        },
        "numeric_metrics_by_gt": {
            gt: _numeric_summary(samples)
            for gt, samples in sorted(samples_by_gt.items())
        },
        "joined_sample_count": sum(len(samples) for samples in samples_by_gt.values()),
    }
    return report, errors


def _write_confusion_csv(path: Path, report: dict[str, Any]) -> None:
    confusion = report["confusion_matrix"]
    pred_labels = _ordered_labels(
        pred for row in confusion.values() for pred in row
    )
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["gt_action", *pred_labels])
        for gt, row in confusion.items():
            writer.writerow([gt, *(row.get(pred, 0) for pred in pred_labels)])


def _write_errors_csv(path: Path, errors: list[dict[str, Any]]) -> None:
    fieldnames = (
        "idx", "id", "stage", "gt_action", "pred_action",
        "label_reason", "metrics_json", "raw_response",
    )
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for error in errors:
            writer.writerow(
                {
                    **{key: error[key] for key in fieldnames if key not in {"metrics_json"}},
                    "metrics_json": json.dumps(error["metrics"], ensure_ascii=False, sort_keys=True),
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize fixed-state action predictions.")
    parser.add_argument("--predictions-csv", type=Path, required=True)
    parser.add_argument("--samples-jsonl", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    predictions = _load_predictions(args.predictions_csv)
    samples = _load_samples(args.samples_jsonl)
    report, errors = analyze_predictions(predictions, samples)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "evaluation_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )
    _write_confusion_csv(args.output_dir / "confusion_matrix.csv", report)
    _write_errors_csv(args.output_dir / "error_cases.csv", errors)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
