"""Score frozen fixed-state replays offline, without imputing missing decisions."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.run import run_skill_replay as replay


def ratio(n, d):
    return n / d if d else None


def summarize(rows, rules):
    expert = [r for r in rows if r['target_type'] == 'recorded_expert_edit']
    derived = [r for r in rows if r['target_type'] == 'derived_terminal_constraint']
    def expert_stats(items):
        n = sum(r['correct'] for r in items)
        return {'correct': n, 'observed': len(items), 'action_match_rate': ratio(n, len(items))}
    def harm_stats(items):
        n = sum(r['harmful'] for r in items)
        return {'harmful': n, 'observed': len(items), 'harmful_rate': ratio(n, len(items)),
                'categories': dict(Counter(r['category'] for r in items))}
    return {'expert': {**expert_stats(expert),
                       'by_phase': {p: expert_stats([r for r in expert if r['phase'] == p])
                                    for p in ('phase1', 'phase2')},
                       'recall_by_action': {a: expert_stats([r for r in expert if r['expert_action'] == a])
                                            for a in rules['expert']['recall_actions']}},
            'derived': {**harm_stats(derived),
                        'by_constraint': {c: harm_stats([r for r in derived if r['constraint'] == c])
                                          for c in rules['constraints']}}}


def calculate_scores(cases, jobs, answers, rules, repeats):
    """answers maps job_id to a strictly parsed action+rationale (testable offline)."""
    case_map = {c['case_id']: c for c in cases}
    if set(answers) - {j['job_id'] for j in jobs}:
        raise replay.ReplayStop('Unplanned answer')
    rows = []
    for job in jobs:
        if job['job_id'] not in answers:
            continue
        c, answer = case_map[job['case_id']], answers[job['job_id']]
        if answer['action'] not in replay.contract.ACTIONS[c['phase']]:
            raise replay.ReplayStop('Illegal phase action')
        row = {**job, **answer}
        if c['target_type'] == 'recorded_expert_edit':
            row.update(expert_action=c['expert_action'], correct=answer['action'] == c['expert_action'])
        else:
            rule = rules['constraints'][c['constraint']]
            categories = [k.removesuffix('_actions') for k in (
                'harmful_actions', 'preferred_actions', 'tolerable_not_ideal_actions', 'acceptable_actions')
                if answer['action'] in rule[k]]
            if len(categories) != 1:
                raise replay.ReplayStop('Action missing/ambiguous in scoring rule')
            row.update(constraint=c['constraint'], category=categories[0], harmful=categories[0] == 'harmful')
        rows.append(row)
    versions = sorted({j['version'] for j in jobs})
    groups = {v: [r for r in rows if r['version'] == v] for v in versions}
    totals = {v: summarize(rs, rules) for v, rs in groups.items()}
    strata, datasets, consistency = {}, {}, {}
    for v, rs in groups.items():
        strata[v] = {}
        for key in sorted({(j['target_type'], j['phase'], j['image_layout'], j['image_count']) for j in jobs}):
            selected = [r for r in rs if (r['target_type'], r['phase'], r['image_layout'], r['image_count']) == key]
            strata[v][' / '.join(map(str, key))] = summarize(selected, rules)
        datasets[v] = {d: summarize([r for r in rs if r['dataset_id'] == d], rules)
                       for d in sorted({j['dataset_id'] for j in jobs})}
        if repeats == 1:
            consistency[v] = None
        else:
            by_case = defaultdict(list)
            for r in rs:
                by_case[r['case_id']].append(r['action'])
            equal = pairs = unanimous = complete_cases = 0
            for actions in by_case.values():
                for a, b in combinations(actions, 2):
                    pairs += 1
                    equal += a == b
                if len(actions) == repeats:
                    complete_cases += 1
                    unanimous += len(set(actions)) == 1
            consistency[v] = {'equal_pairs': equal, 'observed_pairs': pairs,
                              'pairwise_consistency': ratio(equal, pairs), 'unanimous_cases': unanimous,
                              'complete_cases': complete_cases, 'unanimity': ratio(unanimous, complete_cases)}
    paired = {(r['case_id'], r['repeat'], r['version']): r for r in rows}
    changes = {k: [] for k in ('expert_regressions', 'expert_improvements', 'harm_regressions', 'harm_improvements')}
    for cid, repeat in sorted({(r['case_id'], r['repeat']) for r in rows}):
        a, b = paired.get((cid, repeat, 'v0')), paired.get((cid, repeat, 'v1'))
        if a is None or b is None:
            continue
        item = {'case_id': cid, 'repeat': repeat, 'dataset_id': a['dataset_id'], 'phase': a['phase'],
                'v0': {k: a[k] for k in ('action', 'rationale')},
                'v1': {k: b[k] for k in ('action', 'rationale')}}
        if a['target_type'] == 'recorded_expert_edit':
            item['expert_action'] = a['expert_action']
            if a['correct'] != b['correct']:
                changes['expert_regressions' if a['correct'] else 'expert_improvements'].append(item)
        elif a['harmful'] != b['harmful']:
            item['constraint'] = a['constraint']
            changes['harm_regressions' if b['harmful'] else 'harm_improvements'].append(item)
    complete = len(rows) == len(jobs) and set(versions) == {'v0', 'v1'}
    # Require both targets and all intended pairs, not merely an equal response count.
    complete = complete and all((c['case_id'], r, v) in paired
                                for c in cases for r in range(1, repeats + 1) for v in ('v0', 'v1'))
    has_targets = all(totals[v][t]['observed'] > 0 for v in versions for t in ('expert', 'derived'))
    eligible = complete and has_targets
    harm_pass = (totals['v1']['derived']['harmful_rate'] < totals['v0']['derived']['harmful_rate']) if eligible else None
    correct_pass = (totals['v1']['expert']['correct'] >= totals['v0']['expert']['correct'] - 1) if eligible else None
    return {'disclaimer': replay.DISCLAIMER, 'planned_calls': len(jobs), 'valid_responses': len(rows),
            'missing_job_ids': [j['job_id'] for j in jobs if j['job_id'] not in answers],
            'by_version': totals, 'by_stratum': strata, 'by_dataset': datasets,
            'repeat_consistency': consistency, **changes,
            'preregistered_conditions': {'complete_paired_run': complete,
                'v1_harmful_rate_lower': harm_pass, 'v1_expert_correct_at_least_v0_minus_one': correct_pass,
                'regressions_listed_for_manual_review': True,
                'candidate_eligible_for_review': bool(harm_pass and correct_pass) if eligible else None,
                'human_review': 'pending' if changes['expert_regressions'] else 'no expert regressions observed',
                'final_acceptance': 'not automatic'}, 'decisions': rows}


def collect_responses(out, spec):
    path = out / 'responses.jsonl'
    raw_rows = replay.read_jsonl(path) if path.exists() else []
    if len(raw_rows) > len(spec['jobs']):
        raise replay.ReplayStop('Too many responses')
    answers, invalid, bills = {}, [], []
    for row, job in zip(raw_rows, spec['jobs']):
        if row['job_id'] != job['job_id'] or row['request_sha256'] != job['request_sha256']:
            raise replay.ReplayStop('Response order/request hash mismatch')
        if invalid:
            raise replay.ReplayStop('Responses exist after first invalid response')
        raw = row['response']
        try:
            bill = replay.usage_cost(raw)
            bills.append({'job_id': job['job_id'], **bill})
            if bill['output_tokens'] > 4000:
                raise replay.ReplayStop('Output cap exceeded')
            answers[job['job_id']] = replay.decision_from_response(raw, job['phase'])
        except (replay.ReplayStop, ValueError, TypeError, KeyError) as exc:
            invalid.append({'job_id': job['job_id'], 'error_type': type(exc).__name__, 'reason': str(exc)})
    ledger = out / 'usage.jsonl'
    if ledger.exists() and replay.read_jsonl(ledger) != bills:
        raise replay.ReplayStop('Usage ledger differs from raw responses')
    status_path = out / 'execution_status.json'
    status = replay.read_json(status_path) if status_path.exists() else None
    if status and status['state'] == 'completed':
        if invalid or len(answers) != len(spec['jobs']) or status['valid_responses'] != len(answers):
            raise replay.ReplayStop('Completion status contradicts response coverage')
    totals = {k: sum(b[k] for b in bills) for k in (
        'input_tokens', 'output_tokens', 'cached_input_tokens', 'conservative_usd', 'standard_estimate_usd')}
    totals.update(raw_responses=len(raw_rows), valid_responses=len(answers),
                  attempted_calls=status['attempted_calls'] if status else None,
                  charged_or_reserved_usd=status['charged_or_reserved_usd'] if status else None,
                  cost_note='Usage-based estimate, not invoice; unresolved requests retain reservations.')
    return answers, invalid, totals, status


def render_report(result):
    lines = ['# Skill replay diagnostic report', '', replay.DISCLAIMER, '',
             'Three-view historical cases and four-view Phase 1 expert cases are separate strata; '
             'Phase 2 expert cases have three views. No combined accuracy is reported.', '',
             f"Scoring SHA256: `{result['scoring_sha256']}`", '',
             f"Valid responses: {result['valid_responses']} / {result['planned_calls']}", '']
    def table(title, headers, rows):
        lines.extend([f'## {title}', '', '| ' + ' | '.join(headers) + ' |',
                      '| ' + ' | '.join(['---'] * len(headers)) + ' |'])
        lines.extend('| ' + ' | '.join(map(str, row)) + ' |' for row in rows)
        lines.append('')
    def rate(n, d):
        return f'{n}/{d} ({n / d:.1%})' if d else '0/0 (N/A)'
    methods = result['method']
    lines.extend([f"Model: `{methods['model']['name']}`; effort: `{methods['model']['reasoning_effort']}`; "
                  f"repeats: {methods['repeats']}; shuffle seed: {methods['seed']}.", ''])
    expert_rows, harm_rows = [], []
    for version, stats in result['by_version'].items():
        expert = stats['expert']
        expert_rows.append([version, *[rate(x['correct'], x['observed']) for x in (
            expert['by_phase']['phase1'], expert['by_phase']['phase2'],
            *expert['recall_by_action'].values())]])
        for constraint, x in stats['derived']['by_constraint'].items():
            harm_rows.append([version, constraint, rate(x['harmful'], x['observed']),
                              ', '.join(f'{k}={v}' for k, v in x['categories'].items()) or 'N/A'])
        x = stats['derived']
        harm_rows.append([version, 'All derived constraints (diagnostic)', rate(x['harmful'], x['observed']), ''])
    table('Expert action match and per-action recall', ['Skill', 'Phase 1', 'Phase 2', 'DISCARD', 'SPLIT', 'MERGE'], expert_rows)
    table('Derived-constraint harms (not action accuracy)', ['Skill', 'Constraint', 'Harmful / observed', 'Categories'], harm_rows)
    for title, groups in [('Image/phase strata', result['by_stratum']), ('Dataset groups', result['by_dataset'])]:
        table(title, ['Skill', 'Group', 'Expert match', 'Derived harm'],
              [[v, group, rate(x['expert']['correct'], x['expert']['observed']),
                rate(x['derived']['harmful'], x['derived']['observed'])]
               for v, data in groups.items() for group, x in data.items()])
    table('Repeat consistency', ['Skill', 'Equal action pairs', 'Unanimous complete cases'],
          [[v, rate(x['equal_pairs'], x['observed_pairs']), rate(x['unanimous_cases'], x['complete_cases'])]
           if x else [v, 'N/A (repeats=1)', 'N/A'] for v, x in result['repeat_consistency'].items()])
    table('Calls, tokens, cached input and costs', ['Measure', 'Value'], result['usage'].items())
    table('Preregistered conditions (not automatic acceptance)', ['Condition', 'Result'], result['preregistered_conditions'].items())
    for key in ('expert_regressions', 'expert_improvements', 'harm_regressions', 'harm_improvements'):
        lines.extend([f"## {key.replace('_', ' ').title()} — verbatim model rationales", ''])
        if not result[key]:
            lines.extend(['None observed.', ''])
        for item in result[key]:
            lines.extend([f"### {item['case_id']} / repeat {item['repeat']}", '',
                          f"Dataset: {item['dataset_id']}; phase: {item['phase']}; "
                          f"target: {item.get('expert_action', item.get('constraint'))}.", ''])
            for v in ('v0', 'v1'):
                lines.extend([f"{v}: {item[v]['action']}", '',
                              '\n'.join('> ' + line for line in item[v]['rationale'].split('\n')), ''])
    lines.extend(['## Missing and invalid responses (never imputed)', '',
                  f"Missing jobs: {', '.join(result['missing_job_ids']) or 'none'}", ''])
    for x in result['invalid_responses']:
        lines.extend([f"- {x['job_id']}: {x['error_type']} — {x['reason']}", ''])
    return '\n'.join(lines)


def score_run(out, rules_path=None):
    out = replay.experiment_root(out)
    rules_path = Path(rules_path) if rules_path else replay.SCORING
    manifest = replay.read_json(out / 'manifest.json')
    if replay.file_hash(rules_path) != manifest['spec']['scoring_sha256']:
        raise replay.ReplayStop('Scoring hash changed; invalidate run and start again')
    if (out / 'score.json').exists() or (out / 'REPORT.md').exists():
        raise replay.ReplayStop('Scoring outputs already exist; refusing overwrite')
    spec, _, cases = replay.verify_run(out)
    answers, invalid, usage, status = collect_responses(out, spec)
    result = calculate_scores(cases, spec['jobs'], answers, replay.read_json(rules_path), spec['repeats'])
    # An interrupted execution can have a last valid response without a settled run.
    if not status or status['state'] != 'completed':
        result['preregistered_conditions']['candidate_eligible_for_review'] = None
        result['preregistered_conditions']['execution_completed'] = False
    else:
        result['preregistered_conditions']['execution_completed'] = True
    result.update(scoring_sha256=spec['scoring_sha256'], manifest_sha256=replay.file_hash(out / 'manifest.json'),
                  method={k: spec[k] for k in ('model', 'repeats', 'seed', 'config_hashes', 'mode')},
                  invalid_responses=invalid, usage=usage, execution_status=status,
                  response_sha256=replay.file_hash(out / 'responses.jsonl') if (out / 'responses.jsonl').exists() else None)
    replay.write_new(out / 'score.json', result)
    with (out / 'REPORT.md').open('x', encoding='utf-8') as f:
        f.write(render_report(result))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--scoring-rules', type=Path, default=replay.SCORING)
    args = parser.parse_args(argv)
    try:
        result = score_run(args.run_dir, args.scoring_rules)
        print(f"Scored {result['valid_responses']}/{result['planned_calls']} responses. Diagnostic only.")
        return 0
    except (replay.ReplayStop, ValueError, KeyError, OSError) as exc:
        print(f'STOP: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
