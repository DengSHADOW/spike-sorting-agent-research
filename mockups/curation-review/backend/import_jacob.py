"""Offline, read-only approved MAT/saved-run importer. No model/provider calls.

Only allowlisted MAT input fields enter the bundle; expert terminal assignments
and annotations are never read. Original PNGs and checkpoints are hash checked.
The importer creates a new directory and never overwrites historical files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from datetime import datetime, timezone

import h5py
import numpy as np
from scipy.io import loadmat

ROOT = Path(__file__).resolve().parents[3]
DATASET = "cM2-e004_004-006_CH31"
RUN = Path("zero-shot baseline/runs/qwen9b_rollout_comparison_20261013_8000_thinking6000_v2/attempt_003/minimal") / DATASET
DATASETS = (DATASET, "cM2-e004_004-006_CH3", "cM2-e004_011-015_CH20", "cM2-e008_021-028_CH30")
DEFAULT_OUTPUT = ROOT / "output/curation_review_local/jacob_ch31_v1"
COLORS = ["#09675e", "#99582d", "#6560a5", "#9e3f65", "#3769a0", "#6b651b", "#8a4b1d", "#4a6572"]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def arrays_hash(*arrays):
    h = hashlib.sha256()
    for array in arrays:
        a = np.ascontiguousarray(array)
        h.update(str((a.dtype.str, a.shape)).encode())
        if a.size:
            h.update(memoryview(a).cast("B"))
    return h.hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        f.write("\n")


def scoped_row(dataset, input_path=None):
    # Reject scope before opening the MAT, checkpoints, or expert information.
    if dataset not in DATASETS:
        raise ValueError("Only the four approved development channels can be imported")
    protocol = read_json(ROOT / "configs/curation/protocol_v1.json")
    block = dataset.rsplit("_CH", 1)[0]
    if block not in protocol["scope"]["development_blocks"]:
        raise ValueError("Dataset is not an allowed development block")
    if block in protocol["scope"]["quarantined_blocks"] + protocol["scope"]["reserved_after_prior_exposure"]:
        raise ValueError("Reserved/quarantined dataset")
    rows = read_json(ROOT / "output/real_split_manifest_20260903/manifest.json")["datasets"]
    row = next(r for r in rows if r["dataset_id"] == dataset)
    path = Path(input_path).resolve() if input_path else (ROOT / row["mat_path"]).resolve()
    if not path.is_relative_to(ROOT) or sha256(path) != row["mat_sha256"]:
        raise ValueError("MAT path/hash mismatch")
    return row, path


def load_inputs(path):
    if h5py.is_hdf5(path):
        with h5py.File(path, "r") as f:
            fs = float(f["spikes/Fs"][0, 0])
            times = f["spikes/spiketimes"][:].flatten()
            waves = f["spikes/waveforms"][:]
            over = f["spikes/overcluster/assigns"][:].flatten()
            assigns = f["spikes/hierarchy/assigns"][:].flatten()
            tree = f["spikes/hierarchy/tree"][:]
    else:
        # MAT-v5 decompression loads the struct locally; do not access curation.
        s = loadmat(str(path), struct_as_record=False, squeeze_me=True, variable_names=["spikes"])["spikes"]
        fs = float(np.asarray(s.Fs).squeeze())
        times = np.asarray(s.spiketimes).reshape(-1).astype(float)
        waves = np.asarray(s.waveforms)
        over = np.asarray(s.overcluster.assigns).reshape(-1)
        assigns = np.asarray(s.hierarchy.assigns).reshape(-1)
        tree = np.asarray(s.hierarchy.tree)
        del s
    if waves.shape[0] != len(times):
        waves = waves.T
    if tree.shape[0] != 4 and tree.shape[1] == 4:
        tree = tree.T
    if (waves.ndim != 2 or waves.shape[0] != len(times) or assigns.shape != times.shape
            or over.shape != times.shape or tree.ndim != 2 or tree.shape[0] != 4):
        raise ValueError("MAT input alignment mismatch")
    if fs <= 0 or not all(np.isfinite(a).all() for a in (fs, times, waves, over, assigns, tree)):
        raise ValueError("Invalid MAT inputs")
    for a in (over, assigns):
        if np.any(a < 0) or np.any(a != np.floor(a)):
            raise ValueError("Invalid input labels")
    return fs, times, waves, over, assigns, tree


def unit_details(cid, ids, waves, times, fs):
    # Extra reviewer view: evenly sampled chronological rows, explicitly different
    # from the frozen model's max-500 SHA256 member sample.
    sorted_ids = ids[np.argsort(times[ids], kind="stable")]
    picked = sorted_ids[np.linspace(0, len(ids) - 1, min(24, len(ids)), dtype=int)]
    scatter_ids = sorted_ids[np.linspace(0, len(ids) - 1, min(500, len(ids)), dtype=int)]
    w = waves[ids]
    t = np.sort(times[ids])
    gaps = np.diff(t)
    hist, edges = np.histogram(gaps * 1000, bins=np.linspace(0, 50, 101))
    return dict(cluster_id=cid, waveform=dict(
        x_ms=(np.arange(waves.shape[1]) / fs * 1000).tolist(),
        mean=np.mean(w, axis=0).tolist(), traces=waves[picked].tolist(),
        member_ids=picked.tolist(), sampling="chronologically spaced global spike rows, max 24"),
        amplitude_time=dict(time_seconds=times[scatter_ids].tolist(),
            peak_to_trough=np.ptp(waves[scatter_ids], axis=1).tolist(), member_ids=scatter_ids.tolist()),
        isi=dict(edges_ms=edges.tolist(), counts=hist.tolist(),
            violation_rate=None if len(gaps) == 0 else float(np.mean(gaps < .002)), denominator=len(gaps)))


def state_units(assigns, tree, over, waves, times, fs, channel, cache):
    units, details = [], []
    for cid in sorted(int(c) for c in np.unique(assigns) if c > 0):
        ids = np.flatnonzero(assigns == cid)
        split = False
        merges = np.flatnonzero(tree[0] == cid)
        if len(merges):
            last = int(merges[-1]); breakaway = int(tree[1, last]); members = {breakaway}
            for i in range(last - 1, -1, -1):
                if int(tree[0, i]) in members:
                    members.add(int(tree[1, i]))
            selected = np.isin(over[ids], list(members))
            split = breakaway > 0 and breakaway != cid and selected.any() and not selected.all() and not np.any(assigns == breakaway)
        units.append(dict(id=f"U-{cid:03d}", cluster_id=cid, title=f"Cluster {cid}", channel=channel,
            spikes=len(ids), split=bool(split), color=COLORS[cid % len(COLORS)]))
        key = (cid, hashlib.sha256(ids.tobytes()).hexdigest())
        if key not in cache:
            cache[key] = unit_details(cid, ids, waves, times, fs)
        details.append(cache[key])
    return units, details


def import_initial(dataset, output, row, inputs):
    fs, times, waves, over, assigns, tree = inputs
    state_hash = arrays_hash(assigns, tree)
    units, details = state_units(assigns, tree, over, waves, times, fs, row["channel"], {})
    if not units:
        raise ValueError("No active hierarchy clusters in input")
    output.mkdir(parents=True, exist_ok=False)
    (output / "assets").mkdir(); (output / "evidence").mkdir()
    evidence_name = state_hash + ".json"
    write_json(output / "evidence" / evidence_name, dict(units=details, sampling_rate_hz=fs,
        waveform_units="source units", time_units="seconds", synthetic=False))
    cases = []
    for number, u in enumerate(units, 1):
        cid = dataset + "--initial--" + u["id"]
        cases.append(dict(id=cid, synthetic=False, review_mode="manual", dataset_id=dataset,
            mat_path=row["mat_path"], channel=row["channel"], source=u["id"], title=u["title"],
            step_id="initial_"+u["id"], step_number=number, phase="Initial cluster review", phase_id="phase1",
            pair=None, ai=None, rationale=None, summary="No saved model decision. Inspect the real input and record your own proposal.",
            risk="Manual review", brief="Initial hierarchy · manual review", spikes=u["spikes"], split=u["split"], units=units,
            run_id="initial:"+dataset+":"+row["mat_sha256"], state_id=state_hash, state_sha256=state_hash,
            request_sha256=None, request_hash_status="no model request", image_sha256={}, images=[],
            agent_id=None, agent_kind="none", metrics=dict(sampling_rate_hz=fs,n_spikes=u["spikes"]),
            displayed_trace_count=min(24,u["spikes"]), checkpoint_sha256=None, mat_sha256=row["mat_sha256"],
            evidence_id=state_hash, evidence_url="/api/evidence/"+cid, waveform_units="source units"))
    bundle = dict(schema="spikeagent-jacob-bundle-v1", dataset_id=dataset, source_kind="initial", run_status="not_run",
        created_at=datetime.now(timezone.utc).isoformat(), synthetic=False, n_spikes=len(times), sampling_rate_hz=fs,
        cases=cases, assets={}, evidence_files={state_hash:dict(file=evidence_name,sha256=sha256(output/"evidence"/evidence_name))},
        provenance=dict(mat_path=row["mat_path"],mat_sha256=row["mat_sha256"],actor_input_sha256=arrays_hash(over,times,waves),
            expert_fields_accessed=False,new_model_calls=0,note="Initial MAT hierarchy; manual proposals only. No model result or terminal score."))
    write_json(output / "dataset.json",bundle)
    print(json.dumps(dict(cases=len(cases),dataset_id=dataset,source="initial",n_spikes=len(times))),flush=True)
    return bundle


def import_dataset(dataset, output, source="saved", input_path=None):
    row, mat_path = scoped_row(dataset, input_path)
    output = Path(output).resolve()
    allowed_output = (ROOT / "output/curation_review_local").resolve()
    if not output.is_relative_to(allowed_output) or output == allowed_output:
        raise ValueError("Choose a new child of output/curation_review_local")
    if source not in ("initial", "saved"):
        raise ValueError("Choose initial or saved input mode")
    inputs = load_inputs(mat_path)
    if len(inputs[1]) != row["n_spikes"]:
        raise ValueError("MAT spike count differs from approved manifest")
    if source == "initial":
        return import_initial(dataset,output,row,inputs)
    if dataset not in (DATASET,"cM2-e004_004-006_CH3"):
        raise ValueError("This dataset has no saved decisions in the selected Qwen9B run")
    run_relative = RUN.parent / dataset
    run = ROOT / run_relative
    run_manifest = read_json(run / "manifest.json")
    if run_manifest["dataset_id"] != dataset or run_manifest["view"] != "raw_member_v1":
        raise ValueError("Unexpected saved run")
    fs, times, waves, over, initial_assigns, initial_tree = inputs
    if len(times) != row["n_spikes"] or arrays_hash(over, times, waves) != run_manifest["input_sha256"]:
        raise ValueError("MAT/saved-run input identity mismatch")
    decisions = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
    decisions = [r for r in decisions if r["event"] == "decision"]
    expected_count = 67 if dataset == DATASET else 27
    if len(decisions) != expected_count or [r["calls"] for r in decisions] != list(range(1, expected_count+1)):
        raise ValueError("Unexpected historical decision sequence")
    # Index only this approved run's state metadata. Never replay model actions.
    checkpoints = {}
    for path in sorted(run.glob("*.npz")):
        meta = read_json(path.with_suffix(".json"))
        if (meta.get("dataset_id") != dataset or meta.get("input_sha256") != run_manifest["input_sha256"]
                or sha256(path) != meta["sha256"]):
            raise ValueError("Checkpoint provenance mismatch")
        checkpoints[meta["state_sha256"]] = path
    output.mkdir(parents=True, exist_ok=False)
    (output / "assets").mkdir()
    (output / "evidence").mkdir()
    cases, assets, detail_cache, evidence_files = [], {}, {}, {}
    for decision in decisions:
        step = f"step_{decision['calls']:05d}"
        request = read_json(run / step / "request.json")
        phase = request["phase"]
        if phase != decision["phase"] or phase not in ("phase1", "phase2"):
            raise ValueError("Decision/request phase mismatch")
        state_hash = request["state_sha256"]
        if state_hash not in checkpoints:
            raise ValueError("No verified pre-decision checkpoint")
        with np.load(checkpoints[state_hash], allow_pickle=False) as saved:
            assigns, tree = saved["assigns"], saved["tree"]
        if assigns.shape != times.shape or arrays_hash(assigns, tree) != state_hash:
            raise ValueError("State hash/alignment mismatch")
        obs = request["observation"]
        source = int(obs["cluster_id"] if phase == "phase1" else obs["small_cluster_id"])
        target = None if phase == "phase1" else int(obs["large_cluster_id"])
        if source != decision["cid"] or target != decision["target"]:
            raise ValueError("Decision objects differ from request")
        units, details = state_units(assigns,tree,over,waves,times,fs,row["channel"],detail_cache)
        sizes = {u["cluster_id"]: u["spikes"] for u in units}
        expected = {source: obs["n_spikes"]} if phase == "phase1" else {source: obs["n_small"], target: obs["n_large"]}
        if any(sizes.get(cid) != n for cid, n in expected.items()):
            raise ValueError("Request counts differ from checkpoint/MAT")
        images = []
        expected_names = ["waveform", "isi", "amplitude", "tree"] if phase == "phase1" else ["small_waveform", "large_waveform", "merged_isi"]
        if [v["name"] for v in request["display"]["images"]] != expected_names:
            raise ValueError("Unexpected frozen image set")
        for image in request["display"]["images"]:
            path = run / step / (image["name"] + ".png")
            digest = sha256(path)
            if digest != image["sha256"]:
                raise ValueError("Frozen observation image hash mismatch")
            asset_id = digest + ".png"
            if asset_id not in assets:
                shutil.copyfile(path, output / "assets" / asset_id)
                assets[asset_id] = digest
            images.append(dict(name=image["name"], sha256=digest, url="/api/assets/" + asset_id))
        case_id = dataset + "--qwen9b--" + step
        evidence_id = state_hash
        if evidence_id not in evidence_files:
            evidence_name = evidence_id + ".json"
            write_json(output / "evidence" / evidence_name, dict(units=details, sampling_rate_hz=fs,
                waveform_units="source units", time_units="seconds", synthetic=False))
            evidence_files[evidence_id] = dict(file=evidence_name, sha256=sha256(output / "evidence" / evidence_name))
        source_unit = next(u for u in units if u["cluster_id"] == source)
        display_key = "waveform" if phase == "phase1" else "small_waveform"
        cases.append(dict(id=case_id, synthetic=False, dataset_id=dataset, mat_path=row["mat_path"], channel=row["channel"], source=source_unit["id"],
            title=f"Step {decision['calls']:02d} · Cluster {source}", step_id=step, step_number=decision["calls"],
            phase="Cluster review" if phase == "phase1" else "Pair comparison", phase_id=phase,
            pair=None if target is None else [source_unit["id"], f"U-{target:03d}"],
            ai=decision["action"], rationale=decision["rationale"],
            summary=f"Saved {run_manifest['model']} decision. Review the recorded rationale and evidence.",
            risk="Saved decision", brief=f"{step} · {phase} · {decision['action']}",
            spikes=source_unit["spikes"], split=source_unit["split"], units=units,
            run_id=run_relative.as_posix(), state_id=state_hash, state_sha256=state_hash,
            request_sha256=request["request_sha256"], request_hash_status="recorded by historical runner; payload not rehashed",
            image_sha256={i["name"]: i["sha256"] for i in images}, images=images,
            agent_id="Qwen3.5-9B", agent_kind="historical-model-response", metrics=obs,
            displayed_trace_count=len(request["display"][display_key]["member_ids"]),
            checkpoint_sha256=sha256(checkpoints[state_hash]), mat_sha256=row["mat_sha256"],
            evidence_id=evidence_id, evidence_url="/api/evidence/" + case_id,
            waveform_units="source units"))
        print(f"Imported {step}: {source}->{target} {decision['action']}", flush=True)
    bundle = dict(schema="spikeagent-jacob-bundle-v1", dataset_id=dataset, source_kind="saved",
        run_status="completed" if dataset==DATASET else "stopped_invalid_action",
        created_at=datetime.now(timezone.utc).isoformat(), synthetic=False, n_spikes=len(times),
        sampling_rate_hz=fs, cases=cases, assets=assets, evidence_files=evidence_files,
        provenance=dict(mat_path=row["mat_path"], mat_sha256=row["mat_sha256"], run_path=run_relative.as_posix(),
            run_manifest_sha256=sha256(run / "manifest.json"), events_sha256=sha256(run / "events.jsonl"),
            actor_input_sha256=run_manifest["input_sha256"], verified_checkpoints=len(checkpoints),
            verified_images=len(assets), expert_fields_accessed=False, new_model_calls=0,
            note="Previously exposed development channel. Independent historical pre-decision states; reviews do not advance or execute a rollout."))
    write_json(output / "dataset.json", bundle)
    print(json.dumps(dict(output=str(output), cases=len(cases), n_spikes=len(times), verified_images=len(assets)), indent=2))
    return bundle


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source", choices=["initial","saved"], default="saved")
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    import_dataset(args.dataset, args.output, args.source, args.input)
