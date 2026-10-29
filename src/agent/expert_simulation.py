"""Simulated expert from recorded curation actions: strict replay (main) and controller-driven teacher forcing.

Strict replay (`ExpertReplay`): questions come from the expert record, in recorded order, on the exact
pre-action state; every expert edit is asked and then executed whatever the model answers. A cluster that
appears (initially, from a split or a merge) and is never touched by the expert again gets one KEEP question
when it appears (derived, provenance-labelled): the expert only ever picks clusters to edit, so "left alone
for good" is the only recorded evidence of a keep. Optionally (keep_before_merge) a cluster whose next
expert action is a merge is also asked KEEP first, as v2's scheduler would; that follows v2, not the
expert, and is off by default. No scheduler chooses questions.

Controller-driven teacher forcing (`TeacherForcedDecider`): the question distribution is RoundsController's;
answers come from the lookup below. Expert edits the controller never asks about are not executed, so
operation coverage is reported separately and the run can leave the expert path.

The expert's recorded action sequence (Excel sheet or MAT-internal log) is replayed once on a fresh
manager. Every cluster the expert acted on is indexed by its member set (spike indices, the same
identity RoundsController uses), so an agent question about exactly that cluster is answered with
the expert's real action and rationale. The sheets never record KEEP / NOT_MERGE; those and merge
membership are derived from the replayed end state and carry a separate provenance and no invented
rationale. Expert terminal labels (`curation_assigns`) are not used as answers; they are only checked
against the replay for unified datasets.

Both modes ask the model and save its own answer (answer.json, used for review binding).
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from time import monotonic, time

import numpy as np

from scripts.analysis.audit_real_action_sources import load_xlsx_sheets, parse_action
from src.agent.curation_contract import require_development
from src.pipeline.curation_controller_v2 import LimitStop, split_available
from src.pipeline.curation_engineering_v1 import arrays_hash, write_json

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "output/real_split_manifest_20260903/manifest.json"
WORKBOOK = ROOT / "Jacob Bedke-Annotated_spike_sorting_data_w_chronux/action_sheet.xlsx"
VERSION = "expert-replay-sim-v1"
REPLAY_VERSION = "strict-expert-replay-v1"
REVIEWER = "simulated_expert_replay"


def members_key(assigns, cid):
    return arrays_hash(np.flatnonzero(assigns == cid))


def manifest_row(dataset_id):
    rows = {r["dataset_id"]: r for r in json.loads(MANIFEST.read_text(encoding="utf-8"))["datasets"]}
    return rows[dataset_id]


def action_rows(spec, data, workbook=WORKBOOK):
    """(raw action, rationale) pairs in recorded order; a missing rationale stays empty."""
    if spec["action_source"] == "excel":
        rows, source = load_xlsx_sheets(workbook)[spec["dataset_id"]], dict(type="excel", path=str(workbook))
    elif spec["action_source"] == "mat_internal":
        actions = [str(a) for a in data.get("curation_actions", [])]
        reasons = [str(r) for r in data.get("curation_action_reasoning", [])]
        rows, source = list(zip(actions, reasons + [""] * (len(actions) - len(reasons)))), dict(type="mat_internal")
    else:
        raise ValueError(f"No recorded actions for {spec['dataset_id']}")
    if len(rows) != spec["n_actions"]:
        raise ValueError(f"{spec['dataset_id']}: action count {len(rows)} != manifest {spec['n_actions']}")
    return rows, source


class ExpertOracle:
    def __init__(self, manager, rows, *, dataset_id, source=None):
        if manager.history:
            raise ValueError("Supply a fresh manager")
        self.dataset_id, self.source = dataset_id, source or {}
        self.single, self.pairs, self.counts, self.retracted = {}, {}, Counter(), []

        def put(table, key, value):
            if key in table:
                raise ValueError(f"{dataset_id}: expert acted twice on one member set (step {value['step']})")
            table[key] = value

        appended = []  # (to, from) tree rows added by the expert's own merges/discards; splits delete rows
        records = []
        for step, (raw, reason) in enumerate(rows, 1):
            action, src, tgt = parse_action(raw)
            rows_of_src = np.flatnonzero(manager.hierarchy_tree[0] == src)
            last = (src, int(manager.hierarchy_tree[1, rows_of_src[-1]])) if len(rows_of_src) else None
            if action == "split" and last in appended:
                appended.remove(last)
                # Undoing a row the expert appended ('s 0' after a discard, 's T' after a merge into T) retracts
                # that expert edit instead of splitting the original hierarchy.
                kept, restored = manager.split_last_merge(src)
                restored_key = members_key(manager.assigns, restored)
                retracted = (self.single.pop(restored_key, None) if src == 0 else
                             self.pairs.pop(frozenset((members_key(manager.assigns, kept), restored_key)), None))
                if not retracted:
                    raise ValueError(f"{dataset_id} step {step}: {raw!r} undoes no recorded expert edit")
                retracted["retracted"] = True
                self.retracted.append(dict(retracted, undone_at_step=step))
                manager.history.clear()
                manager.history_index = -1
                self.counts[f"UNDO_{retracted['action']}"] += 1
                continue
            active = manager.get_active_clusters()
            if src not in active or (tgt is not None and tgt not in active):
                raise ValueError(f"{dataset_id} step {step}: inactive cluster in {raw!r}")
            key = members_key(manager.assigns, src)
            record = dict(step=step, raw=raw, reason=reason.strip(), action=action.upper(), src=src, tgt=tgt,
                          keys=[key] if tgt is None else [key, members_key(manager.assigns, tgt)])
            records.append(record)
            if action == "merge":
                put(self.pairs, frozenset(record["keys"]), record)
                manager.merge_clusters([src, tgt], target_id=tgt)
                appended.append((tgt, src))
            else:
                put(self.single, key, record)
                manager.split_last_merge(src) if action == "split" else manager.discard_cluster(src)
                if action == "discard":
                    appended.append((0, src))
            manager.history.clear()
            manager.history_index = -1
            self.counts[record["action"]] += 1
        self.final = manager.assigns.copy()
        self.terminal = {members_key(self.final, c) for c in manager.get_active_clusters()}
        # Effective sequence: a retracted edit and its undo restore the state exactly, so both drop out.
        self.sequence = [r for r in records if not r.get("retracted")]
        self.acted = {k for r in self.sequence for k in r["keys"]}

    def next_action(self, key, after):
        """The expert's next effective action on this member set after sequence index `after`, or None. A member
        set can recur (split off, then merged back), so this is a time-ordered lookup, not a global one."""
        for r in self.sequence[after + 1:]:
            if key in r["keys"]:
                return r["action"]
        return None

    @classmethod
    def for_dataset(cls, dataset_id, data, fresh, workbook=WORKBOOK):
        spec = manifest_row(dataset_id)
        rows, source = action_rows(spec, data, workbook)
        oracle = cls(fresh(data), rows, dataset_id=dataset_id, source=dict(source, action_source=spec["action_source"]))
        if spec["target_relation"] == "unified_action_and_terminal" and not np.array_equal(
                oracle.final, data["curation_assigns"]):
            raise ValueError(f"{dataset_id}: unified dataset no longer replays to its terminal labels")
        return oracle

    def describe(self):
        return dict(version=VERSION, dataset_id=self.dataset_id, source=self.source,
                    recorded_actions=dict(self.counts), terminal_clusters=len(self.terminal),
                    retracted=[{k: r[k] for k in ("step", "raw", "undone_at_step")} for r in self.retracted],
                    effective_actions=len(self.sequence),
                    derived="KEEP/NOT_MERGE and merge membership from the replayed end state; no invented rationale")

    def unit(self, assigns, cid):
        """Replayed end-state label shared by all members (0 = all discarded), or None when mixed."""
        labels = np.unique(self.final[assigns == cid])
        return int(labels[0]) if len(labels) == 1 else None

    def label(self, m, kind, cid, target, allowed):
        key = members_key(m.assigns, cid)
        result = dict(labeled=False, action=None, provenance=None, reason="", expert_step=None, expert_raw=None)
        if kind == "single":
            unit = None if key in self.single or key in self.terminal else self.unit(m.assigns, cid)
            if key in self.single:
                rec = self.single[key]
                result.update(action=rec["action"], provenance="expert_recorded", reason=rec["reason"],
                              expert_step=rec["step"], expert_raw=rec["raw"])
            elif key in self.terminal:
                result.update(action="KEEP", provenance="derived_terminal_keep")
            elif unit:
                result.update(action="KEEP", provenance="derived_part_of_kept_unit")
            elif unit == 0:
                result.update(action="DISCARD", provenance="derived_all_members_discarded")
            else:
                result.update(provenance="unlabeled_mixed_expert_units")
        else:
            pair = frozenset((key, members_key(m.assigns, target)))
            source, other = self.unit(m.assigns, cid), self.unit(m.assigns, target)
            if pair in self.pairs:
                rec = self.pairs[pair]
                result.update(action="MERGE", provenance="expert_recorded", reason=rec["reason"],
                              expert_step=rec["step"], expert_raw=rec["raw"])
            elif source and other:
                result.update(action="MERGE" if source == other else "NOT_MERGE",
                              provenance="derived_same_kept_unit" if source == other else "derived_different_units")
            elif source == 0:
                result.update(action="DISCARD", provenance="derived_source_all_discarded")
            else:
                result.update(provenance="unlabeled_mixed_expert_units")
        if result["action"] is not None and result["action"] not in allowed:
            result["provenance"] = f"unlabeled_not_offered:{result['provenance']}"
        result["labeled"] = result["action"] is not None and result["action"] in allowed
        return result


class TeacherForcedDecider:
    """Wraps a prepare/answer decider; the model answers first, then the expert action is executed."""

    kind = "TEACHER_FORCED_EXPERT_REPLAY"

    def __init__(self, inner, oracle):
        self.inner, self.oracle = inner, oracle

    def __getattr__(self, name):  # provider, skill, skill_sha256, contract_path, numbers, ...
        return getattr(self.inner, name)

    def rewrap(self, inner):
        return TeacherForcedDecider(inner, self.oracle)

    def describe(self):
        return dict(self.inner.describe(), simulated_expert=self.oracle.describe())

    def prepare(self, m, kind, cid, target, allowed, *, folder, sampling_rate):
        started, at = monotonic(), time()
        label = self.oracle.label(m, kind, cid, target, allowed)  # on the frozen round state
        content = self.inner.prepare(m, kind, cid, target, allowed, folder=folder, sampling_rate=sampling_rate)
        return dict(content=content, label=label, prepared_at=at, prepare_seconds=monotonic() - started)

    def answer(self, prepared, allowed, *, folder):
        started, at = monotonic(), time()
        d = self.inner.answer(prepared["content"], allowed, folder=folder)
        label = prepared["label"]
        expert = label["action"] if label["labeled"] else None
        submitted = expert or d["action"]  # the controller may still void it (pair conflicts): see events.jsonl
        write_json(Path(folder) / "simulation.json", dict(
            label, version=VERSION, agent_action=d["action"], expert_action=expert, submitted_action=submitted,
            match=None if expert is None else d["action"] == expert,
            timing=dict(prepared_at=prepared["prepared_at"], prepare_seconds=prepared["prepare_seconds"],
                        answer_started_at=at, answer_seconds=monotonic() - started, answered_at=time())))
        return dict(d, action=submitted)

    def decide(self, m, kind, cid, target, allowed, *, folder, sampling_rate):
        return self.answer(self.prepare(m, kind, cid, target, allowed, folder=folder, sampling_rate=sampling_rate),
                           allowed, folder=folder)


def simulated_reviewer(scope="mismatch", sources="all"):
    """Fill a review template from simulation.json. scope: 'mismatch' (only disagreements) or 'all';
    sources: 'recorded' (real expert edits only) or 'all' (also derived KEEP / NOT_MERGE / merge labels)."""
    if scope not in ("mismatch", "all") or sources not in ("recorded", "all"):
        raise ValueError("scope must be mismatch or all; sources recorded or all")

    def fill(run, template):
        for review in template["reviews"]:
            s = json.loads((Path(run) / review["step"] / "simulation.json").read_text(encoding="utf-8"))
            if (s["expert_action"] is None or (scope == "mismatch" and s["match"])
                    or (sources == "recorded" and s["provenance"] != "expert_recorded")):
                continue
            review.update(expert_action=s["expert_action"], expert_reason=s["reason"], reviewer=REVIEWER,
                          evidence_sufficient=True, provenance=s["provenance"])
        return dict(template, synthetic=True)
    return fill


def summarize(run, *, window=20, final=None, oracle=None):
    """Per-step agreement in step order, by provenance and expert action, plus a sliding window."""
    rows = []
    for step in sorted(Path(run).glob("step_*")):
        if (step / "simulation.json").exists():
            s = json.loads((step / "simulation.json").read_text(encoding="utf-8"))
            q = json.loads((step / "question.json").read_text(encoding="utf-8"))
            rows.append(dict(step=step.name, round=q.get("round"), kind=q["kind"], **{
                k: s.get(k) for k in ("agent_action", "expert_action", "provenance", "match", "expert_step")},
                **s["timing"]))
    scored = [r for r in rows if r["match"] is not None]

    def rate(items):
        return dict(n=len(items), correct=sum(r["match"] for r in items),
                    accuracy=sum(r["match"] for r in items) / len(items) if items else None)
    by = lambda key: {v: rate([r for r in scored if r[key] == v]) for v in sorted({r[key] for r in scored})}
    hits = np.array([r["match"] for r in scored], dtype=float)
    sliding = [float(hits[max(0, i + 1 - window):i + 1].mean()) for i in range(len(hits))]
    result = dict(version=VERSION, steps=len(rows), labeled=len(scored), overall=rate(scored),
                  by_provenance=by("provenance"), by_expert_action=by("expert_action"),
                  provenance_counts=dict(Counter(r["provenance"] for r in rows)),
                  sliding_window=dict(window=window, accuracy=sliding),
                  timing=dict(prepare_seconds=sum(r["prepare_seconds"] for r in rows),
                              answer_seconds=sum(r["answer_seconds"] for r in rows)))
    if oracle is not None:
        asked = {r["expert_step"] for r in rows if r["provenance"] == "expert_recorded"}
        missed = [r["raw"] for r in oracle.sequence if r["step"] not in asked]
        result["expert_operations"] = dict(effective=len(oracle.sequence), asked_exactly=len(oracle.sequence) - len(missed),
                                           not_asked=missed)
    events = Path(run) / "events.jsonl"
    if events.exists():
        with events.open(encoding="utf-8") as f:
            result["voided_pairs"] = sum('"event": "pair_void"' in line for line in f)
    if final is not None and oracle is not None:
        result["end_state_vs_expert_replay"] = dict(same_partition=same_partition(final, oracle.final),
                                                    ari=adjusted_rand(oracle.final, final))
    return result, rows


def adjusted_rand(a, b):
    """Hubert-Arabie ARI from the contingency table (numpy only; sklearn is not a project dependency)."""
    _, x = np.unique(a, return_inverse=True)
    _, y = np.unique(b, return_inverse=True)
    table = np.zeros((x.max() + 1, y.max() + 1))
    np.add.at(table, (x, y), 1)
    pairs = lambda v: float((v * (v - 1) / 2).sum())
    index, rows, cols, total = pairs(table), pairs(table.sum(1)), pairs(table.sum(0)), pairs(np.array([len(x)]))
    expected = rows * cols / total if total else 0.
    best = (rows + cols) / 2
    return 1. if best == expected else (index - expected) / (best - expected)


def same_partition(a, b):
    """Identical clusters and identical discarded spikes; merge direction may change surviving IDs."""
    a, b = np.asarray(a), np.asarray(b)
    pairs = np.unique(np.stack([a, b]), axis=1).shape[1]
    return bool(np.array_equal(a == 0, b == 0) and pairs == len(np.unique(a)) == len(np.unique(b)))


class ExpertReplay:
    """Strict replay driver. A round = derived KEEP questions pending at the current state + the next expert
    edit, all asked on that frozen state; then the edit is executed. Output folders match RoundsController
    (manifest.json, step_*/question.json ... answer.json, events.jsonl), so review templates, bundles and
    RoundLearningHook (after_round) work unchanged. The model never chooses what is asked or executed."""

    def __init__(self, manager, oracle, dataset_id, output_root, *, decider, sampling_rate, parallel=1,
                 keep_questions=True, keep_before_merge=False, max_steps=None, after_round=None, prefetch=False,
                 manifest=None):
        require_development(dataset_id)
        if manager.history or type(parallel) is not int or parallel < 1:
            raise ValueError("Supply a fresh manager and a positive parallel")
        self.manager, self.oracle, self.decider, self.fs = manager, oracle, decider, sampling_rate
        self.dataset_id, self.parallel, self.keep, self.max_steps = dataset_id, parallel, keep_questions, max_steps
        self.keep_before_merge = keep_before_merge
        if prefetch and after_round is not None:
            raise ValueError("prefetch answers after the whole path is rendered; it cannot run with a learning hook")
        self.prefetch, self.deferred = prefetch, []  # without learning, answers never change the replayed states
        self.after_round, self.out = after_round, Path(output_root)
        self.calls, self.round, self.charged, self.status = 0, 0, 0., "ready"
        self.out.mkdir(parents=True, exist_ok=False)
        self.input_hash = arrays_hash(manager.overcluster_assigns, manager.spike_times, manager.waveforms)
        write_json(self.out / "manifest.json", dict(
            version=REPLAY_VERSION, dataset_id=dataset_id, input_sha256=self.input_hash, sampling_rate=sampling_rate,
            keep_questions=keep_questions, keep_before_merge=keep_before_merge, max_steps=max_steps, prefetch=prefetch, parallel=parallel, simulated_expert=oracle.describe(),
            questions="expert edits in recorded order (retractions dropped) + one derived KEEP per cluster the "
                      "expert never touches after it appears" + (" or next merges" if keep_before_merge else ""), decider=getattr(decider, "kind", type(decider).__name__),
            **(manifest or {})))

    def log(self, event, **data):
        with (self.out / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(event=event, calls=self.calls, charged_or_reserved_usd=self.charged, **data),
                               sort_keys=True, allow_nan=False) + "\n")

    def state_hash(self):
        return arrays_hash(self.manager.assigns, self.manager.hierarchy_tree)

    def keeps(self, cids, after):
        """KEEP questions for clusters that just appeared (after sequence index `after`) and whose next expert
        action is a merge or nothing at all."""
        if not self.keep:
            return []
        cids = sorted(cids, key=lambda c: (-int(np.count_nonzero(self.manager.assigns == c)), c))
        out = []
        for c in cids:
            following = self.oracle.next_action(members_key(self.manager.assigns, c), after)
            if following is None or (following == "MERGE" and self.keep_before_merge):
                out.append(dict(kind="single", cid=int(c), target=None, record=None,
                                provenance="derived_keep_before_merge" if following else "derived_no_later_expert_action"))
        return out

    def ask_round(self, questions):
        if self.max_steps is not None and self.calls + len(questions) > self.max_steps:
            raise LimitStop("Step limit reached")
        m, state, jobs = self.manager, self.state_hash(), []
        self.round += 1
        for q in questions:
            rec = q["record"]
            allowed = (["KEEP", "DISCARD"] + (["SPLIT"] if split_available(m, q["cid"]) else []) if q["kind"] == "single"
                       else ["MERGE", "NOT_MERGE", "DISCARD"])
            if rec is None:
                label = dict(action="KEEP", provenance=q["provenance"], reason="", expert_step=None, expert_raw=None)
            else:
                if members_key(m.assigns, rec["src"]) != rec["keys"][0]:
                    raise RuntimeError(f"Replay left the expert path before expert step {rec['step']}")
                label = dict(action=rec["action"], provenance="expert_recorded", reason=rec["reason"],
                             expert_step=rec["step"], expert_raw=rec["raw"])
            if label["action"] not in allowed:
                label["provenance"] = f"unlabeled_not_offered:{label['provenance']}"
            label["labeled"] = label["action"] in allowed
            self.calls += 1
            folder = self.out / f"step_{self.calls:05d}"
            folder.mkdir()
            write_json(folder / "question.json", dict(kind=q["kind"], cid=q["cid"], target=q["target"], allowed=allowed,
                                                      round=self.round, state_sha256=state))
            started, at = monotonic(), time()
            content = self.decider.prepare(m, q["kind"], q["cid"], q["target"], allowed, folder=folder,
                                           sampling_rate=self.fs)  # rendering is serial
            jobs.append((q, folder, allowed, label, content, dict(prepared_at=at, prepare_seconds=monotonic() - started,
                                                                  round=self.round)))
        if self.prefetch:
            self.deferred += jobs
        else:
            self.answer_jobs(jobs)

    def answer_jobs(self, jobs):
        def answer(job):
            q, folder, allowed, label, content, timing = job
            started, at = monotonic(), time()
            d = self.decider.answer(content, allowed, folder=folder)
            expert = label["action"] if label["labeled"] else None
            gate = d.get("gate")  # upstream skill gates may replace the model's action; agent_action is the final one
            write_json(folder / "simulation.json", dict(
                label, version=REPLAY_VERSION, agent_action=d["action"], expert_action=expert,
                model_action=gate["model_action"] if gate else d["action"], gate=gate,
                skill_version=self.decider.skill["version"], skill_sha256=self.decider.skill_sha256,
                committed_action=label["action"] if q["record"] else "KEEP (no state change)",
                match=None if expert is None else d["action"] == expert,
                timing=dict({k: v for k, v in timing.items() if k != "round"}, answer_started_at=at, answer_seconds=monotonic() - started, answered_at=time())))
            return d

        if self.parallel > 1 and len(jobs) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=self.parallel) as pool:
                answers = list(pool.map(answer, jobs))
        else:
            answers = [answer(job) for job in jobs]
        for (q, _, allowed, label, _, timing), d in zip(jobs, answers):
            self.log("decision", kind=q["kind"], cid=q["cid"], target=q["target"], allowed=allowed, round=timing["round"],
                     agent_action=d["action"], rationale=d["rationale"], expert_action=label["action"],
                     provenance=label["provenance"])

    def apply(self, rec):
        m = self.manager
        if rec["action"] == "SPLIT":
            result = list(map(int, m.split_last_merge(rec["src"])))
        elif rec["action"] == "DISCARD":
            m.discard_cluster(rec["src"])
            result = []
        else:
            result = [int(m.merge_clusters([rec["src"], rec["tgt"]], target_id=rec["tgt"]))]
        m.history.clear()
        m.history_index = -1
        self.log("executed", action=rec["action"], cid=rec["src"], target=rec["tgt"], expert_step=rec["step"],
                 result=result, state_sha256=self.state_hash())
        return result

    def run(self):
        self.status, started = "running", monotonic()
        try:
            pending = self.keeps(self.manager.get_active_clusters(), -1)
            for index, rec in enumerate(self.oracle.sequence):
                q = dict(kind="pair" if rec["action"] == "MERGE" else "single", cid=int(rec["src"]),
                         target=None if rec["tgt"] is None else int(rec["tgt"]), record=rec)
                self.ask_round(pending + [q])
                pending = self.keeps(self.apply(rec), index)
                if self.after_round is not None:
                    self.after_round(self)
            if pending:
                self.ask_round(pending)
                if self.after_round is not None:
                    self.after_round(self)
            if self.deferred:
                jobs, self.deferred = self.deferred, []
                self.log("prefetch_answering", questions=len(jobs), parallel=self.parallel)
                self.answer_jobs(jobs)
            if not same_partition(self.manager.assigns, self.oracle.final):
                raise RuntimeError("Strict replay did not reproduce the expert end state")
            self.status = "completed"
        except LimitStop as exc:
            if self.deferred:  # questions rendered before the limit are still answered
                jobs, self.deferred = self.deferred, []
                self.answer_jobs(jobs)
            self.status = "stopped_limit"
            self.log("stopped_limit", reason=str(exc))
        except Exception as exc:
            self.status = "stopped"
            self.log("stopped", error=repr(exc))
            raise
        finally:
            self.log("run_end", status=self.status, rounds=self.round, seconds=monotonic() - started)
