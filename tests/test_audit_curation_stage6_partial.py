"""The Stage 6 forensic audit is deliberately offline and read-only."""
import json
import socket

import numpy as np
import pytest

from scripts.analysis import audit_curation_stage6_partial as audit


DATASET = "cM2-e004_004-006_CH1"


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def build_run(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    write_json(root / "manifest.json", {"version": "stage6-development-rollout-v1",
               "datasets": [{"dataset_id": DATASET, "initial_clusters": 2}]})
    write_json(root / "execution_started.json", {"budget_usd": 50})
    (root / "responses").mkdir()
    write_json(root / "responses/000001.json", {"id": "resp_test"})
    rows = [
        {"event": "count", "dataset_id": DATASET, "request_sha256": "a"},
        {"event": "reserved", "call": 1, "dataset_id": DATASET,
         "request_sha256": "a", "reserved_usd": .25},
        {"event": "usage", "call": 1, "conservative_usd": .04,
         "standard_estimate_usd": .03},
        {"event": "count", "dataset_id": DATASET, "request_sha256": "b"},
        {"event": "reserved", "call": 2, "dataset_id": DATASET,
         "request_sha256": "b", "reserved_usd": .25},
    ]
    (root / "provider_ledger.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return root


def test_inflight_call_is_not_filled_or_retried(tmp_path, monkeypatch):
    root = build_run(tmp_path)
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("network used"))
    result = audit.audit(root)
    assert result["campaign_status"] == "interrupted_without_final_status"
    assert result["ledger"]["settled_calls"] == 1
    assert result["ledger"]["unresolved_calls"] == [2]
    assert result["ledger"]["known_plus_reserved_usd"] == .29
    assert not result["automatic_resume_safe"]
    assert not (root / "execution_status.json").exists()
    assert "not_started" in audit.report(result)


def test_phase2_pair_bound_uses_entry_snapshot(tmp_path):
    path = tmp_path / "channel"
    path.mkdir()
    np.savez(path / "initial.npz", assigns=np.array([1, 1, 2] + [10] * 4000 + [11] * 4000))
    events = [{"event": "decision", "phase": "phase2", "calls": 1,
               "cid": 1, "target": 10, "action": "NOT_MERGE"}]
    result = audit.phase2_bound(path, events)
    assert result["initial_small"] == 2 and result["initial_large"] == 2
    assert result["unseen_small"] == 1
    assert result["maximum_remaining_pair_calls"] == 3


def test_request_uniqueness_separates_repeated_state_from_repeated_pair(tmp_path):
    path = tmp_path / "channel"
    path.mkdir()
    requests = [
        ("phase1", 1, None, "state-a"),
        ("phase1", 1, None, "state-a"),
        ("phase2", 2, 10, "state-b"),
        ("phase2", 2, 10, "state-c"),
    ]
    for number, (phase, source, target, state) in enumerate(requests, 1):
        step = path / f"step_{number:05d}"
        step.mkdir()
        observation = {"cluster_id": source} if phase == "phase1" else {
            "small_cluster_id": source, "large_cluster_id": target}
        write_json(step / "request.json", {"phase": phase, "context": None,
                   "observation": observation, "state_sha256": state})
    assert audit.request_uniqueness(path) == {
        "saved_requests": 4, "duplicate_state_requests": 1,
        "phase2_pair_requests": 2, "duplicate_phase2_pairs": 1}


def test_corrupt_ledger_and_missing_response_rejected(tmp_path):
    root = build_run(tmp_path)
    (root / "responses/000001.json").unlink()
    with pytest.raises(ValueError, match="Usage ledger row without saved response"):
        audit.audit(root)
    write_json(root / "responses/000001.json", {"id": "resp_test"})
    ledger = root / "provider_ledger.jsonl"
    rows = audit.read_jsonl(ledger)
    rows[4]["request_sha256"] = "wrong"
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="Count/reservation mismatch"):
        audit.audit(root)


def test_output_directory_must_be_new(tmp_path):
    root = build_run(tmp_path)
    output = tmp_path / "audit"
    audit.main(["--run-root", str(root), "--output-root", str(output)])
    assert (output / "summary.json").is_file()
    with pytest.raises(FileExistsError):
        audit.main(["--run-root", str(root), "--output-root", str(output)])


def test_report_keeps_phase2_detail_outside_table():
    result = {"campaign_status": "stopped", "automatic_resume_safe": False,
        "ledger": {"reserved_calls": 2, "settled_calls": 1, "unresolved_calls": [2],
                   "known_conservative_usd": .04, "unknown_reservation_usd": .25},
        "datasets": [
            {"dataset_id": DATASET, "status": "stopped", "decisions": 1,
             "by_phase": {"phase2": 1}, "phase2": {"initial_small": 2,
             "initial_large": 1, "unseen_small": 1, "maximum_remaining_pair_calls": 1}},
            {"dataset_id": "cM2-e004_004-006_CH3", "status": "not_started", "decisions": 0},
        ]}
    text = audit.report(result)
    assert text.index("| cM2-e004_004-006_CH3 |") < text.index("Phase 2 began")
