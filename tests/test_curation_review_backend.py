"""Offline review persistence, provenance, scope and HTTP boundary regressions."""
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "mockups/curation-review/backend" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


server = module("server")


def case():
    # Small isolated fixture; never served by the production backend.
    return dict(id="fixture-case", dataset_id="cM2-e004_004-006_CH31", synthetic=False,
        source="U-001", pair=None, phase_id="phase1", channel="CH31", ai="KEEP", rationale="Saved explanation",
        units=[dict(id="U-001", cluster_id=1, spikes=10, split=False), dict(id="U-002", cluster_id=2, spikes=20, split=True)],
        run_id="fixture-run", step_id="step_00001", state_id="a"*64, state_sha256="a"*64,
        request_sha256="b"*64, image_sha256={"waveform": "c"*64}, mat_sha256="d"*64,
        checkpoint_sha256="e"*64, request_hash_status="recorded", agent_id="Fixture", agent_kind="historical-model-response",
        displayed_trace_count=10, evidence_id="a"*64)


def body(request_id="request-0001", revision=0):
    return dict(request_id=request_id, expected_revision=revision,
        draft=dict(agree=False, action="DISCARD", target="", reason="Waveform contamination", note="", positive="", disagreement_types=[]),
        context=dict(viewed_cluster_ids=["U-001"], visible_cluster_ids=["U-001"], viewed_tabs=["detail"], annotator="Reviewer"))


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "reviews.sqlite3"
        self.store = server.ReviewStore(self.path)
        self.case = case()

    def tearDown(self):
        self.tmp.cleanup()

    def test_saved_review_survives_restart_and_has_canonical_provenance(self):
        data = body()
        data["context"]["state_sha256"] = "forged"
        result = self.store.write("reviewer-a", self.case, data)
        restored = server.ReviewStore(self.path).reviews("reviewer-a")[self.case["id"]]
        self.assertEqual(restored, dict(review=result["review"], revision=1))
        self.assertEqual(restored["review"]["state_sha256"], self.case["state_sha256"])
        self.assertIs(restored["review"]["synthetic"], False)
        self.assertEqual(restored["review"]["execution_status"], "not_executed")
        self.assertFalse(self.store.reviews("reviewer-b"))

    def test_prefix_matching_is_not_substring_or_sql_wildcard(self):
        self.store.import_reasons(["Waveform contamination", "Mixed waveform populations", "waveform contamination", "100% artifacts", "_prefix"])
        self.assertEqual(len(self.store.reasons("WaVe")), 1)
        self.assertEqual(len(self.store.reasons("ＷＡＶＥ")), 1)
        self.assertEqual(len(self.store.reasons("mixed")), 1)
        self.assertEqual(self.store.reasons("form"), [])
        self.assertEqual(self.store.reasons("%"), [])
        self.assertEqual(len(self.store.reasons("_")), 1)
        self.store.import_reasons(["Waveform contamination"])
        self.assertEqual(len(self.store.reasons("")), 4)

    def test_retry_and_conflict_do_not_duplicate_feedback_or_reason_usage(self):
        first = self.store.write("reviewer-a", self.case, body())
        self.assertEqual(self.store.write("reviewer-a", self.case, body()), first)
        with self.assertRaises(server.Conflict):
            self.store.write("reviewer-a", self.case, body("request-0002"))
        self.assertEqual(self.store.reasons("wave")[0]["uses"], 1)
        self.assertEqual(len(self.store.export("reviewer-a")["events"]), 1)

    def test_undo_keeps_history_and_revision_and_reason(self):
        self.store.write("reviewer-a", self.case, body())
        self.store.write("reviewer-a", self.case, body("undo-request", 1), undo=True)
        self.assertEqual(self.store.reviews("reviewer-a"), {})
        self.assertEqual(self.store.revisions("reviewer-a")[self.case["id"]], 2)
        self.store.write("reviewer-a", self.case, body("resave-request", 2))
        self.assertEqual(len(self.store.export("reviewer-a")["events"]), 3)
        self.assertEqual(self.store.reasons("wave")[0]["uses"], 2)

    def test_semantic_validation_rejects_leaf_split_self_merge_and_invalid_agreement(self):
        for changes in [dict(action="SPLIT"), dict(action="MERGE", target="U-001"), dict(action="MERGE", target="U-999"),
                        dict(agree=True), dict(action="KEEP", reason="", note=""), dict(agree="false")]:
            data = body(); data["draft"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.write("reviewer-a", self.case, data)
        self.assertFalse(self.store.reviews("reviewer-a"))

    def test_rationale_only_and_optional_positive_and_defer_semantics(self):
        data = body();data["draft"].update(action="KEEP", disagreement_types=["rationale"])
        r = self.store.write("reviewer-a", self.case, data)["review"]
        self.assertFalse(r["expert_agree"])
        self.assertEqual(r["expert_action"], "KEEP")
        data = body("positive-request", 1);data["draft"].update(agree=True, action="KEEP", reason="", positive="Good isolation")
        r = self.store.write("reviewer-a", self.case, data)["review"]
        self.assertEqual(r["expert_positive_comment"], "Good isolation")
        data = body("defer-request", 2);data["draft"]["action"] = "DEFER"
        r = self.store.write("reviewer-a", self.case, data)["review"]
        self.assertIsNone(r["expert_action"])
        self.assertFalse(r["phase_contract_compatible"])

    def test_importer_rejects_reserved_scope_before_opening_files(self):
        importer = module("import_jacob")
        with patch.object(importer, "read_json", side_effect=AssertionError("Must not read manifests or MAT")):
            with self.assertRaises(ValueError):
                importer.scoped_row("cM2-e007_012-017_CH1")
            with self.assertRaises(ValueError):
                importer.scoped_row("cM2-e004_001-003_CH1")

    def test_manual_review_has_no_invented_model_and_keep_needs_no_reason(self):
        c=case();c.update(review_mode="manual",ai=None,rationale=None,agent_id=None,agent_kind="none",request_sha256=None,image_sha256={})
        data=body();data["draft"].update(agree=None,action="KEEP",reason="",positive="Well isolated")
        r=self.store.write("reviewer-a",c,data)["review"]
        self.assertIsNone(r["expert_agree"]);self.assertIsNone(r["ai_action"]);self.assertIsNone(r["ai_source_cluster"])
        self.assertEqual(r["model_visible_cluster_set"],[])
        self.assertEqual(r["expert_positive_comment"],"Well isolated")
        data=body("manual-discard",1);data["draft"].update(agree=None,reason="")
        with self.assertRaises(ValueError):self.store.write("reviewer-a",c,data)

    def test_profile_is_persistent_and_validated(self):
        self.store.profile("reviewer-a","  Expert 1  ")
        self.assertEqual(server.ReviewStore(self.path).profile("reviewer-a")["annotator"],"Expert 1")
        self.assertEqual(self.store.profile("reviewer-b")["annotator"],"")
        with self.assertRaises(ValueError):self.store.profile("reviewer-a","x"*101)

    def test_backup_preview_restore_idempotence_and_atomic_validation(self):
        self.store.write("reviewer-a",self.case,body())
        backup=self.store.export("reviewer-a")
        preview=self.store.import_backup("reviewer-b",backup,{self.case["id"]:self.case},True)
        self.assertEqual(preview["available_to_import"],1)
        self.assertFalse(self.store.reviews("reviewer-b"))
        result=self.store.import_backup("reviewer-b",backup,{self.case["id"]:self.case},False)
        self.assertEqual(result["added"],1)
        self.assertEqual(self.store.import_backup("reviewer-b",backup,{self.case["id"]:self.case},False)["skipped_existing"],1)
        self.assertEqual(self.store.export("reviewer-b")["events"][0]["type"],"review_imported")
        bad=json.loads(json.dumps(backup));bad["reviews"][0]["state_sha256"]="forged"
        with self.assertRaises(ValueError):self.store.import_backup("reviewer-c",bad,{self.case["id"]:self.case},False)
        self.assertFalse(self.store.reviews("reviewer-c"))
        bad=json.loads(json.dumps(backup));bad["reviews"].append(dict(bad["reviews"][0],case_id="missing-case"))
        with self.assertRaises(ValueError):self.store.import_backup("reviewer-c",bad,{self.case["id"]:self.case},False)
        self.assertFalse(self.store.reviews("reviewer-c"))


class HTTPBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        bundle = dict(schema="spikeagent-jacob-bundle-v1", dataset_id="cM2-e004_004-006_CH31", cases=[case()], n_spikes=30,
            sampling_rate_hz=30000, provenance={}, assets={}, evidence_files={})
        (root / "dataset.json").write_text(json.dumps(bundle))
        self.http = server.make_server(0, root, root / "db.sqlite3")
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join()
        self.tmp.cleanup()

    def fetch(self, path, data=None, headers=None):
        request = Request(self.base + path, data=None if data is None else json.dumps(data).encode(), headers=headers or {})
        return urlopen(request, timeout=5)

    def test_csrf_origin_host_and_arbitrary_file_reads_are_blocked(self):
        for path in ("/../CODEX_HANDOFF_20261019.md", "/.git/config", "/api/assets/../../.env"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                self.fetch(path)
            self.assertEqual(caught.exception.code, 404)
        with self.assertRaises(HTTPError) as caught:
            self.fetch("/api/status", headers={"Host": "attacker.invalid"})
        self.assertEqual(caught.exception.code, 403)
        headers = {"Content-Type": "application/json", "X-Reviewer-ID": "reviewer-a"}
        with self.assertRaises(HTTPError) as caught:
            self.fetch("/api/reviews", body(), headers)
        self.assertEqual(caught.exception.code, 403)
        headers["X-CSRF-Token"] = self.http.app.csrf
        headers["Origin"] = "http://attacker.invalid"
        with self.assertRaises(HTTPError) as caught:
            self.fetch("/api/reviews", body(), headers)
        self.assertEqual(caught.exception.code, 403)

    def test_http_save_and_prefix_lookup(self):
        headers = {"Content-Type": "application/json", "X-Reviewer-ID": "reviewer-a", "X-CSRF-Token": self.http.app.csrf}
        result = json.load(self.fetch("/api/reviews", dict(body(), case_id="fixture-case"), headers))
        self.assertEqual(result["revision"], 1)
        reasons = json.load(self.fetch("/api/reasons?prefix=wave"))["reasons"]
        self.assertEqual(reasons[0]["text"], "Waveform contamination")

    def test_reason_files_and_profile_inputs(self):
        headers={"Content-Type":"application/json","X-Reviewer-ID":"reviewer-a","X-CSRF-Token":self.http.app.csrf}
        result=json.load(self.fetch("/api/reasons/import-file",dict(format="csv",content='reason\n"Waveform, mixed"\n"waveform, mixed"\n'),headers))
        self.assertEqual(result,{"added":1,"duplicates":1})
        result=json.load(self.fetch("/api/reasons/import-file",dict(format="json",content='{"reasons":[{"text":"Timing artifact"}]}'),headers))
        self.assertEqual(result["added"],1)
        with self.assertRaises(HTTPError):self.fetch("/api/reasons/import-file",dict(format="csv",content="unknown\nabc"),headers)
        json.load(self.fetch("/api/profile",dict(annotator="Expert 2"),headers))
        self.assertEqual(json.load(self.fetch("/api/profile",headers=headers))["annotator"],"Expert 2")
        self.assertEqual(len(json.load(self.fetch("/api/reasons/export",headers=headers))["reasons"]),2)

    def test_multiple_bundle_selection_and_catalog_persistence(self):
        root=Path(self.tmp.name)/"second";root.mkdir()
        bundle=json.loads((Path(self.tmp.name)/"dataset.json").read_text())
        bundle["dataset_id"]="cM2-e004_004-006_CH3"
        bundle["cases"][0].update(id="second-case",dataset_id=bundle["dataset_id"])
        (root/"dataset.json").write_text(json.dumps(bundle))
        self.http.app.datasets.register("second",root)
        result=json.load(self.fetch("/api/cases?bundle_id=second"))
        self.assertEqual(result["cases"][0]["id"],"second-case")
        self.assertEqual(len(json.load(self.fetch("/api/datasets"))["bundles"]),2)
        manager=server.DatasetManager(Path(self.tmp.name),Path(self.tmp.name)/"manager")
        self.assertEqual(len(manager.all_cases()),2);manager.close()

    def test_streamed_upload_hash_duplicates_scope_and_partial_failure(self):
        manager=self.http.app.datasets;module=sys.modules[server.DatasetManager.__module__]
        data=b"small-test-mat-bytes";dataset="cM2-e004_004-006_CH3"
        row=dict(file_size_bytes=len(data),mat_sha256=hashlib.sha256(data).hexdigest())
        with patch.object(module,"approved_rows",return_value={dataset:row}):
            one=manager.receive_upload(io.BytesIO(data),dataset,"CH3_spikes.mat",len(data))
            two=manager.receive_upload(io.BytesIO(data),dataset,"CH3_spikes.mat",len(data))
            self.assertEqual(one["id"],two["id"]);self.assertTrue(two["reused"])
            for name,content,size in [("../bad.mat",data,len(data)),("bad.txt",data,len(data)),("bad.mat",b"x"*len(data),len(data)),("bad.mat",b"short",len(data))]:
                with self.subTest(name=name,content=content),self.assertRaises(ValueError):manager.receive_upload(io.BytesIO(content),dataset,name,size)
        with patch.object(module,"approved_rows",side_effect=AssertionError("No data read before scope rejection")):
            with self.assertRaises(ValueError):manager.receive_upload(io.BytesIO(data),"cM2-e007_012-017_CH1","CH1.mat",len(data))
        self.assertEqual(len(list((manager.root/"uploads").glob("*.mat"))),1)

    def test_queued_job_cancellation_and_restart_are_explicit(self):
        manager=self.http.app.datasets
        with patch.object(manager.executor,"submit"):
            job=manager.start_import(dict(dataset_id="cM2-e008_021-028_CH30",source="initial"))
        manager.cancel(job["id"])
        self.assertTrue(manager.controls[job["id"]]["cancel"].is_set())
        self.assertEqual(manager.jobs[job["id"]]["status"],"cancelled")
        with patch.object(manager.executor,"submit"):
            pending=manager.start_import(dict(dataset_id="cM2-e004_011-015_CH20",source="initial"))
        restored=server.DatasetManager(Path(self.tmp.name),manager.root)
        self.assertEqual(restored.jobs[job["id"]]["status"],"cancelled")
        self.assertEqual(restored.jobs[pending["id"]]["status"],"interrupted")
        restored.close()


if __name__ == "__main__":
    unittest.main()
