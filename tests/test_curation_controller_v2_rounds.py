import hashlib
import json
import socket
import time

import numpy as np
import pytest

from src.cluster.manager import ClusterManager
from src.pipeline import curation_controller_v2 as v2
from src.pipeline.curation_engineering_v1 import ControllerStop

DATASET = "cM2-e004_004-006_CH31"
X = np.linspace(0, 1, 12)
SHAPES = {"A": np.sin(np.pi * X), "A2": np.sin(np.pi * X) + .05 * X, "B": np.linspace(-1, 1, 12),
          "C": np.cos(3 * X)}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network permitted")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def manager(spec, tree=()):
    """spec: overcluster -> (initial cluster, n spikes, shape). tree: (into, merged) rows."""
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


class Threaded(Scripted):
    """prepare/answer split like the model decider; answers finish in a scrambled order."""

    def prepare(self, m, kind, cid, target, allowed, *, folder, sampling_rate):
        self.questions.append((kind, cid, target, tuple(allowed)))
        return (kind, cid, target)

    def answer(self, prepared, allowed, *, folder):
        time.sleep(hashlib.sha256(repr(prepared).encode()).digest()[0] / 25500)
        return dict(action=self.rule(*prepared, allowed), rationale="SCRIPTED test, not a model")


def keep(kind, cid, target, allowed):
    return "KEEP" if kind == "single" else "NOT_MERGE"


def run(tmp_path, m, rule, name="run", decider=Scripted, **kw):
    d = decider(rule)
    c = v2.RoundsController(m, DATASET, tmp_path / name, decider=d, sampling_rate=30000., **kw)
    return c, d, c.run()


def events(c, kind=None):
    rows = [json.loads(line) for line in (c.out / "events.jsonl").read_text().splitlines()]
    return [r for r in rows if kind is None or r["event"] == kind]


def test_first_round_is_all_singles_then_pairs_among_kept_largest_first(tmp_path):
    m = manager({1: (1, 9, "A"), 2: (2, 7, "A2"), 3: (3, 5, "B")})
    c, d, final = run(tmp_path, m, keep, final_sweep=False)
    rounds = events(c, "round")
    assert [(r["singles"], r["pairs"]) for r in rounds] == [(3, 0), (0, 3)]
    assert [q[:3] for q in d.questions[:3]] == [("single", 1, None), ("single", 2, None), ("single", 3, None)]
    assert {q[:3] for q in d.questions[3:]} == {("pair", 2, 1), ("pair", 3, 1), ("pair", 3, 2)}
    manifest = json.loads((c.out / "manifest.json").read_text())
    assert manifest["version"] == v2.ROUNDS_VERSION and manifest["phases"] is None
    assert np.array_equal(final, m.assigns) and c.status == "completed"


def test_parallel_and_serial_give_identical_questions_answers_and_state(tmp_path):
    spec = {1: (1, 9, "A"), 2: (1, 7, "A2"), 3: (3, 5, "B"), 4: (4, 6, "A"), 5: (5, 4, "C")}
    rule = lambda kind, cid, target, allowed: ("SPLIT" if "SPLIT" in allowed else "KEEP") if kind == "single" \
        else ("MERGE" if {cid, target} == {1, 4} else "NOT_MERGE")
    a, da, fa = run(tmp_path, manager(spec, [(1, 2)]), rule, "serial", decider=Threaded)
    b, db, fb = run(tmp_path, manager(spec, [(1, 2)]), rule, "parallel", decider=Threaded, parallel=4)
    strip = lambda rows: [{k: v for k, v in r.items() if k != "calls"} for r in rows if r["event"] != "round"]
    assert strip(events(a)) == strip(events(b)) and np.array_equal(fa, fb)
    assert a.state_hash() == b.state_hash() and a.status == b.status == "completed"


def test_one_merge_per_cluster_per_round_and_the_merged_cluster_is_judged_again(tmp_path):
    m = manager({1: (1, 10, "A"), 2: (2, 8, "A"), 3: (3, 6, "A2")})
    rule = lambda kind, cid, target, allowed: "KEEP" if kind == "single" else "MERGE"
    c, d, final = run(tmp_path, m, rule)
    void = events(c, "pair_void")
    assert len(void) == 2 and events(c, "round")[1]["pairs"] == 3
    third = [q[:3] for q in d.questions[6:]]
    assert third[0][0] == "single"  # the merged cluster is judged again before any new pair
    assert len(np.unique(final[final > 0])) == 1 and c.status == "completed"


def test_split_after_a_merge_vetoes_it_and_the_pair_is_never_asked_again(tmp_path):
    m = manager({1: (1, 10, "A"), 2: (2, 8, "A")})
    seen = []

    def rule(kind, cid, target, allowed):
        seen.append((kind, cid, target))
        if kind == "pair":
            return "MERGE"
        return "SPLIT" if "SPLIT" in allowed else "KEEP"

    c, d, final = run(tmp_path, m, rule)
    assert len(events(c, "merge_vetoed")) == 1
    assert sum(q[0] == "pair" for q in seen) == 1
    assert sorted(np.unique(final)) == [1, 2] and c.status == "completed"


def test_split_children_are_judged_next_round_and_never_offered_to_merge_back(tmp_path):
    m = manager({1: (1, 6, "A"), 2: (1, 6, "A"), 3: (3, 4, "B")}, [(1, 2)])
    rule = lambda kind, cid, target, allowed: "SPLIT" if "SPLIT" in allowed else keep(kind, cid, target, allowed)
    c, d, final = run(tmp_path, m, rule)
    assert [r["singles"] for r in events(c, "round")][:2] == [2, 2]
    assert not any(q[0] == "pair" and {q[1], q[2]} == {1, 2} for q in d.questions)
    assert sorted(np.unique(final)) == [1, 2, 3]


def test_pair_discard_removes_the_smaller_source(tmp_path):
    m = manager({1: (1, 10, "A"), 2: (2, 4, "A2")})
    rule = lambda kind, cid, target, allowed: "KEEP" if kind == "single" else "DISCARD"
    c, d, final = run(tmp_path, m, rule)
    assert ("pair", 2, 1) in [q[:3] for q in d.questions]
    assert sorted(np.unique(final)) == [0, 1]


def test_final_sweep_asks_each_kept_cluster_one_more_unasked_pair(tmp_path):
    spec = {1: (1, 9, "A"), 2: (2, 7, "A2"), 3: (3, 5, "B"), 4: (4, 3, "C")}
    off, d_off, _ = run(tmp_path, manager(spec), keep, "off", k=1, final_sweep=False)
    on, d_on, _ = run(tmp_path, manager(spec), keep, "on", k=1)
    pairs = lambda d: {frozenset(q[1:3]) for q in d.questions if q[0] == "pair"}
    assert pairs(d_off) < pairs(d_on)
    assert len(events(on, "final_sweep")) == 1 and json.loads((on.out / "summary.json").read_text())["final_sweep_done"]


def test_limits_stop_with_a_separately_scored_stopped_state(tmp_path):
    m = manager({1: (1, 9, "A"), 2: (2, 7, "A2"), 3: (3, 5, "B")})
    c, _, _ = run(tmp_path, m, keep, "decisions", max_decisions=4)
    s = json.loads((c.out / "summary.json").read_text())
    assert c.status == "stopped_limit" and s["stopped_state_evaluation"] and not s["terminal_evaluation_allowed"]
    c, _, _ = run(tmp_path, manager({1: (1, 9, "A"), 2: (2, 7, "A2")}), keep, "rounds", max_rounds=1)
    assert c.status == "stopped_limit" and c.round == 1


def test_disallowed_action_stops_the_channel(tmp_path):
    m = manager({1: (1, 9, "A")})
    with pytest.raises(ControllerStop):
        run(tmp_path, m, lambda *a: "SPLIT")
