"""Archive the two stopped Astra incidents without touching active runs.

Copy only small diagnostic evidence; index/hash the existing images and state
snapshots in place. Never include credentials or silently overwrite an archive.
"""
import hashlib
import json
from pathlib import Path
import shutil
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parents[2]
CASES = {
    "ASTRA-001-budget-stop": "astra_high_channel_guards_budget30_20260929",
    "ASTRA-002-offline-replay-schema": "astra_high_channel_guards_budget70_resume_20260930_offline_validation_failed",
}
CORE = ["protocol.json", "budget.json", "batch_status.json", "replay_audit.log",
        "CH30.log", "CH30/status.json", "CH30/requests.jsonl", "CH30/responses.jsonl",
        "CH30/decisions.jsonl", "CH30/effective_decisions.jsonl", "CH30/replayed_calls.jsonl",
        "CH30/action_log.partial.csv", "CH30/assigns.partial.npy",
        "harness_snapshot.py", "astra_harness_snapshot.py", "resume_harness_snapshot.py"]


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def archive(case, directory):
    source = REPO / "output" / directory
    dest = REPO / "output/failure_archive" / case
    if dest.exists():
        raise FileExistsError(f"Archive exists; will not overwrite: {case}")
    status = json.loads((source / "CH30/status.json").read_text())
    if status["status"] != "failed":
        raise ValueError("Refusing to archive an active/successful channel as a failure")
    dest.mkdir(parents=True)
    entries = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        if any(x in rel.parts for x in ("mpl-cache", "__pycache__")):
            continue
        if path.name == ".env" or path.suffix in (".pem", ".key"):
            raise ValueError("Secret-like file is outside archive scope")
        before = path.stat()
        checksum = sha(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError("Evidence changed during archive")
        copied = rel.as_posix() in CORE
        if copied:
            target = dest / "evidence" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            if sha(target) != checksum:
                raise RuntimeError("Archive copy checksum mismatch")
        entries.append({"path": rel.as_posix(), "bytes": after.st_size, "sha256": checksum,
                        "copied": copied})
    manifest = {"case_id": case, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "source": str(source), "scope": "local only; raw images/states retained at source and hashed",
                "files": entries}
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"case": case, "indexed_files": len(entries),
                      "copied_files": sum(x["copied"] for x in entries), "archive": str(dest)}))


if __name__ == "__main__":
    for case, directory in CASES.items():
        archive(case, directory)
