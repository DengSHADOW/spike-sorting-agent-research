"""Local-only review API and static server; no model clients or curation executor.

Serves an explicit imported bundle, never the project/data directory. SQLite
stores annotations, revisions and prefix-searchable reasons on the workspace.
"""
from __future__ import annotations

import argparse
import csv
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import threading
import unicodedata
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parent))
from dataset_inputs import DatasetManager, ALLOWED, MAX_UPLOAD
from jacob_reasons import DEFAULT_SEED, load_seed, VERSION as REASON_SEED_VERSION

ROOT = Path(__file__).resolve().parents[3]
STATIC = ROOT / "mockups/curation-review/dist"
STORE = ROOT / "output/curation_review_local"
SCHEMA = "spikeagent-human-review-v3"
PHASE_ACTIONS = {"phase1": ["KEEP", "SPLIT", "DISCARD"], "phase2": ["MERGE", "NOT_MERGE", "DISCARD"]}
TABS = {"detail", "overview", "gallery", "timing", "amplitude", "stability", "tree", "pair", "quality"}
MISSING_EVIDENCE = {"waveform", "timing", "amplitude", "spatial", "split_tree", "recording_context", "other"}


def now():
    return datetime.now(timezone.utc).isoformat()


def clean_reason(text):
    return " ".join(text.split())


def reason_key(text):
    return unicodedata.normalize("NFKC", clean_reason(text)).casefold()


def string(value, limit=10000):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("Invalid or overlong text")
    return value.strip()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def allowed(case):
    source = next(u for u in case["units"] if u["id"] == case["source"])
    return ["KEEP"] + (["SPLIT"] if source["split"] else []) + ["DISCARD"] + (["MERGE"] if len(case["units"]) > 1 else []) + (["NOT_MERGE"] if case["pair"] else []) + ["DEFER"]


def build_review(case, draft, context):
    """Rebuild canonical provenance on the server; do not trust client metadata."""
    if not isinstance(draft, dict) or not isinstance(context, dict):
        raise ValueError("Expected draft and context objects")
    agree, action = draft.get("agree"), draft.get("action")
    manual=case.get("review_mode")=="manual"
    if (agree is not None if manual else type(agree) is not bool) or action not in allowed(case):
        raise ValueError("Choose an explicit judgment and an available action")
    types = draft.get("disagreement_types", [])
    if not isinstance(types, list) or any(t not in ("action", "target", "rationale", "evidence") for t in types):
        raise ValueError("Invalid disagreement categories")
    units = {u["id"]: u for u in case["units"]}
    source = units[case["source"]]["cluster_id"]
    ai_pair = [units[i]["cluster_id"] for i in case["pair"]] if case["pair"] else None
    target_id = draft.get("target")
    if action == "MERGE" and (not isinstance(target_id, str) or target_id not in units or target_id == case["source"]):
        raise ValueError("Merge target must be a different active cluster in this recording/state")
    target = units[target_id]["cluster_id"] if action == "MERGE" else None
    reason = clean_reason(string(draft.get("reason", ""), 2000))
    note, positive = string(draft.get("note", "")), string(draft.get("positive", ""))
    confidence = draft.get("confidence")
    sufficiency = draft.get("sufficiency", "unspecified")
    missing = draft.get("missing", [])
    missing_note = string(draft.get("missing_note", ""), 2000)
    if confidence is not None and (not isinstance(confidence,str) or confidence not in ("high","medium","low")):
        raise ValueError("Invalid judgment confidence")
    if not isinstance(sufficiency,str) or sufficiency not in ("unspecified","sufficient","insufficient"):
        raise ValueError("Invalid evidence sufficiency")
    if not isinstance(missing,list) or any(not isinstance(item,str) or item not in MISSING_EVIDENCE for item in missing):
        raise ValueError("Invalid missing evidence categories")
    missing = list(dict.fromkeys(missing))
    if sufficiency!="insufficient" and (missing or missing_note):
        raise ValueError("Mark evidence as insufficient when requesting additional evidence")
    if sufficiency=="insufficient" and not missing and not missing_note and not note:
        raise ValueError("Choose missing evidence or explain what is needed")
    if "other" in missing and not missing_note and not note:
        raise ValueError("Explain the other missing evidence")
    differences = set(types)
    if not manual and action != case["ai"]:
        differences.add("action")
    if action == "MERGE" and case["ai"] == "MERGE" and target != ai_pair[1]:
        differences.add("target")
    if agree and differences:
        raise ValueError("Agreement must preserve the action, merge target and explanation")
    if ((not agree and not manual) or (manual and action!="KEEP")) and not reason and not note and not (action=="DEFER" and sufficiency=="insufficient" and (missing or missing_note)):
        raise ValueError("Add a reason or note for disagreement")
    if not manual and not agree and not differences:
        raise ValueError("Mark rationale/evidence when the action and target are unchanged")
    if manual and types:
        raise ValueError("Manual reviews have no AI disagreement categories")
    model_ids = [] if manual else case["pair"] or [case["source"]]
    viewed = context.get("viewed_cluster_ids", [case["source"]])
    visible = context.get("visible_cluster_ids", [case["source"]])
    tabs = context.get("viewed_tabs", ["detail"])
    if any(not isinstance(v, list) for v in (viewed, visible, tabs)):
        raise ValueError("Invalid evidence exposure")
    if any(i not in units for i in viewed + visible) or any(t not in TABS for t in tabs):
        raise ValueError("Unknown evidence object/tab")
    if type(context.get("rationale_viewed", False)) is not bool:
        raise ValueError("Invalid rationale exposure")
    comparisons = context.get("pair_comparisons", [])
    if (not isinstance(comparisons,list) or len(comparisons)>len(units)
            or any(not isinstance(pair,list) or len(pair)!=2 or pair[0]!=case["source"]
                   or any(not isinstance(i,str) or i not in units for i in pair) or pair[0]==pair[1] for pair in comparisons)):
        raise ValueError("Invalid compared cluster pair")
    result = {k: case[k] for k in ("dataset_id", "run_id", "step_id", "phase_id", "state_id", "state_sha256",
        "request_sha256", "image_sha256", "mat_sha256", "checkpoint_sha256", "request_hash_status", "agent_id", "agent_kind")}
    result.update(schema=SCHEMA, synthetic=False, review_mode="manual" if manual else "historical_model_review", case_id=case["id"],
        recording_id=case["dataset_id"], phase=result.pop("phase_id"), current_cluster=source, source_cluster=source,
        channel=case["channel"], ai_action=case["ai"], ai_source_cluster=None if manual else source,
        ai_merge_target=ai_pair[1] if ai_pair else None, ai_pair=ai_pair, ai_rationale=case["rationale"],
        expert_agree=agree, disagreement_types=[] if agree else sorted(differences),
        expert_action=None if action == "DEFER" else action, expert_merge_target=target,
        expert_pair=[source, target] if target is not None else None,
        expert_reason="" if agree else reason, expert_note="" if agree else note,
        expert_positive_comment=positive if agree or manual else "", review_status="deferred" if action == "DEFER" else "reviewed",
        expert_confidence=confidence, evidence_sufficiency=sufficiency, missing_evidence=missing,
        missing_evidence_note=missing_note,
        action=action, execution_status="not_executed", phase_contract_compatible=action in PHASE_ACTIONS[case["phase_id"]],
        spike_counts={str(u["cluster_id"]): u["spikes"] for u in case["units"]},
        displayed_trace_count=case["displayed_trace_count"], reviewer_trace_limit=24, waveform_units="source units",
        evidence_id=case["evidence_id"], visible_cluster_set=[units[i]["cluster_id"] for i in dict.fromkeys(visible)],
        viewed_cluster_ids=[units[i]["cluster_id"] for i in dict.fromkeys(viewed)],
        model_visible_cluster_set=[units[i]["cluster_id"] for i in model_ids], viewed_tabs=list(dict.fromkeys(tabs)),
        rationale_viewed=context.get("rationale_viewed", False),
        pair_comparisons=[[units[i]["cluster_id"] for i in pair] for pair in comparisons],
        additional_evidence_viewed=manual or any(t in ("overview", "gallery", "stability", "pair", "quality") for t in tabs) or any(i not in model_ids for i in viewed),
        timestamp=now(), annotator=string(context.get("annotator", ""), 100) or None)
    return result


class Conflict(Exception):
    pass


class ReviewStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS reviews (
                reviewer TEXT NOT NULL, case_id TEXT NOT NULL, revision INTEGER NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(reviewer,case_id));
              CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, reviewer TEXT NOT NULL,
                case_id TEXT NOT NULL, event TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS reasons (
                key TEXT PRIMARY KEY, text TEXT NOT NULL, provenance TEXT NOT NULL,
                uses INTEGER NOT NULL, created_at TEXT NOT NULL, last_used_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS profiles (
                reviewer TEXT PRIMARY KEY, annotator TEXT NOT NULL, updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS reason_sources (
                key TEXT NOT NULL, source_id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(key,source_id));
              CREATE TABLE IF NOT EXISTS library_meta (name TEXT PRIMARY KEY,payload TEXT NOT NULL);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def reviews(self, reviewer):
        with self.connect() as db:
            rows = db.execute("SELECT case_id, revision, payload FROM reviews WHERE reviewer=?", (reviewer,)).fetchall()
        return {r["case_id"]: dict(review=json.loads(r["payload"]), revision=r["revision"]) for r in rows if json.loads(r["payload"]) is not None}

    def reasons(self, prefix="", limit=20):
        key = reason_key(prefix)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM reasons WHERE substr(key,1,?)=? ORDER BY uses DESC,last_used_at DESC,key LIMIT ?", (len(key), key, limit)).fetchall()
            sources=self._sources(db)
        return [dict(r,sources=sources.get(r["key"],[])) for r in rows]

    def _sources(self, db):
        result={}
        for row in db.execute("SELECT key,payload FROM reason_sources ORDER BY source_id"):
            result.setdefault(row["key"],[]).append(json.loads(row["payload"]))
        return result

    def _source(self, db, key, source):
        payload=json.dumps(source,ensure_ascii=False,sort_keys=True)
        sid=hashlib.sha256(payload.encode()).hexdigest()
        db.execute("INSERT OR IGNORE INTO reason_sources VALUES(?,?,?)",(key,sid,payload))

    def seed_reasons(self, path):
        seed=load_seed(path)
        timestamp=now();added=0
        with self.connect() as db:
            for entry in seed["reasons"]:
                text=clean_reason(entry["text"]);key=reason_key(text)
                added+=db.execute("INSERT OR IGNORE INTO reasons VALUES(?,?,?,?,?,?)",(key,text,"jacob-original-workbook",0,timestamp,timestamp)).rowcount
                self._source(db,key,dict(kind="jacob",version=REASON_SEED_VERSION,references=entry["sources"]))
            meta={k:v for k,v in seed.items() if k!="reasons"}
            meta["unique_reasons"]=len(seed["reasons"])
            db.execute("INSERT OR REPLACE INTO library_meta VALUES(?,?)",("jacob",json.dumps(meta,ensure_ascii=False)))
        return dict(added=added,unique_reasons=len(seed["reasons"]))

    def reason_status(self):
        with self.connect() as db:
            row=db.execute("SELECT payload FROM library_meta WHERE name='jacob'").fetchone()
            total=db.execute("SELECT count(*) FROM reasons").fetchone()[0]
        return dict(total=total,jacob=json.loads(row["payload"]) if row else None)

    def import_reasons(self, texts, provenance="browser-review-history"):
        if not isinstance(texts, list) or len(texts) > 5000:
            raise ValueError("Import up to 5000 reasons at a time")
        normalized = [clean_reason(string(t, 2000)) for t in texts]
        timestamp = now()
        added=0
        with self.connect() as db:
            for text in normalized:
                if text:
                    added+=db.execute("INSERT OR IGNORE INTO reasons VALUES(?,?,?,?,?,?)", (reason_key(text), text, provenance, 0, timestamp, timestamp)).rowcount
                    self._source(db,reason_key(text),dict(kind="user_imported",provenance=provenance))
        return dict(added=added,duplicates=sum(bool(t) for t in normalized)-added)

    def profile(self, reviewer, annotator=None):
        with self.connect() as db:
            if annotator is not None:
                name=string(annotator,100)
                db.execute("INSERT OR REPLACE INTO profiles VALUES(?,?,?)",(reviewer,name,now()))
            row=db.execute("SELECT annotator,updated_at FROM profiles WHERE reviewer=?",(reviewer,)).fetchone()
        return dict(reviewer_id=reviewer,annotator=row["annotator"] if row else "",updated_at=row["updated_at"] if row else None)

    def import_backup(self, reviewer, payload, cases, preview=True):
        if not isinstance(payload,dict) or payload.get("schema")!=SCHEMA or payload.get("synthetic") is not False:
            raise ValueError("Choose a real-data v3 feedback export")
        records=payload.get("reviews")
        if not isinstance(records,list) or len(records)>5000:
            raise ValueError("Invalid feedback backup (maximum 5000 reviews)")
        prepared=[];seen=set()
        for r in records:
            if not isinstance(r,dict) or not isinstance(r.get("case_id"),str) or r["case_id"] in seen:
                raise ValueError("Invalid or duplicate case in backup")
            if r.get("schema")!=SCHEMA or r.get("synthetic") is not False:
                raise ValueError("Backup contains an unsupported or synthetic review")
            seen.add(r["case_id"])
            c=cases.get(r["case_id"])
            if c is None:
                raise ValueError("Import the dataset/view for this case first: "+r["case_id"])
            for field in ("dataset_id","run_id","step_id","state_sha256","mat_sha256","request_sha256","image_sha256","ai_rationale","ai_action"):
                canonical=c.get({"ai_rationale":"rationale","ai_action":"ai"}.get(field,field))
                if r.get(field)!=canonical:
                    raise ValueError("Backup provenance mismatch: "+field)
            unit_map={u["cluster_id"]:u["id"] for u in c["units"]}
            source=next(u["cluster_id"] for u in c["units"] if u["id"]==c["source"])
            ai_pair=[next(u["cluster_id"] for u in c["units"] if u["id"]==uid) for uid in c["pair"]] if c["pair"] else None
            if r.get("source_cluster")!=source or r.get("phase")!=c["phase_id"] or r.get("ai_pair")!=ai_pair:
                raise ValueError("Backup source, phase or AI pair differs from the case")
            for field in ("viewed_cluster_ids","visible_cluster_set"):
                values=r.get(field,[])
                if not isinstance(values,list) or any(type(i) is not int or i not in unit_map for i in values):
                    raise ValueError("Invalid backup evidence exposure")
            d=dict(agree=r.get("expert_agree"),action=r.get("action"),target=unit_map.get(r.get("expert_merge_target"),""),
                reason=r.get("expert_reason",""),note=r.get("expert_note",""),positive=r.get("expert_positive_comment",""),
                disagreement_types=r.get("disagreement_types",[]),confidence=r.get("expert_confidence"),
                sufficiency=r.get("evidence_sufficiency","unspecified"),missing=r.get("missing_evidence",[]),missing_note=r.get("missing_evidence_note",""))
            context=dict(annotator=r.get("annotator") or "",viewed_tabs=r.get("viewed_tabs",["detail"]),
                viewed_cluster_ids=[unit_map[i] for i in r.get("viewed_cluster_ids",[]) if i in unit_map],
                visible_cluster_ids=[unit_map[i] for i in r.get("visible_cluster_set",[]) if i in unit_map],rationale_viewed=r.get("rationale_viewed",False))
            pairs=r.get("pair_comparisons",[])
            if (not isinstance(pairs,list) or any(not isinstance(pair,list) or len(pair)!=2 or any(type(i) is not int or i not in unit_map for i in pair) for pair in pairs)):
                raise ValueError("Invalid backup pair comparison exposure")
            context["pair_comparisons"]=[[unit_map[i] for i in pair] for pair in pairs]
            rebuilt=build_review(c,d,context)
            rebuilt["reviewer_id"]=reviewer
            rebuilt["imported_from"]=dict(reviewer_id=payload.get("reviewer_id"),timestamp=r.get("timestamp"))
            prepared.append(rebuilt)
        library=payload.get("reason_library",[])
        if not isinstance(library,list) or len(library)>5000:raise ValueError("Invalid backup reason library")
        reason_texts=[clean_reason(string(r.get("text") if isinstance(r,dict) else r,2000)) for r in library]
        reasons_added=0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing={row[0] for row in db.execute("SELECT case_id FROM reviews WHERE reviewer=?",(reviewer,))}
            pending=[r for r in prepared if r["case_id"] not in existing]
            if not preview:
                sources=self._sources(db)
                batch=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
                for i,r in enumerate(pending):
                    r["reason_sources"]=sources.get(reason_key(r["expert_reason"]),[]) or ([dict(kind="user_imported",provenance="feedback-backup")] if r["expert_reason"] else [])
                    result=dict(review=r,revision=1,case_id=r["case_id"])
                    db.execute("INSERT INTO reviews VALUES(?,?,?,?)",(reviewer,r["case_id"],1,json.dumps(r,ensure_ascii=False)))
                    request_id="import-"+hashlib.sha256((reviewer+batch+str(i)).encode()).hexdigest()
                    db.execute("INSERT INTO events(request_id,reviewer,case_id,event,timestamp,payload) VALUES(?,?,?,?,?,?)",(request_id,reviewer,r["case_id"],"review_imported",now(),json.dumps(result,ensure_ascii=False)))
                    if r["expert_reason"]:
                        text=r["expert_reason"];timestamp=now()
                        db.execute("INSERT OR IGNORE INTO reasons VALUES(?,?,?,?,?,?)",(reason_key(text),text,"feedback-backup",0,timestamp,timestamp))
                        self._source(db,reason_key(text),dict(kind="user_imported",provenance="feedback-backup"))
                for text in reason_texts:
                    if text:
                        timestamp=now()
                        reasons_added+=db.execute("INSERT OR IGNORE INTO reasons VALUES(?,?,?,?,?,?)",(reason_key(text),text,"feedback-backup",0,timestamp,timestamp)).rowcount
                        self._source(db,reason_key(text),dict(kind="user_imported",provenance="feedback-backup"))
        return dict(preview=preview,valid=len(prepared),added=0 if preview else len(pending),available_to_import=len(pending),skipped_existing=len(prepared)-len(pending),reasons_added=reasons_added)

    def write(self, reviewer, case, body, undo=False):
        request_id = string(body.get("request_id"), 100)
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", request_id):
            raise ValueError("An idempotency request ID is required")
        expected = body.get("expected_revision")
        if type(expected) is not int or expected < 0:
            raise ValueError("An expected revision is required")
        review = None if undo else build_review(case, body.get("draft"), body.get("context", {}))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM events WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if previous["reviewer"] != reviewer or previous["case_id"] != case["id"] or previous["event"] != ("review_undone" if undo else "review_saved"):
                    raise Conflict("Request ID belongs to another operation")
                return json.loads(previous["payload"])
            current = db.execute("SELECT revision,payload FROM reviews WHERE reviewer=? AND case_id=?", (reviewer, case["id"])).fetchone()
            revision = current["revision"] if current else 0
            if revision != expected:
                raise Conflict("Review changed in another tab. Reload before editing it again.")
            if undo and (not current or json.loads(current["payload"]) is None):
                raise ValueError("No saved review to undo")
            if review:
                review["reviewer_id"] = reviewer
                review["reason_sources"] = self._sources(db).get(reason_key(review["expert_reason"]),[]) or ([dict(kind="reviewer",provenance="reviewer-entered-real-review")] if review["expert_reason"] else [])
            result = dict(review=review, revision=revision + 1, case_id=case["id"])
            db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?,?)", (reviewer, case["id"], revision + 1, json.dumps(review, ensure_ascii=False)))
            db.execute("INSERT INTO events(request_id,reviewer,case_id,event,timestamp,payload) VALUES(?,?,?,?,?,?)", (request_id, reviewer, case["id"], "review_undone" if undo else "review_saved", now(), json.dumps(result, ensure_ascii=False)))
            if review and review["expert_reason"]:
                text = review["expert_reason"]; timestamp = now()
                db.execute("""INSERT INTO reasons VALUES(?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET
                  uses=uses+1,last_used_at=excluded.last_used_at""", (reason_key(text), text, "reviewer-entered-real-review", 1, timestamp, timestamp))
                self._source(db,reason_key(text),dict(kind="reviewer",provenance="reviewer-entered-real-review"))
            return result

    def revisions(self, reviewer):
        with self.connect() as db:
            return {r["case_id"]: r["revision"] for r in db.execute("SELECT case_id,revision FROM reviews WHERE reviewer=?", (reviewer,))}

    def export(self, reviewer):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM events WHERE reviewer=? ORDER BY id", (reviewer,)).fetchall()
            reasons = [dict(r) for r in db.execute("SELECT * FROM reasons ORDER BY key")]
            sources=self._sources(db)
            for reason in reasons:reason["sources"]=sources.get(reason["key"],[])
        return dict(schema=SCHEMA, synthetic=False, exported_at=now(), execution_status="not_executed", reviewer_id=reviewer,
            reviews=[r["review"] for r in self.reviews(reviewer).values()],
            events=[dict(type=r["event"], request_id=r["request_id"], timestamp=r["timestamp"], **json.loads(r["payload"])) for r in rows],
            reason_library=reasons)


class ReviewApplication:
    def __init__(self, bundle_path, db_path, reason_seed=None):
        self.bundle_path = Path(bundle_path).resolve()
        self.datasets = DatasetManager(self.bundle_path,Path(db_path).resolve().parent/"manager")
        self.store = ReviewStore(db_path)
        if reason_seed is not None and Path(reason_seed).is_file():
            self.store.seed_reasons(reason_seed)
        self.analysis_service = None
        self.analysis_lock = threading.Lock()
        self.csrf = secrets.token_urlsafe(32)

    def analysis(self, case_id, cluster_id=None, target_id=None):
        with self.analysis_lock:
            if self.analysis_service is None:
                from review_analysis import AnalysisService
                self.analysis_service=AnalysisService(self.datasets)
        return self.analysis_service.get(case_id,cluster_id,target_id)

    @property
    def cases(self):
        return self.datasets.all_cases()

    @property
    def bundle(self):
        return self.datasets.get() or dict(dataset_id=None,n_spikes=0,cases=[],sampling_rate_hz=None,provenance={})

    def asset(self, name):
        path,expected=self.datasets.asset_path(name)
        if not path.resolve().is_relative_to(path.parent.parent) or sha256(path) != expected:
            raise ValueError("Image integrity check failed")
        return path.read_bytes()

    def evidence(self, case_id):
        case,bundle,root = self.datasets.find_case(case_id)
        row = bundle["evidence_files"][case["evidence_id"]]
        path = root / "evidence" / row["file"]
        if not path.resolve().is_relative_to(root) or sha256(path) != row["sha256"]:
            raise ValueError("Evidence integrity check failed")
        return path.read_bytes()


class Handler(BaseHTTPRequestHandler):
    server_version = "SpikeReview/1"

    @property
    def app(self):
        return self.server.app

    def reply(self, value, status=200, content_type="application/json; charset=utf-8"):
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(data)

    def check_host(self):
        host = urlsplit("http://" + self.headers.get("Host", "")).hostname
        if host not in ("localhost", "127.0.0.1", "::1"):
            raise PermissionError("Access through a localhost SSH/VS Code port forward")

    def reviewer(self):
        value = self.headers.get("X-Reviewer-ID", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", value):
            raise ValueError("Browser reviewer identity is missing")
        return value

    def do_GET(self):
        try:
            self.check_host()
            path = unquote(urlsplit(self.path).path)
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            if path == "/api/status":
                self.reply(dict(status="ready", csrf_token=self.app.csrf, synthetic=False,
                    dataset_id=self.app.bundle["dataset_id"], cases=len(self.app.cases), n_spikes=self.app.bundle["n_spikes"], model_calls_enabled=False,
                    bundles=self.app.datasets.summaries(),max_upload_bytes=MAX_UPLOAD))
            elif path == "/api/datasets":
                self.reply(dict(bundles=self.app.datasets.summaries(),inputs=self.app.datasets.inputs(),max_upload_bytes=MAX_UPLOAD))
            elif path == "/api/imports":
                self.reply(dict(jobs=self.app.datasets.list_jobs()))
            elif path == "/api/cases":
                bundle_id=query.get("bundle_id",[None])[0]
                bundle=self.app.datasets.get(bundle_id)
                if bundle is None:raise KeyError("No imported dataset")
                self.reply(dict(dataset_id=bundle["dataset_id"], n_spikes=bundle["n_spikes"],source_kind=bundle["source_kind"],run_status=bundle["run_status"],
                    sampling_rate_hz=bundle["sampling_rate_hz"], provenance=bundle["provenance"], cases=bundle["cases"]))
            elif path.startswith("/api/evidence/"):
                self.reply(self.app.evidence(path.split("/", 3)[3]))
            elif path.startswith("/api/analysis/"):
                cluster=query.get("cluster",[None])[0]
                target=query.get("target",[None])[0]
                self.reply(self.app.analysis(path.split("/",3)[3],cluster,target))
            elif path.startswith("/api/assets/"):
                self.reply(self.app.asset(path.split("/", 3)[3]), content_type="image/png")
            elif path == "/api/reasons":
                prefix = string(query.get("prefix", [""])[0], 2000)
                limit=int(query.get("limit",[20])[0])
                if not 1<=limit<=5000:raise ValueError("Reason result limit must be 1–5000")
                self.reply(dict(reasons=self.app.store.reasons(prefix,limit)))
            elif path == "/api/reasons/status":
                self.reply(self.app.store.reason_status())
            elif path == "/api/reasons/export":
                self.reply(dict(schema="spikeagent-reason-library-v1",exported_at=now(),reasons=self.app.store.export(self.reviewer())["reason_library"]))
            elif path == "/api/profile":
                self.reply(self.app.store.profile(self.reviewer()))
            elif path == "/api/reviews":
                reviewer = self.reviewer()
                self.reply(dict(reviews=self.app.store.reviews(reviewer), revisions=self.app.store.revisions(reviewer)))
            elif path == "/api/export":
                self.reply(self.app.store.export(self.reviewer()))
            elif path.startswith("/api/"):
                self.reply(dict(error="Unknown API route"), 404)
            else:
                name = "index.html" if path == "/" else path.lstrip("/")
                allowed_files = {"index.html": "text/html; charset=utf-8", "style.css": "text/css; charset=utf-8",
                    "app.js": "text/javascript; charset=utf-8", "analysis.js":"text/javascript; charset=utf-8", "inputs.js":"text/javascript; charset=utf-8", "review-core.js": "text/javascript; charset=utf-8"}
                if name not in allowed_files:
                    raise KeyError("Unknown static file")
                self.reply((STATIC / name).read_bytes(), content_type=allowed_files[name])
        except KeyError:
            self.reply(dict(error="Not found"), 404)
        except PermissionError as exc:
            self.reply(dict(error=str(exc)), 403)
        except ValueError as exc:
            self.reply(dict(error=str(exc)), 400)
        except (OSError,sqlite3.Error):
            self.reply(dict(error="Local storage is unavailable. Retry after checking the server."),503)

    def do_POST(self):
        try:
            self.check_host()
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host"):
                raise PermissionError("Cross-origin writes are not allowed")
            if self.headers.get("X-CSRF-Token") != self.app.csrf:
                raise PermissionError("Refresh the page to reconnect before saving")
            path = urlsplit(self.path).path
            if path=="/api/uploads":
                if self.headers.get("Content-Type","").split(";")[0]!="application/octet-stream":
                    raise ValueError("Upload MAT bytes as application/octet-stream")
                query=parse_qs(urlsplit(self.path).query)
                self.connection.settimeout(60)
                self.reply(self.app.datasets.receive_upload(self.rfile,query.get("dataset_id",[None])[0],query.get("filename",[None])[0],int(self.headers.get("Content-Length","0"))))
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("Send application/json")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8_000_000:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            if path == "/api/reasons/import":
                self.reply(dict(status="saved",**self.app.store.import_reasons(body.get("reasons"))))
            elif path=="/api/reasons/import-file":
                content=string(body.get("content"),4_000_000).lstrip("\ufeff")
                fmt=body.get("format")
                if fmt=="json":
                    values=json.loads(content)
                    if isinstance(values,dict):values=values.get("reasons",values.get("reason_library"))
                    if not isinstance(values,list):raise ValueError("JSON must contain a list of reasons")
                    texts=[r.get("text") if isinstance(r,dict) else r for r in values]
                elif fmt=="csv":
                    reader=csv.DictReader(io.StringIO(content))
                    column=next((k for k in ("text","reason") if k in (reader.fieldnames or [])),None)
                    if not column:raise ValueError("CSV needs a text or reason column")
                    texts=[r[column] for r in reader]
                elif fmt=="text":texts=content.splitlines()
                else:raise ValueError("Choose JSON, CSV or text reasons")
                self.reply(self.app.store.import_reasons(texts,"user-imported-reason-library"))
            elif path=="/api/profile":
                self.reply(self.app.store.profile(self.reviewer(),string(body.get("annotator"),100)))
            elif path=="/api/imports":
                self.reply(self.app.datasets.start_import(body),202)
            elif path=="/api/imports/cancel":
                self.reply(self.app.datasets.cancel(string(body.get("job_id"),100)))
            elif path=="/api/reviews/import":
                preview=body.get("preview",True)
                if type(preview) is not bool:raise ValueError("Invalid preview flag")
                self.reply(self.app.store.import_backup(self.reviewer(),body.get("payload"),self.app.cases,preview))
            elif path in ("/api/reviews", "/api/reviews/undo"):
                case = self.app.cases[body.get("case_id")]
                self.reply(self.app.store.write(self.reviewer(), case, body, undo=path.endswith("/undo")))
            else:
                raise KeyError("Unknown API route")
        except Conflict as exc:
            self.reply(dict(error=str(exc)), 409)
        except PermissionError as exc:
            self.reply(dict(error=str(exc)), 403)
        except KeyError:
            self.reply(dict(error="Unknown case/route"), 404)
        except (ValueError, TypeError) as exc:
            self.reply(dict(error=str(exc)), 400)
        except (OSError,sqlite3.Error):
            self.reply(dict(error="Local input/storage is unavailable. Check the selected file and retry."),503)

    def log_message(self, fmt, *args):
        # Request paths contain no review text; never log POST bodies.
        print(self.log_date_time_string(), fmt % args, flush=True)


class LocalReviewServer(ThreadingHTTPServer):
    def server_close(self):
        if hasattr(self,"app"):self.app.datasets.close()
        super().server_close()


def make_server(port=8765, bundle=STORE / "jacob_ch31_v1", db=STORE / "reviews.sqlite3", reason_seed=None):
    application = ReviewApplication(bundle, db, reason_seed)
    server = LocalReviewServer(("127.0.0.1", port), Handler)
    server.app = application
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bundle", type=Path, default=STORE / "jacob_ch31_v1")
    parser.add_argument("--db", type=Path, default=STORE / "reviews.sqlite3")
    parser.add_argument("--reason-seed", type=Path, default=DEFAULT_SEED)
    args = parser.parse_args()
    import signal
    def stop_server(signum,frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop_server)
    server = make_server(args.port, args.bundle, args.db, args.reason_seed)
    print(f"Review backend ready: http://127.0.0.1:{server.server_port}; {len(server.app.cases)} saved real-data cases; model calls disabled", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
