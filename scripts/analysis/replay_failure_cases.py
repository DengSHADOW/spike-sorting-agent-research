"""Failure cases of the strict-expert-replay runs behind the final figures (read-only; never calls a model).

For each channel, on the same shared questions as scripts/analysis/plot_replay_final_figures.py:
  1. run health of every SpikeAgent run (status, recorded steps, failed answers, seconds per question);
  2. SpikeAgent: Jacob's action x SpikeAgent's answer over all seeds, the ceiling its action set allows, seed agreement;
  3. how often the seeds of each method agree;
  4. ours (rules + context): the questions it gets wrong in every seed, with the measurements, the rules active
     at that question, the model's reason and Jacob's reason.

    .venv/bin/python scripts/analysis/replay_failure_cases.py --out output/shadow/failure_cases_20261101
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plot_replay_final_figures as fig  # noqa: E402

KEYS = ("n_spikes", "firing_rate_hz", "isi_violation_rate", "hill_contamination", "amplitude_cv", "presence_ratio")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def step_dirs(run):
    """{expert_step: step folder} of the recorded expert edits."""
    run = Path(run)
    replay = run / "replay" if (run / "replay").is_dir() else run
    out = {}
    for f in sorted(replay.glob("step_*/simulation.json")):
        s = read(f)
        if s.get("provenance") == "expert_recorded" and s.get("expert_action") is not None:
            out[s["expert_step"]] = f.parent
    return out


def measurements(step):
    c = read(step / "request_content.json") if (step / "request_content.json").exists() else {}
    m = c.get("measurements") or {}
    return m.get("measurements", m) if isinstance(m, dict) else {}


def answer_text(step):
    """The model's stated reason (rules + context runs keep it in response_1.json)."""
    f = step / "response_1.json"
    if not f.exists():
        return ""
    try:
        content = read(f)["choices"][0]["message"]["content"]
        body = json.loads(content[content.find("{"):content.rfind("}") + 1])
        return str(body.get("reason") or body.get("rationale") or body.get("reasoning") or "")
    except (KeyError, IndexError, ValueError):
        return ""


def active_rules(run, version):
    f = Path(run) / "learning/skills" / f"{version}.json"
    return [r["text"] for r in read(f)["rules"]] if f.exists() else []


def short(text, n=260):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True)
    p.add_argument("--spikeagent-seeds", default=str(fig.SA_SEEDS))
    p.add_argument("--examples", type=int, default=6, help="examples of ours' every-seed errors per channel")
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    table = fig.runs_table(Path(args.spikeagent_seeds))
    steps = {k: [fig.load_steps(r) for r in rs] for k, rs in table.items()}
    common = {c: sorted(set.intersection(*[set(s) for (f, m), ss in steps.items() if fig.target(f) == c for s in ss]))
              for c in fig.CHANNELS}
    lines = ["# Failure cases of the replay runs", "",
             "Same questions as the final figures (shared by every run of a channel). Development-set results.", ""]
    report = {}

    lines += ["## 1. SpikeAgent run health", "", "| channel | seed | status | recorded edits | failed answers | "
              "s / question |", "|---|---|---|---|---|---|"]
    for c in fig.CHANNELS:
        for seed, run in enumerate(table[(c, "spikeagent")]):
            res = read(run / "result.json") if (run / "result.json").exists() else {}
            sims = [read(f) for f in sorted((run / "replay").glob("step_*/simulation.json"))]
            failed = sum(s.get("agent_action") in (None, "FAILED") for s in sims)
            secs = [s["timing"]["answer_seconds"] for s in sims if s.get("timing", {}).get("answer_seconds")]
            lines.append(f"| {c.upper()} | {seed} | {res.get('status', 'unknown')} | {len(step_dirs(run))} | {failed} | "
                         f"{sum(secs) / len(secs):.0f} |" if secs else f"| {c.upper()} | {seed} | "
                         f"{res.get('status', 'unknown')} | {len(step_dirs(run))} | {failed} | - |")
    lines.append("")

    for c in fig.CHANNELS:
        keys = common[c]
        expert = {k: steps[(c, "base")][0][k][1] for k in keys}
        kinds = {k: read(step_dirs(table[(c, "base")][0])[k] / "question.json")["kind"] for k in keys}
        r = report[c] = {}
        lines += [f"## {c.upper()} ({len(keys)} questions; Jacob: "
                  + ", ".join(f"{a} {n}" for a, n in sorted(Counter(expert.values()).items())) + ")", ""]

        # SpikeAgent
        sa = steps[(c, "spikeagent")]
        conf = Counter((expert[k], s[k][2]) for s in sa for k in keys)
        reachable = [k for k in keys if (kinds[k] == "single" and expert[k] == "DISCARD")
                     or (kinds[k] == "pair" and expert[k] == "MERGE")]
        agree = sum(len({s[k][2] for s in sa}) == 1 for k in keys) if len(sa) > 1 else None
        r["spikeagent"] = dict(seeds=len(sa), confusion={f"{a}->{b}": n for (a, b), n in sorted(conf.items())},
                               ceiling=len(reachable) / len(keys), seed_agreement=agree)
        lines += [f"**SpikeAgent** ({len(sa)} seed{'s' if len(sa) > 1 else ''}). Its action set has no SPLIT and no pair "
                  f"DISCARD, so at most {len(reachable)}/{len(keys)} ({100 * len(reachable) / len(keys):.0f}%) can be "
                  "right. Jacob's action -> SpikeAgent's answer, summed over seeds: "
                  + ", ".join(f"{a}->{b} {n}" for (a, b), n in sorted(conf.items())) + "."]
        if agree is not None:
            lines.append(f"All seeds give the same answer on {agree}/{len(keys)} questions.")
        lines.append("")

        # seed agreement of the other methods
        lines += ["| method | same answer in all seeds | right in all seeds | wrong in all seeds |", "|---|---|---|---|"]
        for m in ("base", "context", "ours"):
            ss = steps[(c, m)]
            same = sum(len({s[k][2] for s in ss}) == 1 for k in keys)
            allr = sum(all(s[k][0] for s in ss) for k in keys)
            allw = sum(not any(s[k][0] for s in ss) for k in keys)
            r[m] = dict(same=same, all_right=allr, all_wrong=allw)
            lines.append(f"| {m} | {same}/{len(keys)} | {allr} | {allw} |")
        lines.append("")

        # ours: wrong in every seed
        runs = table[(c, "ours")]
        dirs = [step_dirs(run) for run in runs]
        wrong = [k for k in keys if not any(s[k][0] for s in steps[(c, "ours")])]
        kinds_of = Counter((expert[k], steps[(c, "ours")][0][k][2]) for k in wrong)
        ctx_right = sum(any(s[k][0] for s in steps[(c, "context")]) for k in wrong)
        r["ours_all_wrong"] = dict(n=len(wrong), kinds={f"{a}->{b}": n for (a, b), n in kinds_of.items()},
                                   context_right_in_some_seed=ctx_right)
        lines += [f"**Ours wrong in all 3 seeds: {len(wrong)}** (Jacob -> seed-0 answer: "
                  + ", ".join(f"{a}->{b} {n}" for (a, b), n in sorted(kinds_of.items())) + f"); simple context gets "
                  f"{ctx_right} of them right in at least one seed.", ""]
        for k in wrong[:args.examples]:
            d = dirs[0][k]
            sim = read(d / "simulation.json")
            m = measurements(d)
            meas = ", ".join(f"{x}={m[x]:.3g}" if isinstance(m.get(x), (int, float)) else f"{x}={m.get(x)}"
                             for x in KEYS if x in m)
            rules = active_rules(runs[0], sim["skill_version"])
            lines += [f"- step {k}, {kinds[k]}, Jacob {expert[k]}, ours {sim['agent_action']} (seeds: "
                      + "/".join(s[k][2] for s in steps[(c, "ours")]) + f"). {meas}.",
                      f"  - rules active: {'; '.join(rules) if rules else 'none'}",
                      f"  - model's reason (seed 0): {short(answer_text(d))}",
                      f"  - Jacob's reason: {short(sim.get('reason') or '(none recorded)')}"]
        lines.append("")

    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
