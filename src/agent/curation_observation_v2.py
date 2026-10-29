"""Observation v2 for harness v2 (项目总文档 §3.7.3). No labels, no API, no network.

Everything is computed from the current members of the asked cluster(s) only. Spike times are in
seconds. Correlograms count ordered pairs (later − earlier for an ACG; target − source for a CCG)
with |lag| < 50 ms in 1 ms half-open bins; an autocorrelogram excludes only each spike's pairing
with itself, so distinct events at the same time are kept.

Default numbers exclude the template metrics that repeat what the waveform image shows
(n_phases, trough_to_peak_ms, baseline_offset_ratio); numbers="full" adds them as the
"all numbers" control condition (§3.7.5, shortcut checks).

v2.3: the default numbers include the trough half-width, the waveform variability and the
spike-like fraction, because the model did not read width or variability from the image
(skill_v3 source R2). SPIKE_LIKE is calibrated on the expert curation of the four development
channels and is not held-out.
"""
from __future__ import annotations

import hashlib
import io
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection
from scipy.stats import poisson

from src.agent.curation_observation_v1 import _tree, member_sample
from src.pipeline.curation_controller_v2 import correlation, split_available

VERSION = "observation-v2.3"
NUMBERS = ("default", "full")
REFRACTORY_S = .002
WINDOW_S, BIN_S = .05, .001
PRESENCE_BIN_S = 60.
MAX_LINES, MAX_LINES_EACH, MAX_SCATTER, MAX_PROJECTION = 500, 250, 50000, 5000
RP_GRID = np.round(np.arange(.0005, .01 + 1e-9, .00025), 6)
CONTAMINATION_GRID = np.round(np.arange(.005, .35 + 1e-9, .005), 4)
STYLE = {"font.size": 11, "axes.titlesize": 11, "axes.labelsize": 11}
BLUE, ORANGE, GREY, RED = "tab:blue", "tab:orange", "0.45", "red"

TEMPLATE_KEYS = ("n_phases", "trough_to_peak_ms", "baseline_offset_ratio")
MORPHOLOGY_KEYS = ("trough_half_width_ms", "waveform_variability")
# A hierarchy leaf is spike-like when its mean trough is narrow and its spikes are consistent (R2).
SPIKE_LIKE = dict(half_width_ms=.2, variability=.15)
DEFINITIONS = {
    "sampling_rate_hz": "Samples per second of the waveform snippets.",
    "waveform_window_ms": "Duration of each waveform snippet.",
    "refractory_ms": "Refractory interval used by every violation and contamination measure (2 ms).",
    "recording_span_s": "Time from the first to the last spike of the whole recording.",
    "censored_ms": "Detector dead time: the shortest interval between any two spikes of the whole recording. "
                   "Intervals shorter than this cannot be observed; contamination estimates account for it.",
    "n_spikes": "Number of spikes in the cluster.",
    "n_leaves": "Number of initial subclusters (hierarchy leaves) the cluster is built from.",
    "firing_rate_hz": "n_spikes / recording_span_s.",
    "presence_ratio": "Fraction of 60 s bins of the recording that contain at least one spike of the cluster.",
    "isi_violation_rate": "Fraction of consecutive inter-spike intervals shorter than 2 ms (a fraction: 0.01 = 1%).",
    "hill_contamination": "Estimated fraction of spikes that are contaminants (Hill et al. 2011, 2 ms refractory, "
                          "minus the detector dead time). An estimate, not a calibrated probability; 0.5 means the formula "
                          "saturates (at least 50%). Null when fewer than 2 spikes.",
    "sliding_rp_contamination": "Smallest contamination level (0.5% to 35% grid) that the autocorrelogram supports with "
                                "90% confidence for some refractory period from just above the dead time to 10 ms "
                                "(IBL sliding refractory period, simplified). Null when no level up to 35% can be confirmed: "
                                "either heavy contamination or too few spikes to tell (read with hill_contamination "
                                "and n_spikes).",
    "amplitude_median": "Median peak-to-trough amplitude of the cluster's spikes (source units).",
    "amplitude_cv": "Standard deviation / mean of the peak-to-trough amplitudes.",
    "trough_half_width_ms": "Width of the mean waveform's trough at half its depth below zero (ms).",
    "waveform_variability": "Largest across-spike standard deviation over the snippet divided by the mean waveform's "
                            "peak-to-trough amplitude.",
    "spike_like_fraction": "Fraction of the cluster's spikes that lie in hierarchy leaves whose own mean waveform is "
                           f"spike-like: trough_half_width_ms at most {SPIKE_LIKE['half_width_ms']} and "
                           f"waveform_variability below {SPIKE_LIKE['variability']} (calibrated on expert curation of "
                           "development recordings). Near 0 = no spike-like part to split out.",
    "children_dprime": "Separation of the two parts SPLIT would produce: difference of their mean projections on the "
                       "line joining their mean waveforms, divided by the pooled standard deviation. Null when SPLIT "
                       "is not available.",
    "n_phases": "Number of alternating-sign lobes of the mean waveform exceeding 10% of its largest absolute value.",
    "trough_to_peak_ms": "Time from the mean waveform's minimum to the following maximum.",
    "baseline_offset_ratio": "Larger of |mean of the first 3 samples| and |mean of the last 3 samples| of the mean "
                             "waveform, divided by its peak-to-trough amplitude.",
    "n_source": "Number of spikes in the source (the smaller cluster of the pair).",
    "n_target": "Number of spikes in the target.",
    "source_contamination": "hill_contamination of the source.",
    "target_contamination": "hill_contamination of the target.",
    "correlation": "Pearson correlation of the two mean waveforms; used only to choose which pairs to ask about.",
    "amplitude_ratio": "Median amplitude of the source / median amplitude of the target.",
    "ccg_refractory_ratio": "Cross-correlogram counts per bin within ±2 ms divided by counts per bin at 10–50 ms, "
                            "counting only the observable part of the ±2 ms zone (outside the detector dead time). "
                            "Near 0 = clean refractory gap; near 1 = no gap. Null when the 10–50 ms bins are empty.",
    "ccg_zero_lag_ratio": "Cross-correlogram counts per bin within ±1 ms divided by counts per bin at 10–50 ms, "
                          "counting only the observable part outside the detector dead time. "
                          "Well above 1 = a sharp peak at zero lag (near-simultaneous events in both clusters), which "
                          "fills the refractory zone; it is not a burst. Null when the 10–50 ms bins are empty or the "
                          "dead time covers the whole ±1 ms.",
    "merged_contamination": "hill_contamination of the hypothetical merged cluster.",
    "contamination_increase": "merged_contamination minus the larger of source_contamination and target_contamination. "
                              "Null when any of the three estimates is saturated at 0.5: the increase cannot be "
                              "measured then, which is not the same as no increase.",
    "isi_violation_increase": "isi_violation_rate of the hypothetical merged cluster minus the larger of the source's "
                              "and the target's isi_violation_rate. Does not saturate.",
}
COMMON = ("sampling_rate_hz", "waveform_window_ms", "refractory_ms", "censored_ms", "recording_span_s")
FIELDS = {"single": COMMON + ("cluster_id", "n_spikes", "n_leaves", "firing_rate_hz", "presence_ratio",
                              "isi_violation_rate", "hill_contamination", "sliding_rp_contamination",
                              "amplitude_median", "amplitude_cv", *MORPHOLOGY_KEYS, "spike_like_fraction",
                              "children_dprime"),
          "pair": COMMON + ("source_cluster_id", "target_cluster_id", "n_source", "n_target", "source_contamination",
                            "target_contamination", "correlation", "amplitude_ratio", "ccg_refractory_ratio",
                            "ccg_zero_lag_ratio", "merged_contamination", "contamination_increase",
                            "isi_violation_increase", *(f"source_{k}" for k in MORPHOLOGY_KEYS),
                            "source_spike_like_fraction")}
IMAGES = {
    "waveform": "All spikes of the cluster as a grey density, overlaid with up to 500 member waveforms sampled across "
                "every hierarchy leaf (blue). Red: mean waveform; red bar: its trough width at half depth.",
    "isi_acg": "Left: inter-spike-interval histogram, 0–50 ms (red line = 2 ms). Right: autocorrelogram, ±50 ms in 1 ms "
               "bins (red band = ±2 ms refractory zone).",
    "amplitude": "Left: peak-to-trough amplitude histogram of all spikes. Right: amplitude of every spike over recording "
                 "time.",
    "tree": "Hierarchical aggregation tree: leaves are initial subclusters; branches show their merge history.",
    "split_preview": "The two parts SPLIT would produce by undoing the last merge, on shared axes (blue = part keeping this "
                     "ID, orange = part that would break away). Right: their cross-correlogram with a time-reversed "
                     "control (grey line).",
    "overlay": "Source (orange) and target (blue) member waveforms on shared axes; up to 250 of each, sampled across "
               "hierarchy leaves.",
    "ccg_acg": "Left: cross-correlogram (target time − source time), ±50 ms in 1 ms bins (red band = ±2 ms), with a "
               "time-reversed control (grey line). Right: autocorrelogram of the hypothetical merged cluster.",
    "amplitude_time": "Amplitude of every spike over recording time: source (orange) and target (blue).",
}


# ---------------------------------------------------------------------- measures
def forward_lags(times, window):
    """All positive lags t[j] − t[i] < window for i < j in sorted order (one per unordered pair)."""
    t, out = np.sort(np.asarray(times, dtype=float)), []
    for j in range(1, len(t)):
        d = t[j:] - t[:-j]
        d = d[d < window]
        if not len(d):
            break
        out.append(d)
    return np.concatenate(out) if out else np.empty(0)


def edges(window=WINDOW_S, width=BIN_S):
    return np.linspace(-window, window, int(round(2 * window / width)) + 1)


def acg(times, window=WINDOW_S, width=BIN_S):
    d = forward_lags(times, window)
    return np.histogram(np.concatenate([d, -d]), edges(window, width))[0]


def ccg(source, target, window=WINDOW_S, width=BIN_S):
    """Counts of (target time − source time) with |lag| < window."""
    t = np.concatenate([source, target]).astype(float)
    lab = np.r_[np.zeros(len(source), dtype=np.int8), np.ones(len(target), dtype=np.int8)]
    order = np.argsort(t, kind="stable")
    t, lab, out = t[order], lab[order], []
    for j in range(1, len(t)):
        d = t[j:] - t[:-j]
        keep = d < window
        if not keep.any():
            break
        first, second, d = lab[:-j][keep], lab[j:][keep], d[keep]
        out += [d[(first == 0) & (second == 1)], -d[(first == 1) & (second == 0)]]
    lags = np.concatenate(out) if out else np.empty(0)
    return np.histogram(lags, edges(window, width))[0]


def isi_rate(times):
    return None if len(times) < 2 else float(np.mean(np.diff(np.sort(times)) < REFRACTORY_S))


def hill_contamination(times, span, censored=0.):
    """Solve r = 2 (τR − τC) N² F(1 − F) / T for the lower root F; τC = detector dead time."""
    n = len(times)
    if n < 2 or not span > 0 or not 0 <= censored < REFRACTORY_S:
        return None
    r = int(np.count_nonzero(np.diff(np.sort(times)) < REFRACTORY_S))
    c = r * span / (2 * (REFRACTORY_S - censored) * n * n)
    return .5 if c >= .25 else float((1 - math.sqrt(1 - 4 * c)) / 2)


def sliding_rp_contamination(times, span, censored=0., confidence=.9):
    """Refractory periods inside the detector dead time carry no information and are skipped; the
    expected number of violations uses the window length outside the dead time."""
    n = len(times)
    grid = RP_GRID[RP_GRID >= censored + .00025 - 1e-12]
    if n < 2 or not span > 0 or not len(grid):
        return None
    lags = np.sort(forward_lags(times, grid[-1] + 1e-9))
    observed = np.searchsorted(lags, grid, side="right")
    expected = (n / span) * CONTAMINATION_GRID[:, None] * (grid - censored)[None, :] * 2 * n
    ok = ((1 - poisson.cdf(observed[None, :], expected)) > confidence).any(axis=1)
    return float(CONTAMINATION_GRID[np.argmax(ok)]) if ok.any() else None


def presence_ratio(times, start, end):
    bins = max(1, int(math.ceil((end - start) / PRESENCE_BIN_S)))
    index = np.minimum(((np.asarray(times) - start) / PRESENCE_BIN_S).astype(int), bins - 1)
    return float(len(np.unique(index)) / bins)


def template_metrics(mean, fs):
    peak = float(np.max(np.abs(mean)))
    if peak == 0:
        return dict.fromkeys(TEMPLATE_KEYS)
    sign = np.sign(mean) * (np.abs(mean) > .1 * peak)
    sign = sign[sign != 0]
    trough = int(np.argmin(mean))
    after = trough + int(np.argmax(mean[trough:]))
    ptp = float(np.ptp(mean))
    edge = max(abs(float(mean[:3].mean())), abs(float(mean[-3:].mean())))
    return dict(n_phases=int(1 + np.count_nonzero(sign[1:] != sign[:-1])) if len(sign) else 0,
                trough_to_peak_ms=float((after - trough) / fs * 1000),
                baseline_offset_ratio=edge / ptp if ptp > 0 else None)


def half_depth_crossings(mean):
    """(left, right, level): the trough's crossings of half its depth below zero, in samples, linearly
    interpolated (a snippet edge if the trough reaches it). None if the mean has no negative trough."""
    tr = int(np.argmin(mean))
    if mean[tr] >= 0:
        return None
    half, end = float(mean[tr]) / 2, len(mean) - 1
    left = right = tr
    while left > 0 and mean[left - 1] < half:
        left -= 1
    while right < end and mean[right + 1] < half:
        right += 1
    lo = left - 1 + (mean[left - 1] - half) / (mean[left - 1] - mean[left]) if left > 0 else 0.
    hi = right + (half - mean[right]) / (mean[right + 1] - mean[right]) if right < end else float(end)
    return float(lo), float(hi), half


def half_width_ms(mean, fs):
    """Width of the mean waveform's trough at half its depth below zero."""
    c = half_depth_crossings(mean)
    return None if c is None else (c[1] - c[0]) / fs * 1000


def waveform_variability(w):
    """Largest across-spike standard deviation over the snippet, relative to the mean waveform's peak-to-trough."""
    ptp = float(np.ptp(w.mean(axis=0)))
    return float(w.std(axis=0).max() / ptp) if ptp > 0 else None


def morphology(w, fs):
    return dict(trough_half_width_ms=half_width_ms(w.mean(axis=0), fs), waveform_variability=waveform_variability(w))


def spike_like_fraction(m, ids, fs):
    """Fraction of ids in hierarchy leaves whose own mean waveform meets SPIKE_LIKE (leaves never change)."""
    leaves = m.overcluster_assigns[ids]
    order = np.argsort(leaves, kind="stable")
    _, start = np.unique(leaves[order], return_index=True)
    like = 0
    for part in np.split(ids[order], start[1:]):
        w = m.waveforms[part]
        hw, v = half_width_ms(w.mean(axis=0), fs), waveform_variability(w)
        if hw is not None and v is not None and hw <= SPIKE_LIKE["half_width_ms"] and v < SPIKE_LIKE["variability"]:
            like += len(part)
    return like / len(ids)


def refractory_ratio(counts, e, half=REFRACTORY_S, censored=0.):
    """Centre counts per unit of observable lag: lags inside the detector dead time cannot occur, so
    without this correction two unrelated clusters would show a partly empty centre (2026-10-28)."""
    if censored < 0:
        raise ValueError("Negative dead time")
    if censored >= half:
        return None  # the whole centre is inside the dead time
    centre = (e[:-1] >= -half - 1e-12) & (e[1:] <= half + 1e-12)
    shoulder = (np.abs(e[:-1] + BIN_S / 2) >= .01)
    s = counts[shoulder].mean()
    observable = (half - censored) / half
    return float(counts[centre].mean() / observable / s) if s > 0 else None


def split_children(m, cid):
    """Members of the two parts split_last_merge(cid) would produce, without changing the manager."""
    if not split_available(m, cid):
        return None
    tree = m.hierarchy_tree
    last = int(np.flatnonzero(tree[0] == cid)[-1])
    child = int(tree[1, last])
    branch = {child}
    for i in range(last - 1, -1, -1):
        if int(tree[0, i]) in branch:
            branch.add(int(tree[1, i]))
    ids = np.flatnonzero(m.assigns == cid)
    away = np.isin(m.overcluster_assigns[ids], sorted(branch))
    return (int(cid), ids[~away]), (child, ids[away])


def dprime(w, a, b):
    """a, b: global member indices of the two parts."""
    axis = w[a].mean(axis=0) - w[b].mean(axis=0)
    norm = float(np.linalg.norm(axis))
    if norm == 0:
        return None
    pa = w[a[member_sample(a, MAX_PROJECTION)]] @ axis / norm
    pb = w[b[member_sample(b, MAX_PROJECTION)]] @ axis / norm
    pooled = (pa.var() + pb.var()) / 2
    return float(abs(pa.mean() - pb.mean()) / math.sqrt(pooled)) if pooled > 0 else None


def stratified(ids, leaves, limit):
    """Positions into ids: each hierarchy leaf gets at least min(size, base) lines, the rest in proportion;
    within a leaf by SHA256(global index) order, so the choice is independent of row order and RNG.
    With more leaves than lines, one line from each of `limit` leaves chosen in the same hash order
    (previously the leaves after the first `limit` were dropped as a block)."""
    if len(ids) <= limit:
        return np.arange(len(ids))
    unique, inverse = np.unique(leaves, return_inverse=True)
    if len(unique) > limit:
        reps = np.array([pos[member_sample(ids[pos], 1)[0]] for pos in
                         (np.flatnonzero(inverse == k) for k in range(len(unique)))])
        return reps[member_sample(ids[reps], limit)]
    counts = np.bincount(inverse)
    quota = np.minimum(counts, max(1, min(5, limit // len(unique))))
    spare, extra = limit - int(quota.sum()), counts - quota
    if spare > 0 and extra.sum():
        raw = spare * extra / extra.sum()
        add = np.minimum(np.floor(raw).astype(int), extra)
        left = spare - int(add.sum())
        for k in np.argsort(-(raw - add), kind="stable"):  # largest remainders first
            if left <= 0:
                break
            if add[k] < extra[k]:
                add[k] += 1
                left -= 1
        quota += add
    picks = []
    for k in range(len(unique)):
        pos = np.flatnonzero(inverse == k)
        picks.append(pos[member_sample(ids[pos], int(quota[k]))])
    return np.concatenate(picks)[:limit]


# ---------------------------------------------------------------------- images
def _png(fig):
    stream = io.BytesIO()
    fig.tight_layout()
    fig.savefig(stream, format="png", dpi=100, metadata={"Software": VERSION})
    plt.close(fig)
    return stream.getvalue()


def _limits(*arrays):
    low, high = min(float(a.min()) for a in arrays), max(float(a.max()) for a in arrays)
    pad = max((high - low) * .05, 1e-9)
    return low - pad, high + pad


def _lines(ax, x, w, colour, alpha):
    ax.add_collection(LineCollection(np.stack([np.broadcast_to(x, w.shape), w], axis=-1),
                                     colors=colour, alpha=alpha, linewidths=.5))


def _overlay(ax, m, fs, parts, limits):
    """parts: [(label, ids, colour)], drawn in order."""
    x = np.arange(m.waveforms.shape[1]) / fs * 1000
    for label, ids, colour in parts:
        sel = ids[stratified(ids, m.overcluster_assigns[ids], MAX_LINES_EACH)]
        _lines(ax, x, m.waveforms[sel], colour, .15)
        ax.plot([], [], color=colour, label=f"{label}: {len(ids)} spikes, {len(sel)} drawn")
    ax.axhline(0, color="black", linestyle="--", linewidth=.5)
    ax.set(xlim=(x[0], x[-1]), ylim=limits, xlabel="Time (ms)", ylabel="Amplitude (source units)")
    ax.legend(loc="lower right", fontsize=9)


def _correlogram(ax, counts, title, control=None):
    e = edges() * 1000
    ax.bar(e[:-1], counts, width=np.diff(e), align="edge", color=BLUE)
    if control is not None:
        ax.step(e[:-1], control, where="post", color=GREY, linewidth=1.2, label="time-reversed control")
        ax.legend(loc="upper right", fontsize=9)
    ax.axvspan(-2, 2, color=RED, alpha=.15)
    ax.set(xlim=(e[0], e[-1]), xlabel="Lag (ms)", ylabel="Count per 1 ms bin", title=title)


def _amplitude_time(ax, m, parts, span_start):
    for label, ids, colour in parts:
        step = max(1, int(math.ceil(len(ids) / MAX_SCATTER)))
        sel = ids[np.argsort(m.spike_times[ids], kind="stable")][::step]
        ax.plot((m.spike_times[sel] - span_start) / 60, np.ptp(m.waveforms[sel], axis=1), ",", color=colour,
                alpha=.6, rasterized=True)
        ax.plot([], [], "o", color=colour, label=f"{label} ({len(sel)} of {len(ids)} shown)")
    ax.set(xlabel="Recording time (min)", ylabel="Peak-to-trough amplitude")
    ax.legend(loc="upper right", fontsize=9, markerscale=.6)


def single_images(m, cid, fs, span, children):
    ids = np.flatnonzero(m.assigns == cid)
    w, t = m.waveforms[ids], m.spike_times[ids]
    leaves = m.overcluster_assigns[ids]
    images = {}
    with plt.rc_context(STYLE):
        x = np.arange(w.shape[1]) / fs * 1000
        low, high = _limits(w)
        e = np.linspace(low, high, 121)
        density = np.stack([np.histogram(w[:, j], e)[0] for j in range(w.shape[1])], axis=1)
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.imshow(np.log1p(density), origin="lower", aspect="auto", cmap="Greys", extent=(x[0], x[-1], low, high))
        sel = stratified(ids, leaves, MAX_LINES)
        _lines(ax, x, w[sel], BLUE, .12)
        mean = w.mean(axis=0)
        ax.plot(x, mean, color=RED, linewidth=1.5)
        crossings = half_depth_crossings(mean)
        if crossings is not None:
            lo, hi, half = crossings
            ax.plot([lo / fs * 1000, hi / fs * 1000], [half, half], color=RED, linewidth=3)
        ax.axhline(0, color="black", linestyle="--", linewidth=.5)
        width = "none" if crossings is None else f"{(crossings[1] - crossings[0]) / fs * 1000:.2f} ms"
        ax.set(xlim=(x[0], x[-1]), ylim=(low, high), xlabel="Time (ms)", ylabel="Amplitude (source units)",
               title=f"Cluster {cid}: {len(ids)} spikes + {len(sel)} drawn from {len(np.unique(leaves))} leaves; "
                     f"half-width {width}")
        images["waveform"] = _png(fig)

        fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
        gaps = np.diff(np.sort(t)) * 1000
        a1.hist(gaps[gaps <= 50], bins=np.linspace(0, 50, 101), color=BLUE)
        a1.axvline(2, color=RED, linestyle="--")
        rate = isi_rate(t)
        a1.set(xlim=(0, 50), xlabel="ISI (ms)", ylabel="Count",
               title=f"ISI: {'unknown' if rate is None else f'{rate:.2%}'} below 2 ms")
        _correlogram(a2, acg(t), f"Autocorrelogram, cluster {cid}")
        images["isi_acg"] = _png(fig)

        fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
        a1.hist(np.ptp(w, axis=1), bins=60, color=BLUE)
        a1.set(xlabel="Peak-to-trough amplitude", ylabel="Count", title=f"Amplitude, all {len(ids)} spikes")
        _amplitude_time(a2, m, [(f"cluster {cid}", ids, BLUE)], span[0])
        a2.set_title("Amplitude over time")
        images["amplitude"] = _png(fig)
    images["tree"] = _tree(m, cid)
    if children:
        (keep_id, a), (away_id, b) = children
        with plt.rc_context(STYLE):
            fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4), gridspec_kw={"width_ratios": [1.2, 1]})
            parts = [(f"stays {keep_id}", a, BLUE), (f"breaks away {away_id}", b, ORANGE)]
            _overlay(a1, m, fs, sorted(parts, key=lambda p: -len(p[1])), _limits(m.waveforms[a], m.waveforms[b]))
            a1.set_title("If SPLIT: the two parts")
            ta, tb = m.spike_times[a], m.spike_times[b]
            _correlogram(a2, ccg(ta, tb), "CCG between the two parts", ccg(ta, span[0] + span[1] - tb))
            images["split_preview"] = _png(fig)
    return images


def pair_images(m, source, target, fs, span):
    a, b = np.flatnonzero(m.assigns == source), np.flatnonzero(m.assigns == target)
    ta, tb = m.spike_times[a], m.spike_times[b]
    parts = [(f"target {target}", b, BLUE), (f"source {source}", a, ORANGE)]
    images = {}
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(8, 4.5))
        _overlay(ax, m, fs, parts, _limits(m.waveforms[a], m.waveforms[b]))
        ax.set_title(f"Source {source} vs target {target}, shared axes")
        images["overlay"] = _png(fig)
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
        _correlogram(a1, ccg(ta, tb), f"CCG {source} → {target}", ccg(ta, span[0] + span[1] - tb))
        _correlogram(a2, acg(np.concatenate([ta, tb])), f"Autocorrelogram if merged")
        images["ccg_acg"] = _png(fig)
        fig, ax = plt.subplots(figsize=(11, 4))
        _amplitude_time(ax, m, parts, span[0])
        ax.set_title("Amplitude over time")
        images["amplitude_time"] = _png(fig)
    return images


# ---------------------------------------------------------------------- entry point
def _active(m, cid):
    ids = np.flatnonzero(m.assigns == cid)
    if cid <= 0 or not len(ids):
        raise ValueError("Cluster is not active")
    if not np.isfinite(m.waveforms[ids]).all() or not np.isfinite(m.spike_times[ids]).all():
        raise ValueError("Non-finite waveforms or times")
    return ids


def observe(m, kind, cid, target=None, *, sampling_rate, numbers="default"):
    """Return (measurements, [png bytes], info). Images are ordered as info['images']."""
    if kind not in ("single", "pair") or numbers not in NUMBERS or not (np.isfinite(sampling_rate) and sampling_rate > 0):
        raise ValueError("Invalid kind, numbers or sampling rate")
    fs = float(sampling_rate)
    span = (float(m.spike_times.min()), float(m.spike_times.max()))
    duration = span[1] - span[0]
    censored = float(np.min(np.diff(np.sort(m.spike_times)))) if len(m.spike_times) > 1 else 0.
    censored = min(censored, REFRACTORY_S / 2)  # a guard; observed 16 samples (0.53 ms) in CH20/CH31
    ids = _active(m, cid)
    w, t = m.waveforms[ids], m.spike_times[ids]
    out = dict(sampling_rate_hz=fs, waveform_window_ms=float(w.shape[1] / fs * 1000),
               refractory_ms=REFRACTORY_S * 1000, censored_ms=censored * 1000, recording_span_s=duration)
    if kind == "single":
        if target is not None:
            raise ValueError("A single judgement has no target")
        amps = np.ptp(w, axis=1)
        children = split_children(m, cid)
        out.update(cluster_id=int(cid), n_spikes=len(ids), n_leaves=len(np.unique(m.overcluster_assigns[ids])),
                   firing_rate_hz=len(ids) / duration if duration > 0 else None,
                   presence_ratio=presence_ratio(t, *span), isi_violation_rate=isi_rate(t),
                   hill_contamination=hill_contamination(t, duration, censored),
                   sliding_rp_contamination=sliding_rp_contamination(t, duration, censored),
                   amplitude_median=float(np.median(amps)),
                   amplitude_cv=float(amps.std() / amps.mean()) if amps.mean() > 0 else None,
                   **morphology(w, fs), spike_like_fraction=spike_like_fraction(m, ids, fs),
                   children_dprime=dprime(m.waveforms, children[0][1], children[1][1]) if children else None)
        if numbers == "full":
            out.update(template_metrics(w.mean(axis=0), fs))
        images = single_images(m, cid, fs, span, children)
    else:
        if target is None or target == cid:
            raise ValueError("A pair judgement needs a different target")
        ids2 = _active(m, target)
        t2 = m.spike_times[ids2]
        cs, ct = hill_contamination(t, duration, censored), hill_contamination(t2, duration, censored)
        both = np.concatenate([t, t2])
        merged = hill_contamination(both, duration, censored)
        estimates = (cs, ct, merged)
        rates = (isi_rate(t), isi_rate(t2), isi_rate(both))
        counts = ccg(t, t2)
        med_s, med_t = float(np.median(np.ptp(w, axis=1))), float(np.median(np.ptp(m.waveforms[ids2], axis=1)))
        out.update(source_cluster_id=int(cid), target_cluster_id=int(target), n_source=len(ids), n_target=len(ids2),
                   source_contamination=cs, target_contamination=ct,
                   correlation=correlation(w.mean(axis=0), m.waveforms[ids2].mean(axis=0)),
                   amplitude_ratio=med_s / med_t if med_t > 0 else None,
                   ccg_refractory_ratio=refractory_ratio(counts, edges(), censored=censored),
                   ccg_zero_lag_ratio=refractory_ratio(counts, edges(), half=BIN_S, censored=censored),
                   merged_contamination=merged,
                   contamination_increase=None if None in estimates or max(estimates) >= .5 else merged - max(cs, ct),
                   isi_violation_increase=None if None in rates else rates[2] - max(rates[:2]),
                   **{f"source_{k}": v for k, v in morphology(w, fs).items()},
                   source_spike_like_fraction=spike_like_fraction(m, ids, fs))
        if numbers == "full":
            out.update({f"source_{k}": v for k, v in template_metrics(w.mean(axis=0), fs).items()})
        images = pair_images(m, cid, target, fs, span)
    pngs = list(images.values())
    info = dict(version=VERSION, numbers=numbers, images=[
        dict(name=name, description=IMAGES[name], sha256=hashlib.sha256(png).hexdigest())
        for name, png in images.items()])
    return out, pngs, info


def definitions(measurements):
    of_source = set(TEMPLATE_KEYS) | set(MORPHOLOGY_KEYS) | {"spike_like_fraction"}
    return {k: f"Of the source: {DEFINITIONS[k.removeprefix('source_')]}" if k.removeprefix("source_") in of_source
            and k.startswith("source_") else DEFINITIONS[k] for k in measurements if not k.endswith("cluster_id")}
