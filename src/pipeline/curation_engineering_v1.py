"""Opt-in controller core. No SDK/client/network initialization on import.

Stage 4/5 entrypoint supplies an explicitly SCRIPTED, offline provider. This is
not a live rollout CLI: paid provider authorization is a separate stage-6 gate.
The provider boundary requires count(request), respond(request); no retry/fallback.
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from pathlib import Path

import numpy as np

from src.agent.curation_contract import build_request, config_hashes, require_development, sha256
from src.agent.curation_observation_v1 import observe
from scripts.run.run_skill_replay import decision_from_response, reserve, usage_cost

VERSION = "controller-engineering-v1"


class ControllerStop(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def arrays_hash(*arrays):
    h = hashlib.sha256()
    for array in arrays:
        a = np.ascontiguousarray(array)
        h.update(str((a.dtype.str, a.shape)).encode())
        if a.size:
            h.update(memoryview(a).cast("B"))
    return h.hexdigest()


class CurationController:
    def __init__(self, manager, dataset_id, output_root, *, provider, budget_usd,
                 max_calls, sampling_rate=30000., skill="v0", view="raw_member_v1"):
        require_development(dataset_id)
        if manager.history:
            raise ValueError("Supply a fresh manager: checkpoints own the undo history")
        if (not np.isfinite(budget_usd) or budget_usd < 0 or type(max_calls) is not int or max_calls < 1):
            raise ValueError("Explicit finite budget and positive call limit required")
        if skill not in {"v0", "v1"}:
            raise ValueError("Unversioned skill")
        self.manager, self.provider = manager, provider
        self.out = Path(output_root)
        self.out.mkdir(parents=True, exist_ok=False)
        self.dataset_id, self.fs, self.skill, self.view = dataset_id, sampling_rate, skill, view
        self.budget, self.max_calls = float(budget_usd), max_calls
        self.charged, self.calls, self.seen = 0., 0, set()
        self.operations = 0
        self.status = "ready"
        self.input_hash = arrays_hash(manager.overcluster_assigns, manager.spike_times, manager.waveforms)
        self.frozen = config_hashes()
        write_json(self.out / "manifest.json", dict(version=VERSION, dataset_id=dataset_id,
            skill=skill, view=view, sampling_rate=sampling_rate, config_hashes=self.frozen,
            input_sha256=self.input_hash, budget_usd=budget_usd, max_calls=max_calls,
            auto_discard_threshold=0, final_minimum_threshold=0, small_cluster_threshold=4000,
            phase2_prevalidation="same phase1 decision; KEEP eligible, DISCARD applied, SPLIT preserved/ineligible",
            provider_kind=getattr(provider, "kind", "externally-supplied"),
            disclaimer="Engineering core, not a benchmark; no automatic retries or fallback"))
        self.checkpoint("initial")

    def log(self, event, **data):
        with (self.out / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(event=event, calls=self.calls, charged_or_reserved_usd=self.charged,
                                    **data), sort_keys=True, allow_nan=False) + "\n")

    def state_hash(self):
        return arrays_hash(self.manager.assigns, self.manager.hierarchy_tree)

    def checkpoint(self, name):
        m = self.manager
        path = self.out / f"{name}.npz"
        with path.open("xb") as f:
            np.savez_compressed(f, assigns=m.assigns, tree=m.hierarchy_tree,
                                modified=np.array(sorted(m.modified_clusters), dtype=np.int64),
                                counter=np.array(m._operation_counter))
        write_json(path.with_suffix(".json"), dict(sha256=sha256(path), input_sha256=self.input_hash,
            state_sha256=self.state_hash(), dataset_id=self.dataset_id,
            note="Restores cluster state only, NOT budget/calls/scheduler; no automatic resume"))
        return path

    def restore(self, path):
        """Manual engineering rollback, never refunds cost or resumes scheduling."""
        path = Path(path)
        meta = json.loads(path.with_suffix(".json").read_text())
        if (meta["dataset_id"] != self.dataset_id or meta["input_sha256"] != self.input_hash
                or meta["sha256"] != sha256(path)):
            raise ControllerStop("Checkpoint provenance/hash mismatch")
        with np.load(path, allow_pickle=False) as saved:
            if saved["assigns"].shape != self.manager.assigns.shape:
                raise ControllerStop("Checkpoint shape mismatch")
            self.manager.assigns = saved["assigns"].copy()
            self.manager.hierarchy_tree = saved["tree"].copy()
            self.manager.modified_clusters = set(map(int, saved["modified"]))
            self.manager._operation_counter = int(saved["counter"])
        self.manager.history.clear()
        self.manager.history_index = -1
        self.manager._build_cluster_composition()
        self.log("state_restored", checkpoint=path.name, state_sha256=self.state_hash())

    def ask(self, phase, cid, target=None, *, context=None):
        if self.status in {"stopped", "completed"}:
            raise ControllerStop("Finished controllers cannot send further requests")
        self.status = "running"
        try:
            key = (context or phase, int(cid), target, self.state_hash())
            if key in self.seen:
                raise ControllerStop("Repeated decision state; stop without another call")
            if self.calls >= self.max_calls:
                raise ControllerStop("Call limit reached")
            if config_hashes() != self.frozen:
                raise ControllerStop("Configuration changed during run")
            obs, urls, pngs, display = observe(self.manager, phase, cid, target,
                                              sampling_rate=self.fs, view=self.view)
            request = build_request(phase, obs, urls, skill_version=self.skill)
            request["service_tier"] = "default"
            step = self.out / f"step_{self.calls + 1:05d}"
            step.mkdir(exist_ok=False)
            for item, png in zip(display["images"], pngs):
                (step / (item["name"] + ".png")).write_bytes(png)
            write_json(step / "request.json", dict(phase=phase, context=context, observation=obs,
                display=display, request_sha256=digest(request), state_sha256=key[-1]))
            n_input = self.provider.count(request)
            held = reserve(self.charged, self.budget, n_input, request["max_output_tokens"])
            self.charged += held
            self.log("reserved", amount_usd=held, step=step.name)
            self.seen.add(key)
            self.calls += 1
            raw = self.provider.respond(request)
            write_json(step / "response.json", raw)
            # Even an incomplete response may be charged. Settle usage first.
            cost = usage_cost(raw)
            self.charged += cost["conservative_usd"] - held
            self.log("usage", **cost, step=step.name)
            if cost["conservative_usd"] > held or self.charged > self.budget:
                raise ControllerStop("Reported cost exceeded reservation; stop")
            decision = decision_from_response(raw, phase)
            self.log("decision", phase=phase, cid=int(cid), target=target, **decision)
            return decision
        except Exception as exc:
            self.status = "stopped"
            self.log("ABSTAIN_stop", phase=phase, cid=int(cid), error_type=type(exc).__name__,
                     status_code=getattr(exc, "status_code", None), state_sha256=self.state_hash(),
                     reason="Request/provider/parse/budget failure; state preserved; no retry")
            raise ControllerStop(f"Stopped: {type(exc).__name__}") from exc

    def apply(self, action, cid, target=None):
        m = self.manager
        if self.status == "stopped":
            raise ControllerStop("Controller stopped")
        if action in {"KEEP", "NOT_MERGE"}:
            return [cid]
        self.operations += 1
        before = self.checkpoint(f"operation_{self.operations:05d}_before")
        try:
            if cid not in m.get_active_clusters():
                raise ValueError("Inactive source")
            if action == "SPLIT":
                old = m.assigns.copy()
                indices = np.flatnonzero(m.hierarchy_tree[0] == cid)
                if not len(indices):
                    raise ValueError("No last merge to split")
                child = int(m.hierarchy_tree[1, indices[-1]])
                if child <= 0 or child in m.get_active_clusters():
                    raise ValueError("Breakaway label collides with active cluster")
                result = list(map(int, m.split_last_merge(cid)))
                if (len(result) != 2 or set(np.unique(m.assigns[old == cid])) != set(result)
                        or not np.array_equal(old[old != cid], m.assigns[old != cid])):
                    raise ValueError("Degenerate split or unrelated members modified")
            elif action == "MERGE":
                if target == cid or target not in m.get_active_clusters():
                    raise ValueError("Invalid merge target")
                m.merge_clusters([cid, target], target_id=target)
                result = [target]
            elif action == "DISCARD":
                m.discard_cluster(cid)
                result = []
            else:
                raise ValueError("Invalid action")
            self.checkpoint(f"operation_{self.operations:05d}_after")
            # Durable full-state checkpoints replace quadratic in-memory history.
            m.history.clear(); m.history_index = -1
            self.log("executed", action=action, cid=int(cid), target=target, state_sha256=self.state_hash())
            return result
        except Exception as exc:
            self.restore(before)
            self.status = "stopped"
            self.log("ABSTAIN_stop", reason="Execution failed; full cluster state restored", error_type=type(exc).__name__)
            raise ControllerStop("Execution failed; state restored") from exc

    def run(self):
        """Same local scheduling semantics; no automatic 500/5000 deletion."""
        try:
            queue = deque(map(int, self.manager.get_active_clusters()))
            while queue:
                cid = queue.popleft()
                d = self.ask("phase1", cid)
                children = self.apply(d["action"], cid)
                if d["action"] == "SPLIT":
                    queue.extendleft(reversed(children))
            active = list(map(int, self.manager.get_active_clusters()))
            small = [cid for cid in active if np.count_nonzero(self.manager.assigns == cid) < 4000]
            large = [cid for cid in active if cid not in small]
            valid = []
            if small and large:
                for cid in large:
                    d = self.ask("phase1", cid, context="phase2_prevalidation")
                    if d["action"] == "KEEP":
                        valid.append(cid)
                    elif d["action"] == "DISCARD":
                        self.apply("DISCARD", cid)
                    else:
                        self.log("preserved_ineligible", cid=cid, action=d["action"])
            for cid in small:
                for target in valid:
                    d = self.ask("phase2", cid, target)
                    self.apply(d["action"], cid, target)
                    if d["action"] in {"MERGE", "DISCARD"}:
                        break
                else:
                    self.log("preserved_unresolved", cid=cid, reason="No target or all NOT_MERGE")
            self.status = "completed"
        except Exception:
            self.status = "stopped"
            raise
        finally:
            self.checkpoint("terminal" if self.status == "completed" else "stopped")
            write_json(self.out / "summary.json", dict(status=self.status, calls=self.calls,
                charged_or_reserved_usd=self.charged, n_active_spikes=int(np.count_nonzero(self.manager.assigns)),
                n_clusters=len(self.manager.get_active_clusters()), final_sha256=self.state_hash(),
                terminal_evaluation_allowed=self.status == "completed"))
        return self.manager.assigns.copy()
