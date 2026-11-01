"""Offline audit of one harness v2 rollout: was each decision right for the cluster it was asked about?

For every decision, reconstructs the asked cluster at decision time (the state saved before the round's
first operation) and reports its expert composition (noise / one unit / mixed), the final and the model's
own action, the gate that changed it, and the model's labels. Reads MAT expert labels: offline analysis
only, never model input.

    .venv/bin/python scripts/analysis/audit_harness_v2_decisions.py output/<rollout run directory>
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.analysis import run_annotated_offline_references as existing

PURE = .95  # a cluster with at least this noise fraction is "noise", at most 1 - PURE is "unit"


def state(roll, k):
    path = roll / f"operation_{k:05d}_before.npz"
    if path.exists():
        return np.load(path)["assigns"]
    for name in ("stopped.npz", "final.npz", "terminal.npz"):
        if (roll / name).exists():
            return np.load(roll / name)["assigns"]
    raise FileNotFoundError(f"No saved state in {roll}")


def decisions(run, gt):
    """Decisions of a round are asked on the state before the round's first executed operation."""
    roll = run / "rollout"
    rows, pending, op = [], [], 0

    def flush():
        if pending:
            assigns = state(roll, op + 1)
            for e in pending:
                ids = assigns == e["cid"]
                gate = e.get("gate") or {}
                rows.append(dict(kind=e["kind"], cid=e["cid"], n=int(ids.sum()),
                                 noise=float(np.mean(gt[ids] == 0)) if ids.any() else None,
                                 action=e["action"], model=gate.get("model_action", e["action"]), gate=gate.get("id"),
                                 labels={k: v["label"] for k, v in e["observations"].items()}))
            pending.clear()

    for line in open(roll / "events.jsonl"):
        e = json.loads(line)
        if e["event"] == "decision":
            pending.append(e)
        elif e["event"] == "executed":
            flush()
            op += 1
        elif e["event"] == "round":
            flush()
    flush()
    return rows


def main(argv=None):
    run = ROOT / (argv or sys.argv[1:])[0]
    data = existing.load_data(json.loads((run / "run_manifest.json").read_text())["dataset"])
    rows = decisions(run, data["curation_assigns"])
    singles = [r for r in rows if r["kind"] == "single" and r["noise"] is not None]
    kind = lambda r: "noise" if r["noise"] >= PURE else "unit" if r["noise"] <= 1 - PURE else "mixed"
    print(f"{run.name}: {len(rows)} decisions, {len(singles)} single judgements")
    for c in ("noise", "unit", "mixed"):
        rs = [r for r in singles if kind(r) == c]
        print(f"  {c:5s} {len(rs):4d}  final {dict(Counter(r['action'] for r in rs))}  "
              f"model {dict(Counter(r['model'] for r in rs))}")
        for f in ("morphology", "spike_like_parts", "refractory"):
            print(f"        {f}: {dict(Counter(r['labels'].get(f) for r in rs))}")
    pairs = [r for r in rows if r["kind"] == "pair"]
    print("  pairs", dict(Counter(r["action"] for r in pairs)), "gates", dict(Counter(r["gate"] for r in rows if r["gate"])))
    print(f"  DISCARDs touching units (noise < {PURE}):")
    for r in rows:
        if r["action"] == "DISCARD" and r["noise"] is not None and r["noise"] < PURE:
            print("   ", r["kind"], r["cid"], r["n"], f"noise {r['noise']:.2f}", "model", r["model"], "gate", r["gate"],
                  r["labels"])


if __name__ == "__main__":
    main()
