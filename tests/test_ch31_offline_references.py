"""All fixtures synthetic. No API, no real expert files, no model performance."""
import json
import socket

import numpy as np
import pytest

from scripts.analysis import run_ch31_offline_references as ref
from scripts.analysis.curation_one_to_one import evaluate
from src.cluster.manager import ClusterManager
from src.pipeline import curation_engineering_v1 as core


@pytest.fixture(autouse=True)
def deny_external(monkeypatch):
    for target in (socket.socket,):
        monkeypatch.setattr(target, "connect", ref.forbidden)
        monkeypatch.setattr(target, "connect_ex", ref.forbidden)
    monkeypatch.setattr(ref.stage6, "make_client", ref.forbidden)
    monkeypatch.setattr(ref.stage6.replay, "client_factory", ref.forbidden)
    monkeypatch.setattr(core, "observe", ref.forbidden)
    monkeypatch.setattr(core, "build_request", ref.forbidden)


def data():
    return dict(hierarchy_assigns=np.array([1, 1, 1, 1, 3, 3]),
                overcluster_assigns=np.array([1, 1, 2, 2, 3, 3]),
                hierarchy_tree=np.array([[1.], [2.], [.9], [0.]]),
                spiketimes=np.arange(6) * .01,
                waveforms=np.random.default_rng(1).normal(size=(6, 12)),
                curation_assigns=np.array([7, 7, 0, 0, 9, 9]), Fs=30000.)


def execute(tmp_path, name, *, seed=None, inputs=None):
    d = data() if inputs is None else inputs
    p = ref.Policy(name, seed=seed, reference=d["curation_assigns"] if name == "oracle_purity_v1" else None)
    c = ref.OfflineController(ref.fresh(d), ref.DATASET, tmp_path, policy=p)
    final = c.run()
    return c, final


def test_scheduler_and_executor_are_inherited():
    assert ref.OfflineController.run is core.CurationController.run
    assert ref.OfflineController.apply is core.CurationController.apply
    assert ref.OfflineController.checkpoint is core.CurationController.checkpoint


def test_always_keep_equals_no_curation_no_images_or_client(tmp_path):
    c, final = execute(tmp_path / "keep", "always_keep")
    assert np.array_equal(final, data()["hierarchy_assigns"])
    assert c.operations == 0 and c.charged == 0 and c.status == "completed"
    assert not list(c.out.glob("*.png"))


def test_split_to_legal_leaves_preserves_spikes(tmp_path):
    c, final = execute(tmp_path / "split", "always_split")
    assert final.tolist() == [1, 1, 2, 2, 3, 3]
    assert c.operations == 1
    assert not ref.split_available(c.manager, 1)
    assert not ref.split_available(c.manager, 2)


def test_oracle_reaches_labels_only_by_existing_actions(tmp_path):
    c, final = execute(tmp_path / "oracle", "oracle_purity_v1")
    assert final.tolist() == [1, 1, 0, 0, 3, 3]
    assert not np.array_equal(final, data()["curation_assigns"])  # IDs differ legitimately
    assert evaluate(final, data()["curation_assigns"])["full_output"]["f1"] == 1
    events = [json.loads(x) for x in (c.out / "events.jsonl").read_text().splitlines()]
    assert [e["action"] for e in events if e["event"] == "executed"] == ["SPLIT", "DISCARD"]
    assert c.charged == 0 and c.operations == 2


@pytest.mark.parametrize("name", ["always_keep", "always_split", "random"])
def test_trivial_policy_cannot_receive_labels(name):
    with pytest.raises(ValueError, match="Only the explicit oracle"):
        ref.Policy(name, seed=0, reference=data()["curation_assigns"])


def test_random_reproducible_legal_and_label_blind(tmp_path):
    a, x = execute(tmp_path / "a", "random", seed=4)
    b, y = execute(tmp_path / "b", "random", seed=4)
    assert np.array_equal(x, y) and a.calls == b.calls
    assert a.policy.reference is None
    events = [json.loads(x) for x in (a.out / "events.jsonl").read_text().splitlines()]
    for event in events:
        if event["event"] == "decision":
            assert event["action"] in event["allowed_actions"]
            assert not event["labels_visible"]
            assert "reference_composition" not in event


def test_oracle_respects_small_small_merge_restriction(tmp_path):
    d = data()
    d.update(hierarchy_assigns=np.array([1, 1, 3, 3]),
             overcluster_assigns=np.array([1, 1, 3, 3]),
             hierarchy_tree=np.empty((4, 0)), spiketimes=np.arange(4)*.01,
             waveforms=np.zeros((4, 12)), curation_assigns=np.array([7, 7, 7, 7]))
    c, final = execute(tmp_path / "restricted", "oracle_purity_v1", inputs=d)
    assert c.operations == 0  # Oracle must not bypass missing Phase 2 candidates
    assert len(np.unique(final)) == 2
    assert evaluate(final, d["curation_assigns"])["full_output"]["f1"] == .5


def test_oracle_merges_when_controller_offers_legal_pair(tmp_path):
    d = data()
    labels = np.array([1]*4000 + [3]*4)
    d.update(hierarchy_assigns=labels, overcluster_assigns=labels.copy(),
             hierarchy_tree=np.empty((4, 0)), spiketimes=np.arange(len(labels))*.01,
             waveforms=np.zeros((len(labels), 12)), curation_assigns=np.full(len(labels), 7))
    c, final = execute(tmp_path / "merge", "oracle_purity_v1", inputs=d)
    assert np.unique(final).tolist() == [1] and c.operations == 1


def test_split_collision_or_degenerate_branch_rejected_without_mutation():
    m = ref.fresh(data())
    before = m.assigns.copy()
    m.hierarchy_tree[1, 0] = 3  # already active
    assert "SPLIT" not in ref.legal_actions(m, "phase1", 1)
    m.hierarchy_tree[1, 0] = 99  # empty breakaway
    assert "SPLIT" not in ref.legal_actions(m, "phase1", 1)
    assert np.array_equal(before, m.assigns)


def test_bad_target_and_reference_rejected():
    with pytest.raises(ValueError):
        ref.legal_actions(ref.fresh(data()), "phase2", 1, 1)
    with pytest.raises(ValueError):
        ref.Policy("oracle_purity_v1")
    with pytest.raises(ValueError):
        ref.Policy("random", seed=-1)


def test_local_limit_stops_not_scores_terminal(tmp_path):
    c = ref.OfflineController(ref.fresh(data()), ref.DATASET, tmp_path / "limited",
                              policy=ref.Policy("always_keep"), max_calls=1)
    with pytest.raises(core.ControllerStop):
        c.run()
    summary = json.loads((c.out / "summary.json").read_text())
    assert summary["status"] == "stopped" and not summary["terminal_evaluation_allowed"]
    assert not (c.out / "terminal.npz").exists()


def test_runtime_guard_blocks_clients():
    with ref.offline_only(), pytest.raises(RuntimeError, match="forbidden"):
        ref.stage6.make_client()


def test_campaign_records_all_seeds_and_no_overwrite(tmp_path, monkeypatch):
    fake_mat = tmp_path / "fixture.mat"
    fake_mat.write_bytes(b"fixture only")
    row = dict(dataset_id=ref.DATASET, mat_path=str(fake_mat), mat_sha256=ref.sha256(fake_mat))
    monkeypatch.setattr(ref.stage6, "selected_rows", lambda ds: [row] if ds == [ref.DATASET] else pytest.fail("Wrong scope"))
    monkeypatch.setattr(ref.stage6, "load_matlab_spikes", lambda p: data())
    out = tmp_path / "campaign"
    rows = ref.run(out, (0, 1))
    m = json.loads((out / "manifest.json").read_text())
    s = json.loads((out / "summary.json").read_text())
    assert len(rows) == 6 and m["random_seeds"] == [0, 1]
    assert s["api_calls"] == s["api_cost_usd"] == 0
    assert s["original_input_and_code_unchanged"]
    assert s["scorer_identity_check"]["one_to_one"]["full_output"]["f1"] == 1
    assert [r["seed"] for r in rows if r["policy"] == "random"] == [0, 1]
    with pytest.raises(FileExistsError):
        ref.run(out, (0, 1))
