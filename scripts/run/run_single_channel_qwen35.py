"""Run the real-data curation agent on one arbitrary current-format MAT file.

The historical entry point accepted only CH3/20/30/31. This version keeps the
filename for compatibility but identifies data by full dataset id and supports
read-only preflight without starting a model call.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import spikeinterface as si

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*, override: bool = False) -> bool:
        """Keep local-vLLM/preflight usable when optional dotenv is absent."""
        return False

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.agent.api import get_call_history, reset_call_tracking
from src.cluster.features import ClusterFeatures
from src.cluster.manager import ClusterManager
from src.eval.metrics import generate_full_evaluation_report, print_evaluation_summary
from src.io.matlab_loader import convert_mat_to_sortings
from src.pipeline.pure import PureVLMCurationPipeline


DEFAULT_VLLM_MODEL = "Qwen/Qwen3.5-4B"
DEFAULT_OPENROUTER_MODEL = "qwen/qwen3.5-4b"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "dataset"


def _infer_recording_block(dataset_id: str) -> str:
    match = re.fullmatch(r"(.+)_CH\d+", dataset_id)
    return match.group(1) if match else "unknown"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _ensure_provider_env(provider: str) -> None:
    if provider == "gpt4o" and not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is required for provider=gpt4o")
    if provider == "openrouter" and not os.getenv("OPENROUTER_API_KEY"):
        raise ValueError("OPENROUTER_API_KEY is required for provider=openrouter")


def _ensure_no_thinking_extra_body(provider: str, disable_thinking: bool) -> None:
    if not disable_thinking or provider not in {"vllm", "openrouter"}:
        return
    if os.getenv("VLM_EXTRA_BODY_JSON", "").strip():
        return
    os.environ["VLM_EXTRA_BODY_JSON"] = json.dumps(
        {"chat_template_kwargs": {"enable_thinking": False}}
    )


def _default_model_for_provider(provider: str) -> str:
    if provider == "openrouter":
        return DEFAULT_OPENROUTER_MODEL
    if provider == "vllm":
        return DEFAULT_VLLM_MODEL
    return "gpt-4.1"


def _resolve_data(args: argparse.Namespace) -> tuple[Path, str, str]:
    if args.mat_path:
        mat_path = Path(args.mat_path)
    elif args.channel:
        channel = args.channel.upper()
        mat_path = Path("data") / f"{channel}_spikes.mat"
    else:
        raise ValueError("Provide --mat-path or --channel")

    dataset_id = args.dataset_id or mat_path.parent.name
    if dataset_id in {"data", ".", ""}:
        dataset_id = mat_path.stem.removesuffix("_spikes")
    recording_block = args.recording_block or _infer_recording_block(dataset_id)
    return mat_path, dataset_id, recording_block


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mat-path", default="", help="Path to a current-format MAT file")
    parser.add_argument("--channel", default="", help="Legacy shortcut for data/CH*_spikes.mat")
    parser.add_argument("--dataset-id", default="", help="Unique id, e.g. cM2-e004_004-006_CH3")
    parser.add_argument("--recording-block", default="", help="Leakage group for this dataset")
    parser.add_argument("--provider", default="vllm", choices=["gpt4o", "openrouter", "vllm", "claude"])
    parser.add_argument("--model", default="", help="Model id served by the selected provider")
    parser.add_argument("--use-mock", action="store_true", help="Explicit test-only mock; never enabled implicitly")
    parser.add_argument("--preflight-only", action="store_true", help="Load and validate MAT without model calls")
    parser.add_argument("--allow-unlabeled", action="store_true", help="Allow inference without curation.assigns")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--disable-thinking", action="store_true")
    parser.add_argument("--enable-thinking", action="store_true")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--output-tag", default="real_open_vlm")
    parser.add_argument(
        "--auto-discard-threshold",
        type=int,
        default=0,
        help="Phase 0 rule; 0 disables it (safe real-data default)",
    )
    parser.add_argument("--small-cluster-threshold", type=int, default=4000)
    parser.add_argument(
        "--final-minimum-threshold",
        type=int,
        default=0,
        help="Phase 3 rule; 0 disables it (safe real-data default)",
    )
    args = parser.parse_args()

    load_dotenv(override=True)
    mat_path, dataset_id, recording_block = _resolve_data(args)
    if not mat_path.exists():
        raise FileNotFoundError(f"Data file not found: {mat_path}")

    model = args.model or _default_model_for_provider(args.provider)
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path("output") / args.output_tag / _safe_name(dataset_id)
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    source_hash_before = _sha256(mat_path)
    started = time.time()

    _, _, meta = convert_mat_to_sortings(str(mat_path))
    gt_assigns = meta.get("curation_assigns")
    if gt_assigns is None and not args.allow_unlabeled:
        raise ValueError(
            f"{dataset_id} has no curation.assigns; use --allow-unlabeled only for inference"
        )

    preflight = {
        "schema_version": "real-vlm-preflight-v1",
        "dataset_id": dataset_id,
        "recording_block": recording_block,
        "mat_path": str(mat_path),
        "mat_sha256": source_hash_before,
        "n_spikes": int(len(meta["spiketimes"])),
        "waveform_shape": list(np.asarray(meta["waveforms"]).shape),
        "sampling_rate_hz": float(meta["Fs"]),
        "n_initial_clusters": int(np.count_nonzero(np.unique(meta["hierarchy_assigns"]))),
        "has_terminal_assigns": gt_assigns is not None,
        "n_terminal_clusters": (
            int(np.count_nonzero(np.unique(gt_assigns))) if gt_assigns is not None else None
        ),
        "provider": args.provider,
        "model": model,
        "preflight_only": bool(args.preflight_only),
        "mock": bool(args.use_mock),
        "auto_discard_threshold": args.auto_discard_threshold,
        "small_cluster_threshold": args.small_cluster_threshold,
        "final_minimum_threshold": args.final_minimum_threshold,
    }
    _write_json(output_dir / "preflight.json", preflight)
    if args.preflight_only:
        print(json.dumps(preflight, indent=2, ensure_ascii=False))
        return

    if not args.use_mock:
        _ensure_provider_env(args.provider)
    disable_thinking = args.disable_thinking or not args.enable_thinking
    _ensure_no_thinking_extra_body(args.provider, disable_thinking)
    reset_call_tracking()

    manager = ClusterManager(
        initial_assigns=meta["hierarchy_assigns"],
        overcluster_assigns=meta["overcluster_assigns"],
        hierarchy_tree=meta["hierarchy_tree"],
        spike_times=meta["spiketimes"],
        waveforms=meta["waveforms"],
    )
    features = ClusterFeatures(meta=meta, assigns=manager.assigns)
    pipeline = PureVLMCurationPipeline(
        manager=manager,
        features=features,
        sampling_rate=float(meta["Fs"]),
        auto_discard_threshold=args.auto_discard_threshold,
        small_cluster_threshold=args.small_cluster_threshold,
        final_minimum_threshold=args.final_minimum_threshold,
        provider=args.provider,
        model=model,
        use_mock=args.use_mock,
        temperature=args.temperature,
        reasoning_effort=args.reasoning_effort,
        output_dir=output_dir,
    )

    final_clusters = pipeline.run_full_pipeline()
    pipeline.save_action_log(output_dir / "action_log.csv")
    np.save(output_dir / "final_assigns.npy", manager.assigns)
    np.save(output_dir / "final_hierarchy_tree.npy", manager.hierarchy_tree)
    np.save(output_dir / "overcluster_assigns.npy", manager.overcluster_assigns)

    report = None
    if final_clusters and gt_assigns is not None:
        spike_frames = (np.asarray(meta["spiketimes"]) * float(meta["Fs"])).astype(np.int64)
        final_sorting = si.NumpySorting.from_unit_dict(
            {int(cid): spike_frames[manager.assigns == cid] for cid in final_clusters},
            sampling_frequency=float(meta["Fs"]),
        )
        gt_ids = np.unique(gt_assigns[gt_assigns > 0])
        gt_sorting = si.NumpySorting.from_unit_dict(
            {int(cid): spike_frames[gt_assigns == cid] for cid in gt_ids},
            sampling_frequency=float(meta["Fs"]),
        )
        report = generate_full_evaluation_report(
            curated_sorting=final_sorting,
            waveforms=meta["waveforms"],
            spike_times=meta["spiketimes"],
            assigns=manager.assigns,
            ground_truth_sorting=gt_sorting,
            gt_assigns=gt_assigns,
            sampling_frequency=float(meta["Fs"]),
            output_dir=output_dir,
        )
        print_evaluation_summary(report)

    source_hash_after = _sha256(mat_path)
    summary = {
        **preflight,
        "schema_version": "real-vlm-run-summary-v1",
        "status": "complete",
        "elapsed_seconds": time.time() - started,
        "n_final_clusters": len(final_clusters),
        "n_actions": len(pipeline.actions),
        "action_counts": {
            action: sum(row["action"] == action for row in pipeline.actions)
            for action in sorted({row["action"] for row in pipeline.actions})
        },
        "n_model_calls": len(get_call_history()),
        "model_calls": get_call_history(),
        "source_files_unchanged": source_hash_before == source_hash_after,
        "evaluation_available": report is not None,
    }
    _write_json(output_dir / "run_summary.json", summary)
    if not summary["source_files_unchanged"]:
        raise RuntimeError("Source MAT fingerprint changed during the run")
    print(f"Done: {dataset_id} -> {output_dir.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Run failed: {exc}", file=sys.stderr)
        raise
