"""Dataset-independent skill feedback loop. Controller and observation contracts stay frozen.

First implementation: explicit normalized reviews, immutable replay bundles, bounded reflective
rule proposals, paired evaluation and accepted/rejected version artifacts. Not an ACE/GEPA
reproduction. Exact-state replay measures judgement agreement, not autonomous rollout quality.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from src.agent.curation_decider_v2 import check_answer, instructions_v2, schema_v2
from src.agent.skill_policy import load_skill, validate_skill
from src.pipeline.curation_engineering_v1 import write_json

REVIEW_SCHEMA = "skill-review-v1"
BUNDLE_SCHEMA = "skill-replay-v1"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def image_paths(step, request):
    out = []
    for image in request["images"]:
        name = image["name"]
        if not isinstance(name, str) or not name or Path(name).name != name:
            raise ValueError("Invalid image name")
        path = step / (name + ".png")
        if sha(path) != image["sha256"]:
            raise ValueError("Image hash mismatch")
        out.append(path)
    return out


def inspect_step(step):
    question, request, policy = (read(step / name) for name in
                               ("question.json", "request_content.json", "policy_snapshot.json"))
    allowed, kind = question["allowed"], question["kind"]
    validate_skill(policy["skill"])
    if (policy["skill_sha256"] != request["skill_sha256"]
            or hashlib.sha256(policy["contract"].encode()).hexdigest() != request["contract_sha256"]
            or request["instructions"] != instructions_v2(policy["contract"], policy["skill"], kind, allowed)
            or request["schema"] != schema_v2(kind, allowed)):
        raise ValueError("Policy snapshot does not match the actual request")
    answer = check_answer(read(step / "answer.json"), kind, allowed)
    image_paths(step, request)
    return question, request, policy, answer


def review_template(run):
    run = Path(run)
    manifest = read(run / "manifest.json")
    rows = []
    for step in sorted(run.glob("step_*")):
        if not (step / "answer.json").exists():
            continue
        q, request, _, answer = inspect_step(step)
        rows.append(dict(feedback_id=step.name, revision=1, step=step.name,
                         state_sha256=q["state_sha256"], request_sha256=sha(step / "request_content.json"),
                         policy_snapshot_sha256=sha(step / "policy_snapshot.json"),
                         kind=q["kind"], source=q["cid"], target=q.get("target"), allowed=q["allowed"],
                         agent_action=answer["action"], expert_action=None, expert_reason="",
                         reviewer="", evidence_sufficient=None, disputed=False))
    return dict(schema=REVIEW_SCHEMA, dataset_id=manifest["dataset_id"],
                input_sha256=manifest["input_sha256"], role="learning", synthetic=False, reviews=rows)


def build_bundle(run, feedback_path, output):
    """Bind reviews to exact saved questions, policy and image bytes before copying anything."""
    run, output = Path(run), Path(output)
    feedback = read(feedback_path)
    manifest = read(run / "manifest.json")
    if (feedback.get("schema") != REVIEW_SCHEMA or feedback.get("role") not in ("learning", "validation")
            or type(feedback.get("synthetic")) is not bool
            or any(feedback.get(k) != manifest[k] for k in ("dataset_id", "input_sha256"))):
        raise ValueError("Feedback schema, role, dataset or input hash mismatch")
    rows, seen, states = [], set(), set()
    for review in feedback["reviews"]:
        step_name = review["step"]
        if not isinstance(step_name, str) or Path(step_name).name != step_name or not step_name.startswith("step_"):
            raise ValueError("Invalid step path")
        step = run / step_name
        q, request, _, answer = inspect_step(step)
        expected = dict(state_sha256=q["state_sha256"], request_sha256=sha(step / "request_content.json"),
                        policy_snapshot_sha256=sha(step / "policy_snapshot.json"), kind=q["kind"],
                        source=q["cid"], target=q.get("target"), agent_action=answer["action"])
        if any(review.get(k) != v for k, v in expected.items()):
            raise ValueError("Review does not match this decision state, target or request")
        if (review.get("expert_action") not in q["allowed"] or review.get("evidence_sufficient") is not True
                or review.get("disputed") is not False or not review.get("reviewer")
                or not isinstance(review.get("expert_reason"), str)
                or type(review.get("revision")) is not int or review["revision"] < 1):
            raise ValueError("Review needs an offered expert action, reviewer and sufficient undisputed evidence")
        if not isinstance(review.get("feedback_id"), str) or not review["feedback_id"].strip():
            raise ValueError("Missing feedback ID")
        identity = (q["state_sha256"], q["kind"], q["cid"], q.get("target"))
        if review["feedback_id"] in seen or identity in states:
            raise ValueError("Duplicate review or decision; select one resolved revision")
        seen.add(review["feedback_id"])
        states.add(identity)
        files = [step / n for n in ("question.json", "request_content.json", "policy_snapshot.json", "answer.json")]
        files += image_paths(step, request)
        rows.append(dict(review=deepcopy(review), files={p.name: sha(p) for p in files}))
    if not rows:
        raise ValueError("No eligible reviews; explicitly select reviewed steps")
    output.mkdir(parents=True, exist_ok=False)
    for row in rows:
        source, destination = run / row["review"]["step"], output / row["review"]["step"]
        destination.mkdir()
        for name in row["files"]:
            shutil.copyfile(source / name, destination / name)
    bundle = dict(schema=BUNDLE_SCHEMA, role=feedback["role"], dataset_id=feedback["dataset_id"],
                  input_sha256=feedback["input_sha256"], synthetic=feedback["synthetic"],
                  source_review_sha256=sha(feedback_path), run_manifest=manifest, cases=rows)
    write_json(output / "bundle.json", bundle)
    return bundle


def load_bundle(folder):
    folder = Path(folder)
    bundle = read(folder / "bundle.json")
    if (bundle.get("schema") != BUNDLE_SCHEMA or bundle.get("role") not in ("learning", "validation")
            or type(bundle.get("synthetic")) is not bool or not isinstance(bundle.get("cases"), list)
            or not bundle["cases"]):
        raise ValueError("Unsupported replay bundle")
    seen = set()
    for row in bundle["cases"]:
        step = folder / row["review"]["step"]
        if step.parent != folder or not row["files"]:
            raise ValueError("Invalid bundle paths")
        for name, expected in row["files"].items():
            if Path(name).name != name or sha(step / name) != expected:
                raise ValueError("Replay evidence was modified")
        q, request, _, answer = inspect_step(step)
        required = {"question.json", "request_content.json", "policy_snapshot.json", "answer.json"}
        required.update(p.name for p in image_paths(step, request))
        if set(row["files"]) != required:
            raise ValueError("Bundle evidence file inventory is incomplete")
        review = row["review"]
        if (review.get("state_sha256") != q["state_sha256"] or review.get("kind") != q["kind"]
                or review.get("source") != q["cid"] or review.get("target") != q.get("target")
                or review.get("request_sha256") != sha(step / "request_content.json")
                or review.get("policy_snapshot_sha256") != sha(step / "policy_snapshot.json")
                or review.get("agent_action") != answer["action"]
                or review.get("expert_action") not in q["allowed"]
                or review.get("evidence_sufficient") is not True or review.get("disputed") is not False
                or not isinstance(review.get("expert_reason"), str) or not review.get("reviewer")
                or type(review.get("revision")) is not int or review["revision"] < 1
                or not isinstance(review.get("feedback_id"), str) or not review["feedback_id"].strip()):
            raise ValueError("Bundle review is not bound to its evidence")
        identity = (q["state_sha256"], q["kind"], q["cid"], q.get("target"))
        if identity in seen:
            raise ValueError("Duplicate decision in bundle")
        seen.add(identity)
    return bundle


def replay_case(folder, row, skill, provider, output):
    step = Path(folder) / row["review"]["step"]
    q, request, policy, _ = inspect_step(step)
    # Deliberately allowlist model inputs: neither feedback nor the previous answer is exposed.
    content = {k: request[k] for k in ("text", "preface", "schema", "schema_name", "images_first")}
    content["images"] = [p.read_bytes() for p in image_paths(step, request)]
    content["instructions"] = instructions_v2(policy["contract"], skill, q["kind"], q["allowed"])
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "evaluation_context.json", dict(skill=skill, feedback_id=row["review"]["feedback_id"]))
    answer = check_answer(provider.respond(content, folder=output), q["kind"], q["allowed"])
    write_json(output / "answer.json", answer)
    expected = row["review"]["expert_action"]
    return dict(feedback_id=row["review"]["feedback_id"], action=answer["action"], expected=expected, answer=answer,
                correct=answer["action"] == expected,
                false_discard=answer["action"] == "DISCARD" and expected != "DISCARD")


def propose_skill(base, bundle_path, provider, output, version):
    bundle = load_bundle(bundle_path)
    if bundle["role"] != "learning" or len(bundle["cases"]) > 32:
        raise ValueError("Proposer accepts only 1–32 learning cases; select a bounded batch")
    examples = []
    images = []
    for row in bundle["cases"]:
        step = Path(bundle_path) / row["review"]["step"]
        q, request, _, answer = inspect_step(step)
        paths = image_paths(step, request)
        start = len(images) + 1
        images.extend(p.read_bytes() for p in paths)
        examples.append(dict(question=q, measurements=json.loads(request["text"]), agent_answer=answer,
                             feedback=row["review"], image_indices=list(range(start, len(images) + 1))))
    rule_schema = dict(type="object", properties={k: {"type": "string"} for k in ("id", "scope", "text")},
                       required=["id", "scope", "text"], additionalProperties=False)
    rule_schema["properties"]["scope"]["enum"] = ["single", "pair", "both"]
    schema = dict(type="object", properties=dict(rules=dict(type="array", items=rule_schema),
                                                explanation={"type": "string"}),
                  required=["rules", "explanation"], additionalProperties=False)
    content = dict(instructions="Propose a small evidence-grounded revision of the domain skill. Return JSON with "
                   "the complete rules and explanation. Preserve useful rules and exceptions. Only judgement guidance "
                   "may change: do not change workflow, actions, schemas, metrics or evidence definitions. Do not "
                   "memorize dataset IDs or cluster numbers. A single correction need not imply a universal rule.",
                   text=json.dumps(dict(base_skill=base, learning_cases=examples)), images=images,
                   schema=schema, schema_name="skill_revision", images_first=False)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    result = provider.respond(content, folder=output)
    if not isinstance(result, dict) or set(result) != {"rules", "explanation"} or not isinstance(result["explanation"], str):
        raise ValueError("Invalid skill proposal")
    skill = validate_skill(dict(version=version, parent=base["version"], status="candidate",
                                rules=result["rules"], explanation=result["explanation"]))
    write_json(output / "proposal.json", skill)
    return skill


def optimize_once(base_path, learning, validation, provider, output, *, candidate_path=None,
                  proposer=None, version="feedback_v1", min_gain=0.0, max_regressions=0):
    """Fresh output per attempt, no hot activation; any exception produces a failed artifact."""
    if not isinstance(min_gain, (int, float)) or not 0 <= min_gain <= 1:
        raise ValueError("Invalid minimum gain")
    if type(max_regressions) is not int or max_regressions < 0:
        raise ValueError("Invalid regression allowance")
    base, base_sha = load_skill(base_path)
    train, val = load_bundle(learning), load_bundle(validation)
    if train["role"] != "learning" or val["role"] != "validation" or not train["cases"] or not val["cases"]:
        raise ValueError("Separate nonempty learning and selection-validation bundles required")
    def identities(bundle):
        return {(bundle["input_sha256"], r["review"]["state_sha256"], r["review"]["kind"],
                 r["review"]["source"], r["review"]["target"]) for r in bundle["cases"]}
    if identities(train) & identities(val):
        raise ValueError("Learning and validation contain overlapping decision states")
    if (candidate_path is None) == (proposer is None):
        raise ValueError("Provide exactly one candidate file or proposer")
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    manifest = dict(schema="skill-optimization-v1", method="provided-candidate" if candidate_path else
                    "bounded-reflective-skill-v1 (not ACE/GEPA)", base_sha256=base_sha,
                    learning_bundle_sha256=sha(Path(learning) / "bundle.json"),
                    validation_bundle_sha256=sha(Path(validation) / "bundle.json"),
                    min_gain=min_gain, max_regressions=max_regressions,
                    synthetic=train["synthetic"] or val["synthetic"],
                    same_input=train["input_sha256"] == val["input_sha256"],
                    evaluator=getattr(provider, "describe", lambda: {"kind": type(provider).__name__})(),
                    proposer=getattr(proposer, "describe", lambda: {"kind": type(proposer).__name__})(),
                    feedback_sources={"learning": [r["review"] for r in train["cases"]],
                                      "validation": [r["review"] for r in val["cases"]]},
                    code_hashes={str(Path(p).name): sha(p) for p in
                        (__file__, Path(__file__).with_name("curation_decider_v2.py"),
                         Path(__file__).with_name("skill_policy.py"))},
                    scope="judgement replay only; workflow unchanged; validation is selection, not final test")
    write_json(out / "manifest.json", manifest)
    write_json(out / "base_skill.json", base)
    try:
        candidate = (load_skill(candidate_path)[0] if candidate_path else
                     propose_skill(base, learning, proposer, out / "proposal", version))
        if candidate["version"] == base["version"] or candidate.get("parent") != base["version"]:
            raise ValueError("Candidate needs a new version and the expected parent")
        write_json(out / "candidate_skill.json", candidate)
        evaluations = {}
        for label, folder, bundle in (("learning", learning, train), ("validation", validation, val)):
            evaluations[label] = {}
            for policy, skill in (("base", base), ("candidate", candidate)):
                evaluations[label][policy] = [replay_case(folder, row, skill, provider,
                    out / label / policy / f"case_{i:04d}") for i, row in enumerate(bundle["cases"])]
        b, c = evaluations["validation"]["base"], evaluations["validation"]["candidate"]
        gain = (sum(r["correct"] for r in c) - sum(r["correct"] for r in b)) / len(b)
        regressions = sum(x["correct"] and not y["correct"] for x, y in zip(b, c))
        new_false_discards = sum(y["false_discard"] and not x["false_discard"] for x, y in zip(b, c))
        learning_delta = sum(r["correct"] for r in evaluations["learning"]["candidate"]) - sum(
                            r["correct"] for r in evaluations["learning"]["base"])
        learning_false_discards = sum(y["false_discard"] and not x["false_discard"] for x, y in zip(
            evaluations["learning"]["base"], evaluations["learning"]["candidate"]))
        accepted = (gain > min_gain and regressions <= max_regressions and not new_false_discards
                    and not learning_false_discards and learning_delta >= 0)
        # Evidence and base must still be exactly what was frozen before model calls.
        for folder, expected in ((learning, manifest["learning_bundle_sha256"]),
                                 (validation, manifest["validation_bundle_sha256"])):
            if sha(Path(folder) / "bundle.json") != expected:
                raise ValueError("Bundle changed during evaluation")
            load_bundle(folder)
        if sha(base_path) != base_sha:
            raise ValueError("Base skill changed during evaluation")
        result = dict(status="accepted" if accepted else "rejected", validation_gain=gain,
                      regressions=regressions, new_false_discards=new_false_discards,
                      learning_new_false_discards=learning_false_discards,
                      learning_delta=learning_delta, evaluations=evaluations,
                      activation="explicit --skill in a subsequent run only")
    except Exception as exc:
        write_json(out / "result.json", dict(status="failed", error_type=type(exc).__name__, error=str(exc)))
        raise
    write_json(out / "result.json", result)
    if accepted:
        write_json(out / "accepted_skill.json", candidate)
    return result
