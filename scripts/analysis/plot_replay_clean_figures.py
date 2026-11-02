"""Eight clean figures for slides (read-only; never calls a model), Tianmin's request of 2026-11-01: the same four
figures for ours with each back end, no panel titles, no footnotes, y axis "Accuracy", ours always named "Ours".

    no_thinking/  ours as in the final figures (Lv's runs, thinking off everywhere)
    thinking/     ours with the rule proposer's thinking on (experiment C1, output/shadow/thinking_proposer_20261101;
                  answers with thinking off), its transfer bars from the frozen C1 agents

Each folder: online_ch31 and online_ch3 (ours alone, mean of 3 seeds with ±1 s.e. band, no legend) and transfer_ch31_to_ch3 and
transfer_ch3_to_ch31 (SpikeAgent, Base agent, Long-context agent, Ours). times.md gives the eight running times:
training time behind each online figure, testing time behind each transfer figure. all_8_figures.pdf has the
eight figures, one per page.

    .venv/bin/python scripts/analysis/plot_replay_clean_figures.py --out output/shadow/final_figures_20261101_by_backend
"""
from __future__ import annotations

import argparse
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plot_replay_final_figures as F  # noqa: E402

ONLINE = ("ch31", "ch3")
BACKENDS = (("no_thinking", "ours", "thinking off everywhere; Lv's runs, one at a time on one H200"),
            ("thinking", "thinking", "thinking on for the rule proposer only, answers with thinking off; one H100 80GB, "
                                     "training runs four channels at a time on one server, transfer tests one at a time"))


def seeds(xs):
    return f"{F.mmss(statistics.mean(xs))} (seeds {' / '.join(F.mmss(x) for x in xs)})"


def times_md(ctx):
    lines = ["# Running times behind the eight figures (wall-clock, min:s, mean of 3 seeds)", "",
             "Each run's own recorded time (result.json), from its first step to its end; starting the vLLM server "
             "(about 3 minutes) is not included. Online figure = the training run on that channel (learning on while "
             "it answers); transfer figure = the frozen agent's test run on the target channel. The two back ends ran "
             "on different GPUs, so their times compare only roughly.", ""]
    for name, m, note in BACKENDS:
        lines += [f"## {name.replace('_', ' ')} ({note})", ""]
        for c in ONLINE:
            runs = ctx["table"][(c, m)]
            lines.append(f"- online_{c}: training on {c.upper()} ({len(ctx['common'][c])} steps) "
                         f"{seeds([F.run_time(r)['total'] for r in runs])}; accuracy "
                         f"{100 * ctx['numbers'][f'{c}:{m}']['accuracy']:.1f}%")
        for src, tgt in F.TRANSFERS:
            key = (f"{src}>{tgt}", m)
            if key not in ctx["table"]:
                lines.append(f"- transfer_{src}_to_{tgt}: testing on {tgt.upper()} not finished yet")
                continue
            lines.append(f"- transfer_{src}_to_{tgt}: testing on {tgt.upper()} ({len(ctx['common'][tgt])} steps) "
                         f"{seeds([F.run_time(r)['total'] for r in ctx['table'][key]])}; accuracy "
                         f"{100 * ctx['numbers'][f'{src}>{tgt}:{m}']['accuracy']:.1f}%")
        lines.append("")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    ctx = F.prepare()
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    every = PdfPages(Path(args.out) / "all_8_figures.pdf")  # one page per figure: no thinking first, then thinking
    for name, m, _ in BACKENDS:
        out = Path(args.out) / name
        out.mkdir(parents=True, exist_ok=True)
        figures = []
        for c in ONLINE:
            fig, ax = plt.subplots(figsize=(5.4, 4.0))
            F.draw_online(ax, ctx, c, None, methods=(m,), title=False, ylabel="Accuracy", shown={m: "ours"},
                          legend=False)  # one curve: no legend
            fig.subplots_adjust(left=.13, right=.97, top=.95, bottom=.13)
            figures.append((f"online_{c}", fig))
        for src, tgt in F.TRANSFERS:
            if (f"{src}>{tgt}", m) not in ctx["table"]:
                print(f"skip {name}/transfer_{src}_to_{tgt}: runs not finished")
                continue
            fig, ax = plt.subplots(figsize=(4.6, 3.6))
            F.draw_transfer(ax, ctx, src, tgt, None, title=False, ylabel="Accuracy", ours=m)
            fig.subplots_adjust(left=.15, right=.97, top=.95, bottom=.2)
            figures.append((f"transfer_{src}_to_{tgt}", fig))
        for fname, fig in figures:
            fig.savefig(out / f"{fname}.pdf")
            fig.savefig(out / f"{fname}.png", dpi=150)
            every.savefig(fig)
            plt.close(fig)
    every.close()
    md = times_md(ctx)
    (Path(args.out) / "times.md").write_text(md + "\n", encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
