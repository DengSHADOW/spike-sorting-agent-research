"""Replay one saved prompt/image bundle against a VLM with full provenance."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.agent.api import call_vlm, get_last_call_meta, reset_call_tracking
from src.agent.runner import _sanitize_json_response


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--image", action="append", required=True)
    parser.add_argument("--provider", default="gpt4o")
    parser.add_argument("--model", default="gpt-5.1")
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--max-tokens", type=int, default=1000)
    parser.add_argument("--expected-action", default="")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv(repo_root / ".env", override=False)
    if args.provider == "gpt4o" and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not available")

    prompt_path = (repo_root / args.prompt_file).resolve()
    image_paths = [(repo_root / item).resolve() for item in args.image]
    output_dir = (repo_root / args.output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    prompt = prompt_path.read_text(encoding="utf-8")
    images = [base64.b64encode(path.read_bytes()).decode("ascii") for path in image_paths]
    reset_call_tracking()
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
        action = str(parsed.get("action", "")).upper().strip()
        parse_status = "ok"
        parse_error = ""
    except Exception as exc:
        action = "ABSTAIN"
        parse_status = "parse_error"
        parse_error = str(exc)

    result = {
        "schema_version": "saved-vlm-input-replay-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": args.provider,
        "requested_model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "max_tokens": args.max_tokens,
        "prompt_file": str(prompt_path),
        "prompt_sha256": sha256_file(prompt_path),
        "images": [
            {"path": str(path), "sha256": sha256_file(path)} for path in image_paths
        ],
        "expected_action": args.expected_action.upper(),
        "predicted_action": action,
        "match": bool(args.expected_action and action == args.expected_action.upper()),
        "parse_status": parse_status,
        "parse_error": parse_error,
        "raw_response": raw,
        "call_meta": get_last_call_meta(),
    }
    (output_dir / "result.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
