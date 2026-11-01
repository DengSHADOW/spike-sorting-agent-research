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

V2 = Path("/workspace/Lv-replay/zs-online-v2/output/overnight_20261031")
HY = Path("/workspace/Lv-replay/zs-online-hybrid/output/hybrid_20261031")
SA0 = Path("/workspace/replay-zeroshot-exp34/output/exp4_spikeagent_qwen27b_nothink_20261029")
SA_SEEDS = Path("output/shadow/spikeagent_seeds_20261101/code/output")
CHANNELS = ("ch30", "ch20", "ch31", "ch3")
TRANSFERS = (("ch31", "ch3"), ("ch3", "ch31"))
WINDOW = 15

# Colours: slots 1-3 of the dataviz reference palette (validated all-pairs, light mode); references in neutral ink.
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
COLOR = dict(ours="#2a78d6", context="#eb6834", scratch="#1baf7a", base="#52514e", spikeagent="#8a8984")
ONLINE_LABEL = dict(
    ours="Ours: learned rules + Jacob's earlier decisions in this channel",
    context="Simple context: Jacob's earlier decisions in this channel, no rules",
    base="Base: zero-shot, never learns (overall accuracy)",
    spikeagent="SpikeAgent: original SpikeAgent prompts, never learns (overall accuracy)",
    discard="Always answer DISCARD (share of DISCARD questions, same window)",
)


def transfer_labels(src, tgt):
    s, t = src.upper(), tgt.upper()
    return dict(
        base=f"Base\nzero-shot, nothing learned",
        spikeagent=f"SpikeAgent\noriginal prompts, never learns",
        context=f"Simple context, transferred\n{s} decisions in the prompt, frozen",
        ours=f"Ours, transferred\n{s} rules + decisions, frozen",
        scratch=f"Ours from scratch on {t}\nlearns on {t} itself, no transfer",
    )


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
    plt.rcParams.update({"font.size": 13, "axes.titlesize": 16, "axes.labelsize": 14, "xtick.labelsize": 12,
                         "ytick.labelsize": 12, "legend.fontsize": 11.5, "axes.edgecolor": INK2,
                         "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
                         "pdf.fonttype": 42, "font.family": "DejaVu Sans"})
    n_sa = len(table[("ch30", "spikeagent")])
    seeds = "3 seeds" if n_sa == 3 else f"3 seeds (SpikeAgent: {n_sa} seed{'s' if n_sa > 1 else ''})"
    note = (f"Qwen3.5-27B, thinking off. Strict expert replay: first answer scored, before Jacob's action is revealed.\n"
            f"Mean ± s.e. over {seeds}. KEEP and NOT_MERGE are not offered\n"
            "(SpikeAgent answers with its own good/bad and merge/not-merge outputs).")
    figures = []

    for c in CHANNELS:
        keys = common[c]
        n = len(keys)
        fig, ax = plt.subplots(figsize=(10, 7.4))
        x = range(1, n + 1)
        tags = []
        expert = [steps[(c, "base")][0][k][1] for k in keys]
        ax.plot(x, window([a == "DISCARD" for a in expert]), color=INK2, lw=1.6, ls=(0, (1, 2.5)),
                label=ONLINE_LABEL["discard"])
        for m in ("base", "spikeagent"):
            r = numbers[f"{c}:{m}"]
            if r["se"]:
                ax.axhspan(r["accuracy"] - r["se"], r["accuracy"] + r["se"], color=COLOR[m], alpha=.12, lw=0)
            ax.axhline(r["accuracy"], color=COLOR[m], lw=2, ls="-" if m == "base" else (0, (6, 2, 1.5, 2)),
                       label=ONLINE_LABEL[m])
        for m in ("context", "ours"):
            curves = [window([s[k][0] for k in keys]) for s in steps[(c, m)]]
            mean = [sum(cv[i] for cv in curves) / len(curves) for i in range(n)]
            se = [mean_se([cv[i] for cv in curves])[1] for i in range(n)]
            ax.fill_between(x, [a - b for a, b in zip(mean, se)], [a + b for a, b in zip(mean, se)],
                            color=COLOR[m], alpha=.18, lw=0)
            ax.plot(x, mean, color=COLOR[m], lw=2.4, label=ONLINE_LABEL[m])
            tags.append([mean[-1], "Ours" if m == "ours" else "Simple context", INK])
        for m, name in (("base", "Base"), ("spikeagent", "SpikeAgent")):
            tags.append([numbers[f"{c}:{m}"]["accuracy"], name, INK2])
        tags.sort()
        for lower, upper in zip(tags, tags[1:]):  # keep the end labels at least one text line apart
            upper[0] = max(upper[0], lower[0] + .045)
        for y, name, color in tags:
            ax.text(n + .6, y, name, color=color, fontsize=12, va="center")
        ax.set(xlim=(1, n), ylim=(0, 1.02), xlabel=f"Jacob's n-th edit in {c.upper()} (question index, {n} questions)",
               ylabel=f"Accuracy over the last {WINDOW} questions")
        ax.set_title(f"{c.upper()}: learning within one channel", loc="left", fontweight="bold")
        ax.grid(axis="y", color=GRID, lw=.8)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(loc="upper center", bbox_to_anchor=(.5, -.13), frameon=False, ncol=1)
        fig.text(.012, .012, note, fontsize=10.5, color=INK2, va="bottom")
        fig.subplots_adjust(left=.09, right=.86, top=.93, bottom=.36)
        figures.append((f"online_{c}", fig))

    for src, tgt in TRANSFERS:
        f = f"{src}>{tgt}"
        labels = transfer_labels(src, tgt)
        order = ["base", "spikeagent", "context", "ours", "scratch"]
        fig, ax = plt.subplots(figsize=(10, 6.6))
        n = len(common[tgt])
        for y, m in enumerate(reversed(order)):
            r = numbers[f"{f}:{m}"]
            ax.barh(y, r["accuracy"], height=.62, color=COLOR[m], alpha=.9 if m in ("ours", "context", "scratch") else .55,
                    xerr=r["se"], error_kw=dict(ecolor=INK, lw=1.4, capsize=5), zorder=2)
            for v in r["per_seed_correct"]:
                ax.plot(v / n, y, "o", ms=6, mfc="white", mec=INK, mew=1.2, zorder=3)
            se = f" ± {100 * r['se']:.1f}" if r["se"] is not None else " (1 seed)"
            ax.text(1.015, y, f"{100 * r['accuracy']:.1f}%{se}", va="center", fontsize=12.5)
        share = sum(steps[(f, "base")][0][k][1] == "DISCARD" for k in common[tgt]) / n
        ax.axvline(share, color=INK2, lw=1.6, ls=(0, (1, 2.5)), zorder=1)
        ax.text(share, len(order) - .35, f"always DISCARD = {100 * share:.0f}%", color=INK2, fontsize=11, ha="center")
        ax.set_yticks(range(len(order)), [labels[m] for m in reversed(order)])
        ax.set(xlim=(0, 1), ylim=(-.6, len(order) - .1),
               xlabel=f"Accuracy on all {n} {tgt.upper()} questions (overall, no window)")
        fig.suptitle(f"Transfer {src.upper()} → {tgt.upper()}: learn on {src.upper()}, freeze, test on {tgt.upper()}",
                     x=.012, ha="left", fontsize=16, fontweight="bold")
        ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1))
        ax.grid(axis="x", color=GRID, lw=.8, zorder=0)
        ax.spines[["top", "right"]].set_visible(False)
        fig.text(.012, .012, note + "\nBars: mean; whiskers: s.e.; circles: individual seeds.", fontsize=10.5,
                 color=INK2, va="bottom")
        fig.subplots_adjust(left=.36, right=.86, top=.92, bottom=.25)
        figures.append((f"transfer_{src}_to_{tgt}", fig))
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
