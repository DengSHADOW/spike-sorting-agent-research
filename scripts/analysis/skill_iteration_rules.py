"""Pure offline v2 scoring and campaign gates. No SDK, credentials or networking."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSTRAINTS = (
    "discard_loses_expert_spikes_next_action_not_unique",
    "all_members_terminal_noise_next_action_not_unique",
    "mixed_terminal_membership_next_action_not_unique",
)
RISKS = ("pure_expert_discard", "pure_noise_keep", "mixed_harm")
ACTIONS = {"phase1": {"KEEP", "SPLIT", "DISCARD"}, "phase2": {"MERGE", "NOT_MERGE", "DISCARD"}}


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_rules(path, _seen=None):
    _seen = set() if _seen is None else _seen
    path = Path(path).resolve()
    if path in _seen:
        raise ValueError('Cyclic rules inheritance')
    _seen.add(path)
    rules = json.loads(Path(path).read_text())
    if 'extends' in rules:
        source = ROOT / rules['extends']['path']
        if file_hash(source) != rules['extends']['sha256']:
            raise ValueError('Base rules hash changed')
        base = load_rules(source, _seen)
        for key, value in rules['updates'].items():
            if isinstance(value, dict):
                base[key] = {**base[key], **value}
            else:
                base[key] = value
        base['amendment'] = rules['amendment']
        return base
    parent = ROOT / rules["parent"]["path"]
    if file_hash(parent) != rules["parent"]["sha256"]:
        raise ValueError("Parent rules hash changed")
    original = json.loads(parent.read_text())
    for key in rules["parent"]["inherit"]:
        rules[key] = original[key]
    return rules


def vote(answers, planned_repeats, threshold=.7):
    if type(planned_repeats) is not int or planned_repeats < 1 or len(answers) > planned_repeats:
        raise ValueError("Invalid repeat count")
    counts = Counter(a["action"] for a in answers)
    top = max(counts.values(), default=0)
    leaders = sorted(a for a, n in counts.items() if n == top)
    complete = len(answers) == planned_repeats
    action = leaders[0] if complete and len(leaders) == 1 and top * 2 > planned_repeats else None
    consistency = top / planned_repeats if planned_repeats > 1 else None
    return dict(action=action, counts=dict(counts), complete=complete,
                tie=len(leaders) > 1, no_majority=complete and action is None,
                consistency=consistency, stable=(complete and action is not None and consistency >= threshold) if consistency is not None else None,
                rationales=[a["rationale"] for a in answers],
                repeat_ids=[a.get("repeat", i) for i, a in enumerate(answers, 1)])


def corrected_cases(cases, rules):
    result = deepcopy(cases)
    by_id = {c["case_id"]: c for c in result}
    if len(by_id) != len(result):
        raise ValueError("Duplicate case IDs")
    for fix in rules["corrections"]:
        if any(k not in fix for k in rules["correction_policy"]["required_fields"]):
            raise ValueError("Incomplete correction audit")
        if not all(isinstance(fix[k], str) and fix[k].strip() for k in ("reason", "evidence", "reviewer")):
            raise ValueError("Correction requires evidence and reviewer")
        case = by_id[fix["case_id"]]
        field = fix["field"]
        if field not in rules["correction_policy"]["allowed_fields"] or case.get(field) != fix["before"]:
            raise ValueError("Correction before/field mismatch")
        if field == "expert_action":
            if case["target_type"] != "recorded_expert_edit" or fix["after"] not in ACTIONS[case["phase"]]:
                raise ValueError("Illegal expert action correction")
        elif case["target_type"] != "derived_terminal_constraint" or fix["after"] not in rules["constraints"]:
            raise ValueError("Illegal constraint correction")
        case[field] = fix["after"]
    return result


def summarize(cases, votes, rules):
    counts = dict.fromkeys((*RISKS, "expert_correct"), 0)
    denominators = counts.copy()
    unresolved = counts.copy()
    recalls = {a: dict(correct=0, total=0, unresolved=0) for a in ("DISCARD", "SPLIT", "MERGE")}
    for c in cases:
        v = votes[c["case_id"]]
        action = v["action"]
        if c["target_type"] == "recorded_expert_edit":
            key, positive = "expert_correct", action == c["expert_action"]
            recall = recalls.setdefault(c["expert_action"], dict(correct=0, total=0, unresolved=0))
            recall["total"] += 1
            recall["unresolved"] += action is None
            recall["correct"] += bool(action is not None and positive)
        else:
            key = RISKS[CONSTRAINTS.index(c["constraint"])]
            positive = action in rules["constraints"][c["constraint"]]["harmful_actions"]
        denominators[key] += 1
        unresolved[key] += action is None
        counts[key] += bool(action is not None and positive)
    return dict(counts=counts, denominators=denominators, unresolved=unresolved,
                rate_bounds={k: [counts[k]/n, (counts[k]+unresolved[k])/n] if n else None
                             for k, n in denominators.items()},
                recall_by_action=recalls, complete=all(votes[c['case_id']]['complete'] for c in cases),
                stable=None if all(votes[c['case_id']]['consistency'] is None for c in cases) else all(votes[c["case_id"]]["stable"] for c in cases))


def compare_stats(base, candidate, rules, eligible=True):
    """Two-sided, predeclared operational tolerance, NOT significance testing."""
    ready = (base.get('complete') and candidate.get('complete')) if rules['selection'].get('single_pass') else (base['stable'] and candidate['stable'])
    if not eligible or not ready:
        return dict(winner=None, status="insufficient_or_unstable", deltas=None)
    if base["denominators"] != candidate["denominators"] or not all(base["denominators"].values()):
        return dict(winner=None, status="missing_or_mismatched_targets", deltas=None)
    delta = {k: candidate["counts"][k] - base["counts"][k] for k in base["counts"]}
    minimum = rules["selection"]["minimum_improvement_cases"]
    tolerance = rules["selection"]["allowed_regression_cases"]
    def wins(d):
        return (all(d[k] <= tolerance for k in RISKS) and d["expert_correct"] >= -tolerance
                and (any(d[k] <= -minimum for k in RISKS[:2]) or d["expert_correct"] >= minimum))
    winner = "candidate" if wins(delta) else "baseline" if wins({k: -v for k, v in delta.items()}) else None
    return dict(winner=winner, status="eligible_for_review" if winner else
                "within_tolerance" if all(abs(n) <= tolerance for n in delta.values()) else "tradeoff_no_winner",
                deltas=delta)


def score_round(cases, arms, rules, repeats):
    if "v0" not in arms or len(arms) < 2:
        raise ValueError("Every round requires v0 and a candidate")
    case_ids = {c["case_id"] for c in cases}
    votes, stats, groups = {}, {}, {}
    for version, answers in arms.items():
        if set(answers) - case_ids:
            raise ValueError("Unregistered extra cases")
        votes[version] = {}
        for case in cases:
            records = answers.get(case["case_id"], [])
            if any(a["action"] not in ACTIONS[case["phase"]] or not isinstance(a["rationale"], str) for a in records):
                raise ValueError("Invalid action/rationale")
            threshold = rules["voting"]["minimum_consistency"]
            votes[version][case["case_id"]] = vote(records, repeats, .7 if threshold is None else threshold)
        stats[version] = summarize(cases, votes[version], rules)
        groups[version] = {}
        for c in cases:
            for key in ("dataset:"+c["dataset_id"],
                        "stratum:"+"/".join((c["target_type"], c["phase"], c["image_layout"]))):
                groups[version].setdefault(key, []).append(c)
        groups[version] = {k: summarize(cs, votes[version], rules) for k, cs in groups[version].items()}
    comparisons = {}
    for version in arms:
        if version == "v0":
            continue
        paired = [(c, votes["v0"][c["case_id"]], votes[version][c["case_id"]]) for c in cases]
        resolved = [(c, a, b) for c, a, b in paired if a["action"] is not None and b["action"] is not None]
        changed = sum(a["action"] != b["action"] for _, a, b in resolved)
        regressions, improvements = [], []
        for c, a, b in resolved:
            if c["target_type"] != "recorded_expert_edit":
                continue
            old, new = a["action"] == c["expert_action"], b["action"] == c["expert_action"]
            if old != new:
                item = dict(case_id=c["case_id"], dataset_id=c["dataset_id"], expert_action=c["expert_action"],
                    baseline=a, candidate=b, attribution=rules["report"]["default_attribution"],
                    attribution_status="pending_review_not_causal_proof")
                (regressions if old else improvements).append(item)
        selection = compare_stats(stats["v0"], stats[version], rules,
                                   eligible=repeats == rules["voting"]["planned_repeats"])
        comparisons[version] = dict(**selection, decision_changes=changed, paired_resolved=len(resolved),
            paired_total=len(cases), unresolved_pairs=len(cases)-len(resolved),
            small_effect=len(resolved) == len(cases) and changed <= rules["report"]["small_effect_max_changed_cases"],
            expert_regressions=regressions, expert_improvements=improvements,
            adopted=False, review_status="pending")
    return dict(votes=votes, by_version=stats, by_group=groups, comparisons=comparisons,
                repeats=repeats, instability_label=rules["voting"]["unstable_label"])


def rescore_campaign(cases, registered_runs, runs, rules, extra_groups=None):
    """One correction set applies to EVERY run/version, including historical data."""
    if len(set(registered_runs)) != len(registered_runs) or set(registered_runs) != set(runs):
        raise ValueError("All registered rounds/versions must be rescored together")
    extra_groups = extra_groups or {}
    all_cases = cases + [c for group in extra_groups.values() for c in group]
    corrected = {c['case_id']: c for c in corrected_cases(all_cases, rules)}
    def subset_score(selected, run):
        ids = {c['case_id'] for c in selected}
        arms = {v: {cid: answers for cid, answers in arm.items() if cid in ids} for v, arm in run['arms'].items()}
        return score_round([corrected[c['case_id']] for c in selected], arms, rules, run['repeats'])
    results = {}
    for name in registered_runs:
        run = runs[name]
        if any(set(arm)-set(corrected) for arm in run['arms'].values()):
            raise ValueError('Unregistered cases in campaign')
        result = subset_score(cases, run)
        result['additional_groups'] = {}
        for group, selected in extra_groups.items():
            extra = subset_score(selected, run)
            for c in extra['comparisons'].values():
                c.update(winner=None, status='supplementary_excluded_from_selection')
            result['additional_groups'][group] = extra
        results[name] = result
    return results


def budget_gate(round_number, cap, spent, unresolved, estimate, rules):
    if type(round_number) is not int or round_number not in (2, 3) or round_number > rules["rounds"]["maximum"]:
        return dict(allowed=False, reason="round_limit_or_historical_round")
    if cap is None:
        return dict(allowed=False, reason="budget_unset")
    values = [cap, spent, unresolved, estimate]
    if any(isinstance(v, bool) for v in values):
        raise ValueError("Invalid monetary value")
    cap, spent, unresolved, estimate = map(lambda v: Decimal(str(v)), values)
    if not all(v.is_finite() for v in (cap, spent, unresolved, estimate)) or cap <= 0 or min(spent, unresolved, estimate) < 0:
        raise ValueError("Invalid monetary value")
    remaining = cap-spent-unresolved
    return dict(allowed=estimate <= remaining, reason="within_cap" if estimate <= remaining else "whole_round_exceeds_remaining",
                remaining_usd=float(remaining), round_estimate_usd=float(estimate), inference_authorized=False)


def fallback_winner(round_results, approved, rules):
    """Approval IDs must be explicit; no automatic adoption from numeric scores."""
    eligible = {v for result in round_results for v, c in result["comparisons"].items()
                if c["winner"] == "candidate" and v in approved}
    for v in ["combined", "v1", *rules["rounds"]["fallback_order_if_multiple_reviewed_single_winners"]]:
        if v in eligible:
            return v
    return "v0"


def next_round(round_number, result, approved, rules, prior_results=()):
    selected = fallback_winner([*prior_results, result], approved, rules)
    if round_number >= rules["rounds"]["maximum"]:
        return dict(next_round=None, selected=selected, reason="round_limit")
    if round_number == 1:
        return dict(next_round=2 if selected == "v0" else None, selected=selected, reason="historical_v1_not_accepted")
    if round_number == 2:
        if set(result['comparisons']) != {'S1', 'S2', 'S3', 'S4', 'S5'}:
            return dict(next_round=None, selected=selected, reason='incomplete_ablation_inventory')
        comparisons = list(result["comparisons"].values())
        if any(c["status"] in {"insufficient_or_unstable", "missing_or_mismatched_targets"} for c in comparisons):
            return dict(next_round=None, selected=selected, reason="insufficient_not_evidence_of_no_improvement")
        if any(c["winner"] == "candidate" and v not in approved for v, c in result["comparisons"].items()):
            return dict(next_round=None, selected=selected, reason="await_manual_review")
        return dict(next_round=3 if selected != "v0" else None, selected=selected,
                    reason="combine_reviewed_winners" if selected != "v0" else "no_single_improvement_freeze_v0")
    raise ValueError("Unknown round")


def freeze_manifest(round_number, skills, files, rules_path, output, rules, combined_from=(), additional_groups=None):
    """Offline-only pre-run manifest. All scientific inputs supplied as paths.

    No client is constructed; even a budget-approved manifest is not authorization.
    """
    if round_number not in (2, 3):
        raise ValueError('Only prospective rounds 2 and 3 may be prepared')
    expected = set(rules["rounds"][f"round_{round_number}"]["versions"])
    if set(skills) != expected:
        raise ValueError("Round must include all predeclared candidate arms")
    if round_number == 3 and (not combined_from or set(combined_from)-set(rules["rounds"]["round_2"]["versions"][1:])):
        raise ValueError("Combined candidate needs reviewed single-change provenance")
    required = {"system", "observations", "model_schema", "code", "estimate", "budget", "cases"}
    if not required <= set(files):
        raise ValueError("Missing frozen scientific inputs")
    if file_hash(files['cases']) != rules['original_cases']['sha256']:
        raise ValueError('Original cases changed')
    cases = [json.loads(line) for line in Path(files['cases']).read_text().splitlines() if line.strip()]
    if len(cases) != rules['original_cases']['count']:
        raise ValueError('Wrong number of frozen cases')
    files = dict(files)
    additional_groups = additional_groups or {}
    for group, path in sorted(additional_groups.items()):
        if not group or 'additional_case_group:'+group in files:
            raise ValueError('Invalid additional group name')
        files['additional_case_group:'+group] = path
        cases.extend(json.loads(line) for line in Path(path).read_text().splitlines() if line.strip())
    if len({c['case_id'] for c in cases}) != len(cases):
        raise ValueError('Additional cases overlap original cases')
    repeats = rules['voting']['planned_repeats']
    manifest = dict(format="skill-iteration-offline-v2", round=round_number, sendable=False,
        rules_sha256=file_hash(rules_path), repeats=rules["voting"]["planned_repeats"],
        combined_from=list(combined_from),
        additional_groups=sorted(additional_groups),
        jobs=[dict(job_id=f"{c['case_id']}--{v}--r{r}", case_id=c['case_id'], phase=c['phase'],
                   version=v, repeat=r) for c in cases for v in sorted(skills) for r in range(1, repeats+1)],
        skills={v: dict(path=str(Path(p).resolve()), sha256=file_hash(p)) for v, p in skills.items()},
        frozen_files={k: dict(path=str(Path(p).resolve()), sha256=file_hash(p)) for k, p in files.items()})
    path = Path(output)
    with path.open("x", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def verify_rules_revision(expected_hash, rules_path):
    visited = set()
    current_path = Path(rules_path)
    while expected_hash != file_hash(current_path):
        # Only a documented correction-only revision may rescore old runs.
        current_hash = file_hash(current_path)
        if current_hash in visited:
            raise ValueError('Cyclic scoring revision')
        visited.add(current_hash)
        current = json.loads(current_path.read_text())
        revision = current.get('correction_revision_of', {})
        old_path = ROOT / revision.get('path', '__missing_rules__')
        if not old_path.is_file() or file_hash(old_path) != revision.get('sha256'):
            raise ValueError("Scoring hash changed; new revision/rescore required")
        old = json.loads(old_path.read_text())
        ignored = {'version', 'status', 'corrections', 'correction_revision_of'}
        if ({k: v for k, v in current.items() if k not in ignored} !=
                {k: v for k, v in old.items() if k not in ignored} or
                current['corrections'][:len(old['corrections'])] != old['corrections']):
            raise ValueError('Not a correction-only cumulative revision')
        current_path = old_path


def verify_manifest(manifest, rules_path):
    verify_rules_revision(manifest['rules_sha256'], rules_path)
    rules = load_rules(rules_path)
    if manifest.get('format') != 'skill-iteration-offline-v2' or manifest.get('round') not in (2, 3):
        raise ValueError('Invalid round manifest')
    if set(manifest['skills']) != set(rules['rounds'][f"round_{manifest['round']}"]['versions']):
        raise ValueError('Missing predeclared arm')
    if manifest['repeats'] != rules['voting']['planned_repeats']:
        raise ValueError('Repeat count changed')
    for row in [*manifest["skills"].values(), *manifest["frozen_files"].values()]:
        if file_hash(row["path"]) != row["sha256"]:
            raise ValueError("Frozen input/skill changed")
    cases_file = manifest['frozen_files']['cases']['path']
    if file_hash(cases_file) != rules['original_cases']['sha256']:
        raise ValueError('Original cases changed')
    cases = [json.loads(line) for line in Path(cases_file).read_text().splitlines() if line.strip()]
    expected_groups = {'additional_case_group:'+name for name in manifest.get('additional_groups', [])}
    actual_groups = {key for key in manifest['frozen_files'] if key.startswith('additional_case_group:')}
    if expected_groups != actual_groups:
        raise ValueError('Additional-group inventory changed')
    for group in manifest.get('additional_groups', []):
        path = manifest['frozen_files']['additional_case_group:'+group]['path']
        cases.extend(json.loads(line) for line in Path(path).read_text().splitlines() if line.strip())
    if len({c['case_id'] for c in cases}) != len(cases):
        raise ValueError('Overlapping cases')
    expected_jobs = [dict(job_id=f"{c['case_id']}--{v}--r{r}", case_id=c['case_id'], phase=c['phase'],
                          version=v, repeat=r) for c in cases for v in sorted(manifest['skills'])
                     for r in range(1, manifest['repeats']+1)]
    if manifest['jobs'] != expected_jobs:
        raise ValueError('Planned cases/versions/repeats changed')
