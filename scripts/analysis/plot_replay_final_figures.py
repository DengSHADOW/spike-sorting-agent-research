"""Final figures of the strict-expert-replay online-learning study (read-only; never calls a model).

Four within-channel learning curves (CH30, CH20, CH31, CH3), the same four with ours alone (per seed, mean, rule
updates marked), two transfer bar charts (CH31->CH3, CH3->CH31), the running-time table and the training/testing
time table of the transfer experiments, each as PDF and PNG, plus all_figures.pdf, numbers.md, numbers.json and
transfer_time.md. Runs: the 2026-10-31 runs in
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
C1 = Path("output/shadow/thinking_proposer_20261101/code/output")  # ours with the rule proposer's thinking on
CHANNELS = ("ch30", "ch20", "ch31", "ch3")
TRANSFERS = (("ch31", "ch3"), ("ch3", "ch31"))
WINDOW = 15
RULE_EVERY = 8  # ours proposes rule edits after every 8 revealed questions (rulebook-v2 select_every)

# Colours and names follow the advisor's example figure (Slack, 2026-11-01).
INK, INK2 = "#0b0b0b", "#52514e"
COLOR = dict(spikeagent="#D4A62A", base="#8E8E8E", context="#D9703A", ours="#2F6EB8", scratch="#3BA27A",
             thinking="#7B4FA6", answer_thinking="#8DB2E0")
NAME = dict(spikeagent="SpikeAgent", base="Base agent", context="Long-context agent", ours="Ours",
            scratch="Ours, learned on the target", thinking="Ours + thinking",
            answer_thinking="Ours, answers with thinking")
DASH = dict(thinking=(0, (4, 1.6)))  # line style next to the colour, so the two ours lines differ without colour
XLABEL = dict(spikeagent="SpikeAgent", base="Base\nagent", context="Long-\ncontext\nagent", ours="Ours",
              scratch="Ours,\nlearned on\nthe target", answer_thinking="Ours,\nanswers\nwith thinking")


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
        if all((C1 / f"seed{s}/{c}/result.json").exists() for s in range(3)):
            t[(c, "thinking")] = [C1 / f"seed{s}/{c}" for s in range(3)]
    for src, tgt in TRANSFERS:
        f = f"{src}>{tgt}"
        t[(f, "base")] = t[(tgt, "base")]
        t[(f, "spikeagent")] = sa[tgt]
        t[(f, "context")] = [V2 / f"transfer/context_{src}_to_{tgt}_s0"] + [
            HY / f"transfer/context_{src}_to_{tgt}_s{s}" for s in (1, 2)]
        t[(f, "ours")] = [HY / f"transfer/hybrid_{src}_to_{tgt}_s{s}" for s in range(3)]
        t[(f, "scratch")] = t[(tgt, "ours")]
        c1 = [C1 / f"transfer/c1_{src}_to_{tgt}_s{s}" for s in range(3)]
        if all((r / "result.json").exists() for r in c1):  # frozen C1 agents (rules learned with thinking)
            t[(f, "thinking")] = c1
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


def prepare(sa_seeds=SA_SEEDS):
    """Load every run, score it on the questions all runs of a channel share, and set the plot style.
    Returns the context the draw_* functions use (steps, common questions, numbers)."""
    table = runs_table(Path(sa_seeds))
    steps = {(f, m): [load_steps(r) for r in rs] for (f, m), rs in table.items()}
    for (f, m), rs in table.items():
        for r, s in zip(rs, steps[(f, m)]):
            if not s:
                raise SystemExit(f"no recorded expert steps in {r}")
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
    plt.rcParams.update({"font.size": 9, "axes.titlesize": 10, "axes.labelsize": 9.5, "xtick.labelsize": 8.5,
                         "ytick.labelsize": 8.5, "legend.fontsize": 8.5, "axes.edgecolor": INK, "axes.linewidth": .8,
                         "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK, "text.color": INK,
                         "pdf.fonttype": 42, "font.family": "DejaVu Sans"})
    return dict(table=table, steps=steps, common=common, numbers=numbers, sa_seeds=sa_seeds)


def panel_title(ax, letter, text):
    ax.text(-.02, 1.03, letter, transform=ax.transAxes, fontsize=11, fontweight="bold", va="bottom", ha="right")
    ax.text(0, 1.03, text, transform=ax.transAxes, fontsize=10, va="bottom", ha="left")


def legend_above(ax, methods, y=1.12, ncol=None):
    from matplotlib.lines import Line2D
    ax.legend([Line2D([], [], color=COLOR[m], lw=2, ls=DASH.get(m, "-")) for m in methods], [NAME[m] for m in methods],
              loc="lower center", bbox_to_anchor=(.5, y), ncol=ncol or len(methods), frameon=False,
              handlelength=2.2 if any(m in DASH for m in methods) else 1.6, columnspacing=1.3)


YLABEL = "Accuracy (agreement with the expert)"


def online_axes(ax, n, ylabel=YLABEL):
    import matplotlib
    ax.set(xlim=(1, n), ylim=(0, 1.0), xlabel="Steps", ylabel=ylabel)
    ax.set_xticks([1] + list(range(10, n + 1, 10)))
    ax.set_yticks([0, .25, .5, .75, 1.0])
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1, decimals=0))
    ax.spines[["top", "right"]].set_visible(False)


def curve_stats(ctx, c, m):
    """(per-seed window curves, their mean, s.e. per step) of method m on channel c."""
    keys = ctx["common"][c]
    curves = [window([s[k][0] for k in keys]) for s in ctx["steps"][(c, m)]]
    mean = [sum(cv[i] for cv in curves) / len(curves) for i in range(len(keys))]
    se = [mean_se([cv[i] for cv in curves])[1] or 0 for i in range(len(keys))]
    return curves, mean, se


ONLINE_METHODS = ("spikeagent", "base", "context", "ours")


def draw_online(ax, ctx, c, letter, methods=ONLINE_METHODS, title=True, ylabel=YLABEL, legend_ncol=None, shown=None,
                legend=True):
    """Panel: within-channel learning curves of the given methods on channel c.
    shown: method whose colour and name each curve takes (e.g. {"thinking": "ours"}); default its own."""
    shown = shown or {}
    n = len(ctx["common"][c])
    x = range(1, n + 1)
    for m in methods:
        _, mean, se = curve_stats(ctx, c, m)
        s = shown.get(m, m)
        ax.fill_between(x, [a - b for a, b in zip(mean, se)], [a + b for a, b in zip(mean, se)],
                        color=COLOR[s], alpha=.18, lw=0)
        ax.plot(x, mean, color=COLOR[s], lw=1.8, ls=DASH.get(s, "-"))
    online_axes(ax, n, ylabel)
    if title:
        panel_title(ax, letter, f"Online learning ({c.upper()}, {n} steps)")
    if legend:
        legend_above(ax, [shown.get(m, m) for m in methods], y=1.12 if title else 1.02, ncol=legend_ncol)


def ours_legend():
    from matplotlib.lines import Line2D
    return ([Line2D([], [], color=COLOR["ours"], lw=2.2), Line2D([], [], color=COLOR["ours"], lw=.8, alpha=.45),
             Line2D([], [], color=INK2, lw=.7, ls=(0, (1.5, 2.5)))],
            ["Ours, mean of 3 seeds", "one seed", "rule proposer runs"])


def draw_online_ours(ax, ctx, c, letter, legend=True):
    """Panel: ours alone on channel c: the 3 seeds as thin lines, their mean with a ±1 s.e. band, rule updates marked."""
    n = len(ctx["common"][c])
    x = range(1, n + 1)
    curves, mean, se = curve_stats(ctx, c, "ours")
    for u in range(RULE_EVERY, n, RULE_EVERY):  # the proposer runs once question u is revealed; rules apply from u+1
        ax.axvline(u + .5, color=INK2, lw=.7, ls=(0, (1.5, 2.5)), alpha=.7, zorder=1)
    for cv in curves:
        ax.plot(x, cv, color=COLOR["ours"], lw=.8, alpha=.45, zorder=2)
    ax.fill_between(x, [a - b for a, b in zip(mean, se)], [a + b for a, b in zip(mean, se)],
                    color=COLOR["ours"], alpha=.18, lw=0)
    ax.plot(x, mean, color=COLOR["ours"], lw=2.2, zorder=3)
    online_axes(ax, n)
    panel_title(ax, letter, f"Online learning, ours only ({c.upper()}, {n} steps)")
    if legend:
        ax.legend(*ours_legend(), loc="lower center", bbox_to_anchor=(.5, 1.12), ncol=3, frameon=False,
                  handlelength=1.6, columnspacing=1.3)


def draw_transfer(ax, ctx, src, tgt, letter, with_scratch=False, title=True, ylabel=YLABEL, ours="ours", extra=()):
    """Panel: accuracy on the target channel after learning on the source (bars, mean ± s.e.).
    ours: which runs fill the Ours bar ("thinking" = the frozen C1 agents), drawn and named as Ours either way.
    extra: more methods drawn after Ours (e.g. "answer_thinking"), each from ctx["numbers"]."""
    import matplotlib
    numbers, f = ctx["numbers"], f"{src}>{tgt}"
    order = ["spikeagent", "base", "context", "ours"] + (["scratch"] if with_scratch else []) + list(extra)
    vals = [numbers[f"{f}:{ours if m == 'ours' else m}"]["accuracy"] for m in order]
    errs = [numbers[f"{f}:{ours if m == 'ours' else m}"]["se"] or 0 for m in order]
    ax.bar(range(len(order)), vals, width=.62, color=[COLOR[m] for m in order], yerr=errs,
           error_kw=dict(ecolor=INK, lw=1, capsize=3), zorder=2)
    for i, (v, e) in enumerate(zip(vals, errs)):
        ax.text(i, v + e + .02, f"{100 * v:.1f}", ha="center", va="bottom", fontsize=9, fontweight="bold")
    top = min(1.0, (max(v + e for v, e in zip(vals, errs)) + .12) // .25 * .25 + .25)
    ax.set(xlim=(-.6, len(order) - .4), ylim=(0, top), ylabel=ylabel)
    ax.set_yticks([t / 4 for t in range(int(top * 4) + 1)])
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1, decimals=0))
    ax.set_xticks(range(len(order)), [XLABEL[m] for m in order])
    ax.spines[["top", "right"]].set_visible(False)
    if title:
        panel_title(ax, letter, f"Transfer to another dataset ({src.upper()} → {tgt.upper()})")


def draw_timing(ax, ctx, letter):
    """Panel: the running-time table, drawn by hand in axes coordinates."""
    import matplotlib.pyplot as plt
    rows = timing_rows(ctx["sa_seeds"])
    ax.axis("off")
    widths = [.3, .07, .1, .09, .1, .09, .08, .1, .07]
    xs = [sum(widths[:i]) for i in range(len(widths))]
    rh = 1.0 / (len(rows) + 2.6)
    y = .97
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
    panel_title(ax, letter, "Running time per step (Qwen3.5-27B, thinking off, 4 requests in flight, one H200/H100)")
    return rows


def mmss(seconds):
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def run_time(run):
    """Wall-clock parts of one finished run: total (result.json, first replay step to the end), figure rendering,
    answering (sum of per-step latencies, and first request to last answer), steps that were answered."""
    run = Path(run)
    t = [s["timing"] for s in (read(p) for p in (run / "replay").glob("step_*/simulation.json"))
         if s.get("timing", {}).get("answer_seconds")]
    return dict(total=read(run / "result.json")["seconds"], render=sum(x["prepare_seconds"] for x in t),
                answer=sum(x["answer_seconds"] for x in t),
                answer_wall=max(x["answered_at"] for x in t) - min(x["answer_started_at"] for x in t), steps=len(t))


TT_NAME = dict(ours="Ours, transferred", context="Long-context agent, transferred", scratch="Ours, learned on the target",
               base="Base agent", spikeagent="SpikeAgent")
TRANSFER_TIME_HEADER = ["Method", "Training on\n(steps)", "Training time\nseed 0 / 1 / 2", "mean",
                        "Testing on\n(steps)", "Testing time\nseed 0 / 1 / 2", "mean"]


def transfer_times(sa_seeds=SA_SEEDS):
    """Training and test runs of every method in the transfer experiments with their wall-clock parts per seed.
    Training = ours learning online on the source channel; testing = the frozen agent on the target. The transferred
    long-context agent has no training run (its pool is Jacob's recorded source decisions); ours learned on the
    target learns while it answers, so its training and testing are one run."""
    table = runs_table(Path(sa_seeds))
    out = []
    for src, tgt in TRANSFERS:
        f = f"{src}>{tgt}"
        for method, train, test in (("ours", table[(src, "ours")], table[(f, "ours")]),
                                    ("context", None, table[(f, "context")]),
                                    ("scratch", table[(tgt, "ours")], None),
                                    ("base", None, table[(f, "base")]),
                                    ("spikeagent", None, table[(f, "spikeagent")])):
            out.append(dict(src=src, tgt=tgt, method=method, train_on=tgt if method == "scratch" else src,
                            train=[run_time(r) for r in train] if train else None,
                            test=[run_time(r) for r in test] if test else None))
    return out


def transfer_time_rows(sa_seeds=SA_SEEDS):
    """Table rows (strings, m:ss) of transfer_times; a leading * marks a bold group row."""
    rows = []
    for t in transfer_times(sa_seeds):
        if t["method"] == "ours":
            rows.append([f"*{t['src'].upper()} → {t['tgt'].upper()}"] + [""] * 6)
        if t["train"]:
            tr = [f"{t['train_on'].upper()} ({t['train'][0]['steps']})", " / ".join(mmss(r["total"]) for r in t["train"]),
                  mmss(statistics.mean(r["total"] for r in t["train"]))]
        else:
            tr = ["none (pool only)" if t["method"] == "context" else "none", "–", "–"]
        if t["test"]:
            te = [f"{t['tgt'].upper()} ({t['test'][0]['steps']})", " / ".join(mmss(r["total"]) for r in t["test"]),
                  mmss(statistics.mean(r["total"] for r in t["test"]))]
        else:
            te = ["same run", "–", "–"]
        rows.append([TT_NAME[t["method"]]] + tr + te)
    return rows


def transfer_time_notes(sa_seeds=SA_SEEDS):
    """Plain-language conclusions with the numbers computed from the runs."""
    tt = {(t["src"], t["tgt"], t["method"]): t for t in transfer_times(sa_seeds)}

    def mean(runs, key):
        return statistics.mean(r[key] for r in runs)

    tr31, tr3 = tt[("ch31", "ch3", "ours")]["train"], tt[("ch3", "ch31", "ours")]["train"]
    te3, te31 = tt[("ch31", "ch3", "ours")]["test"], tt[("ch3", "ch31", "ours")]["test"]
    learn31 = mean(tr31, "total") - mean(tr31, "render") - mean(tr31, "answer")
    learn3 = mean(tr3, "total") - mean(tr3, "render") - mean(tr3, "answer")
    sa3, sa31 = tt[("ch31", "ch3", "spikeagent")]["test"], tt[("ch3", "ch31", "spikeagent")]["test"]
    return [
        f"Training (ours, learning on) takes {mmss(mean(tr31, 'total'))} on CH31 (40 steps; seeds "
        f"{' / '.join(mmss(r['total']) for r in tr31)}) and {mmss(mean(tr3, 'total'))} on CH3 (71 steps; "
        f"{' / '.join(mmss(r['total']) for r in tr3)}). A training run renders each step's five images "
        f"({mean(tr31, 'render'):.0f} s / {mean(tr3, 'render'):.0f} s in total), answers one question at a time "
        f"({mean(tr31, 'answer'):.0f} s / {mean(tr3, 'answer'):.0f} s, {mean(tr31, 'answer') / 40:.1f} s per question) "
        f"and spends the rest, {learn31:.0f} s / {learn3:.0f} s ({100 * learn31 / mean(tr31, 'total'):.0f}% / "
        f"{100 * learn3 / mean(tr3, 'total'):.0f}% of the run), on learning: the rule proposer after every "
        f"{RULE_EVERY} steps and the evaluator re-answering past steps to verify candidate rules. The seed-to-seed "
        f"spread of the training time (CH3: {mmss(min(r['total'] for r in tr3))} to "
        f"{mmss(max(r['total'] for r in tr3))}) is this verification: how many candidate rule sets get re-scored.",
        f"Testing the frozen agent takes {mmss(mean(te3, 'total'))} on CH3 (71 steps) and {mmss(mean(te31, 'total'))} "
        f"on CH31 (40 steps): {mean(te3, 'render'):.0f} s / {mean(te31, 'render'):.0f} s to render the images of all "
        f"steps up front, then {mean(te3, 'answer_wall'):.0f} s / {mean(te31, 'answer_wall'):.0f} s to answer with "
        f"4 steps in flight ({mean(te3, 'answer') / 71:.1f} s latency per question). Rendering, not the model, is "
        f"most of the test time. The base agent and the transferred long-context agent take the same time "
        f"({mmss(mean(tt[('ch31', 'ch3', 'base')]['test'], 'total'))} / "
        f"{mmss(mean(tt[('ch31', 'ch3', 'context')]['test'], 'total'))} on CH3): the frozen rules and the source "
        f"decisions add prompt text but no time.",
        f"One transfer end to end: CH31 → CH3 {mmss(mean(tr31, 'total') + mean(te3, 'total'))} (training "
        f"{mmss(mean(tr31, 'total'))} + testing {mmss(mean(te3, 'total'))}); CH3 → CH31 "
        f"{mmss(mean(tr3, 'total') + mean(te31, 'total'))} ({mmss(mean(tr3, 'total'))} + {mmss(mean(te31, 'total'))}). "
        f"Learning on the target itself is one run of {mmss(mean(tt[('ch31', 'ch3', 'scratch')]['train'], 'total'))} "
        f"(CH3) or {mmss(mean(tt[('ch3', 'ch31', 'scratch')]['train'], 'total'))} (CH31), learning while it answers.",
        "The transferred long-context agent needs no training run: its pool is Jacob's recorded decisions on the "
        "source channel, read from the source run's folder without any model call. The base agent and SpikeAgent "
        f"never train. SpikeAgent's test runs ask more questions (57 on CH31, 99 on CH3: KEEP questions under the "
        f"older protocol) and take {mmss(mean(sa3, 'total'))} on CH3 and {mmss(mean(sa31, 'total'))} on CH31 "
        f"(seed 0 on the H200, seeds 1-2 on the H100).",
        "Wall-clock as recorded by each run (result.json): from the first replay step to the end, the vLLM server "
        "already running and the model loaded (starting the server takes about 3 minutes, once per job of many runs). "
        "Lv's runs: one run at a time on one H200, Qwen3.5-27B, thinking off, "
        "4 requests in flight when not learning (frozen runs) and for the evaluator; SpikeAgent seeds 1-2 on one "
        "H100 80GB.",
    ]


def draw_transfer_time(ax, ctx, letter):
    """Panel: the training/testing time table of the transfer experiments, drawn by hand in axes coordinates."""
    import matplotlib.pyplot as plt
    rows = transfer_time_rows(ctx["sa_seeds"])
    ax.axis("off")
    widths = [.29, .12, .17, .07, .12, .17, .07]
    xs = [sum(widths[:i]) for i in range(len(widths))]
    rh = 1.0 / (len(rows) + 2.6)
    y = .97
    ax.add_patch(plt.Rectangle((0, y - 1.9 * rh), 1, 1.9 * rh, color="#efefef", lw=0))
    for i, (x, wdt, h) in enumerate(zip(xs, widths, TRANSFER_TIME_HEADER)):
        ax.text(x + (.01 if i == 0 else wdt / 2), y - .06 * rh, h, ha="left" if i == 0 else "center", va="top",
                fontsize=8, fontweight="bold", linespacing=1.15)
    y -= 1.9 * rh
    for row in rows:
        bold = row[0].startswith("*")
        for i, (x, wdt, v) in enumerate(zip(xs, widths, row)):
            ax.text(x + (.01 if i == 0 else wdt / 2), y - rh / 2, v.removeprefix("*"), ha="left" if i == 0 else "center",
                    va="center", fontsize=8, fontweight="bold" if bold else "normal")
        y -= rh
    ax.set(xlim=(0, 1), ylim=(0, 1))
    panel_title(ax, letter, "Training and testing time per channel in the transfer experiments (wall-clock, min:s)")
    return rows


ONLINE_PANELS = tuple(zip("abcd", CHANNELS))
TRANSFER_PANELS = tuple(zip("ef", TRANSFERS))
FOOT_CURVE = f"Shaded bands: ±1 standard error over 3 seeds. Curves: accuracy over the last {WINDOW} steps."
FOOT_OURS = (f"Thick line: mean of 3 seeds; thin lines: single seeds; shaded band: ±1 standard error over seeds. Curves: "
             f"accuracy over the last {WINDOW} steps. Dotted lines: the rule proposer runs after every {RULE_EVERY} steps.")
FOOT_TRANSFER_TIME = ("Wall-clock of each run as recorded in its result.json (first replay step to the end; the vLLM "
                      "server was already running). Training = ours learning online on the source channel; testing = the "
                      "frozen agent on the target channel. 'none (pool only)': the transferred long-context agent reads "
                      "Jacob's recorded source decisions from the source run's folder, no model call. 'same run': ours "
                      "learned on the target learns while it answers. Lv's runs one at a time on one H200 (Qwen3.5-27B, "
                      "thinking off); SpikeAgent seed 0 on the H200, seeds 1-2 on one H100 80GB, asking 57 (CH31) / 99 "
                      "(CH3) questions under the older protocol.")
FOOT_TIMING = ("Testing (learning off): about 3 s per step for every LLM agent, 3-4 min per channel with 4 steps in "
               "flight; the expert's decisions in the prompt add no time (answers are ~76 tokens, the 5 images "
               "dominate). Learning on: 12.9 s per step, 4.6x frozen; the cost is the evaluator re-answering past "
               "steps to verify candidate rules (~10 calls/step), not the proposer (1 call per 8 steps). SpikeAgent: "
               "~15x slower (4-5 sequential calls per step, ~400-token reports) and not more accurate. Latency = "
               "request to answer per step (median / mean over steps and seeds); wall-clock = run time / steps.")


def build(sa_seeds=SA_SEEDS):
    """([(name, standalone matplotlib figure)], ctx) for the figure files; the report embeds the same panels."""
    import textwrap
    import matplotlib.pyplot as plt
    ctx = prepare(sa_seeds)
    figures = []
    for letter, c in ONLINE_PANELS:
        fig, ax = plt.subplots(figsize=(5.2, 4.2))
        draw_online(ax, ctx, c, letter)
        fig.text(.01, .01, FOOT_CURVE, fontsize=6.5, color=INK2, va="bottom")
        fig.subplots_adjust(left=.14, right=.97, top=.78, bottom=.19)
        figures.append((f"online_{c}", fig))
    for letter, c in ONLINE_PANELS:
        fig, ax = plt.subplots(figsize=(5.2, 4.2))
        draw_online_ours(ax, ctx, c, letter + "′")
        fig.text(.01, .01, "\n".join(textwrap.wrap(FOOT_OURS, 110)), fontsize=6.5, color=INK2, va="bottom")
        fig.subplots_adjust(left=.14, right=.97, top=.78, bottom=.21)
        figures.append((f"online_{c}_ours_only", fig))
    fig, axes = plt.subplots(2, 2, figsize=(10.4, 8.2))
    for ax, (letter, c) in zip(axes.flat, ONLINE_PANELS):
        draw_online_ours(ax, ctx, c, letter + "′", legend=False)
    fig.legend(*ours_legend(), loc="upper center", bbox_to_anchor=(.5, .99), ncol=3, frameon=False, handlelength=1.6,
               columnspacing=1.3)
    fig.text(.01, .01, FOOT_OURS, fontsize=6.5, color=INK2, va="bottom")
    fig.subplots_adjust(left=.07, right=.98, top=.9, bottom=.09, hspace=.5, wspace=.25)
    figures.append(("online_ours_only_all", fig))
    for letter, (src, tgt) in TRANSFER_PANELS:
        for suffix, ws in (("", False), ("_with_scratch", True)):
            fig, ax = plt.subplots(figsize=(4.6 + .5 * ws, 3.9))
            draw_transfer(ax, ctx, src, tgt, letter, ws)
            fig.text(.01, .01, f"Bars: mean accuracy on all {len(ctx['common'][tgt])} {tgt.upper()} steps; whiskers: "
                     "±1 standard error over 3 seeds.", fontsize=6.5, color=INK2, va="bottom")
            fig.subplots_adjust(left=.17, right=.97, top=.86, bottom=.26)
            figures.append((f"transfer_{src}_to_{tgt}{suffix}", fig))
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    draw_timing(ax, ctx, "g")
    fig.text(.01, .01, "\n".join(textwrap.wrap(FOOT_TIMING, 150)), fontsize=6.5, color=INK2, va="bottom", linespacing=1.3)
    fig.subplots_adjust(left=.02, right=.98, top=.9, bottom=.3)
    figures.append(("running_time", fig))
    fig, ax = plt.subplots(figsize=(7.4, 4.6))
    draw_transfer_time(ax, ctx, "h")
    fig.text(.01, .01, "\n".join(textwrap.wrap(FOOT_TRANSFER_TIME, 150)), fontsize=6.5, color=INK2, va="bottom",
             linespacing=1.3)
    fig.subplots_adjust(left=.02, right=.98, top=.9, bottom=.2)
    figures.append(("transfer_time", fig))
    return figures, ctx


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True)
    p.add_argument("--spikeagent-seeds", default=str(SA_SEEDS), help="folder with seed*/<channel> SpikeAgent runs")
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    figures, ctx = build(args.spikeagent_seeds)
    numbers = ctx["numbers"]

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
    md = ["# Training and testing time in the transfer experiments (wall-clock, min:s)", "",
          "| " + " | ".join(h.replace("\n", " ") for h in TRANSFER_TIME_HEADER) + " |", "|---" * len(TRANSFER_TIME_HEADER) + "|"]
    md += ["| " + " | ".join(f"**{v[1:]}**" if v.startswith("*") else v for v in r) + " |"
           for r in transfer_time_rows(args.spikeagent_seeds)]
    md += [""] + [f"- {s}" for s in transfer_time_notes(args.spikeagent_seeds)]
    (out / "transfer_time.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(lines + [""] + md))


if __name__ == "__main__":
    main()
