"""Four clean figures for slides (read-only; never calls a model): no panel titles and no footnotes, y axis "Accuracy".

- online_ch31, online_ch3: the four methods of the final figures plus ours with the rule proposer's thinking on
  (experiment C1, output/shadow/thinking_proposer_20261101), mean of 3 seeds with ±1 s.e. bands;
- transfer_ch31_to_ch3, transfer_ch3_to_ch31: the four bars (no "learned on the target" bar).

Also writes times.md: wall-clock of ours and of ours + thinking behind each figure.

    .venv/bin/python scripts/analysis/plot_replay_clean_figures.py --out output/shadow/final_figures_20261101_clean
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plot_replay_final_figures as F  # noqa: E402

ONLINE = ("ch31", "ch3")
METHODS = F.ONLINE_METHODS + ("thinking",)


def ledger(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")] if Path(path).exists() else []


def times(ctx):
    """Per channel: run time per seed of ours (A0) and ours + thinking (C1), and their proposer seconds."""
    out = {}
    for c in ONLINE:
        row = {}
        for m in ("ours", "thinking"):
            runs = ctx["table"][(c, m)]
            row[m] = dict(total=[F.run_time(r)["total"] for r in runs],
                          proposer=[sum(x["seconds"] for x in ledger(r / "proposer_ledger.jsonl") if "seconds" in x)
                                    for r in runs],
                          proposer_calls=[sum(1 for x in ledger(r / "proposer_ledger.jsonl") if "seconds" in x)
                                          for r in runs],
                          accuracy=ctx["numbers"][f"{c}:{m}"]["accuracy"])
        out[c] = row
    return out


def span(xs, fmt="{:.0f}"):
    return fmt.format(min(xs)) if min(xs) == max(xs) else f"{fmt.format(min(xs))}-{fmt.format(max(xs))}"


def seeds(xs):
    return " / ".join(F.mmss(x) for x in xs) + f" (mean {F.mmss(statistics.mean(xs))})"


def times_md(ctx):
    t = times(ctx)
    test = {(x["src"], x["tgt"]): x for x in F.transfer_times(ctx["sa_seeds"]) if x["method"] == "ours"}
    lines = ["# Wall-clock behind the four clean figures (min:s, seeds 0 / 1 / 2)", "",
             "Ours = A0: Lv's runs, one at a time on one H200. Ours + thinking = C1: the rule proposer's thinking on, "
             "everything else identical, run on one H100 80GB with the four channels of a seed running at the same "
             "time on one server, so its evaluator calls were also slower (median 5-9 s against 2.7 s). The part that "
             "thinking itself adds is the proposer time, listed separately.", ""]
    for c, n in (("ch31", 40), ("ch3", 71)):
        o, k = t[c]["ours"], t[c]["thinking"]
        lines += [f"## Online learning, {c.upper()} ({n} steps)",
                  f"- Ours: {seeds(o['total'])}; accuracy {100 * o['accuracy']:.1f}%; proposer {span(o['proposer_calls'])} "
                  f"calls, {span(o['proposer'])} s per run in total.",
                  f"- Ours + thinking: {seeds(k['total'])}; accuracy {100 * k['accuracy']:.1f}%; proposer "
                  f"{span(k['proposer_calls'])} calls, {span([s / 60 for s in k['proposer']], '{:.1f}')} min per run "
                  "in total.", ""]
    for src, tgt in F.TRANSFERS:
        x, k = test[(src, tgt)], t[src]["thinking"]
        lines += [f"## Transfer {src.upper()} → {tgt.upper()}",
                  f"- Ours: training on {src.upper()} {seeds([r['total'] for r in x['train']])}; testing on "
                  f"{tgt.upper()} {seeds([r['total'] for r in x['test']])}.",
                  f"- Ours + thinking: training on {src.upper()} {seeds(k['total'])} (the C1 run above); testing not run "
                  f"(no frozen C1 agent was tested on {tgt.upper()}). The proposer is not called in a frozen test, so "
                  "its test time would match ours.", ""]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ctx = F.prepare()
    import matplotlib.pyplot as plt
    figures = []
    for c in ONLINE:
        fig, ax = plt.subplots(figsize=(5.4, 4.0))
        F.draw_online(ax, ctx, c, None, methods=METHODS, title=False, ylabel="Accuracy", legend_ncol=3)
        fig.subplots_adjust(left=.13, right=.97, top=.84, bottom=.13)
        figures.append((f"online_{c}", fig))
    for src, tgt in F.TRANSFERS:
        fig, ax = plt.subplots(figsize=(4.6, 3.6))
        F.draw_transfer(ax, ctx, src, tgt, None, title=False, ylabel="Accuracy")
        fig.subplots_adjust(left=.15, right=.97, top=.95, bottom=.2)
        figures.append((f"transfer_{src}_to_{tgt}", fig))
    for name, fig in figures:
        fig.savefig(out / f"{name}.pdf")
        fig.savefig(out / f"{name}.png", dpi=150)
        plt.close(fig)
    md = times_md(ctx)
    (out / "times.md").write_text(md + "\n", encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
