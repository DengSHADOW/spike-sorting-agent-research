"""Read-only audit of a Stage 6 run interrupted before final status.

Never loads MAT data, creates an SDK client, or writes into the old run.
A reserved call without a saved response remains unresolved.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.agent.curation_contract import require_development


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


def sha256(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def audit_ledger(root: Path):
    calls, counted = {}, None
    for line, row in enumerate(read_jsonl(root / "provider_ledger.jsonl"), 1):
        event = row["event"]
        if event == "count":
            if counted is not None:
                raise ValueError(f"Unsettled count before line {line}")
            counted = row
        elif event == "reserved":
            number = row["call"]
            if (counted is None or number != len(calls) + 1
                    or row["request_sha256"] != counted["request_sha256"]
                    or row["dataset_id"] != counted["dataset_id"]):
                raise ValueError(f"Count/reservation mismatch at line {line}")
            calls[number] = {"reservation": row, "usage": None, "failure": None}
            counted = None
        elif event in {"usage", "failure"}:
            number = row["call"]
            if number not in calls or calls[number][event] is not None:
                raise ValueError(f"Unmatched/duplicate {event} at line {line}")
            calls[number][event] = row
        else:
            raise ValueError(f"Unknown ledger event at line {line}: {event}")
    saved = {int(p.stem) for p in (root / "responses").glob("*.json")}
    if saved - calls.keys():
        raise ValueError("Saved response without reservation")
    settled = {n for n, call in calls.items() if call["usage"] is not None}
    if settled - saved:
        raise ValueError("Usage ledger row without saved response")
    known = sum(c["usage"]["conservative_usd"] for c in calls.values() if c["usage"])
    held = sum(c["reservation"]["reserved_usd"] for c in calls.values() if not c["usage"])
    return {
        "ledger_sha256": sha256(root / "provider_ledger.jsonl"),
        "reserved_calls": len(calls), "settled_calls": len(settled),
        "failed_calls": [n for n, c in calls.items() if c["failure"]],
        "unresolved_calls": sorted(calls.keys() - settled),
        "saved_without_usage": sorted(saved - settled),
        "count_without_reservation": counted is not None,
        "known_conservative_usd": round(known, 9),
        "unknown_reservation_usd": round(held, 9),
        "known_plus_reserved_usd": round(known + held, 9),
        "standard_known_usd": round(sum(c["usage"]["standard_estimate_usd"]
                                        for c in calls.values() if c["usage"]), 9),
    }


def phase2_bound(path: Path, events: list[dict]):
    decisions = [e for e in events if e["event"] == "decision" and e["phase"] == "phase2"]
    if not decisions:
        return None
    first_call = decisions[0]["calls"]
    n_ops = sum(e["event"] == "executed" and e["calls"] < first_call for e in events)
    checkpoint = path / (f"operation_{n_ops:05d}_after.npz" if n_ops else "initial.npz")
    with np.load(checkpoint, allow_pickle=False) as saved:
        assigns = saved["assigns"]
    ids, counts = np.unique(assigns[assigns > 0], return_counts=True)
    small = {int(x) for x in ids[counts < 4000]}
    large = {int(x) for x in ids[counts >= 4000]}
    sources = {int(e["cid"]) for e in decisions}
    targets = {int(e["target"]) for e in decisions}
    if not sources <= small or not targets <= large:
        raise ValueError("Phase 2 IDs disagree with entry checkpoint")
    last = decisions[-1]
    partial = 0
    if last["action"] == "NOT_MERGE":
        done = any(e["event"] == "preserved_unresolved" and e.get("cid") == last["cid"]
                   and e["calls"] >= last["calls"] for e in events)
        if not done:
            tried = {int(e["target"]) for e in decisions if e["cid"] == last["cid"]}
            partial = len(large - tried)
    return {
        "entry_checkpoint": checkpoint.name, "initial_small": len(small),
        "initial_large": len(large), "sources_seen": len(sources),
        "unseen_small": len(small - sources), "observed_targets": len(targets),
        "all_large_targets_seen": targets == large,
        "maximum_remaining_pair_calls": len(small - sources) * len(large) + partial,
    }


def request_uniqueness(path: Path):
    keys, pairs = [], []
    for request_file in sorted(path.glob("step_*/request.json")):
        saved = read_json(request_file)
        observation = saved["observation"]
        source = observation.get("small_cluster_id", observation.get("cluster_id"))
        target = observation.get("large_cluster_id")
        keys.append((saved.get("context") or saved["phase"], source, target,
                     saved["state_sha256"]))
        if saved["phase"] == "phase2":
            pairs.append((source, target))
    return {"saved_requests": len(keys), "duplicate_state_requests": len(keys) - len(set(keys)),
            "phase2_pair_requests": len(pairs), "duplicate_phase2_pairs": len(pairs) - len(set(pairs))}


def audit_dataset(root: Path, row: dict):
    path = root / "datasets" / row["dataset_id"]
    if not path.is_dir():
        return {"dataset_id": row["dataset_id"], "status": "not_started", "decisions": 0}
    events = read_jsonl(path / "events.jsonl")
    decisions = [e for e in events if e["event"] == "decision"]
    summary = path / "summary.json"
    status = read_json(summary)["status"] if summary.is_file() else "interrupted_without_summary"
    return {
        "dataset_id": row["dataset_id"], "status": status, "decisions": len(decisions),
        "by_phase": dict(Counter(e["phase"] for e in decisions)),
        "by_action": dict(Counter(e["action"] for e in decisions)),
        "executed": dict(Counter(e["action"] for e in events if e["event"] == "executed")),
        "request_uniqueness": request_uniqueness(path),
        "phase2": phase2_bound(path, events),
    }


def audit(root: Path):
    root = Path(root)
    manifest = read_json(root / "manifest.json")
    if manifest.get("version") != "stage6-development-rollout-v1":
        raise ValueError("Not a Stage 6 development rollout")
    for row in manifest["datasets"]:
        require_development(row["dataset_id"])
    if not (root / "execution_started.json").is_file():
        raise ValueError("Run has not started")
    status_path = root / "execution_status.json"
    status = read_json(status_path)["status"] if status_path.is_file() else "interrupted_without_final_status"
    ledger = audit_ledger(root)
    return {
        "run_root": str(root), "manifest_sha256": sha256(root / "manifest.json"),
        "campaign_status": status, "ledger": ledger,
        "datasets": [audit_dataset(root, row) for row in manifest["datasets"]],
        "automatic_resume_safe": False,  # This auditor never implements resume.
        "note": "Unresolved reservation is not a confirmed charge; without a saved response ID, its action cannot be retrieved or substituted.",
    }


def report(result: dict):
    ledger = result["ledger"]
    phase2_details = []
    lines = ["# Stage 6 interrupted-run audit", "",
        "Read-only audit; no inference, no repair of the old run, no terminal score for incomplete datasets.", "",
        f"Campaign: {result['campaign_status']}. Automatic resume safe: {result['automatic_resume_safe']}.",
        f"Reserved / settled / unresolved calls: {ledger['reserved_calls']} / {ledger['settled_calls']} / {len(ledger['unresolved_calls'])}.",
        f"Known conservative usage: USD {ledger['known_conservative_usd']:.6f}; unresolved reservation: USD {ledger['unknown_reservation_usd']:.6f} (not a confirmed charge).",
        "", "| Dataset | Status | Decisions | Phase 1 | Phase 2 |", "|---|---|---:|---:|---:|"]
    for d in result["datasets"]:
        phase = d.get("by_phase", {})
        lines.append(f"| {d['dataset_id']} | {d['status']} | {d['decisions']} | {phase.get('phase1', 0)} | {phase.get('phase2', 0)} |")
        uniqueness = d.get("request_uniqueness")
        if uniqueness:
            phase2_details.append(f"{d['dataset_id']}: {uniqueness['saved_requests']} saved requests; "
                                  f"{uniqueness['duplicate_state_requests']} duplicate decision-state keys; "
                                  f"{uniqueness['phase2_pair_requests']} Phase 2 pairs, "
                                  f"{uniqueness['duplicate_phase2_pairs']} repeated pairs.")
        if d.get("phase2"):
            p = d["phase2"]
            phase2_details.append(f"{d['dataset_id']}: Phase 2 began with {p['initial_small']} small and {p['initial_large']} large clusters; {p['unseen_small']} small sources unseen. Maximum remaining pair calls: {p['maximum_remaining_pair_calls']} (MERGE/DISCARD can reduce it).")
    lines.extend(["", *phase2_details, "", f"Unresolved call IDs: {ledger['unresolved_calls']}. Do not automatically resend or score as completed.", ""])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args(argv)
    result = audit(args.run_root)
    args.output_root.mkdir(parents=True, exist_ok=False)
    (args.output_root / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_root / "REPORT.md").write_text(report(result), encoding="utf-8")
    print(report(result))


if __name__ == "__main__":
    main()
