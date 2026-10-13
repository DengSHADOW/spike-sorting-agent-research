"""Offline development-channel extension; reuse frozen reference policies.

No model requests, client, keys, rendering, prompt changes or historical writes.
Only new output folders; development scope checked before reading any MAT.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.analysis import run_annotated_offline_references as existing

DEFAULT_DATASETS = ("cM2-e004_004-006_CH1", "cM2-e004_011-015_CH17")


def frozen_files():
    return {**existing.frozen_files(),
            str(Path(__file__).resolve().relative_to(ROOT)): existing.sha(Path(__file__))}


def report(out, bundles, diagnostics):
    lines = ["# 开发通道：离线参照与受限 oracle", "",
        "API 调用 0，费用 $0；没有模型、图像推理、训练或 prompt 调优。",
        "除不处理的初态评分外，均执行完整 controller rollout；继承现有调度/动作，只替换决策接口。",
        "同一套既有策略、每通道随机种子 0–9，未根据通道调整规则。只用 MAT 专家终态，不回放动作表。",
        "主评分：同一 spike 索引，Hungarian 一对一、IoU ≥ 0.5，全部输出计入分母。", "",
        "| 数据集 | 不处理 / 全 KEEP F1 | 全 SPLIT F1 | 随机 F1 均值 ± 标准差 | 随机范围 | oracle F1 | oracle 匹配 |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for dataset, b in bundles.items():
        trials = {t["trial"]: t for t in b["trials"]}
        f = lambda name: trials[name]["scores"]["one_to_one"]["full_output"]["f1"]
        r = b["random_statistics"]["f1"]
        s = trials["oracle_purity_v1"]["scores"]["one_to_one"]
        lines.append(f"| {dataset} | {f('no_curation'):.4f} | {f('always_split'):.4f} | "
            f"{r['mean']:.4f} ± {r['std_population']:.4f} | {r['minimum']:.4f}–{r['maximum']:.4f} | "
            f"{f('oracle_purity_v1'):.4f} | {s['n_matched_units']}/{s['n_expert_units']} |")
    for dataset, b in bundles.items():
        lines += ["", f"## {dataset}", "",
            "| 策略 | 一对一 P | R | F1 | 匹配 units | 输出群 | 误删专家 spikes | 保留标注噪声 | 多对一 F1（辅助） |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for t in b["trials"]:
            s = t["scores"]["one_to_one"]; p = s["full_output"]
            h = t["scores"]["historical_many_to_one"]
            lines.append(f"| {t['trial']} | {p['precision']:.4f} | {p['recall']:.4f} | {p['f1']:.4f} | "
                f"{s['n_matched_units']}/{s['n_expert_units']} | {s['n_predicted_units']} | "
                f"{s['discarded_expert_spikes']} | {s['retained_reference_noise_spikes']} | {h['overall_f1_score']:.4f} |")
        o = next(t for t in b["trials"] if t["policy"] == "oracle_purity_v1")
        d = diagnostics[dataset]
        lines += ["", f"Oracle：{o['local_decisions']} 次本地判断；动作 {o['by_action']}；"
            f"Phase 1/2 = {o['by_phase'].get('phase1', 0)}/{o['by_phase'].get('phase2', 0)}。",
            f"初态已位于 label 0 的专家 spikes：{d['initial_expert_spikes_in_noise']}；"
            f"终态误删：{d['final_expert_spikes_in_noise']}；保留标注噪声：{d['final_reference_noise_retained']}。",
            "详细群成员组成、可分裂性和未合并事件见 oracle_diagnostics.json。"]
    lines += ["", "## 解释边界", "",
        "- 普通策略不能看专家标签；oracle 显式有标签特权，成绩不能当成模型或 zero-shot 成绩。",
        "- oracle 仍只能执行现有动作、使用 controller 给出的候选，不能直接写答案或恢复初态 label 0。",
        "- oracle 达到 F1=1 表明存在可达专家终态的路径；低于 1 不证明绝对上限，可能是约束或启发式不足。",
        "- 零分意味着没有通过匹配门槛，不等于没有保留专家 spikes。全 SPLIT 可能因碎片化得零分。",
        "- 不处理与全 KEEP 必须完全相同；随机报告全部十个种子，不挑最好结果。",
        "- 所有通道属于已曝光开发块，不是独立 held-out 泛化；专家标注也不等于生物学 ground truth。",
        "- 没有改 controller、skill、评分规则、原 MAT 或先前结果；不自动发起付费实验。"]
    with (out / "REPORT.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def run(out, datasets=DEFAULT_DATASETS):
    out = Path(out)
    if out.exists():
        raise FileExistsError(out)
    ref = existing.ref
    with ref.offline_only():
        rows = ref.stage6.selected_rows(list(datasets))  # Reject every forbidden scope BEFORE loading data.
        hashes, configs = frozen_files(), ref.config_hashes()
        out.mkdir(parents=True, exist_ok=False)
        existing.write(out / "manifest.json", dict(version="development-offline-reference-extension-v1",
            dataset_order=list(datasets), datasets=rows, policies=ref.POLICIES,
            random_seeds=list(existing.SEEDS), code_hashes=hashes, config_hashes=configs,
            environment=ref.stage6.environment(), index_sha256=existing.sha(ref.stage6.INDEX),
            model=None, api_calls=0, api_cost_usd=0,
            implementation="Unmodified run_annotated_offline_references.run_dataset and CH31 policy/controller",
            visibility="Only oracle and evaluators receive MAT terminal labels; no action-sheet input",
            ordinary_gt_tuning=False, max_local_decisions_per_trial=10000))
        bundles, diagnostics = {}, {}
        status, error = "stopped", None
        try:
            for row in rows:
                dataset = row["dataset_id"]
                bundles[dataset], diagnostics[dataset] = existing.run_dataset(row, out / "datasets" / dataset)
            if hashes != frozen_files() or configs != ref.config_hashes():
                raise ValueError("Frozen code or configuration changed")
            if any(existing.sha(ROOT / r["mat_path"]) != r["mat_sha256"] for r in rows):
                raise ValueError("MAT changed")
            existing.write(out / "oracle_diagnostics.json", diagnostics)
            report(out, bundles, diagnostics)
            existing.write(out / "summary.json", dict(status="completed", api_calls=0, api_cost_usd=0,
                manifest_sha256=existing.sha(out / "manifest.json"), datasets=bundles,
                total_evaluations=len(rows) * len(existing.trial_specs()),
                controller_rollouts=len(rows) * (len(existing.trial_specs()) - 1),
                initial_state_scores=len(rows), all_terminal_scores_reverified=True))
            status = "completed"
        except BaseException as exc:
            error = dict(type=type(exc).__name__, message=str(exc))
            raise
        finally:
            existing.write(out / "execution_status.json", dict(status=status, error=error,
                completed_datasets=list(bundles), api_calls=0, api_cost_usd=0))
        print(f"COMPLETE {list(bundles)}; API=0; {out}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--datasets", nargs="+", default=list(DEFAULT_DATASETS))
    args = parser.parse_args()
    run(existing.ref.stage6.output_path(args.output_root), args.datasets)


if __name__ == "__main__":
    main()
