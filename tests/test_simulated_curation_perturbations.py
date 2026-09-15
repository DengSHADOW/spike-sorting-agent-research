"""Regression checks for controlled simulated MERGE/DISCARD coverage data."""

from __future__ import annotations

import numpy as np

from src.actions.oracle import choose_oracle_action
from src.actions.trajectory import apply_oracle_action
from src.cluster.manager import ClusterManager
from src.simulate.overcluster import _apply_curation_perturbations
from src.simulate.setting import SettingConfig


def test_controlled_perturbations_create_executable_discard_then_merge() -> None:
    n_spikes = 1200
    cfg = SettingConfig(
        setting_id="coverage_test",
        seed=7,
        forced_merge_pairs=1,
        noise_clusters=1,
        noise_spikes_per_cluster=600,
    )
    spike_times = np.linspace(0.0, 10.0, n_spikes, endpoint=False)
    waveforms = np.ones((n_spikes, 8), dtype=np.float32)
    overclusters = np.ones(n_spikes, dtype=np.int64)
    hierarchy = np.ones(n_spikes, dtype=np.int64)
    gt_assigns = np.ones(n_spikes, dtype=np.int64)

    result = _apply_curation_perturbations(
        cfg, 0, spike_times, waveforms, overclusters, hierarchy, gt_assigns
    )
    spike_times, waveforms, overclusters, hierarchy, gt_assigns, meta = result

    assert meta["forced_merge_pairs_applied"] == 1
    assert meta["noise_clusters_applied"] == 1
    assert int((gt_assigns == 0).sum()) == 600

    manager = ClusterManager(
        initial_assigns=hierarchy,
        overcluster_assigns=overclusters,
        hierarchy_tree=np.empty((4, 0)),
        spike_times=spike_times,
        waveforms=waveforms,
    )
    discard = choose_oracle_action(manager.assigns, gt_assigns, manager.get_active_clusters())
    assert discard.action_type == "DISCARD"
    before_discard = manager.assigns.copy()
    apply_oracle_action(manager, discard)
    assert not np.array_equal(before_discard, manager.assigns)

    merge = choose_oracle_action(manager.assigns, gt_assigns, manager.get_active_clusters())
    assert merge.action_type == "MERGE"
    before_merge = manager.assigns.copy()
    apply_oracle_action(manager, merge)
    assert not np.array_equal(before_merge, manager.assigns)
