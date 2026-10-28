import json
import socket
import threading

import numpy as np
import pytest

from src.agent import curation_decider_v2 as dec
from src.agent.vllm_provider import VllmProvider
from src.cluster.manager import ClusterManager
from src.pipeline.curation_controller_v2 import RoundsController

PROFILES = json.loads((dec.ROOT / "configs/controller_v2/models.json").read_text())["models"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network permitted")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


class SchemaServer:
    """Stands in for vLLM: answers any observe-then-act schema with the first label of every field and an
    action chosen by `rule(kind, allowed)`; records bodies; thread-safe."""

    def __init__(self, profile, rule):
        self.name, self.rule, self.bodies, self.lock = profile["served_name"], rule, [], threading.Lock()

    def __call__(self, method, path, body=None):
        if path == "/v1/models":
            return {"data": [{"id": self.name}]}
        if path == "/version":
            return {"version": "0.32.0"}
        with self.lock:
            self.bodies.append(body)
        props = body["response_format"]["json_schema"]["schema"]["properties"]
        kind = "single" if "waveform" in props else "pair"
        answer = {k: {"label": v["properties"]["label"]["enum"][0], "note": "scripted"}
                  for k, v in props.items() if k not in ("action", "rationale")}
        answer.update(action=self.rule(kind, props["action"]["enum"]), rationale="scripted, not a model")
        return {"model": self.name, "usage": {"prompt_tokens": 4000, "completion_tokens": 300},
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}


def manager():
    rng = np.random.default_rng(0)
    x = np.linspace(0, 1, 30)
    shapes = {1: -np.sin(np.pi * x), 2: -np.sin(np.pi * x), 3: np.cos(np.pi * x), 4: -.9 * np.sin(np.pi * x)}
    sizes = {1: 300, 2: 200, 3: 150, 4: 120}
    over = np.concatenate([[k] * n for k, n in sizes.items()])
    labels = np.where(over == 2, 1, over)
    waves = np.vstack([shapes[k] + rng.normal(scale=.05, size=(n, 30)) for k, n in sizes.items()])
    tree = np.array([[1.], [2.], [.9], [0.]])
    return ClusterManager(labels, over, tree, np.sort(rng.uniform(0, 600, len(over))), waves)


def test_instructions_carry_contract_scoped_rules_offered_actions_and_ordered_fields():
    skill, _ = dec.load_skill()
    contract = dec.CONTRACT.read_text()
    single = dec.instructions_v2(contract, skill, "single", ["KEEP", "DISCARD"])
    assert single.startswith(contract.rstrip()) and "[S1]" in single and "[P1]" not in single
    assert "SPLIT:" not in single and "split_preview" not in single
    pair = dec.instructions_v2(contract, skill, "pair", ["MERGE", "NOT_MERGE", "DISCARD"])
    assert "[P1]" in pair and "[S1]" not in pair
    s = dec.schema_v2("single", ["KEEP", "DISCARD", "SPLIT"])
    assert list(s["properties"]) == ["waveform", "refractory", "amplitude_time", "tree", "split_preview", "action",
                                     "rationale"]
    assert s["properties"]["action"]["enum"] == ["KEEP", "SPLIT", "DISCARD"]
    assert list(dec.schema_v2("pair", ["MERGE", "NOT_MERGE", "DISCARD"])["properties"])[:4] == \
        ["overlay", "ccg", "amplitude_time", "source_validity"]


def test_answers_outside_the_schema_are_rejected():
    good = {"overlay": {"label": "similar", "note": "x"}, "ccg": {"label": "contaminated", "note": "x"},
            "amplitude_time": {"label": "separate", "note": "x"}, "source_validity": {"label": "valid", "note": "x"},
            "action": "NOT_MERGE", "rationale": "x"}
    allowed = ["MERGE", "NOT_MERGE", "DISCARD"]
    assert dec.check_answer(dict(good), "pair", allowed)["action"] == "NOT_MERGE"
    for bad in ({**good, "ccg": {"label": "fine", "note": "x"}}, {k: v for k, v in good.items() if k != "ccg"},
                {**good, "action": "KEEP"}, {}):
        with pytest.raises(ValueError):
            dec.check_answer(bad, "pair", allowed)


def test_rounds_skill_decider_and_provider_run_end_to_end_in_parallel(tmp_path):
    profile = PROFILES["qwen27b"]
    rule = lambda kind, allowed: "KEEP" if kind == "single" else "NOT_MERGE"
    server = SchemaServer(profile, rule)
    provider = VllmProvider(profile, tmp_path / "ledger.jsonl", transport=server)
    d = dec.SkillDecider(provider)
    c = RoundsController(manager(), "cM2-e004_004-006_CH31", tmp_path / "run", decider=d, sampling_rate=30000.,
                         parallel=3, manifest=d.describe())
    c.run()
    assert c.status == "completed" and provider.calls == len(server.bodies) == c.calls
    assert len((tmp_path / "ledger.jsonl").read_text().splitlines()) == c.calls
    first = server.bodies[0]["messages"][1]["content"]
    assert first[0]["type"] == "text" and first[0]["text"].startswith("Single-cluster judgement")
    assert first[-1]["type"] == "text" and "measurements" in first[-1]["text"]
    n_images = sorted({len(b["messages"][1]["content"]) - 2 for b in server.bodies})
    assert n_images == [3, 4, 5]  # pair, single without SPLIT, single with split preview
    events = [json.loads(line) for line in (c.out / "events.jsonl").read_text().splitlines()]
    decisions = [e for e in events if e["event"] == "decision"]
    assert all(e["observations"] and all(set(v) == {"label", "note"} for v in e["observations"].values())
               for e in decisions)
    manifest = json.loads((c.out / "manifest.json").read_text())
    request = json.loads((c.out / "step_00001" / "request_content.json").read_text())
    assert manifest["skill_sha256"] == request["skill_sha256"] == d.skill_sha256
    assert (c.out / "step_00001" / "split_preview.png").exists()
