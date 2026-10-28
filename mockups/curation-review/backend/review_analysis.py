"""Read-only reviewer statistics from approved inputs and verified checkpoints.

No expert labels, model calls, controller actions, or historical-file writes.
The single input cache and bounded result cache limit memory use.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import threading

import numpy as np

from import_jacob import ROOT, RUN, DATASETS, arrays_hash, load_inputs, read_json, scoped_row, sha256

VERSION = "reviewer-analysis-v1"
REFRACTORY_MS = 2.0


def interval_statistics(times):
    times = np.asarray(times, dtype=float)
    if times.ndim != 1 or not np.isfinite(times).all():
        raise ValueError("Expected finite spike times in seconds")
    gaps = np.diff(np.sort(times))
    counts, edges = np.histogram(gaps * 1000, bins=np.linspace(0, 50, 101))
    violations = int(np.count_nonzero(gaps < REFRACTORY_MS / 1000))
    return dict(edges_ms=edges.tolist(), counts=counts.tolist(),
        denominator=len(gaps), violation_count=violations,
        violation_rate=violations / len(gaps) if len(gaps) else None,
        refractory_ms=REFRACTORY_MS, outside_display_count=int(np.count_nonzero(gaps * 1000 > 50)),
        definition="count(diff(sort(times_seconds)) < 0.002) / (N - 1); all intervals in denominator")


def population_statistics(waves, times, fs, cluster_id):
    waves, times = np.asarray(waves, dtype=float), np.asarray(times, dtype=float)
    if waves.ndim != 2 or waves.shape[0] != len(times) or not len(times) or waves.shape[1] < 2:
        raise ValueError("Expected aligned nonempty waveform rows")
    if not np.isfinite(waves).all() or not np.isfinite(times).all() or not np.isfinite(fs) or fs <= 0:
        raise ValueError("Nonfinite waveform/time or invalid sampling rate")
    amps = np.ptp(waves, axis=1)
    mean_amp, std_amp = float(np.mean(amps)), float(np.std(amps))
    ordered = amps[np.argsort(times, kind="stable")]
    window = max(1, int(np.ceil(len(ordered) * .2)))
    early, late = float(np.mean(ordered[:window])), float(np.mean(ordered[-window:]))
    return dict(cluster_id=int(cluster_id), n_spikes=len(times),
        isi=interval_statistics(times),
        amplitude=dict(mean_peak_to_trough=mean_amp, std_peak_to_trough=std_amp,
            cv=std_amp / mean_amp if mean_amp > 0 else None, units="source units",
            definition="population std(ddof=0) / mean of per-spike peak-to-trough amplitudes"),
        drift=dict(early_mean=early, late_mean=late, window_spikes=window,
            delta_percent=(late - early) / early * 100 if early > 0 and len(times) >= 2 else None,
            definition="last 20% vs first 20% of spikes in chronological order, not equal-duration windows"),
        waveform=dict(x_ms=(np.arange(waves.shape[1]) / fs * 1000).tolist(),
            mean=np.mean(waves, axis=0).tolist(), std=np.std(waves, axis=0).tolist(),
            definition="all-member mean +/- one population SD at each sample; SD is not confidence interval"))


def compare_populations(source, target):
    a, b = np.asarray(source["waveform"]["mean"]), np.asarray(target["waveform"]["mean"])
    correlation = float(np.clip(np.corrcoef(a, b)[0, 1], -1, 1)) if np.std(a) > 0 and np.std(b) > 0 else None
    return dict(waveform_correlation=correlation,
        definition="Pearson correlation of all-member mean waveforms, aligned at recorded sample indices; no time shifting",
        interpretation="Similarity is descriptive and does not establish that clusters are the same neuron")


class AnalysisService:
    def __init__(self, datasets):
        self.datasets = datasets
        self.lock = threading.RLock()
        self.input_dataset = None
        self.inputs = None
        self.checkpoints = {}
        self.results = OrderedDict()

    def _inputs(self, case, bundle):
        dataset = case["dataset_id"]
        if dataset not in DATASETS:
            raise ValueError("Reviewer analysis is limited to the four approved development datasets")
        if dataset != self.input_dataset:
            row, path = scoped_row(dataset)
            self.input_dataset, self.inputs = None, None
            inputs = load_inputs(path)
            if len(inputs[1]) != row["n_spikes"] or row["mat_sha256"] != case["mat_sha256"]:
                raise ValueError("Input identity does not match this review case")
            if bundle["provenance"].get("actor_input_sha256") != arrays_hash(inputs[3], inputs[1], inputs[2]):
                raise ValueError("Actor input hash mismatch")
            self.input_dataset, self.inputs = dataset, inputs
        return self.inputs

    def _assignments(self, case, bundle, inputs, return_tree=False):
        dataset = case["dataset_id"]
        if case.get("review_mode") == "manual":
            assigns, tree = inputs[4], inputs[5]
        elif case.get("study_id"):
            _,_,root=self.datasets.find_case(case["id"])
            row=bundle.get("study_checkpoints",{}).get(case["state_sha256"])
            if not row or Path(row["file"]).name!=row["file"]:
                raise ValueError("Unknown matched-study checkpoint")
            path=root/"states"/row["file"]
            if (not path.resolve().is_relative_to(root.resolve()) or sha256(path)!=row["sha256"]
                    or row["sha256"]!=case["checkpoint_sha256"]):
                raise ValueError("Matched-study checkpoint integrity mismatch")
            with np.load(path,allow_pickle=False) as saved:assigns,tree=saved["assigns"],saved["tree"]
        else:
            if dataset not in (DATASETS[0], DATASETS[1]):
                raise ValueError("No approved saved checkpoints for this dataset")
            run = (ROOT / RUN.parent / dataset).resolve()
            if case["run_id"] != str(run.relative_to(ROOT)) or bundle["provenance"].get("run_path") != case["run_id"]:
                raise ValueError("Unexpected saved run")
            manifest = run / "manifest.json"
            if sha256(manifest) != bundle["provenance"]["run_manifest_sha256"]:
                raise ValueError("Run manifest hash mismatch")
            if dataset not in self.checkpoints:
                index = {}
                for path in sorted(run.glob("*.npz")):
                    meta = read_json(path.with_suffix(".json"))
                    if meta.get("dataset_id") == dataset and meta.get("input_sha256") == bundle["provenance"]["actor_input_sha256"]:
                        index[(meta["state_sha256"], meta["sha256"])] = path
                self.checkpoints[dataset] = index
            path = self.checkpoints[dataset].get((case["state_sha256"], case["checkpoint_sha256"]))
            if path is None or sha256(path) != case["checkpoint_sha256"]:
                raise ValueError("Verified checkpoint is unavailable")
            with np.load(path, allow_pickle=False) as saved:
                assigns, tree = saved["assigns"], saved["tree"]
        if assigns.shape != inputs[1].shape or arrays_hash(assigns, tree) != case["state_sha256"]:
            raise ValueError("Checkpoint state hash/alignment mismatch")
        return (assigns, tree) if return_tree else assigns

    def correlograms(self, case_id, cluster_id=None, target_id=None):
        from review_correlograms import CorrelogramCache
        case, bundle, _ = self.datasets.find_case(case_id)
        units = {u['id']: u for u in case['units']}
        source_id = cluster_id or case['source']
        if case['dataset_id'] not in DATASETS or source_id not in units or (target_id is not None and (target_id not in units or target_id == source_id or source_id != case['source'])):
            raise ValueError('Choose active clusters in this exact dataset/state')
        with self.lock:
            if not hasattr(self, 'correlation_cache'):
                self.correlation_cache = CorrelogramCache()
            inputs = self._inputs(case, bundle)
            assigns = self._assignments(case, bundle, inputs)
            chosen = [source_id] + ([target_id] if target_id else [])
            times = []
            acg = []
            for uid in chosen:
                u = units[uid]
                t = inputs[1][assigns == u['cluster_id']]
                if len(t) != u['spikes'] or not len(t):
                    raise ValueError('Correlogram membership mismatch')
                times.append(t)
                acg.append(dict(self.correlation_cache.get(t), source=u['cluster_id'], target=u['cluster_id']))
            return dict(schema='review-correlograms-v1', case_id=case_id, dataset_id=case['dataset_id'],
                state_sha256=case['state_sha256'], mat_sha256=case['mat_sha256'],
                source_cluster=units[source_id]['cluster_id'], target_cluster=units[target_id]['cluster_id'] if target_id else None,
                acg=acg, ccg=dict(self.correlation_cache.get(*times), source=units[source_id]['cluster_id'], target=units[target_id]['cluster_id']) if target_id else None,
                new_model_calls=0, execution_status='not_executed')

    def get(self, case_id, cluster_id=None, target_id=None):
        case, bundle, _ = self.datasets.find_case(case_id)
        # Scope and object validation happen before raw inputs/checkpoints are opened.
        if case["dataset_id"] not in DATASETS:
            raise ValueError("Dataset is outside reviewer analysis scope")
        units = {u["id"]: u for u in case["units"]}
        source_id = cluster_id or case["source"]
        if source_id not in units or (target_id is not None and (target_id not in units or target_id == source_id)):
            raise ValueError("Choose different active clusters in this exact dataset/state")
        if target_id is not None and source_id != case["source"]:
            raise ValueError("Candidate pairs must use this case's fixed review source")
        key = (case_id, source_id, target_id)
        with self.lock:
            if key in self.results:
                self.results.move_to_end(key)
                return self.results[key]
            inputs = self._inputs(case, bundle)
            fs, times, waves = inputs[:3]
            assigns = self._assignments(case, bundle, inputs)
            populations = []
            selected_times = []
            for uid in ([source_id, target_id] if target_id is not None else [source_id]):
                cid = units[uid]["cluster_id"]
                ids = np.flatnonzero(assigns == cid)
                if len(ids) != units[uid]["spikes"] or not len(ids):
                    raise ValueError("Cluster count differs from the imported checkpoint")
                selected_times.append(times[ids])
                populations.append(population_statistics(waves[ids], times[ids], fs, cid))
            result = dict(schema=VERSION, case_id=case_id, dataset_id=case["dataset_id"],
                state_sha256=case["state_sha256"], mat_sha256=case["mat_sha256"],
                source_cluster=units[source_id]["cluster_id"],
                target_cluster=units[target_id]["cluster_id"] if target_id is not None else None,
                sampling_rate_hz=fs, units=populations, waveform_units="source units",
                expert_fields_accessed=False, new_model_calls=0, execution_status="not_executed")
            if target_id is not None:
                result.update(pair=compare_populations(*populations), merged_isi=interval_statistics(np.concatenate(selected_times)))
            result["analysis_sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
            self.results[key] = result
            while len(self.results) > 128:
                self.results.popitem(last=False)
            return result
