import numpy as np
import pytest
from scripts.analysis.curation_one_to_one import evaluate, match_iou


def test_permutation_invariance():
    s = evaluate([9, 9, 8, 8, 0], [1, 1, 2, 2, 0])
    assert s['full_output']['f1'] == 1 and s['n_matched_units'] == 2


def test_unmatched_noise_stays_in_denominator():
    s = evaluate([10,10,20,20,30,30], [1,1,2,2,0,0])
    assert s['full_output']['precision'] == pytest.approx(4/6)
    assert s['full_output']['recall'] == 1
    assert s['n_pure_reference_noise_clusters'] == 1
    assert s['retained_reference_noise_spikes'] == 2


@pytest.mark.parametrize('pred,ref', [([7,7,7,7], [1,1,2,2]), ([7,7,8,8], [1,1,1,1])])
def test_merge_and_fragmentation_penalized(pred, ref):
    s = evaluate(pred, ref)
    assert s['n_matched_units'] == 1
    assert s['full_output']['precision'] == .5
    assert s['full_output']['recall'] == .5


def test_contamination_below_threshold_not_pure_noise():
    s = evaluate([9]*5, [1,1,0,0,0])
    assert s['n_matched_units'] == 0
    assert s['n_pure_reference_noise_clusters'] == 0
    assert s['expert_units'][0]['best_candidate_iou'] == .4
    assert s['expert_units'][0]['accuracy'] == 0


def test_threshold_before_assignment():
    assert match_iou(np.array([[.51,.49],[.99,.51]])) == [(0,0),(1,1)]


def test_all_discarded_and_empty():
    s = evaluate([0,0], [1,1])
    assert s['discarded_expert_spikes'] == 2
    assert s['full_output']['fn'] == 2 and s['full_output']['recall'] == 0
    assert evaluate([], [])['full_output']['f1'] == 0


@pytest.mark.parametrize('pred,ref', [([1], [1,1]), ([-1], [1]), ([1.5], [1]), ([float('nan')], [1])])
def test_bad_labels_rejected(pred, ref):
    with pytest.raises(ValueError): evaluate(pred, ref)
