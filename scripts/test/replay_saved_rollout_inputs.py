"""Replay retained legacy VLM inputs and compare current actions to the saved run.

This is a controlled model-stability audit, not a fresh closed-loop rollout:
the prompt and PNG bytes are exactly the retained historical inputs.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.agent.api import call_vlm, get_call_history, get_last_call_meta, reset_call_tracking
from src.agent.runner import _sanitize_json_response


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_expected_actions(action_log: Path) -> tuple[dict[str, str], list[str]]:
    """Resolve saved bundle prefixes to the last logged action for that state.

    The oldest artifact writer overwrote repeated prefixes. Selecting the last
    matching action is therefore the only mapping consistent with the retained
    file. NOT_MERGE comparisons were not logged and remain unresolved.
    """
    phase1: dict[int, str] = {}
    phase2: dict[int, str] = {}
    limitations: list[str] = []
    with action_log.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ids = [int(item) for item in row["Cluster IDs"].split(",") if item]
            if not ids:
                continue
            phase = row["Phase"]
            action = row["Action"].strip().upper()
            if phase.startswith("Phase1"):
                phase1[ids[0]] = action
            elif phase == "Phase2":
                phase2[ids[0]] = action

    expected: dict[str, str] = {}
    for cluster_id, action in phase1.items():
        expected[f"phase1_cluster_{cluster_id}"] = action
    for small_id, action in phase2.items():
        expected[f"phase2_small_{small_id}"] = action
    limitations.append(
        "Repeated cluster prefixes were overwritten in the historical artifacts; "
        "the retained PNG/prompt is mapped to the last logged action for that cluster."
    )
    limitations.append(
        "Historical NOT_MERGE comparisons were not written to action_log.csv, so an "
        "expected action may be unavailable for such bundles."
    )
    return expected, limitations


def expected_for_prefix(prefix: str, expected: dict[str, str]) -> str:
    phase1_match = re.fullmatch(r"phase1_cluster_(\d+)", prefix)
    if phase1_match:
        return expected.get(prefix, "")
    phase2_match = re.fullmatch(r"phase2_merge_(\d+)_into_(\d+)", prefix)
    if phase2_match:
        return expected.get(f"phase2_small_{int(phase2_match.group(1))}", "")
    return ""


def image_paths_for_prefix(input_dir: Path, prefix: str) -> list[Path]:
    if prefix.startswith("phase1_cluster_"):
        suffixes = ("waveform", "isi", "tree")
    elif prefix.startswith("phase2_merge_"):
        suffixes = ("small_waveform", "large_waveform", "merged_isi")
    else:
        raise ValueError(f"unrecognized legacy prefix: {prefix}")
    paths = [input_dir / f"{prefix}_{suffix}.png" for suffix in suffixes]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"missing images for {prefix}: {missing}")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel-dir", default="output/main_gpt-5.1/CH30")
    parser.add_argument("--provider", default="gpt4o")
    parser.add_argument("--model", default="gpt-5.1")
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--max-tokens", type=int, default=1000)
    parser.add_argument("--max-calls", type=int, default=0, help="0 means all bundles")
    parser.add_argument("--exclude-prefix", action="append", default=[])
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv(repo_root / ".env", override=False)
    if args.provider == "gpt4o" and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not available")

    channel_dir = (repo_root / args.channel_dir).resolve()
    input_dir = channel_dir / "vlm_inputs"
    action_log = channel_dir / "action_log.csv"
    output_dir = (repo_root / args.output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    expected_map, limitations = load_expected_actions(action_log)
    prefixes = sorted(
        path.name.removesuffix("_prompt.txt")
        for path in input_dir.glob("*_prompt.txt")
        if path.name.removesuffix("_prompt.txt") not in set(args.exclude_prefix)
    )
    if args.max_calls > 0:
        prefixes = prefixes[: args.max_calls]

    manifest: dict[str, Any] = {
        "schema_version": "saved-rollout-input-replay-v1",
        "status": "running",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "fixed historical states with byte-identical retained prompt/PNG inputs",
        "not_scope": "fresh closed-loop rollout",
        "channel_dir": str(channel_dir),
        "provider": args.provider,
        "requested_model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "max_tokens": args.max_tokens,
        "excluded_prefixes": list(args.exclude_prefix),
        "limitations": limitations,
        "n_scheduled": len(prefixes),
    }
    write_json(output_dir / "manifest.json", manifest)
    result_path = output_dir / "results.jsonl"
    result_path.write_text("", encoding="utf-8")
    reset_call_tracking()
    results: list[dict[str, Any]] = []

    try:
        for index, prefix in enumerate(prefixes, start=1):
            prompt_path = input_dir / f"{prefix}_prompt.txt"
            image_paths = image_paths_for_prefix(input_dir, prefix)
            prompt = prompt_path.read_text(encoding="utf-8")
            images = [base64.b64encode(path.read_bytes()).decode("ascii") for path in image_paths]
            raw = call_vlm(
                prompt=prompt,
                images=images,
                provider=args.provider,
                model=args.model,
                max_tokens=args.max_tokens,
                temperature=0.0,
                reasoning_effort=args.reasoning_effort,
                response_schema=None,
            )
            try:
                parsed = json.loads(_sanitize_json_response(raw))
                predicted = str(parsed.get("action", "")).strip().upper()
                parse_status = "ok"
                parse_error = ""
            except Exception as exc:
                predicted = "ABSTAIN"
                parse_status = "parse_error"
                parse_error = str(exc)
            expected = expected_for_prefix(prefix, expected_map)
            record = {
                "index": index,
                "prefix": prefix,
                "prompt": {"path": str(prompt_path), "sha256": sha256_file(prompt_path)},
                "images": [
                    {"path": str(path), "sha256": sha256_file(path)} for path in image_paths
                ],
                "expected_action": expected,
                "predicted_action": predicted,
                "match": bool(expected and predicted == expected),
                "parse_status": parse_status,
                "parse_error": parse_error,
                "raw_response": raw,
                "call_meta": get_last_call_meta(),
            }
            results.append(record)
            with result_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
            print(f"[{index}/{len(prefixes)}] {prefix}: {predicted} expected={expected or 'unknown'}")
    except BaseException as exc:
        manifest.update(
            {
                "status": "failed",
                "stopped_at": datetime.now(timezone.utc).isoformat(),
                "n_completed": len(results),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        write_json(output_dir / "manifest.json", manifest)
        raise

    calls = get_call_history()
    known = [record for record in results if record["expected_action"]]
    matched = sum(int(record["match"]) for record in known)
    usage_keys = (
        "input_tokens",
        "cached_input_tokens",
        "uncached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    manifest.update(
        {
            "status": "complete",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "n_completed": len(results),
            "n_expected_known": len(known),
            "n_matches": matched,
            "action_agreement": matched / len(known) if known else None,
            "actual_models": sorted(
                {str(call.get("actual_model")) for call in calls if call.get("actual_model")}
            ),
            "usage": {
                key: sum(int(call.get("usage", {}).get(key, 0) or 0) for call in calls)
                for key in usage_keys
            },
        }
    )
    write_json(output_dir / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
