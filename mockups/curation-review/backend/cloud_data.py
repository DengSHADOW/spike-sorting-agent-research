"""Private immutable object cache and the six published approved dataset views.

Imports reuse published verified views, as the local catalog already does.
Cloud input uploads use resumable storage sessions, bounded exact identity
validation, and durable upload metadata. They never invoke experiments.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from flask import g, request
from google.cloud import storage
from dataset_inputs import DatasetManager, ALLOWED, ROOT, approved_rows, now


class PrivateData:
    def __init__(self,inventory,bucket=None,local_source=None):
        self.inventory=inventory
        self.files={r["path"]:r for r in inventory["files"]}
        self.bucket=bucket
        self.local_source=Path(local_source) if local_source else None
        self.lock=threading.RLock()
        self.verified=set()

    def ensure(self,relative):
        relative=Path(relative).as_posix()
        if relative not in self.files:raise KeyError("File is outside the published inventory")
        path=(ROOT/relative).resolve()
        if not path.is_relative_to(ROOT):raise ValueError("Invalid data path")
        with self.lock:
            if relative in self.verified and path.is_file():return path
            row=self.files[relative]
            path.parent.mkdir(parents=True,exist_ok=True)
            temp=path.with_name(path.name+"."+uuid.uuid4().hex+".tmp")
            try:
                if self.local_source:
                    import shutil
                    shutil.copyfile(self.local_source/relative,temp)
                else:
                    self.bucket.blob("snapshot-v1/data/"+relative).download_to_filename(str(temp),timeout=120)
                h=hashlib.sha256()
                with temp.open("rb") as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b""):h.update(chunk)
                if temp.stat().st_size!=row["bytes"] or h.hexdigest()!=row["sha256"]:
                    raise ValueError("Published file failed its integrity check")
                temp.chmod(0o600);os.replace(temp,path)
                self.verified.add(relative)
            finally:
                temp.unlink(missing_ok=True)
        return path

    def ensure_role(self,*roles,dataset=None):
        for relative,row in self.files.items():
            if row["role"] in roles and (dataset is None or "/"+dataset+"/" in "/"+relative):
                self.ensure(relative)


class CloudDatasets(DatasetManager):
    def __init__(self,data,db):
        self.data=data;self.db=db
        self.lock=threading.RLock();self.bundles={};self.paths={}
        self.data.ensure("output/real_split_manifest_20260903/manifest.json")
        self.data.ensure("configs/curation/protocol_v1.json")
        for item in data.inventory["bundles"]:
            path=self.data.ensure(item["path"]+"/dataset.json")
            self._load(item["id"],path.parent)

    def asset_path(self,name):
        path,expected=super().asset_path(name)
        self.data.ensure(path.relative_to(ROOT))
        return path,expected

    def inputs(self):
        return [dict(dataset_id=dataset,filename=Path(row["mat_path"]).name,mat_path=row["mat_path"],
            n_spikes=row["n_spikes"],bytes=row["file_size_bytes"],sha256=row["mat_sha256"],server_available=True,
            sources=["initial"]+(["saved"] if dataset in ALLOWED[:2] else [])) for dataset,row in approved_rows().items()]

    def start_import(self,body):
        dataset=body.get("dataset_id");source=body.get("source","initial")
        if dataset not in ALLOWED or source not in ("initial","saved"):raise ValueError("Choose an approved dataset and view")
        if source=="saved" and dataset not in ALLOWED[:2]:raise ValueError("No saved Qwen9B decisions exist for this dataset")
        upload=body.get("upload_id")
        if upload:
            if not isinstance(upload,str) or not re.fullmatch(r"[a-f0-9]{32}",upload):raise ValueError("Invalid upload identity")
            meta=self.db.collection("review_uploads").document(upload).get()
            value=meta.to_dict() if meta.exists else {}
            if value.get("reviewer")!=g.identity["uid"] or value.get("dataset_id")!=dataset or value.get("status")!="verified":
                raise ValueError("A verified upload owned by this account is required")
        for identity,bundle in self.bundles.items():
            if (bundle["dataset_id"],bundle["source_kind"])==(dataset,source):
                return dict(id=None,status="completed",bundle_id=identity,reused=True,progress=100)
        raise ValueError("This approved view has not been published. Prepare it on Runpod before publishing it to the cloud.")

    def list_jobs(self):
        return []  # Every currently supported source/view is already published.

    def cancel(self,identity):
        raise ValueError("No queued import exists; published views are already complete")

    def close(self):pass

    def begin_upload(self,body,origin):
        dataset=body.get("dataset_id");filename=body.get("filename");length=body.get("bytes")
        if dataset not in ALLOWED:raise ValueError("Choose an approved dataset")
        if not isinstance(filename,str) or not re.fullmatch(r"[^/\\\x00-\x1f]{1,200}\.mat",filename,re.I):
            raise ValueError("Choose a MAT filename without directory components")
        row=approved_rows()[dataset]
        if type(length) is not int or length!=row["file_size_bytes"]:raise ValueError("File size does not match this approved dataset")
        if not self.data.bucket:raise ValueError("Cloud upload storage is unavailable")
        identity=uuid.uuid4().hex
        blob=self.data.bucket.blob("uploads/"+identity+".mat")
        url=blob.create_resumable_upload_session(content_type="application/octet-stream",size=length,
            origin=origin,if_generation_match=0,timeout=30)
        self.db.collection("review_uploads").document(identity).set(dict(
            reviewer=g.identity["uid"],dataset_id=dataset,filename=filename,bytes=length,
            created_at=now(),expires_at=time.time()+3600,status="pending"))
        return dict(id=identity,url=url,bytes=length)

    def finish_upload(self,body):
        identity=body.get("id")
        if not isinstance(identity,str) or not re.fullmatch(r"[a-f0-9]{32}",identity):raise ValueError("Invalid upload identity")
        ref=self.db.collection("review_uploads").document(identity)
        doc=ref.get();meta=doc.to_dict() if doc.exists else {}
        if meta.get("reviewer")!=g.identity["uid"]:raise PermissionError("Upload belongs to a different account")
        if meta.get("status")=="verified":return dict(meta,id=identity,reused=True)
        if meta.get("status")!="pending" or meta["expires_at"]<time.time():raise ValueError("Upload expired; start a new upload")
        row=approved_rows()[meta["dataset_id"]]
        blob=self.data.bucket.blob("uploads/"+identity+".mat")
        blob.reload(timeout=20)
        if blob.size!=row["file_size_bytes"]:raise ValueError("Uploaded byte count does not match this dataset")
        h=hashlib.sha256()
        with blob.open("rb",chunk_size=1024*1024) as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b""):h.update(chunk)
        if h.hexdigest()!=row["mat_sha256"]:
            ref.update(dict(status="rejected"))
            blob.delete(if_generation_match=blob.generation)
            raise ValueError("MAT SHA256 does not match the selected dataset")
        meta.update(status="verified",sha256=h.hexdigest(),verified_at=now())
        ref.set(meta)
        return dict(meta,id=identity,reused=False)
