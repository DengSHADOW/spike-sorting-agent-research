import numpy as np
import spikeinterface as si

from src.eval.metrics import (
    compute_overall_performance,
    match_clusters_to_ground_truth,
)


def test_empty_curated_sorting_counts_all_ground_truth_as_false_negative() -> None:
    sampling_frequency = 30_000.0
    curated_assigns = np.zeros(5, dtype=int)
    gt_assigns = np.array([1, 1, 2, 2, 2], dtype=int)
    curated = si.NumpySorting.from_unit_dict({}, sampling_frequency=sampling_frequency)
    ground_truth = si.NumpySorting.from_unit_dict(
        {1: np.array([0, 1]), 2: np.array([2, 3, 4])},
        sampling_frequency=sampling_frequency,
    )

    comparison, total_gt = match_clusters_to_ground_truth(
        curated, ground_truth, curated_assigns, gt_assigns, sampling_frequency
    )
    performance = compute_overall_performance(comparison, total_gt)

    assert comparison.empty
    assert {'tp', 'fp', 'fn', 'precision', 'recall', 'f1_score'} <= set(comparison.columns)
    assert performance['n_curated_clusters'] == 0
    assert performance['total_tp'] == 0
    assert performance['total_fp'] == 0
    assert performance['total_fn'] == 5
    assert performance['overall_precision'] == 0.0
    assert performance['overall_recall'] == 0.0
    assert performance['overall_f1_score'] == 0.0
