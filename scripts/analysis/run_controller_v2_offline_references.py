"""Offline reference policies through controller v2 (interleaved, no phases).

Same frozen policies, seeds and scorer as the v1 offline references (run_ch31_offline_references);
only the controller is replaced. No model, prompt, image rendering, API client, keys or network.
The oracle reads MAT terminal labels: it shows whether v2 still leaves a path to the expert
terminal, it is not a model score.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.analysis import run_annotated_offline_references as existing
from src.pipeline import curation_controller_v2 as v2

ref = existing.ref
DATASETS = ("cM2-e004_004-006_CH31", "cM2-e004_004-006_CH3", "cM2-e004_011-015_CH20",
            "cM2-e008_021-028_CH30", "cM2-e004_004-006_CH1", "cM2-e004_011-015_CH17")
V1_SUMMARIES = (ROOT / "output/annotated_offline_references_20261012/summary.json",
                ROOT / "output/ch1_ch17_offline_references_20261012/summary.json")
PHASE = {"single": "phase1", "pair": "phase2"}


class PolicyDecider:
    """Adapter: v2 asks 'single'/'pair'; the frozen v1 policies answer 'phase1'/'phase2' questions."""

    def __init__(self, policy):
        self.policy, self.kind = policy, policy.kind

    def decide(self, m, kind, cid, target, allowed, *, folder, sampling_rate):
        action, reason = self.policy.choose(m, PHASE[kind], cid, target, allowed)
        return dict(action=action, rationale=reason)


def frozen_files():
    return {**existing.frozen_files(), **{str(p.relative_to(ROOT)): existing.sha(p) for p in
            (Path(__file__).resolve(), ROOT / "src/pipeline/curation_controller_v2.py")}}


def run_dataset(row, out, k, controller="interleaved"):
    data = existing.load_data(row)
    out.mkdir(parents=True, exist_ok=False)
    rows = []
    for name, seed in existing.trial_specs():
        label = existing.trial_name(name, seed)
        folder, started = out / label, monotonic()
        if name == "no_curation":
            folder.mkdir()
            pred, calls, operations, kinds, actions, status = data["hierarchy_assigns"].copy(), 0, 0, {}, {}, "completed"
        else:
            policy = ref.Policy(name, seed=seed, reference=data["curation_assigns"] if name == "oracle_purity_v1" else None)
            cls = v2.RoundsController if controller == "rounds" else v2.CurationControllerV2
            c = cls(ref.fresh(data), row["dataset_id"], folder, decider=PolicyDecider(policy),
                    sampling_rate=data["Fs"], k=k, manifest=dict(policy=name, seed=seed,
                    labels_visible=name == "oracle_purity_v1", api_calls=0))
            pred = c.run()
            if name == "always_keep" and not (pred == data["hierarchy_assigns"]).all():
                raise AssertionError("KEEP changed input")
            decisions = [e for e in map(json.loads, (folder / "events.jsonl").read_text().splitlines())
                         if e["event"] == "decision"]
            calls, operations, status = c.calls, c.operations, c.status
            kinds, actions = dict(Counter(e["kind"] for e in decisions)), dict(Counter(e["action"] for e in decisions))
        rounds = getattr(c, "round", None) if name != "no_curation" else 0
        result = dict(trial=label, policy=name, seed=seed, status=status, local_decisions=calls, rounds=rounds,
                      operations=operations, by_kind=kinds, by_action=actions, seconds=monotonic() - started,
                      api_calls=0, scores=ref.score(pred, data) if status == "completed" else None)
        existing.write(folder / "score.json", result)
        rows.append(result)
        f1 = result["scores"]["one_to_one"]["full_output"]["f1"] if result["scores"] else None
        print(f"{row['dataset_id']} / {label}: {status}, F1={f1}, decisions={calls}", flush=True)
    return dict(dataset_id=row["dataset_id"], trials=rows, random_statistics=ref.random_statistics(rows))


def v1_reference():
    out = {}
    for path in V1_SUMMARIES:
        for dataset, b in json.loads(path.read_text())["datasets"].items():
            out[dataset] = {t["trial"]: t for t in b["trials"]}
    return out


def report(out, bundles, k, controller="interleaved"):
    v1 = v1_reference()
    f1 = lambda t: t["scores"]["one_to_one"]["full_output"]["f1"]
    what = "round-synchronous, final sweep on" if controller == "rounds" else "interleaved single/pair judgements"
    lines = [f"# Controller v2 offline references ({controller})", "",
             f"Controller v2 ({what}, no phases, k = {k}); frozen v1 reference policies "
             "and seeds 0–9; one-to-one IoU ≥ 0.5 full-output scoring. API calls 0. The oracle reads expert terminal "
             "labels: F1 = 1 means v2 still leaves a path to the expert terminal; it is not a model score.", "",
             "| Dataset | No curation | Always split v1 → v2 | Random mean v1 → v2 | Oracle v1 → v2 | Oracle units v2 | "
             "Oracle decisions v1 → v2 | Oracle rounds |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for dataset, b in bundles.items():
        t, old = {x["trial"]: x for x in b["trials"]}, v1[dataset]
        o = t["oracle_purity_v1"]["scores"]["one_to_one"]
        lines.append(f"| {dataset} | {f1(t['no_curation']):.4f} | {f1(old['always_split']):.4f} → {f1(t['always_split']):.4f} | "
                     f"{old_random(old):.4f} → {b['random_statistics']['f1']['mean']:.4f} | "
                     f"{f1(old['oracle_purity_v1']):.4f} → {f1(t['oracle_purity_v1']):.4f} | "
                     f"{o['n_matched_units']}/{o['n_expert_units']} | "
                     f"{old['oracle_purity_v1']['local_decisions']} → {t['oracle_purity_v1']['local_decisions']} | "
                     f"{t['oracle_purity_v1'].get('rounds')} |")
    for dataset, b in bundles.items():
        lines += ["", f"## {dataset}", "", "| Trial | Status | P | R | F1 | Units | Clusters | Expert spikes discarded | "
                  "Decisions (single / pair) |", "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
        for t in b["trials"]:
            if not t["scores"]:
                lines.append(f"| {t['trial']} | {t['status']} | — | — | — | — | — | — | {t['local_decisions']} |")
                continue
            s = t["scores"]["one_to_one"]; p = s["full_output"]
            lines.append(f"| {t['trial']} | {t['status']} | {p['precision']:.4f} | {p['recall']:.4f} | {p['f1']:.4f} | "
                         f"{s['n_matched_units']}/{s['n_expert_units']} | {s['n_predicted_units']} | "
                         f"{s['discarded_expert_spikes']} | {t['by_kind'].get('single', 0)} / {t['by_kind'].get('pair', 0)} |")
    lines += ["", "No-curation equals always-keep by construction (checked). All channels are exposed development data."]
    with (out / "REPORT.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def old_random(trials):
    values = [t["scores"]["one_to_one"]["full_output"]["f1"] for t in trials.values() if t["policy"] == "random"]
    return sum(values) / len(values)


def run(out, datasets=DATASETS, k=3, controller="interleaved"):
    out = Path(out)
    with ref.offline_only():
        rows = ref.stage6.selected_rows(list(datasets))
        hashes = frozen_files()
        out.mkdir(parents=True, exist_ok=False)
        existing.write(out / "manifest.json", dict(version="controller-v2-offline-references-v1", datasets=rows,
            k=k, controller=v2.ROUNDS_VERSION if controller == "rounds" else v2.VERSION, policies=ref.POLICIES, random_seeds=list(existing.SEEDS),
            code_hashes=hashes, environment=ref.stage6.environment(), model=None, api_calls=0,
            visibility="Only the oracle and the scorer see MAT terminal labels"))
        bundles = {}
        for row in rows:
            bundles[row["dataset_id"]] = run_dataset(row, out / "datasets" / row["dataset_id"], k, controller)
        if hashes != frozen_files() or any(existing.sha(ROOT / r["mat_path"]) != r["mat_sha256"] for r in rows):
            raise ValueError("Frozen code or MAT changed during the run")
        existing.write(out / "summary.json", dict(status="completed", api_calls=0, datasets=bundles))
        report(out, bundles, k, controller)
    print(f"COMPLETE {out}", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-root", required=True)
    p.add_argument("--datasets", nargs="+", default=list(DATASETS))
    p.add_argument("-k", type=int, default=3)
    p.add_argument("--controller", choices=("interleaved", "rounds"), default="interleaved")
    args = p.parse_args()
    run(ref.stage6.output_path(args.output_root), args.datasets, args.k, args.controller)


if __name__ == "__main__":
    main()
