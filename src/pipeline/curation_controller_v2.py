"""Controller v2: one work queue, single-cluster and pair judgements interleaved. No phases.

v1 (curation_engineering_v1) ran every single-cluster judgement first, then split clusters at 4000
spikes and compared each small one with re-validated large ones in a fixed order. v2 keeps the two
judgement types but drops the phase barrier:

- every cluster gets a single judgement (KEEP / SPLIT / DISCARD); SPLIT is offered only when the
  last hierarchical merge can be undone, so a leaf SPLIT can never be chosen;
- right after a KEEP, the cluster is compared with the k already-kept clusters whose mean waveforms
  correlate best, in that order, regardless of size; source = the smaller of the two;
- the first MERGE is executed and the merged cluster is judged again; a SPLIT there undoes the merge
  and both clusters stay kept; a pair DISCARD removes the source; otherwise the next candidate;
- a pair that would exactly undo one of the decider's own SPLITs (including that one) is not proposed;
- no re-validation pass, no size threshold, no "unresolved small clusters are kept" rule.

The decider owns observations, requests and costs; this module owns scheduling, execution,
checkpoints and limits. Executor, checkpoints and logging are inherited from engineering_v1.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
from time import monotonic

import numpy as np

from src.agent.curation_contract import require_development
from src.pipeline.curation_engineering_v1 import CurationController, ControllerStop, arrays_hash, write_json

VERSION = "controller-v2-interleaved"
ACTIONS = {"single": ("KEEP", "SPLIT", "DISCARD"), "pair": ("MERGE", "NOT_MERGE", "DISCARD")}
ORDERS = ("spike_count_desc", "cluster_id")


class LimitStop(RuntimeError):
    """A per-channel safety limit (decisions, seconds, or a decider budget) was reached."""


def split_available(m, cid):
    """True iff split_last_merge(cid) would produce two non-empty clusters without a label collision."""
    idx = np.flatnonzero(m.hierarchy_tree[0] == cid)
    if not len(idx):
        return False
    last = int(idx[-1])
    child = int(m.hierarchy_tree[1, last])
    if child <= 0 or child in m.get_active_clusters():
        return False
    branch = {child}
    for i in range(last - 1, -1, -1):
        src, other = map(int, m.hierarchy_tree[:2, i])
        if src in branch:
            branch.add(other)
    present = set(m.cluster_composition[cid])
    return bool(present & branch) and bool(present - branch)


def correlation(a, b):
    """Pearson correlation of two mean waveforms; same definition as the observation's 'correlation'."""
    if np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.clip(np.corrcoef(a, b)[0, 1], -1, 1))


class CurationControllerV2(CurationController):
    """Inherits apply/checkpoint/restore/log/state_hash only; scheduling and the decision interface are new."""

    def __init__(self, manager, dataset_id, output_root, *, decider, sampling_rate, k=3,
                 order="spike_count_desc", max_decisions=5000, max_seconds=None, manifest=None):
        require_development(dataset_id)
        if manager.history:
            raise ValueError("Supply a fresh manager: checkpoints own the undo history")
        if type(k) is not int or k < 1 or order not in ORDERS or type(max_decisions) is not int or max_decisions < 1:
            raise ValueError("Invalid k, order or decision limit")
        if max_seconds is not None and not (np.isfinite(max_seconds) and max_seconds > 0):
            raise ValueError("Invalid time limit")
        self.manager, self.decider, self.out = manager, decider, Path(output_root)
        self.out.mkdir(parents=True, exist_ok=False)
        self.dataset_id, self.fs, self.k, self.order = dataset_id, sampling_rate, k, order
        self.max_decisions, self.max_seconds = max_decisions, max_seconds
        self.calls, self.operations, self.charged, self.status = 0, 0, 0., "ready"
        self.seen, self.kept, self.undo_split = set(), set(), {}
        self.means, self.started = {}, None
        self.input_hash = arrays_hash(manager.overcluster_assigns, manager.spike_times, manager.waveforms)
        write_json(self.out / "manifest.json", dict(self.describe(), dataset_id=dataset_id,
            input_sha256=self.input_hash, sampling_rate=sampling_rate, k=k, order=order,
            max_decisions=max_decisions, max_seconds=max_seconds, phases=None, small_cluster_threshold=None,
            prevalidation=None, unresolved_rule=None, auto_discard_threshold=0,
            split_offered="only when the last hierarchical merge can be undone",
            decider=getattr(decider, "kind", type(decider).__name__), **(manifest or {})))
        self.checkpoint("initial")

    def describe(self):
        return dict(version=VERSION, merge_candidates=f"top {self.k} already-kept clusters by mean-waveform "
                    "correlation, any size; first MERGE executed, merged cluster judged again")

    # ------------------------------------------------------------------ decisions
    def ask(self, kind, cid, target=None):
        if self.status in {"stopped", "completed"}:
            raise ControllerStop("Finished controllers cannot ask again")
        key = (kind, int(cid), target, self.state_hash())
        if key in self.seen:
            raise ControllerStop("Repeated decision state: scheduler bug")
        if self.calls >= self.max_decisions:
            raise LimitStop("Decision limit reached")
        if self.max_seconds is not None and monotonic() - self.started > self.max_seconds:
            raise LimitStop("Time limit reached")
        m = self.manager
        allowed = ["KEEP", "DISCARD"] + (["SPLIT"] if split_available(m, cid) else []) if kind == "single" \
            else list(ACTIONS["pair"])
        self.seen.add(key)
        self.calls += 1
        folder = self.out / f"step_{self.calls:05d}"
        folder.mkdir()
        write_json(folder / "question.json", dict(kind=kind, cid=int(cid), target=target, allowed=allowed,
                                                   state_sha256=key[-1]))
        d = self.decider.decide(m, kind, int(cid), target, allowed, folder=folder, sampling_rate=self.fs)
        if d.get("action") not in allowed or not isinstance(d.get("rationale"), str):
            raise ControllerStop("Decider returned an action outside the offered set")
        self.log("decision", kind=kind, cid=int(cid), target=target, allowed=allowed,
                 action=d["action"], rationale=d["rationale"])
        return d["action"]

    # ------------------------------------------------------------------ helpers
    def size(self, cid):
        return int(np.count_nonzero(self.manager.assigns == cid))

    def mean(self, cid):
        if cid not in self.means:
            self.means[cid] = self.manager.waveforms[self.manager.assigns == cid].mean(axis=0)
        return self.means[cid]

    def members(self, *cids):
        return arrays_hash(np.flatnonzero(np.isin(self.manager.assigns, cids)))

    def execute(self, action, cid, target=None):
        touched = [cid] if target is None else [cid, target]
        result = self.apply(action, cid, target)
        for c in touched + list(result):
            self.means.pop(c, None)
        return result

    def candidates(self, cid):
        active, ranked = set(self.manager.get_active_clusters()), []
        for other in self.kept:
            if other == cid or other not in active:
                continue
            pair = (min(cid, other), max(cid, other))
            if pair in self.undo_split and self.undo_split[pair] == self.members(cid, other):
                continue
            r = correlation(self.mean(cid), self.mean(other))
            if r is not None:
                ranked.append((-r, other))
        return [other for _, other in sorted(ranked)[:self.k]]

    # ------------------------------------------------------------------ scheduling
    def initial_queue(self):
        ids = list(map(int, self.manager.get_active_clusters()))
        if self.order == "spike_count_desc":
            ids.sort(key=lambda c: (-self.size(c), c))
        return deque(ids)

    def merge_check(self, cid, queue):
        """Compare a just-kept cluster with its best-correlated kept clusters; act on the first MERGE/DISCARD."""
        for other in self.candidates(cid):
            source, target = sorted((cid, other), key=lambda c: (self.size(c), c))
            before = self.state_hash()
            action = self.ask("pair", source, target)
            if action == "MERGE":
                self.execute("MERGE", source, target)
                self.kept.discard(source); self.kept.discard(target)
                queue.appendleft((target, (source, before)))
                return
            if action == "DISCARD":
                self.execute("DISCARD", source)
                self.kept.discard(source)
                if source == cid:
                    return

    def review(self, cid, merged, queue):
        state = self.state_hash()
        action = self.ask("single", cid)
        if action == "DISCARD":
            self.execute("DISCARD", cid)
        elif action == "SPLIT":
            members = self.members(cid)
            first, second = self.execute("SPLIT", cid)
            self.undo_split[(min(first, second), max(first, second))] = members
            if merged and merged[0] == second and self.state_hash() == merged[1]:
                # The re-judgement undid the merge it was asked about: both were kept before it; not re-proposed.
                self.kept.update((first, second))
                self.log("merge_vetoed", source=second, target=first, state_sha256=state)
                return
            queue.extendleft([(second, None), (first, None)])
        else:
            self.kept.add(cid)
            self.merge_check(cid, queue)

    def run(self):
        self.status, self.started = "running", monotonic()
        try:
            queue = deque((cid, None) for cid in self.initial_queue())
            while queue:
                cid, merged = queue.popleft()
                if cid in self.manager.get_active_clusters():
                    self.review(cid, merged, queue)
            self.status = "completed"
        except LimitStop as exc:
            self.status = "stopped_limit"
            self.log("limit_stop", reason=str(exc), state_sha256=self.state_hash())
        except BaseException:
            self.status = "stopped"
            raise
        finally:
            done = self.status == "completed"
            self.checkpoint("terminal" if done else "stopped")
            write_json(self.out / "summary.json", dict(status=self.status, decisions=self.calls,
                operations=self.operations, seconds=monotonic() - self.started,
                n_active_spikes=int(np.count_nonzero(self.manager.assigns)),
                n_clusters=len(self.manager.get_active_clusters()), final_sha256=self.state_hash(),
                terminal_evaluation_allowed=done))
        return self.manager.assigns.copy()


# ====================================================================== rounds (harness v2, §3.7.4)
ROUNDS_VERSION = "controller-v2-rounds"


class RoundsController(CurationControllerV2):
    """Round-synchronous scheduling. Each round collects every independent question on the frozen
    current state (single judgements of new clusters, pair judgements of newly kept clusters against
    their top-k correlated kept clusters), asks them as one batch and only then executes the answers.
    Parallelism (`parallel`) therefore changes timing, never the questions or the outcome.

    Execution: singles first (DISCARD, SPLIT children judged next round, KEEP joins the kept set);
    then pair answers in decreasing correlation, each cluster in at most one MERGE/DISCARD per round
    (later conflicting answers are void and logged). A merged cluster is judged again next round; a
    SPLIT that exactly restores the two merged clusters vetoes the merge and the pair is never asked
    again. A pair already answered (identified by both clusters' member hashes) is not asked again.
    Final sweep (default on): when nothing is left, each kept cluster is asked once against its best
    correlated kept cluster not yet asked; resulting merges are judged as usual; no second sweep.
    Limits: decisions, rounds, seconds, decider budgets -> 'stopped_limit'; the stopped state is
    scored separately ('stopped_state'), never mixed with completed runs.
    """

    def __init__(self, manager, dataset_id, output_root, *, parallel=1, final_sweep=True, max_rounds=None, **kw):
        if type(parallel) is not int or parallel < 1 or (max_rounds is not None and (type(max_rounds) is not int
                                                                                    or max_rounds < 1)):
            raise ValueError("Invalid parallel or max_rounds")
        self.parallel, self.final_sweep, self.max_rounds = parallel, bool(final_sweep), max_rounds
        self.round, self.asked_pairs, self.vetoed = 0, set(), set()
        super().__init__(manager, dataset_id, output_root, **kw)

    def describe(self):
        return dict(version=ROUNDS_VERSION, scheduling="round-synchronous; answers executed after the whole round",
                    parallel=self.parallel, final_sweep=self.final_sweep, max_rounds=self.max_rounds,
                    merge_candidates=f"top {self.k} kept clusters by mean-waveform correlation, any size; "
                                     "MERGEs executed in decreasing correlation, one per cluster per round")

    # ------------------------------------------------------------------ questions
    def pair_key(self, a, b):
        return frozenset((self.members(a), self.members(b)))

    def ranked(self, cid):
        active, ranked = set(self.manager.get_active_clusters()), []
        for other in self.kept:
            if other == cid or other not in active:
                continue
            pair = (min(cid, other), max(cid, other))
            if pair in self.undo_split and self.undo_split[pair] == self.members(cid, other):
                continue
            r = correlation(self.mean(cid), self.mean(other))
            if r is not None:
                ranked.append((-r, other))
        return [(other, -r) for r, other in sorted(ranked)]

    def pair_question(self, a, b, r, chosen):
        key = self.pair_key(a, b)
        if key in self.asked_pairs or key in self.vetoed or key in chosen:
            return None
        chosen.add(key)
        source, target = sorted((a, b), key=lambda c: (self.size(c), c))
        return dict(kind="pair", cid=source, target=target, correlation=r, key=key)

    def by_size(self, cids):
        return sorted(cids, key=lambda c: (-self.size(c), c))

    def round_pairs(self, fresh):
        chosen, out = set(), []
        for cid in self.by_size(fresh):
            for other, r in self.ranked(cid)[:self.k]:
                q = self.pair_question(cid, other, r, chosen)
                if q:
                    out.append(q)
        return out

    def sweep_pairs(self):
        chosen, out = set(), []
        for cid in self.by_size(self.kept):
            for other, r in self.ranked(cid):
                q = self.pair_question(cid, other, r, chosen)
                if q:
                    out.append(q)
                    break
        return out

    # ------------------------------------------------------------------ asking
    def ask_round(self, questions):
        if self.calls + len(questions) > self.max_decisions:
            raise LimitStop("Decision limit reached")
        if self.max_seconds is not None and monotonic() - self.started > self.max_seconds:
            raise LimitStop("Time limit reached")
        m, state, jobs = self.manager, self.state_hash(), []
        for q in questions:
            key = (q["kind"], self.members(q["cid"]), None if q.get("target") is None else self.members(q["target"]))
            if key in self.seen:
                raise ControllerStop("Repeated decision state: scheduler bug")
            self.seen.add(key)
            q["allowed"] = ["KEEP", "DISCARD"] + (["SPLIT"] if split_available(m, q["cid"]) else []) \
                if q["kind"] == "single" else list(ACTIONS["pair"])
            self.calls += 1
            folder = self.out / f"step_{self.calls:05d}"
            folder.mkdir()
            write_json(folder / "question.json", dict(kind=q["kind"], cid=int(q["cid"]), target=q.get("target"),
                       allowed=q["allowed"], round=self.round, state_sha256=state))
            prepared = None
            if hasattr(self.decider, "prepare"):  # rendering is serial; only the model calls run in parallel
                prepared = self.decider.prepare(m, q["kind"], int(q["cid"]), q.get("target"), q["allowed"],
                                                folder=folder, sampling_rate=self.fs)
            jobs.append((q, folder, prepared))

        def answer(job):
            q, folder, prepared = job
            if prepared is None:
                return self.decider.decide(m, q["kind"], int(q["cid"]), q.get("target"), q["allowed"],
                                           folder=folder, sampling_rate=self.fs)
            return self.decider.answer(prepared, q["allowed"], folder=folder)

        if self.parallel > 1 and len(jobs) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=self.parallel) as pool:
                answers = list(pool.map(answer, jobs))
        else:
            answers = [answer(job) for job in jobs]
        for (q, _, _), d in zip(jobs, answers):
            if not isinstance(d, dict) or d.get("action") not in q["allowed"] or not isinstance(d.get("rationale"), str):
                raise ControllerStop("Decider returned an action outside the offered set")
            q["action"] = d["action"]
            self.log("decision", kind=q["kind"], cid=int(q["cid"]), target=q.get("target"), allowed=q["allowed"],
                     action=d["action"], rationale=d["rationale"], round=self.round,
                     observations={k: v for k, v in d.items() if k not in ("action", "rationale", "gate")} or None,
                     gate=d.get("gate"))
        return questions

    # ------------------------------------------------------------------ execution
    def execute_round(self, singles, pairs):
        pending, fresh, active = [], [], lambda c: c in self.manager.get_active_clusters()
        for q in singles:
            cid, action, merged = q["cid"], q["action"], q.get("merged")
            if action == "DISCARD":
                self.execute("DISCARD", cid)
            elif action == "SPLIT":
                members = self.members(cid)
                first, second = self.execute("SPLIT", cid)
                self.undo_split[(min(first, second), max(first, second))] = members
                if merged and {self.members(first), self.members(second)} == merged:
                    self.kept.update((first, second))
                    self.vetoed.add(frozenset(merged))
                    self.log("merge_vetoed", source=second, target=first, round=self.round)
                else:
                    pending += [dict(cid=first), dict(cid=second)]
            else:
                self.kept.add(cid)
                fresh.append(cid)
        used = set()
        for q in sorted(pairs, key=lambda q: -q["correlation"]):
            self.asked_pairs.add(q["key"])
            source, target, action = q["cid"], q["target"], q["action"]
            if action == "NOT_MERGE":
                continue
            if source in used or target in used or not (active(source) and active(target)):
                self.log("pair_void", source=source, target=target, action=action, round=self.round,
                         reason="a cluster of this pair already changed in this round")
                continue
            if action == "MERGE":
                merged = {self.members(source), self.members(target)}
                self.execute("MERGE", source, target)
                self.kept.discard(source)
                self.kept.discard(target)
                used.update((source, target))
                pending.append(dict(cid=target, merged=merged))
            else:  # DISCARD the source
                self.execute("DISCARD", source)
                self.kept.discard(source)
                used.add(source)
        return pending, fresh

    def run(self):
        self.status, self.started = "running", monotonic()
        swept = False
        try:
            pending = [dict(cid=c) for c in self.initial_queue()]
            fresh = []
            while True:
                singles = [dict(q, kind="single") for q in pending if q["cid"] in self.manager.get_active_clusters()]
                order = {c: i for i, c in enumerate(self.by_size([q["cid"] for q in singles]))}
                singles.sort(key=lambda q: order[q["cid"]])
                pairs = self.round_pairs(fresh)
                if not singles and not pairs:
                    if not self.final_sweep or swept:
                        break
                    swept, pairs = True, self.sweep_pairs()
                    self.log("final_sweep", pairs=len(pairs))
                    if not pairs:
                        break
                if self.max_rounds is not None and self.round >= self.max_rounds:
                    raise LimitStop("Round limit reached")
                self.round += 1
                self.log("round", round=self.round, singles=len(singles), pairs=len(pairs),
                         state_sha256=self.state_hash())
                self.ask_round(singles + pairs)
                pending, fresh = self.execute_round(singles, pairs)
            self.status = "completed"
        except LimitStop as exc:
            self.status = "stopped_limit"
            self.log("limit_stop", reason=str(exc), round=self.round, state_sha256=self.state_hash())
        except BaseException:
            self.status = "stopped"
            raise
        finally:
            done = self.status == "completed"
            self.checkpoint("terminal" if done else "stopped")
            write_json(self.out / "summary.json", dict(status=self.status, decisions=self.calls, rounds=self.round,
                operations=self.operations, seconds=monotonic() - self.started, final_sweep_done=swept,
                n_active_spikes=int(np.count_nonzero(self.manager.assigns)),
                n_clusters=len(self.manager.get_active_clusters()), final_sha256=self.state_hash(),
                terminal_evaluation_allowed=done, stopped_state_evaluation=self.status == "stopped_limit"))
        return self.manager.assigns.copy()
