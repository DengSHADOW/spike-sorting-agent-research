"""Offline paired plot report. No automatic adoption or extra inference."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from scripts.run import run_skill_replay as r
from scripts.run import run_image_ab as image_ab
from scripts.analysis.score_skill_replay import summarize


def score(cases,jobs,answers,rules):
    by_case={c['case_id']:c for c in cases}
    if set(answers)!={j['job_id'] for j in jobs}: raise r.ReplayStop('Complete70 responses required')
    rows=[]
    for job in jobs:
        case=by_case[job['case_id']]; answer=answers[job['job_id']]
        if answer['action'] not in r.contract.ACTIONS[case['phase']]: raise r.ReplayStop('Invalid action')
        row={**job,**answer}
        if case['target_type']=='recorded_expert_edit':
            row.update(expert_action=case['expert_action'],correct=answer['action']==case['expert_action'])
        else:
            constraint=case['constraint']; rule=rules['constraints'][constraint]
            category=[k.removesuffix('_actions') for k in ('harmful_actions','preferred_actions','tolerable_not_ideal_actions','acceptable_actions') if answer['action'] in rule[k]]
            if len(category)!=1: raise r.ReplayStop('Ambiguous scoring category')
            row.update(constraint=constraint,category=category[0],harmful=category[0]=='harmful')
        rows.append(row)
    totals={}; strata={}; datasets={}
    for arm in ('raw','summary'):
        selected=[x for x in rows if x['version']==arm]
        totals[arm]=summarize(selected,rules)
        groups={}; by_dataset={}
        for x in selected:
            key='/'.join([x['target_type'],x['phase'],x['image_layout'],str(x['image_count'])])
            groups.setdefault(key,[]).append(x); by_dataset.setdefault(x['dataset_id'],[]).append(x)
        strata[arm]={k:summarize(v,rules) for k,v in groups.items()}
        datasets[arm]={k:summarize(v,rules) for k,v in by_dataset.items()}
    pairs={(x['case_id'],x['version']):x for x in rows}
    flips=[]
    for cid in by_case:
        a,b=pairs[(cid,'raw')],pairs[(cid,'summary')]
        if a['action']!=b['action']: flips.append(dict(case_id=cid,raw=a,summary=b))
    return dict(totals=totals,strata=strata,datasets=datasets,decision_changes=len(flips),flips=flips,
                repeats=1,repeat_consistency=None,automatic_adoption=False,
                disclaimer='Targeted dependent35 cases; descriptive paired comparison only, not general accuracy, significance or rollout performance.')


def report(result):
    lines=['# Waveform image A/B — 单次诊断','',
        '定向、相互关联的35例，仅作诊断。三图历史与四图专家用例分层，不合并成单一准确率。',
        '固定skill v0。raw=确定性抽样原始波形；summary=同样原始波形＋全群中位线/10–90%范围带，Phase2统一纵轴。',
        '两组数值均重新计算且相同，非波形PNG保持原样（可能仍显示历史计算标签）；不与旧skill实验直接作因果比较。',
        '不是有图/无图对照，不是完整闭环；单次观察，不估计重复稳定性、不自动替换画法。','',
        '| 画法 | Phase1专家匹配 | Phase2专家匹配 | 约束有害动作 |','| --- | --- | --- | --- |']
    for arm,s in result['totals'].items():
        e=s['expert']['by_phase']; d=s['derived']
        lines.append(f"| {arm} | {e['phase1']['correct']}/{e['phase1']['observed']} | {e['phase2']['correct']}/{e['phase2']['observed']} | {d['harmful']}/{d['observed']} |")
    lines += ['',f"决策改变：{result['decision_changes']}/35。",'', '## 各约束及专家动作','']
    for arm,s in result['totals'].items():
        lines += [f'### {arm}','']
        for kind,x in s['derived']['by_constraint'].items(): lines += [f"- {kind}: 有害{x['harmful']}/{x['observed']}。"]
        for action,x in s['expert']['recall_by_action'].items(): lines += [f"- {action}: 匹配{x['correct']}/{x['observed']}。"]
        lines += ['']
    lines += ['## 用例分层与数据集','']
    for arm,groups in result['strata'].items():
        for name,s in groups.items():
            lines += [f"- {arm} / {name}: 专家{s['expert']['correct']}/{s['expert']['observed']}，有害{s['derived']['harmful']}/{s['derived']['observed']}。"]
    lines += ['']
    for arm,groups in result['datasets'].items():
        for name,s in groups.items():
            lines += [f"- {arm} / {name}: 专家{s['expert']['correct']}/{s['expert']['observed']}，有害{s['derived']['harmful']}/{s['derived']['observed']}。"]
    lines += ['', '## 全部翻转案例与模型理由原文','', '理由不等于因果证明，未据此更改标签。','']
    for x in result['flips']:
        lines += [f"### {x['case_id']}",'']
        for arm in ('raw','summary'):
            row=x[arm]
            target=row.get('expert_action',row.get('constraint'))
            lines += [f"{arm}: {row['action']}；参照={target}。",'', '\n'.join('> '+line for line in row['rationale'].splitlines()),'']
    lines += ['## 调用与费用','',str(result.get('usage',{})),'','费用按usage估算，不是账单；没有附加推理。']
    return '\n'.join(lines)+'\n'


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,required=True); p.add_argument('--output-root',type=Path,required=True)
    p.add_argument('--wait',action='store_true'); args=p.parse_args(argv)
    run,out=r.experiment_root(args.run_dir),r.experiment_root(args.output_root)
    if out.exists(): raise r.ReplayStop('Report output must be new')
    while not(run/'execution_status.json').exists():
        if not args.wait: raise r.ReplayStop('Execution not complete')
        time.sleep(10)
    time.sleep(1)
    status=r.read_json(run/'execution_status.json')
    if status['state']!='completed':
        out.mkdir(parents=True,exist_ok=False); r.write_new(out/'STOP.json',status)
        print('STOP archived; no model conclusion, no automatic retry',flush=True); return
    spec,_,cases=image_ab.verify(run)
    jobs={j['job_id']:j for j in spec['jobs']}; answers={}; bills=[]
    for item in r.read_jsonl(run/'responses.jsonl'):
        job=jobs[item['job_id']]
        if item['job_id'] in answers or item['request_sha256']!=job['request_sha256']: raise r.ReplayStop('Duplicate or mismatched response')
        answers[item['job_id']]=r.decision_from_response(item['response'],job['phase'])
        bills.append(r.usage_cost(item['response']))
    result=score(cases,spec['jobs'],answers,r.read_json(r.SCORING))
    result.update(manifest_sha256=r.file_hash(run/'manifest.json'),scoring_sha256=spec['scoring_sha256'],
        protocol_sha256=spec['protocol_sha256'],responses_sha256=r.file_hash(run/'responses.jsonl'),
        usage={**{k:sum(x[k] for x in bills) for k in bills[0]},'calls':len(bills)})
    out.mkdir(parents=True,exist_ok=False); r.write_new(out/'score.json',result)
    with (out/'REPORT.md').open('x') as f: f.write(report(result))
    print('Image A/B report complete:',out,flush=True)


if __name__=='__main__': main()
