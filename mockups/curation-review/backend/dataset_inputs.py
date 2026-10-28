"""Persistent local dataset catalog, bounded MAT uploads and offline import jobs."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(os.environ.get("SPIKE_REVIEW_DATA_ROOT", Path(__file__).resolve().parents[3])).resolve()
# CH17 was explicitly authorized for interface-only manual review on 2026-10-26.
# Experiment scopes and reserved recording blocks remain unchanged.
ALLOWED = ("cM2-e004_004-006_CH31", "cM2-e004_004-006_CH3", "cM2-e004_011-015_CH20", "cM2-e008_021-028_CH30", "cM2-e004_011-015_CH17")
MAX_UPLOAD = 256 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_json(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with tmp.open("x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, allow_nan=False)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def approved_rows():
    manifest = read_json(ROOT / "output/real_split_manifest_20260903/manifest.json")
    return {r["dataset_id"]: r for r in manifest["datasets"] if r["dataset_id"] in ALLOWED}


class DatasetManager:
    def __init__(self, initial_bundle, storage):
        self.root = Path(storage).resolve()
        for name in ("bundles", "uploads", "jobs"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.bundles = {}
        self.paths = {}
        self.jobs = {}
        self.controls = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mat-import")
        self.registry = self.root / "registry.json"
        for entry in read_json(self.registry) if self.registry.exists() else []:
            self._load(entry["id"], Path(entry["path"]))
        initial_bundle = Path(initial_bundle).resolve()
        if (initial_bundle / "dataset.json").exists() and initial_bundle not in self.paths.values():
            self.register(initial_bundle.name, initial_bundle)
        for path in (self.root / "jobs").glob("*.json"):
            job = read_json(path)
            if job["status"] in ("queued", "running"):
                job.update(status="interrupted", error="Server stopped before completion. Retry creates a new import.", finished_at=now())
                atomic_json(path,job)
            self.jobs[job["id"]] = job

    def _load(self, bundle_id, path):
        bundle = read_json(path / "dataset.json")
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", bundle_id) or bundle.get("schema") != "spikeagent-jacob-bundle-v1"
                or bundle.get("dataset_id") not in ALLOWED):
            raise ValueError("Invalid dataset bundle")
        source = bundle.get("source_kind", "saved")
        existing = {c["id"] for b in self.bundles.values() for c in b["cases"]}
        if any(c["dataset_id"] != bundle["dataset_id"] or c["id"] in existing for c in bundle["cases"]):
            raise ValueError("Duplicate or cross-dataset case identity")
        bundle["source_kind"] = source
        bundle.setdefault("run_status", "completed" if source == "saved" else "not_run")
        self.bundles[bundle_id] = bundle
        self.paths[bundle_id] = path.resolve()
        return bundle

    def register(self, bundle_id, path):
        with self.lock:
            self._load(bundle_id, Path(path))
            atomic_json(self.registry, [dict(id=k,path=str(v)) for k,v in self.paths.items()])

    def summaries(self):
        with self.lock:
            entries=list(self.bundles.items())
            if any(b.get("study_id") for _,b in entries):
                entries=[(k,b) for k,b in entries if b.get("study_id") and b.get("study_agent_id") in ("Agent_1","Agent_2") or b["source_kind"]=="initial"]
                entries.sort(key=lambda item:(not bool(item[1].get("study_id")),item[1].get("study_agent_id","")))
            return [dict(id=k,dataset_id=b["dataset_id"],source_kind=b["source_kind"],run_status=b["run_status"],
                cases=len(b["cases"]),n_spikes=b["n_spikes"],sampling_rate_hz=b["sampling_rate_hz"],
                study_id=b.get("study_id"),study_agent_id=b.get("study_agent_id"),optional_reference=b.get("optional_reference",False),
                mat_path=b["provenance"].get("mat_path"),mat_sha256=b["provenance"].get("mat_sha256")) for k,b in entries]

    def get(self, bundle_id=None):
        with self.lock:
            if bundle_id:
                return self.bundles[bundle_id]
            return next(iter(self.bundles.values()), None)

    def all_cases(self):
        with self.lock:
            return {c["id"]:c for bundle in self.bundles.values() for c in bundle["cases"]}

    def find_case(self, case_id):
        with self.lock:
            for key,b in self.bundles.items():
                for case in b["cases"]:
                    if case["id"] == case_id:
                        return case,b,self.paths[key]
        raise KeyError("Unknown case")

    def asset_path(self, name):
        if not re.fullmatch(r"[a-f0-9]{64}\.png",name):
            raise KeyError("Unknown asset")
        with self.lock:
            for key,b in self.bundles.items():
                if name in b["assets"]:
                    return self.paths[key] / "assets" / name,b["assets"][name]
        raise KeyError("Unknown asset")

    def inputs(self):
        result=[]
        for dataset,row in approved_rows().items():
            path=ROOT / row["mat_path"]
            study_active=any(b.get("study_id") for b in self.bundles.values())
            sources=["initial"]+(["saved"] if dataset in ALLOWED[:2] and not study_active else [])
            result.append(dict(dataset_id=dataset,filename=path.name,mat_path=row["mat_path"],n_spikes=row["n_spikes"],
                bytes=row["file_size_bytes"],sha256=row["mat_sha256"],server_available=path.is_file(),sources=sources))
        return result

    def receive_upload(self, stream, dataset, filename, length):
        if dataset not in ALLOWED:
            raise ValueError("Only approved review channels can be uploaded")
        if not isinstance(filename,str) or not re.fullmatch(r"[^/\\\x00-\x1f]{1,200}\.mat",filename,re.I):
            raise ValueError("Choose a MAT filename without directory components")
        row=approved_rows()[dataset]
        if type(length) is not int or length<=0 or length>MAX_UPLOAD or length!=row["file_size_bytes"]:
            raise ValueError("File size does not match this approved dataset (maximum 256 MiB)")
        if shutil.disk_usage(self.root).free < length*2+100_000_000:
            raise ValueError("Not enough disk space for upload and import")
        upload_id=uuid.uuid4().hex
        path=self.root/"uploads"/(upload_id+".mat")
        h=hashlib.sha256();remaining=length
        try:
            with path.open("xb") as f:
                while remaining:
                    chunk=stream.read(min(1024*1024,remaining))
                    if not chunk:
                        raise ValueError("Upload interrupted before all bytes arrived")
                    f.write(chunk);h.update(chunk);remaining-=len(chunk)
            if h.hexdigest()!=row["mat_sha256"]:
                raise ValueError("MAT SHA256 does not match the selected dataset; check dataset selection")
            # Multiple uploads of the same approved input reuse the first copy.
            with self.lock:
                for existing in (self.root/"uploads").glob("*.json"):
                    old=read_json(existing)
                    if old["dataset_id"]==dataset and old["sha256"]==h.hexdigest():
                        path.unlink();return dict(old,reused=True)
                result=dict(id=upload_id,dataset_id=dataset,filename=filename,bytes=length,sha256=h.hexdigest(),created_at=now())
                atomic_json(path.with_suffix(".json"),result)
            return result
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def start_import(self, body):
        dataset,source=body.get("dataset_id"),body.get("source","initial")
        if dataset not in ALLOWED or source not in ("saved","initial"):
            raise ValueError("Choose an approved dataset and import mode")
        if source=="saved" and dataset not in ALLOWED[:2]:
            raise ValueError("No saved Qwen9B decisions exist for this dataset")
        upload_id=body.get("upload_id")
        if upload_id:
            if not isinstance(upload_id,str) or not re.fullmatch(r"[a-f0-9]{32}",upload_id):
                raise ValueError("Invalid upload identity")
            meta=read_json(self.root/"uploads"/(upload_id+".json"))
            if meta["dataset_id"]!=dataset:
                raise ValueError("Uploaded file belongs to another dataset")
            input_path=self.root/"uploads"/(upload_id+".mat")
        else:
            input_path=ROOT/approved_rows()[dataset]["mat_path"]
        with self.lock:
            for key,bundle in self.bundles.items():
                if bundle["dataset_id"]==dataset and bundle["source_kind"]==source:
                    return dict(id=None,status="completed",bundle_id=key,reused=True,progress=100)
            for job in self.jobs.values():
                if job["dataset_id"]==dataset and job["source"]==source and job["status"] in ("queued","running"):
                    return dict(job,reused=True)
            job_id=uuid.uuid4().hex
            job=dict(id=job_id,dataset_id=dataset,source=source,status="queued",progress=0,created_at=now(),error=None,log=[],bundle_id=None)
            self.jobs[job_id]=job;self.controls[job_id]=dict(cancel=threading.Event(),process=None)
            self._save(job)
            self.executor.submit(self._run,job_id,input_path)
            return dict(job)

    def _save(self,job):
        atomic_json(self.root/"jobs"/(job["id"]+".json"),job)

    def _run(self,job_id,input_path):
        with self.lock:
            job=self.jobs[job_id];control=self.controls[job_id]
            if control["cancel"].is_set():
                job.update(status="cancelled",finished_at=now());self._save(job);return
            job.update(status="running",started_at=now(),progress=5);self._save(job)
        destination=self.root/"bundles"/(job["dataset_id"]+"-"+job["source"]+"-"+job_id[:12])
        env=dict(os.environ,OPENBLAS_NUM_THREADS="1",OMP_NUM_THREADS="1",MKL_NUM_THREADS="1")
        command=[sys.executable,"-B","-u",str(Path(__file__).with_name("import_jacob.py")),"--dataset",job["dataset_id"],
            "--source",job["source"],"--output",str(destination),"--input",str(input_path)]
        process=None;selector=None
        try:
            process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env)
            control["process"]=process
            selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
            started=time.monotonic();buffer=b"";line_count=0
            while process.poll() is None or selector.get_map():
                if control["cancel"].is_set() or time.monotonic()-started>1800:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
                    if control["cancel"].is_set():
                        with self.lock:job.update(status="cancelled",finished_at=now());self._save(job)
                        return
                    raise ValueError("Import exceeded the 30-minute processing limit")
                for key,_ in selector.select(timeout=.2):
                    chunk=os.read(key.fileobj.fileno(),8192)
                    if not chunk:selector.unregister(key.fileobj);continue
                    buffer+=chunk
                    while b"\n" in buffer:
                        line,buffer=buffer.split(b"\n",1);line=line.decode("utf-8","replace")[:500]
                        line_count+=int(line.startswith("Imported step_"))
                        count=67 if job["dataset_id"]==ALLOWED[0] else 27
                        with self.lock:
                            job["log"]=(job["log"]+[line])[-20:]
                            job["progress"]=min(95,10+int(line_count/max(count,1)*85));self._save(job)
            process.wait()
            selector.close();process.stdout.close()
            if process.returncode:
                raise ValueError(job["log"][-1] if job["log"] else "MAT import failed")
            bundle_id=destination.name
            self.register(bundle_id,destination)
            with self.lock:job.update(status="completed",progress=100,bundle_id=bundle_id,finished_at=now());self._save(job)
        except Exception as exc:
            with self.lock:job.update(status="failed",error=str(exc),finished_at=now());self._save(job)
        finally:
            if process is not None and process.poll() is None:process.kill();process.wait()
            if selector is not None:selector.close()
            if process is not None and process.stdout is not None:process.stdout.close()
            control["process"]=None

    def list_jobs(self):
        with self.lock:return [dict(j) for j in sorted(self.jobs.values(),key=lambda j:j["created_at"],reverse=True)]

    def cancel(self,job_id):
        with self.lock:
            job=self.jobs[job_id]
            if job["status"] in ("queued","running"):
                self.controls[job_id]["cancel"].set()
                if job["status"]=="queued":job.update(status="cancelled",finished_at=now())
                else:job["cancel_requested"]=True
                self._save(job)
            return dict(job)

    def close(self):
        for control in self.controls.values():control["cancel"].set()
        self.executor.shutdown(wait=True,cancel_futures=False)
