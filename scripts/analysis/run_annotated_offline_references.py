"""Four annotated datasets: offline policy references, reusing frozen CH31.

No paid calls, no model, no prompt change. Original CH31 runner/results remain
immutable. Only the other three datasets execute new controller trajectories.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from time import monotonic

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.analysis import run_ch31_offline_references as ref

DATASETS = ("cM2-e004_004-006_CH3", "cM2-e004_011-015_CH20",
            "cM2-e008_021-028_CH30", "cM2-e004_004-006_CH31")
SEEDS = ref.DEFAULT_SEEDS
read = ref.stage6.replay.read_json
write = ref.write_json
sha = ref.sha256


def trial_specs():
    return [("no_curation", None), ("always_keep", None), ("always_split", None)] + [
        ("random", seed) for seed in SEEDS] + [("oracle_purity_v1", None)]


def trial_name(name, seed):
    return name if seed is None else f"{name}_seed_{seed}"


def validate_scope(ids):
    if tuple(ids) != DATASETS:
        raise ValueError("Scope/order must be exactly the four annotated datasets")


def inventory(folder):
    return {str(p.relative_to(folder)): sha(p) for p in sorted(folder.rglob("*")) if p.is_file()}


def frozen_files():
    return {**ref.frozen_files(), str(Path(__file__).resolve().relative_to(ROOT)): sha(Path(__file__))}


def load_data(row):
    data = ref.stage6.load_matlab_spikes(str(ROOT / row["mat_path"]))
    gt = data.get("curation_assigns")
    if gt is None:
        raise ValueError("Missing MAT expert terminal")
    ref.curation_one_to_one.evaluate(data["hierarchy_assigns"], gt)
    return data


def terminal(folder, trial):
    path = folder / trial["trial"] / "terminal.npz"
    if sha(path) != trial["terminal_sha256"]:
        raise ValueError("Saved terminal hash changed")
    with np.load(path, allow_pickle=False) as state:
        pred = state["assigns"].copy()
    return pred


def oracle_diagnostics(folder, trial, data):
    path = folder / trial["trial"] / "terminal.npz"
    with np.load(path, allow_pickle=False) as state:
        pred = state["assigns"].copy()
        manager = ref.fresh(data)
        manager.assigns = pred.copy()
        manager.hierarchy_tree = state["tree"].copy()
        manager._build_cluster_composition()
    gt = data["curation_assigns"]
    clusters = []
    for cid in manager.get_active_clusters():
        mask = pred == cid
        labels, counts = np.unique(gt[mask], return_counts=True)
        composition = {str(int(k)): int(v) for k, v in zip(labels, counts)}
        clusters.append(dict(cluster_id=cid, n_spikes=int(mask.sum()),
            reference_composition=composition, pure_single_expert_unit=len(labels) == 1 and labels[0] > 0,
            pure_reference_noise=len(labels) == 1 and labels[0] == 0,
            split_executable_at_terminal=ref.split_available(manager, cid),
            large_by_controller_threshold=bool(mask.sum() >= 4000)))
    # Cast numpy bools for strict JSON serialization.
    for c in clusters:
        c["pure_single_expert_unit"] = bool(c["pure_single_expert_unit"])
        c["pure_reference_noise"] = bool(c["pure_reference_noise"])
    events = [json.loads(x) for x in (folder/trial["trial"]/"events.jsonl").read_text().splitlines()]
    return dict(exact_label_array_equal=np.array_equal(pred, gt),
        initial_expert_spikes_in_noise=int(np.count_nonzero((gt > 0) & (data["hierarchy_assigns"] == 0))),
        final_expert_spikes_in_noise=int(np.count_nonzero((gt > 0) & (pred == 0))),
        final_reference_noise_retained=int(np.count_nonzero((gt == 0) & (pred > 0))),
        phase_counts=trial["by_phase"], action_counts=trial["by_action"], operations=trial["operations"],
        terminal_clusters=clusters,
        preserved_unresolved=[e for e in events if e["event"] == "preserved_unresolved"],
        preserved_ineligible=[e for e in events if e["event"] == "preserved_ineligible"],
        note="Current-state constraints and heuristic outcome; non-perfect oracle is not an optimality proof.")


def verify_saved(folder, row, *, require_legacy=False):
    manifest, summary = read(folder/"manifest.json"), read(folder/"summary.json")
    if (manifest["dataset"] != row or manifest["policies"] != ref.POLICIES
            or manifest["random_seeds"] != list(SEEDS)
            or summary["manifest_sha256"] != sha(folder/"manifest.json")
            or summary["status"] != "completed" or summary["dataset_id"] != row["dataset_id"]
            or summary["api_calls"] != 0 or summary["api_cost_usd"] != 0):
        raise ValueError("Saved scope/policies/seeds/status mismatch")
    if require_legacy and manifest["version"] != "ch31-offline-reference-v1":
        raise ValueError("Expected original CH31 manifest")
    if (manifest["config_hashes"] != ref.config_hashes()
            or any(sha(ROOT / p) != h for p, h in manifest["code_hashes"].items())
            or manifest["environment"] != ref.stage6.environment()):
        raise ValueError("Frozen code/config/environment changed")
    expected = [trial_name(n, s) for n, s in trial_specs()]
    if [r["trial"] for r in summary["trials"]] != expected:
        raise ValueError("Missing/extra/out-of-order policy trials")
    data = load_data(row)
    if (manifest["initial_sha256"] != ref.arrays_hash(data["hierarchy_assigns"])
            or manifest["reference_sha256"] != ref.arrays_hash(data["curation_assigns"])):
        raise ValueError("Saved input identity changed")
    for trial, (name, seed) in zip(summary["trials"], trial_specs()):
        if (trial != read(folder/trial["trial"]/"score.json") or trial["status"] != "completed"
                or trial["policy"] != name or trial["seed"] != seed
                or trial["labels_visible"] != (name == "oracle_purity_v1")
                or trial["api_calls"] != 0 or trial["api_cost_usd"] != 0):
            raise ValueError("Saved trial metadata mismatch")
        pred = terminal(folder, trial)
        if ref.score(pred, data) != trial["scores"]:
            raise ValueError("Recomputed scores disagree")
        if name in {"no_curation", "always_keep"} and not np.array_equal(pred, data["hierarchy_assigns"]):
            raise ValueError("Preserve policy changed input")
        if name != "no_curation":
            state_summary = read(folder/trial["trial"]/"summary.json")
            if state_summary["status"] != "completed" or not state_summary["terminal_evaluation_allowed"]:
                raise ValueError("Partial trajectory cannot be scored")
    if ref.random_statistics(summary["trials"]) != summary["random_statistics"]:
        raise ValueError("Random aggregate changed")
    oracle = next(t for t in summary["trials"] if t["policy"] == "oracle_purity_v1")
    return summary, oracle_diagnostics(folder, oracle, data)


def run_dataset(row, out):
    data = load_data(row)
    out.mkdir(parents=True, exist_ok=False)
    write(out/"manifest.json", dict(version="annotated-offline-reference-v1", dataset=row,
        policies=ref.POLICIES, random_seeds=list(SEEDS), code_hashes=frozen_files(),
        config_hashes=ref.config_hashes(), environment=ref.stage6.environment(),
        initial_sha256=ref.arrays_hash(data["hierarchy_assigns"]),
        reference_sha256=ref.arrays_hash(data["curation_assigns"]),
        model=None, api_calls=0, api_cost_usd=0, max_local_decisions_per_trial=10000,
        semantics="Same CH31 Policy and OfflineController; no skill/image/model calls; MAT terminal reference only"))
    rows = []
    for name, seed in trial_specs():
        label = trial_name(name, seed)
        folder = out / label
        started = monotonic()
        if name == "no_curation":
            folder.mkdir()
            pred = data["hierarchy_assigns"].copy()
            with (folder/"terminal.npz").open("xb") as stream:
                np.savez_compressed(stream, assigns=pred)
            calls, operations, phases, actions = 0, 0, {}, {}
        else:
            policy = ref.Policy(name, seed=seed,
                                reference=data["curation_assigns"] if name == "oracle_purity_v1" else None)
            c = ref.OfflineController(ref.fresh(data), row["dataset_id"], folder,
                                      policy=policy, sampling_rate=data["Fs"])
            pred = c.run()
            if name == "always_keep" and not np.array_equal(pred, data["hierarchy_assigns"]):
                raise AssertionError("KEEP changed input")
            events = [json.loads(x) for x in (folder/"events.jsonl").read_text().splitlines()]
            decisions = [x for x in events if x["event"] == "decision"]
            calls, operations = c.calls, c.operations
            phases = dict(Counter(x["phase"] for x in decisions))
            actions = dict(Counter(x["action"] for x in decisions))
        result = dict(trial=label, policy=name, seed=seed, status="completed",
            local_decisions=calls, operations=operations, by_phase=phases, by_action=actions,
            labels_visible=name == "oracle_purity_v1", seconds=monotonic()-started,
            api_calls=0, api_cost_usd=0, terminal_sha256=sha(folder/"terminal.npz"), scores=ref.score(pred, data))
        write(folder/"score.json", result)
        rows.append(result)
        print(f"COMPLETE {row['dataset_id']} / {label}: "
              f"F1={result['scores']['one_to_one']['full_output']['f1']:.6f}; API=0", flush=True)
    identity = ref.score(data["curation_assigns"].copy(), data)
    assert identity["one_to_one"]["full_output"]["f1"] == 1
    write(out/"summary.json", dict(status="completed", dataset_id=row["dataset_id"], api_calls=0, api_cost_usd=0,
        manifest_sha256=sha(out/"manifest.json"), trials=rows, random_statistics=ref.random_statistics(rows),
        scorer_identity_check=identity, original_input_and_code_unchanged=True))
    return verify_saved(out, row)


def report(out, bundles, diagnostics):
    lines = ["# 四个 annotated 数据集：离线参照与受限 oracle", "",
        "API 调用 0；API 费用 $0。CH31 复用并复核原结果，CH3/20/30 新运行；全部使用同一策略与随机种子 0–9。",
        "同一 Stage 6 调度/动作执行与快照；只替换决策接口。没有模型、prompt、图像渲染或训练。",
        "只用各 MAT 专家终态评分，不把动作表与终态混作统一答案。oracle 是标签特权诊断，不是模型成绩。",
        "", "## 主结果：一对一 F1", "",
        "固定 Hungarian、IoU ≥ 0.5、全部输出计入分母；不是按匹配群筛选后的高分。",
        "", "| 通道 | 不处理 | 全 KEEP | 合法拆到叶子 | 随机均值 ± 标准差 | 随机范围 | 受限 oracle | oracle 匹配 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for dataset in DATASETS:
        b = bundles[dataset]; ts = {t["trial"]: t for t in b["trials"]}
        f = lambda p: ts[p]["scores"]["one_to_one"]["full_output"]["f1"]
        r = b["random_statistics"]["f1"]; o = ts["oracle_purity_v1"]["scores"]["one_to_one"]
        lines.append(f"| {dataset.split('_')[-1]} | {f('no_curation'):.4f} | {f('always_keep'):.4f} | "
            f"{f('always_split'):.4f} | {r['mean']:.4f} ± {r['std_population']:.4f} | "
            f"{r['minimum']:.4f}–{r['maximum']:.4f} | {f('oracle_purity_v1'):.4f} | "
            f"{o['n_matched_units']}/{o['n_expert_units']} |")
    lines += ["", "## Oracle 的实际终态与限制", "",
        "| 通道 | P | R | F1 | 输出群 | 误删专家 spikes | 保留标注噪声 | Phase 1 / 2 判断 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for dataset in DATASETS:
        t = next(t for t in bundles[dataset]["trials"] if t["policy"] == "oracle_purity_v1")
        s = t["scores"]["one_to_one"]; p = s["full_output"]
        lines.append(f"| {dataset.split('_')[-1]} | {p['precision']:.4f} | {p['recall']:.4f} | {p['f1']:.4f} | "
            f"{s['n_predicted_units']} | {s['discarded_expert_spikes']} | {s['retained_reference_noise_spikes']} | "
            f"{t['by_phase'].get('phase1',0)} / {t['by_phase'].get('phase2',0)} |")
    lines += ["", "达到 1 说明该通道专家终态在现有执行路径下确实可达；低于 1 只是当前纯度启发式的可达结果，"
              "不证明框架的绝对天花板。详细终态 composition、可分裂性、候选保留事件见 oracle_diagnostics.json。",
              "如果没有 Phase 2 判断，不能据此宣称真实合并调度也经过了验证。", "",
              "## 每通道完整策略结果（历史多对一另列）"]
    for dataset in DATASETS:
        lines += ["", f"### {dataset}", "",
            "| 策略 | 一对一 P | R | F1 | 匹配 units | 输出群 | 误删专家 spikes | 保留标注噪声 | 多对一 F1 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for t in bundles[dataset]["trials"]:
            s=t["scores"]["one_to_one"]; p=s["full_output"]; h=t["scores"]["historical_many_to_one"]
            lines.append(f"| {t['trial']} | {p['precision']:.4f} | {p['recall']:.4f} | {p['f1']:.4f} | "
                f"{s['n_matched_units']}/{s['n_expert_units']} | {s['n_predicted_units']} | "
                f"{s['discarded_expert_spikes']} | {s['retained_reference_noise_spikes']} | {h['overall_f1_score']:.4f} |")
    lines += ["", "## 实验边界", "",
        "- 不处理与全 KEEP 终态数组必须一致；两行不是独立样本。",
        "- 随机仅选择合法动作，固定全部十个种子，不挑最好值；标准差描述单通道种子波动，不是泛化置信区间。",
        "- oracle 只能对当前对象返回动作，不覆盖 assigns，不调整 4,000 分组阈值，不补充任意配对，不恢复初态 label 0。",
        "- 普通策略不接收专家标签；oracle/评分器才访问标签。专家 MAT 标注不是生物学 ground truth。",
        "- 一直 SPLIT 可能把神经元切碎；spike 保留率、多对一 recall 与一对一 unit 恢复不是同一件事。",
        "- CH3 与 CH31 来自同一 recording block；这是已曝光开发数据的诊断，不能当独立 held-out 泛化。",
        "- 不与 CH1 分数跨通道比较，不启动任何付费实验，不修改原始 MAT、冻结 skill/controller 或 CH31 历史结果。"]
    (out/"REPORT.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


def run(out, reuse_ch31):
    validate_scope(DATASETS)
    if out == reuse_ch31 or out.is_relative_to(reuse_ch31) or reuse_ch31.is_relative_to(out):
        raise ValueError("New output must not overlap CH31 source")
    with ref.offline_only():
        rows = ref.stage6.selected_rows(list(DATASETS))  # validate entire scope before any sample read
        by_id = {r["dataset_id"]: r for r in rows}
        old_files = inventory(reuse_ch31)
        reused, old_diagnostics = verify_saved(reuse_ch31, by_id[ref.DATASET], require_legacy=True)
        out.mkdir(parents=True, exist_ok=False)
        hashes, configs = frozen_files(), ref.config_hashes()
        write(out/"manifest.json", dict(version="four-annotated-offline-reference-v1",
            dataset_order=list(DATASETS), datasets=rows, policies=ref.POLICIES, random_seeds=list(SEEDS),
            code_hashes=hashes, config_hashes=configs, environment=ref.stage6.environment(),
            reused_ch31=str(reuse_ch31), reused_source_files=old_files, api_calls=0, api_cost_usd=0,
            reuse_contract="Full terminal hashes and both scoring modes recomputed; original CH31 never written",
            source_outputs="CH31 points to reused source; all new datasets stored under datasets/"))
        bundles={ref.DATASET:reused}; diagnostics={ref.DATASET:old_diagnostics}
        status, error = "stopped", None
        try:
            for row in rows:
                if row["dataset_id"] == ref.DATASET:
                    continue
                folder=out/"datasets"/row["dataset_id"]
                b,d = run_dataset(row,folder)
                bundles[row["dataset_id"]]=b; diagnostics[row["dataset_id"]]=d
            if hashes != frozen_files() or configs != ref.config_hashes() or old_files != inventory(reuse_ch31):
                raise ValueError("Frozen files or reused source changed")
            if any(sha(ROOT/r["mat_path"]) != r["mat_sha256"] for r in rows):
                raise ValueError("MAT changed")
            write(out/"oracle_diagnostics.json",diagnostics)
            write(out/"summary.json",dict(status="completed",api_calls=0,api_cost_usd=0,
                manifest_sha256=sha(out/"manifest.json"),datasets=bundles,
                reused_dataset=ref.DATASET,new_trajectories=3*len(trial_specs()),
                total_trajectories=len(DATASETS)*len(trial_specs()),all_terminal_scores_reverified=True))
            report(out,bundles,diagnostics)
            status="completed"
        except BaseException as exc:
            error=dict(type=type(exc).__name__,message=str(exc))
            raise
        finally:
            write(out/"execution_status.json",dict(status=status,error=error,
                completed_datasets=list(bundles),api_calls=0,api_cost_usd=0))
        print(f"COMPLETE four datasets; 42 new + 14 reused trajectories; API=0; {out}",flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root",required=True)
    parser.add_argument("--reuse-ch31",default="output/ch31_offline_references_20261012")
    args=parser.parse_args()
    run(ref.stage6.output_path(args.output_root),ref.stage6.output_path(args.reuse_ch31))


if __name__ == "__main__":
    main()
