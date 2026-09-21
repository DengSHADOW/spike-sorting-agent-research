"""Collect a small, reproducible result bundle from a remote experiment.

The collector intentionally uses an allowlist. It packages predictions, metrics,
manifests, and text logs, but does not recursively copy the run directory. Raw
MAT files, generated images, model weights, caches, and environment files are
therefore excluded unless this policy is changed in code.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


STANDARD_ARTIFACTS: tuple[str, ...] = (
    "run_manifest.json",
    "detail_predictions.csv",
    "summary_accuracy.csv",
    "summary_accuracy.json",
    "preflight.json",
    "action_log.csv",
    "evaluation_report.json",
    "overall_performance.txt",
    "quality_metrics.csv",
    "pipeline_metadata.json",
    "call_history.json",
    "raw.log",
    "stdout.log",
    "stderr.log",
    "vllm.log",
    "server.log",
)

FORBIDDEN_NAMES = {".env", "environment.env", "credentials.json", "secrets.json"}
FORBIDDEN_SUFFIXES = {
    ".mat",
    ".npy",
    ".npz",
    ".h5",
    ".hdf5",
    ".safetensors",
    ".pt",
    ".pth",
    ".ckpt",
    ".bin",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".webp",
}

PACKAGE_NAMES: tuple[str, ...] = (
    "numpy",
    "openai",
    "pandas",
    "spikeinterface",
    "torch",
    "transformers",
    "vllm",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_run_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip()).strip("._")
    if not safe:
        raise ValueError("Run name must contain at least one letter or number")
    return safe


def _validate_collectable_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Extra file does not exist: {path}")
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Only regular files can be collected: {path}")
    lower_name = path.name.lower()
    if lower_name in FORBIDDEN_NAMES or lower_name.startswith(".env"):
        raise ValueError(f"Sensitive environment file is not allowed: {path}")
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        raise ValueError(f"Data/image/model artifact is not allowed: {path}")


def _run_command(command: Sequence[str], cwd: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": type(exc).__name__}
    return {
        "available": True,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _git_metadata(repo_root: Path) -> dict[str, Any]:
    commit = _run_command(("git", "rev-parse", "HEAD"), repo_root)
    branch = _run_command(("git", "branch", "--show-current"), repo_root)
    status = _run_command(("git", "status", "--porcelain"), repo_root)
    status_lines = status.get("stdout", "").splitlines() if status.get("returncode") == 0 else []
    return {
        "commit": commit.get("stdout") if commit.get("returncode") == 0 else None,
        "branch": branch.get("stdout") if branch.get("returncode") == 0 else None,
        "dirty": bool(status_lines),
        "changed_path_count": len(status_lines),
    }


def _package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in PACKAGE_NAMES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def _environment_metadata(repo_root: Path) -> dict[str, Any]:
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "packages": _package_versions(),
        "gpu": _run_command(
            (
                "nvidia-smi",
                "--query-gpu=name,uuid,memory.total,driver_version",
                "--format=csv,noheader",
            ),
            repo_root,
        ),
    }


def _unique_destination(directory: Path, source: Path) -> Path:
    candidate = directory / source.name
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        candidate = directory / f"{source.stem}_{index}{source.suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def collect_experiment(
    *,
    run_dir: Path,
    output_root: Path,
    run_name: str | None = None,
    extra_files: Sequence[Path] = (),
    create_archive: bool = True,
    repo_root: Path | None = None,
) -> tuple[Path, Path | None]:
    """Collect experiment artifacts and return (directory, optional archive)."""

    run_dir = run_dir.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    if not run_dir.is_dir():
        raise NotADirectoryError(f"Run directory does not exist: {run_dir}")

    timestamp = datetime.now(timezone.utc)
    chosen_name = run_name or f"{run_dir.name}_{timestamp.strftime('%Y%m%dT%H%M%SZ')}"
    chosen_name = _safe_run_name(chosen_name)
    collection_dir = output_root / chosen_name
    try:
        collection_dir.relative_to(run_dir)
    except ValueError:
        pass
    else:
        raise ValueError("Output collection directory must not be inside the source run directory")
    if collection_dir.exists():
        raise FileExistsError(f"Collection already exists; choose another run name: {collection_dir}")

    artifacts_dir = collection_dir / "artifacts"
    logs_dir = collection_dir / "logs"
    artifacts_dir.mkdir(parents=True)
    logs_dir.mkdir()

    copied: list[tuple[Path, Path]] = []
    missing_standard: list[str] = []
    log_names = {"raw.log", "stdout.log", "stderr.log", "vllm.log", "server.log"}
    for name in STANDARD_ARTIFACTS:
        source = run_dir / name
        if not source.is_file() or source.is_symlink():
            missing_standard.append(name)
            continue
        destination_dir = logs_dir if name in log_names else artifacts_dir
        destination = destination_dir / name
        shutil.copy2(source, destination)
        copied.append((source, destination))

    for raw_path in extra_files:
        source = raw_path.expanduser().resolve()
        _validate_collectable_file(source)
        destination = _unique_destination(logs_dir, source)
        shutil.copy2(source, destination)
        copied.append((source, destination))

    if not copied:
        shutil.rmtree(collection_dir)
        raise ValueError(
            "No collectable artifacts found. Put standard result files in --run-dir "
            "or provide text/CSV/JSON logs with --extra-file."
        )

    actual_repo_root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    artifact_records = []
    for source, destination in sorted(copied, key=lambda pair: str(pair[1])):
        artifact_records.append(
            {
                "path": destination.relative_to(collection_dir).as_posix(),
                "source": str(source),
                "bytes": destination.stat().st_size,
                "sha256": _sha256(destination),
            }
        )

    manifest = {
        "schema_version": "runpod-experiment-collection-v1",
        "collected_at_utc": timestamp.isoformat(),
        "source_run_dir": str(run_dir),
        "collection_name": chosen_name,
        "policy": {
            "allowlisted_results_and_logs_only": True,
            "raw_data_images_model_weights_and_env_excluded": True,
        },
        "artifacts": artifact_records,
        "missing_optional_standard_artifacts": missing_standard,
        "git": _git_metadata(actual_repo_root),
        "environment": _environment_metadata(actual_repo_root),
    }
    manifest_path = collection_dir / "collection_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

    checksum_paths = [destination for _, destination in copied] + [manifest_path]
    checksum_lines = [
        f"{_sha256(path)}  {path.relative_to(collection_dir).as_posix()}"
        for path in sorted(checksum_paths)
    ]
    (collection_dir / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n")

    archive_path: Path | None = None
    if create_archive:
        output_root.mkdir(parents=True, exist_ok=True)
        archive_path = output_root / f"{chosen_name}.tar.gz"
        if archive_path.exists():
            raise FileExistsError(f"Archive already exists: {archive_path}")
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(collection_dir, arcname=collection_dir.name)

    return collection_dir, archive_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect compact Runpod experiment outputs with provenance and checksums."
    )
    parser.add_argument("--run-dir", type=Path, required=True, help="Evaluation output directory")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("output/runpod_collected"),
        help="Where to create the collection directory and archive",
    )
    parser.add_argument("--run-name", default=None, help="Stable experiment name; UTC suffix is default")
    parser.add_argument(
        "--extra-file",
        type=Path,
        action="append",
        default=[],
        help="Additional text/CSV/JSON log file; repeat as needed",
    )
    parser.add_argument("--no-archive", action="store_true", help="Keep only the collection directory")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    collection_dir, archive_path = collect_experiment(
        run_dir=args.run_dir,
        output_root=args.output_root,
        run_name=args.run_name,
        extra_files=args.extra_file,
        create_archive=not args.no_archive,
    )
    print(f"Collected directory: {collection_dir}")
    if archive_path is not None:
        print(f"Transfer archive: {archive_path}")
    print(f"Verify after transfer: sha256sum -c {collection_dir / 'checksums.sha256'}")


if __name__ == "__main__":
    main()
