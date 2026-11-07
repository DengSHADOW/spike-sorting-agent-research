"""Transfer figures with the answers given with thinking on (read-only; never calls a model).

The frozen C1 agents (rule proposer with thinking on) tested on the other channel twice with the same rules, cases,
questions and seeds: answers with thinking off (output/shadow/thinking_proposer_20261101/code/output/transfer) and on
(.../transfer_answer_thinking). Only transfer tests were run with answers thinking, so there is no online figure.
The bars are those of final_figures_20261101_by_backend/thinking (SpikeAgent, Base agent, Long-context agent, Ours)
plus "Ours, answers with thinking". A question left unanswered (the stopped CH3 -> CH31 seed 0 run) counts as wrong.
test_time compares the testing time per channel. times.md gives the accuracy and times per seed.

    .venv/bin/python scripts/analysis/plot_answer_thinking_figures.py --out output/shadow/final_figures_20261102_answer_thinking
"""
from __future__ import annotations

import argparse
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plot_replay_final_figures as F  # noqa: E402

AT = F.C1 / "transfer_answer_thinking"


def calls(run):
    return [x for x in F.ledger(run / "ledger.jsonl") if x.get("completion_tokens")]


def answer_thinking(ctx):
    """Adds '<src>>tgt>:answer_thinking' to ctx['numbers'] (scored on the same questions); returns per-run rows."""
    rows = []
    for src, tgt in F.TRANSFERS:
        f, keys = f"{src}>{tgt}", ctx["common"][tgt]
        runs = [AT / f"c1_{src}_to_{tgt}_s{s}" for s in range(3)]
        correct = []
        for s, (run, off) in enumerate(zip(runs, ctx["table"][(f, "thinking")])):
            on, no = F.load_steps(run), F.load_steps(off)
            correct.append(sum(on.get(k, (False,))[0] for k in keys))
            c_on, c_off, res = calls(run), calls(off), F.read(run / "result.json")
            rows.append(dict(transfer=f"{src.upper()} → {tgt.upper()}", seed=s, n=len(keys), answered=sum(k in on for k in keys),
                             correct_off=sum(no[k][0] for k in keys), correct_on=correct[-1],
                             time_off=F.read(off / "result.json")["seconds"], time_on=res["seconds"], status=res.get("status"),
                             latency_off=statistics.median(x["seconds"] for x in c_off),
                             latency_on=statistics.median(x["seconds"] for x in c_on),
                             tokens_off=statistics.median(x["completion_tokens"] for x in c_off),
                             tokens_on=statistics.median(x["completion_tokens"] for x in c_on),
                             truncated=sum(x["finish_reason"] == "length" for x in c_on)))
        mean, se = F.mean_se([c / len(keys) for c in correct])
        ctx["numbers"][f"{f}:answer_thinking"] = dict(n_questions=len(keys), seeds=3, per_seed_correct=correct,
                                                      accuracy=mean, se=se, runs=[str(r) for r in runs])
    return rows


def times_md(ctx, rows):
    lines = ["# Transfer with answers thinking: accuracy and running time (development-set results)", "",
             "Frozen C1 agents, same rules, cases, questions and seeds; answers with thinking off against on (output cap "
             "8000 tokens; a truncated answer is retried once, a second truncation stops the run; an unanswered question "
             "counts as wrong). Test time = the run's own recorded time (result.json), without starting the vLLM server. "
             "Both on one H100 80GB, one run at a time, 4 requests in flight.", "",
             "| transfer | seed | correct, thinking off | correct, thinking on | test time off | test time on | "
             "median s per question off / on | median output tokens off / on | truncated answers | status |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        answered = f" ({r['answered']} answered)" if r["answered"] < r["n"] else ""
        lines.append(f"| {r['transfer']} | {r['seed']} | {r['correct_off']}/{r['n']} | {r['correct_on']}/{r['n']}{answered} | "
                     f"{F.mmss(r['time_off'])} | {F.mmss(r['time_on'])} | {r['latency_off']:.1f} / {r['latency_on']:.0f} | "
                     f"{r['tokens_off']:.0f} / {r['tokens_on']:.0f} | {r['truncated']} | {r['status']} |")
    lines += ["", "| transfer | accuracy, thinking off | accuracy, thinking on | mean test time off | mean test time on |",
              "|---|---|---|---|---|"]
    for src, tgt in F.TRANSFERS:
        f, sub = f"{src}>{tgt}", [r for r in rows if r["transfer"] == f"{src.upper()} → {tgt.upper()}"]
        off, on = ctx["numbers"][f"{f}:thinking"], ctx["numbers"][f"{f}:answer_thinking"]
        lines.append(f"| {src.upper()} → {tgt.upper()} | {100 * off['accuracy']:.1f}% (s.e. {100 * off['se']:.1f}) | "
                     f"{100 * on['accuracy']:.1f}% (s.e. {100 * on['se']:.1f}) | "
                     f"{F.mmss(statistics.mean(r['time_off'] for r in sub))} | {F.mmss(statistics.mean(r['time_on'] for r in sub))} |")
    diffs = [(r["correct_on"] - r["correct_off"]) / r["n"] for r in rows]
    lines += ["", f"Paired over the 6 runs: {100 * statistics.mean(diffs):+.1f} points with answers thinking "
                  f"(s.e. {100 * statistics.stdev(diffs) / len(diffs) ** .5:.1f}); better in {sum(d > 0 for d in diffs)}, "
                  f"worse in {sum(d < 0 for d in diffs)}, equal in {sum(d == 0 for d in diffs)}."]
    return "\n".join(lines)


def draw_time(ax, rows):
    """Testing time per channel, answers thinking off against on (mean of 3 seeds ± s.e., seeds as dots)."""
    groups = [f"{a.upper()} → {b.upper()}" for a, b in F.TRANSFERS]
    for j, (key, m, label) in enumerate((("time_off", "ours", "Ours, answers without thinking"),
                                         ("time_on", "answer_thinking", F.NAME["answer_thinking"]))):
        for i, g in enumerate(groups):
            xs = [r[key] / 60 for r in rows if r["transfer"] == g]
            mean, se = F.mean_se(xs)
            x = i + (j - .5) * .36
            ax.bar(x, mean, width=.34, color=F.COLOR[m], yerr=se, error_kw=dict(ecolor=F.INK, lw=1, capsize=3),
                   zorder=2, label=label if i == 0 else None)
            ax.scatter([x] * len(xs), xs, s=12, color=F.INK, zorder=3, linewidths=0)
            ax.text(x, max(xs + [mean + se]) + .5, F.mmss(mean * 60), ha="center", va="bottom", fontsize=9, fontweight="bold")
    ax.set_xticks(range(len(groups)), [f"Testing on {g.split('→ ')[1]}\n({g})" for g in groups])
    ax.set(xlim=(-.6, len(groups) - .4), ylim=(0, 30), ylabel="Testing time (min)")  # room for the legend
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="upper left", frameon=False)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ctx = F.prepare()
    rows = answer_thinking(ctx)
    import matplotlib.pyplot as plt
    figures = []
    for src, tgt in F.TRANSFERS:
        fig, ax = plt.subplots(figsize=(5.6, 3.6))
        F.draw_transfer(ax, ctx, src, tgt, None, title=False, ylabel="Accuracy", ours="thinking", extra=("answer_thinking",))
        fig.subplots_adjust(left=.13, right=.98, top=.95, bottom=.22)
        figures.append((f"transfer_{src}_to_{tgt}", fig))
    fig, ax = plt.subplots(figsize=(5.0, 3.6))
    draw_time(ax, rows)
    fig.subplots_adjust(left=.12, right=.98, top=.95, bottom=.16)
    figures.append(("test_time", fig))
    for name, fig in figures:
        fig.savefig(out / f"{name}.pdf")
        fig.savefig(out / f"{name}.png", dpi=150)
        plt.close(fig)
    md = times_md(ctx, rows)
    (out / "times.md").write_text(md + "\n", encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
