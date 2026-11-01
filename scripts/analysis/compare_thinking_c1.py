"""Thinking experiment C1 (proposer with thinking) versus A0 (no thinking anywhere), paired by channel and seed.

Both are ours (rules + online context) under the same protocol and seeds; only the rule proposer's thinking differs.
Reads the strict-expert-replay outputs (read-only; never calls a model) and writes RESULTS.md next to the C1 runs.

    .venv/bin/python scripts/analysis/compare_thinking_c1.py \
        --c1 output/shadow/thinking_proposer_20261101/code/output --out output/shadow/thinking_proposer_20261101/RESULTS.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plot_replay_final_figures as F  # noqa: E402

CHANNELS = ("ch30", "ch20", "ch31", "ch3")


def read(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def ledger(p):
    return [json.loads(l) for l in open(p, encoding="utf-8")] if Path(p).exists() else []


def run_summary(run):
    steps = F.load_steps(run)  # {expert_step: (correct, expert, answer)}
    sels = sorted((run / "learning/selections").glob("selection_*/result.json"))
    first_accept = None
    accepted = 0
    for s in sels:
        r = read(s)
        if any(st.get("decision") == "accepted" for st in r["steps"]):
            accepted += 1
            if first_accept is None:
                first_accept = r["cases"]  # revealed questions at that selection = step index
    rules = [r["text"] for r in read(run / "final_skill.json")["rules"]] if (run / "final_skill.json").exists() else []
    prop = [x for x in ledger(run / "proposer_ledger.jsonl") if "finish_reason" in x]
    state = read(run / "learning/state.json") if (run / "learning/state.json").exists() else {}
    return dict(steps=steps, n=len(steps), correct=sum(v[0] for v in steps.values()),
                by_action={a: (sum(v[0] for v in steps.values() if v[1] == a), sum(1 for v in steps.values() if v[1] == a))
                           for a in ("DISCARD", "SPLIT", "MERGE")},
                false_discard=sum(v[2] == "DISCARD" and v[1] != "DISCARD" for v in steps.values()),
                selections=len(sels), accepted=accepted, first_accept=first_accept, rules=rules,
                proposer_calls=len(prop), proposer_truncated=sum(x["finish_reason"] == "length" for x in prop),
                proposer_s=statistics.median([x["seconds"] for x in prop]) if prop else None,
                proposer_out=statistics.median([x["completion_tokens"] for x in prop]) if prop else None,
                failed_calls=state.get("failed_calls"), seconds=read(run / "result.json")["seconds"])


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--c1", required=True, help="folder with seed{0,1,2}/<channel> runs")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    c1root = Path(args.c1)
    lines = ["# Thinking experiment C1 vs A0 (ours, rules + online context; only the rule proposer's thinking differs)", "",
             "Development-set results. Same protocol (edit-only, first answer scored) and the same seeds as A0 "
             "(`/workspace/Lv-replay/zs-online-hybrid/output/hybrid_20261031/hybrid`). Paired by channel and seed.", ""]
    rows, tot = [], {"a0": [], "c1": []}
    lines += ["| channel | seed | A0 correct | C1 correct | Δ | flipped answers | A0 DISCARD / SPLIT | C1 DISCARD / SPLIT | "
              "false DISCARD A0 → C1 | first rule accepted (step) A0 → C1 | rule updates accepted A0 → C1 |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    detail = []
    for c in CHANNELS:
        for s in range(3):
            a0 = run_summary(F.HY / f"hybrid/{c}_s{s}")
            c1 = run_summary(c1root / f"seed{s}" / c)
            if set(a0["steps"]) != set(c1["steps"]):
                raise SystemExit(f"{c} s{s}: question sets differ")
            flips = sum(a0["steps"][k][2] != c1["steps"][k][2] for k in a0["steps"])
            tot["a0"].append(a0["correct"] / a0["n"]); tot["c1"].append(c1["correct"] / c1["n"])
            da = f"{a0['by_action']['DISCARD'][0]}/{a0['by_action']['DISCARD'][1]} / {a0['by_action']['SPLIT'][0]}/{a0['by_action']['SPLIT'][1]}"
            dc = f"{c1['by_action']['DISCARD'][0]}/{c1['by_action']['DISCARD'][1]} / {c1['by_action']['SPLIT'][0]}/{c1['by_action']['SPLIT'][1]}"
            lines.append(f"| {c.upper()} | {s} | {a0['correct']}/{a0['n']} | {c1['correct']}/{c1['n']} | "
                         f"{c1['correct'] - a0['correct']:+d} | {flips} | {da} | {dc} | {a0['false_discard']} → {c1['false_discard']} | "
                         f"{a0['first_accept']} → {c1['first_accept']} | {a0['accepted']}/{a0['selections']} → {c1['accepted']}/{c1['selections']} |")
            detail.append((c, s, a0, c1))
    lines.append("")
    ma, mc = statistics.mean(tot["a0"]), statistics.mean(tot["c1"])
    diffs = [c - a for a, c in zip(tot["a0"], tot["c1"])]
    lines += [f"**Mean accuracy over the 12 runs: A0 {100 * ma:.1f}% → C1 {100 * mc:.1f}% "
              f"(paired difference {100 * statistics.mean(diffs):+.1f} points, s.e. {100 * statistics.stdev(diffs) / len(diffs) ** .5:.1f}; "
              f"C1 better in {sum(d > 0 for d in diffs)}, worse in {sum(d < 0 for d in diffs)}, equal in {sum(d == 0 for d in diffs)} runs).**", ""]
    lines += ["## Per channel (3 seeds)", "", "| channel | A0 accuracy | C1 accuracy | Δ points |", "|---|---|---|---|"]
    for c in CHANNELS:
        a = [d[2]["correct"] / d[2]["n"] for d in detail if d[0] == c]
        b = [d[3]["correct"] / d[3]["n"] for d in detail if d[0] == c]
        lines.append(f"| {c.upper()} | {100 * statistics.mean(a):.1f}% | {100 * statistics.mean(b):.1f}% | {100 * (statistics.mean(b) - statistics.mean(a)):+.1f} |")
    lines += ["", "## Proposer calls (C1, thinking on)", "", "| channel | seed | calls | truncated | median s | median output tokens | failed learning calls | run minutes |",
              "|---|---|---|---|---|---|---|---|"]
    for c, s, a0, c1 in detail:
        lines.append(f"| {c.upper()} | {s} | {c1['proposer_calls']} | {c1['proposer_truncated']} | {c1['proposer_s']:.0f} | "
                     f"{c1['proposer_out']:.0f} | {c1['failed_calls']} | {c1['seconds'] / 60:.0f} |")
    lines += ["", "A0 proposer calls take about 7 s and 80 output tokens; the C1 runs shared one server four at a time, so their "
              "run minutes are not comparable to A0's single-channel runs.", ""]
    tr = c1root / "transfer"
    if all((tr / f"c1_{a}_to_{b}_s{s}/result.json").exists() for a, b in F.TRANSFERS for s in range(3)):
        lines += ["## Transfer (frozen agents, answers with thinking off)", "",
                  "C1 agent learned on the source, frozen, tested on the target (`transfer/c1_*`, one run at a time on "
                  "the H100), against A0's (`hybrid_20261031/transfer/hybrid_*`, H200).", "",
                  "| transfer | seed | A0 correct | C1 correct | Δ | A0 test time | C1 test time |", "|---|---|---|---|---|---|---|"]
        for a, b in F.TRANSFERS:
            for s in range(3):
                ra, rc = F.HY / f"transfer/hybrid_{a}_to_{b}_s{s}", tr / f"c1_{a}_to_{b}_s{s}"
                sa, sc = run_summary(ra), run_summary(rc)
                lines.append(f"| {a.upper()} → {b.upper()} | {s} | {sa['correct']}/{sa['n']} | {sc['correct']}/{sc['n']} | "
                             f"{sc['correct'] - sa['correct']:+d} | {F.mmss(sa['seconds'])} | {F.mmss(sc['seconds'])} |")
        lines += ["", "A frozen test never calls the proposer, so thinking adds no test time; the C1 tests are faster because "
                  "the GPU Pod renders the images faster (CH3: 80 s against 139 s), while answering takes the same "
                  "(about 3 s per question).", ""]
    lines += ["## Final rules", ""]
    for c, s, a0, c1 in detail:
        lines += [f"- **{c.upper()} seed {s}**", f"  - A0: " + (" | ".join(a0["rules"]) or "(none)"),
                  f"  - C1: " + (" | ".join(c1["rules"]) or "(none)")]
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
