"""Offline campaign report. Never estimates tokens, sends inference or changes runs.

Campaign JSON: {"runs": [{"id": "round1", "round": 1, "run_dir": "..."}, ...]}.
It is the complete run inventory; corrections require all listed runs plus round 1.
Later runs use a skill-iteration-offline-v2 manifest with explicit planned jobs and
responses.jsonl in the legacy raw-response envelope (job_id, response).
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.analysis import skill_iteration_rules as v2
from scripts.run import run_skill_replay as replay


def read_runs(inventory, rules, rules_path):
    rows = inventory["runs"]
    numbers = [r["round"] for r in rows]
    if sorted(numbers) != list(range(1, max(numbers, default=0)+1)) or len(rows) > 3:
        raise ValueError("Inventory must include each round once, starting with historical round 1")
    runs, sources = {}, {}
    for entry in rows:
        name = entry["id"]
        if name in runs:
            raise ValueError("Duplicate registered run")
        directory = (ROOT / entry["run_dir"]).resolve()
        manifest = replay.read_json(directory / "manifest.json")
        if entry["round"] == 1:
            if directory != (ROOT / rules["rounds"]["historical_round_1"]["run_dir"]).resolve():
                raise ValueError("Wrong historical round")
            spec, _, _ = replay.verify_run(directory)
            jobs, repeats = spec["jobs"], spec["repeats"]
        elif manifest.get('spec', {}).get('format') == 'skill-ablation-paid-v1':
            from scripts.run import run_skill_ablation as ablation
            spec, _, _ = ablation.verify(directory)
            v2.verify_rules_revision(spec['scoring_sha256'], rules_path)
            if entry['round'] != 2:
                raise ValueError('Ablation is round 2 only')
            jobs, repeats = spec['jobs'], spec['repeats']
        else:
            v2.verify_manifest(manifest, rules_path)
            if manifest["round"] != entry["round"] or manifest["format"] != "skill-iteration-offline-v2":
                raise ValueError("Wrong round manifest")
            jobs, repeats = manifest["jobs"], manifest["repeats"]
        by_job = {j["job_id"]: j for j in jobs}
        if len(by_job) != len(jobs):
            raise ValueError("Duplicate planned jobs")
        arms = {j["version"]: {} for j in jobs}
        seen, seen_slots, usage = set(), set(), []
        response_dirs = [directory]
        unresolved_reservation = 0.0
        if entry.get('continuation_dirs'):
            if len(entry['continuation_dirs']) != 1 or entry['round'] != 2:
                raise ValueError('Only one audited round2 continuation supported')
            from scripts.run import resume_skill_ablation as continuation
            extra_dir = replay.experiment_root(ROOT / entry['continuation_dirs'][0])
            extra_spec, _, _ = continuation.verify(extra_dir)
            if Path(extra_spec['continuation']['source']).resolve() != directory:
                raise ValueError('Continuation parent mismatch')
            response_dirs.append(extra_dir)
            unresolved_reservation = extra_spec['continuation']['unresolved_prior_reservation_usd']
            allowed_extra = {j['job_id'] for j in extra_spec['jobs']}
            if any(x['job_id'] not in allowed_extra for x in replay.read_jsonl(extra_dir/'responses.jsonl')):
                raise ValueError('Continuation contains an unplanned response')
        for item in (x for folder in response_dirs for x in replay.read_jsonl(folder / 'responses.jsonl')):
            job = by_job[item["job_id"]]
            if item.get('request_sha256') != job.get('request_sha256') and 'request_sha256' in job:
                raise ValueError('Response request hash mismatch')
            slot = (job["version"], job["case_id"], job["repeat"])
            if item["job_id"] in seen or slot in seen_slots or not 1 <= job["repeat"] <= repeats:
                raise ValueError("Duplicate/invalid response repeat")
            seen.add(item["job_id"]); seen_slots.add(slot)
            answer = replay.decision_from_response(item["response"], job["phase"])
            usage.append(replay.usage_cost(item['response']))
            answer['repeat'] = job['repeat']
            arms[job["version"]].setdefault(job["case_id"], []).append(answer)
        runs[name] = dict(arms=arms, repeats=repeats)
        sources[name] = dict(manifest_sha256=v2.file_hash(directory / "manifest.json"),
                             responses_sha256=v2.file_hash(directory / "responses.jsonl"), round=entry["round"],
                             planned_calls=len(jobs), observed_calls=len(usage),
                             response_segments=[dict(path=str(folder), manifest_sha256=v2.file_hash(folder/'manifest.json'),
                                 responses_sha256=v2.file_hash(folder/'responses.jsonl')) for folder in response_dirs],
                             usage={key: sum(row[key] for row in usage) for key in
                                    ('input_tokens', 'output_tokens', 'cached_input_tokens', 'standard_estimate_usd', 'conservative_usd')})
        sources[name]['usage']['unresolved_failed_request_reservation_usd'] = unresolved_reservation
    return runs, sources


def render_report(results, rules, rules_hash):
    lines = ["# 多轮 skill 回放诊断（离线重评分）", "", rules["scope"], "",
             "三图历史状态、四图专家状态分层；不合并成总体准确率。历史第一轮的新规则分析是事后补充，不替代原预注册结论。", "",
             f"Scoring SHA256: `{rules_hash}`", ""]
    for name, result in results.items():
        if any(x["small_effect"] for x in result["comparisons"].values()):
            lines += [f"{name}：部分对照 {rules['report']['small_effect_label']}（≤1/35；具体分母见下）。", ""]
        if result['repeats'] > 1 and any(not x["stable"] for x in result["by_version"].values()):
            lines += [f"{name}：{rules['voting']['unstable_label']}。未删除这些用例。", ""]
        if result['repeats'] == 1:
            lines += [f"{name}：单次观察，重复一致率 N/A，不声称多数票或稳定性已验证。", ""]
        if result["repeats"] != rules["voting"]["planned_repeats"]:
            lines += [f"{name}：repeats={result['repeats']}，不满足新规则的{rules['voting']['planned_repeats']}次重复，禁止据此判新规则赢家。", ""]
    def table(headers, rows):
        lines.extend(["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"]*len(headers)) + " |"])
        lines.extend("| " + " | ".join(map(str, row)) + " |" for row in rows)
        lines.append("")
    for name, result in results.items():
        counting = "单次观测动作" if result['repeats'] == 1 else "已确定多数动作"
        lines += [f"## {name}", "", f"计数为{counting}；未知计数另列，比例范围保存在 score.json，不能当成零错误。", ""]
        if 'source_usage' in result:
            lines += ['### 原运行调用与usage（不是本次重新消费）', '']
            table(['字段', '值'], list(result['source_usage'].items()))
        table(["版本", "专家群 DISCARD", "纯噪声 KEEP", "混合群有害", "专家匹配", "未知用例", "不稳定用例"],
              [[v, *[f"{s['counts'][k]}/{s['denominators'][k]}" for k in (*v2.RISKS, "expert_correct")],
                sum(x['action'] is None for x in result['votes'][v].values()),
                'N/A' if result['repeats'] == 1 else sum(not x['stable'] for x in result['votes'][v].values())]
               for v, s in result["by_version"].items()])
        table(["对照 v0 的版本", "决策改变数/已确定配对", "未确定配对", "判定", "数值赢家（尚未批准）"],
              [[v, f"{c['decision_changes']}/{c['paired_resolved']}", c["unresolved_pairs"], c["status"], c["winner"]]
               for v, c in result["comparisons"].items()])
        table(["版本", "专家动作", "正确/总数", "未知"],
              [[v, a, f"{x['correct']}/{x['total']}", x['unresolved']]
               for v, s in result["by_version"].items() for a, x in s["recall_by_action"].items()])
        lines += ["### 分层及分数据集", ""]
        table(["版本", "分组", "专家匹配", "专家群误删", "噪声误留", "混合有害"],
              [[v, group, *[f"{s['counts'][k]}/{s['denominators'][k]}" for k in ("expert_correct", *v2.RISKS)]]
               for v, groups in result["by_group"].items() for group, s in groups.items()])
        lines += ["### 翻转用例归因与改善清单", "", "默认待复核；理由原文不是因果证明，不据此自动修改标签。", ""]
        for version, comparison in result["comparisons"].items():
            for direction in ("expert_regressions", "expert_improvements"):
                for item in comparison[direction]:
                    lines += [f"#### {version} / {direction} / {item['case_id']}", "",
                              f"专家动作：{item['expert_action']}；归因：{item['attribution']}。", ""]
                    for arm in ("baseline", "candidate"):
                        lines += [f"{arm}: {item[arm]['action']}", ""]
                        for i, rationale in zip(item[arm]['repeat_ids'], item[arm]["rationales"]):
                            lines += [f"重复 {i}：", "", '\n'.join('> '+line for line in rationale.splitlines()), ""]
        for group, extra in result.get('additional_groups', {}).items():
            lines += [f"### 新增用例组：{group}（不并入原35例、不用于胜出判定）", ""]
            table(['版本', '专家匹配', '专家群误删', '噪声误留', '混合有害'],
                  [[v, *[f"{s['counts'][k]}/{s['denominators'][k]}" for k in ('expert_correct', *v2.RISKS)]]
                   for v, s in extra['by_version'].items()])
    lines += ["## 更正记录", "", json.dumps(rules["corrections"], ensure_ascii=False, indent=2), "",
              "只做离线分析，未发送请求。新预算仍须用户填写；任何数值胜出仍须人工复核。", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--rules", type=Path, default=ROOT / "configs/curation/replay_scoring_v2.json")
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args(argv)
    rules = v2.load_rules(args.rules)
    cases_path = ROOT / rules["original_cases"]["path"]
    if v2.file_hash(cases_path) != rules["original_cases"]["sha256"]:
        raise ValueError("Original 35 cases changed")
    cases = replay.read_jsonl(cases_path)
    if len(cases) != rules["original_cases"]["count"]:
        raise ValueError("Case count changed")
    for case in cases:
        replay.contract.require_development(case["dataset_id"])
    inventory = replay.read_json(args.campaign)
    extra_groups = {}
    for group in inventory.get('additional_groups', []):
        if group['name'] in extra_groups or v2.file_hash(group['path']) != group['sha256']:
            raise ValueError('Additional-group name/hash mismatch')
        extra_groups[group['name']] = replay.read_jsonl(group['path'])
        for case in extra_groups[group['name']]:
            replay.contract.require_development(case['dataset_id'])
    runs, sources = read_runs(inventory, rules, args.rules)
    results = v2.rescore_campaign(cases, [r["id"] for r in inventory["runs"]], runs, rules, extra_groups)
    for name, result in results.items():
        if sources[name]['round'] == 1:
            for comparison in result['comparisons'].values():
                comparison.update(winner=None, status='historical_supplementary_only')
        result['source_usage'] = {k: sources[name][k] for k in ('planned_calls', 'observed_calls')}
        result['source_usage'].update(sources[name]['usage'])
    out = replay.experiment_root(args.output_root)
    out.mkdir(parents=True, exist_ok=False)
    rules_hash = v2.file_hash(args.rules)
    replay.write_new(out / "score.json", dict(scoring_sha256=rules_hash, campaign_sha256=v2.file_hash(args.campaign),
        source_runs=sources, corrections=rules["corrections"], results=results, api_calls=0,
        code_hashes={name: v2.file_hash(ROOT / name) for name in
                     ('scripts/analysis/skill_iteration_rules.py', 'scripts/analysis/score_skill_iterations.py')},
        note="Historical round remains scored by original preregistration; this is supplementary only"))
    with (out / "REPORT.md").open("x", encoding="utf-8") as f:
        f.write(render_report(results, rules, rules_hash))
    budget = replay.read_json(ROOT / rules['budget']['file'])
    replay.write_new(out / 'next_round_gate.json', dict(
        inference_authorized=False, start_allowed=False,
        reason='budget_unset' if budget['cumulative_budget_usd'] is None else 'requires_whole_round_estimate_and_separate_authorization',
        cumulative_budget_usd=budget['cumulative_budget_usd'],
        round2_original_case_calls=rules['original_cases']['count'] * len(rules['rounds']['round_2']['versions']) * rules['voting']['planned_repeats'],
        round3_original_case_calls_if_needed=rules['original_cases']['count'] * 2 * rules['voting']['planned_repeats'],
        note='No candidate skills, paid executor, token estimate or execution authorization generated.'))
    print(f"Offline report written to {out}; inference requests=0")


if __name__ == "__main__":
    main()
