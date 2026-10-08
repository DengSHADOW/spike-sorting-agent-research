"""Offline tests only. Fake responses never constitute model evaluation."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.run import run_curation_stage6 as run
from scripts.analysis.collect_curation_stage6 import evaluate
from src.cluster.manager import ClusterManager
from src.pipeline import curation_engineering_v1 as core


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network/keys/client in offline tests")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr(run, "make_client", forbidden)
    monkeypatch.setattr(run.replay, "client_factory", forbidden)


def request():
    return dict(model="gpt-6-astra", reasoning={"effort": "high"}, max_output_tokens=4000,
                service_tier="default", instructions="fixture", input=[], text={})


def test_installed_dependency_inventory_without_sdk_client():
    metadata = run.environment()
    assert metadata["packages"]["openai"]
    assert metadata["packages"]["httpx2"]


def response(action="KEEP"):
    return dict(status="completed", model="gpt-6-astra", reasoning={"effort": "high"},
        output=[dict(type="message", role="assistant", status="completed", content=[dict(
            type="output_text", text=json.dumps(dict(action=action, rationale="SCRIPTED TEST ONLY")))])],
        usage=dict(input_tokens=100, output_tokens=10, input_tokens_details=dict(cached_tokens=0)))


class FakeClient:
    def __init__(self, actions=("KEEP",), fail=False):
        self.actions, self.fail, self.calls = iter(actions), fail, 0
        self.responses = SimpleNamespace(input_tokens=SimpleNamespace(count=lambda **kw: SimpleNamespace(input_tokens=100)),
                                         create=self.create)

    def create(self, **kw):
        self.calls += 1
        if self.fail:
            raise RuntimeError("transport failure")
        raw = response(next(self.actions))
        return SimpleNamespace(model_dump=lambda **kw: raw)


def provider(tmp_path, budget=1, **kw):
    (tmp_path / "responses").mkdir()
    return run.LiveProvider(FakeClient(**kw), tmp_path, budget)


def test_budget_prevents_inference(tmp_path):
    p = provider(tmp_path, budget=.001)
    with pytest.raises(run.replay.BudgetStop):
        p.count(request())
    assert p.client.calls == 0 and p.charged == 0


def test_budget_shared_across_datasets(tmp_path):
    p = provider(tmp_path, budget=run.replay.reservation(100), actions=("KEEP", "KEEP"))
    p.dataset = run.DATASETS[0]
    p.count(request()); p.respond(request())
    p.dataset = run.DATASETS[1]
    with pytest.raises(run.replay.BudgetStop):
        p.count(request())
    assert p.client.calls == 1


def test_failure_retains_reservation_no_retry(tmp_path):
    p = provider(tmp_path, fail=True)
    p.count(request())
    with pytest.raises(RuntimeError):
        p.respond(request())
    assert p.client.calls == 1 and p.charged == run.replay.reservation(100)
    rows = run.replay.read_jsonl(tmp_path / "provider_ledger.jsonl")
    assert [r["event"] for r in rows] == ["count", "reserved", "failure"]


def test_request_must_match_counted_hash(tmp_path):
    p = provider(tmp_path)
    p.count(request())
    changed = dict(request(), instructions="changed")
    with pytest.raises(ValueError, match="differs"):
        p.respond(changed)
    assert p.client.calls == 0


def test_first_request_matches_dryrun_before_counting(tmp_path):
    p = provider(tmp_path)
    p.dataset = run.DATASETS[0]
    p.expected_first[p.dataset] = "wrong"
    with pytest.raises(ValueError, match="preflight"):
        p.count(request())
    assert not (tmp_path / "provider_ledger.jsonl").exists()


def test_incomplete_response_saved_then_controller_stops(tmp_path):
    p = provider(tmp_path)
    raw = response()
    raw["status"] = "incomplete"
    p.client.responses.create = lambda **kw: SimpleNamespace(model_dump=lambda **kw: raw)
    c = core.CurationController(tiny_manager(), run.DATASETS[0], tmp_path / "controller",
                               provider=p, budget_usd=1, max_calls=10)
    before = c.state_hash()
    with pytest.raises(core.ControllerStop):
        c.run()
    assert c.state_hash() == before and c.status == "stopped"
    assert (tmp_path / "responses/000001.json").exists()
    assert not run.replay.read_json(c.out / "summary.json")["terminal_evaluation_allowed"]


@pytest.mark.parametrize("mode", ["missing", "wrong-budget", "wrong-hash", "wrong-scope", "started"])
def test_authorization_before_client(tmp_path, monkeypatch, mode):
    run.write_json(tmp_path / "manifest.json", {})
    monkeypatch.setattr(run, "verify", lambda out: {})
    if mode != "missing":
        auth = dict(budget_usd=50, manifest_sha256=run.sha256(tmp_path / "manifest.json"),
                    scope="stage6-development-full-rollout")
        if mode == "wrong-budget": auth["budget_usd"] = 25
        if mode == "wrong-hash": auth["manifest_sha256"] = "bad"
        if mode == "wrong-scope": auth["scope"] = "image-ab"
        run.write_json(tmp_path / "authorize_execute.json", auth)
    if mode == "started":
        run.write_json(tmp_path / "execution_started.json", {})
    with pytest.raises((ValueError, FileNotFoundError)):
        run.execute(tmp_path, 50)


def test_reserved_scope_rejected_before_any_file(monkeypatch):
    monkeypatch.setattr(run.replay, "read_json", lambda *a: pytest.fail("Should reject before index read"))
    with pytest.raises(ValueError):
        run.selected_rows([run.DATASETS[0], "cM2-e007_012-017_CH1"])


def test_dry_run_no_sdk_no_secret_and_immutable_output(tmp_path, monkeypatch):
    row = dict(dataset_id=run.DATASETS[0], mat_path="fixture.mat", mat_sha256="fixture")
    monkeypatch.setattr(run, "selected_rows", lambda ids: [row])
    monkeypatch.setattr(run, "load_actor", lambda r: (tiny_manager(), 30000.))
    monkeypatch.setattr(run, "sources", lambda: {})
    monkeypatch.setattr(run, "environment", lambda: {})
    out = tmp_path / "dry"
    manifest = run.prepare(out, [run.DATASETS[0]])
    assert manifest["api_calls"] == 0
    assert manifest["datasets"][0]["initial_clusters"] == 2
    with pytest.raises(FileExistsError):
        run.prepare(out, [run.DATASETS[0]])


def tiny_manager():
    return ClusterManager(np.array([1, 1, 3, 3]), np.array([1, 2, 3, 3]),
        np.array([[1.], [2.], [.9], [0.]]), np.arange(4)*.01,
        np.random.default_rng(4).normal(size=(4, 12)))


def test_full_controller_through_live_adapter_offline(tmp_path):
    # SPLIT creates a new state which is observed, not a prerecorded next example.
    p = provider(tmp_path, budget=5, actions=("SPLIT", "KEEP", "DISCARD", "KEEP"))
    c = core.CurationController(tiny_manager(), run.DATASETS[0], tmp_path / "controller",
        provider=p, budget_usd=5, max_calls=10)
    c.run()
    assert c.status == "completed" and p.calls == c.calls == 4
    assert np.count_nonzero(c.manager.assigns == 0) == 1
    assert (c.out / "terminal.npz").exists()
    assert len(list((tmp_path / "responses").glob("*.json"))) == 4
    for folder in sorted(c.out.glob("step_*")):
        saved = run.replay.read_json(folder / "request.json")
        assert "curation_assigns" not in json.dumps(saved)
        for item in saved["display"]["images"]:
            assert (folder / (item["name"] + ".png")).is_file()


def test_terminal_metrics_and_empty_prediction():
    gt = np.array([1, 1, 2, 2, 0])
    times = np.arange(5)*.01
    correct = evaluate(gt, gt, times, 30000.)
    assert correct["overall_f1_score"] == 1
    empty = evaluate(np.zeros(5, dtype=int), gt, times, 30000.)
    assert empty["overall_f1_score"] == 0 and empty["expert_spikes_discarded"] == 4
    # Deliberately disclose many-to-one matching: fragmentation isn't penalized.
    fragmented = evaluate(np.array([1, 3, 2, 2, 0]), gt, times, 30000.)
    assert fragmented["overall_f1_score"] == 1


def test_frozen_code_change_rejected(tmp_path, monkeypatch):
    manifest = dict(config_hashes=run.config_hashes(), code_hashes={"a": "old"})
    run.write_json(tmp_path / "manifest.json", manifest)
    run.write_json(tmp_path / "preflight.json", dict(manifest_sha256=run.sha256(tmp_path / "manifest.json")))
    monkeypatch.setattr(run, "sources", lambda: {"a": "new"})
    with pytest.raises(ValueError, match="code changed"):
        run.verify(tmp_path)
