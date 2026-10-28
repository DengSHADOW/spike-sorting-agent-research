"""Prepare a private, scoped migration snapshot offline; never deploy or log in.

Only the four approved development inputs, declared review evidence and verified
saved checkpoints are included. No model execution, expert workbook extraction,
original-data edits or credentials are involved. Destination must be new.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3

from dataset_inputs import ALLOWED
from jacob_reasons import DEFAULT_SEED, load_seed

ROOT = Path(__file__).resolve().parents[3]
PROJECT = "spikeagent-review-20261023"
STORE = ROOT / "output/curation_review_local"
RUNS = Path("zero-shot baseline/runs/qwen9b_rollout_comparison_20261013_8000_thinking6000_v2/attempt_003/minimal")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def scoped_file(path):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve(strict=True)
    if (not resolved.is_relative_to(ROOT) or resolved != path.absolute()
            or not resolved.is_file()):
        raise ValueError("Migration source must be a regular file inside this repository")
    return resolved


def prepare(destination):
    destination = destination.absolute()
    parent = (ROOT / "output/firebase_migration").resolve()
    if not destination.resolve().is_relative_to(parent) or destination.resolve() == parent:
        raise ValueError("Use a new child directory under output/firebase_migration")
    if destination.exists():
        raise ValueError("Destination already exists; choose a new directory")
    files = {}

    def add(path, role, expected=None):
        path = scoped_file(path)
        checksum = digest(path)
        if expected is not None and checksum != expected:
            raise ValueError("Source integrity mismatch: " + str(path.relative_to(ROOT)))
        relative = path.relative_to(ROOT).as_posix()
        entry = dict(path=relative, bytes=path.stat().st_size, sha256=checksum, role=role)
        if relative in files and files[relative] != entry:
            raise ValueError("Conflicting source identity")
        files[relative] = entry

    manifest = read(ROOT / "output/real_split_manifest_20260903/manifest.json")
    rows = [row for row in manifest["datasets"] if row["dataset_id"] in ALLOWED]
    if len(rows) != len(ALLOWED) or {r["dataset_id"] for r in rows} != set(ALLOWED):
        raise ValueError("Approved MAT manifest is incomplete or duplicated")
    for row in rows:
        add(row["mat_path"], "approved-mat-input", row["mat_sha256"])
        if files[row["mat_path"]]["bytes"] != row["file_size_bytes"]:
            raise ValueError("MAT byte count mismatch")

    registry = read(STORE / "manager/registry.json")
    catalog, case_ids = [], set()
    for item in registry:
        directory = Path(item["path"])
        if not directory.is_absolute():
            directory = ROOT / directory
        if not directory.resolve().is_relative_to(STORE):
            raise ValueError("Bundle is outside the local review directory")
        path = scoped_file(directory / "dataset.json")
        bundle = read(path)
        dataset = bundle.get("dataset_id")
        if bundle.get("schema") != "spikeagent-jacob-bundle-v1" or dataset not in ALLOWED:
            raise ValueError("Unapproved bundle in registry")
        expected_mat = next(row["mat_sha256"] for row in rows if row["dataset_id"] == dataset)
        if bundle["provenance"].get("mat_sha256") != expected_mat:
            raise ValueError("Bundle input provenance mismatch")
        for case in bundle["cases"]:
            if case["dataset_id"] != dataset or case["id"] in case_ids:
                raise ValueError("Cross-dataset or duplicated case identity")
            case_ids.add(case["id"])
        add(path, "review-catalog")
        for name, checksum in bundle["assets"].items():
            if Path(name).name != name:
                raise ValueError("Invalid asset name")
            add(directory / "assets" / name, "saved-model-image", checksum)
        for row in bundle["evidence_files"].values():
            if Path(row["file"]).name != row["file"]:
                raise ValueError("Invalid evidence name")
            add(directory / "evidence" / row["file"], "reviewer-evidence", row["sha256"])
        if bundle.get("source_kind", "saved") == "saved":
            if dataset not in ALLOWED[:2]:
                raise ValueError("Unexpected saved-run dataset")
            run = RUNS / dataset
            if bundle["provenance"].get("run_path") != str(run):
                raise ValueError("Unexpected saved-run path")
            add(run / "manifest.json", "saved-run-manifest", bundle["provenance"]["run_manifest_sha256"])
            checkpoints = {}
            for checkpoint in sorted((ROOT / run).glob("*.npz")):
                meta_path = checkpoint.with_suffix(".json")
                meta = read(scoped_file(meta_path))
                if (meta.get("dataset_id") != dataset
                        or meta.get("input_sha256") != bundle["provenance"]["actor_input_sha256"]):
                    raise ValueError("Checkpoint input provenance mismatch")
                checkpoints[(meta["state_sha256"], meta["sha256"])]=checkpoint
            for case in bundle["cases"]:
                checkpoint = checkpoints.get((case["state_sha256"],case["checkpoint_sha256"]))
                if checkpoint is None:
                    raise ValueError("Missing checkpoint for review case")
                add(checkpoint,"saved-state",case["checkpoint_sha256"])
                add(checkpoint.with_suffix(".json"),"saved-state-metadata")
        catalog.append(dict(id=item["id"],path=directory.relative_to(ROOT).as_posix(),
                            dataset_id=dataset,cases=len(bundle["cases"]),source_kind=bundle.get("source_kind","saved")))
    if not catalog or not case_ids:
        raise ValueError("No review cases available")

    seed = load_seed(DEFAULT_SEED)
    add(DEFAULT_SEED,"jacob-reviewer-vocabulary")
    add(Path("configs/curation/protocol_v1.json"),"scope-protocol")
    feedback = dict(reviews=0, events=0, reasons=0)
    db_path = STORE / "reviews.sqlite3"
    if db_path.exists():
        with sqlite3.connect(db_path.as_uri()+"?mode=ro", uri=True) as db:
            feedback = {name:db.execute("SELECT count(*) FROM "+name).fetchone()[0]
                        for name in feedback}

    # Validate every source before writing anything. No database/auth files enter
    # this private data snapshot; reviewer ownership needs an explicit UID mapping.
    old_mask = os.umask(0o077)
    try:
        destination.mkdir(parents=True, exist_ok=False)
        data_root = destination / "data"
        for relative, entry in sorted(files.items()):
            target = data_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
            if digest(target) != entry["sha256"]:
                raise ValueError("Copied file integrity mismatch: "+relative)
        generated = {
            "output/real_split_manifest_20260903/manifest.json":dict(
                schema_version=manifest["schema_version"],scope="approved-review-migration-only",datasets=rows),
            "output/curation_review_local/manager/registry.portable.json":catalog,
        }
        for relative,payload in generated.items():
            target=data_root/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open("x",encoding="utf-8") as stream:
                json.dump(payload,stream,ensure_ascii=False,indent=2)
                stream.write("\n")
            files[relative]=dict(path=relative,bytes=target.stat().st_size,sha256=digest(target),role="generated-scoped-metadata")
        inventory = dict(schema="spikeagent-firebase-snapshot-v1",project_id=PROJECT,
            created_at=datetime.now(timezone.utc).isoformat(),uploaded=False,
            approved_datasets=list(ALLOWED),bundles=catalog,cases=len(case_ids),
            jacob_default_reasons=len(seed["reasons"]),local_database_counts=feedback,
            feedback_database_included=False,
            files=sorted(files.values(),key=lambda row:row["path"]),
            file_count=len(files),bytes=sum(row["bytes"] for row in files.values()),
            excluded=["credentials", "Git metadata", "private uploads", "model weights",
                      "unapproved datasets", "expert workbook", "SQLite databases", "full experiment directories"],
            note="Private deployment staging only. Cloud adapter, UID ownership, billing and deployment still required.")
        with (destination/"inventory.json").open("x",encoding="utf-8") as stream:
            json.dump(inventory,stream,ensure_ascii=False,indent=2)
            stream.write("\n")
    finally:
        os.umask(old_mask)
    return dict(destination=str(destination.relative_to(ROOT)),files=inventory["file_count"],
                bytes=inventory["bytes"],cases=inventory["cases"],bundles=len(catalog),
                reasons=len(seed["reasons"]),local_database_counts=feedback,uploaded=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.output),ensure_ascii=False))
