"""ACE-inspired reflection/curation followed by the pinned GEPA search engine.

The ACE stage freezes evidence-backed add/replace/remove suggestions. GEPA selects subsets and
wording of those suggestions, using instance-Pareto selection. Only the skill is editable.
This is a domain adaptation of ACE, not a reproduction of its original benchmark pipeline.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, asdict
import hashlib
import importlib.metadata
import json
from pathlib import Path

from src.agent import feedback_optimization as replay
from src.agent.skill_policy import load_skill, validate_skill
from src.pipeline.curation_engineering_v1 import write_json

GEPA_VERSION = "0.1.4"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Case:
    folder: str
    row: dict
    dataset_id: str
    input_sha256: str
    role: str
    synthetic: bool
    bundle_sha256: str

    @property
    def key(self):
        r = self.row["review"]
        return digest([self.input_sha256, r["state_sha256"], r["kind"], r["source"], r["target"]])

    def verify(self):
        if replay.sha(Path(self.folder) / "bundle.json") != self.bundle_sha256:
            raise ValueError("Feedback bundle changed after selection")
        bundle = replay.load_bundle(self.folder)
        if self.row not in bundle["cases"]:
            raise ValueError("Selected feedback revision is no longer in its bundle")


def cases_from_bundles(paths, role):
    cases = []
    for path in paths:
        bundle = replay.load_bundle(path)
        if bundle["role"] != role:
            raise ValueError(f"Expected {role} bundle")
        cases += [Case(str(Path(path).resolve()), row, bundle["dataset_id"], bundle["input_sha256"],
                       role, bundle["synthetic"], replay.sha(Path(path) / "bundle.json")) for row in bundle["cases"]]
    if not cases or len({c.key for c in cases}) != len(cases):
        raise ValueError("Empty or duplicate decision cases")
    return cases


def packet(cases):
    """Multimodal evidence for reflectors only. Execution replay never receives these labels."""
    examples, images = [], []
    for case in cases:
        case.verify()
        step = Path(case.folder) / case.row["review"]["step"]
        q, request, _, answer = replay.inspect_step(step)
        paths = replay.image_paths(step, request)
        start = len(images)
        images += [p.read_bytes() for p in paths]
        examples.append(dict(case_key=case.key, question=q, measurements=json.loads(request["text"]),
                             image_names=[p.stem for p in paths], image_indices=list(range(start + 1, len(images) + 1)),
                             agent_answer=answer, feedback=case.row["review"]))
    return examples, images


def obj(properties):
    return dict(type="object", properties=properties, required=list(properties), additionalProperties=False)


STRING = {"type": "string"}
STRINGS = {"type": "array", "items": STRING}
INSIGHT_SCHEMA = obj(dict(case_keys=STRINGS, diagnosis=STRING, suggested_change=STRING, exceptions=STRING))
SUGGESTION_SCHEMA = obj(dict(id=STRING, operation={"type": "string", "enum": ["add", "replace", "remove"]},
                             target_ids=STRINGS, scope={"type": "string", "enum": ["single", "pair", "both"]},
                             text=STRING, conditions=STRING, exceptions=STRING, evidence_keys=STRINGS, reason=STRING))
PLAN_SCHEMA = obj(dict(edits={"type": "array", "items": obj(dict(suggestion_id=STRING, wording=STRING))}))


def ask(provider, folder, stage, instructions, payload, schema, images=()):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    content = dict(instructions=instructions, text=json.dumps(payload, ensure_ascii=False), images=list(images),
                   schema=schema, schema_name=stage, preface=f"{stage}: images follow the numbered case index.",
                   images_first=True)
    write_json(folder / "input.json", dict(stage=stage, instructions=instructions, payload=payload,
                                          image_sha256=[hashlib.sha256(p).hexdigest() for p in images]))
    result = provider.respond(content, folder=folder)
    write_json(folder / "output.json", result)
    return result


def curate(base, learning, provider, output, *, chunk_size=2):
    if type(chunk_size) is not int or not 1 <= chunk_size <= 8:
        raise ValueError("ACE reflection chunk_size must be between 1 and 8")
    if any(c.role != "learning" for c in learning):
        raise ValueError("ACE can only read learning feedback")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    insights = []
    for start in range(0, len(learning), chunk_size):
        chunk = learning[start:start + chunk_size]
        examples, images = packet(chunk)
        response = ask(provider, output / f"reflect_{start:04d}", "ace_reflect",
            "Reflect on expert feedback using the actual images and measurements. Return JSON insights. "
            "Distinguish missing/incorrect guidance, misread evidence and evidence unavailable to the agent. "
            "Keep successful behaviour and counterexamples. Do not infer a universal rule from one label. "
            "Existing domain rules may already cover the issue. Workflow gaps must not become skill rules.",
            dict(base_skill=base, cases=examples), obj(dict(insights={"type": "array", "items": INSIGHT_SCHEMA})), images)
        if not isinstance(response, dict) or set(response) != {"insights"} or not isinstance(response["insights"], list):
            raise ValueError("Malformed ACE reflection")
        keys = {c.key for c in chunk}
        for insight in response["insights"]:
            if (set(insight) != set(INSIGHT_SCHEMA["properties"]) or not isinstance(insight["case_keys"], list)
                    or not insight["case_keys"] or not set(insight["case_keys"]) <= keys
                    or any(not isinstance(insight[k], str) for k in ("diagnosis", "suggested_change", "exceptions"))):
                raise ValueError("ACE reflection has invalid evidence references")
        insights += response["insights"]
    response = ask(provider, output / "curate", "ace_curate",
        "Curate incremental skill changes from the supplied reflections. Return JSON suggestions, at most 20. "
        "Deduplicate against existing rules; preserve successes and exceptions. add has no target_ids; "
        "replace supersedes one or more existing rules (also supports merging duplicates); remove retires existing rules. "
        "Every suggestion needs actual evidence_keys and conditions/exceptions. Use stable unique suggestion IDs. "
        "No dataset/cluster-specific answer rules, controller changes or new actions. Empty suggestions is valid.",
        dict(base_skill=base, reflections=insights),
        obj(dict(suggestions={"type": "array", "items": SUGGESTION_SCHEMA})))
    if not isinstance(response, dict) or set(response) != {"suggestions"}:
        raise ValueError("Malformed ACE curation")
    suggestions = response["suggestions"]
    if not isinstance(suggestions, list) or len(suggestions) > 20:
        raise ValueError("ACE suggestion count exceeds 20")
    keys, rule_ids, ids = {c.key for c in learning}, {r["id"] for r in base["rules"]}, set()
    unique = []
    signatures = set()
    for s in suggestions:
        if (not isinstance(s, dict) or set(s) != set(SUGGESTION_SCHEMA["properties"])
                or any(not isinstance(s[k], str) for k in ("id", "scope", "text", "conditions", "exceptions", "reason"))
                or not s["id"].strip() or s["id"] in ids or s["operation"] not in ("add", "replace", "remove")
                or s["scope"] not in ("single", "pair", "both")
                or not isinstance(s["evidence_keys"], list) or not s["evidence_keys"]
                or not set(s["evidence_keys"]) <= keys or not isinstance(s["target_ids"], list)
                or len(set(s["target_ids"])) != len(s["target_ids"]) or not set(s["target_ids"]) <= rule_ids
                or (s["operation"] == "add") != (not s["target_ids"])
                or (s["operation"] != "remove" and not s["text"].strip())):
            raise ValueError("ACE curation has invalid operations or provenance")
        ids.add(s["id"])
        signature = digest({k: v for k, v in s.items() if k not in ("id", "reason", "evidence_keys")})
        if signature not in signatures:
            unique.append(s)
            signatures.add(signature)
    snapshot = dict(schema="ace-suggestions-v1", base_skill_sha256=digest(base), suggestions=unique,
                    learning_revisions=[dict(case_key=c.key, revision=c.row["review"]["revision"]) for c in learning])
    write_json(output / "suggestions.json", snapshot)
    return snapshot


def materialize(base, snapshot, candidate):
    if set(candidate) != {"skill_plan"}:
        raise ValueError("Only skill_plan can be optimized")
    plan = json.loads(candidate["skill_plan"])
    if not isinstance(plan, dict) or set(plan) != {"edits"} or not isinstance(plan["edits"], list):
        raise ValueError("Malformed skill plan")
    rules = deepcopy(base["rules"])
    suggestions = {s["id"]: s for s in snapshot["suggestions"]}
    selected, touched, lineage = set(), set(), []
    for edit in plan["edits"]:
        if (not isinstance(edit, dict) or set(edit) != {"suggestion_id", "wording"}
                or not isinstance(edit["wording"], str) or edit["suggestion_id"] not in suggestions
                or edit["suggestion_id"] in selected):
            raise ValueError("Plan refers to an unknown or repeated ACE suggestion")
        s = suggestions[edit["suggestion_id"]]
        if touched & set(s["target_ids"]):
            raise ValueError("Conflicting edits to the same rule")
        selected.add(s["id"])
        touched.update(s["target_ids"])
        rules = [r for r in rules if r["id"] not in s["target_ids"]]
        if s["operation"] != "remove":
            wording = edit["wording"].strip() or s["text"]
            text = f"{wording}\nApplies when: {s['conditions']}\nExceptions: {s['exceptions']}"
            rule_id = s["target_ids"][0] if s["target_ids"] else "ACE_" + digest(s)[:12]
            if any(r["id"] == rule_id for r in rules):
                raise ValueError("Generated rule ID collision")
            rules.append(dict(id=rule_id, scope=s["scope"], text=text, evidence_keys=s["evidence_keys"]))
        lineage.append(s["id"])
    # Keep version text constant during comparison so only rule content changes.
    skill = dict(deepcopy(base), rules=rules)
    validate_skill(skill)
    return skill, lineage


class SkillAdapter:
    def __init__(self, base, snapshot, learning, evaluator, proposer, output, max_evaluations):
        self.base, self.snapshot = deepcopy(base), deepcopy(snapshot)
        self.learning = {c.key: c for c in learning}
        self.evaluator, self.proposer, self.out = evaluator, proposer, Path(output)
        self.cache, self.evaluation_count, self.proposal_count = {}, 0, 0
        self.max_evaluations, self.proposal_error = max_evaluations, None

    def evaluate(self, batch, candidate, capture_traces=False):
        from gepa.core.adapter import EvaluationBatch
        skill, _ = materialize(self.base, self.snapshot, candidate)
        outputs, traces = [], []
        new_calls = 0
        for case in batch:
            cache_key = (digest(skill), case.key)
            if cache_key not in self.cache:
                if self.evaluation_count >= self.max_evaluations:
                    raise RuntimeError("Execution evaluation budget exhausted")
                self.evaluation_count += 1
                new_calls += 1
                result = replay.replay_case(case.folder, case.row, skill, self.evaluator,
                                           self.out / "evaluations" / f"eval_{self.evaluation_count:05d}")
                result["false_merge"] = result["action"] == "MERGE" and result["expected"] != "MERGE"
                self.cache[cache_key] = result
            result = deepcopy(self.cache[cache_key])
            outputs.append(result)
            if capture_traces:
                traces.append(dict(case_key=case.key, role=case.role, result=result))
        return EvaluationBatch(outputs=outputs, scores=[float(r["correct"]) for r in outputs],
                               trajectories=traces if capture_traces else None, num_metric_calls=new_calls)

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        if components_to_update != ["skill_plan"]:
            raise ValueError("Only skill_plan reflection is supported")
        records = []
        for trace in eval_batch.trajectories or []:
            if trace["role"] != "learning" or trace["case_key"] not in self.learning:
                raise ValueError("Validation answers cannot be used for reflective proposals")
            case = self.learning[trace["case_key"]]
            records.append(dict(case_key=case.key, generated_output=trace["result"]["answer"],
                                expert_action=case.row["review"]["expert_action"],
                                expert_reason=case.row["review"]["expert_reason"]))
        return {"skill_plan": records}

    def propose_new_texts(self, candidate, reflective_dataset, components_to_update):
        # GEPA catches proposer exceptions internally. Record them and fail the outer update explicitly.
        try:
            if components_to_update != ["skill_plan"]:
                raise ValueError("Unexpected GEPA component")
            self.proposal_count += 1
            records = reflective_dataset["skill_plan"]
            examples, images = packet([self.learning[r["case_key"]] for r in records])
            result = ask(self.proposer, self.out / "proposals" / f"proposal_{self.proposal_count:04d}", "gepa_skill_plan",
                "Improve the current skill_plan using the frozen ACE suggestions and observed task errors. "
                "Return JSON edits, each with suggestion_id and wording. Choose a compatible subset; empty edits "
                "keeps the original skill. You may clarify wording but preserve the suggestion's evidence, "
                "conditions and exceptions. Only listed suggestions are allowed. Do not add case-specific "
                "answers or modify workflow, action contracts, observations or scoring.",
                dict(base_skill=self.base, frozen_ace=self.snapshot, current_plan=json.loads(candidate["skill_plan"]),
                     current_results=records, original_cases=examples), PLAN_SCHEMA, images)
            proposed = {"skill_plan": json.dumps(result, sort_keys=True, ensure_ascii=False)}
            materialize(self.base, self.snapshot, proposed)
            return proposed
        except Exception as exc:
            self.proposal_error = exc
            raise


def run_ace_gepa(base_path, learning, validation, evaluator, reflector, proposer, output, *,
                 version="learned_v1", max_proposals=4, max_evaluations=200, reflection_chunk_size=2,
                 minibatch_size=2, seed=0, min_gain=0.0, max_regressions=0):
    import gepa
    from gepa.utils import MaxCandidateProposalsStopper

    if importlib.metadata.version("gepa") != GEPA_VERSION:
        raise RuntimeError(f"Install pinned gepa=={GEPA_VERSION}")
    if any(type(x) is not int or x < 1 for x in (max_proposals, max_evaluations, minibatch_size)):
        raise ValueError("Search limits must be positive integers")
    if not 0 <= min_gain <= 1 or type(max_regressions) is not int or max_regressions < 0:
        raise ValueError("Invalid acceptance thresholds")
    base, base_sha = load_skill(base_path)
    if not version.strip() or version == base["version"]:
        raise ValueError("A new nonempty version is required")
    if not learning or not validation or any(c.role != "learning" for c in learning) or any(
            c.role != "validation" for c in validation):
        raise ValueError("Separate nonempty learning and validation cases required")
    if ({c.key for c in learning} & {c.key for c in validation}
            or len({c.key for c in learning + validation}) != len(learning + validation)):
        raise ValueError("Duplicate or overlapping decision states")
    for c in learning + validation:
        c.verify()
    # Final paired checks need space even if GEPA spends its whole search allowance.
    gate_reserve = 2 * (len(learning) + len(validation))
    worst_iteration = 2 * min(minibatch_size, len(learning)) + len(validation)
    if max_evaluations < gate_reserve + len(validation) + worst_iteration:
        raise ValueError("Evaluation budget cannot cover seed, one search iteration and final paired checks")
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    describe = lambda p: getattr(p, "describe", lambda: {"kind": type(p).__name__})()
    manifest = dict(method="ace-inspired-curation+gepa", gepa_version=GEPA_VERSION, base_sha256=base_sha,
                    learning=[asdict(c) for c in learning], validation=[asdict(c) for c in validation],
                    evaluator=describe(evaluator), reflector=describe(reflector), proposer=describe(proposer),
                    max_proposals=max_proposals, max_evaluations=max_evaluations, minibatch_size=minibatch_size,
                    seed=seed, min_gain=min_gain, max_regressions=max_regressions,
                    reflection_chunk_size=reflection_chunk_size, synthetic=any(c.synthetic for c in learning + validation),
                    evaluation="fixed-state action agreement; selection validation, not held-out generalization",
                    source_sha256=replay.sha(__file__))
    write_json(out / "manifest.json", manifest)
    write_json(out / "base_skill.json", base)
    try:
        snapshot = curate(base, learning, reflector, out / "ace", chunk_size=reflection_chunk_size)
        frozen_sha = digest(snapshot)
        adapter = SkillAdapter(base, snapshot, learning, evaluator, proposer, out, max_evaluations)
        original = {"skill_plan": '{"edits": []}'}
        if not snapshot["suggestions"]:
            result = dict(status="rejected", reason="ACE proposed no supported changes", evaluations=0)
        else:
            # Stop before an iteration if its worst-case new calls would consume the gate reserve.
            def exhausted(state):
                return (adapter.proposal_error is not None or
                        adapter.evaluation_count + worst_iteration > max_evaluations - gate_reserve)
            search = gepa.optimize(seed_candidate=original, trainset=learning, valset=validation, adapter=adapter,
                candidate_selection_strategy="pareto", reflection_minibatch_size=min(minibatch_size, len(learning)),
                stop_callbacks=[MaxCandidateProposalsStopper(max_proposals), exhausted],
                seed=seed, run_dir=str(out / "gepa"), raise_on_exception=True, cache_evaluation=False,
                skip_perfect_score=True, use_merge=False)
            if adapter.proposal_error is not None:
                raise RuntimeError("GEPA proposal failed; no skill accepted") from adapter.proposal_error
            write_json(out / "search.json", search.to_dict())
            candidate = search.best_candidate
            skill, selected = materialize(base, snapshot, candidate)
            skill.update(version=version, parent=base["version"], status="candidate",
                         learning_provenance=dict(ace_snapshot_sha256=frozen_sha, suggestion_ids=selected))
            write_json(out / "candidate_skill.json", skill)
            checks = {}
            for name, cases in (("learning", learning), ("validation", validation)):
                old = adapter.evaluate(cases, original).outputs
                new = adapter.evaluate(cases, candidate).outputs
                checks[name] = dict(base=old, candidate=new,
                    gain=(sum(r["correct"] for r in new) - sum(r["correct"] for r in old)) / len(cases),
                    regressions=sum(a["correct"] and not b["correct"] for a, b in zip(old, new)),
                    new_false_discards=sum(b["false_discard"] and not a["false_discard"] for a, b in zip(old, new)),
                    new_false_merges=sum(b["false_merge"] and not a["false_merge"] for a, b in zip(old, new)))
            val = checks["validation"]
            accepted = (bool(selected) and val["gain"] > min_gain and val["regressions"] <= max_regressions
                        and checks["learning"]["gain"] >= 0
                        and all(not x["new_false_discards"] and not x["new_false_merges"] for x in checks.values()))
            result = dict(status="accepted" if accepted else "rejected", checks=checks,
                          search_candidates=len(search.candidates), search_best_idx=search.best_idx,
                          proposals=adapter.proposal_count, evaluations=adapter.evaluation_count,
                          ace_snapshot_sha256=frozen_sha, selected_suggestions=selected)
        for case in learning + validation:
            case.verify()
        if replay.sha(base_path) != base_sha or digest(replay.read(out / "ace/suggestions.json")) != frozen_sha:
            raise ValueError("Frozen learning inputs changed during optimization")
        if result["status"] == "accepted":
            # Record checksum before activation; failed runs cannot be mistaken for accepted versions.
            skill["status"] = "accepted"
            write_json(out / "accepted_skill.json", skill)
            result["accepted_skill_sha256"] = replay.sha(out / "accepted_skill.json")
        write_json(out / "result.json", result)
        return result
    except Exception as exc:
        write_json(out / "result.json", dict(status="failed", error_type=type(exc).__name__, error=str(exc)))
        raise
