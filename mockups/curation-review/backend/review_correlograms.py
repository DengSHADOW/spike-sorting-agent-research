"""Exact signed, all-pair correlograms; only aggregate counts leave this module."""
from collections import OrderedDict
import hashlib
import numpy as np

EDGES = np.linspace(-0.05, 0.05, 101)

def correlogram(first, second=None, *, self_pairs=False):
    if second is None:
        self_pairs = True
    a = np.asarray(first, dtype=np.float64)
    b = a if second is None else np.asarray(second, dtype=np.float64)
    if a.ndim != 1 or b.ndim != 1 or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError('Expected finite spike times in seconds')
    if self_pairs and (a.shape != b.shape or not np.array_equal(a, b)):
        raise ValueError('ACG requires identical populations')
    a, b = np.sort(a), np.sort(b)
    swapped = len(a) > len(b)
    small, large = (b, a) if swapped else (a, b)
    # A broad search window avoids dropping pairs at floating-point boundaries.
    # The actual B-A differences, not rounded spike times, determine the bins.
    lo = np.searchsorted(large, small - .050000001, side='left')
    hi = np.searchsorted(large, small + .050000001, side='right')
    counts = np.zeros(100, dtype=np.int64)
    cursor = 0
    while cursor < len(small):
        lengths = hi[cursor:cursor + 2048] - lo[cursor:cursor + 2048]
        cumulative = np.cumsum(lengths)
        take = max(1, min(len(lengths), int(np.searchsorted(cumulative, 500000, side='right'))))
        lengths = lengths[:take]
        total = int(lengths.sum())
        if total:
            starts = np.cumsum(lengths) - lengths
            indices = np.repeat(lo[cursor:cursor + take], lengths) + np.arange(total) - np.repeat(starts, lengths)
            refs = np.repeat(np.arange(cursor, cursor + take), lengths)
            delta = small[refs] - large[indices] if swapped else large[indices] - small[refs]
            valid = (delta >= EDGES[0]) & (delta < EDGES[-1])
            if self_pairs:
                valid &= indices != refs  # Distinct coincident spikes must remain.
            bins = np.searchsorted(EDGES, delta[valid], side='right') - 1
            counts += np.bincount(bins, minlength=100)
        cursor += take
    return dict(counts=counts.tolist(), edges_ms=(EDGES * 1000).tolist(),
        bin_ms=1, min_ms=-50, max_ms=50, n_source=len(a), n_target=len(b),
        self_pairs_excluded=bool(self_pairs), normalization='raw ordered pair counts',
        definition='B minus A; all pairs in [-50, 50) ms; half-open 1 ms bins; no time rounding or sampling')

class CorrelogramCache:
    def __init__(self):
        self.values = OrderedDict()

    def get(self, first, second=None):
        a = np.sort(np.asarray(first, dtype=np.float64))
        b = a if second is None else np.sort(np.asarray(second, dtype=np.float64))
        key = (hashlib.sha256(a.tobytes()).digest(), hashlib.sha256(b.tobytes()).digest(), second is None)
        if key not in self.values:
            self.values[key] = correlogram(a, b, self_pairs=second is None)
            while len(self.values) > 8192:
                self.values.popitem(last=False)
        self.values.move_to_end(key)
        return self.values[key]


def snapshot(dataset, state_sha, mat_sha, member_times, pairs, cache):
    return dict(schema='review-correlograms-snapshot-v1', dataset_id=dataset,
        state_sha256=state_sha, mat_sha256=mat_sha,
        acg={str(cid): dict(cache.get(t), source=cid, target=cid) for cid, t in member_times.items()},
        ccg={f'{a},{b}': dict(cache.get(member_times[a], member_times[b]), source=a, target=b) for a, b in sorted(pairs)},
        new_model_calls=0, raw_spike_times_published=False)
