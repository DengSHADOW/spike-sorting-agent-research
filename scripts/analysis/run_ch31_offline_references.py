"""Offline CH31 reference policies through the unchanged Stage 6 scheduler/executor.

No model, prompt, image generation, API client, keys, or network. The oracle has
privileged final labels; its achieved score is NOT an optimality certificate.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack, contextmanager
import json
from pathlib import Path
import socket
import sys
from time import monotonic
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.run import run_curation_stage6 as stage6
from scripts.analysis.collect_curation_stage6 import evaluate as historical_evaluate
from scripts.analysis import curation_one_to_one
from src.cluster.manager import ClusterManager
from src.pipeline.curation_engineering_v1 import CurationController, ControllerStop, arrays_hash, write_json
from src.agent.curation_contract import config_hashes, sha256

DATASET = "cM2-e004_004-006_CH31"
DEFAULT_SEEDS = tuple(range(10))
POLICIES = {
    "no_curation": "Score the initial assignments without executing a controller.",
    "always_keep": "Phase 1 KEEP; Phase 2 NOT_MERGE. Must equal no_curation.",
    "always_split": "Phase 1 SPLIT iff executable, otherwise KEEP; Phase 2 NOT_MERGE.",
    "random": "Uniform over executable phase actions; frozen independent seeds; report every seed.",
    "oracle_purity_v1": (
        "Privileged final labels. Phase 1: pure noise DISCARD, pure single expert unit KEEP, "
        "mixed SPLIT iff executable, otherwise KEEP. Phase 2: pure noise source DISCARD; "
        "both pure fragments of the same expert unit MERGE; otherwise NOT_MERGE. "
        "No arbitrary assignment edits, no candidate override, no ground-truth-guided threshold tuning. "
        "Feasible label-guided reference, NOT a proven optimum; a score of 1 would attain the metric bound."
    ),
}


def forbidden(*args, **kwargs):
    raise RuntimeError("Offline reference experiment: network/client/secret loading forbidden")


@contextmanager
def offline_only():
    with ExitStack() as stack:
        for target in ("socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex",
                       "scripts.run.run_curation_stage6.make_client",
                       "scripts.run.run_skill_replay.client_factory", "dotenv.load_dotenv"):
            stack.enter_context(patch(target, forbidden))
        yield


def split_available(m, cid):
    """Mirror last-merge eligibility, without mutating the manager or using labels."""
    idx = np.flatnonzero(m.hierarchy_tree[0] == cid)
    if not len(idx):
        return False
    last = int(idx[-1])
    child = int(m.hierarchy_tree[1, last])
    if child <= 0 or child in m.get_active_clusters():
        return False
    branch = {child}
    for i in range(last - 1, -1, -1):
        src, other = map(int, m.hierarchy_tree[:2, i])
        if src in branch:
            branch.add(other)
    present = set(m.cluster_composition[cid])
    return bool(present & branch) and bool(present - branch)


def legal_actions(m, phase, cid, target=None):
    active = m.get_active_clusters()
    if cid not in active:
        raise ValueError("Inactive source")
    if phase == "phase1":
        return ["KEEP", "DISCARD"] + (["SPLIT"] if split_available(m, cid) else [])
    if phase == "phase2" and target in active and target != cid:
        return ["MERGE", "NOT_MERGE", "DISCARD"]
    raise ValueError("Unknown phase or invalid target")


class Policy:
    kind = "OFFLINE_RULE_NOT_A_MODEL"
    def __init__(self, name, *, seed=None, reference=None):
        if name not in POLICIES or name == "no_curation":
            raise ValueError("Unknown executable policy")
        if (name == "oracle_purity_v1") != (reference is not None):
            raise ValueError("Only the explicit oracle may receive reference labels")
        if name == "random" and (type(seed) is not int or seed < 0):
            raise ValueError("Random policy needs a nonnegative integer seed")
        self.name, self.seed = name, seed
        self.reference = None if reference is None else np.asarray(reference).copy()
        self.rng = np.random.default_rng(seed) if name == "random" else None

    def composition(self, m, cid):
        labels = self.reference[m.assigns == cid]
        return {str(int(k)): int(v) for k, v in zip(*np.unique(labels, return_counts=True))}

    def choose(self, m, phase, cid, target, allowed):
        if self.name == "always_keep":
            return ("KEEP" if phase == "phase1" else "NOT_MERGE"), "Fixed preserve policy"
        if self.name == "always_split":
            return ("SPLIT" if "SPLIT" in allowed else
                    "KEEP" if phase == "phase1" else "NOT_MERGE"), "Split only when executable; never merge"
        if self.name == "random":
            return str(self.rng.choice(allowed)), "Uniform executable-action policy"
        if self.reference.shape != m.assigns.shape:
            raise ValueError("Oracle spike-index mismatch")
        labels = set(self.composition(m, cid))
        if labels == {"0"}:
            return "DISCARD", "ORACLE: source contains only reference noise"
        if phase == "phase1":
            if len(labels) == 1:
                return "KEEP", "ORACLE: pure fragment of one expert unit"
            if "SPLIT" in allowed:
                return "SPLIT", "ORACLE: mixed reference labels; legal hierarchical split"
            return "KEEP", "ORACLE: mixed but no executable split; preserve (heuristic, not optimal)"
        other = set(self.composition(m, target))
        if len(labels) == 1 and labels == other:
            return "MERGE", "ORACLE: two pure fragments of the same expert unit"
        return "NOT_MERGE", "ORACLE: not two pure fragments of the same expert unit"


class OfflineController(CurationController):
    """Override the decision interface ONLY; inherit run/apply/checkpoint unchanged."""
    def __init__(self, manager, dataset_id, out, *, policy, sampling_rate=30000., max_calls=10000):
        self.policy = policy
        super().__init__(manager, dataset_id, out, provider=policy, budget_usd=0.,
                         max_calls=max_calls, sampling_rate=sampling_rate)
        write_json(self.out / "offline_policy.json", dict(
            policy=policy.name, seed=policy.seed, labels_visible=policy.reference is not None,
            model=None, skill_used=False, observations_rendered=False, api_calls=0, api_cost_usd=0,
            note="Inherited manifest skill field is constructor metadata; no prompt is built or read by policy. "
                 "calls counts local decisions, not API calls. Not a VLM/plotting validation."))

    def ask(self, phase, cid, target=None, *, context=None):
        if self.status in {"stopped", "completed"}:
            raise ControllerStop("Finished controller")
        self.status = "running"
        try:
            key = (context or phase, int(cid), target, self.state_hash())
            if key in self.seen or self.calls >= self.max_calls:
                raise ControllerStop("Repeated decision state or local decision limit")
            if config_hashes() != self.frozen:
                raise ControllerStop("Frozen config changed")
            allowed = legal_actions(self.manager, phase, cid, target)
            action, reason = self.policy.choose(self.manager, phase, cid, target, allowed)
            if action not in allowed:
                raise ValueError("Policy chose non-executable action")
            privileged = {}
            if self.policy.reference is not None:
                privileged = dict(reference_composition=self.policy.composition(self.manager, cid))
                if target is not None:
                    privileged["target_reference_composition"] = self.policy.composition(self.manager, target)
            self.calls += 1
            self.seen.add(key)
            self.log("decision", phase=phase, context=context, cid=int(cid), target=target,
                     action=action, rationale=reason, allowed_actions=allowed,
                     state_sha256=self.state_hash(), policy=self.policy.name,
                     labels_visible=self.policy.reference is not None, **privileged)
            return dict(action=action, rationale=reason)
        except Exception:
            self.status = "stopped"
            self.log("offline_policy_error", phase=phase, cid=int(cid), state_sha256=self.state_hash())
            raise


def fresh(data):
    return ClusterManager(data["hierarchy_assigns"], data["overcluster_assigns"],
                          data["hierarchy_tree"].copy(), data["spiketimes"], data["waveforms"])


def score(pred, data):
    gt = data["curation_assigns"]
    return dict(one_to_one=curation_one_to_one.evaluate(pred, gt),
                historical_many_to_one=historical_evaluate(pred, gt, data["spiketimes"], data["Fs"]))


def frozen_files():
    files = [Path(__file__), Path(stage6.__file__), ROOT / "src/pipeline/curation_engineering_v1.py",
             ROOT / "src/cluster/manager.py", ROOT / "src/io/matlab_loader.py",
             ROOT / "scripts/analysis/curation_one_to_one.py",
             ROOT / "scripts/analysis/collect_curation_stage6.py", ROOT / "src/eval/metrics.py",
             ROOT / "src/agent/curation_contract.py", curation_one_to_one.RULES]
    return {str(p.relative_to(ROOT)): sha256(p) for p in files}


def random_statistics(rows):
    randoms = [r for r in rows if r["policy"] == "random"]
    result = {}
    for metric in ("precision", "recall", "f1"):
        values = [r["scores"]["one_to_one"]["full_output"][metric] for r in randoms]
        result[metric] = dict(mean=float(np.mean(values)), std_population=float(np.std(values)),
                              minimum=float(min(values)), maximum=float(max(values)))
    return result


def report(out, rows, stats, sanity):
    lines = ["# CH31 离线简单策略与受限 oracle", "",
        "仅 cM2-e004_004-006_CH31。API 调用 0、费用 $0，无模型/密钥/网络。", "",
        "策略、随机种子在执行前写入 manifest。完整继承 Stage 6 的调度、动作执行与快照；"
        "只把模型决策接口替换为本地策略。跳过不被这些策略使用的图片/数值观测，不验证 VLM 或画图。", "",
        "主评分：同一 spike 索引、一对一 Hungarian、IoU ≥ 0.5、全部输出计入分母。"
        "专家标签不是生物学 ground truth；未配对群不被丢出评分。", "",
        "| 策略 | P | R | F1 | 匹配专家群 | 输出群 | 纯标注噪声群 | 误删专家 spikes | 保留标注噪声 spikes | 本地判断数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        s = r["scores"]["one_to_one"]; p = s["full_output"]
        lines.append(f"| {r['trial']} | {p['precision']:.4f} | {p['recall']:.4f} | {p['f1']:.4f} | "
                     f"{s['n_matched_units']}/{s['n_expert_units']} | {s['n_predicted_units']} | "
                     f"{s['n_pure_reference_noise_clusters']} | {s['discarded_expert_spikes']} | "
                     f"{s['retained_reference_noise_spikes']} | {r['local_decisions']} |")
    f = stats["f1"]
    lines += ["", f"随机：预先固定种子 {list(r['seed'] for r in rows if r['policy'] == 'random')}；"
              f"F1 均值 {f['mean']:.4f}，标准差 {f['std_population']:.4f}，"
              f"范围 {f['minimum']:.4f}–{f['maximum']:.4f}。不挑最好种子，不解释为独立数据集上的置信区间。",
        "", "## 历史多对一（辅助，不能与一对一混用）", "",
        "| 策略 | P | R | F1 |", "|---|---:|---:|---:|"]
    for r in rows:
        s = r["scores"]["historical_many_to_one"]
        lines.append(f"| {r['trial']} | {s['overall_precision']:.4f} | {s['overall_recall']:.4f} | {s['overall_f1_score']:.4f} |")
    o = next(r for r in rows if r["policy"] == "oracle_purity_v1")["scores"]["one_to_one"]
    lines += ["", "## 边界与解释", "",
        "- no_curation 与 always_keep 的完整终态数组已断言相同；两行不是两份独立效果证据。",
        "- always_split 到不能合法分裂时 KEEP，Phase 2 不合并。随机只选合法动作，不把报错当作预测。",
        "- oracle 显式访问专家终态，只能对 controller 当前提供的对象返回动作，不能直接覆盖 assigns，"
        "不能恢复初态已在 0 的 spikes，不能增加任意 merge 候选。"
        "它是基于纯度的可达参照，不是经搜索证明的最优策略。",
        f"- 直接把专家标签送给评分器的一对一 F1={sanity['one_to_one']['full_output']['f1']:.4f}；"
        "这仅校验评分器，不计作框架/模型性能。",
        f"- 受限 oracle 一对一 F1={o['full_output']['f1']:.4f}，"
        f"匹配 {o['n_matched_units']}/{o['n_expert_units']} 个专家 unit。"
        + ("本次实际达到评分上限 1，证明该 CH31 初态在现有调度和动作下可到达专家分群；不代表真实模型会做到。"
           if o["full_output"]["f1"] == 1. else
           "未达到 1 不证明框架无法做到；需区分分裂/候选约束与 oracle 启发式自身的不足。"),
        "- 不将 CH1 的 Astra F1=0.2784 与这里的 CH31 分数直接比较；跨通道不是同条件模型对照。",
        "- 仅这两个离线策略组；不启动 CH31/CH20 付费 rollout，不修改冻结 skill/system/controller。"]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(out, seeds=DEFAULT_SEEDS):
    if not seeds or len(set(seeds)) != len(seeds) or any(type(s) is not int or s < 0 for s in seeds):
        raise ValueError("Distinct nonnegative seeds required")
    with offline_only():
        row = stage6.selected_rows([DATASET])[0]  # scope/hash checked before MAT
        data = stage6.load_matlab_spikes(str(ROOT / row["mat_path"]))
        if data.get("curation_assigns") is None:
            raise ValueError("CH31 reference missing")
        initial, gt = data["hierarchy_assigns"], data["curation_assigns"]
        curation_one_to_one.evaluate(initial, gt)  # shape/label validation
        out.mkdir(parents=True, exist_ok=False)
        files, configs = frozen_files(), config_hashes()
        write_json(out / "manifest.json", dict(version="ch31-offline-reference-v1", dataset=row,
            policies=POLICIES, random_seeds=list(seeds), model=None, skill_used=False,
            api_calls=0, api_cost_usd=0, code_hashes=files, config_hashes=configs,
            environment=stage6.environment(), initial_sha256=arrays_hash(initial),
            reference_sha256=arrays_hash(gt), max_local_decisions_per_trial=10000,
            controller="CurationController.run/apply/checkpoint inherited without changes; only ask replaced",
            skipped="No image rendering/request construction/provider usage because local rules do not consume them",
            gt_visibility="Oracle and evaluator only; trivial/random Policy objects get no reference",
            oracle_optimality="Not established; achieved score only, unless score actually reaches 1",
            primary="Frozen one_to_one_scoring_v1; historical many-to-one separately",
            ordinary_gt_tuning=False))
        rows = []
        trials = [("no_curation", None), ("always_keep", None), ("always_split", None)]
        trials += [("random", s) for s in seeds] + [("oracle_purity_v1", None)]
        for name, seed in trials:
            trial = name if seed is None else f"{name}_seed_{seed}"
            folder = out / trial
            start = monotonic()
            if name == "no_curation":
                folder.mkdir()
                final = initial.copy()
                with (folder / "terminal.npz").open("xb") as stream:
                    np.savez_compressed(stream, assigns=final)
                n, ops, phases, actions = 0, 0, {}, {}
            else:
                policy = Policy(name, seed=seed, reference=gt if name == "oracle_purity_v1" else None)
                c = OfflineController(fresh(data), DATASET, folder, policy=policy, sampling_rate=data["Fs"])
                final = c.run()
                if name == "always_keep" and not np.array_equal(final, initial):
                    raise AssertionError("KEEP/NOT_MERGE changed assignments")
                events = [json.loads(x) for x in (folder / "events.jsonl").read_text().splitlines()]
                decisions = [x for x in events if x["event"] == "decision"]
                n, ops = c.calls, c.operations
                phases = dict(Counter(x["phase"] for x in decisions))
                actions = dict(Counter(x["action"] for x in decisions))
            result = dict(trial=trial, policy=name, seed=seed, status="completed",
                local_decisions=n, operations=ops, by_phase=phases, by_action=actions,
                labels_visible=name == "oracle_purity_v1", seconds=monotonic()-start,
                api_calls=0, api_cost_usd=0, terminal_sha256=sha256(folder/"terminal.npz"),
                scores=score(final, data))
            write_json(folder / "score.json", result)
            rows.append(result)
            print(f"COMPLETE {trial}: F1={result['scores']['one_to_one']['full_output']['f1']:.6f}; "
                  f"local decisions={n}; API=0", flush=True)
        sanity = score(gt.copy(), data)
        assert sanity["one_to_one"]["full_output"]["f1"] == 1
        if files != frozen_files() or configs != config_hashes() or sha256(ROOT/row["mat_path"]) != row["mat_sha256"]:
            raise RuntimeError("Frozen input/code/config changed")
        stats = random_statistics(rows)
        write_json(out / "summary.json", dict(status="completed", dataset_id=DATASET, api_calls=0, api_cost_usd=0,
            manifest_sha256=sha256(out/"manifest.json"), trials=rows, random_statistics=stats,
            scorer_identity_check=sanity, original_input_and_code_unchanged=True))
        report(out, rows, stats, sanity)
        return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--random-seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    args = parser.parse_args()
    run(stage6.output_path(args.output_root), tuple(args.random_seeds))


if __name__ == "__main__":
    main()
