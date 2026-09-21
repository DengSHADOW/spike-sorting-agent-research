"""Train leakage-aware numeric-only action baselines on real curation logs.

The learned task is the split-stage DISCARD-versus-SPLIT decision because the
audited action logs contain no negative merge decisions.  Merge-stage rows are
therefore handled by an explicit constant-MERGE reference and reported as a
positive-only limitation, not as learned merge performance.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import joblib
import sklearn
from sklearn.base import clone
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
from sklearn.model_selection import LeaveOneGroupOut


DEFAULT_TRAIN_BLOCKS = (
    "cM2-e004_001-003",
    "cM2-e004_011-015",
    "cM2-e007_012-017",
)
DEFAULT_EVAL_BLOCKS = ("cM2-e008_021-028",)
SPLIT_LABELS = ("DISCARD", "SPLIT")
OVERALL_LABELS = ("DISCARD", "MERGE", "SPLIT")
FEATURE_NAMES = (
    "log1p_n_spikes",
    "log1p_n_overclusters",
    "isi_violation_rate",
    "amplitude_cv",
)


def _parse_values(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open() as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected object at {path}:{line_number}")
            rows.append(value)
    if not rows:
        raise ValueError(f"No rows found in {path}")
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _select_rows(
    rows: Sequence[dict[str, Any]],
    recording_blocks: Iterable[str],
) -> list[dict[str, Any]]:
    wanted = set(recording_blocks)
    selected = []
    for row in rows:
        if str(row.get("recording_block", "")) not in wanted:
            continue
        if bool((row.get("preprocessing") or {}).get("synthetic_actions", False)):
            continue
        selected.append(row)
    return selected


def _numeric_value(metrics: dict[str, Any], key: str, row_id: str) -> float:
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{row_id}: missing/non-numeric metric {key!r}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{row_id}: non-finite metric {key!r}")
    return result


def split_features(row: dict[str, Any]) -> list[float]:
    """Return the fixed numeric feature vector for one split-stage state."""
    if str(row.get("stage", "")).lower() != "split":
        raise ValueError(f"{row.get('id', '<unknown>')}: expected split-stage row")
    metrics = row.get("metrics") or {}
    row_id = str(row.get("id", "<unknown>"))
    n_spikes = _numeric_value(metrics, "n_spikes", row_id)
    n_overclusters = _numeric_value(metrics, "n_overclusters", row_id)
    if n_spikes < 0 or n_overclusters < 0:
        raise ValueError(f"{row_id}: count metrics must be non-negative")
    return [
        math.log1p(n_spikes),
        math.log1p(n_overclusters),
        _numeric_value(metrics, "isi_violation_rate", row_id),
        _numeric_value(metrics, "amplitude_cv", row_id),
    ]


def _xyg(rows: Sequence[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    split_rows = [row for row in rows if str(row.get("stage", "")).lower() == "split"]
    labels = np.asarray([str(row["label_action"]).upper() for row in split_rows], dtype=object)
    unsupported = sorted(set(labels).difference(SPLIT_LABELS))
    if unsupported:
        raise ValueError(f"Unexpected split-stage labels: {unsupported}")
    features = np.asarray([split_features(row) for row in split_rows], dtype=float)
    groups = np.asarray([str(row["recording_block"]) for row in split_rows], dtype=object)
    return features, labels, groups


def _metrics(y_true: Sequence[str], y_pred: Sequence[str], labels: Sequence[str]) -> dict[str, Any]:
    true = np.asarray(y_true, dtype=object)
    pred = np.asarray(y_pred, dtype=object)
    precision, recall, f1, support = precision_recall_fscore_support(
        true,
        pred,
        labels=list(labels),
        zero_division=0,
    )
    per_action = {
        label: {
            "support": int(support[index]),
            "precision": float(precision[index]),
            "recall": float(recall[index]),
            "f1": float(f1[index]),
        }
        for index, label in enumerate(labels)
    }
    return {
        "n_samples": int(len(true)),
        "correct": int(np.sum(true == pred)),
        "accuracy": float(accuracy_score(true, pred)),
        "macro_f1": float(np.mean(f1)),
        "gt_counts": dict(Counter(str(value) for value in true)),
        "prediction_counts": dict(Counter(str(value) for value in pred)),
        "per_action": per_action,
        "confusion_matrix": {
            true_label: {
                pred_label: int(value)
                for pred_label, value in zip(labels, row)
            }
            for true_label, row in zip(
                labels,
                confusion_matrix(true, pred, labels=list(labels)),
            )
        },
    }


def build_models(seed: int, rf_estimators: int = 500) -> dict[str, Any]:
    return {
        "stage_majority": DummyClassifier(strategy="most_frequent"),
        "random_forest": RandomForestClassifier(
            n_estimators=rf_estimators,
            max_features="sqrt",
            min_samples_leaf=5,
            class_weight="balanced_subsample",
            random_state=seed,
            n_jobs=-1,
        ),
        "hist_gradient_boosting": HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=200,
            max_leaf_nodes=15,
            min_samples_leaf=20,
            l2_regularization=1.0,
            class_weight="balanced",
            random_state=seed,
        ),
    }


def _group_oof(
    model: Any,
    features: np.ndarray,
    labels: np.ndarray,
    groups: np.ndarray,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    unique_groups = sorted(set(str(value) for value in groups))
    if len(unique_groups) < 2:
        raise ValueError("At least two train recording blocks are required for grouped CV")
    predictions = np.empty(len(labels), dtype=object)
    fold_reports: list[dict[str, Any]] = []
    for train_indices, holdout_indices in LeaveOneGroupOut().split(features, labels, groups):
        estimator = clone(model)
        estimator.fit(features[train_indices], labels[train_indices])
        fold_predictions = estimator.predict(features[holdout_indices])
        predictions[holdout_indices] = fold_predictions
        fold_reports.append(
            {
                "holdout_recording_block": str(groups[holdout_indices][0]),
                **_metrics(labels[holdout_indices], fold_predictions, SPLIT_LABELS),
            }
        )
    return predictions, fold_reports


def _validate_split(train_blocks: Sequence[str], eval_blocks: Sequence[str]) -> None:
    overlap = sorted(set(train_blocks) & set(eval_blocks))
    if overlap:
        raise ValueError(f"Recording-block leakage between train/eval: {overlap}")
    if not train_blocks or not eval_blocks:
        raise ValueError("Both train and eval recording blocks are required")


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[dict[str, Any]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-jsonl",
        type=Path,
        default=Path("output/real_manifest_action_dataset_20260914/samples.jsonl"),
    )
    parser.add_argument(
        "--train-recording-blocks",
        default=",".join(DEFAULT_TRAIN_BLOCKS),
    )
    parser.add_argument(
        "--eval-recording-blocks",
        default=",".join(DEFAULT_EVAL_BLOCKS),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/numeric_action_baseline_20260921"),
    )
    parser.add_argument("--seed", type=int, default=3407)
    parser.add_argument("--rf-estimators", type=int, default=500)
    args = parser.parse_args()

    train_blocks = _parse_values(args.train_recording_blocks)
    eval_blocks = _parse_values(args.eval_recording_blocks)
    _validate_split(train_blocks, eval_blocks)
    rows = _read_jsonl(args.input_jsonl)
    train_rows = _select_rows(rows, train_blocks)
    eval_rows = _select_rows(rows, eval_blocks)
    if not train_rows or not eval_rows:
        raise ValueError("No train/eval rows after recording-block filtering")

    x_train, y_train, train_groups = _xyg(train_rows)
    x_eval, y_eval_split, _ = _xyg(eval_rows)
    models = build_models(seed=args.seed, rf_estimators=args.rf_estimators)

    cv_results: dict[str, Any] = {}
    for name, model in models.items():
        oof_predictions, folds = _group_oof(model, x_train, y_train, train_groups)
        cv_results[name] = {
            "pooled": _metrics(y_train, oof_predictions, SPLIT_LABELS),
            "folds": folds,
        }

    learned_names = ("random_forest", "hist_gradient_boosting")
    primary_name = max(
        learned_names,
        key=lambda name: (
            cv_results[name]["pooled"]["macro_f1"],
            cv_results[name]["pooled"]["accuracy"],
            -learned_names.index(name),
        ),
    )

    fitted_models: dict[str, Any] = {}
    eval_split_predictions: dict[str, np.ndarray] = {}
    for name, model in models.items():
        estimator = clone(model)
        estimator.fit(x_train, y_train)
        fitted_models[name] = estimator
        eval_split_predictions[name] = estimator.predict(x_eval)

    eval_gt = np.asarray([str(row["label_action"]).upper() for row in eval_rows], dtype=object)
    split_cursor_by_model = {name: 0 for name in models}
    overall_predictions = {name: [] for name in models}
    detail_rows: list[dict[str, Any]] = []
    for row, gt in zip(eval_rows, eval_gt):
        stage = str(row["stage"]).lower()
        predictions_for_row: dict[str, str] = {}
        for name in models:
            if stage == "split":
                cursor = split_cursor_by_model[name]
                prediction = str(eval_split_predictions[name][cursor])
                split_cursor_by_model[name] += 1
            elif stage == "merge":
                prediction = "MERGE"
            else:
                raise ValueError(f"Unsupported stage {stage!r} in {row.get('id')}")
            predictions_for_row[name] = prediction
            overall_predictions[name].append(prediction)
        detail_rows.append(
            {
                "id": str(row["id"]),
                "dataset_id": str(row["dataset_id"]),
                "recording_block": str(row["recording_block"]),
                "stage": stage,
                "gt_action": str(gt),
                "pred_stage_majority": predictions_for_row["stage_majority"],
                "pred_random_forest": predictions_for_row["random_forest"],
                "pred_hist_gradient_boosting": predictions_for_row["hist_gradient_boosting"],
                "metrics_json": json.dumps(row.get("metrics") or {}, sort_keys=True),
            }
        )

    validation_results: dict[str, Any] = {}
    for name in models:
        validation_results[name] = {
            "split_stage": _metrics(y_eval_split, eval_split_predictions[name], SPLIT_LABELS),
            "overall_expert_edit": _metrics(eval_gt, overall_predictions[name], OVERALL_LABELS),
        }

    merge_train = [row for row in train_rows if str(row.get("stage", "")).lower() == "merge"]
    merge_eval = [row for row in eval_rows if str(row.get("stage", "")).lower() == "merge"]
    merge_train_labels = Counter(str(row["label_action"]).upper() for row in merge_train)
    merge_eval_labels = Counter(str(row["label_action"]).upper() for row in merge_eval)
    report = {
        "schema_version": "numeric-action-baseline-v1",
        "task": "real fixed-state expert edit-action prediction",
        "primary_model": primary_name,
        "primary_selection": "highest pooled macro-F1 from train-only leave-one-recording-block-out CV",
        "feature_names": list(FEATURE_NAMES),
        "feature_transforms": {
            "n_spikes": "log1p",
            "n_overclusters": "log1p",
            "isi_violation_rate": "identity",
            "amplitude_cv": "identity",
        },
        "train": {
            "recording_blocks": list(train_blocks),
            "n_rows": len(train_rows),
            "n_split_rows": int(len(y_train)),
            "split_action_counts": dict(Counter(str(value) for value in y_train)),
        },
        "validation": {
            "recording_blocks": list(eval_blocks),
            "n_rows": len(eval_rows),
            "n_split_rows": int(len(y_eval_split)),
            "action_counts": dict(Counter(str(value) for value in eval_gt)),
        },
        "train_group_cv_split_stage": cv_results,
        "validation_results": validation_results,
        "merge_stage_limitation": {
            "learned_classifier": False,
            "policy": "constant MERGE for recorded positive merge candidates",
            "train_rows": len(merge_train),
            "train_action_counts": dict(merge_train_labels),
            "validation_rows": len(merge_eval),
            "validation_action_counts": dict(merge_eval_labels),
            "warning": "No NOT_MERGE or merge-stage DISCARD labels exist; MERGE performance is positive-only and cannot establish generalization.",
        },
        "data_leakage_checks": {
            "recording_block_overlap": False,
            "model_selection_used_validation": False,
            "final_test_used": False,
            "images_used": False,
            "api_calls": 0,
        },
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )
    manifest = {
        "schema_version": "numeric-action-baseline-run-v1",
        "input_jsonl": str(args.input_jsonl),
        "input_sha256": _sha256(args.input_jsonl),
        "output_dir": str(args.output_dir),
        "seed": args.seed,
        "rf_estimators": args.rf_estimators,
        "train_recording_blocks": list(train_blocks),
        "eval_recording_blocks": list(eval_blocks),
        "primary_model": primary_name,
        "primary_model_file": "primary_split_model.joblib",
        "scikit_learn_version": sklearn.__version__,
        "merge_policy": "constant MERGE; not learned because negative merge labels are absent",
    }
    (args.output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    )
    _write_csv(
        args.output_dir / "detail_predictions.csv",
        list(detail_rows[0]),
        detail_rows,
    )
    summary_rows = []
    for name in models:
        for scope, metrics in (
            ("train_group_oof_split", cv_results[name]["pooled"]),
            ("validation_split", validation_results[name]["split_stage"]),
            ("validation_overall", validation_results[name]["overall_expert_edit"]),
        ):
            summary_rows.append(
                {
                    "model": name,
                    "is_primary": name == primary_name,
                    "scope": scope,
                    "n_samples": metrics["n_samples"],
                    "accuracy": metrics["accuracy"],
                    "macro_f1": metrics["macro_f1"],
                    "discard_recall": metrics["per_action"].get("DISCARD", {}).get("recall", ""),
                    "split_recall": metrics["per_action"].get("SPLIT", {}).get("recall", ""),
                }
            )
    _write_csv(args.output_dir / "summary.csv", list(summary_rows[0]), summary_rows)

    joblib.dump(fitted_models[primary_name], args.output_dir / "primary_split_model.joblib")
    primary_estimator = fitted_models[primary_name]
    if hasattr(primary_estimator, "feature_importances_"):
        importance_rows = [
            {"feature": feature, "impurity_importance": float(importance)}
            for feature, importance in sorted(
                zip(FEATURE_NAMES, primary_estimator.feature_importances_),
                key=lambda item: item[1],
                reverse=True,
            )
        ]
        _write_csv(
            args.output_dir / "feature_importance.csv",
            list(importance_rows[0]),
            importance_rows,
        )

    checksum_paths = sorted(
        path for path in args.output_dir.iterdir()
        if path.is_file() and path.name != "checksums.sha256"
    )
    (args.output_dir / "checksums.sha256").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in checksum_paths)
    )

    primary = validation_results[primary_name]["overall_expert_edit"]
    print(json.dumps({
        "primary_model": primary_name,
        "validation_accuracy": primary["accuracy"],
        "validation_macro_f1": primary["macro_f1"],
        "validation_per_action": primary["per_action"],
        "output_dir": str(args.output_dir),
    }, indent=2))


if __name__ == "__main__":
    main()
