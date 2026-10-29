"""Download, verify and explicitly serve the pinned local VLMs.

Download/verification never import torch or run inference. Serving is a separate
command and uses only verified files already present on the persistent volume.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/local_models.json"
STATE_DIR = ROOT / "output/local_model_setup_20261026"


def read_config():
    return json.loads(CONFIG.read_text())


def snapshot_path(config, alias):
    model = config["models"][alias]
    return (
        Path(config["cache_dir"])
        / ("models--" + model["repo_id"].replace("/", "--"))
        / "snapshots"
        / model["revision"]
    )


def save_state(alias, value):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    value["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    path = STATE_DIR / f"{alias}_status.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def verify_file(path, expected):
    if path.stat().st_size != expected["size"]:
        raise ValueError(f"File size mismatch: {path.name}")
    digest = hashlib.sha256() if "sha256" in expected else hashlib.sha1()
    if "git_blob_sha1" in expected:
        digest.update(f"blob {expected['size']}\0".encode())
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    wanted = expected.get("sha256", expected.get("git_blob_sha1"))
    if digest.hexdigest() != wanted:
        raise ValueError(f"Checksum mismatch: {path.name}")
    return digest.hexdigest()


def verify_model(config, alias):
    model = config["models"][alias]
    snapshot = snapshot_path(config, alias)
    state = {
        "status": "verifying", "repo_id": model["repo_id"],
        "revision": model["revision"], "snapshot": str(snapshot),
        "dtype_requested": model["dtype"], "files": [],
        "gpu_load_validated": False, "inference_started": False,
    }
    save_state(alias, state)
    try:
        for expected in model["files"]:
            path = snapshot / expected["name"]
            print(f"VERIFY {alias} {expected['name']}", flush=True)
            checksum = verify_file(path, expected)
            state["files"].append({
                "name": expected["name"], "size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns, "checksum": checksum,
            })
            save_state(alias, state)
        index = snapshot / "model.safetensors.index.json"
        if index.exists():
            shards = set(json.loads(index.read_text())["weight_map"].values())
            downloaded = {f["name"] for f in model["files"]}
            if not shards.issubset(downloaded):
                raise ValueError("Model index references missing weight shards")
        state.update(status="verified", bytes=sum(f["size"] for f in model["files"]))
        save_state(alias, state)
        print(f"VERIFIED {alias} {state['bytes']} bytes", flush=True)
    except Exception as error:
        state.update(status="failed_verification", error=str(error))
        save_state(alias, state)
        raise
    return state


def download_models(config, aliases):
    # Bound memory use on the CPU Pod. Public repositories need no credentials.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
    from huggingface_hub import snapshot_download

    remaining = sum(
        f["size"] for alias in aliases for f in config["models"][alias]["files"]
        if not (snapshot_path(config, alias) / f["name"]).exists()
    )
    free = shutil.disk_usage(Path(config["cache_dir"])).free
    if free < remaining + 5 * 1024**3:
        raise RuntimeError(f"Insufficient disk: {free} free, {remaining} to download")
    for alias in aliases:
        model = config["models"][alias]
        save_state(alias, {"status": "downloading", "repo_id": model["repo_id"],
                           "revision": model["revision"], "inference_started": False})
        print(f"DOWNLOAD {alias} {model['repo_id']}@{model['revision']}", flush=True)
        try:
            snapshot_download(
                repo_id=model["repo_id"], revision=model["revision"],
                cache_dir=config["cache_dir"],
                allow_patterns=[f["name"] for f in model["files"]],
                max_workers=2, token=False,
            )
            verify_model(config, alias)
        except Exception as error:
            save_state(alias, {"status": "download_or_verification_failed",
                               "error": str(error), "repo_id": model["repo_id"],
                               "revision": model["revision"], "inference_started": False})
            raise


def serve_command(config, alias, port):
    model = config["models"][alias]
    runtime = config["runtime"]
    binary = ROOT / runtime["venv"] / "bin/vllm"
    return [
        str(binary), "serve", str(snapshot_path(config, alias)),
        "--served-model-name", model["repo_id"],
        "--host", runtime["host"], "--port", str(port or runtime["port"]),
        "--dtype", model["dtype"],
        "--max-model-len", str(runtime["max_model_len"]),
        "--max-num-seqs", str(runtime["max_num_seqs"]),
        "--gpu-memory-utilization", str(runtime["gpu_memory_utilization"]),
        "--limit-mm-per-prompt", json.dumps({"image": runtime["max_images_per_prompt"], "video": 0, "audio": 0},
                                            separators=(",", ":")),
        "--reasoning-parser", model["reasoning_parser"],
        "--generation-config", "vllm",
    ]


def serve_model(config, alias, port, dry_run):
    command = serve_command(config, alias, port)
    print(shlex.join(command), flush=True)
    if dry_run:
        return
    state = json.loads((STATE_DIR / f"{alias}_status.json").read_text())
    if state.get("status") != "verified" or state.get("revision") != config["models"][alias]["revision"]:
        raise RuntimeError("Download and verify this revision before serving")
    if {f["name"] for f in state.get("files", [])} != {
        f["name"] for f in config["models"][alias]["files"]
    }:
        raise RuntimeError("Verification record is incomplete")
    for file in state["files"]:
        path = snapshot_path(config, alias) / file["name"]
        stat = path.stat()
        if stat.st_size != file["size"] or stat.st_mtime_ns != file["mtime_ns"]:
            raise RuntimeError(f"Model changed after verification: {file['name']}")
    if not shutil.which("nvidia-smi"):
        raise RuntimeError("No NVIDIA runtime visible; model server was not started")
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True,
    )
    if not result.stdout.strip():
        raise RuntimeError("No GPU visible; model server was not started")
    if not Path(command[0]).exists():
        raise RuntimeError("Install the isolated vLLM environment first")
    environment = os.environ.copy()
    environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                       HF_HUB_DISABLE_IMPLICIT_TOKEN="1", VLLM_NO_USAGE_STATS="1")
    # FlashInfer JIT-compiles its sampler at warm-up and failed on the H100 Pod (no ninja on PATH, system
    # nvcc 12.8 vs cu130); the PyTorch sampler takes the same sampling parameters (2026-10-28).
    environment.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")
    os.execve(command[0], command, environment)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["download", "verify", "status", "serve"])
    config = read_config()
    parser.add_argument("model", nargs="?", choices=["all", *config["models"]], default="all")
    parser.add_argument("--port", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    # A retired model's weights were deleted on purpose; it stays in the config only as a record.
    active = [a for a, m in config["models"].items() if not m.get("retired")]
    if args.model != "all" and args.model not in active:
        raise SystemExit(f"{args.model} is retired: {config['models'][args.model]['retired']}")
    aliases = active if args.model == "all" else [args.model]
    if args.command == "download":
        download_models(config, aliases)
    elif args.command == "verify":
        for alias in aliases:
            verify_model(config, alias)
    elif args.command == "status":
        for alias in aliases:
            path = STATE_DIR / f"{alias}_status.json"
            print(alias, path.read_text() if path.exists() else "not downloaded")
    else:
        if len(aliases) != 1:
            parser.error("Serve one model at a time: qwen27b or gemma4")
        serve_model(config, aliases[0], args.port, args.dry_run)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
