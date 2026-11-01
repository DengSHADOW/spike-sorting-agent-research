"""Final figures of the strict-expert-replay online-learning study (read-only; never calls a model).

Four within-channel learning curves (CH30, CH20, CH31, CH3) and two transfer bar charts (CH31->CH3, CH3->CH31),
each as PDF and PNG, plus all_figures.pdf, numbers.md and numbers.json. Runs: the 2026-10-31 runs in
/workspace/Lv-replay (base, simple context, ours = rules + context, transfer) and SpikeAgent (seed 0 from
/workspace/replay-zeroshot-exp34, further seeds from --spikeagent-seeds). Only recorded expert edits are scored, on
the questions every run of a channel shares (SpikeAgent's runs also asked KEEP and single-option questions, which
are dropped); the score is the first answer, given before Jacob's action is revealed.

    .venv/bin/python scripts/analysis/plot_replay_final_figures.py --out output/shadow/final_figures_20261101
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics

V2 = Path("/workspace/Lv-replay/zs-online-v2/output/overnight_20261031")
HY = Path("/workspace/Lv-replay/zs-online-hybrid/output/hybrid_20261031")
SA0 = Path("/workspace/replay-zeroshot-exp34/output/exp4_spikeagent_qwen27b_nothink_20261029")
SA_SEEDS = Path("output/shadow/spikeagent_seeds_20261101/code/output")
CHANNELS = ("ch30", "ch20", "ch31", "ch3")
TRANSFERS = (("ch31", "ch3"), ("ch3", "ch31"))
WINDOW = 15

# Colours and names follow the advisor's example figure (Slack, 2026-11-01).
INK, INK2 = "#0b0b0b", "#52514e"
COLOR = dict(spikeagent="#D4A62A", base="#8E8E8E", context="#D9703A", ours="#2F6EB8", scratch="#3BA27A")
NAME = dict(spikeagent="SpikeAgent", base="Base agent", context="Long-context agent", ours="Ours",
            scratch="Ours, learned on the target")
XLABEL = dict(spikeagent="SpikeAgent", base="Base\nagent", context="Long-\ncontext\nagent", ours="Ours",
              scratch="Ours,\nlearned on\nthe target")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_steps(run):
    """{expert_step: (correct, expert action, answer)} of the recorded expert edits of one run."""
    run = Path(run)
    replay = run / "replay" if (run / "replay").is_dir() else run
    steps = {}
    for f in sorted(replay.glob("step_*/simulation.json")):
        s = read(f)
        if s.get("provenance") == "expert_recorded" and s.get("expert_action") is not None:
            steps[s["expert_step"]] = (bool(s["match"]), s["expert_action"], s["agent_action"])
    return steps


def runs_table(sa_seeds):
    """{(figure, method): [run dir per seed]}; figure is a channel (online) or 'src>tgt' (transfer)."""
    sa = {c: [SA0 / c] + sorted(p for p in (sa_seeds.glob(f"seed*/{c}")) if (p / "result.json").exists())
          for c in CHANNELS}
    t = {}
    for c in CHANNELS:
        t[(c, "base")] = [V2 / f"base/{c}_s{s}" for s in range(3)]
        t[(c, "context")] = [V2 / f"context_online/{c}_s{s}" for s in range(3)]
        t[(c, "ours")] = [HY / f"hybrid/{c}_s{s}" for s in range(3)]
        t[(c, "spikeagent")] = sa[c]
    for src, tgt in TRANSFERS:
        f = f"{src}>{tgt}"
        t[(f, "base")] = t[(tgt, "base")]
        t[(f, "spikeagent")] = sa[tgt]
        t[(f, "context")] = [V2 / f"transfer/context_{src}_to_{tgt}_s0"] + [
            HY / f"transfer/context_{src}_to_{tgt}_s{s}" for s in (1, 2)]
        t[(f, "ours")] = [HY / f"transfer/hybrid_{src}_to_{tgt}_s{s}" for s in range(3)]
        t[(f, "scratch")] = t[(tgt, "ours")]
    return t


def target(figure):
    return figure.split(">")[-1]


def window(xs, w=WINDOW):
    """Trailing mean over the last w questions (fewer at the start)."""
    return [sum(xs[max(0, i - w + 1):i + 1]) / len(xs[max(0, i - w + 1):i + 1]) for i in range(len(xs))]


def mean_se(xs):
    m = sum(xs) / len(xs)
    se = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1) / len(xs)) if len(xs) > 1 else None
    return m, se


def ledger(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")] if Path(path).exists() else []


TIMING_HEADER = ["Method", "calls\n/step", "latency\nmedian (s)", "latency\nmean (s)", "wall-clock\n(s/step)",
                 "prompt\ntokens", "output\ntokens", "learning\ncalls/step", "steps"]


def timing_rows(sa_seeds=SA_SEEDS):
    """Per-step cost of each method from the run folders: calls, latency, wall-clock, tokens, learning calls."""
    tr = TRANSFERS
    sa = [SA0] + sorted(p for p in Path(sa_seeds).glob("seed*") if p.is_dir())
    groups = [
        ("Ours, frozen (transfer test)", [HY / f"transfer/hybrid_{a}_to_{b}_s{s}" for a, b in tr for s in range(3)]),
        ("Long-context, frozen (transfer test)", [V2 / f"transfer/context_{a}_to_{b}_s0" for a, b in tr]
         + [HY / f"transfer/context_{a}_to_{b}_s{s}" for a, b in tr for s in (1, 2)]),
        ("Base agent", [V2 / f"base/{c}_s{s}" for c in CHANNELS for s in range(3)]),
        ("Long-context, online", [V2 / f"context_online/{c}_s{s}" for c in CHANNELS for s in range(3)]),
        ("Ours, online (learning on)", [HY / f"hybrid/{c}_s{s}" for c in CHANNELS for s in range(3)]),
        ("SpikeAgent", [p / c for p in sa for c in CHANNELS]),
    ]
    rows = []
    for name, runs in groups:
        lat, wall, pt, ct, steps, calls, lcalls = [], [], [], [], 0, 0, 0
        for r in runs:
            sims = [read(p) for p in (r / "replay").glob("step_*/simulation.json")]
            sims = [s for s in sims if s.get("provenance") == "expert_recorded" and s.get("timing", {}).get("answer_seconds")]
            if not sims or not (r / "result.json").exists():
                continue
            lat += [s["timing"]["answer_seconds"] for s in sims]
            steps += len(sims)
            wall.append(read(r / "result.json")["seconds"] / len(sims))
            main = [x for x in ledger(r / "ledger.jsonl") if x.get("prompt_tokens")]
            calls += len(main)
            pt += [x["prompt_tokens"] for x in main]
            ct += [x["completion_tokens"] for x in main]
            lcalls += sum(1 for n in ("proposer", "evaluator") for x in ledger(r / f"{n}_ledger.jsonl")
                          if x.get("prompt_tokens"))
        if not steps:
            continue
        rows.append([name, f"{calls / steps:.1f}", f"{statistics.median(lat):.1f}", f"{statistics.mean(lat):.1f}",
                     f"{statistics.mean(wall):.1f}", f"{statistics.median(pt):,.0f}", f"{statistics.median(ct):.0f}",
                     f"{lcalls / steps:.1f}" if lcalls else "0", f"{steps}"])
    return rows


def build(sa_seeds=SA_SEEDS):
    """([(name, matplotlib figure)], numbers) of the six figures; make_replay_report_pdf.py uses it too."""
    table = runs_table(Path(sa_seeds))
    steps = {(f, m): [load_steps(r) for r in rs] for (f, m), rs in table.items()}
    for (f, m), rs in table.items():
        for r, s in zip(rs, steps[(f, m)]):
            if not s:
                raise SystemExit(f"no recorded expert steps in {r}")
    # score every run of a channel on the questions all of them share
    common = {}
    for c in CHANNELS:
        sets = [set(s) for (f, m), ss in steps.items() if target(f) == c for s in ss]
        common[c] = sorted(set.intersection(*sets))
    numbers = {}
    for (f, m), ss in steps.items():
        keys = common[target(f)]
        accs = [sum(s[k][0] for k in keys) / len(keys) for s in ss]
        mean, se = mean_se(accs)
        numbers[f"{f}:{m}"] = dict(n_questions=len(keys), seeds=len(ss), per_seed_correct=[sum(s[k][0] for k in keys)
                                   for s in ss], accuracy=mean, se=se, runs=[str(r) for r in table[(f, m)]],
                                   false_discard=[sum(s[k][2] == "DISCARD" and s[k][1] != "DISCARD" for k in keys)
                                                  for s in ss])

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    plt.rcParams.update({"font.size": 9, "axes.titlesize": 10, "axes.labelsize": 9.5, "xtick.labelsize": 8.5,
                         "ytick.labelsize": 8.5, "legend.fontsize": 8.5, "axes.edgecolor": INK, "axes.linewidth": .8,
                         "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK, "text.color": INK,
                         "pdf.fonttype": 42, "font.family": "DejaVu Sans"})
    pct = matplotlib.ticker.PercentFormatter(1, decimals=0)
    foot = f"Shaded bands and whiskers: ±1 standard error over 3 seeds. Curves: accuracy over the last {WINDOW} steps."
    figures = []

    def panel_title(ax, letter, text):
        ax.set_title(text, loc="left", x=.045, fontsize=10)
        ax.text(-.04 if ax.axison else 0, 1.0, letter, transform=ax.transAxes, fontsize=11, fontweight="bold",
                va="bottom", ha="left")

    def legend_top(fig, methods):
        fig.legend([Line2D([], [], color=COLOR[m], lw=2) for m in methods], [NAME[m] for m in methods],
                   loc="upper center", ncol=len(methods), frameon=False, handlelength=1.6, columnspacing=1.3,
                   bbox_to_anchor=(.5, 1.0))

    for letter, c in zip("abcd", CHANNELS):
        keys = common[c]
        n = len(keys)
        fig, ax = plt.subplots(figsize=(5.2, 3.9))
        x = range(1, n + 1)
        for m in ("spikeagent", "base", "context", "ours"):
            curves = [window([s[k][0] for k in keys]) for s in steps[(c, m)]]
            mean = [sum(cv[i] for cv in curves) / len(curves) for i in range(n)]
            se = [mean_se([cv[i] for cv in curves])[1] or 0 for i in range(n)]
            ax.fill_between(x, [a - b for a, b in zip(mean, se)], [a + b for a, b in zip(mean, se)],
                            color=COLOR[m], alpha=.18, lw=0)
            ax.plot(x, mean, color=COLOR[m], lw=1.8)
        ax.set(xlim=(1, n), ylim=(0, 1.0), xlabel="Steps", ylabel="Accuracy (agreement with the expert)")
        ax.set_xticks([1] + list(range(10, n + 1, 10)))
        ax.yaxis.set_major_formatter(pct)
        ax.set_yticks([0, .25, .5, .75, 1.0])
        ax.spines[["top", "right"]].set_visible(False)
        panel_title(ax, letter, f"Online learning ({c.upper()}, {n} steps)")
        legend_top(fig, ("spikeagent", "base", "context", "ours"))
        fig.text(.01, .01, foot, fontsize=6.5, color=INK2, va="bottom")
        fig.subplots_adjust(left=.14, right=.97, top=.80, bottom=.2)
        figures.append((f"online_{c}", fig))

    for letter, (src, tgt) in zip("ef", TRANSFERS):
        f = f"{src}>{tgt}"
        for suffix, order in (("", ["spikeagent", "base", "context", "ours"]),
                              ("_with_scratch", ["spikeagent", "base", "context", "ours", "scratch"])):
            fig, ax = plt.subplots(figsize=(4.6 + .5 * (len(order) - 4), 3.9))
            vals = [numbers[f"{f}:{m}"]["accuracy"] for m in order]
            errs = [numbers[f"{f}:{m}"]["se"] or 0 for m in order]
            ax.bar(range(len(order)), vals, width=.62, color=[COLOR[m] for m in order], yerr=errs,
                   error_kw=dict(ecolor=INK, lw=1, capsize=3), zorder=2)
            for i, (v, e) in enumerate(zip(vals, errs)):
                ax.text(i, v + e + .02, f"{100 * v:.1f}", ha="center", va="bottom", fontsize=9, fontweight="bold")
            top = min(1.0, (max(v + e for v, e in zip(vals, errs)) + .12) // .25 * .25 + .25)
            ax.set(xlim=(-.6, len(order) - .4), ylim=(0, top), ylabel="Accuracy (agreement with the expert)")
            ax.set_yticks([t / 4 for t in range(int(top * 4) + 1)])
            ax.yaxis.set_major_formatter(pct)
            ax.set_xticks(range(len(order)), [XLABEL[m] for m in order])
            ax.spines[["top", "right"]].set_visible(False)
            panel_title(ax, letter, f"Transfer to another dataset ({src.upper()} → {tgt.upper()})")
            fig.text(.01, .01, f"Bars: mean accuracy on all {len(common[tgt])} {tgt.upper()} steps; whiskers: "
                     "±1 standard error over 3 seeds.", fontsize=6.5, color=INK2, va="bottom")
            fig.subplots_adjust(left=.17, right=.97, top=.86, bottom=.26)
            figures.append((f"transfer_{src}_to_{tgt}{suffix}", fig))
    # running time per step, as a table figure in the same style (panel g); drawn by hand for exact alignment
    rows = timing_rows(sa_seeds)
    fig, ax = plt.subplots(figsize=(7.2, 1.2 + .27 * len(rows) + 1.0))
    ax.axis("off")
    widths = [.3, .07, .1, .09, .1, .09, .08, .1, .07]
    xs = [sum(widths[:i]) for i in range(len(widths))]
    y, rh = .97, .27 / (fig.get_figheight()) * 1.0
    rh = 1.0 / (len(rows) + 2.6)  # row height in axes units
    ax.add_patch(plt.Rectangle((0, y - 1.9 * rh), 1, 1.9 * rh, color="#efefef", lw=0))
    for x, wdt, h in zip(xs, widths, TIMING_HEADER):
        ax.text(x + (.01 if wdt == .3 else wdt / 2), y - .06 * rh, h, ha="left" if wdt == .3 else "center", va="top",
                fontsize=8, fontweight="bold", linespacing=1.15)
    y -= 1.9 * rh
    for row in rows:
        for x, wdt, v in zip(xs, widths, row):
            ax.text(x + (.01 if wdt == .3 else wdt / 2), y - rh / 2, v, ha="left" if wdt == .3 else "center",
                    va="center", fontsize=8)
        y -= rh
    ax.set(xlim=(0, 1), ylim=(0, 1))
    panel_title(ax, "g", "Running time per step (Qwen3.5-27B, thinking off, 4 requests in flight, one H200/H100)")
    import textwrap as _tw
    foot = ("Testing (learning off): about 3 s per step for every LLM agent, 3-4 min per channel with 4 steps in flight; "
            "the expert's decisions in the prompt add no time (answers are ~76 tokens, the 5 images dominate). "
            "Learning on: 12.9 s per step, 4.6x frozen; the cost is the evaluator re-answering past steps to verify "
            "candidate rules (~10 calls/step), not the proposer (1 call per 8 steps). SpikeAgent: ~15x slower (4-5 "
            "sequential calls per step, ~400-token reports) and not more accurate. Latency = request to answer per "
            "step (median / mean over steps and seeds); wall-clock = run time / steps.")
    fig.text(.01, .01, "\n".join(_tw.wrap(foot, 150)), fontsize=6.5, color=INK2, va="bottom", linespacing=1.3)
    fig.subplots_adjust(left=.02, right=.98, top=.9, bottom=.3)
    figures.append(("running_time", fig))
    return figures, numbers


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True)
    p.add_argument("--spikeagent-seeds", default=str(SA_SEEDS), help="folder with seed*/<channel> SpikeAgent runs")
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    figures, numbers = build(args.spikeagent_seeds)

    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    with PdfPages(out / "all_figures.pdf") as pdf:
        for name, fig in figures:
            fig.savefig(out / f"{name}.pdf")
            fig.savefig(out / f"{name}.png", dpi=130)
            pdf.savefig(fig)
            plt.close(fig)

    lines = ["# Numbers behind the figures", "", "Accuracy on the questions all runs of a channel share; mean ± s.e. "
             "over seeds; false DISCARD = answered DISCARD where Jacob did not.", "",
             "| figure | method | seeds | questions | correct per seed | accuracy | s.e. | false DISCARD per seed |",
             "|---|---|---|---|---|---|---|---|"]
    for k, r in numbers.items():
        f, m = k.split(":")
        se = f"{100 * r['se']:.1f}" if r["se"] is not None else "-"
        lines.append(f"| {f.replace('>', ' -> ')} | {m} | {r['seeds']} | {r['n_questions']} | {r['per_seed_correct']} | "
                     f"{100 * r['accuracy']:.1f}% | {se} | {r['false_discard']} |")
    (out / "numbers.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "numbers.json").write_text(json.dumps(numbers, indent=2), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
