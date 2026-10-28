"""Durable cloud review store with the local API's validation and transactions.

Firestore stores JSON payload strings to preserve nested pair arrays exactly.
Every save/undo commits the review, immutable event and reason update together.
No client-side document access or browser-provided identity is required.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import threading
import time

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from server import ReviewStore, Conflict, SCHEMA, build_review, clean_reason, now, reason_key, string
from jacob_reasons import load_seed, VERSION


def key(value):
    return hashlib.sha256(value.encode()).hexdigest()


def encode(value):
    data=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(",",":"))
    if len(data.encode())>900_000:
        raise ValueError("Record exceeds the cloud storage limit")
    return data


def decode(snapshot, default=None):
    return json.loads(snapshot.to_dict()["payload"]) if snapshot.exists else default


def add_source(row, source):
    if source not in row["sources"]:
        row["sources"].append(source)


def reason_row(text, provenance, source=None):
    timestamp=now()
    row=dict(key=reason_key(text),text=text,provenance=provenance,uses=0,
             created_at=timestamp,last_used_at=timestamp,sources=[])
    if source:add_source(row,source)
    return row


class FirestoreReviewStore:
    def __init__(self, client, seed=None):
        self.db=client
        self.cache=None
        self.cache_at=0
        self.lock=threading.RLock()
        if seed:self.seed_reasons(seed)

    def collection(self,name):
        return self.db.collection("review_"+name)

    def ref(self,name,identity):
        return self.collection(name).document(key(identity))

    def _rows(self,collection,reviewer):
        return list(self.collection(collection).where(filter=FieldFilter("reviewer","==",reviewer)).stream())

    def invalidate(self):
        with self.lock:self.cache=None

    def all_reasons(self):
        with self.lock:
            if self.cache is None or time.monotonic()-self.cache_at>15:
                self.cache=[decode(row) for row in self.collection("reasons").stream()]
                self.cache_at=time.monotonic()
            return copy.deepcopy(self.cache)

    def reviews(self,reviewer):
        result={}
        for row in self._rows("reviews",reviewer):
            entry=row.to_dict();review=decode(row)
            if review is not None:result[entry["case_id"]]=dict(review=review,revision=entry["revision"])
        return result

    def revisions(self,reviewer):
        return {row.to_dict()["case_id"]:row.to_dict()["revision"] for row in self._rows("reviews",reviewer)}

    def reasons(self,prefix="",limit=20):
        prefix=reason_key(prefix)
        rows=[r for r in self.all_reasons() if r["key"].startswith(prefix)]
        rows.sort(key=lambda r:(-r["uses"],r["key"]))
        # Stable tie-breaking agrees with the local order: newest usage, then key.
        rows.sort(key=lambda r:r["last_used_at"],reverse=True)
        rows.sort(key=lambda r:r["uses"],reverse=True)
        return rows[:limit]

    def reason_status(self):
        meta=self.collection("meta").document("jacob").get()
        return dict(total=len(self.all_reasons()),jacob=decode(meta))

    def seed_reasons(self,path):
        seed=load_seed(path)
        marker=self.collection("meta").document("jacob")
        if marker.get().exists:return
        @firestore.transactional
        def apply(tx):
            if marker.get(transaction=tx).exists:return
            refs=[self.ref("reasons",reason_key(r["text"])) for r in seed["reasons"]]
            existing={r.id:decode(r) for r in self.db.get_all(refs,transaction=tx)}
            for entry,ref in zip(seed["reasons"],refs):
                row=existing.get(ref.id) or reason_row(entry["text"],"jacob-original-workbook")
                add_source(row,dict(kind="jacob",version=VERSION,references=entry["sources"]))
                tx.set(ref,dict(payload=encode(row)))
            meta={k:v for k,v in seed.items() if k!="reasons"}
            meta["unique_reasons"]=len(seed["reasons"])
            tx.set(marker,dict(payload=encode(meta)))
        apply(self.db.transaction())
        self.invalidate()

    def import_reasons(self,texts,provenance="browser-review-history"):
        if not isinstance(texts,list) or len(texts)>5000:raise ValueError("Import up to 5000 reasons at a time")
        normalized=[clean_reason(string(t,2000)) for t in texts]
        unique={reason_key(t):t for t in normalized if t}
        added=0
        # Each chunk is idempotent. The local endpoint also promises deduplication,
        # not atomicity with other simultaneous reason-library imports.
        entries=list(unique.items())
        for offset in range(0,len(entries),400):
            group=entries[offset:offset+400]
            @firestore.transactional
            def apply(tx):
                refs=[self.ref("reasons",k) for k,t in group]
                old={r.id:decode(r) for r in self.db.get_all(refs,transaction=tx)}
                count=0
                for (k,text),ref in zip(group,refs):
                    previous=old.get(ref.id)
                    row=previous or reason_row(text,provenance)
                    add_source(row,dict(kind="user_imported",provenance=provenance))
                    if row!=previous:tx.set(ref,dict(payload=encode(row)))
                    count+=previous is None
                return count
            added+=apply(self.db.transaction())
        self.invalidate()
        return dict(added=added,duplicates=sum(bool(t) for t in normalized)-added)

    def profile(self,reviewer,annotator=None):
        ref=self.ref("profiles",reviewer)
        if annotator is not None:
            result=dict(reviewer_id=reviewer,annotator=string(annotator,100),updated_at=now())
            ref.set(dict(payload=encode(result)))
            return result
        return decode(ref.get(),dict(reviewer_id=reviewer,annotator="",updated_at=None))

    def write(self,reviewer,case,body,undo=False):
        request_id=string(body.get("request_id"),100)
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}",request_id):raise ValueError("An idempotency request ID is required")
        expected=body.get("expected_revision")
        if type(expected) is not int or expected<0:raise ValueError("An expected revision is required")
        review=None if undo else build_review(case,body.get("draft"),body.get("context",{}))
        event="review_undone" if undo else "review_saved"
        event_ref=self.ref("events",request_id)
        review_ref=self.ref("reviews",reviewer+"\0"+case["id"])
        text=review["expert_reason"] if review else ""
        reason_ref=self.ref("reasons",reason_key(text)) if text else None
        @firestore.transactional
        def apply(tx):
            previous=event_ref.get(transaction=tx)
            if previous.exists:
                entry=previous.to_dict()
                if (entry["reviewer"],entry["case_id"],entry["event"])!=(reviewer,case["id"],event):
                    raise Conflict("Request ID belongs to another operation")
                return decode(previous)
            current=review_ref.get(transaction=tx)
            revision=current.to_dict()["revision"] if current.exists else 0
            if revision!=expected:raise Conflict("Review changed in another tab. Reload before editing it again.")
            if undo and (not current.exists or decode(current) is None):raise ValueError("No saved review to undo")
            reason=decode(reason_ref.get(transaction=tx)) if reason_ref else None
            saved=copy.deepcopy(review)
            if saved is not None:
                saved["reviewer_id"]=reviewer
                saved["reason_sources"]=reason["sources"] if reason else ([dict(kind="reviewer",provenance="reviewer-entered-real-review")] if text else [])
            result=dict(review=saved,revision=revision+1,case_id=case["id"])
            tx.set(review_ref,dict(reviewer=reviewer,case_id=case["id"],revision=revision+1,payload=encode(saved)))
            tx.set(event_ref,dict(reviewer=reviewer,case_id=case["id"],event=event,request_id=request_id,timestamp=now(),payload=encode(result)))
            if text:
                row=reason or reason_row(text,"reviewer-entered-real-review")
                row["uses"]+=1;row["last_used_at"]=now()
                add_source(row,dict(kind="reviewer",provenance="reviewer-entered-real-review"))
                tx.set(reason_ref,dict(payload=encode(row)))
            return result
        result=apply(self.db.transaction())
        self.invalidate()
        return result

    def export(self,reviewer):
        events=[]
        for row in self._rows("events",reviewer):
            entry=row.to_dict()
            events.append(dict(type=entry["event"],request_id=entry["request_id"],timestamp=entry["timestamp"],**decode(row)))
        events.sort(key=lambda e:e["timestamp"])
        return dict(schema=SCHEMA,synthetic=False,exported_at=now(),execution_status="not_executed",reviewer_id=reviewer,
                    reviews=[v["review"] for v in self.reviews(reviewer).values()],events=events,
                    reason_library=sorted(self.all_reasons(),key=lambda r:r["key"]))

    def import_backup(self,reviewer,payload,cases,preview=True):
        # Reuse the existing exhaustive provenance/field validator in an isolated,
        # disposable SQLite database. Nothing writes to the local user's store.
        with tempfile.TemporaryDirectory() as directory:
            local=ReviewStore(Path(directory)/"validation.sqlite3")
            local.import_backup(reviewer,payload,cases,preview=False)
            validated=local.export(reviewer)
        records=validated["reviews"]
        library={r["key"]:r for r in validated["reason_library"]}
        refs={r["case_id"]:self.ref("reviews",reviewer+"\0"+r["case_id"]) for r in records}
        reason_refs={k:self.ref("reasons",k) for k in library}
        batch_id=key(encode(payload))
        @firestore.transactional
        def apply(tx):
            existing={r.id:r for r in self.db.get_all(list(refs.values()),transaction=tx)} if refs else {}
            old_reasons={r.id:decode(r) for r in self.db.get_all(list(reason_refs.values()),transaction=tx)} if reason_refs else {}
            pending=[r for r in records if not existing[refs[r["case_id"]].id].exists]
            updates={}
            for k,row in library.items():
                if not old_reasons.get(reason_refs[k].id):
                    updates[k]=reason_row(row["text"],"feedback-backup",dict(kind="user_imported",provenance="feedback-backup"))
            for r in pending:
                k=reason_key(r["expert_reason"])
                old=old_reasons.get(reason_refs[k].id) if k else None
                r["reason_sources"]=copy.deepcopy(old["sources"]) if old else ([dict(kind="user_imported",provenance="feedback-backup")] if k else [])
                if k:
                    row=copy.deepcopy(old or updates[k])
                    add_source(row,dict(kind="user_imported",provenance="feedback-backup"))
                    updates[k]=row
            if 2*len(pending)+len(updates)>500:
                raise ValueError("Backup exceeds one atomic cloud transaction. Import its reason library separately, then restore reviews.")
            if not preview:
                for r in pending:
                    cid=r["case_id"];result=dict(review=r,revision=1,case_id=cid)
                    request_id="import-"+key(reviewer+batch_id+cid)
                    tx.set(refs[cid],dict(reviewer=reviewer,case_id=cid,revision=1,payload=encode(r)))
                    tx.set(self.ref("events",request_id),dict(reviewer=reviewer,case_id=cid,event="review_imported",request_id=request_id,timestamp=now(),payload=encode(result)))
                for k,row in updates.items():tx.set(reason_refs[k],dict(payload=encode(row)))
            return dict(preview=preview,valid=len(records),added=0 if preview else len(pending),
                        available_to_import=len(pending),skipped_existing=len(records)-len(pending),
                        reasons_added=0 if preview else sum(old_reasons.get(reason_refs[k].id) is None for k in updates))
        result=apply(self.db.transaction())
        if not preview:self.invalidate()
        return result
