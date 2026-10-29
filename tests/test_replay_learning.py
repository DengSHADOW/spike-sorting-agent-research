"""Offline batch-learning tests: actual replay observations, scripted models, no network."""
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest

from src.agent import curation_decider_v2 as dec
from src.agent import expert_simulation as sim
from src.agent import replay_learning as learning
from tests.test_expert_simulation import ROWS, answer, manager


class Executor:
    def respond(self, content, *, folder):
        kind = "single" if "waveform" in content["schema"]["properties"] else "pair"
        allowed = content["schema"]["properties"]["action"]["enum"]
        action = "KEEP" if kind == "single" else "NOT_MERGE"
        instructions, preface = content["instructions"], content["preface"]
        if "[FIXALL]" in instructions:
            if kind == "pair":
                action = "MERGE"
            elif "cluster 4." in preface:
                action = "DISCARD"
            elif "cluster 1." in preface and "SPLIT" in allowed:
                action = "SPLIT"
        if "[GREEDY]" in instructions or ("[FIX4]" in instructions and "cluster 4." in preface):
            action = "DISCARD"
        return answer(kind, allowed, action)


class Proposer:
    def __init__(self, texts=()):
        self.texts, self.seen, self.evidence = list(texts), [], []

    def respond(self, content, *, folder):
        payload = json.loads(content["text"])
        if content["schema_name"] == "feedback_evidence":
            self.evidence.append((payload, len(content["images"])))
            return {key: "Offline evidence; retain uncertainty." for key in learning.EVIDENCE_SCHEMA["required"]}
        self.seen.append(payload)
        assert content["images"] == []  # joint curation stays within the existing serving image limit
        text = self.texts.pop(0) if self.texts else None
        return dict(diagnosis="offline", edits=[] if text is None else
                    [dict(op="add", rule_id="", scope="both", text=text)])


class OnlineLearningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        root = Path(cls.fixture.name)
        oracle = sim.ExpertOracle(manager(), ROWS, dataset_id="cM2-e004_004-006_CH31")
        replay = sim.ExpertReplay(manager(), oracle, oracle.dataset_id, root / "run",
                                 decider=dec.SkillDecider(Executor()), sampling_rate=30000.)
        replay.run()
        cls.rounds = replay.round

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(Path(self.fixture.name) / "run", self.root / "run")
        self.replay = SimpleNamespace(out=self.root / "run", round=0, status="running",
                                      dataset_id="cM2-e004_004-006_CH31", decider=dec.SkillDecider(Executor()),
                                      log=lambda *a, **kw: None)

    def learner(self, texts=(), **kw):
        proposer = Proposer(texts)
        learner = learning.OnlineLearner(self.root / "learning", proposer=proposer, evaluator=Executor(), **kw)
        return learner, proposer

    def reveal(self, learner):
        for r in range(1, self.rounds + 1):
            self.replay.round = r
            learner(self.replay)
        self.replay.status = "completed"

    def test_all_feedback_counts_and_n1_correct_batches_are_noops(self):
        learner, proposer = self.learner(batch_size=1)
        self.reveal(learner)
        self.assertEqual(learner.feedback_count, 5)
        self.assertEqual(learner.correct_feedback_count, 2)
        self.assertEqual(len(learner.attempts), 5)
        self.assertEqual(learner.summary()["outcomes"], {"no_edit_proposed": 3, "all_correct_no_change": 2})
        self.assertEqual(len(proposer.seen), 3)
        self.assertEqual(learner.pending, [])

    def test_n4_jointly_receives_positive_and_negative_without_future(self):
        learner, proposer = self.learner(batch_size=4)
        self.replay.round = 2
        learner(self.replay)
        self.assertEqual(len(learner.pending), 3)
        self.assertEqual(proposer.seen, [])
        self.replay.round = 3
        learner(self.replay)
        cases = proposer.seen[0]["cases"]
        self.assertEqual([c["was_correct"] for c in cases], [False, True, False, False])
        self.assertEqual([c["expert"]["action"] for c in cases], ["SPLIT", "KEEP", "DISCARD", "MERGE"])
        self.assertTrue(all(c["question"]["round"] <= 3 for c in cases))
        self.assertEqual([c["case_id"] for c in cases], [f"case_{i}" for i in range(1, 5)])
        self.assertEqual(cases[1]["expert"]["rationale"], "")
        self.assertNotEqual(cases[1]["provenance"], "expert_recorded")
        self.assertTrue(all(0 < n <= 6 for _, n in proposer.evidence))
        learner(self.replay)
        self.assertEqual(len(learner.attempts), 1)  # repeated hooks cannot consume feedback twice

    def test_n8_partial_flush_and_stopped_run(self):
        learner, proposer = self.learner(batch_size=8)
        self.reveal(learner)
        self.assertEqual(len(learner.pending), 5)
        self.replay.status = "stopped_limit"
        learner.finish(self.replay)
        self.assertEqual(proposer.seen, [])
        self.replay.status = "completed"
        learner.finish(self.replay, flush=False)
        self.assertEqual(proposer.seen, [])
        learner.finish(self.replay)
        self.assertEqual(len(proposer.seen[0]["cases"]), 5)
        self.assertTrue(learner.attempts[0]["partial"])
        learner.finish(self.replay)
        self.assertEqual(len(learner.attempts), 1)

    def test_n8_full_batch_is_one_joint_proposal(self):
        # Extend the fixture stream with three independently numbered synthetic feedback events.
        for i in range(1, 4):
            source = self.replay.out / f"step_{i:05d}"
            target = self.replay.out / f"step_{i + 5:05d}"
            shutil.copytree(source, target)
            q = learning.read(target / "question.json")
            q["round"] = self.rounds + 1
            (target / "question.json").write_text(json.dumps(q))
        learner, proposer = self.learner(batch_size=8)
        self.replay.round = self.rounds + 1
        learner(self.replay)
        self.assertEqual(len(learner.attempts), 1)
        self.assertFalse(learner.attempts[0]["partial"])
        self.assertEqual(len(proposer.seen[0]["cases"]), 8)
        self.assertEqual(len(proposer.evidence), 8)
        self.assertEqual(learner.pending, [])

    def test_accepts_batch_gain_and_protects_successes(self):
        learner, _ = self.learner(["Use distinct evidence for every action [FIXALL]."], batch_size=4)
        self.replay.round = 3
        learner(self.replay)
        self.assertEqual(learner.attempts[0]["outcome"], "accepted")
        checks = learner.attempts[0]["batch_checks"]
        self.assertEqual(learner.attempts[0]["correct_before"], 1)
        self.assertFalse((learner.out / "attempt_0001" / "parent").exists())
        self.assertEqual(sum(c["right"] for c in checks), 4)
        self.assertIn("n4b1", self.replay.decider.skill["version"])

    def test_fixing_some_errors_is_enough_no_requirement_to_fix_every_error(self):
        learner, _ = self.learner(["Discard noise-like clusters when appropriate [FIX4]."], batch_size=4)
        self.replay.round = 3
        learner(self.replay)
        self.assertEqual(learner.attempts[0]["outcome"], "accepted")
        self.assertEqual(sum(c["right"] for c in learner.attempts[0]["batch_checks"]), 2)

    def test_candidate_cannot_trade_away_correct_keep_inside_batch(self):
        original = self.replay.decider.skill_sha256
        learner, _ = self.learner(["Discard every cluster that is offered [GREEDY]."], batch_size=4,
                                  protect_correct=True)
        self.replay.round = 3
        learner(self.replay)
        self.assertEqual(learner.attempts[0]["outcome"], "batch_regression")
        self.assertEqual(self.replay.decider.skill_sha256, original)

    def test_without_protection_only_net_batch_gain_decides(self):
        learner, _ = self.learner(["Discard every cluster that is offered [GREEDY]."], batch_size=4, k=0)
        self.replay.round = 3
        learner(self.replay)
        a = learner.attempts[0]
        self.assertTrue(any(c["was_right"] and not c["right"] for c in a["batch_checks"]))
        self.assertEqual(a["outcome"], "accepted" if a["correct_after"] > a["correct_before"] else "batch_no_gain")
        self.assertEqual(a.get("regression", []), [])

    def test_recent_k_guard_outside_batch_is_still_active(self):
        learner, _ = self.learner([None, "Discard every cluster that is offered [GREEDY]."],
                                  batch_size=1, feedback_scope="mismatch")
        self.replay.round = 2
        learner(self.replay)
        self.assertEqual([a["outcome"] for a in learner.attempts], ["no_edit_proposed", "regression"])
        self.assertEqual(learner.attempts[1]["regression"][-1]["step"], "step_00002")

    def test_no_gain_rejected_even_if_proposal_is_valid(self):
        learner, _ = self.learner(["Use the same existing guidance without a new effect."], batch_size=4)
        self.replay.round = 3
        learner(self.replay)
        self.assertEqual(learner.attempts[0]["outcome"], "batch_no_gain")

    def test_evaluation_failure_keeps_active_skill(self):
        learner, _ = self.learner(["Use distinct evidence for every action [FIXALL]."], batch_size=4)
        original = self.replay.decider.skill_sha256

        class BrokenEvaluator:
            def respond(self, *args, **kwargs):
                raise RuntimeError("offline simulated provider failure")

        learner.evaluator = BrokenEvaluator()
        self.replay.round = 3
        learner(self.replay)
        self.assertEqual(learner.attempts[0]["outcome"], "evaluation_error")
        self.assertEqual(self.replay.decider.skill_sha256, original)

    def test_invalid_evidence_cannot_be_used_to_propose_rules(self):
        learner, proposer = self.learner(batch_size=4)
        proposer.respond = lambda *args, **kwargs: {"invented": "not evidence"}
        original = self.replay.decider.skill_sha256
        self.replay.round = 3
        learner(self.replay)
        self.assertEqual(learner.attempts[0]["outcome"], "proposer_error")
        self.assertEqual(self.replay.decider.skill_sha256, original)

    def test_recorded_only_filters_derived_keep_and_k_zero(self):
        learner, proposer = self.learner(batch_size=8, sources="recorded", k=0)
        self.reveal(learner)
        learner.finish(self.replay)
        self.assertEqual(learner.feedback_count, 3)
        self.assertTrue(all(c["provenance"] == "expert_recorded" for c in proposer.seen[0]["cases"]))

    def test_attempt_cap_keeps_unconsumed_feedback_visible(self):
        learner, _ = self.learner(batch_size=1, max_feedback=1)
        self.reveal(learner)
        learner.finish(self.replay)
        self.assertEqual(len(learner.attempts), 1)
        self.assertEqual(learner.summary()["pending_feedback"], 4)
        state = learning.read(learner.out / "feedback_state.json")
        self.assertEqual(len(state["pending_steps"]), 4)

    def test_static_schema_and_gate_protection_and_remove(self):
        base = dict(version="v", gates=[dict(id="G1")], rules=[dict(id="S1", scope="single", text="x" * 50)])
        proposal = dict(diagnosis="test", edits=[dict(op="remove", rule_id="S1", scope="single", text="")])
        self.assertIsNone(learning.static_check(base, proposal, 10000))
        self.assertEqual(learning.apply_edits(base, proposal["edits"], "next")["gates"], base["gates"])
        self.assertEqual(learning.static_check(base, dict(proposal, gates=[]), 10000), "malformed_proposal")
        proposal["edits"][0]["op"] = "unknown"
        self.assertEqual(learning.static_check(base, proposal, 10000), "malformed_proposal")

    def test_invalid_configuration_and_wrong_replay_rejected(self):
        for kw in (dict(batch_size=0), dict(batch_size=True), dict(k=-1), dict(max_feedback=0)):
            with self.assertRaises(ValueError):
                learning.OnlineLearner(self.root / "invalid", proposer=None, evaluator=None, **kw)
        learner, _ = self.learner()
        learner(self.replay)
        self.replay.out = self.root / "other"
        with self.assertRaises(ValueError):
            learner(self.replay)

    def test_real_replay_uses_new_skill_only_after_round_boundary(self):
        oracle = sim.ExpertOracle(manager(), ROWS, dataset_id="cM2-e004_004-006_CH31")
        proposer = Proposer(["Use distinct evidence for every action [FIXALL]."])
        learner = learning.OnlineLearner(self.root / "live_learning", proposer=proposer, evaluator=Executor(), batch_size=4)
        replay = sim.ExpertReplay(manager(), oracle, oracle.dataset_id, self.root / "live_run",
                                  decider=dec.SkillDecider(Executor()), sampling_rate=30000., after_round=learner)
        replay.run()
        learner.finish(replay)
        snapshots = [learning.read(p) for p in sorted(replay.out.glob("step_*/simulation.json"))]
        self.assertEqual(len({s["skill_sha256"] for s in snapshots[:4]}), 1)
        self.assertNotEqual(snapshots[3]["skill_sha256"], snapshots[4]["skill_sha256"])
        self.assertEqual(learner.summary()["outcomes"], {"accepted": 1, "all_correct_no_change": 1})


if __name__ == "__main__":
    unittest.main()
