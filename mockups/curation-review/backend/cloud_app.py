"""Cloud Run WSGI API: authenticated sessions, Firestore and private evidence."""
from __future__ import annotations

import os
os.environ.setdefault("SPIKE_REVIEW_DATA_ROOT","/tmp/spikeagent-review-data")
import hashlib
import io
import json
from pathlib import Path
import re
import threading
import time
from datetime import timedelta
from types import SimpleNamespace

import firebase_admin
from firebase_admin import auth
from flask import Flask, Response, g, jsonify, request
from google.api_core.exceptions import GoogleAPICallError
from google.cloud import firestore, storage

from cloud_data import PrivateData, CloudDatasets
from firestore_store import FirestoreReviewStore, key
from import_jacob import ROOT, RUN
from server import Handler, ReviewApplication, now


class CloudApplication(ReviewApplication):
    def __init__(self,db,data):
        self.data=data
        self.datasets=CloudDatasets(data,db)
        seed=data.ensure("output/curation_review_local/jacob_reason_seed_v1.json")
        self.store=FirestoreReviewStore(db,seed)
        self.analysis_service=None;self.analysis_lock=threading.Lock()

    @property
    def csrf(self):
        return hashlib.sha256(("spikeagent-csrf-v1\0"+request.cookies.get("__session","")).encode()).hexdigest()

    def evidence(self,case_id):
        case,bundle,root=self.datasets.find_case(case_id)
        row=bundle["evidence_files"][case["evidence_id"]]
        self.data.ensure((root/"evidence"/row["file"]).relative_to(ROOT))
        return super().evidence(case_id)

    def analysis(self,case_id,cluster_id=None,target_id=None):
        case,bundle,_=self.datasets.find_case(case_id)
        from dataset_inputs import approved_rows
        self.data.ensure(approved_rows()[case["dataset_id"]]["mat_path"])
        if case.get("review_mode")!="manual":
            self.data.ensure_role("saved-state","saved-state-metadata","saved-run-manifest",dataset=case["dataset_id"])
        return super().analysis(case_id,cluster_id,target_id)


class CloudHandler(Handler):
    def check_host(self):pass  # All API requests pass the WSGI authentication guard.
    def reviewer(self):return g.identity["uid"]
    def request_origin(self):return request.headers.get("Origin","")
    def send_response(self,status,message=None):self.response_status=status
    def send_header(self,name,value):self.response_headers[name]=value
    def end_headers(self):pass
    def log_message(self,*args):pass
    def reply(self,value,status=200,content_type="application/json; charset=utf-8"):
        if request.path=="/api/status" and isinstance(value,dict) and status==200:
            value.update(cloud=True,reviewer_id=g.identity["uid"],email=g.identity["email"],role=g.role,
                         upload_mode="resumable",published_views_only=True)
        super().reply(value,status,content_type)


def create_app(application=None,db=None,verify_session=None,verify_token=None,make_session=None):
    flask=Flask(__name__)
    flask.config["MAX_CONTENT_LENGTH"]=8_000_000
    project=os.environ["GOOGLE_CLOUD_PROJECT"]
    owners={email.strip().casefold() for email in os.environ["SPIKE_REVIEW_OWNER_EMAILS"].split(",") if email.strip()}
    origins={v.strip() for v in os.environ["SPIKE_REVIEW_ORIGINS"].split(",") if v.strip()}
    if not owners or not origins or any(not v.startswith("https://") for v in origins):
        raise ValueError("Configure explicit owner emails and HTTPS application origins")
    if db is None:db=firestore.Client(project=project)
    if application is None:
        firebase_admin.initialize_app(options={"projectId":project})
        inventory=json.loads(Path(os.environ["SPIKE_REVIEW_INVENTORY"]).read_text())
        if inventory["project_id"]!=project:raise ValueError("Snapshot belongs to another project")
        bucket=storage.Client(project=project).bucket(os.environ["SPIKE_REVIEW_BUCKET"])
        application=CloudApplication(db,PrivateData(inventory,bucket=bucket))
    verify_session=verify_session or (lambda cookie:auth.verify_session_cookie(cookie,check_revoked=True))
    verify_token=verify_token or (lambda token:auth.verify_id_token(token,check_revoked=True))
    make_session=make_session or (lambda token:auth.create_session_cookie(token,expires_in=timedelta(days=5)))
    config=json.loads(os.environ["SPIKE_REVIEW_WEB_CONFIG"])

    def role(identity):
        email=identity.get("email","").casefold()
        if not identity.get("uid") or identity.get("email_verified") is not True:raise PermissionError("Use a verified Google account")
        if email in owners:return "admin"
        doc=db.collection("review_access").document(key(email)).get()
        if not doc.exists or doc.to_dict().get("active") is not True:raise PermissionError("This account has not been invited. Contact the project owner.")
        return "reviewer"

    @flask.before_request
    def guard():
        if request.method not in ("GET","POST"):return jsonify(error="Method not allowed"),405
        if request.method=="POST" and request.headers.get("Origin") not in origins:
            return jsonify(error="Cross-origin writes are not allowed"),403
        if request.path in ("/api/public-config","/api/session"):
            return None
        cookie=request.cookies.get("__session","")
        if not cookie:return jsonify(error="Sign in to access real review data",authentication_required=True),401
        try:
            g.identity=verify_session(cookie)
        except Exception:
            return jsonify(error="Session expired. Sign in again.",authentication_required=True),401
        try:g.role=role(g.identity)
        except PermissionError as exc:return jsonify(error=str(exc)),403
        if request.method=="POST" and request.headers.get("X-CSRF-Token")!=application.csrf:
            return jsonify(error="Refresh the page to reconnect before saving"),403

    @flask.after_request
    def headers(response):
        response.headers["Cache-Control"]="no-store, private"
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="same-origin"
        return response

    @flask.errorhandler(PermissionError)
    def permission(exc):return jsonify(error=str(exc)),403
    @flask.errorhandler(ValueError)
    def invalid(exc):return jsonify(error=str(exc)),400
    @flask.errorhandler(GoogleAPICallError)
    def unavailable(exc):
        flask.logger.warning("Cloud storage request failed: %s",type(exc).__name__)
        return jsonify(error="Cloud storage temporarily unavailable. Retry without discarding your draft."),503

    def body():
        value=request.get_json()
        if not isinstance(value,dict):raise ValueError("Expected a JSON object")
        return value

    def admin():
        if g.role!="admin":raise PermissionError("Only the project owner can change access or upload raw inputs")

    @flask.get("/api/public-config")
    def public_config():return jsonify(cloud=True,firebase=config)

    @flask.post("/api/session")
    def session():
        header=request.headers.get("Authorization","")
        if not header.startswith("Bearer "):raise PermissionError("A fresh Google sign-in token is required")
        token=header[7:]
        try:identity=verify_token(token)
        except Exception:raise PermissionError("Google sign-in could not be verified")
        access=role(identity)
        if time.time()-identity.get("auth_time",0)>300:raise PermissionError("Sign in again to start a fresh session")
        cookie=make_session(token)
        response=jsonify(uid=identity["uid"],email=identity["email"],role=access)
        response.set_cookie("__session",cookie,max_age=5*24*3600,secure=True,httponly=True,samesite="Lax",path="/")
        return response

    @flask.post("/api/logout")
    def logout():
        response=jsonify(status="signed_out")
        response.delete_cookie("__session",secure=True,httponly=True,samesite="Lax",path="/")
        return response

    @flask.get("/api/admin/access")
    def access_list():
        admin()
        return jsonify(owner_emails=sorted(owners),reviewers=[d.to_dict() for d in db.collection("review_access").stream()])

    @flask.post("/api/admin/access")
    def access_write():
        admin();value=body();email=value.get("email","").strip().casefold();active=value.get("active",True)
        if not re.fullmatch(r"[^\s@]{1,200}@[^\s@]{1,200}\.[^\s@]{1,100}",email) or len(email)>254 or type(active) is not bool:
            raise ValueError("Enter a valid reviewer email and access state")
        if email in owners:raise ValueError("Owner access is configured on the service")
        row=dict(email=email,active=active,role="reviewer",updated_at=now())
        db.collection("review_access").document(key(email)).set(row)
        return jsonify(row)

    @flask.post("/api/uploads/session")
    def begin_upload():
        admin()
        return jsonify(application.datasets.begin_upload(body(),request.headers["Origin"]))

    @flask.post("/api/uploads/complete")
    def finish_upload():
        admin()
        return jsonify(application.datasets.finish_upload(body()))

    @flask.route("/api/<path:route>",methods=["GET","POST"])
    def legacy(route):
        if request.method=="POST" and (request.path.startswith("/api/imports") or request.path=="/api/uploads"):
            admin()
        if request.path=="/api/uploads":raise ValueError("Use the resumable upload endpoint for cloud inputs")
        handler=CloudHandler.__new__(CloudHandler)
        handler.server=SimpleNamespace(app=application)
        handler.path=request.full_path.rstrip("?")
        handler.headers=request.headers
        handler.rfile=io.BytesIO(request.get_data())
        handler.wfile=io.BytesIO();handler.response_headers={};handler.response_status=200
        if request.method=="GET":handler.do_GET()
        else:handler.do_POST()
        return Response(handler.wfile.getvalue(),status=handler.response_status,headers=handler.response_headers)

    return flask
