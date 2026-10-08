"""Versioned observations, independent of the legacy renderer and API imports.

ISI = count(diff(sort(times_seconds)) < 0.002) / (N-1); N<2 is unknown.
Waveform units are intentionally not assumed to be microvolts.
"""
from __future__ import annotations

import base64
import hashlib
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection

VERSION = "observation-engineering-v1"
VIEWS = ("raw_member_v1", "summary_member_v1")
REFRACTORY_S = 0.002


def isi_rate(times):
    times = np.asarray(times, dtype=float)
    if times.ndim != 1 or not np.isfinite(times).all():
        raise ValueError("Expected finite spike times in seconds")
    return None if len(times) < 2 else float(np.mean(np.diff(np.sort(times)) < REFRACTORY_S))


def member_sample(ids, limit=500):
    """Select and order by SHA256(global spike index); independent of row order/RNG."""
    ids = np.asarray(ids, dtype=np.int64)
    if limit < 1 or len(np.unique(ids)) != len(ids):
        raise ValueError("Invalid limit or duplicate member IDs")
    return np.array(sorted(range(len(ids)), key=lambda i: (
        hashlib.sha256(f"member-v1:{int(ids[i])}".encode()).digest(), int(ids[i])))[:limit])


def _cluster(manager, cid):
    ids = np.flatnonzero(manager.assigns == cid)
    if cid <= 0 or not len(ids):
        raise ValueError("Cluster is not active")
    w, t = manager.waveforms[ids], manager.spike_times[ids]
    if w.ndim != 2 or w.shape[1] < 2 or not np.isfinite(w).all():
        raise ValueError("Invalid waveforms")
    isi_rate(t)
    return ids, w, t


def metrics(manager, phase, cid, target=None, sampling_rate=30000.):
    if not np.isfinite(sampling_rate) or sampling_rate <= 0:
        raise ValueError("Invalid sampling rate")
    ids, w, t = _cluster(manager, cid)
    common = dict(sampling_rate_hz=float(sampling_rate),
                  waveform_window_ms=float(w.shape[1] / sampling_rate * 1000), refractory_ms=2.)
    if phase == "phase1":
        amps = np.ptp(w, axis=1)
        return dict(common, cluster_id=int(cid), n_spikes=len(ids),
                    n_overclusters=len(np.unique(manager.overcluster_assigns[ids])),
                    isi_violation_rate=isi_rate(t),
                    amplitude_cv=float(np.std(amps) / np.mean(amps)) if np.mean(amps) > 0 else None)
    if phase != "phase2" or target == cid:
        raise ValueError("Invalid phase or self-merge")
    ids2, w2, t2 = _cluster(manager, target)
    a, b = np.mean(w, axis=0), np.mean(w2, axis=0)
    corr = float(np.clip(np.corrcoef(a, b)[0, 1], -1, 1)) if np.std(a) > 0 and np.std(b) > 0 else None
    return dict(common, small_cluster_id=int(cid), large_cluster_id=int(target),
                n_small=len(ids), n_large=len(ids2), small_isi_rate=isi_rate(t),
                large_isi_rate=isi_rate(t2), merged_isi_rate=isi_rate(np.concatenate([t, t2])),
                correlation=corr)


def _png(fig):
    stream = io.BytesIO()
    fig.tight_layout()
    fig.savefig(stream, format="png", dpi=110, metadata={"Software": VERSION})
    plt.close(fig)
    return stream.getvalue()


def _waveform(ids, w, cid, fs, view, limits=None):
    selected = member_sample(ids)
    x = np.arange(w.shape[1]) / fs * 1000
    fig, ax = plt.subplots(figsize=(8, 4))
    lines = np.stack([np.broadcast_to(x, w[selected].shape), w[selected]], axis=-1)
    ax.add_collection(LineCollection(lines, colors="steelblue", alpha=.16, linewidths=.5))
    if view == "summary_member_v1":
        lo, mid, hi = np.quantile(w, [.1, .5, .9], axis=0)
        ax.fill_between(x, lo, hi, alpha=.2, color="orange", label="10–90% (all spikes)")
        ax.plot(x, mid, color="darkorange", linewidth=1.5, label="median (all spikes)")
        ax.legend(fontsize=8)
    low, high = limits if limits is not None else (float(w.min()), float(w.max()))
    pad = max((high - low) * .05, 1e-9)
    ax.set(xlim=(x[0], x[-1]), ylim=(low-pad, high+pad), xlabel="Time (ms)",
           ylabel="Amplitude (source units)", title=f"Cluster {cid}: n={len(ids)}, raw displayed={len(selected)}")
    ax.axhline(0, color="black", linestyle="--", linewidth=.5)
    return _png(fig), {"member_ids": ids[selected].tolist(), "ylim": [low-pad, high+pad]}


def _isi(times, title):
    fig, ax = plt.subplots(figsize=(8, 4))
    gaps = np.diff(np.sort(times)) * 1000
    ax.hist(gaps[gaps <= 50], bins=np.linspace(0, 50, 101), color="steelblue")
    ax.axvline(2, color="red", linestyle="--")
    rate = isi_rate(times)
    shown = "unknown (N<2)" if rate is None else f"{rate:.3%}"
    ax.set(xlabel="ISI (ms); display 0–50 ms", ylabel="Count",
           title=f"{title}: ISI<2 ms={shown}; denominator=max(N-1,0)={len(gaps)}")
    return _png(fig)


def _tree(manager, cid):
    """Reconstruct chronological binary topology with the legacy height formula.

    Only surviving overcluster membership is shown. If the reconstructed root
    disagrees with current membership, show unknown instead of inventing a tree.
    """
    members = set(map(int, np.unique(manager.overcluster_assigns[manager.assigns == cid])))
    nodes, roots = [], {}
    def root(label):
        if label not in roots:
            roots[label] = len(nodes)
            nodes.append((None, None, {label}, label, 0.))
        return roots[label]
    for row in manager.hierarchy_tree.T:
        a, b = int(row[0]), int(row[1])
        if a <= 0 or b <= 0 or a == b:
            continue
        left, right = root(a), root(b)
        roots[a] = len(nodes)
        nodes.append((left, right, nodes[left][2] | nodes[right][2], None, 1.-float(row[2])))
        roots.pop(b, None)
    top = root(cid)
    fig, ax = plt.subplots(figsize=(8, 4))
    if nodes[top][2] != members:
        ax.text(.5, .5, "Tree unavailable: topology / current membership mismatch", ha="center", wrap=True)
        ax.axis("off")
    else:
        positions, labels = {}, []
        stack = [(top, False)]
        while stack:
            node, visited = stack.pop()
            l, r, _, label, increment = nodes[node]
            if l is None:
                positions[node] = (len(labels), 0)
                ax.plot(len(labels), 0, 'o', color="steelblue", markersize=3)
                labels.append(label)
            elif not visited:
                stack.extend([(node, True), (r, False), (l, False)])
            else:
                lx, ly = positions[l]; rx, ry = positions[r]
                y = max(ly, ry) + increment
                ax.plot([lx, lx, rx, rx], [ly, y, y, ry], color="steelblue", linewidth=.8)
                positions[node] = ((lx+rx)/2, y)
        stride = max(1, int(np.ceil(len(labels) / 40)))
        ax.set_xticks(list(range(len(labels)))[::stride], labels[::stride], rotation=90, fontsize=6)
        ax.set(xlabel=f"Overcluster ID ({len(labels)} leaves; tick stride={stride})",
               ylabel="Height: max(child heights) + (1 - similarity)")
        if len(labels) == 1:
            ax.text(.5, .7, "Single overcluster: no hierarchical merge", transform=ax.transAxes, ha="center")
    ax.set_title(f"Cluster {cid}: chronological merge topology")
    return _png(fig)


def observe(manager, phase, cid, target=None, *, sampling_rate=30000., view="raw_member_v1"):
    if view not in VIEWS:
        raise ValueError("Unversioned view")
    measured = metrics(manager, phase, cid, target, sampling_rate)
    ids, w, t = _cluster(manager, cid)
    info = {"version": VERSION, "view": view, "sampling": "SHA256(global member index), max 500"}
    if phase == "phase1":
        wave, details = _waveform(ids, w, cid, sampling_rate, view)
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.hist(np.ptp(w, axis=1), bins=50, color="steelblue")
        ax.set(xlabel="Peak-to-trough amplitude (source units)", ylabel="Count", title=f"Cluster {cid}: all spikes")
        images = [wave, _isi(t, f"Cluster {cid}"), _png(fig), _tree(manager, cid)]
        names = ["waveform", "isi", "amplitude", "tree"]
        info["waveform"] = details
    else:
        ids2, w2, t2 = _cluster(manager, target)
        # Shared axes only in candidate view: two panes retained to match contract.
        limits = (float(min(w.min(), w2.min())), float(max(w.max(), w2.max()))) if view == "summary_member_v1" else None
        wave, details = _waveform(ids, w, cid, sampling_rate, view, limits)
        wave2, details2 = _waveform(ids2, w2, target, sampling_rate, view, limits)
        images = [wave, wave2, _isi(np.concatenate([t, t2]), f"Merged {cid}+{target}")]
        names = ["small_waveform", "large_waveform", "merged_isi"]
        info.update(small_waveform=details, large_waveform=details2)
    info["images"] = [{"name": name, "sha256": hashlib.sha256(png).hexdigest()} for name, png in zip(names, images)]
    urls = ["data:image/png;base64," + base64.b64encode(png).decode() for png in images]
    return measured, urls, images, info
