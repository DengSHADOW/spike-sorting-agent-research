"""Prepare development-only skill cases and request previews. NO API calls.

Images/states are referenced and hashed, never regenerated or overwritten.
Targets live in audit_cases.jsonl, separate from actor request previews.
This is targeted diagnosis, not a balanced accuracy benchmark or live rollout.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.agent.curation_contract import (
    build_request, config_hashes, load_protocol, require_development, sha256,
)

EXPORT = ROOT / "output/real_manifest_action_dataset_20260914"
MANIFEST = ROOT / "output/real_split_manifest_20260903/manifest.json"
RUNS = {
    "astra": ROOT / "output/astra_high_channel_guards_budget70_resume_20260930",
    "gpt51": ROOT / "output/upstream_baseline_bfcca625_gpt51_seed0_budget4000_20260928",
}
SELECTED = {("astra", "CH30"): [6, 83, 190, 210], ("astra", "CH31"): [1, 161, 165],
            ("gpt51", "CH30"): [6, 12], ("gpt51", "CH31"): [1, 3]}


def rows(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def terminal_constraint(labels):
    """Describe terminal evidence; NEVER invent a unique human next action."""
    values, counts = np.unique(labels, return_counts=True)
    composition = {str(int(k)): int(v) for k, v in zip(values, counts)}
    n = len(labels)
    positive = n - composition.get("0", 0)
    constraint = ("discard_loses_expert_spikes_next_action_not_unique" if positive == n
                  else "all_members_terminal_noise_next_action_not_unique" if positive == 0
                  else "mixed_terminal_membership_next_action_not_unique")
    return {"target_type": "derived_terminal_constraint", "expert_action": None,
            "composition": composition, "constraint": constraint,
            "expert_positive_spikes": positive, "n_spikes": n,
            "independent_sample": False}


def save_case(out, case_id, phase, observation, image_paths, layout, audit):
    require_development(audit["dataset_id"])
    urls, image_records = [], []
    for path in image_paths:
        path = path.resolve()
        if not path.is_relative_to(ROOT / "output") or path.suffix.lower() != ".png":
            raise ValueError("Only existing repository output PNGs may be used")
        data = path.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"Not a PNG: {path}")
        image_records.append({"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(data).hexdigest()})
        urls.append("data:image/png;base64," + base64.b64encode(data).decode())
    previews = []
    for version in ("v0", "v1"):
        request = build_request(phase, observation, urls, skill_version=version, layout=layout)
        for content, record in zip(request["input"][0]["content"][1:], image_records):
            content["image_url"] = "LOCAL_PREVIEW_ONLY:" + record["path"]
        preview = {"sendable": False, "api_calls": 0, "request_preview": request,
                   "images": image_records, "config_hashes": config_hashes()}
        preview_path = out / "actor" / f"{case_id}_{version}.json"
        write_json(preview_path, preview)
        previews.append(str(preview_path.relative_to(out)))
    return {"case_id": case_id, "phase": phase, "image_layout": layout,
            "request_previews": previews, "images": image_records, **audit}


def expert_cases(out, manifest):
    cases, rationales, sources = [], [], {}
    for meta in manifest["datasets"]:
        if meta["recording_block"] not in load_protocol()["scope"]["development_blocks"]:
            continue  # Do not open reserved datasets, not even their sample JSONL.
        dataset = meta["dataset_id"]
        require_development(dataset)
        if not meta["n_actions"]:
            continue
        source = EXPORT / "datasets" / dataset / "samples.jsonl"
        sources[str(source.relative_to(ROOT))] = sha256(source)
        selected = set()
        for row in rows(source):
            if row["dataset_id"] != dataset:
                raise ValueError("Dataset/source mismatch")
            if row.get("label_reason"):
                rationales.append({"id": row["id"], "dataset_id": dataset,
                                   "source": row["annotation_source"],
                                   "action": row["label_action"], "reason": row["label_reason"]})
            key = (row["stage"], row["label_action"])
            if key in selected:
                continue
            selected.add(key)  # First recorded example per action/stage, no outcome-based selection.
            metrics = row["metrics"]
            if row["stage"] == "split":
                match = re.search(r"Cluster (\d+) metrics:", row["prompt"])
                if not match:
                    raise ValueError("Missing explicit cluster ID in exported observation")
                phase = "phase1"
                obs = {"cluster_id": int(match[1]), **{k: metrics[k] for k in
                       ("n_spikes", "n_overclusters", "isi_violation_rate", "amplitude_cv")}}
                order = ["waveform_overlay", "isi_histogram", "amplitude_distribution", "aggregation_tree"]
            else:
                phase = "phase2"
                obs = {"small_cluster_id": row["small_cluster_id"], "large_cluster_id": row["large_cluster_id"],
                       "n_small": metrics["small_n_spikes"], "n_large": metrics["large_n_spikes"],
                       "small_isi_rate": metrics["small_isi_rate"], "large_isi_rate": metrics["large_isi_rate"],
                       "merged_isi_rate": metrics["merged_isi_rate"], "correlation": metrics["waveform_correlation"]}
                order = ["small_waveform_overlay", "large_waveform_overlay", "merged_isi_histogram"]
            obs["refractory_ms"] = 2.0  # Existing export metric definition, not a newly calibrated threshold.
            cases.append(save_case(out, "expert_" + row["id"], phase, obs,
                [EXPORT / row["images"][k] for k in order], "local_four", {
                    "dataset_id": dataset, "target_type": "recorded_expert_edit",
                    "expert_action": row["label_action"], "annotation_source": row["annotation_source"],
                    "state_before": row["state_before"], "ground_truth_scope": row["ground_truth_scope"],
                    "source_jsonl": str(source.relative_to(ROOT)), "source_sample_id": row["id"],
                    "original_prompt_sha256": hashlib.sha256(row["prompt"].encode()).hexdigest(),
                    "selection": "first-recorded-per-action-stage-not-random"}))
    return cases, rationales, sources


def historical_cases(out, manifest):
    from src.io.matlab_loader import load_matlab_spikes
    cases, sources = [], {}
    lookup = {row["mat_sha256"]: row for row in manifest["datasets"]}
    for channel in ("CH30", "CH31"):
        reference = json.loads((RUNS["astra"] / "protocol.json").read_text())["mat_files"][channel]
        dataset = lookup[reference["sha256"]]["dataset_id"]
        require_development(dataset)
        mat = Path(reference["path"])
        if sha256(mat) != reference["sha256"]:
            raise ValueError("Source MAT hash changed")
        meta = load_matlab_spikes(str(mat))
        gt = np.asarray(meta["curation_assigns"]).astype(int)
        for name, root in RUNS.items():
            protocol = json.loads((root / "protocol.json").read_text())
            if protocol["mat_files"][channel]["sha256"] != reference["sha256"]:
                raise ValueError("Runs do not share the same MAT/index domain")
            directory = root / channel
            requests = {r["call_id"]: r for r in rows(directory / "requests.jsonl")}
            decisions = {d["api_call_id"]: d for d in rows(directory / "decisions.jsonl")}
            with (directory / "vlm_inputs/vlm_call_log.csv").open() as stream:
                images = {int(r["call_id"]): r for r in csv.DictReader(stream)}
            for path in (root / "protocol.json", directory / "requests.jsonl", directory / "decisions.jsonl"):
                sources[str(path.relative_to(ROOT))] = sha256(path)
            chosen = set(SELECTED[(name, channel)])
            # Add at most one all-noise KEEP and DISCARD per run/channel, bounded scan.
            found = set()
            candidates = [d for d in decisions.values() if d["stage"] == "phase1" and d["action"] in {"KEEP", "DISCARD"}][:40]
            for decision in candidates:
                if decision["action"] in found:
                    continue
                with np.load(directory / "states" / f"call_{decision['api_call_id']:05d}.npz") as state:
                    mask = state["assigns"] == decision["cluster_id"]
                if mask.any() and np.all(gt[mask] == 0):
                    chosen.add(decision["api_call_id"])
                    found.add(decision["action"])
            for call in sorted(chosen):
                req, dec, entry = requests[call], decisions[call], images[call]
                if dec["stage"] != "phase1":
                    raise ValueError("Unexpected historical phase")
                state_path = directory / "states" / f"call_{call:05d}.npz"
                with np.load(state_path) as state:
                    assigns = state["assigns"].copy()
                if assigns.shape != gt.shape:
                    raise ValueError("Spike index domain mismatch")
                mask = assigns == dec["cluster_id"]
                if not mask.any():
                    raise ValueError("Requested cluster absent from state")
                prompt_path = directory / "vlm_inputs" / entry["prompt_file"]
                if sha256(prompt_path) != req["prompt_sha256"] or hashlib.sha256(req["prompt"].encode()).hexdigest() != req["prompt_sha256"]:
                    raise ValueError("Historical prompt bytes changed")
                paths = [directory / "vlm_inputs" / f for f in entry["image_files"].split(";")]
                if [sha256(p) for p in paths] != req["image_sha256_in_order"]:
                    raise ValueError("Historical image bytes/order changed")
                times, waveforms = meta["spiketimes"][mask], meta["waveforms"][mask]
                amplitudes = np.ptp(waveforms, axis=1)
                mean = float(amplitudes.mean())
                obs = {"cluster_id": int(dec["cluster_id"]), "n_spikes": int(mask.sum()),
                       "n_overclusters": int(len(np.unique(meta["overcluster_assigns"][mask]))),
                       "isi_violation_rate": float(np.mean(np.diff(np.sort(times)) < 0.002)) if len(times) > 1 else None,
                       "amplitude_cv": float(amplitudes.std() / mean) if mean > 1e-12 else None,
                       "sampling_rate_hz": float(meta["Fs"]), "refractory_ms": 2.0,
                       "waveform_window_ms": float(waveforms.shape[1] / meta["Fs"] * 1000)}
                cases.append(save_case(out, f"{name}_{channel}_{call:05d}", "phase1", obs, paths,
                    "legacy_three", {"dataset_id": dataset, **terminal_constraint(gt[mask]),
                        "historical_action": dec["action"], "historical_call": call,
                        "original_prompt_sha256": req["prompt_sha256"],
                        "state_path": str(state_path.relative_to(ROOT)), "state_sha256": sha256(state_path),
                        "mat_sha256": reference["sha256"],
                        "selection": "targeted-failure-and-control-not-unbiased",
                        "protocol_difference": "legacy three PNGs reused; numeric observation recomputed identically for v0/v1, not an original-request replay"}))
        if sha256(mat) != reference["sha256"]:
            raise ValueError("Source MAT changed during preparation")
        sources[str(mat.relative_to(ROOT))] = reference["sha256"]
    return cases, sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "output/curation_skill_preparation_20261006")
    args = parser.parse_args()
    out = args.output_root.resolve()
    if not out.is_relative_to(ROOT / "output") or out == ROOT / "output":
        parser.error("Use a new child directory of output/")
    out.mkdir(parents=True, exist_ok=False)
    (out / "actor").mkdir()
    manifest = json.loads(MANIFEST.read_text())
    expert, rationales, source_a = expert_cases(out, manifest)
    history, source_b = historical_cases(out, manifest)
    cases = expert + history
    for filename, records in (("audit_cases.jsonl", cases), ("development_reasoning.jsonl", rationales)):
        (out / filename).write_text("".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in records))
    summary = {"status": "offline-prepared-not-model-evaluated", "api_calls": 0,
        "n_cases": len(cases), "recorded_expert_cases": len(expert), "derived_constraint_cases": len(history),
        "actor_previews": len(cases) * 2, "rationale_rows": len(rationales),
        "rationale_by_dataset": dict(Counter(r["dataset_id"] for r in rationales)),
        "cases_by_dataset": dict(Counter(r["dataset_id"] for r in cases)),
        "scope": load_protocol()["scope"], "config_hashes": config_hashes(),
        "source_hashes": {str(MANIFEST.relative_to(ROOT)): sha256(MANIFEST), **source_a, **source_b},
        "code_hashes": {p: sha256(ROOT / p) for p in ["src/agent/curation_contract.py", "scripts/analysis/prepare_curation_skill.py", "src/agent/context.py", "src/agent/runner.py", "src/pipeline/pure.py"]},
        "limitations": ["Targeted dependent development examples; do not report their accuracy as generalization.",
                        "Legacy three-view diagnostics and local four-view expert states are distinct strata.",
                        "Terminal composition is not a unique next-action ground truth.",
                        "Skill v1 is unvalidated. No live runner was changed. Reserved block is previously exposed."]}
    write_json(out / "summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("status", "api_calls", "n_cases", "actor_previews", "rationale_by_dataset")}, indent=2))


if __name__ == "__main__":
    main()
