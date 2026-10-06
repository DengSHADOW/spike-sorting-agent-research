"""Offline resume safeguards: source equality, no repeated inference, budget."""
import base64
import copy
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/run"))
import resume_astra_guarded_baseline as m


def request_and_manifest():
    images = [b"a", b"b", b"c"]
    request = {"model": "gpt-6-astra", "reasoning": {"effort": "high"}, "max_output_tokens": 4000,
               "text": {"format": {"type": "json_schema"}},
               "input": [{"role": "user", "content": [{"type": "input_text", "text": "frozen"}] +
                  [{"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(x).decode()} for x in images]}]}
    manifest = {"model": request["model"], "reasoning": request["reasoning"], "max_output_tokens": 4000,
                "text_format": request["text"]["format"], "prompt_sha256": m.base.digest(b"frozen"),
                "image_sha256_in_order": [m.base.digest(x) for x in images]}
    return request, manifest


def test_request_equality_and_changed_prompt_rejected():
    request, manifest = request_and_manifest()
    m.compare_request(request, manifest)
    request["input"][0]["content"][0]["text"] = "changed"
    with pytest.raises(m.base.ExecutionStop):
        m.compare_request(request, manifest)


def test_state_comparison_rejects_changed_assignment(tmp_path):
    a, b = tmp_path / "a.npz", tmp_path / "b.npz"
    np.savez(a, assigns=[1, 2], hierarchy_tree=[float("nan")])
    np.savez(b, assigns=[1, 2], hierarchy_tree=[float("nan")])
    m.compare_state(a, b)
    np.savez(b, assigns=[1, 0], hierarchy_tree=[float("nan")])
    with pytest.raises(m.base.ExecutionStop):
        m.compare_state(a, b)


def test_cached_response_never_calls_provider(tmp_path):
    e = object.__new__(m.ReplayExperiment)
    e.source = tmp_path / "source"
    out = tmp_path / "new"
    for p in (e.source, out):
        (p / "states").mkdir(parents=True)
        np.savez(p / "states/call_00001.npz", assigns=[1], hierarchy_tree=[2])
    request, manifest = request_and_manifest()
    e.requests = [manifest]
    raw = {"id": "cached-id", "model": "gpt-6-astra", "status": "completed",
           "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"action":"KEEP","rationale":"ok"}'}]}],
           "text": {"format": {"type": "json_schema", "schema_": {}}}}
    e.responses = [{"response": raw}]
    e.served, e.image_index = 0, 3
    def forbidden(**kwargs):
        pytest.fail("Cached replay called paid provider")
    response = e.send(SimpleNamespace(create=forbidden), request, out, 1)
    assert response.model_dump() == raw
    assert response.output_text == '{"action":"KEEP","rationale":"ok"}'
    assert e.served == 1 and e.image_index == 0


def test_audit_boundary_cannot_send_paid_request(tmp_path):
    e = object.__new__(m.ReplayExperiment)
    e.source = tmp_path / "source"
    out = tmp_path / "new"
    for p in (e.source, out):
        (p / "states").mkdir(parents=True)
        np.savez(p / "states/call_00001.npz", assigns=[1], hierarchy_tree=[2])
    request, manifest = request_and_manifest()
    e.requests, e.responses, e.served, e.audit = [manifest], [], 0, True
    with pytest.raises(m.ReplayBoundaryReached):
        e.send(None, request, out, 1)
    assert (out / "resume_boundary.json").exists()


def test_new_budget_includes_old_spend():
    ledger = {"limit_usd": 70, "charged_usd": 29.7754, "calls": []}
    m.astra.reserve(ledger, "CH30", 589, 1900, 4000)
    assert 29.7754 < ledger["charged_usd"] < 70
    ledger["charged_usd"] = 69.99
    before = copy.deepcopy(ledger)
    with pytest.raises(m.astra.BudgetStop):
        m.astra.reserve(ledger, "CH30", 590, 1900, 4000)
    assert ledger == before
