"""Collect strict expert replay runs: step-level agreement, final (whole-run) agreement and timing. Offline.

Usage:
  .venv/bin/python scripts/analysis/summarize_expert_replay.py output/replay_qwen27b_*_baseline \
      --window 20 --output output/replay_qwen27b_baseline_summary
Writes steps.csv (one row per question, in order), summary.json and SUMMARY.md. Agreement is the
harness's final action (after skill gates) against the expert action; derived KEEP questions are
reported separately from recorded expert edits.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.agent import expert_simulation as sim


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def quantiles(values):
    if not values:
        return None
    values = sorted(values)
    return dict(n=len(values), median=statistics.median(values), p90=values[min(len(values) - 1, int(.9 * len(values)))],
                mean=statistics.fmean(values), total=sum(values))


def ledger(run):
    calls = [json.loads(line) for line in (run / "ledger.jsonl").open(encoding="utf-8")] if (run / "ledger.jsonl").exists() else []
    return dict(calls=len(calls), http_seconds=quantiles([c["seconds"] for c in calls]),
                prompt_tokens=sum(c.get("prompt_tokens", 0) for c in calls),
                completion_tokens=sum(c.get("completion_tokens", 0) for c in calls),
                truncations=sum(c.get("finish_reason") == "length" for c in calls))


# Image-blind reference policies on the same questions: they show how much of the agreement the question
# mix alone gives (strict replay asks mostly about clusters the expert edits).
REFERENCES = {
    "always_keep": lambda kind, allowed: "KEEP" if kind == "single" else "NOT_MERGE",
    "always_discard": lambda kind, allowed: "DISCARD",
    "split_if_offered_else_discard": lambda kind, allowed: "SPLIT" if "SPLIT" in allowed else "DISCARD",
}


def rate(rows):
    n = len(rows)
    return dict(n=n, correct=sum(r["match"] for r in rows), accuracy=sum(r["match"] for r in rows) / n if n else None)


def collect(run, window):
    run = Path(run)
    manifest, result = read(run / "run_manifest.json"), read(run / "result.json")
    summary, rows = sim.summarize(run / "replay", window=window)
    for i, r in enumerate(rows, 1):
        s = read(run / "replay" / r["step"] / "simulation.json")
        q = read(run / "replay" / r["step"] / "question.json")
        r.update(index=i, dataset=manifest["dataset"]["dataset_id"], model_action=s.get("model_action"),
                 gated=bool(s.get("gate")), recorded=r["provenance"] == "expert_recorded", allowed=q["allowed"])
    scored = [r for r in rows if r["match"] is not None]
    by_action = {a: rate([r for r in scored if r["expert_action"] == a]) for a in sorted({r["expert_action"] for r in scored})}
    return dict(run=str(run), dataset=manifest["dataset"]["dataset_id"], model=manifest["model_alias"],
                skill=manifest.get("skill_version"), status=result["status"], error=result["error"],
                final=dict(all=rate(scored), recorded_only=rate([r for r in scored if r["recorded"]]),
                           derived_keep=rate([r for r in scored if not r["recorded"]]), by_expert_action=by_action,
                           gated_steps=sum(r["gated"] for r in rows),
                           references={name: rate([dict(match=f(r["kind"], r["allowed"]) == r["expert_action"])
                                                   for r in scored]) for name, f in REFERENCES.items()}),
                step_level=dict(window=window, sliding=summary["sliding_window"]["accuracy"]),
                timing=dict(run_seconds=result["seconds"], render_seconds=quantiles([r["prepare_seconds"] for r in rows]),
                            answer_seconds=quantiles([r["answer_seconds"] for r in rows]), provider=ledger(run)),
                expert_operations=summary.get("expert_operations")), rows


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("runs", nargs="+")
    p.add_argument("--window", type=int, default=20)
    p.add_argument("--output", required=True)
    args = p.parse_args(argv)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    runs, steps = [], []
    for run in args.runs:
        r, rows = collect(run, args.window)
        runs.append(r)
        steps += rows
    fields = ["dataset", "index", "step", "round", "kind", "expert_action", "provenance", "agent_action", "model_action",
              "gated", "match", "prepare_seconds", "answer_seconds", "prepared_at", "answered_at"]
    with (out / "steps.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(steps)
    (out / "summary.json").write_text(json.dumps(runs, indent=2), encoding="utf-8")
    pct = lambda d: "n/a" if d["accuracy"] is None else f"{d['correct']}/{d['n']} ({d['accuracy']:.1%})"
    lines = ["# Strict expert replay summary", "", "Agreement = harness final action (after gates) vs expert action.", "",
             "| dataset | status | all | always DISCARD | SPLIT if offered else DISCARD | recorded edits | derived KEEP | by expert action | gated | model s/step (median, p90) | render s/step | run min |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        a, t = r["final"], r["timing"]
        by = "; ".join(f"{k} {pct(v)}" for k, v in a["by_expert_action"].items())
        ans, ren = t["answer_seconds"], t["render_seconds"]
        ref = a["references"]
        lines.append(f"| {r['dataset']} | {r['status']} | {pct(a['all'])} | {pct(ref['always_discard'])} "
                     f"| {pct(ref['split_if_offered_else_discard'])} | {pct(a['recorded_only'])} | {pct(a['derived_keep'])} "
                     f"| {by} | {a['gated_steps']} | {ans['median']:.1f}, {ans['p90']:.1f} | {ren['median']:.1f} "
                     f"| {t['run_seconds'] / 60:.1f} |")
    lines += ["", f"Step-level curves: sliding window of {args.window} labeled steps in summary.json; per-step rows in steps.csv."]
    (out / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
