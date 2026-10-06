"""Offline spike-level attribution of completed rollout errors; no API calls.

Replay the saved executed action log with the frozen upstream ClusterManager,
verify exact final assignments, and attribute losses against MAT curation.assigns.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def analyze(root, channel):
    from src.cluster.manager import ClusterManager
    from src.io.matlab_loader import load_matlab_spikes
    protocol = json.loads((root / "protocol.json").read_text())
    out = root / channel
    status = json.loads((out / "status.json").read_text())
    if status["status"] != "complete":
        raise ValueError(f"{channel}: not a complete rollout")
    mat = protocol["mat_files"][channel]
    if sha(Path(mat["path"])) != mat["sha256"]:
        raise ValueError("MAT hash mismatch")
    meta = load_matlab_spikes(mat["path"])
    gt = meta["curation_assigns"]
    manager = ClusterManager(meta["hierarchy_assigns"].copy(), meta["overcluster_assigns"].copy(),
                             meta["hierarchy_tree"].copy(), meta["spiketimes"], meta["waveforms"])
    decisions = [json.loads(s) for s in (out / "decisions.jsonl").read_text().splitlines()]
    events = []
    failed_splits = []
    phases = []
    counts = defaultdict(lambda: {"events": 0, "deleted_spikes": 0, "deleted_expert_spikes": 0})

    def state(label):
        return {"stage": label, "active_clusters": len(manager.get_active_clusters()),
                "retained_expert_spikes": int(np.sum((gt > 0) & (manager.assigns > 0))),
                "retained_gt_noise_spikes": int(np.sum((gt == 0) & (manager.assigns > 0)))}

    phases.append(state("initial"))
    prev_phase = None
    with (out / "action_log.csv").open() as f:
        actions = list(csv.DictReader(f))
    for idx, row in enumerate(actions, 1):
        action, phase, reason = row["Action"], row["Phase"], row["Reason"]
        group = phase.split("_")[0]
        if prev_phase is not None and group != prev_phase:
            phases.append(state(prev_phase + "_end"))
        prev_phase = group
        ids = [int(x) for x in row["Cluster IDs"].split(",")]
        if action == "DISCARD":
            cid = ids[0]
            mask = manager.assigns == cid
            gt_counts = {str(int(k)): int(v) for k, v in zip(*np.unique(gt[mask], return_counts=True))}
            cause = ("phase0_size_filter" if phase == "Phase0" else
                     "phase3_size_filter" if phase.startswith("Phase3") else
                     "phase2_no_merge_target_rule" if "No compatible merge target" in reason or "No large clusters" in reason or "No valid large clusters" in reason else
                     "phase2_vlm_prevalidation_discard" if "PreValidation" in phase else
                     "phase2_vlm_discard" if phase == "Phase2" else "phase1_vlm_discard")
            deleted = int(mask.sum())
            expert = int(np.sum(mask & (gt > 0)))
            candidates = []
            if "vlm" in cause:
                for d in decisions:
                    dcid = d.get("cluster_id", d.get("small_cluster_id"))
                    if dcid == cid and d["action"] == "DISCARD" and d["rationale"] in reason:
                        p = out / "states" / f"call_{d['api_call_id']:05d}.npz"
                        with np.load(p) as saved:
                            if np.array_equal(saved["assigns"], manager.assigns):
                                candidates.append(d["api_call_id"])
            events.append({"action_log_row": idx, "cluster_id": cid, "phase": phase, "cause": cause,
                           "n_spikes": deleted, "expert_spikes": expert, "expert_cluster_counts": gt_counts,
                           "source_api_call_ids": candidates, "reason": reason})
            counts[cause]["events"] += 1
            counts[cause]["deleted_spikes"] += deleted
            counts[cause]["deleted_expert_spikes"] += expert
            manager.discard_cluster(cid)
        elif action == "SPLIT":
            before = manager.assigns.copy()
            try:
                manager.split_last_merge(ids[0])
            except Exception as exc:
                # The pinned pipeline logs SPLIT before attempting it, then
                # preserves the cluster when split_last_merge raises.
                if not np.array_equal(before, manager.assigns):
                    raise RuntimeError("Failed split mutated assignments") from exc
                failed_splits.append({"action_log_row": idx, "cluster_id": ids[0],
                                      "reason": str(exc)})
        elif action == "MERGE":
            manager.merge_clusters(ids, target_id=ids[-1])
        elif action not in ("KEEP", "ABSTAIN"):
            raise ValueError(f"Unknown executed action {action}")
        # History is not read by split/merge/discard; release duplicate arrays to
        # keep this diagnostic lightweight beside the ongoing experiment.
        manager.history.clear()
        manager.history_index = -1
    phases.append(state((prev_phase or "initial") + "_end"))
    final = np.load(out / "final_assigns.npy")
    if not np.array_equal(manager.assigns, final):
        raise RuntimeError("Executed-action replay did not recover exact final assignments")
    per_unit = []
    matched = np.zeros(len(gt), dtype=bool)
    for cid in np.unique(final[final > 0]):
        mask = final == cid
        labels, numbers = np.unique(gt[mask], return_counts=True)
        composition = {str(int(k)): int(v) for k, v in zip(labels, numbers)}
        positives = [(int(n), int(k)) for k, n in zip(labels, numbers) if k > 0]
        best = sorted(positives, key=lambda x: (-x[0], x[1]))[0] if positives else (0, None)
        if best[1] is not None:
            matched |= mask & (gt == best[1])
        per_unit.append({"cluster_id": int(cid), "n_spikes": int(mask.sum()), "matched_gt": best[1],
                         "tp": best[0], "fp": int(mask.sum()) - best[0], "composition": composition})
    per_gt = []
    for gid in np.unique(gt[gt > 0]):
        mask = gt == gid
        per_gt.append({"gt_id": int(gid), "total": int(mask.sum()),
                       "deleted": int(np.sum(mask & (final == 0))),
                       "retained_and_matched": int(np.sum(mask & matched)),
                       "retained_but_mismatched": int(np.sum(mask & (final > 0) & ~matched)),
                       "prediction_clusters": {str(int(k)): int(v) for k, v in zip(*np.unique(final[mask], return_counts=True))}})
    perf = status["performance"]
    assert int(matched.sum()) == perf["total_tp"]
    assert sum(x["deleted_expert_spikes"] for x in counts.values()) == phases[0]["retained_expert_spikes"] - int(np.sum((gt > 0) & (final > 0)))
    return {"channel": channel, "api_calls_added": 0, "final_assignment_replay_exact": True,
            "performance": perf, "stage_retention": phases, "loss_by_cause": dict(counts),
            "largest_expert_losses": sorted(events, key=lambda x: -x["expert_spikes"])[:12],
            "all_deletion_events": events, "final_units": per_unit, "expert_units": per_gt,
            "failed_split_attempts": failed_splits,
            "source_hashes": {str(p.relative_to(root)): sha(p) for p in
                              (out / "action_log.csv", out / "final_assigns.npy", out / "decisions.jsonl")}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-root", required=True, type=Path)
    p.add_argument("--output-root", required=True, type=Path)
    p.add_argument("--channels", nargs="+", default=["CH30", "CH31"])
    args = p.parse_args()
    root = args.run_root.resolve()
    protocol = json.loads((root / "protocol.json").read_text())
    for rel, expected in protocol["source_hashes"].items():
        if sha(root / "upstream" / rel) != expected:
            raise RuntimeError("Frozen source modified")
    sys.path.insert(0, str(root / "upstream"))
    args.output_root.mkdir(parents=True, exist_ok=False)
    for channel in args.channels:
        result = analyze(root, channel)
        (args.output_root / f"{channel}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps({k:result[k] for k in ("channel", "final_assignment_replay_exact", "stage_retention", "loss_by_cause", "largest_expert_losses", "expert_units")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
