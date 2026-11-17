"""Run a free-order spike curation pilot on a local, expert-labelled MAT file."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import nullcontext
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from time import monotonic

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/coresearch-harness"))
from coresearch_harness import (
    AskProposal,
    GuidanceStore,
    HarnessTreatment,
    HumanHarness,
    append_jsonl,
    atomic_json,
    coresearch_harness_source_sha256,
)
from manage_memory import sha256_file

OUTCOMES = ("APPROVE", "REJECT", "ABSTAIN")
MODES = ("none", "discard", "selective", "always")


@dataclass(frozen=True)
class Candidate:
    action: str
    cluster: int | None = None
    target: int | None = None
    priority: float = 0.0
    evidence: str = ""

    @property
    def name(self):
        if self.target is not None:
            return f"{self.action} {self.cluster} into {self.target}"
        if self.cluster is not None:
            return f"{self.action} {self.cluster}"
        return self.action


def new_manager(data):
    from src.cluster.manager import ClusterManager

    return ClusterManager(data["hierarchy_assigns"], data["overcluster_assigns"],
                          data["hierarchy_tree"].copy(), data["spiketimes"], data["waveforms"])


def load_adviser(data, dataset_id):
    from src.agent.expert_simulation import ExpertOracle, same_partition

    actions = data["curation_actions"]
    reasons = data["curation_action_reasoning"]
    if not actions or (reasons and len(reasons) != len(actions)):
        raise ValueError("Expected in-MAT expert edits and optionally aligned rationales")
    oracle = ExpertOracle(new_manager(data), zip(actions, reasons or [""] * len(actions)),
                          dataset_id=dataset_id, source={"type": "mat_internal"})
    if not same_partition(oracle.final, data["curation_assigns"]):
        raise ValueError("Recorded edits and terminal labels describe different partitions")
    return oracle


def candidate_key(manager, candidate):
    from src.agent.expert_simulation import members_key

    members = (members_key(manager.assigns, cid)
               for cid in (candidate.cluster, candidate.target) if cid is not None)
    return candidate.action, tuple(sorted(members))


def candidate_menu(manager, sampling_rate, seen, limit):
    from src.agent.curation_observation_v1 import isi_rate, metrics
    from src.agent.expert_simulation import members_key
    from src.pipeline.curation_controller_v2 import correlation, split_available

    active = sorted(manager.get_active_clusters())
    values = {cid: metrics(manager, "phase1", cid, sampling_rate=sampling_rate) for cid in active}
    means = {cid: manager.waveforms[manager.assigns == cid].mean(axis=0) for cid in active}
    candidates = []
    for cid, stat in values.items():
        n = stat["n_spikes"]
        isi = stat["isi_violation_rate"] or 0.0
        cv = stat["amplitude_cv"] or 0.0
        evidence = f"n={n}, ISI<2ms={isi:.2%}, amp CV={cv:.2f}, subclusters={stat['n_overclusters']}"
        split_priority = max(isi - .025, (cv - .45) / 4)
        if split_available(manager, cid) and split_priority > 0:
            candidates.append(Candidate("SPLIT", cid, priority=split_priority, evidence=evidence))
        discard_priority = max((500 - n) / 5000, isi - .10)
        if discard_priority > 0:
            candidates.append(Candidate("DISCARD", cid, priority=discard_priority, evidence=evidence))
        candidates.append(Candidate("KEEP", cid, evidence=evidence))
    for i, source in enumerate(active):
        for target in active[i + 1:]:
            similarity = correlation(means[source], means[target])
            if similarity is None or similarity < .96:
                continue
            a, b = values[source], values[target]
            pooled = isi_rate(manager.spike_times[np.isin(manager.assigns, [source, target])])
            extra_isi = pooled - max(a["isi_violation_rate"] or 0, b["isi_violation_rate"] or 0)
            priority = similarity - .96 - max(0, extra_isi)
            if priority > 0:
                evidence = (f"n={a['n_spikes']}+{b['n_spikes']}, mean waveform r={similarity:.3f}, "
                            f"merged ISI<2ms={pooled:.2%}, increase over worse cluster={extra_isi:+.2%}")
                candidates.append(Candidate("MERGE", source, target, priority, evidence))
    keys = {cid: members_key(manager.assigns, cid) for cid in active}
    available = []
    for item in candidates:
        members = (keys[item.cluster],) if item.target is None else tuple(sorted((keys[item.cluster], keys[item.target])))
        if (item.action, members) not in seen:
            available.append(item)
    available.sort(key=lambda item: -item.priority)
    return [*available[:limit - 1], Candidate("FINISH", evidence=f"{len(active)} active clusters")]


def adviser_reply(oracle, manager, item):
    from src.pipeline.curation_controller_v2 import split_available

    if item.action == "MERGE":
        label = oracle.label(manager, "pair", item.cluster, item.target, ("MERGE", "NOT_MERGE", "DISCARD"))
    else:
        allowed = ("KEEP", "DISCARD", "SPLIT") if split_available(manager, item.cluster) else ("KEEP", "DISCARD")
        label = oracle.label(manager, "single", item.cluster, None, allowed)
    if not label["labeled"]:
        return "ABSTAIN", "The records do not establish a valid action for these current members."
    reply = "APPROVE" if label["action"] == item.action else "REJECT"
    answer = f"The recorded or derived decision is {label['action']} ({label['provenance']})."
    if label["reason"]:
        answer += f" Recorded rationale: {label['reason']}"
    return reply, answer


def review(store, oracle, manager, item, turn, prediction):
    reply, answer = adviser_reply(oracle, manager, item)
    proposal = AskProposal(
        attempt_id=f"turn_{turn:04d}",
        question=f"Should I {item.name}?",
        relevant_context=(item.evidence,),
        options=OUTCOMES,
        predicted_option=prediction,
        ask_form="options",
    )
    harness = HumanHarness(store, lambda packet: f"{reply}. {answer}")
    message = harness.ask_proposal(proposal)
    return reply, harness.result(message)


def choose_rule(menu, mode):
    item = menu[0]
    ask = item.action in {"SPLIT", "MERGE"} and item.priority < .025 and mode == "selective"
    return 0, ask, "APPROVE"


def execute(args, model):
    from scripts.analysis.curation_one_to_one import evaluate
    from src.io.matlab_loader import load_matlab_spikes
    from src.pipeline.curation_engineering_v1 import CurationController

    data = load_matlab_spikes(str(args.mat))
    if data["curation_assigns"] is None:
        raise ValueError("This pilot requires expert terminal labels for scoring")
    oracle = load_adviser(data, args.dataset_id) if args.mode != "none" else None
    manager = new_manager(data)
    fs = data["Fs"]
    args.output.mkdir(parents=True, exist_ok=False)
    atomic_json(args.output / "manifest.json", {
        "dataset_id": args.dataset_id, "mat_sha256": sha256_file(args.mat),
        "mode": args.mode, "policy": str(args.local_model or "rules"),
        "max_turns": args.max_turns, "max_queries": args.max_queries if oracle else 0,
        "menu_size": args.menu_size, "harness_mode": "fixed", "learner": None,
        "harness_sha256": coresearch_harness_source_sha256(),
        "source_sha256": {name: sha256_file(Path(__file__).with_name(name))
                          for name in ("run.py", "local_model.py")},
        "adviser": oracle.describe() if oracle else None,
    })
    # Reuse the existing checkpointed action executor, never its fixed scheduling loop.
    controller = CurationController(manager, args.dataset_id, args.output / "curation", provider=None,
                                    budget_usd=0, max_calls=args.max_turns, sampling_rate=fs)
    store = GuidanceStore(args.output, args.dataset_id, max_queries=args.max_queries,
                          treatment=HarnessTreatment(domain="spike_sorting", harness_mode="fixed")) if oracle else None
    baseline = evaluate(manager.assigns, data["curation_assigns"])
    seen, counts, last_review = set(), Counter(), ""
    menu, menu_state = [], None
    status = "stopped_turn_limit"
    started = monotonic()
    for turn in range(1, args.max_turns + 1):
        state = controller.state_hash()
        if state != menu_state or len(menu) == 1:
            menu = candidate_menu(manager, fs, seen, args.menu_size)
            menu_state = state
        remaining = args.max_queries - counts["queries"] if store else 0
        choice, ask, prediction = (model.choose(menu, args.mode, remaining, last_review, store)
                                   if model else choose_rule(menu, args.mode))
        item = menu[choice]
        if item.action == "FINISH":
            append_jsonl(args.output / "decisions.jsonl", {"turn": turn, "action": item.name})
            status = "completed"
            break
        seen.add(candidate_key(manager, item))
        menu.pop(choice)
        counts["proposals"] += 1
        required = bool(store and (args.mode == "always" or item.action == "DISCARD"))
        requested = required or (args.mode == "selective" and ask)
        applied, reply, result = True, None, None
        if requested and remaining > 0:
            reply, result = review(store, oracle, manager, item, turn, prediction)
            counts["queries"] += 1
            counts[reply.lower()] += 1
            last_review = f"{item.name}: {result['answer']}"
            applied = reply == "APPROVE"
        elif required:
            applied = False
            counts["blocked_by_budget"] += 1
        if applied:
            controller.apply(item.action, item.cluster, item.target)
            counts[item.action.lower()] += 1
        append_jsonl(args.output / "decisions.jsonl", {
            "turn": turn, "action": item.name, "evidence": item.evidence,
            "offered": [option.name for option in menu], "agent_requested": ask,
            "review_required": required, "review_requested": requested,
            "adviser_outcome": reply, "guidance": result, "applied": applied,
            "state_sha256": controller.state_hash(),
        })

    controller.checkpoint("terminal" if status == "completed" else "stopped")
    score = evaluate(manager.assigns, data["curation_assigns"])
    summary = dict(status=status, turns=turn, counts=dict(counts), seconds=monotonic() - started,
                   initial_score=baseline, terminal_score=score if status == "completed" else None,
                   stopped_state_score=score if status != "completed" else None)
    atomic_json(args.output / "summary.json", summary)
    print(json.dumps({"dataset": args.dataset_id, "mode": args.mode, "status": status, "turns": turn,
                      "queries": counts["queries"], "initial_f1": baseline["full_output"]["f1"],
                      "final_state_f1": score["full_output"]["f1"],
                      "discarded_expert_spikes": score["discarded_expert_spikes"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spike-repo", required=True, type=Path)
    parser.add_argument("--mat", required=True, type=Path)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mode", choices=MODES, default="selective")
    parser.add_argument("--max-queries", type=int, default=12)
    parser.add_argument("--max-turns", type=int, default=150)
    parser.add_argument("--menu-size", type=int, default=8)
    parser.add_argument("--local-model", type=Path, help="Local Hugging Face checkpoint for the text policy")
    parser.add_argument("--model-python", type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    if args.max_queries < 1 or args.max_turns < 1 or not 2 <= args.menu_size <= 12:
        parser.error("positive query/turn limits and a menu size from 2 to 12 are required")
    if not (args.spike_repo / "src/pipeline/curation_engineering_v1.py").is_file():
        parser.error("--spike-repo must point to the compatible spike-sorting-agent-tianmin checkout")
    if not args.mat.is_file() or args.output.exists():
        parser.error("--mat must exist and --output must be a new directory")
    sys.path.insert(0, str(args.spike_repo.resolve()))
    from src.agent.curation_contract import require_development

    require_development(args.dataset_id)
    if args.local_model and not args.local_model.is_dir():
        parser.error("--local-model must point to an existing local checkpoint directory")
    model_context = nullcontext()
    if args.local_model:
        from local_model import LocalModel

        model_context = LocalModel(args.model_python, args.local_model)
    with model_context as model:
        execute(args, model)


if __name__ == "__main__":
    main()
