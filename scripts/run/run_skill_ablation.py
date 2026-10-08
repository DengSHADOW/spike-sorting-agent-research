"""Prospective original-35, six-arm skill ablation. Offline unless explicit mode.

Reuse the original verified image/request and fail-stop cost helpers without
changing historical files. Frozen manifest binds candidate construction as well
as materialized skill files. No third round or automatic adoption is supported.
"""
from __future__ import annotations
import argparse
import copy
from decimal import Decimal
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.run import run_skill_replay as r
from scripts.analysis import skill_iteration_rules as scoring

RULES = ROOT / 'configs/curation/replay_scoring_v3.json'
ABLATION = ROOT / 'configs/curation/skill_ablation_v1.json'
BUDGET = ROOT / 'configs/curation/replay_iteration_budget.json'
CODE = ('scripts/run/run_skill_ablation.py', 'scripts/analysis/skill_iteration_rules.py')


def candidate_skills():
    plan = r.read_json(ABLATION)
    base_path = r.contract.CONFIG_DIR / 'skill_v0.json'
    source_path = r.contract.CONFIG_DIR / 'skill_v1.json'
    if (r.file_hash(base_path) != plan['base_sha256'] or
            r.file_hash(source_path) != plan['candidate_source_sha256']):
        raise r.ReplayStop('Frozen skill source changed')
    base, source = r.read_json(base_path), r.read_json(source_path)
    skills = {'v0': base}
    for name, change in plan['versions'].items():
        skill = copy.deepcopy(base)
        skill.update(version=name, status='single-topic-candidate-unvalidated',
                     ablation=dict(plan_sha256=r.file_hash(ABLATION), **change))
        for phase in ('phase1', 'phase2'):
            selected = change[phase]
            paragraphs = {int(m.group(1)): m.group(2) for line in source[phase].splitlines()
                          if (m := re.fullmatch(r'(\d+)\. (.+)', line))}
            if selected:
                skill[phase] += plan['amendment_prefix'] + '\n'.join(paragraphs[i] for i in selected)
        skills[name] = skill
    return skills


def build_plan():
    rules = scoring.load_rules(RULES)
    case_path = ROOT / rules['original_cases']['path']
    if r.file_hash(case_path) != rules['original_cases']['sha256']:
        raise r.ReplayStop('Original cases changed')
    # Validates every image and both original previews; never opens MAT files.
    spec, original, cases = r.build_plan(case_path.parent, versions=['v0'], repeats=1)
    if len(cases) != 35 or rules['voting']['planned_repeats'] != 1:
        raise r.ReplayStop('Wrong case or repeat count')
    skills = candidate_skills()
    versions = rules['rounds']['round_2']['versions']
    if set(skills) != set(versions):
        raise r.ReplayStop('Candidate inventory mismatch')
    templates = {j['case_id']: j for j in spec['jobs']}
    payloads, jobs = {}, []
    for case in cases:
        base = original[(case['case_id'], 'v0')]
        common = r.digest(r.without_skill(base))
        for version in versions:
            request = copy.deepcopy(base)
            part = request['input'][0]['content'][0]
            observation = part['text'].split('\nOBSERVATION\n', 1)[1]
            part['text'] = 'DOMAIN SKILL\n' + skills[version][case['phase']] + '\nOBSERVATION\n' + observation
            if r.digest(r.without_skill(request)) != common:
                raise r.ReplayStop('Non-skill request difference')
            payloads[(case['case_id'], version)] = request
            job = dict(templates[case['case_id']], version=version,
                       job_id=f"{case['case_id']}--{version}--r1",
                       request_sha256=r.digest(request), non_skill_sha256=common)
            jobs.append(job)
    random.Random(spec['seed']).shuffle(jobs)
    for i, job in enumerate(jobs):
        job['order'] = i
    files = [RULES, ABLATION, BUDGET, ROOT/'configs/curation/replay_scoring_v2.json',
             *[ROOT/name for name in CODE]]
    spec.update(format='skill-ablation-paid-v1', round=2, versions=versions,
                jobs=jobs, planned_calls=len(jobs), scoring_sha256=r.file_hash(RULES),
                rules_path=str(RULES), frozen_extensions={str(p): r.file_hash(p) for p in files},
                skills={name: r.digest(skill) for name, skill in skills.items()},
                budget_cap_usd=r.read_json(BUDGET)['cumulative_budget_usd'])
    return spec, payloads, cases, skills


def prepare(out):
    spec, payloads, cases, skills = build_plan()
    r.prepare(out, spec, payloads)
    (out / 'skills').mkdir()
    for name, skill in skills.items():
        r.write_new(out/'skills'/f'{name}.json', skill)
    return spec


def verify(out):
    out = r.experiment_root(out)
    saved = r.read_json(out/'manifest.json')['spec']
    spec, payloads, cases, skills = build_plan()
    if r.canonical(saved) != r.canonical(spec):
        raise r.ReplayStop('Frozen scientific inputs/code/budget changed; do not reuse run')
    if r.read_json(out/'dry_run.json')['manifest_sha256'] != r.file_hash(out/'manifest.json'):
        raise r.ReplayStop('Manifest hash mismatch')
    if r.read_jsonl(out/'requests.jsonl') != spec['jobs']:
        raise r.ReplayStop('Request schedule changed')
    for name, skill in skills.items():
        if r.read_json(out/'skills'/f'{name}.json') != skill:
            raise r.ReplayStop('Materialized candidate changed')
    return spec, payloads, cases


def whole_round_gate(out, spec, budget):
    if budget != spec['budget_cap_usd']:
        raise r.ReplayStop('Budget differs from frozen user cap')
    estimate = r.read_json(out/'estimate.json')
    if estimate['manifest_sha256'] != r.file_hash(out/'manifest.json'):
        raise r.ReplayStop('Estimate manifest mismatch')
    if estimate['scoring_sha256'] != spec['scoring_sha256']:
        raise r.ReplayStop('Estimate scoring mismatch')
    if len(estimate['per_call']) != len(spec['jobs']):
        raise r.ReplayStop('Estimate job count mismatch')
    total = Decimal(0)
    for row, job in zip(estimate['per_call'], spec['jobs']):
        if (row['job_id'] != job['job_id'] or row['request_sha256'] != job['request_sha256'] or
                row['conservative_reserved_usd'] != r.reservation(row['input_tokens'])):
            raise r.ReplayStop('Estimate request/reservation mismatch')
        total += Decimal(str(row['conservative_reserved_usd']))
    # Account for any previous attempts in this new campaign, even partial failures.
    spent = Decimal(0)
    for marker in (ROOT/'output').glob('*/execution_started.json'):
        if marker.parent.resolve() == out.resolve():
            continue
        manifest = marker.parent/'manifest.json'
        if not manifest.is_file():
            continue
        prior = r.read_json(manifest).get('spec', {})
        if prior.get('format') != 'skill-ablation-paid-v1':
            continue
        status = marker.parent/'execution_status.json'
        if not status.is_file():
            raise r.ReplayStop('Another ablation attempt is running or unsettled')
        spent += Decimal(str(r.read_json(status)['charged_or_reserved_usd']))
    gate = scoring.budget_gate(2, budget, spent, 0, total, scoring.load_rules(RULES))
    if not gate['allowed']:
        raise r.BudgetStop(f"Whole-round reserve ${total:.6f} exceeds remaining ${Decimal(str(budget))-spent:.6f}; no inference")
    return gate


def enable_credentials():
    # Only reached in explicit network modes after local verification.
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env', override=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--estimate', action='store_true')
    mode.add_argument('--execute', action='store_true')
    parser.add_argument('--budget-usd', type=float)
    args = parser.parse_args(argv)
    out = r.experiment_root(args.output_root)
    try:
        if not args.estimate and not args.execute:
            if args.budget_usd is not None:
                raise r.ReplayStop('Budget flag only valid for execution')
            spec = prepare(out)
            print(f"Dry-run: {spec['planned_calls']} calls, repeats=1, no client/network/key access", flush=True)
            return 0
        spec, payloads, _ = verify(out)
        if args.estimate:
            enable_credentials()
            estimate = r.estimate(out, spec, payloads)
            print(estimate['totals'], flush=True)
            whole_round_gate(out, spec, spec['budget_cap_usd'])
        else:
            whole_round_gate(out, spec, args.budget_usd)
            r.authorization(out, args.budget_usd, spec)
            enable_credentials()
            print(r.execute(out, spec, payloads, args.budget_usd), flush=True)
        return 0
    except (r.ReplayStop, ValueError, OSError) as exc:
        # Never print SDK exception bodies or requests/keys.
        print(f"STOP: {str(exc) if isinstance(exc, r.ReplayStop) else type(exc).__name__}", flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
