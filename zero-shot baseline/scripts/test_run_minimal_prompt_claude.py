"""Offline tests for the Claude Opus 5.5 minimal runner. A fake client stands in for the API."""
import base64
import json
from pathlib import Path
import sys
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_minimal_prompt_claude as rc  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
URL = "data:image/png;base64," + base64.b64encode(PNG).decode()
OBS1 = dict(sampling_rate_hz=30000.0, waveform_window_ms=1.5, refractory_ms=2.0, cluster_id=3,
            n_spikes=1200, n_overclusters=4, isi_violation_rate=0.01, amplitude_cv=0.2)


def message(text='{"action": "KEEP", "rationale": "r"}', stop="end_turn", model=rc.MODEL, extra=()):
    return {"type": "message", "role": "assistant", "model": model, "stop_reason": stop,
            "content": [{"type": "thinking", "thinking": ""}, *extra, {"type": "text", "text": text}],
            "usage": {"input_tokens": 3000, "output_tokens": 400,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}


def test_request_is_a_faithful_translation_of_the_astra_request():
    req = rc.build_request("phase1", OBS1, [URL] * 4)
    oa = rc.rb.build_request("phase1", OBS1, [URL] * 4)
    assert req["system"] == oa["instructions"]
    assert req["messages"][0]["content"][0]["text"] == oa["input"][0]["content"][0]["text"]
    assert [b["source"]["data"] for b in req["messages"][0]["content"][1:]] == [URL.split(",", 1)[1]] * 4
    assert req["output_config"]["format"]["schema"] == oa["text"]["format"]["schema"]
    assert req["output_config"]["effort"] == "high" and req["thinking"] == {"type": "adaptive"}
    assert req["max_tokens"] == 8000 and "fallbacks" not in req
    rc.check_request(req)


def test_fallback_or_wrong_settings_are_rejected():
    req = rc.build_request("phase1", OBS1, [URL] * 4)
    for change in ({"fallbacks": "default"}, {"max_tokens": 4000}, {"model": "claude-opus-5"},
                   {"output_config": {"effort": "medium", "format": req["output_config"]["format"]}}):
        with pytest.raises(rc.ClaudeStop):
            rc.check_request({**req, **change})


def test_decision_parsing_accepts_only_a_clean_end_turn_answer():
    assert rc.decision_from_response(message(), "phase1")["action"] == "KEEP"
    bad = [message(stop="max_tokens"), message(stop="refusal"), message(model="claude-opus-4-8"),
           message(text='{"action": "MERGE", "rationale": "r"}'), message(text="not json"),
           message(extra=[{"type": "text", "text": "{}"}]), message(extra=[{"type": "tool_use"}])]
    for raw in bad:
        with pytest.raises(rc.ClaudeStop):
            rc.decision_from_response(raw, "phase1")


def test_costs_and_reservation():
    cost = rc.usage_cost(message())
    assert abs(cost["standard_estimate_usd"] - (3000 * 4 + 400 * 20) / 1e6) < 1e-12
    assert abs(cost["conservative_usd"] - (3000 * 5 + 400 * 20) / 1e6) < 1e-12
    held = rc.reserve(0.0, 1.0, 3000)
    assert held >= cost["conservative_usd"]
    with pytest.raises(rc.ClaudeStop):
        rc.reserve(0.999, 1.0, 3000)


class FakeMessages:
    def __init__(self, raw):
        self.raw, self.created = raw, 0

    def count_tokens(self, **kwargs):
        assert set(kwargs) == {"model", "system", "messages", "thinking", "output_config"}
        return types.SimpleNamespace(input_tokens=3000)

    def create(self, **kwargs):
        self.created += 1
        return types.SimpleNamespace(model_dump=lambda mode="json": self.raw)


def test_provider_counts_reserves_calls_once_and_logs(tmp_path):
    (tmp_path / "responses").mkdir()
    client = types.SimpleNamespace(messages=FakeMessages(message()))
    provider = rc.ClaudeProvider(client, tmp_path, budget=1.0)
    provider.dataset = "test"
    req = rc.build_request("phase1", OBS1, [URL] * 4)
    provider.count(req)
    raw = provider.respond(req)
    assert client.messages.created == 1 and raw["stop_reason"] == "end_turn"
    events = [json.loads(l)["event"] for l in (tmp_path / "provider_ledger.jsonl").read_text().splitlines()]
    assert events == ["count", "reserved", "usage"]
    assert abs(provider.standard - (3000 * 4 + 400 * 20) / 1e6) < 1e-12
    with pytest.raises(rc.ClaudeStop):
        provider.respond(req)  # not counted again -> refused


def test_provider_refuses_a_request_that_differs_from_the_counted_one(tmp_path):
    (tmp_path / "responses").mkdir()
    provider = rc.ClaudeProvider(types.SimpleNamespace(messages=FakeMessages(message())), tmp_path, budget=1.0)
    provider.dataset = "test"
    provider.count(rc.build_request("phase1", OBS1, [URL] * 4))
    other = rc.build_request("phase1", {**OBS1, "cluster_id": 4}, [URL] * 4)
    with pytest.raises(rc.ClaudeStop):
        provider.respond(other)


class SeqMessages:
    """Fake API returning the given raw responses in order."""
    def __init__(self, raws):
        self.raws, self.created = list(raws), 0

    def count_tokens(self, **kwargs):
        return types.SimpleNamespace(input_tokens=3000)

    def create(self, **kwargs):
        self.created += 1
        raw = self.raws.pop(0)
        return types.SimpleNamespace(model_dump=lambda mode="json": raw)


def provider_with(tmp_path, raws, **kw):
    (tmp_path / "responses").mkdir(exist_ok=True)
    client = types.SimpleNamespace(messages=SeqMessages(raws))
    p = rc.ClaudeProvider(client, tmp_path, kw.pop("budget", 1.0), **kw)
    p.dataset = kw.get("dataset", "test")
    return p, client


REQ = rc.build_request("phase1", OBS1, [URL] * 4)
REQ2 = rc.build_request("phase1", {**OBS1, "cluster_id": 4}, [URL] * 4)


def test_truncation_is_retried_once_and_counted(tmp_path):
    p, client = provider_with(tmp_path, [message(stop="max_tokens"), message()])
    p.count(REQ)
    raw = p.respond(REQ)
    assert raw["stop_reason"] == "end_turn" and client.messages.created == 2
    assert len(p.truncations) == 1 and p.truncations[0]["retried"] and p.last_live_attempts == 2
    assert p.stop_kind is None


def test_second_truncation_stops(tmp_path):
    p, client = provider_with(tmp_path, [message(stop="max_tokens"), message(stop="max_tokens"), message()])
    p.count(REQ)
    raw = p.respond(REQ)
    assert client.messages.created == 2 and p.stop_kind == "truncated_after_retry"
    with pytest.raises(rc.ClaudeStop):
        rc.decision_from_response(raw, "phase1")


def test_retry_needs_its_own_budget(tmp_path):
    held = rc.reserve(0.0, 1.0, 3000)
    p, _ = provider_with(tmp_path, [message(stop="max_tokens")], budget=held * 1.05)  # first fits, retry does not
    p.count(REQ)
    with pytest.raises(rc.ClaudeStop):
        p.respond(REQ)
    assert p.stop_kind == "budget"


def test_replay_is_free_ordered_and_then_goes_live(tmp_path):
    entry = dict(source_call=1, request_sha256=rc.digest(REQ), input_tokens=3000, raw=message())
    p, client = provider_with(tmp_path, [message()], replay=[entry],
                              inherited={"conservative_usd": 0.5, "standard_usd": 0.4, "tokens": {"output_tokens": 7}})
    p.count(REQ)
    assert p.replaying_current
    p.respond(REQ)
    assert client.messages.created == 0 and p.charged == 0.5 and p.replayed == 1
    p.count(REQ2)
    p.respond(REQ2)
    assert client.messages.created == 1 and p.charged > 0.5


def test_replay_divergence_stops(tmp_path):
    entry = dict(source_call=1, request_sha256=rc.digest(REQ), input_tokens=3000, raw=message())
    p, _ = provider_with(tmp_path, [], replay=[entry])
    with pytest.raises(rc.ClaudeStop):
        p.count(REQ2)


def test_pilot_stops_before_the_next_stepwise_request(tmp_path):
    p, _ = provider_with(tmp_path, [message()], pilot_stepwise=1)
    p.dataset = "x:stepwise"
    p.count(REQ)
    p.respond(REQ)
    with pytest.raises(rc.PilotStop):
        p.count(REQ2)


def test_plan_replay_keeps_accepted_tail_and_drops_failed_tail():
    h1, h2 = rc.digest(REQ), rc.digest(REQ2)
    ledger = [dict(event="count", request_sha256=h1, input_tokens=3000), dict(event="reserved", call=1, request_sha256=h1),
              dict(event="count", request_sha256=h2, input_tokens=3100), dict(event="reserved", call=2, request_sha256=h2),
              dict(event="reserved", call=3, request_sha256=h2)]
    phases = {h1: "phase1", h2: "phase1"}
    ok = rc.plan_replay(ledger[:2], {1: message()}, phases)
    assert [e["source_call"] for e in ok] == [1]  # e.g. pilot or budget stop after an accepted answer
    failed = rc.plan_replay(ledger, {1: message(), 2: message(stop="max_tokens"), 3: message(stop="max_tokens")}, phases)
    assert [e["source_call"] for e in failed] == [1]  # truncated twice -> that request is re-asked live
    http = rc.plan_replay(ledger, {1: message(), 2: message(stop="max_tokens")}, phases)
    assert [e["source_call"] for e in http] == [1]  # retry lost to a transport failure
