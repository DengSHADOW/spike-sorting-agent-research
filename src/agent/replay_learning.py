"""Batched feedback learning on a strict expert replay (positive and negative examples).

N counts eligible feedback, not errors. Each example is inspected with its original images;
short evidence records are jointly curated into one candidate. The candidate re-answers the batch and
must answer strictly more of it correctly than the recorded answers (all given by the active skill,
since skills change only at attempts). With protect_correct, any loss of a recorded correct answer
also vetoes it. The K earlier questions are an optional separate regression guard (K=0 disables it).
No future questions or test data are used. This is an internal learner, not a GEPA search run.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from time import monotonic, time

from src.agent import curation_decider_v2 as dec
from src.pipeline.curation_engineering_v1 import write_json

VERSION = "replay-online-learning-v3-batched"
MAX_EDITS, MIN_RULE_CHARS, MAX_RULE_CHARS, EXTRA_RULE_CHARS = 3, 40, 700, 3000
PLACEHOLDER = re.compile(r"^\W*(\.{3}|…|same|unchanged|no change|as above|tbd|todo)\W*$", re.I)
FORBIDDEN = re.compile(r"\bcluster\s*#?\s*\d+|\bCH\s*\d+|\bcM2|\bstep\s*\d+|\bsource\s+\d+|\btarget\s+\d+", re.I)
EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {"type": "string"},
        "edits": {"type": "array", "items": {
            "type": "object",
            "properties": {"op": {"type": "string", "enum": ["add", "replace", "remove"]},
                           "rule_id": {"type": "string"},
                           "scope": {"type": "string", "enum": ["single", "pair", "both"]},
                           "text": {"type": "string"}},
            "required": ["op", "rule_id", "scope", "text"], "additionalProperties": False}},
    },
    "required": ["diagnosis", "edits"], "additionalProperties": False,
}
EVIDENCE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {k: {"type": "string"} for k in
                   ("observations", "successful_guidance", "failure_analysis", "uncertainty")},
    "required": ["observations", "successful_guidance", "failure_analysis", "uncertainty"],
}
EVIDENCE_INSTRUCTIONS = """Inspect one spike-curation feedback case using its original images and measurements.
Describe concrete evidence distinguishing the expert action from alternatives. For a correct answer, explain
what guidance should be preserved; for an error, diagnose it without assuming every expert label is visually
recoverable. Distinguish model errors from frozen-gate effects. A derived label is not a recorded expert edit;
an empty expert rationale means no rationale was supplied. Never invent expert reasons. Do not edit rules.
Return the four JSON fields, at most two concise sentences per field, at most 2400 characters total."""
PROPOSER_INSTRUCTIONS = """Maintain the domain skill of a spike-curation agent from a BATCH of feedback.
All supplied cases have already been answered and revealed. Consider every case together, including correct
answers as positive evidence and counterexamples to overgeneralization. Each case has measurements, the
original answer, expert action/provenance, and an image-grounded evidence record. Evidence summaries may be
uncertain: do not treat them as additional expert annotations. Gates, contract and schema are frozen.

Propose at most three rule edits (add/replace/remove), or an empty list when no justified improvement exists.
Repair errors while preserving successful decisions. Do not simply favour the majority action. Compare
KEEP and DISCARD evidence when both are present; acknowledge missing contrasting evidence when absent.
Prefer refining existing rules over adding redundant ones. Remove a rule only with supporting evidence.
Rules must generalize: no cluster/channel/recording/step identifiers or memorized answers.
For add, rule_id is empty. For remove, text may be empty. Write replacement/addition text in full.
Do not modify gates or output fields. Return the JSON object; diagnosis at most three sentences."""


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rule_chars(skill):
    return sum(len(r["text"]) for r in skill["rules"])


def apply_edits(skill, edits, version):
    """New candidate skill; raises ValueError on an edit that does not fit the skill."""
    s = deepcopy(skill)
    by_id = {r["id"]: r for r in s["rules"]}
    for e in edits:
        text = e["text"].strip()
        if e["op"] == "add":
            n = 1 + max([int(i[1:]) for i in by_id if re.fullmatch(r"L\d+", i)] or [0])
            rule = dict(id=f"L{n}", scope=e["scope"], sources=["simulated_expert_feedback"], added_in=version,
                        changed_in=None, text=text)
            s["rules"].append(rule)
            by_id[rule["id"]] = rule
        elif e["rule_id"] not in by_id:
            raise ValueError(f"Unknown rule {e['rule_id']!r}")
        elif e["op"] == "replace":
            by_id[e["rule_id"]].update(scope=e["scope"], text=text, changed_in=version)
        else:
            s["rules"].remove(by_id.pop(e["rule_id"]))
    s.update(version=version, parent=skill["version"], status="candidate")
    return s


def static_check(base, proposal, budget):
    if not isinstance(proposal, dict) or set(proposal) != {"diagnosis", "edits"}:
        return "malformed_proposal"
    if not isinstance(proposal["diagnosis"], str) or not isinstance(proposal["edits"], list):
        return "malformed_proposal"
    for edit in proposal["edits"]:
        if (not isinstance(edit, dict) or set(edit) != {"op", "rule_id", "scope", "text"}
                or not all(isinstance(v, str) for v in edit.values())
                or edit["op"] not in ("add", "replace", "remove")
                or edit["scope"] not in ("single", "pair", "both")
                or (edit["op"] == "add" and edit["rule_id"])):
            return "malformed_proposal"
    edits = proposal["edits"]
    if not edits:
        return "no_edit_proposed"
    if len(edits) > MAX_EDITS:
        return "too_many_edits"
    current = {r["id"]: r["text"] for r in base["rules"]}
    for e in edits:
        text = e["text"].strip()
        if e["op"] != "remove":
            if PLACEHOLDER.match(text) or len(text) < MIN_RULE_CHARS:
                return "placeholder_or_too_short_rule"
            # An existing rule may already exceed the cap for new rules (skill_v4 S4 has 866 characters).
            limit = max(MAX_RULE_CHARS, int(1.25 * len(current.get(e["rule_id"], "")))) if e["op"] == "replace" \
                else MAX_RULE_CHARS
            if len(text) > limit:
                return "oversized_rule"
            if e["op"] == "replace" and text == current.get(e["rule_id"], "").strip():
                return "replace_without_change"
        if FORBIDDEN.search(e["text"]):
            return "recording_specific_text"
    try:
        candidate = apply_edits(base, edits, "check")
    except ValueError as exc:
        return f"invalid_edit: {exc}"
    if rule_chars(candidate) > budget:
        return "skill_over_budget"
    return None


def model_answer(step):
    """The model's own answer (before gates) from the provider's saved raw response; None if unreadable."""
    for name in ("response_2.json", "response_1.json"):
        if (step / name).exists():
            try:
                return json.loads(read(step / name)["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError, ValueError):
                return None
    return None


def step_content(step, skill, contract):
    """The exact request of a saved step, with the instructions rebuilt for another skill."""
    req, q = read(step / "request_content.json"), read(step / "question.json")
    return dict(instructions=dec.instructions_v2(contract, skill, q["kind"], q["allowed"]), preface=req["preface"],
                text=req["text"], images=[(step / f"{i['name']}.png").read_bytes() for i in req["images"]],
                images_first=req["images_first"], schema=req["schema"], schema_name=req["schema_name"]), q


class OnlineLearner:
    """Collect all reviewed answers, propose jointly every N, activate only after a replay round."""

    def __init__(self, folder, *, proposer, evaluator, k=3, sources="all", max_feedback=None,
                 numbers="default", batch_size=1, feedback_scope="all", protect_correct=False):
        if type(k) is not int or k < 0 or sources not in ("recorded", "all"):
            raise ValueError("k must be non-negative; sources recorded or all")
        if type(batch_size) is not int or batch_size < 1 or feedback_scope not in ("all", "mismatch"):
            raise ValueError("batch_size must be positive; feedback_scope all or mismatch")
        if max_feedback is not None and (type(max_feedback) is not int or max_feedback < 1):
            raise ValueError("max_feedback is a positive cap on optimisation batches")
        self.out, self.proposer, self.evaluator = Path(folder), proposer, evaluator
        self.k, self.sources, self.max_feedback, self.numbers = k, sources, max_feedback, numbers
        self.batch_size, self.feedback_scope, self.protect_correct = batch_size, feedback_scope, bool(protect_correct)
        self.out.mkdir(parents=True, exist_ok=False)
        (self.out / "skills").mkdir()
        self.attempts, self.budget = [], None
        self.seen, self.pending, self.observed = set(), [], []
        self.feedback_count = self.correct_feedback_count = 0

    def describe(self):
        return dict(version=VERSION, k=self.k, sources=self.sources, max_feedback=self.max_feedback,
                    batch_size=self.batch_size, feedback_scope=self.feedback_scope,
                    protect_correct=self.protect_correct,
                    max_edits=MAX_EDITS, min_rule_chars=MIN_RULE_CHARS, max_rule_chars=MAX_RULE_CHARS,
                    extra_rule_chars=EXTRA_RULE_CHARS, proposal_mode="per_case_images_then_joint_curation",
                    filters=["static", "batch_strict_gain" + ("_no_correct_losses" if self.protect_correct else ""),
                             *([f"regression_last_{self.k}"] if self.k else [])],
                    all_correct_batch="retain_skill_without_model_calls")

    def initialize(self, replay):
        if self.budget is None:
            self.budget = rule_chars(replay.decider.skill) + EXTRA_RULE_CHARS
            self.contract_path = Path(getattr(replay.decider, "contract_path", dec.CONTRACT))
            self.contract = self.contract_path.read_text(encoding="utf-8")
            if hashlib.sha256(self.contract.encode()).hexdigest() != replay.decider.contract_sha256:
                raise ValueError("Replay contract does not match its snapshot")
            self.replay_out = replay.out.resolve()
        if replay.out.resolve() != self.replay_out:
            raise ValueError("Use a fresh learner per dataset; pass the final skill to the next dataset")

    def labeled_steps(self, replay):
        rows = []
        for step in sorted(replay.out.glob("step_*")):
            if (step / "simulation.json").exists():
                s = read(step / "simulation.json")
                if s["match"] is not None and read(step / "question.json")["round"] <= replay.round:
                    rows.append((step, s))
        return rows

    def __call__(self, replay):
        self.initialize(replay)
        for step, s in self.labeled_steps(replay):
            if step.name in self.seen:
                continue
            self.seen.add(step.name)
            self.observed.append((step, s))
            if ((self.sources == "recorded" and s["provenance"] != "expert_recorded")
                    or (self.feedback_scope == "mismatch" and s["match"])):
                continue
            self.pending.append((step, s))
            self.feedback_count += 1
            self.correct_feedback_count += int(s["match"])
        self.drain(replay, flush=False)

    def drain(self, replay, *, flush):
        while self.pending and (len(self.pending) >= self.batch_size or flush):
            if self.max_feedback is not None and len(self.attempts) >= self.max_feedback:
                break
            batch = self.pending[:self.batch_size]
            first = next(i for i, (step, _) in enumerate(self.observed) if step == batch[0][0])
            history = self.observed[max(0, first - self.k):first]
            self.attempt(replay, batch, history, partial=len(batch) < self.batch_size)
            del self.pending[:len(batch)]
        self.save_state(replay)

    def finish(self, replay, *, flush=True):
        """Explicit dataset-end flush. Never learn from a failed or stopped replay."""
        self.initialize(replay)
        if replay.status == "completed":
            self.drain(replay, flush=flush)
        else:
            self.save_state(replay)

    def save_state(self, replay):
        state = dict(self.summary(), dataset_id=replay.dataset_id,
                   observed_steps=[s.name for s, _ in self.observed], pending_steps=[s.name for s, _ in self.pending],
                   active_skill_version=replay.decider.skill["version"],
                   active_skill_sha256=replay.decider.skill_sha256)
        temporary = self.out / "feedback_state.tmp"
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.out / "feedback_state.json")

    def reask(self, step, skill, folder):
        content, q = step_content(step, skill, self.contract)
        folder.mkdir(parents=True)
        return self.decider.answer(content, q["allowed"], folder=folder)

    def proposal(self, batch, base, folder, timings):
        started = monotonic()
        examples = []
        for i, (step, s) in enumerate(batch):
            content, q = step_content(step, base, self.contract)
            example = dict(case_id=f"case_{i + 1}", question=q, measurements=json.loads(content["text"]),
                           was_correct=s["match"], provenance=s["provenance"],
                           agent=dict(final_action=s["agent_action"], model_action=s.get("model_action"),
                                      gate=s.get("gate"), answer=model_answer(step)),
                           expert=dict(action=s["expert_action"], rationale=s["reason"]),
                           original_skill_version=s.get("skill_version"))
            stage = folder / "evidence" / step.name
            stage.mkdir(parents=True)
            payload = dict(skill_rules=base["rules"], gates=base.get("gates", []), case=example)
            write_json(stage / "input.json", dict(payload=payload,
                       image_sha256=[hashlib.sha256(im).hexdigest() for im in content["images"]]))
            evidence = self.proposer.respond(dict(instructions=EVIDENCE_INSTRUCTIONS,
                preface=content["preface"], text=json.dumps(payload, allow_nan=False), images=content["images"],
                images_first=True, schema=EVIDENCE_SCHEMA, schema_name="feedback_evidence"), folder=stage)
            if (not isinstance(evidence, dict) or set(evidence) != set(EVIDENCE_SCHEMA["required"])
                    or any(not isinstance(v, str) for v in evidence.values())
                    or sum(len(v) for v in evidence.values()) > 2400):
                raise ValueError("Malformed or oversized evidence summary")
            write_json(stage / "evidence.json", evidence)
            examples.append(dict(example, evidence=evidence))
        timings["evidence_seconds"] = monotonic() - started
        stage = folder / "proposal"
        stage.mkdir()
        payload = dict(skill_rules=base["rules"], gates=base.get("gates", []), cases=examples)
        write_json(stage / "input.json", payload)
        started = monotonic()
        result = self.proposer.respond(dict(instructions=PROPOSER_INSTRUCTIONS,
            preface="All numbered cases belong to one feedback batch. Inspect them jointly.",
            text=json.dumps(payload, allow_nan=False), images=[], images_first=True,
            schema=EDIT_SCHEMA, schema_name="skill_edit_proposal"), folder=stage)
        timings["curation_seconds"] = monotonic() - started
        return result

    def attempt(self, replay, batch, history, *, partial):
        n = len(self.attempts) + 1
        folder = self.out / f"attempt_{n:04d}"
        folder.mkdir()
        base = replay.decider.skill
        record = dict(attempt=n, step=batch[-1][0].name, steps=[s.name for s, _ in batch], round=replay.round,
                      batch_size=len(batch), partial=partial, correct_before=sum(int(s["match"]) for _, s in batch),
                      expert_actions=[s["expert_action"] for _, s in batch],
                      provenances=[s["provenance"] for _, s in batch], base_version=base["version"],
                      base_sha256=replay.decider.skill_sha256, started_at=time())
        started = monotonic()
        outcome, candidate = None, None
        # Successful feedback is collected, but an entirely successful batch does not justify a correction.
        if all(s["match"] for _, s in batch):
            outcome = "all_correct_no_change"
        else:
            try:
                proposal = self.proposal(batch, base, folder, record)
                record["proposal"] = proposal
                outcome = static_check(base, proposal, self.budget)
            except Exception as exc:
                outcome, record["error"] = "proposer_error", repr(exc)
            record["proposal_seconds"] = monotonic() - started
        if outcome is None:
            version = f"{base['version'].split('+')[0]}+{replay.dataset_id.rsplit('_', 1)[-1]}n{self.batch_size}b{n}"
            candidate = apply_edits(base, record["proposal"]["edits"], version)
            path = folder / "candidate_skill.json"
            write_json(path, candidate)
            try:
                dec.load_skill(path)
            except ValueError as exc:
                outcome = f"upstream_skill_check: {exc}"
        if outcome is None:
            try:
                t, checks = monotonic(), []
                # Every buffered answer came from the active skill (it changes only here), with the same
                # request and seed, so the recorded answers are the parent's answers; no parent re-ask.
                if any(s.get("skill_sha256") not in (None, replay.decider.skill_sha256) for _, s in batch):
                    raise ValueError("Batch answers were not produced by the active skill")
                self.decider = dec.SkillDecider(self.evaluator, skill_path=path,
                                                contract_path=self.contract_path, numbers=self.numbers)
                for step, s in batch:
                    d = self.reask(step, candidate, folder / "batch" / step.name)
                    checks.append(dict(step=step.name, expert=s["expert_action"], recorded=s["agent_action"],
                                       was_right=s["match"], candidate=d["action"],
                                       right=d["action"] == s["expert_action"]))
                record.update(batch_checks=checks, batch_seconds=monotonic() - t,
                              correct_after=sum(c["right"] for c in checks))
                if self.protect_correct and any(c["was_right"] and not c["right"] for c in checks):
                    outcome = "batch_regression"
                elif record["correct_after"] <= record["correct_before"]:
                    outcome = "batch_no_gain"
                if outcome is None:
                    t, regressions = monotonic(), []
                    for old_step, old in history:
                        d = self.reask(old_step, candidate, folder / "regression" / old_step.name)
                        regressions.append(dict(step=old_step.name, expert=old["expert_action"],
                                                was_right=old["match"], candidate=d["action"],
                                                right=d["action"] == old["expert_action"]))
                    record.update(regression=regressions, regression_seconds=monotonic() - t)
                    if any(c["was_right"] and not c["right"] for c in regressions):
                        outcome = "regression"
            except Exception as exc:
                outcome, record["error"] = "evaluation_error", repr(exc)
        accepted = outcome is None
        record.update(outcome="accepted" if accepted else outcome, seconds=monotonic() - started, finished_at=time())
        write_json(folder / "attempt.json", record)
        self.attempts.append(record)
        replay.log("skill_attempt", attempt=n, steps=record["steps"], outcome=record["outcome"], seconds=record["seconds"])
        if accepted:
            final = dict(candidate, status="accepted")
            path = self.out / "skills" / f"{final['version']}.json"
            write_json(path, final)
            previous = replay.decider.skill["version"]
            replay.decider = dec.SkillDecider(replay.decider.provider, skill_path=path,
                                              contract_path=self.contract_path, numbers=self.numbers)
            replay.log("skill_activated", previous=previous, version=final["version"],
                       skill_sha256=replay.decider.skill_sha256, after_step=batch[-1][0].name)

    def summary(self):
        outcomes = {}
        for a in self.attempts:
            outcomes[a["outcome"]] = outcomes.get(a["outcome"], 0) + 1
        return dict(self.describe(), attempts=len(self.attempts), outcomes=outcomes,
                    feedback_count=self.feedback_count, correct_feedback_count=self.correct_feedback_count,
                    incorrect_feedback_count=self.feedback_count - self.correct_feedback_count,
                    pending_feedback=len(self.pending),
                    seconds=sum(a["seconds"] for a in self.attempts),
                    accepted=[a["attempt"] for a in self.attempts if a["outcome"] == "accepted"],
                    proposer_stats=self.proposer.stats() if hasattr(self.proposer, "stats") else None,
                    evaluator_stats=self.evaluator.stats() if hasattr(self.evaluator, "stats") else None)
