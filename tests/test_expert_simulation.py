"""Offline tests of the simulated expert (replay, member-set lookup, teacher forcing); no MAT or model."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from src.agent import curation_decider_v2 as dec
from src.agent import expert_simulation as sim
try:  # feedback loop and answer.json snapshots exist only in the Tianmin copy, not in upstream main
    from src.agent import feedback_optimization as opt
except ImportError:
    opt = None
from src.cluster.manager import ClusterManager
from src.pipeline.curation_controller_v2 import RoundsController


def answer(kind, allowed, action):
    result = {name: {"label": labels[0], "note": "offline synthetic"}
              for name, _, labels, _ in dec.fields_for(kind, allowed)}
    return dict(result, action=action, rationale="offline synthetic")

# Overclusters 1..4; hierarchy cluster 1 = overclusters {1, 2}. The expert splits 1, discards 4 and
# merges the breakaway 2 into 3, so the expert end state is {1}, {2, 3} and noise {4}.
ROWS = [("s 1", "Two families in cluster 1."), ("m 0 4", "Noise."), ("m 3 2", "Same unit as 3.")]


def manager():
    rng = np.random.default_rng(0)
    over = np.repeat([1, 2, 3, 4], 40)
    labels = np.where(over == 2, 1, over)
    waves = -np.sin(np.linspace(0, np.pi, 30))[None, :] + rng.normal(0, .01, (160, 30))
    return ClusterManager(labels, over, np.array([[1.], [2.], [.9], [0.]]), np.sort(rng.uniform(0, 100, 160)), waves)


class KeepProvider:
    def respond(self, content, *, folder):
        kind = "single" if "waveform" in content["schema"]["properties"] else "pair"
        allowed = content["schema"]["properties"]["action"]["enum"]
        return answer(kind, allowed, "KEEP" if kind == "single" else "NOT_MERGE")


@unittest.skipIf(opt is None, "controller-driven mode and reviews need the Tianmin feedback modules")
class ExpertSimulationTests(unittest.TestCase):
    def run_forced(self, root):
        oracle = sim.ExpertOracle(manager(), ROWS, dataset_id="cM2-e004_004-006_CH31")
        decider = sim.TeacherForcedDecider(dec.SkillDecider(KeepProvider()), oracle)
        controller = RoundsController(manager(), "cM2-e004_004-006_CH31", root / "run", decider=decider,
                                      sampling_rate=30000., manifest=decider.describe())
        controller.run()
        return oracle, controller

    def test_teacher_forcing_reaches_expert_end_state(self):
        with tempfile.TemporaryDirectory() as root:
            oracle, c = self.run_forced(Path(root))
            self.assertEqual(c.status, "completed")
            summary, rows = sim.summarize(c.out, final=c.manager.assigns, oracle=oracle)
            self.assertTrue(summary["end_state_vs_expert_replay"]["same_partition"])
            self.assertEqual(summary["labeled"], summary["steps"])
            recorded = {r["expert_action"] for r in rows if r["provenance"] == "expert_recorded"}
            self.assertEqual(recorded, {"SPLIT", "DISCARD", "MERGE"})
            # The model answered KEEP / NOT_MERGE everywhere: only the three recorded edits disagree.
            self.assertEqual(summary["overall"]["n"] - summary["overall"]["correct"], 3)
            for row in rows:
                saved = json.loads((c.out / row["step"] / "answer.json").read_text())
                self.assertEqual(saved["action"], row["agent_action"])  # answer.json keeps the model's own answer

    def test_reviews_bind_to_saved_answers(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            _, c = self.run_forced(root)
            filled = sim.simulated_reviewer("mismatch")(c.out, opt.review_template(c.out))
            reviewed = [r for r in filled["reviews"] if r["expert_action"] is not None]
            self.assertTrue(filled["synthetic"])
            self.assertEqual(len(reviewed), 3)
            self.assertEqual({r["reviewer"] for r in reviewed}, {sim.REVIEWER})
            path = root / "reviews.json"
            path.write_text(json.dumps(dict(filled, reviews=reviewed)))
            bundle = opt.build_bundle(c.out, path, root / "bundle")
            self.assertTrue(bundle["synthetic"])
            everything = sim.simulated_reviewer("all")(c.out, opt.review_template(c.out))
            self.assertEqual(sum(r["expert_action"] is not None for r in everything["reviews"]), c.calls)

    def test_rewrap_keeps_oracle_and_inconsistent_sheet_rejected(self):
        oracle = sim.ExpertOracle(manager(), ROWS, dataset_id="cM2-e004_004-006_CH31")
        wrapped = sim.TeacherForcedDecider(dec.SkillDecider(KeepProvider()), oracle)
        other = wrapped.rewrap(dec.SkillDecider(KeepProvider()))
        self.assertIs(other.oracle, oracle)
        self.assertEqual(other.skill_sha256, wrapped.skill_sha256)
        with self.assertRaises(ValueError):  # the second action refers to an already discarded cluster
            sim.ExpertOracle(manager(), [("m 0 4", ""), ("m 0 4", "")], dataset_id="x")

    def test_split_of_noise_retracts_the_last_discard(self):
        oracle = sim.ExpertOracle(manager(), [("m 0 4", "first"), ("s 0", ""), ("m 0 4", "again")], dataset_id="x")
        once = sim.ExpertOracle(manager(), [("m 0 4", "again")], dataset_id="x")
        self.assertTrue(sim.same_partition(oracle.final, once.final))
        self.assertEqual([r["reason"] for r in oracle.single.values()], ["again"])
        self.assertEqual(oracle.describe()["retracted"], [dict(step=1, raw="m 0 4", undone_at_step=2)])

    def test_split_after_own_merge_retracts_the_merge(self):
        rows = ROWS + [("s 3", ""), ("m 3 2", "again")]
        oracle = sim.ExpertOracle(manager(), rows, dataset_id="x")
        base = sim.ExpertOracle(manager(), ROWS, dataset_id="x")
        self.assertTrue(sim.same_partition(oracle.final, base.final))
        self.assertEqual([r["reason"] for r in oracle.pairs.values()], ["again"])
        self.assertEqual(oracle.counts["UNDO_MERGE"], 1)
        self.assertEqual(oracle.counts["SPLIT"], 1)  # the original-hierarchy split of cluster 1 stays recorded



class StrictReplayTests(unittest.TestCase):
    def replay(self, root, rows=ROWS, **kw):
        oracle = sim.ExpertOracle(manager(), rows, dataset_id="cM2-e004_004-006_CH31")
        replay = sim.ExpertReplay(manager(), oracle, "cM2-e004_004-006_CH31", root / "run",
                                  decider=dec.SkillDecider(KeepProvider()), sampling_rate=30000., **kw)
        replay.run()
        return oracle, replay

    def test_default_keeps_only_clusters_left_alone_for_good(self):
        # split 1 -> KEEP the untouched child | discard 4 | merge 2 into 3 -> KEEP the merged cluster
        with tempfile.TemporaryDirectory() as root:
            oracle, r = self.replay(Path(root), keep_before_merge=False)
            _, rows = sim.summarize(r.out, oracle=oracle)
            self.assertEqual([(x["round"], x["expert_action"]) for x in rows],
                             [(1, "SPLIT"), (2, "KEEP"), (2, "DISCARD"), (3, "MERGE"), (4, "KEEP")])
            self.assertEqual({x["provenance"] for x in rows if x["expert_action"] == "KEEP"},
                             {"derived_no_later_expert_action"})

    def test_questions_follow_the_record_with_derived_keeps(self):
        with tempfile.TemporaryDirectory() as root:
            oracle, r = self.replay(Path(root), keep_before_merge=True)
            self.assertEqual(r.status, "completed")
            summary, rows = sim.summarize(r.out, oracle=oracle)
            # KEEP 3 (merged later) | split 1 -> KEEP both children (2 is merged later) | discard 4 |
            # merge 2 into 3 -> KEEP the merged cluster
            self.assertEqual([(x["round"], x["kind"], x["expert_action"], x["provenance"]) for x in rows], [
                (1, "single", "KEEP", "derived_keep_before_merge"), (1, "single", "SPLIT", "expert_recorded"),
                (2, "single", "KEEP", "derived_no_later_expert_action"),
                (2, "single", "KEEP", "derived_keep_before_merge"), (2, "single", "DISCARD", "expert_recorded"),
                (3, "pair", "MERGE", "expert_recorded"), (4, "single", "KEEP", "derived_no_later_expert_action")])
            self.assertEqual(summary["expert_operations"], dict(effective=3, asked_exactly=3, not_asked=[]))
            self.assertEqual(summary["overall"], dict(n=7, correct=4, accuracy=4 / 7))
            question = json.loads((r.out / rows[5]["step"] / "question.json").read_text())
            self.assertEqual((question["cid"], question["target"]), (2, 3))  # expert 'm 3 2': source 2 into 3

    @unittest.skipIf(opt is None, "reviews need the Tianmin feedback modules")
    def test_recorded_only_feedback(self):
        with tempfile.TemporaryDirectory() as root:
            _, r = self.replay(Path(root))
            filled = sim.simulated_reviewer("all", "recorded")(r.out, opt.review_template(r.out))
            self.assertEqual(sum(x["expert_action"] is not None for x in filled["reviews"]), 3)

    def test_no_keep_mode(self):
        with tempfile.TemporaryDirectory() as root:
            _, r = self.replay(Path(root), keep_questions=False)
            self.assertEqual(r.calls, 3)

    def test_member_set_split_then_merged_back_is_kept_before_its_next_merge(self):
        # Expert splits 1 into {1}, {2}, then merges 2 back into 1: the merged set equals the original
        # cluster 1 that was split, but its next action (none here) decides, not its earlier SPLIT.
        with tempfile.TemporaryDirectory() as root:
            oracle, r = self.replay(Path(root), rows=[("s 1", ""), ("m 1 2", "back")], keep_before_merge=True)
            _, rows = sim.summarize(r.out, oracle=oracle)
            self.assertEqual([(x["expert_action"], x["provenance"]) for x in rows], [
                ("KEEP", "derived_no_later_expert_action"), ("KEEP", "derived_no_later_expert_action"),
                ("SPLIT", "expert_recorded"), ("KEEP", "derived_keep_before_merge"),
                ("KEEP", "derived_keep_before_merge"), ("MERGE", "expert_recorded"),
                ("KEEP", "derived_no_later_expert_action")])

    def test_retracted_edits_are_not_asked(self):
        with tempfile.TemporaryDirectory() as root:
            oracle, r = self.replay(Path(root), rows=ROWS + [("s 3", ""), ("m 3 2", "again")])
            self.assertEqual(r.status, "completed")
            self.assertEqual([x["raw"] for x in oracle.sequence], ["s 1", "m 0 4", "m 3 2"])
            self.assertEqual(oracle.sequence[-1]["reason"], "again")

    def test_prefetch_gives_the_same_questions_and_answers(self):
        with tempfile.TemporaryDirectory() as root:
            oracle, a = self.replay(Path(root) / "a")
            _, b = self.replay(Path(root) / "b", prefetch=True, parallel=3)
            key = lambda r: [(x["round"], x["kind"], x["expert_action"], x["agent_action"], x["match"])
                             for x in sim.summarize(r.out, oracle=oracle)[1]]
            self.assertEqual(key(a), key(b))
            with self.assertRaises(ValueError):
                self.replay(Path(root) / "c", prefetch=True, after_round=lambda r: None)

    def test_step_limit_stops_cleanly(self):
        for prefetch in (False, True):
            with tempfile.TemporaryDirectory() as root:
                _, r = self.replay(Path(root), max_steps=2, prefetch=prefetch)
                self.assertEqual((r.status, r.calls), ("stopped_limit", 1))
                self.assertTrue((r.out / "step_00001" / "simulation.json").exists())


if __name__ == "__main__":
    unittest.main()
