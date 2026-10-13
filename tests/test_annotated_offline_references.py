"""Synthetic-only regression coverage for the four-dataset offline campaign."""
import json
import socket

import numpy as np
import pytest

from scripts.analysis import run_annotated_offline_references as campaign
from src.pipeline import curation_engineering_v1 as core

ref = campaign.ref


def fixture_data():
    return dict(hierarchy_assigns=np.array([1, 1, 1, 1, 3, 3]),
                overcluster_assigns=np.array([1, 1, 2, 2, 3, 3]),
                hierarchy_tree=np.array([[1.], [2.], [.9], [0.]]),
                spiketimes=np.arange(6) * .01,
                waveforms=np.random.default_rng(1).normal(size=(6, 12)),
                curation_assigns=np.array([7, 7, 0, 0, 9, 9]), Fs=30000.)


@pytest.fixture
def frozen_source(tmp_path, monkeypatch):
    for obj, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"),
                      (ref.stage6, "make_client"), (ref.stage6.replay, "client_factory"),
                      (core, "observe"), (core, "build_request")):
        monkeypatch.setattr(obj, name, ref.forbidden)
    rows = {}
    for dataset in campaign.DATASETS:
        path = tmp_path / f"{dataset}.mat"
        path.write_bytes(dataset.encode())
        rows[dataset] = dict(dataset_id=dataset, mat_path=str(path), mat_sha256=ref.sha256(path))
    reads = []

    def load(path):
        assert path in {r["mat_path"] for r in rows.values()}
        reads.append(path)
        return fixture_data()

    monkeypatch.setattr(ref.stage6, "load_matlab_spikes", load)
    monkeypatch.setattr(ref.stage6, "selected_rows", lambda ids: [rows[i] for i in ids])
    source = tmp_path / "original_ch31"
    ref.run(source)  # Tiny original-format fixture, never a real dataset.
    return source, rows, reads


def test_scope_exact_four():
    campaign.validate_scope(campaign.DATASETS)
    for ids in (campaign.DATASETS[:-1], campaign.DATASETS[::-1], ["CH1"],
                (*campaign.DATASETS, "reserved_CH5")):
        with pytest.raises(ValueError, match="exactly"):
            campaign.validate_scope(ids)


def test_full_campaign_reuses_source_and_keeps_every_seed(frozen_source, tmp_path):
    source, rows, _ = frozen_source
    before = campaign.inventory(source)
    out = tmp_path / "four"
    campaign.run(out, source)
    summary = campaign.read(out / "summary.json")
    assert summary["status"] == "completed"
    assert summary["api_calls"] == summary["api_cost_usd"] == 0
    assert summary["new_trajectories"] == 42 and summary["total_trajectories"] == 56
    assert summary["all_terminal_scores_reverified"]
    assert set(summary["datasets"]) == set(campaign.DATASETS)
    assert not (out / "datasets" / ref.DATASET).exists()
    assert before == campaign.inventory(source)
    for dataset, bundle in summary["datasets"].items():
        folder = source if dataset == ref.DATASET else out / "datasets" / dataset
        assert campaign.read(folder / "manifest.json")["dataset"] == rows[dataset]
        assert [t["seed"] for t in bundle["trials"] if t["policy"] == "random"] == list(range(10))
        assert [t["trial"] for t in bundle["trials"]] == [campaign.trial_name(n, s) for n, s in campaign.trial_specs()]
        for trial in bundle["trials"]:
            assert trial["labels_visible"] == (trial["policy"] == "oracle_purity_v1")
            if trial["policy"] != "no_curation":
                assert campaign.read(folder / trial["trial"] / "manifest.json")["dataset_id"] == dataset
        verified, diag = campaign.verify_saved(folder, rows[dataset])
        assert verified == bundle and diag["final_reference_noise_retained"] == 0
        assert not diag["exact_label_array_equal"]  # Arbitrary cluster IDs, same partition.
    assert not list(out.rglob("*.png"))
    assert "API 费用 $0" in (out / "REPORT.md").read_text()
    with pytest.raises(FileExistsError):
        campaign.run(out, source)
    assert before == campaign.inventory(source)


def test_reuse_rejects_tampered_terminal(frozen_source, tmp_path):
    source, rows, _ = frozen_source
    path = source / "always_keep" / "terminal.npz"
    path.write_bytes(b"tampered synthetic fixture")
    with pytest.raises(ValueError, match="terminal hash"):
        campaign.run(tmp_path / "must_not_start", source)
    assert not (tmp_path / "must_not_start").exists()


@pytest.mark.parametrize("field,value", [("random_seeds", [0]), ("environment", {}),
                                          ("code_hashes", {"missing.py": "bad"})])
def test_reuse_rejects_changed_protocol(frozen_source, tmp_path, field, value):
    source, rows, _ = frozen_source
    path = source / "manifest.json"
    manifest = campaign.read(path)
    manifest[field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        campaign.run(tmp_path / "must_not_start", source)
    assert not (tmp_path / "must_not_start").exists()


def test_overlap_rejected_before_opening_any_data(tmp_path, monkeypatch):
    monkeypatch.setattr(ref.stage6, "selected_rows", ref.forbidden)
    source = tmp_path / "source"
    for out in (source, source / "nested", tmp_path):
        with pytest.raises(ValueError, match="overlap"):
            campaign.run(out, source)


def test_unchanged_controller_and_policy_used():
    assert ref.OfflineController.run is core.CurationController.run
    assert ref.OfflineController.apply is core.CurationController.apply
    assert campaign.SEEDS == tuple(range(10))
