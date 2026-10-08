"""Offline expert-state reconstruction and strict action scoring for minimal prompts.

Actor request files never contain targets/reasons. No old PNGs or old prompts
are reused: observations are rendered with the autonomous rollout renderer.
"""
from __future__ import annotations

import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / "output/real_manifest_action_dataset_20260914/datasets"


def assigns_hash(assigns):
    a = np.ascontiguousarray(assigns)
    return hashlib.sha256(str(a.dtype).encode("ascii") + str(a.shape).encode("ascii") + a.tobytes()).hexdigest()


def parse(raw):
    text = str(raw).strip().strip("'\"").strip()
    if m := re.fullmatch(r"s\s+(\d+)", text, flags=re.I):
        return "SPLIT", int(m[1]), None
    if m := re.fullmatch(r"m\s+(\d+)\s+(\d+)", text, flags=re.I):
        target, source = int(m[1]), int(m[2])
        return ("DISCARD", source, None) if target == 0 else ("MERGE", source, target)
    raise ValueError("Unknown expert action")


def apply_expert(m, action, source, target, *, reconstruction_only=False):
    active = set(map(int, m.get_active_clusters()))
    if reconstruction_only and source == 0 and action == 'SPLIT' and np.any(m.assigns == 0):
        active.add(0)  # Recover noise membership in the reference trajectory only.
    if source not in active or (target is not None and target not in active):
        raise ValueError("Inactive expert action object")
    old = m.assigns.copy()
    if action == "SPLIT": m.split_last_merge(source)
    elif action == "DISCARD": m.discard_cluster(source)
    elif action == "MERGE": m.merge_clusters([source, target], target_id=target)
    else: raise ValueError("Unsupported expert action")
    if np.array_equal(old, m.assigns): raise ValueError("Expert no-op")
    m.history.clear()
    m.history_index = -1


def prepare(out, dataset_rows, api):
    jobs, targets, provenance, exclusions = [], [], {}, []
    index = {r["dataset_id"]: r for r in api.accounting.read_json(api.INDEX)["datasets"]}
    for ds in dataset_rows:
        api.require_dataset(ds["dataset_id"])
        path = EXPORT / ds["dataset_id"] / "samples.jsonl"
        provenance[str(path.relative_to(ROOT))] = api.sha256(path)
        samples = api.accounting.read_jsonl(path)
        if len(samples) != index[ds["dataset_id"]]["n_actions"]:
            raise ValueError("Action count differs from audited index")
        m, fs = api.load_actor(ds)
        for i, sample in enumerate(samples, 1):
            if sample["dataset_id"] != ds["dataset_id"] or sample["trajectory_step"] != i:
                raise ValueError("Sample order/provenance mismatch")
            if sample["mat_source"]["sha256"] != ds["mat_sha256"]:
                raise ValueError("Sample MAT mismatch")
            annotation = sample["annotation_source"]
            source_path = ROOT / annotation["path"]
            if not source_path.resolve().is_relative_to(ROOT): raise ValueError("Outside source")
            name = str(source_path.relative_to(ROOT))
            if name not in provenance: provenance[name] = api.sha256(source_path)
            if provenance[name] != annotation["sha256"]: raise ValueError("Annotation source changed")
            action, cid, target = parse(sample["expert_action_raw"])
            if action != sample["label_action"]: raise ValueError("Expert action/label mismatch")
            before = assigns_hash(m.assigns)
            if before != sample["state_before"]["assigns_sha256"]:
                raise ValueError(f"Expert pre-state mismatch: {ds['dataset_id']} step {i}")
            if action == 'SPLIT' and cid == 0:
                apply_expert(m, action, cid, target, reconstruction_only=True)
                after = assigns_hash(m.assigns)
                if after != sample['state_after']['assigns_sha256']:
                    raise ValueError('Excluded reconstruction state mismatch')
                exclusions.append(dict(dataset_id=ds['dataset_id'], trajectory_step=i,
                    expert_action_raw=sample['expert_action_raw'], state_before_sha256=before,
                    state_after_sha256=after, reason='Noise-cluster recovery (s 0) is outside the active-cluster actor interface; replayed only to reconstruct subsequent expert states.'))
                continue
            phase = "phase2" if action == "MERGE" else "phase1"
            obs, urls, pngs, display = api.observe(m, phase, cid, target, sampling_rate=fs, view="raw_member_v1")
            request = api.build_request(phase, obs, urls)
            job_id = f"{ds['dataset_id']}_{i:06d}"
            folder = out / "stepwise_inputs" / job_id
            folder.mkdir(parents=True)
            api.save_request(folder, request, pngs, display)
            api.write_json(folder / "observation.json", obs)
            job = dict(job_id=job_id, dataset_id=ds["dataset_id"], phase=phase,
                trajectory_step=i, source=cid, target=target,
                folder=str(folder.relative_to(out)), request_sha256=api.digest(request),
                payload_sha256=api.sha256(folder / "request_payload.json"),
                observation_sha256=api.sha256(folder / "observation.json"),
                images=display["images"], state_before_sha256=before)
            apply_expert(m, action, cid, target)
            if assigns_hash(m.assigns) != sample["state_after"]["assigns_sha256"]:
                raise ValueError("Expert post-state mismatch")
            jobs.append(job)
            targets.append(dict(job_id=job_id, dataset_id=ds["dataset_id"], phase=phase, expert_action=action,
                source=cid, target=target, annotation_source=annotation, ground_truth_scope=sample["ground_truth_scope"]))
            if i % 10 == 0: print(f"STEPWISE PREFLIGHT {ds['dataset_id']} {i}/{len(samples)}; no API", flush=True)
        print(f"STEPWISE VERIFIED {ds['dataset_id']} {len(samples)} pre/post states", flush=True)
        del m
    audit = out / "stepwise_targets.jsonl"
    with audit.open("x") as f:
        for target in targets: f.write(json.dumps(target, sort_keys=True) + "\n")
    return dict(jobs=jobs, total=len(jobs), sources=provenance, targets_sha256=api.sha256(audit),
        reference_steps=len(jobs)+len(exclusions), excluded=exclusions,
        action_counts=dict(Counter(t["expert_action"] for t in targets)), repeats=1,
        note="Given source/target and phase; no autonomous object-selection score. MERGE positives only, no synthetic KEEP/NOT_MERGE.")


def rebuild(out, job, api):
    folder = out / job["folder"]
    if not folder.resolve().is_relative_to(out.resolve()): raise ValueError("Outside job folder")
    if api.sha256(folder / "request_payload.json") != job["payload_sha256"] or api.sha256(folder / "observation.json") != job["observation_sha256"]:
        raise ValueError("Stepwise input changed")
    urls = []
    for item in job["images"]:
        data = (folder / (item["name"] + ".png")).read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]: raise ValueError("Stepwise image changed")
        urls.append("data:image/png;base64," + base64.b64encode(data).decode())
    req = api.build_request(job["phase"], api.accounting.read_json(folder / "observation.json"), urls)
    if api.digest(req) != job["request_sha256"]: raise ValueError("Stepwise request mismatch")
    return req


def execute(out, spec, provider, api):
    """Does NOT open targets: provider only sees rebuilt actor payloads."""
    for job in spec["jobs"]:
        provider.dataset = job["dataset_id"] + ":stepwise"
        req = rebuild(out, job, api)
        provider.count(req)
        response = provider.respond(req)
        decision = api.accounting.decision_from_response(response, job["phase"])
        row = dict(job_id=job["job_id"], phase=job["phase"], dataset_id=job["dataset_id"], **decision,
                   provider_call=provider.calls, request_sha256=api.digest(req))
        with (out / "stepwise_predictions.jsonl").open("a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
            f.flush()
        print(f"STEPWISE {provider.calls}/{spec['total']} {job['job_id']} {decision['action']}; campaign ${provider.charged:.4f}", flush=True)


def score(targets, predictions):
    t = {r["job_id"]: r for r in targets}
    p = {r["job_id"]: r for r in predictions}
    if len(t) != len(targets) or len(p) != len(predictions) or not set(p) <= set(t):
        raise ValueError("Duplicate/unplanned predictions")
    labels = ["DISCARD", "SPLIT", "MERGE", "KEEP", "NOT_MERGE"]
    confusion = {a: {b: 0 for b in labels} for a in labels}
    for key, row in p.items():
        if row["action"] not in labels: raise ValueError("Invalid predicted action")
        confusion[t[key]["expert_action"]][row["action"]] += 1
    correct = sum(confusion[a][a] for a in labels)
    classes = {}
    for a in labels:
        tp = confusion[a][a]
        support = sum(confusion[a].values())
        predicted = sum(confusion[b][a] for b in labels)
        precision = tp/predicted if predicted else None
        recall = tp/support if support else None
        f1 = 2*tp/(support+predicted) if support and support+predicted else None
        classes[a] = dict(precision=precision, recall=recall, f1=f1, evaluated_support=support,
                          planned_support=sum(r["expert_action"] == a for r in targets), predicted=predicted)
    return dict(planned=len(t), valid=len(p), missing=len(t)-len(p), correct=correct,
        accuracy=correct/len(t) if len(p) == len(t) and t else None,
        accuracy_on_valid=correct/len(p) if p else None, coverage=len(p)/len(t) if t else None,
        per_class=classes, confusion_matrix=confusion,
        note="Rows=expert, columns=model; incomplete valid-only accuracy is not full-set accuracy. Objects supplied, not predicted.")


def collect(out, spec, api):
    if api.sha256(out / "stepwise_targets.jsonl") != spec["targets_sha256"]:
        raise ValueError("Scoring targets changed")
    targets = api.accounting.read_jsonl(out / "stepwise_targets.jsonl")
    path = out / "stepwise_predictions.jsonl"
    preds = api.accounting.read_jsonl(path) if path.exists() else []
    return dict(overall=score(targets, preds),
        by_dataset={d: score([r for r in targets if r["dataset_id"] == d], [r for r in preds if r["dataset_id"] == d])
                    for d in dict.fromkeys(r["dataset_id"] for r in targets)},
        by_phase={phase: score([r for r in targets if r["phase"] == phase], [r for r in preds if r["phase"] == phase])
                  for phase in ("phase1", "phase2")})
