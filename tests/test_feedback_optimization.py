"""Offline behavioural tests, executable with unittest; no pytest, MATs or model required."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from src.agent import curation_decider_v2 as dec
from src.agent import feedback_optimization as opt
from src.agent.hosted_provider import HostedProvider
from src.agent.vllm_provider import ProviderStop
from src.pipeline.curation_controller_v2 import LimitStop


def save(path, value):
    Path(path).write_text(json.dumps(value), encoding="utf-8")


def answer(kind, allowed, action):
    result = {name: {"label": labels[0], "note": "offline synthetic"}
              for name, _, labels, _ in dec.fields_for(kind, allowed)}
    return dict(result, action=action, rationale="offline synthetic")


class FakeProvider:
    def __init__(self, candidate_action="NOT_MERGE"):
        self.contents = []
        self.candidate_action = candidate_action

    def respond(self, content, *, folder):
        self.contents.append(deepcopy(content))
        action = self.candidate_action if "[NEW_RULE]" in content["instructions"] else "MERGE"
        return answer("pair", ["MERGE", "NOT_MERGE", "DISCARD"], action)


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base, _ = dec.load_skill()
        self.base_path = self.root / "base.json"
        save(self.base_path, self.base)
        self.candidate = dict(deepcopy(self.base), version="feedback_v1", parent=self.base["version"])
        self.candidate["rules"].append(dict(id="NEW_RULE", scope="pair", text="Use the available evidence."))
        self.candidate_path = self.root / "candidate.json"
        save(self.candidate_path, self.candidate)

    def run_fixture(self, name, state):
        run = self.root / name
        step = run / "step_00001"
        step.mkdir(parents=True)
        kind, allowed = "pair", ["MERGE", "NOT_MERGE", "DISCARD"]
        save(run / "manifest.json", dict(dataset_id="synthetic_CH31", input_sha256="same-recording"))
        save(step / "question.json", dict(kind=kind, allowed=allowed, cid=1, target=2, state_sha256=state))
        images = []
        for image in ("overlay", "ccg_acg", "amplitude_time"):
            png = ("synthetic-not-real-png-" + image).encode()
            (step / (image + ".png")).write_bytes(png)
            images.append(dict(name=image, sha256=hashlib.sha256(png).hexdigest()))
        contract = dec.CONTRACT.read_text()
        skill_sha = opt.sha(self.base_path)
        metadata = dict(skill_sha256=skill_sha, contract_sha256=hashlib.sha256(contract.encode()).hexdigest())
        request = dict(instructions=dec.instructions_v2(contract, self.base, kind, allowed),
                       preface="synthetic pair", text='{"measurements": {"correlation": 0.9}}',
                       images=images, images_first=True, schema=dec.schema_v2(kind, allowed),
                       schema_name="curation_pair_observe_then_act", **metadata)
        save(step / "request_content.json", request)
        save(step / "policy_snapshot.json", dict(contract=contract, skill=self.base, **metadata))
        save(step / "answer.json", answer(kind, allowed, "MERGE"))
        return run

    def bundle(self, name, role, state):
        run = self.run_fixture(name, state)
        feedback = opt.review_template(run)
        feedback.update(role=role, synthetic=True)
        feedback["reviews"][0].update(expert_action="NOT_MERGE", expert_reason="SYNTHETIC_SECRET_LABEL",
                                      reviewer="synthetic-reviewer", evidence_sufficient=True)
        path = self.root / (name + "_reviews.json")
        save(path, feedback)
        output = self.root / (name + "_bundle")
        opt.build_bundle(run, path, output)
        return output

    def optimize(self, provider=None):
        train = self.bundle("train", "learning", "state1")
        val = self.bundle("val", "validation", "state2")
        output = self.root / "optimization"
        result = opt.optimize_once(self.base_path, train, val, provider or FakeProvider(), output,
                                   candidate_path=self.candidate_path)
        return output, result

    def test_accept_and_no_expert_label_in_execution_request(self):
        provider = FakeProvider()
        output, result = self.optimize(provider)
        self.assertEqual(result["status"], "accepted")
        self.assertTrue((output / "accepted_skill.json").exists())
        self.assertEqual(len(provider.contents), 4)
        for content in provider.contents:
            self.assertNotIn("SYNTHETIC_SECRET_LABEL", str(content))
            self.assertNotIn("expert_action", str(content))
        first, second = provider.contents[:2]
        self.assertEqual({k: v for k, v in first.items() if k != "instructions"},
                         {k: v for k, v in second.items() if k != "instructions"})

    def test_tie_rejected(self):
        output, result = self.optimize(FakeProvider("MERGE"))
        self.assertEqual(result["status"], "rejected")
        self.assertFalse((output / "accepted_skill.json").exists())

    def test_new_false_discard_rejected(self):
        output, result = self.optimize(FakeProvider("DISCARD"))
        self.assertEqual(result["new_false_discards"], 1)
        self.assertFalse((output / "accepted_skill.json").exists())

    def test_overlap_rejected_before_calls(self):
        train = self.bundle("train", "learning", "same-state")
        val = self.bundle("val", "validation", "same-state")
        provider = FakeProvider()
        with self.assertRaisesRegex(ValueError, "overlapping"):
            opt.optimize_once(self.base_path, train, val, provider, self.root / "out",
                              candidate_path=self.candidate_path)
        self.assertEqual(provider.contents, [])

    def test_evidence_tampering_rejected(self):
        train = self.bundle("train", "learning", "state1")
        (train / "step_00001" / "overlay.png").write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "modified"):
            opt.load_bundle(train)

    def test_wrong_state_target_and_missing_review_rejected(self):
        run = self.run_fixture("run", "state1")
        template = opt.review_template(run)
        path = self.root / "reviews.json"
        for changes in ({}, {"expert_action": "SPLIT"}, {"target": 9}, {"state_sha256": "wrong"}):
            feedback = deepcopy(template)
            feedback["reviews"][0].update(expert_action="NOT_MERGE", reviewer="human", evidence_sufficient=True)
            if not changes:
                feedback["reviews"][0]["evidence_sufficient"] = None
            feedback["reviews"][0].update(changes)
            save(path, feedback)
            with self.assertRaises(ValueError):
                opt.build_bundle(run, path, self.root / "out")
        self.assertFalse((self.root / "out").exists())

    def test_final_test_role_rejected(self):
        run = self.run_fixture("run", "state1")
        feedback = opt.review_template(run)
        feedback["role"] = "test"
        path = self.root / "reviews.json"
        save(path, feedback)
        with self.assertRaises(ValueError):
            opt.build_bundle(run, path, self.root / "out")

    def test_model_failure_recorded_and_no_version_accepted(self):
        class Broken(FakeProvider):
            def respond(self, *args, **kwargs):
                raise RuntimeError("offline failure")
        with self.assertRaisesRegex(RuntimeError, "offline failure"):
            self.optimize(Broken())
        self.assertEqual(opt.read(self.root / "optimization/result.json")["status"], "failed")
        self.assertFalse((self.root / "optimization/accepted_skill.json").exists())

    def test_automatic_proposal_sees_learning_only(self):
        train = self.bundle("train", "learning", "learning-state")
        class Proposer:
            def respond(inner, content, *, folder):
                self.assertIn("learning-state", content["text"])
                self.assertNotIn("validation-state", content["text"])
                return dict(rules=self.candidate["rules"], explanation="Synthetic proposal")
        val = self.bundle("val", "validation", "validation-state")
        result = opt.optimize_once(self.base_path, train, val, FakeProvider(), self.root / "out",
                                   proposer=Proposer(), version="new")
        self.assertEqual(result["status"], "accepted")


class HostedTests(unittest.TestCase):
    def test_success_keeps_images_schema_and_actual_usage(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            profile = dict(base_url="https://example.invalid/v1", model="fake/model", api_key_env="FAKE_KEY",
                           max_tokens=32, temperature=0, response_format="json_schema")
            bodies = []
            def transport(body):
                bodies.append(body)
                return dict(model="fake/model-version", provider="synthetic", usage={"cost": 0.01},
                            choices=[dict(finish_reason="stop", message=dict(content='{"ok": true}'))])
            provider = HostedProvider(profile, root / "ledger.jsonl", max_calls=1, transport=transport)
            schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
            content = dict(instructions="Return JSON", preface="question", text="measurements", images=[b"PNG"],
                           schema=schema, schema_name="test")
            self.assertEqual(provider.respond(content, folder=root), {"ok": True})
            user = bodies[0]["messages"][1]["content"]
            self.assertEqual([x["type"] for x in user], ["text", "image_url", "text"])
            self.assertEqual(bodies[0]["response_format"]["json_schema"]["schema"], schema)
            self.assertEqual(provider.stats()["usage"], [{"cost": 0.01}])

    def test_parallel_request_cap_cannot_be_exceeded(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            profile = dict(base_url="https://example.invalid/v1", model="fake/model", api_key_env="FAKE_KEY",
                           max_tokens=32, temperature=0, response_format="json_object")
            def transport(body):
                return dict(choices=[dict(finish_reason="stop", message=dict(content="{}"))])
            provider = HostedProvider(profile, root / "ledger.jsonl", max_calls=2, transport=transport)
            content = dict(instructions="Return JSON", text="input", images=[], schema={}, schema_name="test")
            def call(index):
                folder = root / str(index)
                folder.mkdir()
                try:
                    provider.respond(content, folder=folder)
                    return True
                except LimitStop:
                    return False
            with ThreadPoolExecutor(max_workers=8) as pool:
                outcomes = list(pool.map(call, range(8)))
            self.assertEqual(sum(outcomes), 2)
            self.assertEqual(provider.calls, 2)

    def test_auth_not_in_body_and_cap_includes_failures(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            profile = dict(base_url="https://example.invalid/v1", model="fake/model", api_key_env="FAKE_KEY",
                           max_tokens=32, temperature=0, response_format="json_schema")
            bodies = []
            def transport(body):
                bodies.append(body)
                return dict(model="provider/model-version", usage={"prompt_tokens": 1},
                            choices=[dict(finish_reason="length", message=dict(content="{}"))])
            provider = HostedProvider(profile, root / "ledger.jsonl", max_calls=1, transport=transport)
            content = dict(instructions="Return JSON", text="input", images=[b"synthetic"],
                           schema={"type": "object"}, schema_name="test")
            with self.assertRaises(ProviderStop):
                provider.respond(content, folder=root)
            with self.assertRaises(LimitStop):
                provider.respond(content, folder=root)
            self.assertEqual(len(bodies), 1)
            self.assertNotIn("Authorization", str(bodies))
            self.assertEqual(provider.stats()["failed_calls"], 1)
            self.assertEqual(provider.stats()["usage"], [{"prompt_tokens": 1}])


class RoundIntegrationTests(unittest.TestCase):
    def test_round_controller_writes_replayable_skill_snapshots(self):
        import numpy as np
        from src.cluster.manager import ClusterManager
        from src.pipeline.curation_controller_v2 import RoundsController

        class Provider:
            def respond(self, content, *, folder):
                kind = "single" if "waveform" in content["schema"]["properties"] else "pair"
                allowed = content["schema"]["properties"]["action"]["enum"]
                return answer(kind, allowed, "KEEP" if kind == "single" else "NOT_MERGE")

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            rng = np.random.default_rng(0)
            over = np.repeat([1, 2, 3], 40)
            labels = np.where(over == 2, 1, over)
            waves = -np.sin(np.linspace(0, np.pi, 30))[None, :] + rng.normal(0, .01, (120, 30))
            manager = ClusterManager(labels, over, np.array([[1.], [2.], [.9], [0.]]),
                                     np.sort(rng.uniform(0, 100, 120)), waves)
            decider = dec.SkillDecider(Provider())
            controller = RoundsController(manager, "cM2-e004_004-006_CH31", root / "run", decider=decider,
                                          sampling_rate=30000., manifest=decider.describe())
            controller.run()
            self.assertEqual(controller.status, "completed")
            template = opt.review_template(root / "run")
            self.assertEqual(len(template["reviews"]), controller.calls)
            counts = set()
            for step in (root / "run").glob("step_*"):
                _, request, policy, _ = opt.inspect_step(step)
                counts.add(len(request["images"]))
                self.assertEqual(policy["skill_sha256"], decider.skill_sha256)
            self.assertEqual(counts, {3, 4, 5})


if __name__ == "__main__":
    unittest.main()
