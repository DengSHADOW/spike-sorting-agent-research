"""Run an isolated real-data rollout with the retained legacy detailed prompt.

The purpose is reproduction, not the current recommended deployment policy.
It deliberately enables the historical 500/5,000 spike-count filters, while
retaining current safety fixes for provider/parse failures and NOT_MERGE.  It
never overwrites the retained ``output/main_gpt-*`` results.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from io import BytesIO
from pathlib import Path
from typing import Any, Optional, Sequence

import matplotlib.pyplot as plt
import numpy as np
import spikeinterface as si
from dotenv import load_dotenv

from src.agent import api as agent_api
from src.agent import runner as agent_runner
from src.cluster.features import ClusterFeatures
from src.cluster.manager import ClusterManager
from src.eval.metrics import generate_full_evaluation_report
from src.io.matlab_loader import load_matlab_spikes
from src.pipeline import pure as pure_module


PROTOCOL_VERSION = "legacy-artifact-recovered-v2-current-safety"
LEGACY_MAX_WAVEFORMS = 5000

LEGACY_NEURONAL_CRITERIA = """
## Valid Extracellular Action Potential Shape

A neuronal waveform must exhibit a clean extracellular spike morphology.

### Shape Requirements (Neuronal Check)

- **Phases:** Must be strictly biphasic or triphasic.
  - Expected pattern: small initial positive deflection → sharp negative trough → single smooth return toward baseline.
  - Invalid if the waveform shows >3 phases, extra bumps, secondary rises/falls, or multiple depolarization events.

- **Depolarization (Negative Peak):**
  - Should descend rapidly, forming a sharp and well-defined trough.
  - Should not be overly broad in time (peak-to-trough typically < 0.5–0.6 ms).
  - Should not contain pre-trough bumps or irregularities.

- **Repolarization:**
  - Should rise smoothly back toward baseline.
  - Should not include slow drifting, oscillations, or a second hump after the main trough.

- **Baseline Stability:**
  - Beginning and end of the waveform should remain close to baseline (near zero).
  - No large amplitude offsets or drift before or after the main spike.

If any of these conditions are violated, the waveform is **not** considered neuronal.
""".strip()


def build_legacy_phase1_prompt(cluster_id: int, n_spikes: int, n_overclusters: int) -> str:
    """Reconstruct the prompt retained in the 2025 legacy rollout artifacts."""
    return f"""

## STEP 1: Neuronal Shape Check
First check if the waveform shape is neuronal:

Neuronal Shape Criteria:

{LEGACY_NEURONAL_CRITERIA}


If waveforms do NOT have valid neuronal shape → DISCARD immediately.

## STEP 2: Split Decision (only if neuronal)
If waveforms ARE neuronal, check if cluster needs splitting:

Split Criteria (check waveform overlay + ISI histogram):
1. **Waveform Variability:** Do you see multiple distinct waveform families/shapes?
2. **Temporal Consistency:** Does the vertical spread (width) change significantly over time?
3. **ISI Pattern:** Does the ISI histogram suggest multiple units (high violations > 0.006, bimodal distribution)?
4. **Aggregation Complexity:** Is this cluster composed of many complex subclusters?

Decision Logic:
- NOT neuronal shape → DISCARD
- Neuronal + high variability/drift/ISI violations → SPLIT (identify tight subgroups)
- Neuronal + low variability + clean ISI → KEEP

You are judging Cluster {cluster_id} for spike sorting curation.

Cluster Summary:
- Spike count: {n_spikes}
- Composed of {n_overclusters} overclusters (hierarchical subcomponents)

Output JSON schema:
{{
  "action": "KEEP" | "DISCARD" | "SPLIT",
  "rationale": "Brief explanation (2-3 sentences)",
}}
"""


def build_legacy_phase2_prompt(
    *,
    small_cluster_id: int,
    n_small: int,
    small_isi_rate: float,
    large_cluster_id: int,
    n_large: int,
    large_isi_rate: float,
    correlation: float,
    merged_isi_rate: float,
) -> str:
    """Reconstruct the merge prompt retained in the legacy artifacts."""
    return f"""
## Decision Logic:

**STEP 1: Check small cluster quality**
- If small cluster (n < 1000) has excessive variability (too broad/noisy) → DISCARD
- If small cluster shape is not neuronal → DISCARD

**STEP 2: Check similarity to large cluster (only if small cluster is valid)**
- If waveforms looks similar AND merged ISI acceptable (< 0.006) → MERGE
- If waveforms are dissimilar → Choose "NOT_MERGE"

**STEP 3: Final decision**
- If you choose "NOT_MERGE" for ALL large clusters → small cluster will be DISCARDED
- So "NOT_MERGE" means: "These are different units, try other large clusters"
- Only use actual "DISCARD" if small cluster itself is invalid (bad shape/too noisy)

Neuronal Shape Criteria:

{LEGACY_NEURONAL_CRITERIA}


You are deciding whether to MERGE small cluster {small_cluster_id} into large cluster {large_cluster_id}.

Small Cluster {small_cluster_id} (post-split result):
- Spike count: {n_small}
- ISI violation rate: {small_isi_rate:.2%}

Large Cluster {large_cluster_id} (independent valid unit):
- Spike count: {n_large} (large, standalone cluster)
- ISI violation rate: {large_isi_rate:.2%}

Merge Prediction:
- Waveform correlation: {correlation:.3f}
- Merged ISI violation rate: {merged_isi_rate:.2%}
- Total spikes after merge: {n_small + n_large}

Output JSON schema:
{{
  "action": "MERGE" | "NOT_MERGE" | "DISCARD",
  "rationale": "Brief explanation (2-3 sentences)"
}}
"""


def create_legacy_waveform_overlay_image(
    waveforms: np.ndarray,
    cluster_id: int,
    sampling_rate: float = 30000.0,
    max_waveforms: int = LEGACY_MAX_WAVEFORMS,
) -> str:
    """Reproduce the recoverable legacy waveform-overlay implementation.

    The old run used NumPy's process-global RNG and did not save its state.
    Thus the implementation is recoverable, but the original random subset is
    not reproducible unless the retained historical PNG is replayed directly.
    """
    n_spikes, n_samples = waveforms.shape
    time_ms = np.arange(n_samples) / sampling_rate * 1000
    if n_spikes > max_waveforms:
        indices = np.random.choice(n_spikes, max_waveforms, replace=False)
        plot_waveforms = waveforms[indices]
    else:
        plot_waveforms = waveforms

    fig, ax = plt.subplots(figsize=(8, 5))
    for waveform in plot_waveforms:
        ax.plot(time_ms, waveform, "steelblue", alpha=0.3, linewidth=0.8)
    ax.axhline(0, color="black", linestyle="--", linewidth=0.5)
    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Amplitude (μV)")
    ax.set_title(f"Cluster {cluster_id} Waveform Overlay (n={n_spikes})")
    ax.grid(True, alpha=0.3)

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=100, bbox_inches="tight")
    buffer.seek(0)
    encoded = base64.b64encode(buffer.read()).decode("utf-8")
    buffer.close()
    plt.close(fig)
    return encoded


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


@dataclass
class DecisionRecorder:
    output_dir: Path
    max_attempts: Optional[int]
    attempts: int = 0

    @property
    def path(self) -> Path:
        return self.output_dir / "vlm_decisions.jsonl"

    def begin_attempt(self) -> int:
        if self.max_attempts is not None and self.attempts >= self.max_attempts:
            raise RuntimeError(
                f"VLM decision-attempt budget exhausted ({self.max_attempts}); stopping rollout"
            )
        self.attempts += 1
        return self.attempts

    def append(self, record: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(json_safe(record), ensure_ascii=False) + "\n")


def _parse_decision(raw: str, allowed: set[str]) -> tuple[str, str]:
    decision = json.loads(agent_runner._sanitize_json_response(raw))
    action = str(decision.get("action", "")).strip().upper()
    if action not in allowed:
        raise ValueError(f"invalid action {action!r}; allowed={sorted(allowed)}")
    rationale = str(decision.get("rationale", decision.get("reason", "")))
    return action, rationale


def _call_and_parse(
    *,
    recorder: DecisionRecorder,
    stage: str,
    entity: dict[str, Any],
    prompt: str,
    images: list[str],
    allowed: set[str],
    provider: str,
    model: str,
    use_mock: bool,
    temperature: float,
    reasoning_effort: Optional[str],
    image_paths: Sequence[str],
) -> dict[str, Any]:
    last_raw = ""
    for parse_attempt in range(1, 4):
        attempt_index = recorder.begin_attempt()
        last_raw = agent_runner.call_vlm_api(
            prompt=prompt,
            images=images,
            model=model,
            provider=provider,
            use_mock=use_mock,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            response_schema=None,
        )
        call_meta = agent_api.get_last_call_meta()
        try:
            action, rationale = _parse_decision(last_raw, allowed)
            result = {
                "attempt_index": attempt_index,
                "parse_attempt": parse_attempt,
                "stage": stage,
                **entity,
                "action": action,
                "rationale": rationale,
                "raw_response": last_raw,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "image_paths": list(image_paths),
                "call_meta": call_meta,
                "decision_status": "ok",
            }
            recorder.append(result)
            return {
                "action": action,
                "rationale": rationale,
                "raw_response": last_raw,
                "prompt_text": prompt,
                "image_paths": list(image_paths),
                "decision_status": "ok",
            }
        except Exception as exc:
            recorder.append(
                {
                    "attempt_index": attempt_index,
                    "parse_attempt": parse_attempt,
                    "stage": stage,
                    **entity,
                    "raw_response": last_raw,
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "image_paths": list(image_paths),
                    "call_meta": call_meta,
                    "decision_status": "parse_error",
                    "error": str(exc),
                }
            )
            if parse_attempt == 3:
                return {
                    "action": "ABSTAIN",
                    "rationale": f"JSON parse error after {parse_attempt} attempts",
                    "raw_response": last_raw,
                    "prompt_text": prompt,
                    "image_paths": list(image_paths),
                    "decision_status": "parse_error",
                }
    raise AssertionError("unreachable")


def legacy_phase1_decision(
    *,
    recorder: DecisionRecorder,
    cluster_id: int,
    waveforms: np.ndarray,
    spike_times: np.ndarray,
    overcluster_composition: list[int],
    hierarchy_tree: np.ndarray,
    sampling_rate: float = 30000.0,
    provider: str = "gpt4o",
    model: str = "gpt-5.1",
    use_mock: bool = False,
    temperature: float = 0.0,
    reasoning_effort: Optional[str] = None,
    output_dir: Optional[Path] = None,
    retrieved_examples: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    if retrieved_examples:
        raise ValueError("legacy reproduction does not permit RAG/few-shot injection")
    waveform = create_legacy_waveform_overlay_image(
        waveforms,
        cluster_id,
        sampling_rate,
        max_waveforms=LEGACY_MAX_WAVEFORMS,
    )
    isi = agent_runner.create_isi_histogram_image(spike_times, cluster_id)
    tree = agent_runner.create_aggregation_tree_image(
        hierarchy_tree, overcluster_composition, cluster_id
    )
    prompt = build_legacy_phase1_prompt(
        cluster_id=cluster_id,
        n_spikes=len(spike_times),
        n_overclusters=len(overcluster_composition),
    )
    saved = agent_runner._save_vlm_inputs(
        output_dir=output_dir,
        prefix=f"phase1_cluster_{cluster_id}",
        images=[waveform, isi, tree],
        prompt=prompt,
        image_names=["waveform", "isi", "tree"],
        extra_meta={"protocol": PROTOCOL_VERSION},
    )
    return _call_and_parse(
        recorder=recorder,
        stage="phase1",
        entity={"cluster_id": cluster_id},
        prompt=prompt,
        images=[waveform, isi, tree],
        allowed={"KEEP", "DISCARD", "SPLIT"},
        provider=provider,
        model=model,
        use_mock=use_mock,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        image_paths=[] if saved is None else saved.get("image_files", []),
    )


def legacy_phase2_decision(
    *,
    recorder: DecisionRecorder,
    small_cluster_id: int,
    small_waveforms: np.ndarray,
    small_spike_times: np.ndarray,
    large_cluster_id: int,
    large_waveforms: np.ndarray,
    large_spike_times: np.ndarray,
    sampling_rate: float = 30000.0,
    provider: str = "gpt4o",
    model: str = "gpt-5.1",
    use_mock: bool = False,
    temperature: float = 0.0,
    reasoning_effort: Optional[str] = None,
    output_dir: Optional[Path] = None,
    retrieved_examples: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    if retrieved_examples:
        raise ValueError("legacy reproduction does not permit RAG/few-shot injection")
    small_image = create_legacy_waveform_overlay_image(
        small_waveforms,
        small_cluster_id,
        sampling_rate,
        max_waveforms=LEGACY_MAX_WAVEFORMS,
    )
    large_image = create_legacy_waveform_overlay_image(
        large_waveforms,
        large_cluster_id,
        sampling_rate,
        max_waveforms=LEGACY_MAX_WAVEFORMS,
    )
    merged_times = np.sort(np.concatenate([small_spike_times, large_spike_times]))
    merged_isi_image = agent_runner.create_isi_histogram_image(
        merged_times, f"{small_cluster_id}+{large_cluster_id}"
    )
    correlation = agent_runner.compute_waveform_correlation(small_waveforms, large_waveforms)
    merged_isi_rate = agent_runner.compute_merged_isi_violation_rate(
        small_spike_times, large_spike_times
    )
    small_isi_rate = agent_runner.compute_merged_isi_violation_rate(
        small_spike_times, small_spike_times[:1]
    )
    large_isi_rate = agent_runner.compute_merged_isi_violation_rate(
        large_spike_times, large_spike_times[:1]
    )
    prompt = build_legacy_phase2_prompt(
        small_cluster_id=small_cluster_id,
        n_small=len(small_spike_times),
        small_isi_rate=small_isi_rate,
        large_cluster_id=large_cluster_id,
        n_large=len(large_spike_times),
        large_isi_rate=large_isi_rate,
        correlation=correlation,
        merged_isi_rate=merged_isi_rate,
    )
    images = [small_image, large_image, merged_isi_image]
    saved = agent_runner._save_vlm_inputs(
        output_dir=output_dir,
        prefix=f"phase2_merge_{small_cluster_id}_into_{large_cluster_id}",
        images=images,
        prompt=prompt,
        image_names=["small_waveform", "large_waveform", "merged_isi"],
        extra_meta={"protocol": PROTOCOL_VERSION},
    )
    return _call_and_parse(
        recorder=recorder,
        stage="phase2",
        entity={
            "small_cluster_id": small_cluster_id,
            "large_cluster_id": large_cluster_id,
        },
        prompt=prompt,
        images=images,
        allowed={"MERGE", "NOT_MERGE", "DISCARD"},
        provider=provider,
        model=model,
        use_mock=use_mock,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        image_paths=[] if saved is None else saved.get("image_files", []),
    )


class CheckpointingPipeline(pure_module.PureVLMCurationPipeline):
    """Persist action/state snapshots without changing the source data."""

    def _save_checkpoint(self) -> None:
        if self.output_dir is None:
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.save_action_log(self.output_dir / "action_log.partial.csv")
        np.save(self.output_dir / "assigns.partial.npy", self.manager.assigns)
        np.save(self.output_dir / "hierarchy_tree.partial.npy", self.manager.hierarchy_tree)

    def log_action(
        self, action: str, cluster_ids: list[int], reason: str, phase: str
    ) -> None:
        super().log_action(action, cluster_ids, reason, phase)
        self._save_checkpoint()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(json_safe(payload), indent=2) + "\n", encoding="utf-8")


def _call_summary() -> dict[str, Any]:
    calls = agent_api.get_call_history()
    usage_keys = (
        "input_tokens",
        "cached_input_tokens",
        "uncached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    return {
        "n_successful_provider_calls": len(calls),
        "actual_models": sorted(
            {str(call.get("actual_model")) for call in calls if call.get("actual_model")}
        ),
        "usage": {
            key: sum(int(call.get("usage", {}).get(key, 0) or 0) for call in calls)
            for key in usage_keys
        },
    }


def _optional_positive_int(value: str) -> Optional[int]:
    normalized = value.strip().lower()
    if normalized in {"none", "unlimited"}:
        return None
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer, 'none', or 'unlimited'")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default="CH30")
    parser.add_argument("--provider", default="gpt4o")
    parser.add_argument("--model", default="gpt-5.1")
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--max-vlm-attempts",
        type=_optional_positive_int,
        default=50,
        help="Cost guard for provider attempts; use 'unlimited' to disable it.",
    )
    parser.add_argument(
        "--numpy-seed",
        type=int,
        default=None,
        help=(
            "Optional seed for a controlled rerun. Omit it to match the "
            "historical unseeded NumPy sampling protocol."
        ),
    )
    parser.add_argument("--use-mock", action="store_true")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    if args.numpy_seed is not None:
        np.random.seed(args.numpy_seed)

    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv(repo_root / ".env", override=False)
    if not args.use_mock and not os.getenv("OPENAI_API_KEY") and args.provider == "gpt4o":
        raise RuntimeError("OPENAI_API_KEY is not available")

    output_dir = (repo_root / args.output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    mat_path = (repo_root / "data" / f"{args.channel}_spikes.mat").resolve()
    if not mat_path.exists():
        raise FileNotFoundError(mat_path)
    meta = load_matlab_spikes(str(mat_path))
    gt_assigns = meta.get("curation_assigns")
    if gt_assigns is None:
        raise ValueError(f"{mat_path} has no curation.assigns")

    manager = ClusterManager(
        initial_assigns=meta["hierarchy_assigns"].copy(),
        overcluster_assigns=meta["overcluster_assigns"].copy(),
        hierarchy_tree=meta["hierarchy_tree"].copy(),
        spike_times=meta["spiketimes"],
        waveforms=meta["waveforms"],
    )
    features = ClusterFeatures(meta=meta, assigns=manager.assigns)
    recorder = DecisionRecorder(output_dir=output_dir, max_attempts=args.max_vlm_attempts)
    recorder.path.write_text("", encoding="utf-8")

    original_phase1 = pure_module.vlm_phase1_cluster_decision
    original_phase2 = pure_module.vlm_phase2_merge_decision
    pure_module.vlm_phase1_cluster_decision = partial(legacy_phase1_decision, recorder=recorder)
    pure_module.vlm_phase2_merge_decision = partial(legacy_phase2_decision, recorder=recorder)
    agent_api.reset_call_tracking()

    manifest = {
        "schema_version": "legacy-real-rollout-reproduction-v1",
        "status": "running",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL_VERSION,
        "scope": "full autonomous rollout from hierarchy.assigns",
        "channel": args.channel,
        "provider": args.provider,
        "requested_model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "temperature": args.temperature,
        "use_mock": bool(args.use_mock),
        "max_vlm_attempts": args.max_vlm_attempts,
        "prompt_contract": {
            "phase1_images": ["waveform", "isi", "tree"],
            "phase2_images": ["small_waveform", "large_waveform", "merged_isi"],
            "waveform_overlay_cap": LEGACY_MAX_WAVEFORMS,
            "waveform_sampling": "numpy.random.choice without replacement",
            "numpy_seed": args.numpy_seed,
            "response_schema_enforced": False,
            "rationale_requested": True,
        },
        "provenance": {
            "prompt_source": "retained output/main_gpt-5.1 prompt artifacts",
            "visualization_source_commit": "03f68e5413648b41491656ae1ba772c9077bf7a5",
            "source_artifact_mismatch": (
                "The initial commit contains result artifacts generated before the "
                "prompt/logging code stored in that same commit; exact historical "
                "source and NumPy RNG state are unavailable."
            ),
        },
        "thresholds": {
            "auto_discard": 500,
            "small_cluster": 4000,
            "final_minimum": 5000,
        },
        "current_safety_differences_from_historical_controller": [
            "provider failure stops instead of falling back to mock",
            "parse failure returns ABSTAIN instead of DISCARD",
            "no merge target or all NOT_MERGE preserves the cluster before Phase 3",
        ],
        "mat_path": str(mat_path),
        "mat_sha256": sha256_file(mat_path),
    }
    _write_json(output_dir / "run_manifest.json", manifest)

    pipeline = CheckpointingPipeline(
        manager=manager,
        features=features,
        sampling_rate=float(meta["Fs"]),
        auto_discard_threshold=500,
        small_cluster_threshold=4000,
        final_minimum_threshold=5000,
        provider=args.provider,
        model=args.model,
        use_mock=args.use_mock,
        temperature=args.temperature,
        reasoning_effort=args.reasoning_effort,
        output_dir=output_dir,
    )

    try:
        final_clusters = pipeline.run_full_pipeline()
        pipeline.save_action_log(output_dir / "action_log.csv")
        np.save(output_dir / "final_assigns.npy", manager.assigns)
        np.save(output_dir / "final_hierarchy_tree.npy", manager.hierarchy_tree)
        np.save(output_dir / "overcluster_assigns.npy", manager.overcluster_assigns)

        fs = float(meta["Fs"])
        spike_frames = (meta["spiketimes"] * fs).astype(np.int64)
        curated = si.NumpySorting.from_unit_dict(
            {
                int(cid): spike_frames[manager.assigns == cid]
                for cid in final_clusters
            },
            sampling_frequency=fs,
        )
        gt_ids = np.unique(gt_assigns[gt_assigns > 0])
        ground_truth = si.NumpySorting.from_unit_dict(
            {int(cid): spike_frames[gt_assigns == cid] for cid in gt_ids},
            sampling_frequency=fs,
        )
        report = generate_full_evaluation_report(
            curated_sorting=curated,
            waveforms=meta["waveforms"],
            spike_times=meta["spiketimes"],
            assigns=manager.assigns,
            ground_truth_sorting=ground_truth,
            gt_assigns=gt_assigns,
            sampling_frequency=fs,
            output_dir=output_dir,
        )

        manifest.update(
            {
                "status": "complete",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "n_vlm_attempts": recorder.attempts,
                **_call_summary(),
                "n_final_clusters": len(final_clusters),
                "n_final_assigned_spikes": int(np.sum(manager.assigns > 0)),
                "overall_performance": report.get("overall_performance"),
            }
        )
        _write_json(output_dir / "run_manifest.json", manifest)
        print(json.dumps(json_safe(manifest["overall_performance"]), indent=2))
    except BaseException as exc:
        pipeline._save_checkpoint()
        budget_stop = isinstance(exc, RuntimeError) and "budget exhausted" in str(exc)
        manifest.update(
            {
                "status": "stopped_budget" if budget_stop else "failed",
                "stopped_at": datetime.now(timezone.utc).isoformat(),
                "n_vlm_attempts": recorder.attempts,
                **_call_summary(),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        _write_json(output_dir / "run_manifest.json", manifest)
        raise
    finally:
        pure_module.vlm_phase1_cluster_decision = original_phase1
        pure_module.vlm_phase2_merge_decision = original_phase2


if __name__ == "__main__":
    main()
