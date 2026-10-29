"""Persistent N-feedback trigger and explicit skill activation. Single-writer file lock (Linux/macOS).

Bundles are copied into the session so it can travel with the repository to RunPod. A failed or
interrupted attempt blocks automatic retry. Accepted/rejected attempts consume their feedback once.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import fcntl
import json
import os
from pathlib import Path
import shutil
import tempfile

from src.agent.ace_gepa import Case, cases_from_bundles, digest
from src.agent.feedback_optimization import read, sha, load_bundle
from src.agent.skill_policy import load_skill
from src.pipeline.curation_engineering_v1 import write_json


def atomic_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def batch_size(value):
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError("feedback_batch_size must be a positive integer or null (manual)")
    return value


class FeedbackSession:
    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextmanager
    def locked(self):
        with (self.path / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _save(self, state):
        atomic_json(self.path / "state.json", state)

    def _read(self):
        state = read(self.path / "state.json")
        if state.get("schema") != "ace-gepa-session-v1":
            raise ValueError("Unsupported feedback session")
        return state

    def _copy_bundle(self, path):
        bundle = load_bundle(path)
        key = sha(Path(path) / "bundle.json")
        relative = f"bundles/{key}"
        destination = self.path / relative
        if not destination.exists():
            staging = Path(tempfile.mkdtemp(dir=self.path / "bundles", prefix=".import-"))
            try:
                shutil.copyfile(Path(path) / "bundle.json", staging / "bundle.json")
                for row in bundle["cases"]:
                    step = row["review"]["step"]
                    (staging / step).mkdir()
                    for name in row["files"]:
                        shutil.copyfile(Path(path) / step / name, staging / step / name)
                load_bundle(staging)
                if sha(staging / "bundle.json") != key:
                    raise ValueError("Bundle changed during import")
                staging.rename(destination)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        elif sha(destination / "bundle.json") != key:
            raise ValueError("Stored bundle changed")
        return [dict(asdict(c), folder=relative) for c in cases_from_bundles([destination], bundle["role"])]

    def _case(self, record):
        return Case(**dict(record, folder=str(self.path / record["folder"])))

    @classmethod
    def create(cls, path, base_skill, validation_paths, *, feedback_batch_size=10, config=None):
        batch_size(feedback_batch_size)
        load_skill(base_skill)
        cases_from_bundles(validation_paths, "validation")
        session = cls(path)
        session.path.mkdir(parents=True, exist_ok=False)
        (session.path / "bundles").mkdir()
        (session.path / "attempts").mkdir()
        shutil.copyfile(base_skill, session.path / "seed_skill.json")
        validation = []
        for folder in validation_paths:
            validation += session._copy_bundle(folder)
        state = dict(schema="ace-gepa-session-v1", feedback_batch_size=feedback_batch_size,
                     active_skill="seed_skill.json", active_sha256=sha(session.path / "seed_skill.json"),
                     validation=validation, feedback={}, order=[], attempts=[], blocked=False,
                     config=deepcopy(config or {}))
        session._save(state)
        return session

    def import_bundle(self, path):
        cases_from_bundles([path], "learning")
        with self.locked():
            state = self._read()
            if any(a["status"] == "running" for a in state["attempts"]):
                raise RuntimeError("Recover interrupted optimization before importing feedback")
            records = self._copy_bundle(path)
            validation_keys = {self._case(r).key for r in state["validation"]}
            added, revised = 0, 0
            for record in records:
                case = self._case(record)
                key = case.key
                if key in validation_keys:
                    raise ValueError("Validation cases cannot also become learning feedback")
                old = state["feedback"].get(key)
                if old:
                    review, previous = case.row["review"], old["case"]["row"]["review"]
                    if review == previous:
                        continue
                    if review["revision"] <= previous["revision"]:
                        raise ValueError("Conflicting or stale feedback revision")
                    if old["consumed_by"] is not None:
                        raise ValueError("Revision affects an already optimized case; start a corrected session explicitly")
                    revised += 1
                else:
                    state["order"].append(key)
                    added += 1
                state["feedback"][key] = dict(case=record, consumed_by=None)
            self._save(state)
            return dict(added=added, revised=revised, pending=self._pending(state))

    @staticmethod
    def _pending(state):
        return [key for key in state["order"] if state["feedback"][key]["consumed_by"] is None]

    def status(self):
        with self.locked():
            state = self._read()
            return dict(feedback_batch_size=state["feedback_batch_size"], pending=len(self._pending(state)),
                        active_skill=state["active_skill"], active_sha256=state["active_sha256"],
                        attempts=state["attempts"], blocked=state["blocked"])

    def set_batch_size(self, value):
        batch_size(value)
        with self.locked():
            state = self._read()
            state["feedback_batch_size"] = value
            self._save(state)

    def active(self):
        with self.locked():
            state = self._read()
            path = self.path / state["active_skill"]
            if sha(path) != state["active_sha256"]:
                raise ValueError("Active skill artifact changed")
            load_skill(path)
            return path

    def _finish(self, state, attempt, result):
        if result["status"] not in ("accepted", "rejected"):
            raise ValueError("Attempt did not finish successfully")
        if result["status"] == "accepted":
            skill_path = self.path / attempt["output"] / "accepted_skill.json"
            if sha(skill_path) != result["accepted_skill_sha256"]:
                raise ValueError("Accepted skill checksum mismatch")
            skill, _ = load_skill(skill_path)
            previous, _ = load_skill(self.path / state["active_skill"])
            if skill.get("parent") != previous["version"]:
                raise ValueError("Accepted skill has the wrong parent")
            state.update(active_skill=str(skill_path.relative_to(self.path)), active_sha256=sha(skill_path))
        for key in attempt["keys"]:
            state["feedback"][key]["consumed_by"] = attempt["id"]
        attempt["status"] = result["status"]
        state["blocked"] = False

    def advance(self, optimizer, *, force=False, retry_failed=False):
        """At most one N-sized update. Caller invokes only between rounds or outside a run."""
        with self.locked():
            state = self._read()
            if any(a["status"] == "running" for a in state["attempts"]):
                raise RuntimeError("Interrupted attempt: run session recover before continuing")
            if state["blocked"] and not retry_failed:
                raise RuntimeError("Failed update blocks automatic retry; use --retry-failed explicitly")
            pending, n = self._pending(state), state["feedback_batch_size"]
            if not pending or (not force and (n is None or len(pending) < n)):
                return dict(status="waiting", pending=len(pending))
            selected = pending[:n] if n is not None else pending
            attempt_id = f"update_{len(state['attempts']) + 1:04d}"
            base_path = self.path / state["active_skill"]
            if sha(base_path) != state["active_sha256"]:
                raise ValueError("Active skill artifact changed")
            attempt = dict(id=attempt_id, keys=selected, base_sha256=state["active_sha256"],
                           output=f"attempts/{attempt_id}", status="running")
            state["attempts"].append(attempt)
            self._save(state)  # durable claim before any model calls
            learning = [self._case(state["feedback"][k]["case"]) for k in selected]
            validation = [self._case(r) for r in state["validation"]]
            try:
                result = optimizer(base_path, learning, validation, self.path / attempt["output"],
                                   attempt_id, deepcopy(state["config"]))
                saved = read(self.path / attempt["output"] / "result.json")
                if saved != result:
                    raise ValueError("Optimizer result differs from the durable artifact")
                self._finish(state, attempt, result)
            except Exception as exc:
                attempt.update(status="failed", error_type=type(exc).__name__, error=str(exc))
                state["blocked"] = True
                self._save(state)
                raise
            self._save(state)
            return dict(status=result["status"], attempt=attempt_id, pending=len(self._pending(state)),
                        active_skill=state["active_skill"])

    def recover(self):
        """Reconcile saved results after interruption, without making any model calls."""
        with self.locked():
            state = self._read()
            for attempt in state["attempts"]:
                if attempt["status"] != "running":
                    continue
                result_path = self.path / attempt["output"] / "result.json"
                result = read(result_path) if result_path.exists() else {"status": "interrupted"}
                if result["status"] in ("accepted", "rejected"):
                    self._finish(state, attempt, result)
                else:
                    attempt.update(status="failed", error="interrupted; pending feedback retained")
                    state["blocked"] = True
            self._save(state)
