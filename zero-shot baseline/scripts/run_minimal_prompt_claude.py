"""Minimal-prompt stepwise AND rollout with Claude Opus 5.5. Offline preflight unless --estimate/--execute.

Same protocol as scripts/run/run_minimal_prompt_baseline.py (Astra) except the provider layer:
  - identical prompt files, observation text, metric definitions, images (same order) and JSON schema:
    the OpenAI request is built by the original builder first and then translated field by field;
  - Anthropic Messages API: system = prompt file, user content = observation text + PNGs,
    thinking adaptive (cannot be disabled on Opus 5.5), effort "high", max_tokens 8000 (thinking included),
    structured output json_schema. Astra used 4000 but never came near it (max observed output 1,536 of
    3,047 calls); Qwen 9B was truncated at 4000, so the new runs use a non-binding 8000;
  - truncation (stop_reason "max_tokens"): the same request is retried automatically ONCE; every truncation
    and retry is counted in the status/report; a second truncation of that request stops the run;
  - no other retries, no server-side refusal fallback (a fallback model would answer instead of Opus 5.5
    and contaminate the baseline): refusal, parse failure or API error stops;
  - per-request token count + conservative reservation against an explicit, user-written budget;
  - optional pilot (--pilot-stepwise N) and resume (--prepare-resume-from SOURCE, then --execute --resume):
    a resumed run replays the source's saved responses locally, request hash by request hash, without
    re-billing, and continues live from the first request that has no accepted response.
The original Astra runner, its prompts and its results are not modified.
"""
from __future__ import annotations

import argparse
import base64
import copy
import importlib.metadata
import json
import math
from pathlib import Path
import signal
import sys
import types
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import h5py  # noqa: E402
import numpy as np  # noqa: E402
from scipy.io import loadmat  # noqa: E402
from scripts.analysis import curation_one_to_one  # noqa: E402
from scripts.run import run_minimal_prompt_baseline as rb  # noqa: E402
from scripts.run import run_curation_stage6 as infra  # noqa: E402
from src.pipeline.curation_engineering_v1 import ControllerStop, arrays_hash, digest, write_json  # noqa: E402

BASE, INDEX, DATASETS = rb.BASE, rb.INDEX, rb.DATASETS
sha256, observe, load_actor, require_dataset = rb.sha256, rb.observe, rb.load_actor, rb.require_dataset
SCOPE = "minimal-prompt-four-channel-stepwise-and-rollout-claude-opus-5-5-v1"
MODEL, EFFORT, MAX_TOKENS = "claude-opus-5-5", "high", 8000
TRUNCATION_RETRIES = 1  # automatic retries of the same request after stop_reason == "max_tokens"
EXECUTION_ARTIFACTS = {"authorize_execute.json", "execution_started.json", "execution_status.json", "estimate.json",
                       "responses", "provider_ledger.jsonl", "datasets", "stepwise_predictions.jsonl",
                       "score.json", "resume_source.json", "SUPERSEDED.md"}
PRICES = {
    "checked_on": "2026-10-12",
    "source": "Claude API skill model table (cached 2026-09-25); recheck in the Claude Console before paid use",
    "standard_usd_per_million": {"input": 4.0, "cache_write_5m": 5.0, "cache_read": 0.20, "output": 20.0},
    "reserve_input_multiplier": 1.10, "reserve_extra_input_tokens": 1024, "max_input_tokens": 200000,
    "note": "Usage estimates, not invoices.",
}


class ClaudeStop(RuntimeError):
    pass


class PilotStop(RuntimeError):
    """Planned stop after the requested number of stepwise requests; not an error."""


# ---------------------------------------------------------------- request -----------------------

def build_request(phase, obs, urls):
    """Translate the validated original request; prompt, observation, images and schema stay identical."""
    oa = rb.build_request(phase, obs, urls)
    parts = oa["input"][0]["content"]
    images = []
    for part in parts[1:]:
        prefix = "data:image/png;base64,"
        if not part["image_url"].startswith(prefix):
            raise ValueError("Expected PNG data URL")
        images.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                   "data": part["image_url"][len(prefix):]}})
    return dict(model=MODEL, max_tokens=MAX_TOKENS, system=oa["instructions"],
                thinking={"type": "adaptive"},
                output_config={"effort": EFFORT, "format": {"type": "json_schema",
                                                             "schema": oa["text"]["format"]["schema"]}},
                messages=[{"role": "user", "content": [{"type": "text", "text": parts[0]["text"]}, *images]}])


def save_request(folder, request, pngs, display):
    """Save every model-visible character; replace base64 with hashed local PNG references."""
    clean = copy.deepcopy(request)
    blocks = clean["messages"][0]["content"][1:]
    if len(blocks) != len(pngs):
        raise ValueError("Image count mismatch")
    for block, png, meta in zip(blocks, pngs, display["images"]):
        if base64.b64decode(block["source"]["data"], validate=True) != png:
            raise ValueError("Payload PNG differs from observation")
        if rb.hashlib.sha256(png).hexdigest() != meta["sha256"]:
            raise ValueError("Observation image hash mismatch")
        name = meta["name"] + ".png"
        with (folder / name).open("xb") as stream:
            stream.write(png)
        block["source"] = {"local_file": name, "sha256": meta["sha256"]}
    write_json(folder / "request_payload.json", clean)


def check_request(request):
    if (request.get("model") != MODEL or request.get("max_tokens") != MAX_TOKENS
            or request.get("thinking") != {"type": "adaptive"}
            or (request.get("output_config") or {}).get("effort") != EFFORT
            or (request.get("output_config") or {}).get("format", {}).get("type") != "json_schema"
            or "fallbacks" in request or "betas" in request):
        raise ClaudeStop("Unexpected model/effort/limit/format, or a fallback was requested")


# ---------------------------------------------------------------- accounting --------------------

def money(uncached_input, output, cache_write=0, cache_read=0, *, conservative=False):
    values = (uncached_input, output, cache_write, cache_read)
    if any(type(x) is not int or x < 0 for x in values):
        raise ClaudeStop("Invalid usage")
    p = PRICES["standard_usd_per_million"]
    input_rate = p["cache_write_5m"] if conservative else p["input"]
    return (uncached_input * input_rate + cache_write * p["cache_write_5m"]
            + cache_read * p["cache_read"] + output * p["output"]) / 1e6


def reserve(charged, limit, n_input, max_output=MAX_TOKENS):
    if type(n_input) is not int or n_input <= 0 or n_input > PRICES["max_input_tokens"]:
        raise ClaudeStop("Invalid or out-of-protocol input token count")
    padded = math.ceil(n_input * PRICES["reserve_input_multiplier"] + PRICES["reserve_extra_input_tokens"])
    amount = money(padded, max_output, conservative=True)
    if charged + amount > limit + 1e-12:
        raise ClaudeStop("Remaining budget cannot cover next request's reserved maximum")
    return amount


def usage_cost(raw):
    u = raw.get("usage")
    if not isinstance(u, dict) or not {"input_tokens", "output_tokens"} <= set(u):
        raise ClaudeStop("Missing usage; retain reservation")
    n, m = u["input_tokens"], u["output_tokens"]
    cw, cr = u.get("cache_creation_input_tokens") or 0, u.get("cache_read_input_tokens") or 0
    return {"input_tokens": n + cw + cr, "output_tokens": m, "cached_input_tokens": cr,
            "conservative_usd": money(n, m, cw, cr, conservative=True),
            "standard_estimate_usd": money(n, m, cw, cr)}


def decision_from_response(raw, phase):
    if raw.get("type") != "message" or raw.get("role") != "assistant":
        raise ClaudeStop("Not an assistant message")
    model = raw.get("model", "")
    if model != MODEL and not model.startswith(MODEL + "-"):
        raise ClaudeStop("Unexpected response model")
    if raw.get("stop_reason") != "end_turn":
        raise ClaudeStop(f"Stop reason {raw.get('stop_reason')!r}; no substitute action")
    texts = []
    for block in raw.get("content", []):
        if block.get("type") in ("thinking", "redacted_thinking"):
            continue
        if block.get("type") != "text" or not isinstance(block.get("text"), str):
            raise ClaudeStop("Unexpected content block")
        texts.append(block["text"])
    if len(texts) != 1:
        raise ClaudeStop("Expected one JSON answer")
    try:
        answer = json.loads(texts[0])
    except (ValueError, TypeError) as exc:
        raise ClaudeStop("Response JSON parse failed") from exc
    if (not isinstance(answer, dict) or set(answer) != {"action", "rationale"}
            or answer["action"] not in rb.ACTIONS[phase] or not isinstance(answer["rationale"], str)):
        raise ClaudeStop("Response schema invalid")
    return answer


accounting = types.SimpleNamespace(read_json=rb.accounting.read_json, read_jsonl=rb.accounting.read_jsonl,
                                   now=rb.accounting.now, decision_from_response=decision_from_response,
                                   reserve=reserve, usage_cost=usage_cost)


# ---------------------------------------------------------------- provider ----------------------

def make_client():
    """Only called after all local gates. Key from ANTHROPIC_API_KEY (.env loaded, not overriding)."""
    from dotenv import load_dotenv
    import anthropic
    load_dotenv(ROOT / ".env", override=False)
    return anthropic.Anthropic(max_retries=0, timeout=600.0)


def count_tokens(client, request):
    check_request(request)
    fields = {k: request[k] for k in ("model", "system", "messages", "thinking", "output_config")}
    n = client.messages.count_tokens(**fields).input_tokens
    if type(n) is not int or n <= 0 or n > PRICES["max_input_tokens"]:
        raise ClaudeStop("Invalid/long-context token count")
    return n


class LazyClient:
    """Builds the real client only at the first live call, so replay/pilot bookkeeping never needs a key."""

    def __init__(self, factory=None):
        self._factory, self._client = factory or make_client, None

    @property
    def messages(self):
        if self._client is None:
            self._client = self._factory()
        return self._client.messages


class ClaudeProvider:
    """count -> reserve -> call (one automatic retry after truncation) -> persist -> settle.

    replay: entries {source_call, request_sha256, input_tokens, raw} from a source run, consumed strictly
    in order and never billed again. inherited: the source's accounting, so the budget is cumulative.
    pilot_stepwise: stop (PilotStop) before the (N+1)-th stepwise request.
    """
    kind = "ANTHROPIC_MESSAGES_NO_FALLBACK_ONE_TRUNCATION_RETRY"

    def __init__(self, client, out, budget, *, replay=None, inherited=None, pilot_stepwise=None):
        self.client, self.out, self.budget = client, out, budget
        inherited = inherited or {}
        self.charged = float(inherited.get("conservative_usd", 0.))
        self.standard = float(inherited.get("standard_usd", 0.))
        self.tokens = dict(input_tokens=0, output_tokens=0, cached_input_tokens=0)
        for key, value in (inherited.get("tokens") or {}).items():
            if key in self.tokens:
                self.tokens[key] = int(value)
        self.calls = self.live_calls = self.replayed = 0
        self.pending, self.dataset = None, None
        self.expected_first, self.started_datasets = {}, set()
        self.replay = list(replay or [])
        self.pilot_stepwise, self.stepwise_requests = pilot_stepwise, 0
        self.truncations = []
        self.last_live_attempts, self.last_conservative = 0, 0.
        self.stop_kind = None

    @property
    def replaying_current(self):
        return self.pending is not None and self.pending[3]

    def count(self, request):
        if self.pending is not None:
            raise ClaudeStop("Unsettled previous request")
        check_request(request)
        h = digest(request)
        stepwise = bool(self.dataset) and self.dataset.endswith(":stepwise")
        if stepwise and self.pilot_stepwise is not None and self.stepwise_requests >= self.pilot_stepwise:
            self.stop_kind = "pilot"
            raise PilotStop(f"Pilot complete after {self.stepwise_requests} stepwise requests")
        if self.dataset not in self.started_datasets and self.dataset in self.expected_first:
            if h != self.expected_first[self.dataset]:
                raise ClaudeStop("Initial live request differs from offline preflight")
        started = monotonic()
        if self.replay:
            if self.replay[0]["request_sha256"] != h:
                self.stop_kind = "replay_diverged"
                raise ClaudeStop("Resume replay diverged from the source run")
            n, held, replaying, event = self.replay[0]["input_tokens"], 0., True, "count_replayed"
        else:
            n = count_tokens(self.client, request)
            try:
                held = reserve(self.charged, self.budget, n)
            except ClaudeStop:
                self.stop_kind = "budget"
                raise
            replaying, event = False, "count"
        if stepwise:
            self.stepwise_requests += 1
        self.pending = (h, held, n, replaying)
        infra.append(self.out, dict(event=event, dataset_id=self.dataset, request_sha256=h,
                                    input_tokens=n, seconds=monotonic() - started, at=accounting.now()))
        return n

    def respond(self, request):
        if self.pending is None or self.pending[0] != digest(request):
            raise ClaudeStop("Request differs from counted request")
        h, held, n, _ = self.pending
        self.last_live_attempts, self.last_conservative = 0, 0.
        self.started_datasets.add(self.dataset)
        for attempt in range(TRUNCATION_RETRIES + 1):
            raw = self._attempt(request, h, n, held if attempt == 0 else None)
            if raw.get("stop_reason") != "max_tokens":
                break
            retry = attempt < TRUNCATION_RETRIES
            self.truncations.append(dict(dataset_id=self.dataset, request_sha256=h, call=self.calls,
                                         attempt=attempt + 1, retried=retry,
                                         output_tokens=(raw.get("usage") or {}).get("output_tokens")))
            infra.append(self.out, dict(event="truncated", dataset_id=self.dataset, call=self.calls,
                                        request_sha256=h, attempt=attempt + 1, will_retry=retry, at=accounting.now()))
            if not retry:
                self.stop_kind = "truncated_after_retry"
        self.pending = None
        return raw  # a still-truncated answer is rejected by decision_from_response -> run stops

    def _attempt(self, request, h, n, held):
        if self.replay:
            entry = self.replay.pop(0)
            if entry["request_sha256"] != h:
                self.stop_kind = "replay_diverged"
                raise ClaudeStop("Resume replay diverged from the source run")
            self.calls += 1
            self.replayed += 1
            raw = entry["raw"]
            write_json(self.out / "responses" / f"{self.calls:06d}.json", raw)
            infra.append(self.out, dict(event="replayed", dataset_id=self.dataset, call=self.calls, request_sha256=h,
                                        source_call=entry["source_call"], stop_reason=raw.get("stop_reason"),
                                        at=accounting.now()))
            return raw
        if held is None:  # a retry, or the live remainder after replay, needs its own reservation
            try:
                held = reserve(self.charged, self.budget, n)
            except ClaudeStop:
                self.stop_kind = "budget"
                raise
        self.charged += held
        self.calls += 1
        self.live_calls += 1
        self.last_live_attempts += 1
        infra.append(self.out, dict(event="reserved", dataset_id=self.dataset, call=self.calls,
                     request_sha256=h, reserved_usd=held, cumulative_usd=self.charged, at=accounting.now()))
        started = monotonic()
        try:
            raw = self.client.messages.create(**request).model_dump(mode="json")
            write_json(self.out / "responses" / f"{self.calls:06d}.json", raw)  # persist before parsing
            cost = usage_cost(raw)
            self.charged += cost["conservative_usd"] - held
            self.standard += cost["standard_estimate_usd"]
            for key in self.tokens:
                self.tokens[key] += cost[key]
            self.last_conservative += cost["conservative_usd"]
            infra.append(self.out, dict(event="usage", dataset_id=self.dataset, call=self.calls, **cost,
                         stop_reason=raw.get("stop_reason"), cumulative_usd=self.charged,
                         seconds=monotonic() - started, at=accounting.now()))
            if cost["conservative_usd"] > held or self.charged > self.budget:
                self.stop_kind = "accounting"
                raise ClaudeStop("Reported usage exceeded reservation; stopping")
            return raw
        except BaseException as exc:
            infra.append(self.out, dict(event="failure", dataset_id=self.dataset, call=self.calls,
                         error_type=type(exc).__name__, status_code=getattr(exc, "status_code", None),
                         seconds=monotonic() - started, at=accounting.now(), automatic_retries=0))
            if self.stop_kind is None:
                self.stop_kind = "api_error"
            raise


class ClaudeController(rb.MinimalController):
    """Inherited scheduler/actions/checkpoints; ask() uses the Claude request, accounting and parser."""

    def ask(self, phase, cid, target=None, *, context=None):
        if self.status in {"stopped", "completed"}:
            raise ControllerStop("Controller finished")
        self.status = "running"
        try:
            key = (context or phase, int(cid), target, self.state_hash())
            if key in self.seen or self.calls >= self.max_calls:
                raise ControllerStop("Repeated state or safety call limit")
            if rb.prompt_hashes() != self.frozen:
                raise ControllerStop("Prompt changed during run")
            obs, urls, pngs, display = observe(self.manager, phase, cid, target, sampling_rate=self.fs, view=self.view)
            request = build_request(phase, obs, urls)
            folder = self.out / f"step_{self.calls + 1:05d}"
            folder.mkdir()
            save_request(folder, request, pngs, display)
            write_json(folder / "request.json", dict(phase=phase, context=context, observation=obs,
                       display=display, request_sha256=digest(request), state_sha256=key[-1]))
            n = self.provider.count(request)
            replayed = self.provider.replaying_current
            held = 0. if replayed else reserve(self.charged, self.budget, n)
            self.charged += held
            self.log("reserved", amount_usd=held, step=folder.name, replayed=replayed)
            self.calls += 1
            self.seen.add(key)
            raw = self.provider.respond(request)
            write_json(folder / "response.json", raw)
            spent = self.provider.last_conservative  # all live attempts of this request; 0 when replayed
            attempts = max(1, self.provider.last_live_attempts)
            self.charged += spent - held
            self.log("usage", **usage_cost(raw), conservative_usd_all_attempts=spent,
                     live_attempts=self.provider.last_live_attempts, replayed=replayed, step=folder.name)
            if spent > held * attempts + 1e-12 or self.charged > self.budget:
                raise ControllerStop("Usage exceeded reservation")
            decision = decision_from_response(raw, phase)
            self.log("decision", phase=phase, cid=int(cid), target=target, **decision)
            print(f"{self.dataset_id} {self.calls} {phase} {cid}->{target} {decision['action']} ${self.charged:.4f}", flush=True)
            return decision
        except BaseException as exc:
            self.status = "stopped"
            self.log("ABSTAIN_stop", error_type=type(exc).__name__, phase=phase, cid=int(cid),
                     state_sha256=self.state_hash(), reason="No fallback, retry, or substitute action")
            raise ControllerStop(f"Stopped: {type(exc).__name__}") from exc


# ---------------------------------------------------------------- campaign ----------------------

def sources():
    paths = [Path(__file__), Path(curation_one_to_one.__file__), curation_one_to_one.RULES]
    own = {str(p.resolve().relative_to(ROOT)): sha256(p) for p in paths}
    return {**rb.sources(), **own}


def environment():
    env = infra.environment()
    env["provider_packages"] = {p: importlib.metadata.version(p) for p in ("anthropic",)}
    return env


def prepare(out):
    hashes, rows = rb.prompt_hashes(), rb.selected_rows(DATASETS)
    out.mkdir(parents=True, exist_ok=False)
    records = []
    for row in rows:
        m, fs = load_actor(row)
        active = list(map(int, m.get_active_clusters()))
        record = dict(row, initial_clusters=len(active), n_spikes=len(m.assigns), sampling_rate_hz=fs)
        preview = out / "preflight" / row["dataset_id"]
        preview.mkdir(parents=True)
        if active:
            obs, urls, pngs, display = observe(m, "phase1", active[0], sampling_rate=fs)
            request = build_request("phase1", obs, urls)
            save_request(preview, request, pngs, display)
            record["first_request_sha256"] = digest(request)
            write_json(preview / "observation.json", dict(observation=obs, display=display, request_sha256=digest(request)))
        records.append(record)
        print(f"PREFLIGHT {row['dataset_id']}: {len(active)} groups; no API", flush=True)
        del m
    from scripts.analysis import minimal_stepwise
    stepwise = minimal_stepwise.prepare(out, rows, sys.modules[__name__])
    manifest = dict(version=SCOPE, created_at=accounting.now(), datasets=records, prompt_hashes=hashes,
        stepwise=stepwise, execution_order=f"all {stepwise['total']} eligible expert-state decisions, then CH31/CH3/CH20/CH30 autonomous rollouts",
        code_hashes=sources(), environment=environment(), index_sha256=sha256(INDEX),
        provider="anthropic-messages", model=MODEL, effort=EFFORT, thinking="adaptive (cannot be disabled on this model)",
        max_tokens=MAX_TOKENS, retries=0, truncation_retries=TRUNCATION_RETRIES,
        output_limit_note="Astra ran at 4000 and never exceeded 1,536 output tokens (3,047 calls); Qwen 9B was truncated "
                          "at 4000, so new runs use a non-binding 8000. A truncated request is retried once automatically "
                          "and counted; a second truncation stops the run.",
        refusal_fallback="disabled: a fallback model would contaminate the baseline; refusal stops",
        skill=None, legacy_system=False, observation="raw_member_v1", max_calls_per_dataset=10000,
        scheduler="Inherited engineering_v1 via the Astra minimal runner: recursive split, phase2 prevalidation and all eligible targets",
        auto_discard_threshold=0, final_minimum_threshold=0, small_cluster_threshold=4000,
        same_as_astra_run="prompt files, observation text, definitions, image order, JSON schema, controller, datasets, evaluators",
        differs_from_astra_run="provider/model, effort semantics, image tokenisation, max_tokens includes adaptive thinking",
        pricing=PRICES, scoring_sha256=sha256(curation_one_to_one.RULES),
        evaluation="Completed terminals only, scored against this run's own terminal and registered MAT. Primary: "
                   "one-to-one (curation_one_to_one, IoU>=0.5, full-output P/R/F1 incl. unmatched clusters, all expert units). "
                   "Supplementary: historical many-to-one (not comparable). Exposed development data, not an independent test.")
    write_json(out / "manifest.json", manifest)
    write_json(out / "preflight.json", dict(manifest_sha256=sha256(out / "manifest.json"), api_calls=0, status="offline-passed"))
    return manifest


def verify(out):
    m = accounting.read_json(out / "manifest.json")
    if m["version"] != SCOPE or accounting.read_json(out / "preflight.json")["manifest_sha256"] != sha256(out / "manifest.json"):
        raise ValueError("Manifest changed")
    if m["prompt_hashes"] != rb.prompt_hashes() or m["code_hashes"] != sources() or m["environment"] != environment():
        raise ValueError("Frozen prompt/code/dependencies changed")
    if m["index_sha256"] != sha256(INDEX):
        raise ValueError("Dataset index changed")
    if m["scoring_sha256"] != sha256(curation_one_to_one.RULES):
        raise ValueError("Pre-registered scoring rules changed")
    rows = rb.selected_rows([r["dataset_id"] for r in m["datasets"]])
    for name, h in m["stepwise"]["sources"].items():
        if sha256(ROOT / name) != h:
            raise ValueError("Stepwise source changed")
    if sha256(out / "stepwise_targets.jsonl") != m["stepwise"]["targets_sha256"]:
        raise ValueError("Stepwise scoring targets changed")
    if any(any(r[k] != saved[k] for k in r) for r, saved in zip(rows, m["datasets"])):
        raise ValueError("Dataset changed")
    return m


def authorize(out, budget):
    if type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0:
        raise ValueError("Explicit finite budget required")
    if (out / "execution_started.json").exists():
        raise ValueError("Already started; no automatic restart")
    path = out / "authorize_execute.json"
    if path.is_symlink():
        raise ValueError("Authorization symlink prohibited")
    a = accounting.read_json(path)
    if (type(a.get("budget_usd")) not in (int, float) or a["budget_usd"] != budget or a.get("scope") != SCOPE
            or a.get("manifest_sha256") != sha256(out / "manifest.json")):
        raise ValueError("Authorization mismatch")


def estimate(out):
    """Count tokens for every stepwise request and the four first rollout requests. No inference."""
    m = verify(out)
    if (out / "estimate.json").exists() or (out / "execution_started.json").exists():
        raise ValueError("Estimate already exists or run started")
    from scripts.analysis import minimal_stepwise
    client = make_client()
    counts = []
    for job in m["stepwise"]["jobs"]:
        counts.append(count_tokens(client, minimal_stepwise.rebuild(out, job, sys.modules[__name__])))
    first = []
    for row in m["datasets"]:
        payload = accounting.read_json(out / "preflight" / row["dataset_id"] / "request_payload.json")
        req = rebuild_preflight(out, row["dataset_id"], payload)
        first.append(count_tokens(client, req))
    mean_in = float(np.mean(counts + first))
    astra = dict(rollout_calls=2753, mean_output_tokens=293)  # Astra minimal run: 893,804 output / 3,048 calls
    # Stepwise is a fixed set of 294 counted requests: reserving every one at max_tokens bounds its cost
    # (given these prices). The rollout has no such bound: Opus chooses its own actions, so call count,
    # image sizes and output length can all differ from Astra. Only scenario figures are given for it.
    stepwise_reserved_max = sum(money(math.ceil(n * 1.1 + 1024), MAX_TOKENS, conservative=True) for n in counts)
    stepwise_at_astra_output = sum(money(n, astra["mean_output_tokens"]) for n in counts)
    rollout_at_astra_output = astra["rollout_calls"] * money(round(mean_in), astra["mean_output_tokens"])
    rollout_at_max_output = astra["rollout_calls"] * money(math.ceil(mean_in * 1.1 + 1024), MAX_TOKENS, conservative=True)
    result = dict(at=accounting.now(), manifest_sha256=sha256(out / "manifest.json"), inference_calls=0,
                  counted_requests=len(counts) + len(first), mean_input_tokens=mean_in,
                  stepwise=dict(requests=len(counts),
                                usd_if_output_matches_astra_mean=stepwise_at_astra_output,
                                usd_if_every_request_reserved_at_max_tokens=stepwise_reserved_max),
                  rollout_scenarios=dict(
                      not_an_upper_bound="Scenarios assume Astra's 2,753 rollout calls; Opus may make more or fewer calls. "
                                         "Per-request reservation stops the run at the budget but cannot guarantee completion.",
                      calls_assumed=astra["rollout_calls"],
                      usd_if_output_matches_astra_mean=rollout_at_astra_output,
                      usd_if_every_call_reserved_at_max_tokens=rollout_at_max_output),
                  pricing=PRICES)
    write_json(out / "estimate.json", result)
    print(json.dumps({k: result[k] for k in ("mean_input_tokens", "stepwise", "rollout_scenarios")}, indent=2))
    return result


def rebuild_preflight(out, dataset_id, payload):
    req = copy.deepcopy(payload)
    for block in req["messages"][0]["content"][1:]:
        ref = block["source"]
        data = (out / "preflight" / dataset_id / ref["local_file"]).read_bytes()
        if rb.hashlib.sha256(data).hexdigest() != ref["sha256"]:
            raise ValueError("Preflight image changed")
        block["source"] = {"type": "base64", "media_type": "image/png", "data": base64.b64encode(data).decode()}
    return req


def collect(out):
    """Score ONLY this run's own terminals against the MAT registered in this run's manifest."""
    from scripts.analysis.collect_curation_stage6 import evaluate, deletion_audit
    from scripts.analysis import minimal_stepwise
    from score_terminal_matching import report_lines
    m = verify(out)
    status = accounting.read_json(out / "execution_status.json")
    results = []
    for row in m["datasets"]:
        folder = out / "datasets" / row["dataset_id"]
        r = dict(dataset_id=row["dataset_id"], status="not-started", final_metrics=None)
        if (folder / "summary.json").exists():
            r.update(accounting.read_json(folder / "summary.json"))
            if r["status"] == "completed" and r["terminal_evaluation_allowed"]:
                checkpoint = folder / "terminal.npz"
                if sha256(checkpoint) != accounting.read_json(checkpoint.with_suffix(".json"))["sha256"]:
                    raise ValueError("Terminal corrupted")
                with np.load(checkpoint, allow_pickle=False) as a:
                    final = a["assigns"].copy()
                    if arrays_hash(final, a["tree"]) != r["final_sha256"]:
                        raise ValueError("Final state mismatch")
                actor, fs = load_actor(row)
                path = ROOT / row["mat_path"]
                if h5py.is_hdf5(path):
                    with h5py.File(path, "r") as f:
                        gt = f["spikes/curation/assigns"][:].flatten()
                else:
                    gt = np.asarray(loadmat(str(path), struct_as_record=False, squeeze_me=True,
                                            variable_names=['spikes'])['spikes'].curation.assigns).reshape(-1)
                r["one_to_one"] = curation_one_to_one.evaluate(final, gt)
                r["no_curation_one_to_one"] = curation_one_to_one.evaluate(actor.assigns, gt)
                r["final_metrics"] = r["many_to_one"] = evaluate(final, gt, actor.spike_times, fs)
                r["no_curation_metrics"] = r["no_curation_many_to_one"] = evaluate(actor.assigns, gt, actor.spike_times, fs)
                r["deletions_containing_expert_spikes"] = deletion_audit(folder, accounting.read_jsonl(folder / "events.jsonl"), gt)
                del actor
        results.append(r)
    dest = BASE / "results" / out.name
    dest.mkdir(parents=True, exist_ok=False)
    stepwise = minimal_stepwise.collect(out, m["stepwise"], sys.modules[__name__])
    write_json(out / "score.json", dict(status=status, stepwise=stepwise, datasets=results,
                                        manifest_sha256=sha256(out / "manifest.json")))
    complete = [r for r in results if r.get("one_to_one")]
    lines = ["# Minimal-prompt baseline — Claude Opus 5.5", "",
             f"Run: `{out.name}`; status: **{status['status']}**.", "",
             "Claude Opus 5.5 / effort high / adaptive thinking; same minimal prompts, observations, images, schema and "
             "controller as the Astra run; no domain skill, examples, feedback or refusal fallback. Exposed development channels.",
             f"Manifest SHA256: `{sha256(out / 'manifest.json')}`.",
             f"Calls: {status['inference_calls']}; known standard estimate: ${status['standard_estimate_known_responses_usd']:.6f}; "
             f"conservative usage/reservations: ${status['conservative_charged_or_reserved_usd']:.6f}.",
             f"Output limit {MAX_TOKENS} tokens (thinking included). Truncations: "
             f"{(status.get('truncations') or {}).get('count', 0)} (retried automatically once: "
             f"{(status.get('truncations') or {}).get('retried', 0)}; stopped after retry: "
             f"{(status.get('truncations') or {}).get('stopped_after_retry', 0)}). "
             f"Live calls {status.get('live_calls', 0)}, replayed from a source run {status.get('replayed_calls', 0)}"
             f"{'; resumed from `' + Path(status['inherited_from_source'] and accounting.read_json(out / 'resume_source.json')['source']).name + '`' if status.get('inherited_from_source') else ''}.",
             "", "## Stepwise action evaluation", "",
             "Given expert pre-action states and source/target objects; predictions never advance this trajectory. "
             "No KEEP/NOT_MERGE reference labels; MERGE positives only.",
             "", "| Dataset | Valid/planned | Correct | Full accuracy |", "|---|---:|---:|---:|"]
    for d, s in stepwise["by_dataset"].items():
        lines.append(f"| {d} | {s['valid']}/{s['planned']} | {s['correct']} | {s['accuracy']} |")
    for phase, s in stepwise["by_phase"].items():
        lines += ["", f"### {phase}", "", "| Action | Evaluated support | Precision | Recall | F1 |", "|---|---:|---:|---:|---:|"]
        for a, c in s["per_class"].items():
            lines.append(f"| {a} | {c['evaluated_support']} | {c['precision']} | {c['recall']} | {c['f1']} |")
    lines += ["", "## Autonomous rollout", "", "Dataset status: " +
              ", ".join(f"{r['dataset_id']} {r['status']}" for r in results) + ". Only completed datasets are scored.", ""]
    lines += report_lines("Terminal scores", complete)[3:] if complete else ["No completed rollout to score."]
    lines += ["", "Partial runs are not final scores. Not a SOTA claim. No automatic prompt update or retry.",
              f"Stop/error: `{status['error']}`. Local records: `../../runs/{out.name}/`."]
    with (dest / "REPORT.md").open("x") as f:
        f.write("\n".join(lines) + "\n")
    return results


# ---------------------------------------------------------------- resume ------------------------

def phase_index(source):
    """request_sha256 -> phase for every stepwise job and every rollout step saved in a run."""
    m = accounting.read_json(source / "manifest.json")
    phases = {j["request_sha256"]: j["phase"] for j in m["stepwise"]["jobs"]}
    for path in sorted((source / "datasets").glob("*/step_*/request.json")):
        r = accounting.read_json(path)
        phases[r["request_sha256"]] = r["phase"]
    return phases


def plan_replay(ledger, responses, phases):
    """Attempts with saved responses, in call order; drop the tail request unless it ended accepted.

    ledger: provider_ledger rows; responses: {call: raw}; phases: {request_sha256: phase}.
    """
    counted, attempts = {}, []
    for e in ledger:
        if e["event"] in ("count", "count_replayed"):
            counted[e["request_sha256"]] = e["input_tokens"]
        elif e["event"] in ("reserved", "replayed"):
            attempts.append(dict(source_call=e["call"], request_sha256=e["request_sha256"],
                                 input_tokens=counted[e["request_sha256"]]))
    plan = []
    for a in attempts:
        if a["source_call"] not in responses:
            break  # transport failure: nothing after it can be replayed
        plan.append(dict(a, raw=responses[a["source_call"]]))
    if plan:
        tail, ok = plan[-1]["request_sha256"], True
        try:
            decision_from_response(plan[-1]["raw"], phases[tail])
        except (ClaudeStop, KeyError):
            ok = False
        if not ok:
            while plan and plan[-1]["request_sha256"] == tail:
                plan.pop()
    return plan


def prepare_resume(source, out):
    """Offline: copy the frozen inputs of a stopped run and record what will be replayed for free."""
    import shutil
    source = source.resolve()
    verify(source)
    status = accounting.read_json(source / "execution_status.json")
    if status["status"] == "completed":
        raise ValueError("Source run completed; nothing to resume")
    ledger = accounting.read_jsonl(source / "provider_ledger.jsonl")
    responses = {int(p.stem): accounting.read_json(p) for p in (source / "responses").glob("*.json")}
    plan = plan_replay(ledger, responses, phase_index(source))
    shutil.copytree(source, out, ignore=lambda d, names: [n for n in names
                    if Path(d).resolve() == source and n in EXECUTION_ARTIFACTS])
    verify(out)
    info = dict(created_at=accounting.now(), source=str(source), source_status=status["status"],
                source_error=status["error"], source_stop_kind=status.get("stop_kind"),
                source_manifest_sha256=sha256(source / "manifest.json"), replay_calls=len(plan),
                replay=[dict(source_call=e["source_call"], request_sha256=e["request_sha256"],
                             input_tokens=e["input_tokens"],
                             response_sha256=sha256(source / "responses" / f"{e['source_call']:06d}.json"))
                        for e in plan],
                inherited=dict(conservative_usd=status["conservative_charged_or_reserved_usd"],
                               standard_usd=status["standard_estimate_known_responses_usd"],
                               tokens=status.get("tokens") or {}),
                note="Replayed responses are not billed again; the budget in the new authorization is cumulative.")
    write_json(out / "resume_source.json", info)
    print(f"RESUME PREPARED from {source.name}: {len(plan)} attempts replayed locally; "
          f"inherited conservative ${info['inherited']['conservative_usd']:.4f}; no API")
    return info


def load_replay(out):
    info = accounting.read_json(out / "resume_source.json")
    source = Path(info["source"])
    if (sha256(source / "manifest.json") != info["source_manifest_sha256"]
            or sha256(out / "manifest.json") != info["source_manifest_sha256"]):
        raise ValueError("Source and resumed manifests differ")
    entries = []
    for r in info["replay"]:
        path = source / "responses" / f"{r['source_call']:06d}.json"
        if sha256(path) != r["response_sha256"]:
            raise ValueError("Saved source response changed")
        entries.append(dict(r, raw=accounting.read_json(path)))
    return info, entries


# ---------------------------------------------------------------- execute -----------------------

def execute(out, budget, *, resume=False, pilot_stepwise=None):
    m = verify(out)
    authorize(out, budget)
    resume_file = out / "resume_source.json"
    if resume != resume_file.exists():
        raise ValueError("--resume must be used exactly for directories made by --prepare-resume-from")
    info, replay = load_replay(out) if resume else (None, [])
    inherited = info["inherited"] if info else None
    if inherited and budget <= inherited["conservative_usd"]:
        raise ValueError("Cumulative budget must exceed what the source run already used")
    if pilot_stepwise is not None and (type(pilot_stepwise) is not int or pilot_stepwise < 1):
        raise ValueError("Pilot size must be a positive integer")
    write_json(out / "execution_started.json", dict(at=accounting.now(), budget_usd=budget,
               manifest_sha256=sha256(out / "manifest.json"), authorization_sha256=sha256(out / "authorize_execute.json"),
               resume_source=info["source"] if info else None, replay_calls=len(replay), pilot_stepwise=pilot_stepwise))
    (out / "responses").mkdir()
    provider, completed, status, error, stepwise_accounting = None, [], "stopped", None, None
    started = monotonic()
    try:
        provider = ClaudeProvider(LazyClient(), out, budget, replay=replay, inherited=inherited,
                                  pilot_stepwise=pilot_stepwise)
        from scripts.analysis import minimal_stepwise
        try:
            minimal_stepwise.execute(out, m["stepwise"], provider, sys.modules[__name__])
        finally:
            stepwise_accounting = dict(calls=provider.calls, conservative_usd=provider.charged,
                                       standard_usd=provider.standard, tokens=copy.deepcopy(provider.tokens),
                                       seconds=monotonic() - started)
        provider.expected_first = {r["dataset_id"]: r["first_request_sha256"] for r in m["datasets"] if "first_request_sha256" in r}
        for row in m["datasets"]:
            verify(out)
            actor, fs = load_actor(row)
            provider.dataset = row["dataset_id"]
            c = ClaudeController(actor, row["dataset_id"], out / "datasets" / row["dataset_id"],
                                 provider=provider, budget_usd=max(0., budget - provider.charged), sampling_rate=fs)
            c.run()
            completed.append(row["dataset_id"])
            print(f"COMPLETE {row['dataset_id']}; campaign ${provider.charged:.6f}", flush=True)
            del c, actor
        status = "completed"
    except PilotStop:
        status = "pilot-stopped"
        print(f"PILOT STOPPED after {provider.stepwise_requests} stepwise requests; campaign ${provider.charged:.6f}", flush=True)
    except BaseException as exc:
        error = dict(type=type(exc).__name__, cause=type(exc.__cause__).__name__ if exc.__cause__ else None,
                     message=str(exc)[:200] if isinstance(exc, (ClaudeStop, ControllerStop, ValueError)) else None)
        raise
    finally:
        p = provider
        write_json(out / "execution_status.json", dict(status=status, error=error, stop_kind=p.stop_kind if p else None,
                   completed_datasets=completed, inference_calls=p.calls if p else 0,
                   live_calls=p.live_calls if p else 0, replayed_calls=p.replayed if p else 0,
                   unconsumed_replay=len(p.replay) if p else 0,
                   truncations=dict(count=len(p.truncations), retried=sum(t["retried"] for t in p.truncations),
                                    stopped_after_retry=sum(not t["retried"] for t in p.truncations),
                                    events=p.truncations) if p else None,
                   tokens=p.tokens if p else {},
                   conservative_charged_or_reserved_usd=p.charged if p else 0,
                   standard_estimate_known_responses_usd=p.standard if p else 0,
                   inherited_from_source=inherited, stepwise_accounting=stepwise_accounting,
                   seconds=monotonic() - started, at=accounting.now()))
        collect(out)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output-root", required=True)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--estimate", action="store_true", help="count tokens only (sends requests to Anthropic, no inference)")
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--prepare-resume-from", metavar="SOURCE_RUN", help="offline: new run dir that replays SOURCE_RUN")
    p.add_argument("--resume", action="store_true", help="with --execute, for a directory made by --prepare-resume-from")
    p.add_argument("--pilot-stepwise", type=int, help="with --execute: stop after N stepwise requests (resumable)")
    p.add_argument("--budget-usd", type=float)
    args = p.parse_args()
    out = rb.output_path(args.output_root)
    if (args.resume or args.pilot_stepwise is not None) and not args.execute:
        raise ValueError("--resume / --pilot-stepwise only apply to --execute")
    if args.execute:
        def stop(signum, frame):
            raise KeyboardInterrupt("Explicit process stop")
        signal.signal(signal.SIGTERM, stop)
        execute(out, args.budget_usd, resume=args.resume, pilot_stepwise=args.pilot_stepwise)
    elif args.estimate:
        estimate(out)
    elif args.prepare_resume_from:
        prepare_resume(rb.output_path(args.prepare_resume_from), out)
    else:
        if args.budget_usd is not None:
            raise ValueError("Dry run needs no budget")
        prepare(out)
        print(f"OFFLINE PASSED; manifest {sha256(out / 'manifest.json')}")


if __name__ == "__main__":
    main()
