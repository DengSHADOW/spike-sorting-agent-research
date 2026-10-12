"""Shared-spike-ID curation scoring; no model calls or actor inputs."""
from pathlib import Path
import hashlib
import json

import numpy as np
from scipy.optimize import linear_sum_assignment

RULES = Path(__file__).resolve().parents[2] / 'configs/curation/one_to_one_scoring_v1.json'


def _rate(a, b):
    return float(a / b) if b else 0.


def _prf(tp, pred, ref):
    p, r = _rate(tp, pred), _rate(tp, ref)
    return dict(precision=p, recall=r, f1=_rate(2 * p * r, p + r))


def match_iou(iou, threshold=.5):
    """Threshold first; ineligible assignments cannot steal a valid match."""
    if not iou.size:
        return []
    eligible = iou >= threshold
    rows, cols = linear_sum_assignment(np.where(eligible, iou, 0.), maximize=True)
    return [(int(r), int(c)) for r, c in zip(rows, cols) if eligible[r, c]]


def evaluate(prediction, reference):
    pred, ref = np.asarray(prediction), np.asarray(reference)
    if pred.ndim != 1 or ref.ndim != 1 or pred.shape != ref.shape:
        raise ValueError('Assignments must have the same one-dimensional spike row identity')
    for a in (pred, ref):
        if not np.issubdtype(a.dtype, np.number) or not np.isfinite(a).all() or np.any(a < 0) or np.any(a != np.floor(a)):
            raise ValueError('Expected nonnegative integer labels with noise=0')
    rule_bytes = RULES.read_bytes()
    rules = json.loads(rule_bytes)
    threshold = rules['minimum_iou_inclusive']
    if threshold != .5 or rules['noise_label'] != 0:
        raise ValueError('Scoring protocol changed; version and revalidate before use')
    refs, ref_n = np.unique(ref[ref > 0], return_counts=True)
    preds, pred_n = np.unique(pred[pred > 0], return_counts=True)
    overlap = np.zeros((len(refs), len(preds)), dtype=np.int64)
    both = (ref > 0) & (pred > 0)
    if both.any():
        np.add.at(overlap, (np.searchsorted(refs, ref[both]), np.searchsorted(preds, pred[both])), 1)
    union = ref_n[:, None] + pred_n[None, :] - overlap
    iou = np.divide(overlap, union, out=np.zeros(overlap.shape, dtype=float), where=union > 0)
    pairs = match_iou(iou, threshold)
    by_ref = dict(pairs)
    tp = sum(int(overlap[r, c]) for r, c in pairs)
    total_pred, total_ref = int(pred_n.sum()), int(ref_n.sum())
    units = []
    for r, label in enumerate(refs):
        c = by_ref.get(r)
        unit = dict(reference_unit=int(label), reference_spikes=int(ref_n[r]), matched=c is not None,
                    predicted_unit=int(preds[c]) if c is not None else None,
                    best_candidate_iou=float(iou[r].max()) if len(preds) else 0.,
                    accuracy=float(iou[r, c]) if c is not None else 0.,
                    **(_prf(int(overlap[r, c]), int(pred_n[c]), int(ref_n[r])) if c is not None else _prf(0, 0, int(ref_n[r]))))
        units.append(unit)
    return dict(version=rules['version'], scoring_sha256=hashlib.sha256(rule_bytes).hexdigest(),
        n_spikes=len(ref), n_expert_units=len(refs), n_predicted_units=len(preds), n_matched_units=len(pairs),
        n_unmatched_predicted_units=len(preds)-len(pairs),
        n_pure_reference_noise_clusters=int(np.count_nonzero(overlap.sum(axis=0) == 0)),
        retained_reference_noise_spikes=int(np.count_nonzero((ref == 0) & (pred > 0))),
        discarded_expert_spikes=int(np.count_nonzero((ref > 0) & (pred == 0))),
        full_output=dict(tp=tp, fp=total_pred-tp, fn=total_ref-tp,
                         predicted_spikes=total_pred, reference_spikes=total_ref, **_prf(tp, total_pred, total_ref)),
        expert_units=units,
        expert_unit_macro={k: _rate(sum(u[k] for u in units), len(units))
                           for k in ('accuracy', 'precision', 'recall', 'f1')})
