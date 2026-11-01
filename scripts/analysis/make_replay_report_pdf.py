"""One PDF for the lab: setup, method definitions, accuracy tables and notes, then the six final figures (read-only).

The figures and numbers come from plot_replay_final_figures.build(); the text pages are drawn with matplotlib
(the project environment has no other PDF library).

    .venv/bin/python scripts/analysis/make_replay_report_pdf.py --out output/shadow/final_figures_20261101/replay_final_report.pdf
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
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
    "Hardware and serving: Qwen3.5-27B (BF16) on one NVIDIA H200 for the 2026-10-31 runs and one H100 80GB for "
    "SpikeAgent seeds 1-2; vLLM 0.32.0, thinking off, at most 4 requests in flight (--max-num-seqs 4, --parallel 4).",
    "Latency per step: time from sending a step's request(s) to receiving the answer, median over all steps and "
    "seeds. One step is one model call, except SpikeAgent, whose step is 3 modality reports followed by a head "
    "decision (4-5 sequential calls).",
    "Wall-clock per step: run duration divided by the number of steps, with 4 steps in flight; for the learning "
    "rows it includes the learning calls.",
    "Learning calls: the rule proposer (one call every 8 steps) and the evaluator re-answers that screen each "
    "candidate rule set on 24 past steps and re-score the best on up to 60; averaged per step. With the proposer's "
    "thinking on (experiment C1), a proposer call takes about 115 s instead of 7 s.",
    "In deployment with learning switched off, the cost is the frozen row: about 3 s per step, dominated by the "
    "5 images and the transferred expert decisions (about 10k prompt tokens).",
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
    "Ours is the highest in every setting. Its gain over simple context comes mostly from DISCARD questions.",
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
    "Panels a-d: online learning within one channel, CH30, CH20, CH31 and CH3 (CH31 and CH3 are also the transfer "
    "sources). All four methods are drawn as curves (accuracy over the last 15 steps, mean of 3 seeds, band = "
    "±1 s.e.).",
    "Panels e-f: transfer CH31 → CH3 and CH3 → CH31, four bars (SpikeAgent, Base agent, Long-context "
    "agent, Ours). The two 'with_scratch' versions add a fifth bar, ours learned on the target channel itself.",
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


def ledger(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")] if Path(path).exists() else []


def timing_rows(sa_seeds):
    """Per-step cost of each method from the run folders: calls, latency, wall-clock, tokens, learning calls."""
    V2, HY, CH = F.V2, F.HY, F.CHANNELS
    tr = (("ch31", "ch3"), ("ch3", "ch31"))
    sa = [F.SA0] + sorted(p for p in Path(sa_seeds).glob("seed*") if p.is_dir())
    groups = [
        ("Ours, frozen (transfer test)", [HY / f"transfer/hybrid_{a}_to_{b}_s{s}" for a, b in tr for s in range(3)]),
        ("Long-context, frozen (transfer test)", [V2 / f"transfer/context_{a}_to_{b}_s0" for a, b in tr]
         + [HY / f"transfer/context_{a}_to_{b}_s{s}" for a, b in tr for s in (1, 2)]),
        ("Base agent", [V2 / f"base/{c}_s{s}" for c in CH for s in range(3)]),
        ("Long-context, online", [V2 / f"context_online/{c}_s{s}" for c in CH for s in range(3)]),
        ("Ours, online (learning on)", [HY / f"hybrid/{c}_s{s}" for c in CH for s in range(3)]),
        ("SpikeAgent", [p / c for p in sa for c in CH]),
    ]
    rows = []
    for name, runs in groups:
        lat, wall, pt, ct, steps, calls, lcalls = [], [], [], [], 0, 0, 0
        for r in runs:
            sims = [F.read(p) for p in (r / "replay").glob("step_*/simulation.json")]
            sims = [s for s in sims if s.get("provenance") == "expert_recorded" and s.get("timing", {}).get("answer_seconds")]
            if not sims or not (r / "result.json").exists():
                continue
            lat += [s["timing"]["answer_seconds"] for s in sims]
            steps += len(sims)
            wall.append(F.read(r / "result.json")["seconds"] / len(sims))
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
    figures, numbers = F.build(args.spikeagent_seeds)

    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
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
        w.table(["Method", "calls\n/step", "latency\nmedian (s)", "latency\nmean (s)", "wall-clock\n(s/step)",
                 "prompt\ntokens", "output\ntokens", "learning\ncalls/step", "steps"],
                timing_rows(args.spikeagent_seeds), [2.25, .46, .66, .6, .7, .6, .56, .66, .4], size=8)
        for s in TIMING_NOTES:
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
        for i, (name, fig) in enumerate(figures, 1):
            fig.text(.988, .988, f"Figure {i}", ha="right", va="top", fontsize=12, color=F.INK2)
            pdf.savefig(fig)
            plt.close(fig)
    print(out)


if __name__ == "__main__":
    main()
