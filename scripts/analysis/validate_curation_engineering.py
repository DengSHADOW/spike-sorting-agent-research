"""Stages 4/5 OFFLINE validation; never performs inference or opens reserved MATs.

Scripted decisions below exercise wiring ONLY. No model/accuracy/F1 claims.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import socket
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
from src.agent.curation_contract import build_request, config_hashes, require_development, sha256
from src.agent.curation_observation_v1 import VIEWS, isi_rate, observe
from src.cluster.manager import ClusterManager
from src.io.matlab_loader import load_matlab_spikes
from src.pipeline.curation_engineering_v1 import CurationController, digest, write_json

DATASETS = ["cM2-e004_004-006_CH1", "cM2-e004_004-006_CH3", "cM2-e004_004-006_CH31",
            "cM2-e004_011-015_CH17", "cM2-e004_011-015_CH20", "cM2-e008_021-028_CH30"]
CODE = ["src/agent/curation_observation_v1.py", "src/pipeline/curation_engineering_v1.py",
        "scripts/analysis/validate_curation_engineering.py", "tests/test_curation_engineering_v1.py",
        "src/agent/curation_contract.py", "src/cluster/manager.py", "src/io/matlab_loader.py",
        "scripts/run/run_skill_replay.py"]


class ScriptedOffline:
    kind = "SCRIPTED_OFFLINE_TEST_NOT_MODEL"
    def count(self, request):
        return 100  # Fictional usage for exercising ledger, not a token estimate.

    def respond(self, request):
        phase = json.loads(request["input"][0]["content"][0]["text"].split("\nOBSERVATION\n")[1])["phase"]
        answer = dict(action="KEEP" if phase == "phase1" else "NOT_MERGE",
                      rationale="SCRIPTED engineering response, not inference or expert label")
        return dict(status="completed", model="gpt-6-astra", reasoning={"effort": "high"},
            output=[dict(type="message", role="assistant", status="completed",
                         content=[dict(type="output_text", text=json.dumps(answer))])],
            usage=dict(input_tokens=100, output_tokens=10, input_tokens_details=dict(cached_tokens=0)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args(argv)
    out = Path(args.output_root).absolute()
    if out.resolve() != out or not out.is_relative_to(ROOT / "output") or out == ROOT / "output":
        raise ValueError("Use a new non-symlink child directory of output/")
    out.mkdir(parents=True, exist_ok=False)
    def forbidden(*args, **kw):
        raise AssertionError("Offline validation: network forbidden")
    socket.socket.connect = forbidden
    import scripts.run.run_skill_replay as replay
    replay.client_factory = forbidden
    started = monotonic()
    before = config_hashes()
    protected = [ROOT / "output/curation_skill_preparation_20261006/audit_cases.jsonl",
                 ROOT / "output/curation_skill_replay_20261006_after_topup/responses.jsonl",
                 ROOT / "output/curation_skill_replay_20261006_after_topup/score.json"]
    protected_hashes = {str(p.relative_to(ROOT)): sha256(p) for p in protected}
    index = json.loads((ROOT / "output/real_split_manifest_20260903/manifest.json").read_text())
    rows = {r["dataset_id"]: r for r in index["datasets"]}
    results = []
    for dataset in DATASETS:
        require_development(dataset)  # Before ANY sample or MAT access.
        row = rows[dataset]
        mat = ROOT / row["mat_path"]
        if mat.resolve().parent.name != dataset or not mat.resolve().is_relative_to(ROOT):
            raise ValueError("Manifest path does not match allowed dataset")
        if sha256(mat) != row["mat_sha256"]:
            raise ValueError(f"MAT hash mismatch: {dataset}")
        data = load_matlab_spikes(str(mat))
        m = ClusterManager(data["hierarchy_assigns"], data["overcluster_assigns"],
                           data["hierarchy_tree"].copy(), data["spiketimes"], data["waveforms"])
        dest = out / dataset
        dest.mkdir()
        active = list(map(int, m.get_active_clusters()))
        counts = {cid: int(np.count_nonzero(m.assigns == cid)) for cid in active}
        ordered = sorted(active, key=lambda c: (counts[c], c))
        cid, target = ordered[0], ordered[-1]
        if cid == target:
            raise ValueError("Validation requires two active clusters")
        comparison = []
        for cluster in active:
            times = m.spike_times[m.assigns == cluster]
            new = isi_rate(times)
            old = isi_rate(np.concatenate([times, times[:1]]))
            comparison.append(dict(cluster_id=cluster, n_spikes=len(times),
                                   corrected_isi_rate=new, legacy_duplicated_first_rate=old,
                                   difference_percentage_points=None if new is None else (old-new)*100))
        observations = []
        for phase in ("phase1", "phase2"):
            for view in VIEWS:
                plot_cid = target if phase == "phase1" else cid
                obs, urls, pngs, meta = observe(m, phase, plot_cid, target if phase == "phase2" else None,
                                               sampling_rate=data["Fs"], view=view)
                repeated = observe(m, phase, plot_cid, target if phase == "phase2" else None,
                                   sampling_rate=data["Fs"], view=view)
                assert pngs == repeated[2], "Repeated rendering changed bytes"
                folder = dest / f"{phase}_{view}"
                folder.mkdir()
                for image, png in zip(meta["images"], pngs):
                    (folder / f"{image['name']}.png").write_bytes(png)
                requests = [build_request(phase, obs, urls, skill_version=v) for v in ("v0", "v1")]
                assert replay.without_skill(requests[0]) == replay.without_skill(requests[1])
                if phase == "phase2" and view == "summary_member_v1":
                    assert meta["small_waveform"]["ylim"] == meta["large_waveform"]["ylim"]
                record = dict(phase=phase, view=view, metrics=obs, display=meta,
                              request_hashes={v: digest(r) for v, r in zip(("v0", "v1"), requests)},
                              deterministic_bytes=True, skill_pair_invariant=True)
                write_json(folder / "observation.json", record)
                observations.append(record)
        c = CurationController(m, dataset, dest / "scripted_boundary_test", provider=ScriptedOffline(),
                               budget_usd=1, max_calls=2, sampling_rate=data["Fs"])
        initial = c.state_hash()
        for phase in ("phase1", "phase2"):
            d = c.ask(phase, cid, target if phase == "phase2" else None)
            c.apply(d["action"], cid, target if phase == "phase2" else None)
        assert c.state_hash() == initial
        restored = []
        # Explicit test operations, NOT model decisions, never persisted to MAT.
        for action in ("DISCARD", "MERGE"):
            c.apply(action, cid, target)
            assert c.state_hash() != initial
            c.restore(c.out / "initial.npz")
            assert c.state_hash() == initial
            restored.append(action)
        assert sha256(mat) == row["mat_sha256"]
        record = dict(dataset_id=dataset, mat_sha256=row["mat_sha256"],
                      n_spikes=len(m.assigns), n_clusters=len(active), source_refractory_seconds=data["refractory_period"],
                      used_refractory_seconds=.002, source_cluster=cid, target_cluster=target,
                      observations=observations, isi_comparison=comparison,
                      scripted_requests=2, api_calls=0, actual_cost_usd=0,
                      restored_operations=restored, state_preserved=True)
        write_json(dest / "validation.json", record)
        results.append(record)
        print(f"PASS {dataset}: {len(active)} cluster metrics; four observation configurations; DISCARD/MERGE restored", flush=True)
        del c, m, data
    assert before == config_hashes()
    assert protected_hashes == {str(p.relative_to(ROOT)): sha256(p) for p in protected}
    summary = dict(status="passed", mode="OFFLINE_ENGINEERING_NOT_MODEL_EVALUATION",
                   api_calls=0, actual_cost_usd=0, elapsed_seconds=monotonic()-started,
                   datasets=results, config_hashes=before, protected_hashes=protected_hashes,
                   code_hashes={p: sha256(ROOT / p) for p in CODE},
                   packages={p: importlib.metadata.version(p) for p in ("numpy", "matplotlib", "h5py", "scipy")},
                   limitations=["Scripted responses do not validate provider availability or model quality",
                                "Plots have no paid predictive A/B; do not claim improvement",
                                "No full real-data autonomous rollout; stage 6 not started",
                                "Tree retains legacy max(child heights)+(1-similarity) formula; layout independently implemented",
                                "Existing skill replay and source MATs unchanged"])
    write_json(out / "summary.json", summary)
    print(json.dumps(dict(status="passed", datasets=len(results), api_calls=0, output=str(out))))


if __name__ == "__main__":
    main()
