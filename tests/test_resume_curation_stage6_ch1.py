"""Recovery tests use synthetic observations/responses; never contact an API."""
import copy
import json
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.run import resume_curation_stage6_ch1 as resume
from src.pipeline.curation_engineering_v1 import CurationController, ControllerStop


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("Network or key/client access during offline test")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr(resume.base, "make_client", forbidden)


def manager():
    return resume.base.ClusterManager(np.array([1, 1, 2, 2]), np.array([1, 1, 2, 2]),
        np.empty((4, 0)), np.arange(4)*.01, np.random.default_rng(4).normal(size=(4, 12)))


def raw(action="KEEP"):
    return dict(status="completed", model="gpt-6-astra", reasoning={"effort":"high"},
        output=[dict(type="message", role="assistant", status="completed", content=[dict(
            type="output_text", text=json.dumps(dict(action=action, rationale="OFFLINE FIXTURE")))])],
        usage=dict(input_tokens=100, output_tokens=10, input_tokens_details=dict(cached_tokens=0)))


class FakeClient:
    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail
        self.responses = SimpleNamespace(
            input_tokens=SimpleNamespace(count=lambda **kw: SimpleNamespace(input_tokens=100)),
            create=self.create)

    def create(self, **kw):
        self.calls += 1
        if self.fail:
            raise RuntimeError("transport")
        return SimpleNamespace(model_dump=lambda **kw: raw())


def live(folder, budget=1, fail=False):
    folder.mkdir()
    (folder/"responses").mkdir()
    p = resume.base.LiveProvider(FakeClient(fail), folder, budget)
    p.dataset = resume.DATASET
    return p


def recorded(tmp_path):
    p = live(tmp_path/"reference-provider")
    c = CurationController(manager(), resume.DATASET, tmp_path/"reference",
                           provider=p, budget_usd=1, max_calls=10)
    c.run()
    records = [dict(request=resume.read(c.out/f"step_{i:05d}/request.json"),
                    raw=resume.read(c.out/f"step_{i:05d}/response.json") if i==1 else None)
               for i in (1,2)]
    return c, records


def request_for(record, folder):
    import base64
    saved = record["request"]
    urls = ["data:image/png;base64," + base64.b64encode(
        (folder/(i["name"]+".png")).read_bytes()).decode() for i in saved["display"]["images"]]
    r = resume.base.build_request(saved["phase"], saved["observation"], urls, skill_version="v0")
    r["service_tier"] = "default"
    return r


def test_offline_replay_reaches_identical_boundary_without_client(tmp_path):
    ref, records = recorded(tmp_path)
    p = resume.PrefixProvider(records)
    c = resume.PrefixController(manager(), resume.DATASET, tmp_path/"offline",
                               provider=p, budget_usd=1, max_calls=10)
    with pytest.raises(ControllerStop) as e:
        c.run()
    assert isinstance(e.value.__cause__, resume.ReplayBoundary)
    assert p.index == 1 and p.boundary_verified and p.live is None
    assert c.state_hash() == records[-1]["request"]["state_sha256"]
    assert resume.read(c.out/"step_00002/request.json")["request_sha256"] == records[-1]["request"]["request_sha256"]


@pytest.mark.parametrize("change", ["state", "request"])
def test_mismatch_stops_before_lazy_client(tmp_path, change):
    _, records = recorded(tmp_path)
    records[0]["request"]["state_sha256" if change=="state" else "request_sha256"] = "tampered"
    p = resume.PrefixProvider(records, lambda: pytest.fail("Client created"))
    c = resume.PrefixController(manager(), resume.DATASET, tmp_path/"mismatch",
                               provider=p, budget_usd=1, max_calls=10)
    with pytest.raises((ValueError, ControllerStop)):
        c.run()
    assert p.live is None and p.index == 0


def test_only_missing_decision_is_sent_and_unknown_reserve_is_retained(tmp_path):
    reference, records = recorded(tmp_path)
    cost = resume.base.replay.usage_cost(records[0]["raw"])["conservative_usd"]
    unknown = resume.base.replay.reservation(100)
    inherited = cost + unknown
    factories = []
    def factory():
        p = live(tmp_path/"new-provider")
        p.calls, p.charged = 2, inherited
        factories.append(p)
        return p
    p = resume.PrefixProvider(records, factory)
    c = resume.PrefixController(manager(), resume.DATASET, tmp_path/"continued",
                               provider=p, budget_usd=1-unknown, max_calls=10)
    c.run()
    assert c.status == "completed" and c.calls == 2
    assert c.state_hash() == reference.state_hash()
    assert len(factories)==1 and p.live.client.calls==1
    assert p.live.calls == 3 and p.index == 2
    assert p.live.charged == pytest.approx(inherited+cost)
    assert c.charged + unknown == pytest.approx(p.live.charged)
    ledger = resume.base.replay.read_jsonl(p.live.out/"provider_ledger.jsonl")
    assert [x["dataset_id"] for x in ledger] == [resume.DATASET]*3


def test_insufficient_remaining_budget_never_infers(tmp_path):
    _, records = recorded(tmp_path)
    cost = resume.base.replay.usage_cost(records[0]["raw"])["conservative_usd"]
    unknown = resume.base.replay.reservation(100)
    # Enough to replay the old prefix virtually, but not reserve another live call.
    budget = 2*unknown + cost/2
    def factory():
        p = live(tmp_path/"limited-provider", budget=budget)
        p.calls, p.charged = 2, unknown+cost
        return p
    p = resume.PrefixProvider(records, factory)
    c = resume.PrefixController(manager(), resume.DATASET, tmp_path/"limited",
                               provider=p, budget_usd=budget-unknown, max_calls=10)
    with pytest.raises(ControllerStop):
        c.run()
    assert p.live is not None and p.live.client.calls == 0
    assert p.live.charged == pytest.approx(unknown+cost)


def test_live_transport_failure_is_saved_and_not_retried(tmp_path):
    _, records = recorded(tmp_path)
    p = resume.PrefixProvider(records, lambda: live(tmp_path/"failed-provider", fail=True))
    c = resume.PrefixController(manager(), resume.DATASET, tmp_path/"failed",
                               provider=p, budget_usd=1, max_calls=10)
    with pytest.raises(ControllerStop):
        c.run()
    assert p.live.client.calls==1 and p.index==1 and c.status=="stopped"
    assert resume.read(c.out/"summary.json")["terminal_evaluation_allowed"] is False
    ledger = resume.base.replay.read_jsonl(p.live.out/"provider_ledger.jsonl")
    assert ledger[-1]["event"] == "failure"


@pytest.mark.parametrize("mode", ["missing","budget","scope","dataset","hash","retry","started"])
def test_authorization_rejects_invalid_scope_or_repeat(tmp_path, mode):
    resume.base.write_json(tmp_path/"manifest.json", {"fixture":True})
    m = {"cumulative_budget_usd":50, "explicitly_reissued_unresolved_call":532}
    auth = dict(scope=resume.SCOPE, dataset_id=resume.DATASET, budget_usd=50,
                manifest_sha256=resume.base.sha256(tmp_path/"manifest.json"), retry_unresolved_call=532)
    key = {"budget":"budget_usd","scope":"scope","dataset":"dataset_id","hash":"manifest_sha256","retry":"retry_unresolved_call"}
    if mode in key: auth[key[mode]] = "wrong"
    if mode != "missing": resume.base.write_json(tmp_path/"authorize_execute.json", auth)
    if mode == "started": resume.base.write_json(tmp_path/"execution_started.json", {})
    with pytest.raises((ValueError, FileNotFoundError)):
        resume.authorize(tmp_path, 50, m)


def test_valid_authorization_keeps_old_cap(tmp_path):
    resume.base.write_json(tmp_path/"manifest.json", {"fixture":True})
    m = {"cumulative_budget_usd":50, "explicitly_reissued_unresolved_call":532}
    resume.base.write_json(tmp_path/"authorize_execute.json", dict(
        scope=resume.SCOPE, dataset_id=resume.DATASET, budget_usd=50,
        manifest_sha256=resume.base.sha256(tmp_path/"manifest.json"), retry_unresolved_call=532))
    resume.authorize(tmp_path, 50, m)
    with pytest.raises(ValueError):
        resume.authorize(tmp_path, 100, m)
