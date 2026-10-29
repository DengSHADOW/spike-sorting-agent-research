"""Offline engineering demo: real GEPA, synthetic feedback and scripted models. No API calls.

python scripts/demo/demo_ace_gepa.py --output output/ace_gepa_demo_001
Synthetic improvements here test wiring only; they are not scientific evidence of learning.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.agent import curation_decider_v2 as dec
from src.agent import feedback_optimization as replay
from src.agent.ace_gepa import run_ace_gepa
from src.agent.feedback_session import FeedbackSession
from src.pipeline.curation_engineering_v1 import write_json


def model_answer(kind, allowed, action):
    fields = {name: dict(label=labels[0], note="SYNTHETIC scripted observation")
              for name, _, labels, _ in dec.fields_for(kind, allowed)}
    return dict(fields, action=action, rationale="SYNTHETIC model, no inference")


class DemoModel:
    def __init__(self):
        self.requests = []

    def describe(self):
        return dict(kind="SYNTHETIC_SCRIPTED_NO_NETWORK")

    def respond(self, content, *, folder):
        self.requests.append(deepcopy(content))
        stage = content["schema_name"]
        if stage == "ace_reflect":
            payload = json.loads(content["text"])
            return dict(insights=[dict(case_keys=[c["case_key"] for c in payload["cases"]],
                diagnosis="Synthetic wrong merge", suggested_change="Add demo rule", exceptions="Synthetic only")])
        if stage == "ace_curate":
            payload = json.loads(content["text"])
            keys = sorted({k for insight in payload["reflections"] for k in insight["case_keys"]})
            return dict(suggestions=[dict(id="demo", operation="replace", target_ids=["P1"], scope="pair",
                text="DEMO_LEARNED_RULE", conditions="Synthetic fixture only", exceptions="Never a domain claim",
                evidence_keys=keys, reason="Synthetic feedback")])
        if stage == "gepa_skill_plan":
            return dict(edits=[dict(suggestion_id="demo", wording="DEMO_LEARNED_RULE")])
        kind = "single" if "waveform" in content["schema"]["properties"] else "pair"
        allowed = content["schema"]["properties"]["action"]["enum"]
        action = "KEEP" if kind == "single" else (
            "NOT_MERGE" if "DEMO_LEARNED_RULE" in content["instructions"] else "MERGE")
        return model_answer(kind, allowed, action)


def demo_bundle(root, name, role, *, count=2):
    """Tiny valid PNGs stand in for observations; all labels and outcomes are explicitly synthetic."""
    root = Path(root)
    run = root / f"{name}_run"
    run.mkdir(parents=True)
    write_json(run / "manifest.json", dict(dataset_id="synthetic-" + name, input_sha256="synthetic-input"))
    skill, skill_hash = dec.load_skill()
    contract = dec.CONTRACT.read_text()
    allowed = ["MERGE", "NOT_MERGE", "DISCARD"]
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1cAAAAASUVORK5CYII=")
    for i in range(count):
        step = run / f"step_{i + 1:05d}"
        step.mkdir()
        write_json(step / "question.json", dict(kind="pair", allowed=allowed, cid=1, target=2,
                                                state_sha256=f"synthetic-{name}-{i}", round=1))
        images = []
        for image_name in ("overlay", "ccg_acg", "amplitude_time"):
            (step / f"{image_name}.png").write_bytes(png)
            images.append(dict(name=image_name, sha256=hashlib.sha256(png).hexdigest()))
        meta = dict(skill_sha256=skill_hash, contract_sha256=hashlib.sha256(contract.encode()).hexdigest())
        write_json(step / "request_content.json", dict(
            instructions=dec.instructions_v2(contract, skill, "pair", allowed), preface="SYNTHETIC pair",
            text='{"measurements": {"correlation": 0.5}}', images=images, images_first=True,
            schema=dec.schema_v2("pair", allowed), schema_name="curation_pair_observe_then_act", **meta))
        write_json(step / "policy_snapshot.json", dict(contract=contract, skill=skill, **meta))
        write_json(step / "answer.json", model_answer("pair", allowed, "MERGE"))
    feedback = replay.review_template(run)
    feedback.update(role=role, synthetic=True)
    for row in feedback["reviews"]:
        row.update(expert_action="NOT_MERGE", expert_reason="SYNTHETIC_LABEL_DO_NOT_LEAK",
                   reviewer="synthetic-fixture", evidence_sufficient=True)
    reviews = root / f"{name}_reviews.json"
    write_json(reviews, feedback)
    bundle = root / f"{name}_bundle"
    replay.build_bundle(run, reviews, bundle)
    return bundle


def demo_optimizer(base, learning, validation, output, version, config):
    return run_ace_gepa(base, learning, validation, DemoModel(), DemoModel(), DemoModel(), output,
                        version=version, max_proposals=2, max_evaluations=40)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True)
    args = p.parse_args(argv)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    learning = demo_bundle(out, "learning", "learning")
    validation = demo_bundle(out, "validation", "validation")
    session = FeedbackSession.create(out / "session", dec.SKILL_V1, [validation], feedback_batch_size=2)
    session.import_bundle(learning)
    duplicate = session.import_bundle(learning)
    result = session.advance(demo_optimizer)
    write_json(out / "DEMO_RESULT.json", dict(synthetic=True, no_network=True, duplicate_added=duplicate["added"],
                                             result=result, active_skill=str(session.active())))
    print(json.dumps(readable := dict(synthetic=True, duplicate_added=duplicate["added"], **result), indent=2))
    return 0 if readable["status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
