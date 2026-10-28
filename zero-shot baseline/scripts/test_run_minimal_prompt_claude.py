"""Offline tests for the Claude Opus 5.5 minimal runner. A fake client stands in for the API."""
import base64
import json
import math
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


# ---------------------------------------------------------------- agentic-free v0 ---------------

def agent_message(action, cluster=None, source=None, target=None, stop="end_turn", rationale="r"):
    return message(json.dumps(dict(action=action, cluster=cluster, source=source, target=target,
                                   rationale=rationale)), stop=stop)


def tiny_manager():
    """Overclusters 1-4; hierarchy cluster 1 = 1+2, cluster 3 = 3+4 (tree columns: [to, from, cs, score])."""
    import numpy as np
    from src.cluster.manager import ClusterManager
    rng = np.random.default_rng(0)
    over = np.repeat([1, 2, 3, 4], 60)
    assigns = np.where(over <= 2, 1, 3)
    tree = np.array([[1, 3], [2, 4], [0.9, 0.8], [0.0, 0.0]], dtype=float)
    times = np.sort(rng.uniform(0, 600, len(over)))
    waves = rng.normal(0, 1, (len(over), 45)) - over[:, None] * np.hanning(45)
    return ClusterManager(assigns, over, tree, times, waves)


def agent_run(tmp_path, raws, turn_limit=20):
    tmp_path.mkdir(parents=True, exist_ok=True)
    p, client = provider_with(tmp_path, raws, budget=10.0)
    c = rc.AgentController(tiny_manager(), rc.DATASETS[0], tmp_path / "channel", provider=p,
                           sampling_rate=30000., turn_limit=turn_limit)
    return c, client


def sent_texts(client):
    return [json.loads(k["messages"][0]["content"][0]["text"]) for k in client.messages.requests]


def test_agent_request_uses_the_agent_prompt_schema_and_no_fallback():
    req = rc.build_agent_request('{"turn": 1}', [URL] * 4)
    rc.check_request(req)
    assert req["system"] == rc.AGENT_PROMPT and "fallbacks" not in req
    assert req["output_config"]["format"]["schema"]["properties"]["action"]["enum"] == list(rc.AGENT_ACTIONS)
    assert len(req["messages"][0]["content"]) == 5
    with pytest.raises(ValueError):
        rc.build_agent_request("{}", [URL] * 2)
    with pytest.raises(rc.ClaudeStop):
        rc.agent_decision(message('{"action": "KEEP", "rationale": "r"}'))
    assert rc.agent_decision(agent_message("FINISH"))["action"] == "FINISH"


def test_agent_loop_executes_model_choices_and_reports_non_executable_ones(tmp_path):
    script = [agent_message("INSPECT", cluster=1, rationale="1 looks mixed"), agent_message("SPLIT", cluster=1),
              agent_message("SPLIT", cluster=2),  # leaf: no merge to undo -> not executed, state unchanged
              agent_message("COMPARE", source=2, target=3), agent_message("MERGE", source=2, target=3),
              agent_message("DISCARD", cluster=1), agent_message("KEEP", cluster=3), agent_message("FINISH")]
    c, client = agent_run(tmp_path, script)
    client.messages.requests = []
    create = client.messages.create
    client.messages.create = lambda **k: (client.messages.requests.append(k), create(**k))[1]
    final = c.run()
    assert c.status == "completed" and c.calls == 8 and c.operations == 3  # KEEP is not an operation
    assert sorted(set(final[final > 0].tolist())) == [3]
    texts = sent_texts(client)
    assert [t["turn"] for t in texts] == list(range(1, 9))
    assert texts[7]["record"][-1] == "turn 7: KEEP 3: kept"
    assert texts[7]["active_clusters"] == texts[6]["active_clusters"]  # KEEP changes no state
    images = [len(k["messages"][0]["content"]) - 1 for k in client.messages.requests]
    assert images == [0, 4, 0, 0, 3, 0, 0, 0]  # only the observation requested by the previous action
    assert texts[1]["record"] == ["turn 1: INSPECT 1: images and measurements returned"]  # no own judgement kept
    assert all("looks mixed" not in json.dumps(t) for t in texts)
    assert {t["turns_remaining"] + t["turn"] for t in texts} == {21}
    assert [r["cluster_id"] for r in texts[2]["active_clusters"]] == [1, 2, 3]
    assert "not executed" in texts[3]["record"][-1] and texts[3]["active_clusters"] == texts[2]["active_clusters"]
    assert set(texts[4]["observation"]["measurements"]) >= {"source_cluster_id", "target_cluster_id", "merged_isi_rate"}
    assert texts[5]["observation"] is None  # MERGE returns no images
    summary = json.loads((tmp_path / "channel" / "summary.json").read_text())
    assert summary["terminal_evaluation_allowed"] and summary["not_executed"] == 1
    assert (tmp_path / "channel" / "terminal.npz").exists()


def test_agent_turn_limit_and_invalid_streak_leave_the_channel_incomplete(tmp_path, monkeypatch):
    c, _ = agent_run(tmp_path / "a", [agent_message("INSPECT", cluster=1)] * 2, turn_limit=2)
    c.run()
    assert c.status == "stopped_turn_limit" and c.calls == 2
    assert not json.loads((tmp_path / "a" / "channel" / "summary.json").read_text())["terminal_evaluation_allowed"]
    monkeypatch.setattr(rc, "AGENT_INVALID_STREAK", 2)
    c, _ = agent_run(tmp_path / "b", [agent_message("KEEP", cluster=99), agent_message("MERGE", source=1, target=1)])
    c.run()
    assert c.status == "stopped_invalid_streak" and c.operations == 0


def test_agent_model_switch_to_fable_changes_only_model_limit_and_prices():
    opus_scope, opus_request = rc.AGENT_SCOPE, rc.build_agent_request("{}", [])
    try:
        rc.use_model("claude-fable-5-1")
        req = rc.build_agent_request("{}", [])
        rc.check_request(req)
        assert req["model"] == "claude-fable-5-1" and req["max_tokens"] == 16000 and "fallbacks" not in req
        assert {k: v for k, v in req.items() if k not in ("model", "max_tokens")} == \
               {k: v for k, v in opus_request.items() if k not in ("model", "max_tokens")}
        assert rc.AGENT_SCOPE.endswith("claude-fable-5-1")
        held = rc.reserve(0.0, 10.0, 3000)
        assert abs(held - (math.ceil(3000 * 1.1 + 1024) * 12.5 + 16000 * 50.0) / 1e6) < 1e-12
        fable = message(json.dumps(dict(action="FINISH", cluster=None, source=None, target=None, rationale="r")),
                        model="claude-fable-5-1")
        assert rc.agent_decision(fable)["action"] == "FINISH"
        with pytest.raises(rc.ClaudeStop):
            rc.agent_decision(agent_message("FINISH"))  # an Opus answer is rejected in a Fable run
    finally:
        rc.use_model("claude-opus-5-5")
    assert rc.AGENT_SCOPE == opus_scope and rc.MAX_TOKENS == 8000


def test_agent_resume_replays_saved_turns_byte_identically_for_free_then_continues(tmp_path):
    script = [agent_message("INSPECT", cluster=1), agent_message("SPLIT", cluster=1), agent_message("KEEP", cluster=2),
              agent_message("DISCARD", cluster=3), agent_message("FINISH")]
    first = tmp_path / "first"
    first.mkdir()
    # The 4th reservation no longer fits: a clean budget stop before any request is sent.
    p1, _ = provider_with(first, script, budget=rc.reserve(0.0, 10.0, 3000) + 0.0585)
    c1 = rc.AgentController(tiny_manager(), rc.DATASETS[0], first / "channel", provider=p1,
                            sampling_rate=30000., turn_limit=20)
    with pytest.raises(rc.ClaudeStop):
        c1.run()
    assert c1.calls == 3 and p1.stop_kind == "budget" and c1.status == "stopped"
    ledger = [json.loads(l) for l in (first / "provider_ledger.jsonl").read_text().splitlines()]
    responses = {int(f.stem): json.loads(f.read_text()) for f in (first / "responses").glob("*.json")}
    plan = rc.plan_replay(ledger, responses, accept=rc.agent_decision)
    assert [e["source_call"] for e in plan] == [1, 2, 3]
    second = tmp_path / "second"
    second.mkdir()
    p2, client = provider_with(second, script[3:], replay=plan,
                               inherited={"conservative_usd": p1.charged, "standard_usd": p1.standard})
    c2 = rc.AgentController(tiny_manager(), rc.DATASETS[0], second / "channel", provider=p2,
                            sampling_rate=30000., turn_limit=20)
    c2.run()
    assert c2.status == "completed" and c2.calls == 5 and p2.replayed == 3 and not p2.replay
    assert client.messages.created == 2  # only DISCARD and FINISH were sent live
    hashes = lambda d, n: [json.loads((d / f"step_{i:05d}" / "request.json").read_text())["request_sha256"] for i in range(1, n + 1)]
    assert hashes(first / "channel", 3) == hashes(second / "channel", 3)
    assert p2.charged > p1.charged  # cumulative accounting continues from the source


def test_agent_refusal_stops_without_a_substitute_action(tmp_path):
    c, _ = agent_run(tmp_path, [agent_message("FINISH", stop="refusal")])
    with pytest.raises(rc.ClaudeStop):
        c.run()
    assert c.status == "stopped" and (tmp_path / "channel" / "stopped.npz").exists()
