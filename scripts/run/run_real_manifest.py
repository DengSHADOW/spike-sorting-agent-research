"""Preflight or run all labeled real MAT datasets from the audited manifest.

Datasets without ``curation.assigns`` (currently CH5) are excluded by default.
Each dataset receives a unique output directory based on its full dataset id;
this avoids collisions between equal channel numbers from different recordings.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _check_local_vllm(model: str) -> None:
    base_url = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base_url}/models", timeout=5) as response:
            payload = json.load(response)
    except Exception as exc:
        raise RuntimeError(
            f"Local vLLM server is not ready at {base_url}; start the model server before batch run"
        ) from exc
    served = {str(row.get("id")) for row in payload.get("data", [])}
    if model not in served:
        raise RuntimeError(f"Requested model {model!r} is not served by vLLM; available={sorted(served)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("output/real_split_manifest_20260903/manifest.json"),
    )
    parser.add_argument("--mode", choices=["preflight", "run"], default="preflight")
    parser.add_argument("--output-dir", type=Path, default=Path("output/real_open_vlm_20260914"))
    parser.add_argument("--provider", default="vllm", choices=["gpt4o", "openrouter", "vllm", "claude"])
    parser.add_argument("--model", default="Qwen/Qwen3.5-4B")
    parser.add_argument("--dataset-id", action="append", default=[], help="Optional exact dataset filter")
    parser.add_argument("--include-unlabeled", action="store_true")
    parser.add_argument("--use-mock", action="store_true", help="Explicit plumbing test only")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--auto-discard-threshold", type=int, default=0)
    parser.add_argument("--small-cluster-threshold", type=int, default=4000)
    parser.add_argument("--final-minimum-threshold", type=int, default=0)
    args = parser.parse_args()

    if args.mode == "run" and args.provider == "vllm" and not args.use_mock:
        _check_local_vllm(args.model)

    manifest = json.loads(args.manifest.read_text())
    selected = []
    requested = set(args.dataset_id)
    for row in manifest["datasets"]:
        if requested and row["dataset_id"] not in requested:
            continue
        if not args.include_unlabeled and not row["has_terminal_assigns"]:
            continue
        selected.append(row)

    if requested:
        found = {row["dataset_id"] for row in selected}
        missing = requested - found
        if missing:
            raise ValueError(f"Requested dataset ids missing or excluded: {sorted(missing)}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    started = time.time()
    child_script = Path("scripts/run/run_single_channel_qwen35.py")

    for index, row in enumerate(selected, start=1):
        dataset_id = str(row["dataset_id"])
        dataset_output = args.output_dir / dataset_id
        done_file = dataset_output / ("preflight.json" if args.mode == "preflight" else "run_summary.json")
        if args.resume and done_file.exists():
            results.append({"dataset_id": dataset_id, "status": "skipped_existing"})
            continue

        cmd = [
            sys.executable,
            str(child_script),
            "--mat-path",
            str(row["mat_path"]),
            "--dataset-id",
            dataset_id,
            "--recording-block",
            str(row["recording_block"]),
            "--provider",
            args.provider,
            "--model",
            args.model,
            "--output-dir",
            str(dataset_output),
            "--auto-discard-threshold",
            str(args.auto_discard_threshold),
            "--small-cluster-threshold",
            str(args.small_cluster_threshold),
            "--final-minimum-threshold",
            str(args.final_minimum_threshold),
        ]
        if args.mode == "preflight":
            cmd.append("--preflight-only")
        if args.use_mock:
            cmd.append("--use-mock")
        if args.include_unlabeled and not row["has_terminal_assigns"]:
            cmd.append("--allow-unlabeled")

        print(f"[{index}/{len(selected)}] {args.mode}: {dataset_id}", flush=True)
        run_started = time.time()
        dataset_output.mkdir(parents=True, exist_ok=True)
        log_path = dataset_output / f"{args.mode}.log"
        with log_path.open("w", encoding="utf-8") as log_handle:
            completed = subprocess.run(
                cmd,
                text=True,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
        log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        result = {
            "dataset_id": dataset_id,
            "recording_block": row["recording_block"],
            "mat_path": row["mat_path"],
            "status": "complete" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
            "elapsed_seconds": time.time() - run_started,
            "log_path": str(log_path),
            "log_tail": log_tail,
        }
        results.append(result)
        _write_json(args.output_dir / "batch_summary.partial.json", {"results": results})
        if completed.returncode != 0 and args.fail_fast:
            break

    complete = sum(row["status"] == "complete" for row in results)
    failed = sum(row["status"] == "failed" for row in results)
    skipped = sum(row["status"] == "skipped_existing" for row in results)
    summary = {
        "schema_version": "real-vlm-batch-summary-v1",
        "manifest": str(args.manifest),
        "mode": args.mode,
        "provider": args.provider,
        "model": args.model,
        "mock": args.use_mock,
        "unlabeled_included": args.include_unlabeled,
        "n_selected": len(selected),
        "n_complete": complete,
        "n_failed": failed,
        "n_skipped": skipped,
        "elapsed_seconds": time.time() - started,
        "results": results,
    }
    _write_json(args.output_dir / "batch_summary.json", summary)
    print(
        f"Finished: selected={len(selected)}, complete={complete}, failed={failed}, skipped={skipped}",
        flush=True,
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
