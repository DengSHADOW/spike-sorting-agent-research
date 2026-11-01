"""One PDF for the lab: setup, method definitions, accuracy and time tables and notes, then the figures (read-only).

The panels and numbers come from plot_replay_final_figures (prepare + draw_*); the text pages are drawn with matplotlib
(the project environment has no other PDF library).

    .venv/bin/python scripts/analysis/make_replay_report_pdf.py --out output/shadow/final_figures_20261101/replay_final_report.pdf
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import textwrap

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plot_replay_final_figures as F  # noqa: E402

PAGE_W, PAGE_H, LEFT, RIGHT, TOP, BOTTOM = 8.5, 11.0, .8, .8, .75, .75  # inches

SETUP = [
    ("Data", "Jacob's recorded curation of four channels. Questions scored per channel: CH30 81, CH20 45, CH31 40, "
             "CH3 71. CH31 and CH3 are taken to come from the same recording (to be confirmed with Jacob)."),
    ("Strict expert replay", "Jacob's edits are replayed in his order. At each step the model sees the cluster (five "
             "images and its measurements) and the offered actions, and answers once; that first answer is scored. "
             "Then Jacob's action is executed, so the state always follows Jacob."),
    ("Human feedback", "After every question, right or wrong, Jacob's recorded action and his written reason are "
             "revealed to the learning methods. No new annotation was collected: the feedback is his existing "
             "curation record."),
    ("Actions", "Single cluster: SPLIT or DISCARD. Pair: MERGE or DISCARD. KEEP and NOT_MERGE are not offered for "
             "now, and steps with only one possible action are executed without asking. SpikeAgent is the "
             "exception: it answers with its own good/bad and merge/not-merge outputs and is scored on the same "
             "questions."),
    ("Runs", "Three seeds per method (same questions, different sampling seeds). SpikeAgent seed 0 is from "
             "2026-10-29; seeds 1 and 2 were run on 2026-11-01 with identical settings."),
    ("Scores", "Learning-curve figures: accuracy over the last 15 steps (fewer at the start), mean over the 3 seeds; "
             "the shaded band is ±1 standard error over seeds (not a confidence interval). Transfer figures and "
             "the tables below: accuracy over all steps of the target channel; whiskers are ±1 standard error "
             "over seeds. The metric is agreement with Jacob's recorded action, not F1."),
]
TIMING_NOTES = [
    "Testing (learning off): every LLM agent answers a step in about 3 s (ours frozen 2.7 s median, base agent 2.7 s, "
    "long-context 2.3 s; Qwen3.5-27B on one H200, thinking off). With 4 steps in flight, a 70-80 step channel takes "
    "3-4 minutes. In a human-in-the-loop run the agent is not the bottleneck; the human's feedback is.",
    "Putting the expert's decisions in the prompt costs almost nothing: ours reads 3x more prompt tokens than the base "
    "agent (10k vs 3k) at the same latency, because the answer is about 76 tokens and the time goes to the 5 images.",
    "Learning is what costs, and the cost is verification, not proposing: with learning on, ours takes 12.9 s of "
    "wall-clock per step, 4.6x the frozen agent. The rule proposer runs once every 8 steps (7 s per call); the "
    "evaluator re-answers past steps to check every candidate rule set, about 10 extra calls per step. An 81-step "
    "channel: about 17 minutes with learning vs 4 minutes frozen. A sparser or error-triggered update schedule "
    "would remove most of this.",
    "SpikeAgent is about 15x slower per step (42 s) and not more accurate: each step is 4-5 sequential model calls "
    "that each write a report of about 400 tokens.",
    "Definitions: latency = request to answer for one step, median and mean over all steps and seeds; wall-clock = run "
    "time divided by steps, 4 steps in flight, learning calls included; learning calls = proposer + evaluator "
    "re-answers per step. SpikeAgent seeds 1-2 ran on one H100 80GB. With the proposer's thinking on (experiment C1) "
    "a proposer call takes about 200 s instead of 7 s, for no measurable gain in accuracy.",
]
METHODS = [
    ("Base agent", "zero-shot: task description, images and measurements. Never learns."),
    ("Long-context agent", "Base plus Jacob's earlier decisions in this channel (measurements, action and reason; "
             "no images), the most recent ones that fit in 40,000 characters."),
    ("Ours", "Long-context agent plus learned rules. Every 8 questions the model proposes rule edits (add, modify, "
             "delete) from a table of the questions revealed so far. An edit is kept only if re-answering the "
             "revealed questions with it fixes more answers than it breaks (fixed minus broken at least 2, more "
             "when many answers flip). At most 6 rules. This is not ACE: ACE was tried first and did no better "
             "than Base."),
    ("Transferred", "learn on the source channel, freeze the rules and Jacob's decisions from it, and use them on "
             "the target channel with no further learning."),
    ("Ours, learned on the target", "Ours learning on the target channel itself (the extra bar in the "
             "'with_scratch' transfer figures). Transfer is only worth it if it beats this."),
    ("SpikeAgent", "the original SpikeAgent curation prompts (upstream 53822ac). Never learns; it has no SPLIT "
             "action."),
]
OBSERVATIONS = [
    "Ours is the highest in every setting. Its gain over the long-context agent comes mostly from DISCARD questions.",
    "The learned rules are mostly one firing-rate threshold (about 1-2 Hz). The curves jump when the first rule is "
    "accepted (around question 8-10) and then stay high.",
    "Transfer CH31 ↔ CH3: ours transferred beats ours learned from scratch on the target by about 3 questions "
    "(5-7 points). The two channels are likely one recording, so this shows reuse within a recording.",
    "SpikeAgent cannot answer SPLIT, so at most 44-50% of the questions are within its reach; it stays near the "
    "always-DISCARD level. Its three seeds give the same answer on 80-91% of the questions.",
    "Questions ours gets wrong in all three seeds (2-6 per channel) fall into three groups: before the first rule is "
    "learned; a threshold set too high (CH20, 2.0 Hz, where Jacob split 1.35-1.9 Hz clusters); and clusters Jacob "
    "discarded for their waveform shape (too broad, monophasic, more than three phases) above the firing-rate "
    "threshold, which a size rule cannot capture.",
]
CAVEATS = [
    "Development-set results: ours was designed on these four channels, after earlier variants failed on them.",
    "That CH3 and CH31 come from one recording is inferred from file names and not yet confirmed.",
    "The seeds share the questions; differences of a question or two between seeds are within noise.",
    "SpikeAgent ran under the older protocol (KEEP offered), scored on the questions shared with the other methods.",
]
FIGURES = [
    "The figures follow on the next pages, each with its caption: a-d online learning on CH30, CH20, CH31 and CH3 "
    "(all four methods as curves, mean of 3 seeds, band = \u00b11 s.e.); a\u2032-d\u2032 the same channels with ours "
    "alone (each seed, their mean, and the steps where the rule proposer runs); e-f transfer CH31 \u2192 CH3 and "
    "CH3 \u2192 CH31 with four bars; e\u2032-f\u2032 the same with a fifth bar, ours learned on the target channel "
    "itself; g the running-time-per-step table; h the training and testing time of the transfer experiments.",
]


class Writer:
    """Top-down text layout on letter pages, starting a new page when one is full."""

    def __init__(self, plt, pdf, preview=None):
        self.plt, self.pdf, self.fig, self.preview, self.n = plt, pdf, None, preview, 0

    def flush(self):
        if self.fig is not None:
            self.pdf.savefig(self.fig)
            if self.preview:
                self.n += 1
                self.fig.savefig(Path(self.preview) / f"text_page_{self.n}.png", dpi=90)
            self.plt.close(self.fig)

    def page(self):
        self.flush()
        self.fig = self.plt.figure(figsize=(PAGE_W, PAGE_H))
        self.y = PAGE_H - TOP

    def _line_h(self, size):
        return size * 1.4 / 72

    def _room(self, height):
        if self.fig is None or self.y - height < BOTTOM:
            self.page()

    def text(self, s, size=10.5, weight="normal", color=F.INK, indent=0.0, after=.35, hang=0.0, lead="", bold=""):
        """A wrapped paragraph; `bold` is a prefix of s drawn in bold (mathtext), `lead` a bullet in the hang."""
        width = PAGE_W - LEFT - RIGHT - indent - hang
        lines = textwrap.wrap(s, max(20, int(width * 72 / (size * .53))))
        h = self._line_h(size)
        self._room(h * len(lines))
        for i, line in enumerate(lines):
            x = LEFT + indent + (hang if i or lead else 0)
            if i == 0 and lead:
                self.fig.text((LEFT + indent) / PAGE_W, self.y / PAGE_H, lead, fontsize=size, color=color, va="top")
            if i == 0 and bold and line.startswith(bold):
                line = "$\\bf{" + bold.replace(" ", "\\ ").replace("-", "\\text{-}") + "}$" + line[len(bold):]
            self.fig.text(x / PAGE_W, self.y / PAGE_H, line, fontsize=size, fontweight=weight, color=color, va="top")
            self.y -= h
        self.y -= h * after

    def term(self, name, body, size=10.5):
        """'name: body' with the name in bold, wrapped as one paragraph."""
        self.text(f"{name}: {body}", size=size, after=.3, bold=name)

    def bullet(self, s, size=10.5):
        self.text(s, size=size, indent=.12, hang=.2, lead="•", after=.3)

    def heading(self, s):
        self._room(self._line_h(13) * 3)
        self.y -= self._line_h(13) * .4
        self.text(s, size=13, weight="bold", after=.25)

    def table(self, header, rows, widths, size=10):
        h = self._line_h(size) * 1.25
        hl = max(c.count("\n") + 1 for c in header)  # header cells may span several lines
        self._room(h * (len(rows) + hl + .5))
        xs = [LEFT]
        for w in widths[:-1]:
            xs.append(xs[-1] + w)
        right = LEFT + sum(widths)
        for r, cells in enumerate([header] + rows):
            for x, cell in zip(xs, cells):  # a leading * marks a bold cell
                self.fig.text(x / PAGE_W, self.y / PAGE_H, cell.removeprefix("*"), fontsize=size, va="top",
                              color=F.INK, fontweight="bold" if r == 0 or cell.startswith("*") else "normal",
                              linespacing=1.15)
            if r == 0:
                self.y -= h * (hl - 1) * .9
                y = (self.y - h * .85) / PAGE_H
                self.fig.add_artist(self.plt.Line2D([LEFT / PAGE_W, right / PAGE_W], [y, y], color=F.INK2, lw=.8))
            self.y -= h
        self.y -= h * .6

    def close(self):
        self.flush()
        self.fig = None


def figure_pages(pdf, plt, ctx, preview=None):
    """Letter pages with two panels each, every panel drawn in place with its caption."""
    numbers, common = ctx["numbers"], ctx["common"]
    pct = lambda key: f"{100 * numbers[key]['accuracy']:.1f}%"

    def online_caption(letter, c):
        return (f"Figure {letter}. Online learning on {c.upper()}. Accuracy over the last {F.WINDOW} steps as the agent "
                f"works through Jacob's {len(common[c])} edits in his order (mean of 3 seeds; shaded band \u00b11 "
                f"standard error). The learning methods see Jacob's action and reason after every step. Overall "
                f"accuracy: Ours {pct(f'{c}:ours')}, Long-context agent {pct(f'{c}:context')}, Base agent "
                f"{pct(f'{c}:base')}, SpikeAgent {pct(f'{c}:spikeagent')}.")

    def transfer_caption(letter, src, tgt, scratch):
        f, s, t = f"{src}>{tgt}", src.upper(), tgt.upper()
        text = (f"Figure {letter}. Transfer {s} \u2192 {t}. The agent learns on {s}; its rules and the {s} decisions are "
                f"then frozen and it answers all {len(common[tgt])} {t} steps with no further learning. Bars: mean "
                f"accuracy over 3 seeds; whiskers \u00b11 standard error. Base agent and SpikeAgent never learn.")
        if scratch:
            d = 100 * (numbers[f"{f}:ours"]["accuracy"] - numbers[f"{f}:scratch"]["accuracy"])
            text += (f" The fifth bar is ours learning on {t} itself from scratch ({pct(f'{f}:scratch')}); the "
                     f"transferred agent is {d:.1f} points higher.")
        return text

    timing_caption = ("Figure g. Running time per step. Testing with learning off costs about 3 s per step for every "
                      "LLM agent (3-4 minutes per channel with 4 steps in flight); the expert's decisions in the prompt "
                      "add no time because the answer is about 76 tokens and the 5 images dominate. Learning raises "
                      "the cost to 12.9 s per step (4.6x), almost all of it the evaluator re-answering past steps to "
                      "verify candidate rules (about 10 calls per step), not the proposer (one call per 8 steps). "
                      "SpikeAgent is about 15x slower (4-5 sequential calls per step) and not more accurate. Latency: "
                      "request to answer for one step, median and mean over steps and seeds; wall-clock: run time "
                      "divided by steps.")

    def ours_caption(letter, c):
        r = numbers[f"{c}:ours"]
        return (f"Figure {letter}. Online learning on {c.upper()}, ours only: the runs of figure {letter[0]} without the "
                f"baselines. Thin lines: the 3 seeds; thick line: their mean; band: \u00b11 standard error; dotted "
                f"lines: the rule proposer runs after every {F.RULE_EVERY} steps (an accepted edit applies from the next "
                f"step). Overall accuracy {pct(f'{c}:ours')}; correct per seed {' / '.join(map(str, r['per_seed_correct']))} "
                f"of {len(common[c])}.")

    transfer_time_caption = ("Figure h. Training and testing time per channel in the transfer experiments: wall-clock "
                             "of each run (min:s) per seed and mean. Training = ours learning online on the source "
                             "channel; testing = the frozen agent on the target channel. The transferred long-context "
                             "agent has no training run (its pool is Jacob's recorded source decisions, no model call); "
                             "ours learned on the target learns while it answers (one run). SpikeAgent asks 57 / 99 "
                             "questions under the older protocol. The breakdown is in the section 'Training and "
                             "testing time in the transfer experiments'.")

    pages = [[("online", "ch30", "a"), ("online", "ch20", "b")],
             [("online", "ch31", "c"), ("online", "ch3", "d")],
             [("online_ours", "ch30", "a\u2032"), ("online_ours", "ch20", "b\u2032")],
             [("online_ours", "ch31", "c\u2032"), ("online_ours", "ch3", "d\u2032")],
             [("transfer", ("ch31", "ch3"), "e"), ("transfer", ("ch3", "ch31"), "f")],
             [("transfer_scratch", ("ch31", "ch3"), "e\u2032"), ("transfer_scratch", ("ch3", "ch31"), "f\u2032")],
             [("timing", None, "g"), ("transfer_time", None, "h")]]
    block, ax_h, ax_w, head = 4.75, 2.7, 5.0, .65
    for n, page in enumerate(pages, 1):
        fig = plt.figure(figsize=(PAGE_W, PAGE_H))
        for i, (kind, arg, letter) in enumerate(page):
            top = PAGE_H - TOP - i * block
            if kind == "timing":
                w_, h_ = 6.9, 2.6
            elif kind == "transfer_time":
                w_, h_ = 6.9, 3.3
            else:
                w_, h_ = ax_w, ax_h
            ax = fig.add_axes([(PAGE_W - w_) / 2 / PAGE_W, (top - head - h_) / PAGE_H, w_ / PAGE_W, h_ / PAGE_H])
            if kind == "online":
                F.draw_online(ax, ctx, arg, letter)
                cap, below = online_caption(letter, arg), .55
            elif kind == "online_ours":
                F.draw_online_ours(ax, ctx, arg, letter)
                cap, below = ours_caption(letter, arg), .55
            elif kind == "timing":
                F.draw_timing(ax, ctx, letter)
                cap, below = timing_caption, .15
            elif kind == "transfer_time":
                F.draw_transfer_time(ax, ctx, letter)
                cap, below = transfer_time_caption, .15
            else:
                F.draw_transfer(ax, ctx, *arg, letter, with_scratch=(kind == "transfer_scratch"))
                cap, below = transfer_caption(letter, *arg, kind == "transfer_scratch"), .7
            fig.text(LEFT / PAGE_W, (top - head - h_ - below) / PAGE_H, "\n".join(textwrap.wrap(cap, 118)),
                     fontsize=9, color=F.INK, va="top", linespacing=1.35)
        pdf.savefig(fig)
        if preview:
            fig.savefig(Path(preview) / f"figure_page_{n}.png", dpi=90)
        plt.close(fig)


def cell(numbers, key, bold=False):
    r = numbers[key]
    se = f" ± {100 * r['se']:.1f}" if r["se"] is not None else ""
    return ("*" if bold else "") + f"{100 * r['accuracy']:.1f}%{se}"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True)
    p.add_argument("--spikeagent-seeds", default=str(F.SA_SEEDS))
    p.add_argument("--preview-dir", help="also save each text page as PNG here, for checking the layout")
    args = p.parse_args()
    ctx = F.prepare(args.spikeagent_seeds)
    numbers = ctx["numbers"]

    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.preview_dir:
        Path(args.preview_dir).mkdir(parents=True, exist_ok=True)
    with PdfPages(out) as pdf:
        w = Writer(plt, pdf, args.preview_dir)
        w.text("Learning from Jacob's curation decisions", size=20, weight="bold", after=.2)
        w.text("Strict expert replay · Qwen3.5-27B, thinking off · final figures, 2026-11-01", size=11.5,
               color=F.INK2, after=.15)
        w.text("All numbers are development-set results: the method was designed on these four channels.",
               size=10.5, color=F.INK2, after=.6)
        w.text("Question: does the model get closer to Jacob's decisions as it sees more of them within one "
               "channel, and does what it learns on one channel carry over to another?", size=11, after=.4)
        w.heading("Setup")
        for name, body in SETUP:
            w.term(name, body)
        w.heading("Methods (names as in the figures)")
        for name, body in METHODS:
            w.term(name, body)

        w.heading("Accuracy on all questions of the channel (mean ± s.e. over 3 seeds)")
        rows = []
        for m, name in (("spikeagent", "SpikeAgent"), ("base", "Base agent"), ("context", "Long-context agent"),
                        ("ours", "Ours")):
            rows.append([name] + [cell(numbers, f"{c}:{m}", m == "ours") for c in F.CHANNELS])
        n = {c: numbers[f"{c}:base"]["n_questions"] for c in F.CHANNELS}
        w.table(["Within one channel"] + [f"{c.upper()} ({n[c]})" for c in F.CHANNELS], rows, [1.9, 1.3, 1.3, 1.3, 1.3])
        rows = []
        for m, name in (("spikeagent", "SpikeAgent"), ("base", "Base agent"), ("context", "Long-context agent, transferred"),
                        ("ours", "Ours, transferred"), ("scratch", "Ours, learned on the target")):
            rows.append([name] + [cell(numbers, f"{s}>{t}:{m}", m == "ours") for s, t in F.TRANSFERS])
        w.table(["Transfer (target accuracy)"] + [f"{s.upper()} → {t.upper()} ({n[t]})"
                                                for s, t in F.TRANSFERS], rows, [2.9, 1.6, 1.6])
        w.heading("Running time per step")
        w.table(F.TIMING_HEADER, F.timing_rows(args.spikeagent_seeds), [2.25, .46, .66, .6, .7, .6, .56, .66, .4],
                size=8)
        for s in TIMING_NOTES:
            w.bullet(s, size=9.5)
        w.heading("Training and testing time in the transfer experiments (wall-clock per channel, min:s)")
        w.table(F.TRANSFER_TIME_HEADER, F.transfer_time_rows(args.spikeagent_seeds),
                [1.8, .95, 1.22, .42, .78, 1.22, .42], size=7.5)
        for s in F.transfer_time_notes(args.spikeagent_seeds):
            w.bullet(s, size=9.5)
        w.heading("Observations")
        for s in OBSERVATIONS:
            w.bullet(s)
        w.heading("Caveats")
        for s in CAVEATS:
            w.bullet(s)
        w.heading("Figures")
        for s in FIGURES:
            w.bullet(s)
        w.text("Runs: /workspace/Lv-replay (2026-10-31) and output/shadow/spikeagent_seeds_20261101 (SpikeAgent "
               "seeds 1-2). Figures: scripts/analysis/plot_replay_final_figures.py; this PDF: "
               "scripts/analysis/make_replay_report_pdf.py.", size=9, color=F.INK2, after=0)
        w.close()
        figure_pages(pdf, plt, ctx, args.preview_dir)
    print(out)


if __name__ == "__main__":
    main()
