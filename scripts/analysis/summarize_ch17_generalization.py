"""CH17 generalization (read-only; never calls a model): the four frozen transfer agents and the base agent on CH17.

Reads output/shadow/generalization_ch17_20261102/code/output/ch17/<model>_s<seed> and writes RESULTS.md next to the
job: accuracy per model (mean ± s.e. over 3 seeds), by Jacob's action, false DISCARDs, run time, failures, and the
rules each agent carried.

    .venv/bin/python scripts/analysis/summarize_ch17_generalization.py
"""
from __future__ import annotations

import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plot_replay_final_figures as F  # noqa: E402

JOB = Path("output/shadow/generalization_ch17_20261102")
RUNS = JOB / "code/output/ch17"
MODELS = (("base", "Base agent (zero-shot, never learns)"),
          ("a0_ch31", "Ours learned on CH31, no thinking"),
          ("a0_ch3", "Ours learned on CH3, no thinking"),
          ("c1_ch31", "Ours learned on CH31, rule proposer thinking"),
          ("c1_ch3", "Ours learned on CH3, rule proposer thinking"))


def main():
    steps = {m: [F.load_steps(RUNS / f"{m}_s{s}") for s in range(3)] for m, _ in MODELS}
    keys = sorted(set.intersection(*[set(x) for ss in steps.values() for x in ss]))
    lines = ["# CH17 generalization: frozen transfer agents on an unseen channel", "",
             f"Development-set result. CH17 (`cM2-e004_011-015_CH17`), {len(keys)} questions shared by all runs, first "
             "answer scored, answers with thinking off; see README.md for the setup. CH31 / CH3 are another recording "
             "(`cM2-e004_004-006`), so this is cross-recording transfer.", "",
             "| model | correct per seed | accuracy (mean ± s.e.) | DISCARD | SPLIT | MERGE | false DISCARD per seed | "
             "run time (mean) | status |", "|---|---|---|---|---|---|---|---|---|"]
    for m, name in MODELS:
        ss = steps[m]
        correct = [sum(s[k][0] for k in keys) for s in ss]
        acc, se = F.mean_se([c / len(keys) for c in correct])
        by = {a: (statistics.mean(sum(s[k][0] for k in keys if s[k][1] == a) for s in ss),
                  sum(ss[0][k][1] == a for k in keys)) for a in ("DISCARD", "SPLIT", "MERGE")}
        fd = [sum(s[k][2] == "DISCARD" and s[k][1] != "DISCARD" for k in keys) for s in ss]
        res = [json.loads((RUNS / f"{m}_s{s}/result.json").read_text()) for s in range(3)]
        lines.append(f"| {name} | {' / '.join(map(str, correct))} of {len(keys)} | {100 * acc:.1f}% ± {100 * se:.1f} | "
                     + " | ".join(f"{v:.1f}/{n}" for v, n in by.values())
                     + f" | {' / '.join(map(str, fd))} | {F.mmss(statistics.mean(r['seconds'] for r in res))} | "
                     f"{', '.join(sorted({r.get('status', '?') for r in res}))} |")
    lines += ["", "DISCARD / SPLIT / MERGE: mean correct over seeds / questions of that kind. Run time: the run's own "
              "record (result.json), one at a time on one H100 80GB, vLLM already running.", "", "## Rules carried", ""]
    for m, name in MODELS[1:]:
        lines.append(f"- **{name}**")
        for s in range(3):
            rules = json.loads((JOB / f"code/output/agents/{m}_s{s}.json").read_text())["rules"]
            lines.append(f"  - seed {s}: " + (" | ".join(r["text"] for r in rules) or "(none)"))
    (JOB / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
