"""Real GEPA engine integration with scripted providers; zero network."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from scripts.demo.demo_ace_gepa import DemoModel, demo_bundle, demo_optimizer
from src.agent import curation_decider_v2 as dec
from src.agent import ace_gepa as ag
from src.agent import feedback_optimization as replay
from src.agent.feedback_runtime import RoundLearningHook, load_config
from src.agent.feedback_session import FeedbackSession, atomic_json


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError("No network permitted in learning tests")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def bundles(tmp_path):
    return (demo_bundle(tmp_path, "learning", "learning"),
            demo_bundle(tmp_path, "validation", "validation"))


def test_real_gepa_search_and_gate_with_label_isolation(tmp_path, bundles):
    learning, validation = bundles
    execution, reflection, proposal = DemoModel(), DemoModel(), DemoModel()
    out = tmp_path / "update"
    result = ag.run_ace_gepa(dec.SKILL_V1, ag.cases_from_bundles([learning], "learning"),
        ag.cases_from_bundles([validation], "validation"), execution, reflection, proposal, out,
        max_proposals=2, max_evaluations=40)
    assert result["status"] == "accepted"
    assert result["search_candidates"] >= 2
    assert replay.read(out / "search.json")["per_val_instance_best_candidates"]
    assert replay.sha(out / "accepted_skill.json") == result["accepted_skill_sha256"]
    assert all("SYNTHETIC_LABEL_DO_NOT_LEAK" not in str(c) for c in execution.requests)
    assert all("synthetic-validation" not in c["text"] for c in reflection.requests + proposal.requests)
    assert result["evaluations"] <= 40
    assert replay.read(out / "manifest.json")["gepa_version"] == "0.1.4"


def test_no_suggestions_is_rejected_without_execution_calls(tmp_path, bundles):
    class NoChange(DemoModel):
        def respond(self, content, *, folder):
            if content["schema_name"] == "ace_curate":
                return {"suggestions": []}
            return super().respond(content, folder=folder)
    execution = DemoModel()
    result = ag.run_ace_gepa(dec.SKILL_V1, ag.cases_from_bundles([bundles[0]], "learning"),
        ag.cases_from_bundles([bundles[1]], "validation"), execution, NoChange(), DemoModel(), tmp_path / "out",
        max_evaluations=40)
    assert result["status"] == "rejected" and not execution.requests
    assert not (tmp_path / "out/accepted_skill.json").exists()


def test_proposer_failure_cannot_be_swallowed_by_gepa(tmp_path, bundles):
    class Broken(DemoModel):
        def respond(self, *args, **kwargs):
            raise RuntimeError("synthetic proposer outage")
    with pytest.raises(RuntimeError, match="proposal failed"):
        ag.run_ace_gepa(dec.SKILL_V1, ag.cases_from_bundles([bundles[0]], "learning"),
            ag.cases_from_bundles([bundles[1]], "validation"), DemoModel(), DemoModel(), Broken(), tmp_path / "out",
            max_proposals=2, max_evaluations=40)
    assert replay.read(tmp_path / "out/result.json")["status"] == "failed"
    assert not (tmp_path / "out/accepted_skill.json").exists()


def test_unbacked_ace_evidence_rejected(tmp_path, bundles):
    class Unbacked(DemoModel):
        def respond(self, content, *, folder):
            result = super().respond(content, folder=folder)
            if content["schema_name"] == "ace_curate":
                result["suggestions"][0]["evidence_keys"] = ["invented"]
            return result
    base, _ = dec.load_skill()
    with pytest.raises(ValueError, match="provenance"):
        ag.curate(base, ag.cases_from_bundles([bundles[0]], "learning"), Unbacked(), tmp_path / "ace")


def test_candidate_cannot_modify_unlisted_rules(tmp_path, bundles):
    base, _ = dec.load_skill()
    snapshot = ag.curate(base, ag.cases_from_bundles([bundles[0]], "learning"), DemoModel(), tmp_path / "ace")
    with pytest.raises(ValueError, match="unknown"):
        ag.materialize(base, snapshot, {"skill_plan": json.dumps(dict(edits=[dict(suggestion_id="other", wording="x")]))})
    with pytest.raises(ValueError, match="Only skill_plan"):
        ag.materialize(base, snapshot, {"controller": "changed"})
    unchanged, _ = ag.materialize(base, snapshot, {"skill_plan": '{"edits": []}'})
    assert unchanged == base


def test_session_counts_unique_feedback_and_is_portable(tmp_path, bundles):
    session = FeedbackSession.create(tmp_path / "session", dec.SKILL_V1, [bundles[1]], feedback_batch_size=3)
    session.import_bundle(bundles[0])
    assert session.import_bundle(bundles[0])["added"] == 0
    assert session.status()["pending"] == 2
    assert session.advance(demo_optimizer)["status"] == "waiting"
    session.set_batch_size(2)
    assert session.advance(demo_optimizer)["status"] == "accepted"
    assert session.status()["pending"] == 0
    assert session.advance(demo_optimizer)["status"] == "waiting"
    moved = tmp_path / "moved_session"
    shutil.copytree(session.path, moved)
    relocated = FeedbackSession(moved)
    assert relocated.active().is_relative_to(moved)
    assert relocated.import_bundle(bundles[0])["added"] == 0


def test_revision_replaces_pending_without_increasing_count(tmp_path, bundles):
    session = FeedbackSession.create(tmp_path / "session", dec.SKILL_V1, [bundles[1]], feedback_batch_size=3)
    session.import_bundle(bundles[0])
    feedback_path = tmp_path / "learning_reviews.json"
    feedback = replay.read(feedback_path)
    feedback["reviews"][0].update(revision=2, expert_reason="Updated expert explanation")
    atomic_json(feedback_path, feedback)
    revised = tmp_path / "revised"
    replay.build_bundle(tmp_path / "learning_run", feedback_path, revised)
    assert session.import_bundle(revised)["revised"] == 1
    assert session.status()["pending"] == 2
    with pytest.raises(ValueError, match="stale"):
        session.import_bundle(bundles[0])


def test_failed_update_retains_feedback_and_needs_explicit_retry(tmp_path, bundles):
    session = FeedbackSession.create(tmp_path / "session", dec.SKILL_V1, [bundles[1]], feedback_batch_size=2)
    session.import_bundle(bundles[0])
    def broken(*args):
        raise RuntimeError("synthetic failure")
    with pytest.raises(RuntimeError, match="synthetic failure"):
        session.advance(broken)
    assert session.status()["pending"] == 2 and session.status()["blocked"]
    with pytest.raises(RuntimeError, match="blocks automatic retry"):
        session.advance(demo_optimizer)
    assert session.advance(demo_optimizer, retry_failed=True)["status"] == "accepted"


def test_interruption_recovers_without_duplicate_model_calls(tmp_path, bundles):
    session = FeedbackSession.create(tmp_path / "session", dec.SKILL_V1, [bundles[1]], feedback_batch_size=2)
    session.import_bundle(bundles[0])
    def crash_after_result(*args):
        demo_optimizer(*args)
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        session.advance(crash_after_result)
    with pytest.raises(RuntimeError, match="Interrupted"):
        session.advance(demo_optimizer)
    session.recover()
    assert session.status()["pending"] == 0
    assert session.status()["attempts"][0]["status"] == "accepted"


def test_manual_and_partial_batch_flush(tmp_path, bundles):
    session = FeedbackSession.create(tmp_path / "session", dec.SKILL_V1, [bundles[1]], feedback_batch_size=None)
    session.import_bundle(bundles[0])
    assert session.advance(demo_optimizer)["status"] == "waiting"
    session.set_batch_size(10)
    assert session.advance(demo_optimizer)["status"] == "waiting"
    assert session.advance(demo_optimizer, force=True)["status"] == "accepted"


def test_round_boundary_switch_loads_accepted_skill(tmp_path, bundles):
    session = FeedbackSession.create(tmp_path / "session", dec.SKILL_V1, [bundles[1]], feedback_batch_size=2)
    session.import_bundle(bundles[0])
    events = []
    controller = SimpleNamespace(decider=dec.SkillDecider(DemoModel()), round=1,
                                  log=lambda event, **data: events.append((event, data)))
    old = controller.decider.skill_sha256
    hook = RoundLearningHook(session, pause=False, optimizer=demo_optimizer)
    hook.update(controller)
    assert old != controller.decider.skill_sha256
    assert controller.decider.skill["version"] == "update_0001"
    assert [event for event, _ in events] == ["skill_optimization", "skill_activated"]


def test_full_controller_learning_keeps_one_skill_per_round(tmp_path, bundles):
    import numpy as np
    from src.cluster.manager import ClusterManager
    from src.pipeline.curation_controller_v2 import RoundsController
    session = FeedbackSession.create(tmp_path / "session", dec.SKILL_V1, [bundles[1]], feedback_batch_size=1)
    def reviewer(run, template):
        template["synthetic"] = True
        for row in template["reviews"]:
            row.update(expert_action="KEEP" if row["kind"] == "single" else "NOT_MERGE",
                       expert_reason="SYNTHETIC_LABEL_DO_NOT_LEAK", reviewer="synthetic",
                       evidence_sufficient=True)
        return template
    hook = RoundLearningHook(session, pause=False, optimizer=demo_optimizer, reviewer=reviewer)
    rng = np.random.default_rng(7)
    over = np.repeat([1, 2, 3], 40)
    labels = np.where(over == 2, 1, over)
    waves = -np.sin(np.linspace(0, np.pi, 30))[None, :] + rng.normal(0, .01, (120, 30))
    manager = ClusterManager(labels, over, np.array([[1.], [2.], [.9], [0.]]),
                             np.sort(rng.uniform(0, 100, 120)), waves)
    model = DemoModel()
    controller = RoundsController(manager, "cM2-e004_004-006_CH31", tmp_path / "run",
        decider=dec.SkillDecider(model), sampling_rate=30000., after_round=hook, parallel=3)
    controller.run()
    assert controller.status == "completed"
    per_round = {}
    for step in controller.out.glob("step_*"):
        q = replay.read(step / "question.json")
        policy = replay.read(step / "policy_snapshot.json")
        per_round.setdefault(q["round"], set()).add(policy["skill_sha256"])
    assert all(len(hashes) == 1 for hashes in per_round.values())
    assert len(set.union(*per_round.values())) > 1
    assert any(a["status"] == "accepted" for a in session.status()["attempts"])
    assert all("SYNTHETIC_LABEL_DO_NOT_LEAK" not in str(r) for r in model.requests)


def test_live_profile_mismatch_stops_before_optimization(tmp_path, bundles):
    session = FeedbackSession.create(tmp_path / "session", dec.SKILL_V1, [bundles[1]],
        config={"execution": {"profile": {"model": "expected-model"}}})
    hook = RoundLearningHook(session, pause=False)
    with pytest.raises(ValueError, match="must match"):
        hook.check_execution_profile(SimpleNamespace(profile={"model": "other-model"}))


def test_validation_cannot_be_imported_as_learning(tmp_path, bundles):
    session = FeedbackSession.create(tmp_path / "session", dec.SKILL_V1, [bundles[1]])
    with pytest.raises(ValueError, match="Expected learning"):
        session.import_bundle(bundles[1])
    changed = tmp_path / "relabeled"
    shutil.copytree(bundles[1], changed)
    meta = replay.read(changed / "bundle.json")
    meta["role"] = "learning"
    atomic_json(changed / "bundle.json", meta)
    with pytest.raises(ValueError, match="Validation cases"):
        session.import_bundle(changed)


def test_config_resolves_profiles_and_rejects_unknown_search_keys(tmp_path):
    example = dec.ROOT / "configs/controller_v2/learning.example.json"
    config = load_config(example)
    assert config["execution"]["backend"] == "hosted"
    config["search"]["unexpected"] = True
    path = tmp_path / "bad.json"
    atomic_json(path, config)
    with pytest.raises(ValueError, match="Unknown search"):
        load_config(path)
