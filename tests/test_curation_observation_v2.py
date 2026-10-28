import copy
import socket
from pathlib import Path
from time import monotonic

import numpy as np
import pytest

from src.agent import curation_observation_v2 as obs
from src.cluster.manager import ClusterManager

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network permitted")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def brute(a, b=None, window=.05, width=.001):
    e = obs.edges(window, width)
    if b is None:
        lags = [y - x for i, x in enumerate(a) for j, y in enumerate(a) if i != j and abs(y - x) < window]
    else:
        lags = [y - x for x in a for y in b if abs(y - x) < window]
    return np.histogram(lags, e)[0]


def unit(rng, n, span, refractory=.003, contamination=0.):
    """Renewal train with a hard dead time plus independent Poisson contaminants."""
    clean = int(round(n * (1 - contamination)))
    gaps = refractory + rng.exponential(span / clean - refractory, clean)
    t = np.cumsum(gaps)
    t = t[t < span]
    noise = rng.uniform(0, span, n - clean)
    return np.sort(np.concatenate([t, noise]))


def test_correlograms_match_brute_force_including_simultaneous_events():
    rng = np.random.default_rng(1)
    a = np.round(rng.uniform(0, 1, 80), 3)  # rounding forces ties
    b = np.round(rng.uniform(0, 1, 60), 3)
    assert np.array_equal(obs.acg(a), brute(a))
    assert np.array_equal(obs.ccg(a, b), brute(a, b))
    assert obs.acg(a).sum() % 2 == 0  # ordered pairs; half-open bins are not mirror-symmetric at exact edges


def test_contamination_estimates_track_known_contamination():
    rng = np.random.default_rng(2)
    span = 2000.
    clean, dirty = unit(rng, 20000, span), unit(rng, 20000, span, contamination=.2)
    assert obs.hill_contamination(clean, span) == 0.
    assert abs(obs.hill_contamination(dirty, span) - .2) < .05
    assert obs.sliding_rp_contamination(clean, span) <= .01
    assert obs.sliding_rp_contamination(dirty, span) is None or obs.sliding_rp_contamination(dirty, span) >= .15
    assert obs.sliding_rp_contamination(clean[:30], span) is None  # too few spikes to confirm anything
    assert obs.hill_contamination(np.array([1.]), span) is None
    # A detector dead time hides the shortest intervals; without accounting for it the sliding
    # estimate wrongly reports a clean unit.
    dead = .00053
    seen = dirty[np.r_[True, np.diff(dirty) >= dead]]
    assert (obs.sliding_rp_contamination(seen, span, dead) or 1) >= .15
    assert abs(obs.hill_contamination(seen, span, dead) - .2) < .05
    assert obs.presence_ratio(np.array([1., 2., 130.]), 0., 180.) == pytest.approx(2 / 3)


def test_template_metrics_count_lobes_and_width():
    fs = 30000.
    x = np.arange(45)
    wave = .3 * np.exp(-((x - 8) / 2.) ** 2) - np.exp(-((x - 14) / 2.) ** 2) + .5 * np.exp(-((x - 24) / 3.) ** 2)
    t = obs.template_metrics(wave, fs)
    assert t["n_phases"] == 3 and t["trough_to_peak_ms"] == pytest.approx(10 / fs * 1000, abs=.05)
    assert t["baseline_offset_ratio"] < .01


def manager(seed=0):
    """Cluster 1 = leaves 1, 2, 3 merged as (1←2) then (1←3); cluster 4 = leaf 4. Leaf 3 has another shape."""
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 1, 30)
    shapes = {1: -np.sin(np.pi * x), 2: -np.sin(np.pi * x), 3: -.5 * np.sin(2 * np.pi * x), 4: np.cos(np.pi * x)}
    sizes = {1: 900, 2: 400, 3: 40, 4: 300}
    over = np.concatenate([[k] * n for k, n in sizes.items()])
    labels = np.where(over == 4, 4, 1)
    waves = np.vstack([shapes[k] + rng.normal(scale=.05, size=(n, 30)) for k, n in sizes.items()])
    times = np.sort(rng.uniform(0, 600, len(over)))
    tree = np.array([[1, 1], [2, 3], [.9, .8], [0, 0]], dtype=float)
    return ClusterManager(labels, over, tree, times, waves)


def test_split_preview_is_exactly_what_split_would_produce():
    m = manager()
    (keep, a), (away, b) = obs.split_children(m, 1)
    real = copy.deepcopy(m)
    real.split_last_merge(1)
    assert np.array_equal(a, np.flatnonzero(real.assigns == keep))
    assert np.array_equal(b, np.flatnonzero(real.assigns == away)) and away == 3
    assert obs.split_children(m, 4) is None


def test_sampling_covers_every_leaf():
    m = manager()
    ids = np.flatnonzero(m.assigns == 1)
    sel = obs.stratified(ids, m.overcluster_assigns[ids], 100)
    assert len(sel) == 100 and len(set(sel)) == 100
    assert set(m.overcluster_assigns[ids[sel]]) == {1, 2, 3}


@pytest.mark.parametrize("numbers", obs.NUMBERS)
def test_observations_have_fixed_fields_images_and_no_unknown_numbers(numbers):
    m = manager()
    single, pngs, info = obs.observe(m, "single", 1, sampling_rate=30000., numbers=numbers)
    extra = set(obs.TEMPLATE_KEYS) if numbers == "full" else set()
    assert set(single) == set(obs.FIELDS["single"]) | extra
    assert [i["name"] for i in info["images"]] == ["waveform", "isi_acg", "amplitude", "tree", "split_preview"]
    assert single["children_dprime"] > 2 and all(p.startswith(b"\x89PNG") for p in pngs)
    leaf, pngs, info = obs.observe(m, "single", 4, sampling_rate=30000., numbers=numbers)
    assert len(pngs) == 4 and leaf["children_dprime"] is None
    pair, pngs, info = obs.observe(m, "pair", 4, 1, sampling_rate=30000., numbers=numbers)
    assert [i["name"] for i in info["images"]] == ["overlay", "ccg_acg", "amplitude_time"]
    assert set(pair) == set(obs.FIELDS["pair"]) | {f"source_{k}" for k in extra}
    for values in (single, pair):
        assert all(v is None or (type(v) in (int, float) and np.isfinite(v)) for v in values.values())
        assert set(obs.definitions(values)) == {k for k in values if not k.endswith("cluster_id")}
    with pytest.raises(ValueError):
        obs.observe(m, "pair", 1, 1, sampling_rate=30000.)


@pytest.mark.skipif(not (ROOT / "data").exists(), reason="development MAT files not present")
def test_real_ch31_largest_cluster_renders_in_seconds():
    import sys
    sys.path.insert(0, str(ROOT))
    from scripts.analysis import run_annotated_offline_references as existing
    row = existing.ref.stage6.selected_rows(["cM2-e004_004-006_CH31"])[0]
    m = existing.ref.fresh(existing.load_data(row))
    cids = sorted(m.get_active_clusters(), key=lambda c: -np.count_nonzero(m.assigns == c))
    started = monotonic()
    values, pngs, _ = obs.observe(m, "single", cids[0], sampling_rate=30000.)
    obs.observe(m, "pair", cids[1], cids[0], sampling_rate=30000.)
    assert monotonic() - started < 60 and values["n_spikes"] > 10000
