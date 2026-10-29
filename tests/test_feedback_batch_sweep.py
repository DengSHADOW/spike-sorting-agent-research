"""Campaign invariants: independent N starts, exact chain links, no learning on test runs."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from scripts.run import run_feedback_batch_sweep as sweep


class BatchSweepTests(unittest.TestCase):
    def args(self, root):
        return SimpleNamespace(output_root=str(root / "out"), base_skill=str(root / "skill.json"),
                               repeats=1, batch_sizes=[1, 4, 8], python=str(root / "venv/bin/python"),
                               base_url="http://127.0.0.1:8000", parallel=4, regression_k=3,
                               protect_correct=False, skip_baseline=False, prefetch_tests=False)

    def test_chain_and_starting_skill_isolation(self):
        args = self.args(Path("/tmp/sweep_test"))
        jobs = sweep.plan(args)
        self.assertEqual(len(jobs), 31)
        for n in (1, 4, 8):
            learns = [j for j in jobs if j["n"] == n and j["learn"]]
            self.assertEqual([j["channel"] for j in learns], ["CH31", "CH3", "CH30"])
            self.assertEqual(learns[0]["skill"], str(Path(args.base_skill).resolve()))
            for before, after in zip(learns, learns[1:]):
                self.assertEqual(after["skill"], str(Path(before["out"]) / "final_skill.json"))
            for j in (j for j in jobs if j["n"] == n and not j["learn"]):
                self.assertEqual(j["skill"], str(Path(learns[j["stage"] - 1]["out"]) / "final_skill.json"))
        for j in jobs:
            self.assertNotEqual((j["learn"], j["channel"]), (True, "CH20"))
            self.assertEqual("--online-learning" in sweep.command(args, j), j["learn"])

    def test_python_venv_symlink_is_not_resolved(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            args = self.args(root)
            path = Path(args.python)
            path.parent.mkdir(parents=True)
            path.symlink_to("/usr/bin/python3")
            self.assertEqual(sweep.command(args, sweep.plan(args)[0])[0], str(path))

    def test_wrong_starting_skill_or_learning_in_eval_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            args = self.args(root)
            skill = Path(args.base_skill)
            sweep.save(skill, dict(version="original", rules=[]))
            j = sweep.plan(args)[0]
            out = Path(j["out"])
            out.mkdir(parents=True)
            manifest = dict(skill_sha256="wrong", skill_version="original")
            sweep.save(out / "run_manifest.json", manifest)
            sweep.save(out / "result.json", {})
            with self.assertRaisesRegex(RuntimeError, "Wrong starting skill"):
                sweep.check_result(j, sweep.sha(skill))
            manifest.update(skill_sha256=sweep.sha(skill), dataset=dict(dataset_id=sweep.DATASETS[j["channel"]]),
                            online_learning=dict(batch_size=1))
            sweep.save(out / "run_manifest.json", manifest)
            with self.assertRaisesRegex(RuntimeError, "must not learn"):
                sweep.check_result(j, sweep.sha(skill))


if __name__ == "__main__":
    unittest.main()
