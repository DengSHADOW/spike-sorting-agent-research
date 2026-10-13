"""Synthetic-only tests; no real samples, rendering, client, secrets or network."""
import socket

import numpy as np
import pytest

from scripts.analysis import run_development_offline_references as campaign
from src.pipeline import curation_engineering_v1 as core

existing = campaign.existing
ref = existing.ref


def fixture_data():
    return dict(hierarchy_assigns=np.array([1, 1, 1, 1, 3, 3]),
                overcluster_assigns=np.array([1, 1, 2, 2, 3, 3]),
                hierarchy_tree=np.array([[1.], [2.], [.9], [0.]]),
                spiketimes=np.arange(6) * .01, waveforms=np.zeros((6, 12)),
                curation_assigns=np.array([7, 7, 0, 0, 9, 9]), Fs=30000.)


@pytest.fixture(autouse=True)
def forbid_external(monkeypatch):
    for obj, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"),
                      (ref.stage6, "make_client"), (ref.stage6.replay, "client_factory"),
                      (core, "observe"), (core, "build_request")):
        monkeypatch.setattr(obj, name, ref.forbidden)


@pytest.fixture
def samples(tmp_path, monkeypatch):
    rows = []
    for dataset in campaign.DEFAULT_DATASETS:
        path = tmp_path / f"{dataset}.mat"
        path.write_bytes(dataset.encode())
        rows.append(dict(dataset_id=dataset, mat_path=str(path), mat_sha256=existing.sha(path)))
    monkeypatch.setattr(ref.stage6, "selected_rows", lambda ids: rows if tuple(ids) == campaign.DEFAULT_DATASETS else pytest.fail("Unexpected scope"))
    monkeypatch.setattr(ref.stage6, "load_matlab_spikes", lambda path: fixture_data())
    index = tmp_path / "index.json"
    index.write_text("{}")
    monkeypatch.setattr(ref.stage6, "INDEX", index)
    return rows


def test_two_channels_same_frozen_policies(samples, tmp_path):
    before = campaign.frozen_files()
    out = tmp_path / "new"
    campaign.run(out)
    summary = existing.read(out / "summary.json")
    manifest = existing.read(out / "manifest.json")
    assert summary["controller_rollouts"] == 26 and summary["initial_state_scores"] == 2
    assert summary["total_evaluations"] == 28 and summary["all_terminal_scores_reverified"]
    assert summary["api_calls"] == summary["api_cost_usd"] == 0
    assert manifest["code_hashes"] == before == campaign.frozen_files()
    assert list(summary["datasets"]) == list(campaign.DEFAULT_DATASETS)
    assert manifest["policies"] == ref.POLICIES and manifest["random_seeds"] == list(range(10))
    for row in samples:
        dataset = row["dataset_id"]
        bundle, diag = existing.verify_saved(out / "datasets" / dataset, row)
        assert bundle == summary["datasets"][dataset]
        assert diag["final_reference_noise_retained"] == 0
        oracle = next(t for t in bundle["trials"] if t["policy"] == "oracle_purity_v1")
        assert oracle["scores"]["one_to_one"]["full_output"]["f1"] == 1
        assert [t["seed"] for t in bundle["trials"] if t["policy"] == "random"] == list(range(10))
    assert not list(out.rglob("*.png"))
    before_outputs = existing.inventory(out)
    with pytest.raises(FileExistsError):
        campaign.run(out)
    assert existing.inventory(out) == before_outputs


@pytest.mark.parametrize("datasets", [[], [campaign.DEFAULT_DATASETS[0]] * 2,
    ["cM2-e007_012-017_CH5"], ["cM2-e004_001-003_CH1"], ["CH17"]])
def test_invalid_scope_stops_before_data_or_output(tmp_path, monkeypatch, datasets):
    monkeypatch.setattr(ref.stage6, "load_matlab_spikes", ref.forbidden)
    monkeypatch.setattr(ref.stage6.replay, "read_json", ref.forbidden)
    with pytest.raises(ValueError):
        campaign.run(tmp_path / "must_not_exist", datasets)
    assert not (tmp_path / "must_not_exist").exists()


def test_failure_stops_before_next_dataset(samples, tmp_path, monkeypatch):
    visited = []

    def fail(row, out):
        visited.append(row["dataset_id"])
        raise ValueError("Synthetic failure")

    monkeypatch.setattr(existing, "run_dataset", fail)
    with pytest.raises(ValueError, match="Synthetic failure"):
        campaign.run(tmp_path / "stopped")
    state = existing.read(tmp_path / "stopped" / "execution_status.json")
    assert visited == [campaign.DEFAULT_DATASETS[0]]
    assert state["status"] == "stopped" and state["completed_datasets"] == []
    assert not (tmp_path / "stopped" / "summary.json").exists()
