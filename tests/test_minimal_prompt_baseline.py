"""All tests offline; scripted responses are not experiment results."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.run import run_minimal_prompt_baseline as run
from src.agent import curation_contract as legacy
from src.agent.curation_observation_v1 import metrics
from src.cluster.manager import ClusterManager


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("Network, keys, or legacy prompt code was used")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr(run.infra, "make_client", forbidden)
    monkeypatch.setattr(run.accounting, "client_factory", forbidden)
    monkeypatch.setattr(legacy, "build_request", forbidden)
    monkeypatch.setattr(legacy, "load_protocol", forbidden)
    monkeypatch.setattr(legacy, "config_hashes", forbidden)
    original = Path.open
    def guarded(path, *a, **kw):
        if path.parent.name == "curation" and path.name in {"system_v1.txt", "skill_v0.json", "skill_v1.json", "protocol_v1.json"}:
            forbidden()
        if path.name == ".env":
            forbidden()
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, "open", guarded)


def manager(large=False):
    n = 4000 if large else 4
    return ClusterManager(np.array([1]*n + [3]*3), np.array([1]*(n//2) + [2]*(n-n//2) + [3]*3),
        np.array([[1.], [2.], [.9], [0.]]), np.arange(n+3)*.01,
        np.random.default_rng(12).normal(size=(n+3, 12)))


def raw(action="KEEP"):
    return dict(status="completed", model="gpt-6-astra", reasoning={"effort": "high"}, service_tier="default",
        output=[dict(type="message", role="assistant", status="completed", content=[dict(type="output_text",
            text=json.dumps(dict(action=action, rationale="SCRIPTED OFFLINE RESPONSE")))])],
        usage=dict(input_tokens=100, output_tokens=10, input_tokens_details=dict(cached_tokens=0)))


class Scripted:
    def __init__(self, actions):
        self.actions, self.requests, self.calls = iter(actions), [], 0
    def count(self, request):
        self.requests.append(request)
        return 100
    def respond(self, request):
        self.calls += 1
        return raw(next(self.actions))


@pytest.fixture
def fast(monkeypatch):
    def observation(m, phase, cid, target=None, **kw):
        names = run.VIEWS[phase]
        return metrics(m, phase, cid, target), ["data:image/png;base64,YQ=="]*len(names), [b"a"]*len(names), {
            "images": [dict(name=n, sha256=hashlib.sha256(b"a").hexdigest()) for n in names]}
    monkeypatch.setattr(run, "observe", observation)


@pytest.mark.parametrize("phase", ["phase1", "phase2"])
def test_only_approved_prompt_and_observation(phase):
    obs = metrics(manager(), phase, 1, 3 if phase == "phase2" else None)
    req = run.build_request(phase, obs, ["data:image/png;base64,YQ=="]*len(run.VIEWS[phase]))
    assert req["instructions"] == (run.PROMPTS / f"{phase}.txt").read_text()
    assert len(req["input"]) == 1 and req["input"][0]["role"] == "user"
    payload = json.loads(req["input"][0]["content"][0]["text"])
    assert set(payload) == {"metrics", "definitions"}
    assert set(payload["metrics"]) == run.FIELDS[phase]
    assert not {"tools", "previous_response_id", "conversation"} & set(req)
    assert "DOMAIN SKILL" not in json.dumps(req)


def test_answer_field_rejected():
    obs = dict(metrics(manager(), "phase1", 1), expert_action="DISCARD")
    with pytest.raises(ValueError, match="observation"):
        run.build_request("phase1", obs, ["data:image/png;base64,YQ=="]*4)


def test_prompt_corruption_rejected(tmp_path, monkeypatch):
    for name in ("SHA256SUMS", "phase1.txt", "phase2.txt"):
        (tmp_path / name).write_bytes((run.PROMPTS / name).read_bytes())
    (tmp_path / "phase1.txt").write_text("changed")
    monkeypatch.setattr(run, "PROMPTS", tmp_path)
    with pytest.raises(ValueError, match="hash"):
        run.prompt_hashes()


def test_dryrun_never_loads_legacy_or_keys(tmp_path, monkeypatch, fast):
    from scripts.analysis import minimal_stepwise
    monkeypatch.setattr(minimal_stepwise, "prepare", lambda *a: {"jobs": [], "total": 0})
    row = dict(dataset_id=run.DATASETS[0], mat_path="fixture", mat_sha256="fixture")
    monkeypatch.setattr(run, "selected_rows", lambda ds: [row])
    monkeypatch.setattr(run, "load_actor", lambda r: (manager(), 30000.))
    monkeypatch.setattr(run, "sources", lambda: {})
    monkeypatch.setattr(run.infra, "environment", lambda: {})
    out = tmp_path / "dry"
    manifest = run.prepare(out, [run.DATASETS[0]])
    assert manifest["skill"] is None and manifest["legacy_system"] is False
    assert run.accounting.read_json(out / "preflight.json")["api_calls"] == 0
    with pytest.raises(FileExistsError):
        run.prepare(out, [run.DATASETS[0]])


def test_scope_before_index(monkeypatch):
    monkeypatch.setattr(run.accounting, "read_json", lambda p: pytest.fail("Index opened"))
    with pytest.raises(ValueError):
        run.selected_rows([run.DATASETS[0], "cM2-e004_004-006_CH1"])


@pytest.mark.parametrize("bad", ["missing", "budget", "scope", "hash", "started"])
def test_authorization_before_client(tmp_path, monkeypatch, bad):
    run.write_json(tmp_path / "manifest.json", {})
    monkeypatch.setattr(run, "verify", lambda out: {})
    if bad != "missing":
        auth = dict(scope=run.SCOPE, budget_usd=20, manifest_sha256=run.sha256(tmp_path / "manifest.json"))
        if bad == "budget": auth["budget_usd"] = 50
        if bad == "scope": auth["scope"] = "old-stage6"
        if bad == "hash": auth["manifest_sha256"] = "bad"
        run.write_json(tmp_path / "authorize_execute.json", auth)
    if bad == "started": run.write_json(tmp_path / "execution_started.json", {})
    with pytest.raises((ValueError, FileNotFoundError)):
        run.execute(tmp_path, 20)


@pytest.mark.parametrize("last", ["MERGE", "NOT_MERGE", "DISCARD"])
def test_full_scheduler_minimal_requests(tmp_path, fast, last):
    p = Scripted(["KEEP", "KEEP", "KEEP", last])
    c = run.MinimalController(manager(True), run.DATASETS[0], tmp_path / "run", provider=p,
                             budget_usd=10, sampling_rate=30000.)
    c.run()
    assert c.status == "completed" and c.calls == 4
    assert all(r["instructions"] in [(run.PROMPTS / f"{s}.txt").read_text() for s in run.ACTIONS] for r in p.requests)
    if last == "NOT_MERGE": assert np.count_nonzero(c.manager.assigns == 3) == 3


def test_split_children_reobserved(tmp_path, fast):
    p = Scripted(["SPLIT", "KEEP", "DISCARD", "KEEP"])
    c = run.MinimalController(manager(), run.DATASETS[0], tmp_path / "run", provider=p, budget_usd=10, sampling_rate=30000.)
    c.run()
    assert c.calls == 4 and np.count_nonzero(c.manager.assigns == 0) == 2
    obs = [json.loads(r["input"][0]["content"][0]["text"])["metrics"] for r in p.requests]
    assert obs[0]["n_spikes"] == 4 and obs[1]["n_spikes"] == 2


@pytest.mark.parametrize("mode", ["budget", "parse", "truncated", "transport"])
def test_stop_not_delete(tmp_path, fast, mode):
    p = Scripted(["KEEP"])
    c = run.MinimalController(manager(), run.DATASETS[0], tmp_path / "run", provider=p,
                             budget_usd=0 if mode == "budget" else 10, sampling_rate=30000.)
    before = c.state_hash()
    response = raw()
    if mode == "parse": response["output"][0]["content"][0]["text"] = "bad"
    if mode == "truncated": response["status"] = "incomplete"
    if mode in {"parse", "truncated"}: p.respond = lambda r: response
    if mode == "transport":
        def fail(req): raise RuntimeError("offline failure")
        p.respond = fail
    with pytest.raises(run.ControllerStop): c.run()
    assert c.status == "stopped" and c.state_hash() == before
    assert not run.accounting.read_json(c.out / "summary.json")["terminal_evaluation_allowed"]


def test_image_bytes_verified(tmp_path):
    req = run.build_request("phase1", metrics(manager(), "phase1", 1), ["data:image/png;base64,YQ=="]*4)
    with pytest.raises(ValueError, match="PNG"):
        run.save_request(tmp_path, req, [b"wrong"]*4, {"images": [{"name": n} for n in run.VIEWS["phase1"]]})


def test_stepwise_reference_and_partial_scoring():
    from scripts.analysis import minimal_stepwise as sw
    assert sw.parse('s 12') == ('SPLIT', 12, None)
    assert sw.parse('m 0 9') == ('DISCARD', 9, None)
    assert sw.parse('m 8 9') == ('MERGE', 9, 8)
    with pytest.raises(ValueError): sw.parse('KEEP 3')
    targets = [dict(job_id=str(i), expert_action=a) for i, a in enumerate(['DISCARD', 'SPLIT', 'MERGE'])]
    preds = [dict(job_id='0', action='KEEP'), dict(job_id='1', action='SPLIT')]
    s = sw.score(targets, preds)
    assert s['accuracy'] is None and s['accuracy_on_valid'] == .5
    assert s['coverage'] == 2/3 and s['missing'] == 1
    assert s['per_class']['DISCARD']['recall'] == 0
    assert s['per_class']['KEEP']['recall'] is None
    assert s['confusion_matrix']['DISCARD']['KEEP'] == 1
    preds.append(dict(job_id='2', action='MERGE'))
    assert sw.score(targets, preds)['accuracy'] == 2/3
    with pytest.raises(ValueError): sw.score(targets, preds + preds[:1])


def test_stepwise_image_and_request_integrity(tmp_path, fast):
    from scripts.analysis import minimal_stepwise as sw
    obs, urls, pngs, display = run.observe(manager(), 'phase1', 1)
    req = run.build_request('phase1', obs, urls)
    folder = tmp_path / 'job'
    folder.mkdir()
    run.save_request(folder, req, pngs, display)
    run.write_json(folder / 'observation.json', obs)
    job = dict(folder='job', phase='phase1', images=display['images'], request_sha256=run.digest(req),
        payload_sha256=run.sha256(folder / 'request_payload.json'),
        observation_sha256=run.sha256(folder / 'observation.json'))
    assert sw.rebuild(tmp_path, job, run) == req
    (folder / (display['images'][0]['name'] + '.png')).write_bytes(b'changed')
    with pytest.raises(ValueError, match='image changed'): sw.rebuild(tmp_path, job, run)


def test_stepwise_actor_never_reads_answer(tmp_path, monkeypatch):
    from scripts.analysis import minimal_stepwise as sw
    monkeypatch.setattr(sw, 'rebuild', lambda *a: {})
    provider = Scripted(['KEEP'])
    provider.charged = 0
    # No targets file exists; predictions need neither labels nor expert reasons.
    sw.execute(tmp_path, dict(total=1, jobs=[dict(job_id='one', dataset_id=run.DATASETS[0], phase='phase1')]), provider, run)
    assert run.accounting.read_jsonl(tmp_path / 'stepwise_predictions.jsonl')[0]['action'] == 'KEEP'


def test_mat_v5_actor_does_not_access_curation(tmp_path, monkeypatch):
    class Spikes:
        Fs = 30000.
        spiketimes = np.arange(7) * .01
        waveforms = np.ones((12, 7))
        hierarchy = SimpleNamespace(assigns=np.array([1]*4 + [3]*3), tree=np.array([[1.], [2.], [.9], [0.]]))
        overcluster = SimpleNamespace(assigns=np.array([1, 1, 2, 2, 3, 3, 3]))
        @property
        def curation(self): raise AssertionError('Actor accessed labels')
    monkeypatch.setattr(run.h5py, 'is_hdf5', lambda p: False)
    monkeypatch.setattr(run, 'loadmat', lambda *a, **kw: {'spikes': Spikes()})
    monkeypatch.setattr(run, 'sha256', lambda p: 'fixture')
    m, fs = run.load_actor(dict(dataset_id=run.DATASETS[0], mat_path='fixture', mat_sha256='fixture'))
    assert m.waveforms.shape == (7, 12) and fs == 30000


def test_noise_recovery_only_for_reference_reconstruction():
    from scripts.analysis import minimal_stepwise as sw
    m = manager()
    original = m.assigns.copy()
    sw.apply_expert(m, 'DISCARD', 1, None)
    with pytest.raises(ValueError, match='Inactive'): sw.apply_expert(m, 'SPLIT', 0, None)
    sw.apply_expert(m, 'SPLIT', 0, None, reconstruction_only=True)
    assert np.array_equal(original, m.assigns)
