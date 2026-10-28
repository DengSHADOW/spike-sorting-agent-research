import json
import socket

import numpy as np
import pytest

from src.cluster.manager import ClusterManager
from src.pipeline import curation_controller_v2 as v2
from src.pipeline.curation_engineering_v1 import ControllerStop


DATASET = "cM2-e004_004-006_CH3"
SHAPES = {"A": np.sin(np.linspace(0, np.pi, 12)), "B": np.linspace(-1, 1, 12), "C": np.cos(np.linspace(0, 3, 12))}


def manager(spec, tree):
    """spec: overcluster -> (initial cluster, n spikes, waveform shape). tree: list of (into, merged) rows."""
    labels, over, waves = [], [], []
    rng = np.random.default_rng(0)
    for oc, (cluster, n, shape) in spec.items():
        labels += [cluster] * n
        over += [oc] * n
        waves.append(SHAPES[shape] + rng.normal(scale=.01, size=(n, 12)))
    t = np.array([[a for a, _ in tree], [b for _, b in tree], [1.] * len(tree), [0.] * len(tree)]).reshape(4, -1)
    return ClusterManager(np.array(labels), np.array(over), t, np.arange(len(labels)) * .01, np.vstack(waves))


class Scripted:
    kind = "SCRIPTED_OFFLINE_TEST"

    def __init__(self, rule):
        self.rule, self.questions = rule, []

    def decide(self, m, kind, cid, target, allowed, *, folder, sampling_rate):
        self.questions.append((kind, cid, target, tuple(allowed)))
        return dict(action=self.rule(kind, cid, target, allowed), rationale="SCRIPTED test, not a model")


def keep_all(kind, cid, target, allowed):
    return "KEEP" if kind == "single" else "NOT_MERGE"


def run(tmp_path, m, rule, **kw):
    d = Scripted(rule)
    c = v2.CurationControllerV2(m, DATASET, tmp_path / "run", decider=d, sampling_rate=30000., **kw)
    return c, d, c.run()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network permitted")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def test_split_is_offered_only_when_the_last_merge_can_be_undone(tmp_path):
    m = manager({1: (1, 6, "A"), 2: (1, 4, "B"), 3: (3, 5, "C")}, [(1, 2)])
    c, d, final = run(tmp_path, m, keep_all)
    offered = {cid: allowed for kind, cid, _, allowed in d.questions if kind == "single"}
    assert offered == {1: ("KEEP", "DISCARD", "SPLIT"), 3: ("KEEP", "DISCARD")}
    assert np.array_equal(final, m.assigns) and c.status == "completed"


def test_single_and_pair_judgements_interleave_largest_first_without_phases(tmp_path):
    m = manager({1: (1, 9, "A"), 2: (2, 7, "A"), 3: (3, 5, "A")}, [])
    c, d, _ = run(tmp_path, m, keep_all)
    asked = [(q[0], q[1], q[2]) for q in d.questions]
    assert asked[:4] == [("single", 1, None), ("single", 2, None), ("pair", 2, 1), ("single", 3, None)]
    assert set(asked[4:]) == {("pair", 3, 1), ("pair", 3, 2)}  # order follows correlation
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text())
    assert manifest["phases"] is None and manifest["small_cluster_threshold"] is None


def test_split_children_are_judged_next_and_never_offered_to_merge_back(tmp_path):
    m = manager({1: (1, 6, "A"), 2: (1, 6, "A"), 3: (3, 4, "B")}, [(1, 2)])
    rule = lambda kind, cid, target, allowed: "SPLIT" if "SPLIT" in allowed else keep_all(kind, cid, target, allowed)
    c, d, final = run(tmp_path, m, rule)
    assert [(q[0], q[1]) for q in d.questions][:3] == [("single", 1), ("single", 1), ("single", 2)]
    assert ("pair", 2, 1) not in [(q[0], q[1], q[2]) for q in d.questions]
    assert sorted(np.unique(final)) == [1, 2, 3] and c.status == "completed"


def test_merge_is_re_judged_and_a_split_there_undoes_it_once(tmp_path):
    m = manager({1: (1, 9, "A"), 2: (2, 4, "A")}, [])
    initial = m.assigns.copy()

    def rule(kind, cid, target, allowed):
        if kind == "pair":
            return "MERGE"
        return "SPLIT" if "SPLIT" in allowed else "KEEP"
    c, d, final = run(tmp_path, m, rule)
    assert [(q[0], q[1], q[2]) for q in d.questions] == [
        ("single", 1, None), ("single", 2, None), ("pair", 2, 1), ("single", 1, None)]
    assert np.array_equal(final, initial) and c.status == "completed"
    events = [json.loads(x)["event"] for x in (tmp_path / "run" / "events.jsonl").read_text().splitlines()]
    assert "merge_vetoed" in events


def test_kept_merge_continues_and_pair_discard_removes_the_smaller_source(tmp_path):
    m = manager({1: (1, 9, "A"), 2: (2, 6, "A"), 3: (3, 3, "B")}, [])

    def rule(kind, cid, target, allowed):
        if kind == "single":
            return "KEEP"
        return "MERGE" if {cid, target} == {1, 2} else "DISCARD"
    c, d, final = run(tmp_path, m, rule)
    assert sorted(np.unique(final)) == [0, 1]
    assert np.count_nonzero(final == 1) == 15 and np.count_nonzero(final == 0) == 3
    assert ("pair", 3, 1) in [(q[0], q[1], q[2]) for q in d.questions]


def test_candidates_are_the_k_best_correlated_kept_clusters(tmp_path):
    m = manager({1: (1, 9, "B"), 2: (2, 8, "C"), 3: (3, 7, "A"), 4: (4, 5, "A")}, [])
    c, d, _ = run(tmp_path, m, keep_all, k=1)
    pairs = [(q[1], q[2]) for q in d.questions if q[0] == "pair"]
    assert (4, 3) in pairs and len([p for p in pairs if 4 in p]) == 1


def test_decision_limit_leaves_the_channel_unscored(tmp_path):
    m = manager({1: (1, 9, "A"), 2: (2, 7, "B"), 3: (3, 5, "C")}, [])
    c, d, _ = run(tmp_path, m, keep_all, max_decisions=2)
    summary = json.loads((tmp_path / "run" / "summary.json").read_text())
    assert c.status == "stopped_limit" and summary["terminal_evaluation_allowed"] is False
    assert (tmp_path / "run" / "stopped.npz").exists() and len(d.questions) == 2


def test_an_action_outside_the_offered_set_stops(tmp_path):
    m = manager({1: (1, 9, "A")}, [])
    with pytest.raises(ControllerStop):
        run(tmp_path, m, lambda *a: "SPLIT")
