import base64
import json
import socket
from pathlib import Path

import numpy as np
import pytest

from src.agent import curation_decider_v2 as decider
from src.agent.vllm_provider import ProviderStop, VllmProvider
from src.pipeline.curation_controller_v2 import LimitStop

ROOT = Path(__file__).resolve().parents[1]
PROFILES = json.loads((ROOT / "configs/controller_v2/models.json").read_text())["models"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network permitted")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


class FakeServer:
    """Stands in for vLLM; records request bodies and replays scripted chat completions."""

    def __init__(self, profile, answers, finish=None, model=None):
        self.name, self.answers, self.bodies = profile["served_name"], list(answers), []
        self.finish, self.model = list(finish or []), model

    def __call__(self, method, path, body=None):
        if path == "/v1/models":
            return {"data": [{"id": self.name}]}
        if path == "/version":
            return {"version": "0.32.0"}
        self.bodies.append(body)
        finish = self.finish.pop(0) if self.finish else "stop"
        return {"model": self.model or self.name, "usage": {"prompt_tokens": 3000, "completion_tokens": 200},
                "choices": [{"finish_reason": finish, "message": {"role": "assistant",
                             "reasoning_content": "scripted thinking", "content": self.answers.pop(0)}}]}


def content(kind="single", allowed=("KEEP", "DISCARD", "SPLIT")):
    obs = dict(sampling_rate_hz=30000., waveform_window_ms=1.5, refractory_ms=2.)
    if kind == "single":
        obs.update(cluster_id=1, n_spikes=10, n_overclusters=2, isi_violation_rate=0., amplitude_cv=.1)
    else:
        obs.update(small_cluster_id=2, large_cluster_id=1, n_small=4, n_large=10, small_isi_rate=0.,
                   large_isi_rate=0., merged_isi_rate=0., correlation=.9)
    return decider.content(decider.load_prompts(), kind, obs, [b"png%d" % i for i in range(decider.N_IMAGES[kind])],
                           list(allowed))


def answer(action):
    return json.dumps({"action": action, "rationale": "scripted"})


def test_full_action_lists_reproduce_the_frozen_minimal_prompts_exactly():
    prompts = decider.load_prompts()
    frozen = ROOT / "zero-shot baseline/prompts"
    assert decider.instructions(prompts, "single", ["KEEP", "SPLIT", "DISCARD"]) == (frozen / "phase1.txt").read_text()
    assert decider.instructions(prompts, "pair", ["MERGE", "NOT_MERGE", "DISCARD"]) == (frozen / "phase2.txt").read_text()


def test_unavailable_split_disappears_from_prompt_and_schema():
    c = content(allowed=("KEEP", "DISCARD"))
    assert "SPLIT" not in c["instructions"] and c["schema"]["properties"]["action"]["enum"] == ["KEEP", "DISCARD"]
    assert 'one of "KEEP" or "DISCARD".' in c["instructions"]


@pytest.mark.parametrize("alias", sorted(PROFILES))
def test_body_is_chat_completions_with_schema_sampling_thinking_and_images(tmp_path, alias):
    profile = PROFILES[alias]
    server = FakeServer(profile, [answer("KEEP")])
    p = VllmProvider(profile, tmp_path / "ledger.jsonl", transport=server)
    assert p.check() == {"served_models": [profile["served_name"]], "version": "0.32.0"}
    c = content()
    assert p.respond(c, folder=tmp_path) == {"action": "KEEP", "rationale": "scripted"}
    b = server.bodies[0]
    assert b["model"] == profile["served_name"] and b["max_tokens"] == profile["max_tokens"]
    assert b["response_format"]["json_schema"]["schema"] == c["schema"] and b["response_format"]["json_schema"]["strict"]
    assert b["chat_template_kwargs"] == {"enable_thinking": True}
    assert all(b[k] == v for k, v in profile["sampling"].items())
    system, user = b["messages"]
    assert system == {"role": "system", "content": c["instructions"]} and user["content"][0]["text"] == c["text"]
    images = [base64.b64decode(part["image_url"]["url"].split(",", 1)[1]) for part in user["content"][1:]]
    assert images == c["images"]
    assert p.body(c)["seed"] == b["seed"]  # deterministic per request
    assert p.stats()["prompt_tokens"] == 3000 and (tmp_path / "response_1.json").exists()


def test_truncation_is_retried_once_with_the_identical_body_then_stops(tmp_path):
    profile = PROFILES["qwen27b"]
    server = FakeServer(profile, [answer("KEEP"), answer("KEEP")], finish=["length", "stop"])
    p = VllmProvider(profile, tmp_path / "ledger.jsonl", transport=server)
    assert p.respond(content(), folder=tmp_path)["action"] == "KEEP"
    assert server.bodies[0] == server.bodies[1] and p.truncations == 1
    server = FakeServer(profile, [answer("KEEP")] * 2, finish=["length", "length"])
    (tmp_path / "second").mkdir()
    with pytest.raises(ProviderStop):
        VllmProvider(profile, tmp_path / "l2.jsonl", transport=server).respond(content(), folder=tmp_path / "second")


def test_no_substitute_for_unparseable_answers_or_wrong_models(tmp_path):
    profile = PROFILES["gemma4"]
    with pytest.raises(ProviderStop):
        VllmProvider(profile, tmp_path / "a.jsonl", transport=FakeServer(profile, ["not json"])).respond(content(), folder=tmp_path)
    (tmp_path / "b").mkdir()
    with pytest.raises(ProviderStop):
        VllmProvider(profile, tmp_path / "b.jsonl", transport=FakeServer(profile, [answer("KEEP")], model="other")
                     ).respond(content(), folder=tmp_path / "b")


def test_token_budget_stops_before_the_next_request(tmp_path):
    profile = PROFILES["qwen27b"]
    server = FakeServer(profile, [answer("KEEP")] * 2)
    p = VllmProvider(profile, tmp_path / "ledger.jsonl", transport=server, max_total_tokens=3000)
    p.respond(content(), folder=tmp_path)
    with pytest.raises(LimitStop):
        p.respond(content(), folder=tmp_path)
    assert len(server.bodies) == 1


def test_model_decider_runs_controller_v2_end_to_end_offline(tmp_path):
    from src.cluster.manager import ClusterManager
    from src.pipeline.curation_controller_v2 import CurationControllerV2
    rng = np.random.default_rng(0)
    labels, over = np.array([1] * 30 + [3] * 20), np.array([1] * 15 + [2] * 15 + [3] * 20)
    waves = np.vstack([np.sin(np.linspace(0, np.pi, 30)) + rng.normal(scale=.05, size=(50, 30))])
    m = ClusterManager(labels, over, np.array([[1.], [2.], [1.], [0.]]), np.sort(rng.uniform(0, 100, 50)), waves)
    profile = PROFILES["qwen27b"]
    server = FakeServer(profile, [answer("KEEP"), answer("KEEP"), answer("NOT_MERGE")])
    d = decider.ModelDecider(VllmProvider(profile, tmp_path / "ledger.jsonl", transport=server))
    c = CurationControllerV2(m, "cM2-e004_004-006_CH3", tmp_path / "run", decider=d, sampling_rate=30000.)
    c.run()
    assert c.status == "completed" and len(server.bodies) == 3
    enums = [b["response_format"]["json_schema"]["schema"]["properties"]["action"]["enum"] for b in server.bodies]
    assert enums == [["KEEP", "SPLIT", "DISCARD"], ["KEEP", "DISCARD"], ["MERGE", "NOT_MERGE", "DISCARD"]]
    assert [len(b["messages"][1]["content"]) - 1 for b in server.bodies] == [4, 4, 3]
    assert (tmp_path / "run" / "step_00001" / "request_content.json").exists()
