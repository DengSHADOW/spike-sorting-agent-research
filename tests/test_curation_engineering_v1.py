import json
import socket

import numpy as np
import pytest

from src.agent.curation_contract import build_request
from src.agent.curation_observation_v1 import isi_rate, member_sample, metrics, observe
from src.cluster.manager import ClusterManager
from src.pipeline import curation_engineering_v1 as core


DATASET = "cM2-e004_004-006_CH3"


def manager(large=False):
    n = 4000 if large else 4
    labels = np.array([1] * n + [3] * 3)
    over = np.array([1] * (n//2) + [2] * (n-n//2) + [3] * 3)
    return ClusterManager(labels, over, np.array([[1.], [2.], [.9], [0.]]),
                          np.arange(len(labels)) * .01,
                          np.random.default_rng(3).normal(size=(len(labels), 12)))


def response(action="KEEP"):
    return {"status": "completed", "model": "gpt-6-astra", "reasoning": {"effort": "high"},
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(
                            {"action": action, "rationale": "SCRIPTED engineering test, not a model prediction"})}]}],
            "usage": {"input_tokens": 100, "output_tokens": 10,
                      "input_tokens_details": {"cached_tokens": 0}}}


class Scripted:
    kind = "SCRIPTED_OFFLINE_TEST"
    def __init__(self, actions=("KEEP",)):
        self.actions = iter(actions)
        self.calls = 0
    def count(self, request):
        return 100
    def respond(self, request):
        self.calls += 1
        return response(next(self.actions))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network or client permitted")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("scripts.run.run_skill_replay.client_factory", forbidden)


@pytest.fixture
def fast_observation(monkeypatch):
    def fake(m, phase, cid, target=None, **kw):
        obs = metrics(m, phase, cid, target)
        names = ["waveform", "isi", "amplitude", "tree"] if phase == "phase1" else ["small", "large", "merged"]
        return obs, ["data:image/png;base64,YQ=="]*len(names), [b"fixture"]*len(names), {"images": [{"name": n} for n in names]}
    monkeypatch.setattr(core, "observe", fake)


def controller(tmp_path, actions=("KEEP",), **kw):
    return core.CurationController(kw.pop("manager", manager()), DATASET, tmp_path / "run",
        provider=kw.pop("provider", Scripted(actions)), budget_usd=kw.pop("budget_usd", 10),
        max_calls=kw.pop("max_calls", 20), **kw)


def test_single_and_merge_isi_same_definition():
    assert isi_rate([0., .01, .02]) == 0
    assert isi_rate([0., 0., .01, .02]) == pytest.approx(1/3)
    assert isi_rate([]) is None and isi_rate([1]) is None
    m = manager()
    d = metrics(m, "phase2", 3, 1)
    assert d["small_isi_rate"] == d["large_isi_rate"] == d["merged_isi_rate"] == 0
    with pytest.raises(ValueError):
        isi_rate([float("nan")])


def test_member_sampling_order_and_rng_independent():
    ids = np.arange(900)
    selected = ids[member_sample(ids)]
    np.random.seed(918)
    permuted = np.random.permutation(ids)
    assert np.array_equal(selected, permuted[member_sample(permuted)])
    assert len(selected) == 500


def test_images_deterministic_shared_axes_and_skill_isolation():
    m = manager()
    a = observe(m, "phase2", 3, 1, view="summary_member_v1")
    b = observe(m, "phase2", 3, 1, view="summary_member_v1")
    assert a[2] == b[2]
    assert a[3]["small_waveform"]["ylim"] == a[3]["large_waveform"]["ylim"]
    obs, urls, images, meta = observe(m, "phase1", 1)
    assert len(images) == 4
    x = build_request("phase1", obs, urls, skill_version="v0")
    y = build_request("phase1", obs, urls, skill_version="v1")
    assert x["instructions"] == y["instructions"]
    assert x["input"][0]["content"][1:] == y["input"][0]["content"][1:]


@pytest.mark.parametrize("action", ["SPLIT", "MERGE", "DISCARD"])
def test_full_state_restore(tmp_path, action):
    c = controller(tmp_path)
    before = c.state_hash()
    c.apply(action, 1, 3)
    assert c.state_hash() != before
    assert not c.manager.history
    c.restore(c.out / "initial.npz")
    assert c.state_hash() == before
    assert c.manager.modified_clusters == set()
    assert c.manager._operation_counter == 0


def test_impossible_split_preserves_and_stops(tmp_path):
    c = controller(tmp_path)
    before = c.state_hash()
    with pytest.raises(core.ControllerStop):
        c.apply("SPLIT", 3)
    assert c.state_hash() == before and c.status == "stopped"


@pytest.mark.parametrize("mode", ["budget", "calls", "repeat", "provider", "truncated", "parse", "refusal"])
def test_stop_never_discards(tmp_path, fast_observation, mode):
    p = Scripted(("KEEP", "KEEP"))
    c = controller(tmp_path, provider=p, budget_usd=0 if mode == "budget" else 10, max_calls=1 if mode == "calls" else 20)
    before = c.state_hash()
    if mode in {"calls", "repeat"}:
        c.ask("phase1", 1)
    if mode == "provider":
        def broken(request):
            p.calls += 1
            raise RuntimeError("transport failure")
        p.respond = broken
    if mode in {"truncated", "parse", "refusal"}:
        raw = response()
        if mode == "truncated":
            raw["status"] = "incomplete"
            raw["incomplete_details"] = {"reason": "max_output_tokens"}
        elif mode == "parse":
            raw["output"][0]["content"][0]["text"] = "not json"
        else:
            raw["output"][0]["content"][0] = {"type": "refusal", "refusal": "no"}
        p.respond = lambda request: raw
    with pytest.raises(core.ControllerStop):
        c.ask("phase1", 3 if mode == "calls" else 1)
    assert c.state_hash() == before and c.status == "stopped"
    assert p.calls <= 1
    with pytest.raises(core.ControllerStop):
        c.ask("phase1", 1)


@pytest.mark.parametrize("last", ["NOT_MERGE", "MERGE", "DISCARD"])
def test_phase2_full_scripted_scheduler(tmp_path, fast_observation, last):
    c = controller(tmp_path, manager=manager(large=True), actions=("KEEP", "KEEP", "KEEP", last))
    before = c.manager.assigns.copy()
    c.run()
    assert c.calls == 4 and c.status == "completed"
    if last == "NOT_MERGE":
        assert np.array_equal(c.manager.assigns, before)
    else:
        assert np.all(c.manager.assigns[before == 3] == (1 if last == "MERGE" else 0))


def test_no_target_no_count_filter(tmp_path, fast_observation):
    c = controller(tmp_path, actions=("KEEP", "KEEP"))
    before = c.manager.assigns.copy()
    assert np.array_equal(c.run(), before)
    assert c.calls == 2


def test_split_recursion(tmp_path, fast_observation):
    c = controller(tmp_path, actions=("SPLIT", "KEEP", "KEEP", "KEEP"))
    c.run()
    assert c.manager.get_active_clusters() == [1, 2, 3] and c.calls == 4


def test_checkpoint_tamper_rejected(tmp_path):
    c = controller(tmp_path)
    with (c.out / "initial.npz").open("ab") as f:
        f.write(b"corrupt")
    with pytest.raises(core.ControllerStop):
        c.restore(c.out / "initial.npz")


def test_scope_gate_before_output(tmp_path):
    with pytest.raises(ValueError):
        core.CurationController(manager(), "cM2-e007_012-017_CH1", tmp_path / "forbidden",
                               provider=Scripted(), budget_usd=1, max_calls=1)
    assert not (tmp_path / "forbidden").exists()


@pytest.mark.parametrize("validation", ["SPLIT", "DISCARD"])
def test_phase2_prevalidation_semantics(tmp_path, fast_observation, validation):
    c = controller(tmp_path, manager=manager(large=True), actions=("KEEP", "KEEP", validation))
    c.run()
    assert c.calls == 3
    assert np.all(c.manager.assigns[-3:] == 3)
    assert np.all(c.manager.assigns[:-3] == (1 if validation == "SPLIT" else 0))
    with pytest.raises(core.ControllerStop):
        c.ask("phase1", 3)


def test_empty_tree_and_single_spike(tmp_path):
    m = ClusterManager(np.array([1]), np.array([1]), np.empty((4, 0)),
                       np.array([0.]), np.zeros((1, 12)))
    c = controller(tmp_path, manager=m)
    obs, urls, images, info = observe(m, "phase1", 1)
    assert obs["isi_violation_rate"] is None and obs["amplitude_cv"] is None
    assert len(images) == 4 and c.state_hash()


def test_configuration_mutation_stops(tmp_path, fast_observation, monkeypatch):
    c = controller(tmp_path)
    monkeypatch.setattr(core, "config_hashes", lambda: {"changed": "yes"})
    with pytest.raises(core.ControllerStop):
        c.ask("phase1", 1)
    assert c.provider.calls == 0
