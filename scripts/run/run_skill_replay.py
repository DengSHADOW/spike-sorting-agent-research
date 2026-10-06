"""Frozen skill replay: offline by default; network modes require explicit gates.

No existing experiment is overwritten. A new dry-run directory may later receive
one estimate and one authorized execution. Batch is preparation/estimate-only.
"""
from __future__ import annotations

import argparse
import ast
import base64
from collections import Counter
import copy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.agent import curation_contract as contract

DEFAULT_CASES = ROOT / "output/curation_skill_preparation_20261006"
SCORING = ROOT / "configs/curation/replay_scoring_v1.json"
FROZEN = ("system_v1.txt", "skill_v0.json", "skill_v1.json", "protocol_v1.json")
CODE = ("src/agent/curation_contract.py", "scripts/run/run_skill_replay.py",
        "scripts/analysis/score_skill_replay.py")
PRICES = {
    "checked_on": "2026-10-06",
    "source": "https://developers.openai.com/api/docs/models/gpt-6-astra",
    "standard_usd_per_million": {"input": 10, "cached_input": 1, "cache_write": 12.5, "output": 50},
    "batch_multiplier": 0.5,
    "short_context_max": 272000,
    "reserve_input_multiplier": 1.10,
    "reserve_extra_input_tokens": 1024,
    "note": "Public direct-API rates, no regional/priority uplift. Usage estimates, not invoices. Recheck before paid use.",
}
DISCLAIMER = ("Targeted, dependent development cases: diagnostic only, not a general accuracy benchmark. "
              "Historical three-view cases and local expert cases are stratified; no pooled accuracy. "
              "Phase 2 expert cases also have three images. Fixed-state proxy harms are not rollout outcomes.")


class ReplayStop(RuntimeError):
    pass


class BudgetStop(ReplayStop):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path):
    return contract.sha256(Path(path))


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ReplayStop("Duplicate JSON key")
        obj[key] = value
    return obj


def parse_json(text):
    def bad_constant(_):
        raise ReplayStop("Non-finite JSON value")
    return json.loads(text, object_pairs_hook=_unique, parse_constant=bad_constant)


def read_json(path):
    return parse_json(Path(path).read_text(encoding="utf-8"))


def read_jsonl(path):
    return [parse_json(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def write_new(path, data):
    with Path(path).open("x", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def log_row(stream, row):
    stream.write(canonical(row).decode() + "\n")
    stream.flush()
    # Persist reservation before the network side effect.
    import os
    os.fsync(stream.fileno())


def safe_path(base, relative, scope):
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ReplayStop("Absolute/traversing input path")
    path = (base / rel).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ReplayStop("Input symlink escapes allowed directory")
    for block in scope["reserved_after_prior_exposure"] + scope["quarantined_blocks"]:
        if block in str(path):
            raise ReplayStop("Reserved/quarantined path must not be opened")
    return path


def source_root(path):
    path = Path(path).resolve()
    if not path.is_relative_to((ROOT / "output").resolve()):
        raise ReplayStop("Case directory must be under this repository's output/")
    scope = contract.load_protocol()["scope"]
    for block in scope["reserved_after_prior_exposure"] + scope["quarantined_blocks"]:
        if block in str(path):
            raise ReplayStop("Reserved/quarantined case directory")
    return path


def load_cases(case_dir, rules):
    case_dir = source_root(case_dir)
    cases = read_jsonl(safe_path(case_dir, "audit_cases.jsonl", contract.load_protocol()["scope"]))
    if not cases:
        raise ReplayStop("No cases")
    seen = set()
    # Validate ALL dataset scopes before opening any referenced files.
    for c in cases:
        contract.require_development(c["dataset_id"])
        cid = c["case_id"]
        if not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", cid) or cid in seen:
            raise ReplayStop("Invalid or duplicate case ID")
        seen.add(cid)
        if c["phase"] not in contract.ACTIONS:
            raise ReplayStop("Unknown phase")
        if c["target_type"] == "recorded_expert_edit":
            if c["expert_action"] not in contract.ACTIONS[c["phase"]]:
                raise ReplayStop("Invalid expert action")
        elif c["target_type"] == "derived_terminal_constraint":
            if c["phase"] != "phase1" or c["expert_action"] is not None:
                raise ReplayStop("Derived cases are not unique expert-action labels")
            kind = c["constraint"]
            if kind not in rules["constraints"]:
                raise ReplayStop("Unregistered constraint")
            comp = c["composition"]
            if not comp or any(not k.isdigit() or type(n) is not int or n <= 0 for k, n in comp.items()):
                raise ReplayStop("Invalid terminal composition")
            n = sum(comp.values())
            positive = n - comp.get("0", 0)
            if n != c["n_spikes"] or positive != c["expert_positive_spikes"]:
                raise ReplayStop("Composition count mismatch")
            expected = ("discard_loses_expert_spikes_next_action_not_unique" if positive == n else
                        "all_members_terminal_noise_next_action_not_unique" if positive == 0 else
                        "mixed_terminal_membership_next_action_not_unique")
            if kind != expected:
                raise ReplayStop("Composition does not match constraint")
            groups = ("harmful_actions", "preferred_actions", "tolerable_not_ideal_actions", "acceptable_actions")
            actions = [a for key in groups for a in rules["constraints"][kind][key]]
            if sorted(actions) != sorted(contract.ACTIONS["phase1"]):
                raise ReplayStop("Constraint actions must partition all phase1 actions")
        else:
            raise ReplayStop("Unknown target type")
    return cases


def sdk_capabilities():
    """Inspect installed source, without importing SDK (its import reads env)."""
    try:
        dist = importlib.metadata.distribution('openai')
        def methods(relative, class_name):
            tree = ast.parse(Path(dist.locate_file(relative)).read_text(encoding='utf-8'))
            cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
            return {n.name: n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        batches = methods('openai/resources/batches.py', 'Batches')
        counters = methods('openai/resources/responses/input_tokens.py', 'InputTokens')
        count = counters.get('count')
        params = sorted(a.arg for a in (*count.args.posonlyargs, *count.args.args, *count.args.kwonlyargs)) if count else []
        return {"version": dist.version,
                "batch_supported": all(x in batches for x in ("create", "retrieve")),
                "input_token_count_supported": count is not None,
                "count_parameters": params}
    except (importlib.metadata.PackageNotFoundError, OSError, SyntaxError, StopIteration):
        return {"version": None, "batch_supported": False, "input_token_count_supported": False,
                "count_parameters": []}


def observation_from_preview(request):
    text = request["input"][0]["content"][0]["text"]
    if text.count("\nOBSERVATION\n") != 1:
        raise ReplayStop("Observation delimiter mismatch")
    observed = parse_json(text.split("\nOBSERVATION\n", 1)[1])
    metrics = {}
    for key, value in observed["metrics"].items():
        if key in contract.RATES:
            if not isinstance(value, dict) or set(value) != {"fraction", "percent"}:
                raise ReplayStop("ISI fraction/percent missing")
            frac = value["fraction"]
            if value["percent"] != (None if frac is None else frac * 100):
                raise ReplayStop("ISI unit mismatch")
            value = frac
        metrics[key] = value
    return observed, metrics


def without_skill(request):
    request = copy.deepcopy(request)
    block = request["input"][0]["content"][0]
    if not block["text"].startswith("DOMAIN SKILL\n") or block["text"].count("\nOBSERVATION\n") != 1:
        raise ReplayStop("Malformed skill block")
    block["text"] = "DOMAIN SKILL\n<SKILL>\nOBSERVATION\n" + block["text"].split("\nOBSERVATION\n", 1)[1]
    return request


def case_requests(case_dir, case, hashes, source_hashes):
    scope = contract.load_protocol()["scope"]
    urls = []
    for item in case["images"]:
        path = safe_path(ROOT, item["path"], scope)
        if not path.is_relative_to((ROOT / "output").resolve()) or path.suffix.lower() != ".png":
            raise ReplayStop("Only saved output PNG images allowed")
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != item["sha256"] or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ReplayStop("Image hash/type mismatch")
        source_hashes[str(path)] = actual
        urls.append("data:image/png;base64," + base64.b64encode(raw).decode())
    requests = {}
    for version in ("v0", "v1"):
        expected_name = f"actor/{case['case_id']}_{version}.json"
        if expected_name not in case["request_previews"] or len(case["request_previews"]) != 2:
            raise ReplayStop("Preview name/version mismatch")
        path = safe_path(case_dir, expected_name, scope)
        preview = read_json(path)
        source_hashes[str(path)] = file_hash(path)
        if preview["sendable"] is not False or preview["api_calls"] != 0:
            raise ReplayStop("Expected non-sendable offline preview")
        if preview["images"] != case["images"]:
            raise ReplayStop("Preview/audit image mismatch")
        if any(preview["config_hashes"].get(k) != hashes[k] for k in FROZEN):
            raise ReplayStop("Frozen config changed since preparation")
        saved = preview["request_preview"]
        observed, metrics = observation_from_preview(saved)
        if observed["phase"] != case["phase"]:
            raise ReplayStop("Preview phase mismatch")
        request = contract.build_request(case["phase"], metrics, urls,
                                         skill_version=version, layout=case["image_layout"])
        comparison = copy.deepcopy(request)
        for content, item in zip(comparison["input"][0]["content"][1:], case["images"]):
            content["image_url"] = "LOCAL_PREVIEW_ONLY:" + item["path"]
        if canonical(comparison) != canonical(saved):
            raise ReplayStop("Rebuilt request differs from frozen preview")
        # Same explicit transport tier for both versions; no prompt/schema change.
        request["service_tier"] = "default"
        requests[version] = request
    if canonical(without_skill(requests["v0"])) != canonical(without_skill(requests["v1"])):
        raise ReplayStop("v0/v1 differ outside the skill block")
    return requests


def build_plan(case_dir, versions=("v0", "v1"), repeats=1, seed=20261006, batch=False):
    if not versions or len(set(versions)) != len(versions) or set(versions) - {"v0", "v1"}:
        raise ReplayStop("Versions must be unique v0/v1")
    if type(repeats) is not int or repeats < 1:
        raise ReplayStop("Repeats must be a positive integer")
    case_dir = source_root(case_dir)
    rules = read_json(SCORING)
    cases = load_cases(case_dir, rules)
    hashes = {name: file_hash(contract.CONFIG_DIR / name) for name in FROZEN}
    protocol = contract.load_protocol()
    if protocol["model"] != {"name": "gpt-6-astra", "reasoning_effort": "high",
                             "max_output_tokens": 4000, "endpoint": "responses"}:
        raise ReplayStop("Model protocol outside this priced replay implementation")
    summary = read_json(safe_path(case_dir, "summary.json", protocol["scope"]))
    if any(summary["config_hashes"].get(k) != hashes[k] for k in FROZEN):
        raise ReplayStop("Preparation config hash mismatch")
    sources = {str(case_dir / f): file_hash(case_dir / f) for f in ("audit_cases.jsonl", "summary.json")}
    payloads, jobs = {}, []
    for c in cases:
        requests = case_requests(case_dir, c, hashes, sources)
        common = digest(without_skill(requests["v0"]))
        for v in versions:
            payloads[(c["case_id"], v)] = requests[v]
            for repeat in range(1, repeats + 1):
                jobs.append({"job_id": f"{c['case_id']}--{v}--r{repeat}", "case_id": c["case_id"],
                             "version": v, "repeat": repeat, "dataset_id": c["dataset_id"],
                             "phase": c["phase"], "target_type": c["target_type"],
                             "image_layout": c["image_layout"], "image_count": len(c["images"]),
                             "request_sha256": digest(requests[v]), "non_skill_sha256": common})
    random.Random(seed).shuffle(jobs)
    for i, job in enumerate(jobs):
        job["order"] = i
    caps = sdk_capabilities()
    if batch and not caps["batch_supported"]:
        raise ReplayStop("Installed SDK has no Batch support")
    spec = {
        "format": "skill-replay-v1", "case_dir": str(case_dir), "versions": list(versions),
        "repeats": repeats, "seed": seed, "mode": "batch-preparation-only" if batch else "synchronous",
        "model": protocol["model"], "service_tier": "default",
        "config_hashes": hashes, "scoring_sha256": file_hash(SCORING),
        "case_hashes": {c["case_id"]: digest(c) for c in cases}, "source_hashes": sources,
        "code_hashes": {name: file_hash(ROOT / name) for name in CODE},
        "scope": protocol["scope"], "sdk": caps, "pricing": PRICES, "jobs": jobs,
        "planned_calls": len(jobs), "pair_invariance_checked_cases": len(cases),
        "constraints": dict(Counter(c["constraint"] for c in cases if "constraint" in c)),
        "disclaimer": DISCLAIMER,
    }
    return spec, payloads, cases


def experiment_root(path):
    path = Path(path).absolute()
    if path.is_symlink() or path.resolve() != path:
        raise ReplayStop("Output path must not contain symlinks or traversal")
    if path == ROOT / "output" or not path.is_relative_to(ROOT / "output"):
        raise ReplayStop("Use a new child directory of output/")
    return path


def prepare(out, spec, payloads):
    out = experiment_root(out)
    cases_dir = Path(spec["case_dir"])
    if out == cases_dir or out.is_relative_to(cases_dir) or cases_dir.is_relative_to(out):
        raise ReplayStop("Experiment must not overlap preparation directory")
    out.mkdir(parents=True, exist_ok=False)
    manifest = {"created_at": now(), "state": "dry-run-complete", "spec": spec}
    write_new(out / "manifest.json", manifest)
    with (out / "requests.jsonl").open("x", encoding="utf-8") as f:
        for job in spec["jobs"]:
            log_row(f, job)
    if spec["mode"] == "batch-preparation-only":
        # Metadata-only skeleton: deliberately not uploadable, no image bytes stored.
        with (out / "batch_plan.jsonl").open("x", encoding="utf-8") as f:
            for job in spec["jobs"]:
                log_row(f, {"custom_id": job["job_id"], "method": "POST", "url": "/v1/responses",
                            "request_sha256": job["request_sha256"], "sendable": False})
    write_new(out / "dry_run.json", {
        "network_calls": 0, "client_created": False, "planned_calls": len(spec["jobs"]),
        "pair_invariance_checked_cases": spec["pair_invariance_checked_cases"],
        "manifest_sha256": file_hash(out / "manifest.json"),
        "scoring_sha256": spec["scoring_sha256"],
        "sample_request_hashes": [{k: j[k] for k in ("job_id", "request_sha256", "non_skill_sha256")}
                                  for j in spec["jobs"][:4]],
    })
    return out


def verify_run(out):
    """Rebuild from immutable inputs; refuse changes, never amend a frozen run."""
    out = experiment_root(out)
    manifest = read_json(out / "manifest.json")
    old = manifest["spec"]
    if old["format"] != "skill-replay-v1":
        raise ReplayStop("Not a replay run")
    if file_hash(SCORING) != old["scoring_sha256"]:
        raise ReplayStop("Scoring hash changed; invalidate run and start a new experiment")
    if read_json(out / "dry_run.json")["manifest_sha256"] != file_hash(out / "manifest.json"):
        raise ReplayStop("Manifest changed")
    new, payloads, cases = build_plan(old["case_dir"], old["versions"], old["repeats"],
                                     old["seed"], old["mode"] == "batch-preparation-only")
    if canonical(new) != canonical(old):
        raise ReplayStop("Frozen inputs/code/config changed; start a new experiment")
    if read_jsonl(out / "requests.jsonl") != old["jobs"]:
        raise ReplayStop("Request plan changed")
    return old, payloads, cases


def client_factory():
    """Only called after an explicit network mode and all local gates."""
    from openai import OpenAI
    # Do not load .env; key must be explicitly available in the launch environment.
    return OpenAI(max_retries=0, timeout=180.0, base_url="https://api.openai.com/v1")


def token_count(client, request):
    result = client.responses.input_tokens.count(**{
        key: request[key] for key in ("model", "instructions", "input", "reasoning", "text")})
    n = result.input_tokens
    if type(n) is not int or n <= 0 or n > 272000:
        raise ReplayStop("Invalid/long-context token count")
    return n


def money(input_tokens, output_tokens, cached=0, *, conservative=False, batch=False):
    if any(type(x) is not int or x < 0 for x in (input_tokens, output_tokens, cached)) or cached > input_tokens:
        raise ReplayStop("Invalid usage")
    if input_tokens > 272000:
        raise ReplayStop("Long-context pricing outside protocol")
    rate = Decimal("12.5" if conservative else "10")
    total = (Decimal(input_tokens - cached) * rate + Decimal(cached) + Decimal(output_tokens) * 50) / 1000000
    return float(total / 2 if batch else total)


def reservation(n_input, max_output=4000, *, batch=False):
    if type(n_input) is not int or n_input <= 0 or n_input > 272000:
        raise ReplayStop("Invalid input count")
    padded = math.ceil(n_input * 1.10 + 1024)
    if padded > 272000:
        raise ReplayStop("Reservation could cross long-context pricing boundary")
    return money(padded, max_output, conservative=True, batch=batch)


def reserve(charged, limit, n_input, max_output=4000):
    amount = reservation(n_input, max_output)
    if Decimal(str(charged)) + Decimal(str(amount)) > Decimal(str(limit)):
        raise BudgetStop("Remaining budget cannot cover next request's reserved maximum")
    return amount


def error_record(exc, stage, job=None):
    # No exception body: it could expose credentials or full request data.
    return {"at": now(), "stage": stage, "job_id": job, "error_type": type(exc).__name__,
            "status_code": getattr(exc, "status_code", None),
            "reason": str(exc) if isinstance(exc, ReplayStop) else "Transport/local failure; manual inspection required",
            "automatic_retries": 0}


def estimate(out, spec, payloads):
    if not spec["sdk"]["input_token_count_supported"]:
        raise ReplayStop("Installed SDK has no token count endpoint")
    # Exclusive marker prevents retry/recount even after a crash.
    write_new(out / "estimate_started.json", {"at": now(), "manifest_sha256": file_hash(out / "manifest.json")})
    counted = {}
    client = None
    active = None
    try:
        client = client_factory()
        with (out / "token_counts.jsonl").open("x", encoding="utf-8") as log:
            for job in spec["jobs"]:
                active = job["job_id"]
                h = job["request_sha256"]
                if h not in counted:
                    n = token_count(client, payloads[(job["case_id"], job["version"])])
                    counted[h] = n
                    log_row(log, {"request_sha256": h, "input_tokens": n})
        per_call = []
        for j in spec["jobs"]:
            n = counted[j["request_sha256"]]
            per_call.append({"job_id": j["job_id"], "request_sha256": j["request_sha256"], "input_tokens": n,
                             "standard_max_output_usd": money(n, 4000),
                             "conservative_reserved_usd": reservation(n),
                             "batch_max_output_usd": money(n, 4000, batch=True),
                             "batch_conservative_reserved_usd": reservation(n, batch=True)})
        totals = {k: sum(r[k] for r in per_call) for k in (
            "standard_max_output_usd", "conservative_reserved_usd",
            "batch_max_output_usd", "batch_conservative_reserved_usd")}
        result = {"created_at": now(), "manifest_sha256": file_hash(out / "manifest.json"),
                  "scoring_sha256": spec["scoring_sha256"], "pricing": PRICES,
                  "inference_calls": 0, "token_count_calls": len(counted), "per_call": per_call,
                  "totals": totals, "max_output_tokens_including_reasoning": 4000,
                  "assumptions": "No cache-hit discount assumed; reservation includes 10%+1024 input headroom. Batch execution disabled.",
                  "status": "counted-not-inferred"}
        write_new(out / "estimate.json", result)
        return result
    except Exception as exc:
        write_new(out / "estimate_error.json", error_record(exc, "estimate", active))
        raise ReplayStop("Estimate stopped; no inference and no automatic recount") from None
    finally:
        if client is not None:
            client.close()


def authorization(out, budget, spec):
    if isinstance(budget, bool) or not isinstance(budget, (float, int)) or not math.isfinite(budget) or budget <= 0:
        raise ReplayStop("--execute requires a positive finite --budget-usd")
    if spec["mode"] != "synchronous":
        raise ReplayStop("Batch execution disabled: first-error stopping cannot be guaranteed")
    if (out / "execution_started.json").exists():
        raise ReplayStop("Execution already started; no automatic resend or resume")
    auth_path, est_path = out / "authorize_execute.json", out / "estimate.json"
    if not auth_path.is_file() or auth_path.is_symlink() or not est_path.is_file() or est_path.is_symlink():
        raise ReplayStop("User-written authorization and completed estimate required")
    auth, est = read_json(auth_path), read_json(est_path)
    ab = auth.get("budget_usd")
    if type(ab) not in (int, float) or not math.isfinite(ab) or Decimal(str(ab)) != Decimal(str(budget)):
        raise ReplayStop("Authorization budget mismatch")
    if auth.get("estimate_sha256") != file_hash(est_path):
        raise ReplayStop("Authorization estimate hash mismatch")
    if est.get("manifest_sha256") != file_hash(out / "manifest.json") or est.get("scoring_sha256") != spec["scoring_sha256"]:
        raise ReplayStop("Estimate not bound to this manifest/scoring")
    if est.get("status") != "counted-not-inferred" or est.get("pricing") != PRICES:
        raise ReplayStop("Unusable estimate")
    rows = est["per_call"]
    if len(rows) != len(spec["jobs"]) or [r["job_id"] for r in rows] != [j["job_id"] for j in spec["jobs"]]:
        raise ReplayStop("Estimate schedule mismatch")
    for r, j in zip(rows, spec["jobs"]):
        if r["request_sha256"] != j["request_sha256"] or r["conservative_reserved_usd"] != reservation(r["input_tokens"]):
            raise ReplayStop("Estimate request/cost mismatch")
    return {r["job_id"]: r for r in rows}


def decision_from_response(raw, phase):
    if raw.get("status") != "completed" or raw.get("error") or raw.get("incomplete_details"):
        raise ReplayStop("Incomplete/error response; no substitute action")
    model = raw.get("model", "")
    if model != "gpt-6-astra" and not model.startswith("gpt-6-astra-"):
        raise ReplayStop("Unexpected response model")
    if (raw.get("reasoning") or {}).get("effort") != "high":
        raise ReplayStop("Response does not confirm high reasoning")
    if raw.get("service_tier") not in (None, "default"):
        raise ReplayStop("Unexpected response pricing tier")
    texts = []
    for item in raw.get("output", []):
        if item.get("type") == "reasoning":
            continue
        if item.get("type") != "message" or item.get("role") != "assistant" or item.get("status") != "completed":
            raise ReplayStop("Unexpected output item")
        for part in item.get("content", []):
            if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                raise ReplayStop("Refused/non-text output")
            texts.append(part["text"])
    if len(texts) != 1:
        raise ReplayStop("Expected one JSON answer")
    try:
        answer = parse_json(texts[0])
    except (ValueError, TypeError) as exc:
        raise ReplayStop("Response JSON parse failed") from exc
    if (not isinstance(answer, dict) or set(answer) != {"action", "rationale"}
            or answer["action"] not in contract.ACTIONS[phase] or not isinstance(answer["rationale"], str)):
        raise ReplayStop("Response schema invalid")
    return answer


def usage_cost(raw):
    u = raw.get("usage")
    if not isinstance(u, dict) or not {"input_tokens", "output_tokens"} <= set(u):
        raise ReplayStop("Missing usage; retain reservation")
    details = u.get("input_tokens_details") or {}
    cached = details.get("cached_tokens", 0)
    n, m = u["input_tokens"], u["output_tokens"]
    charge = money(n, m, cached, conservative=True)
    standard = money(n, m, cached)
    return {"input_tokens": n, "output_tokens": m, "cached_input_tokens": cached,
            "conservative_usd": charge, "standard_estimate_usd": standard}


def execute(out, spec, payloads, budget):
    estimates = authorization(out, budget, spec)  # before any client or key access
    # Check the first reservation offline too.
    reserve(0, budget, estimates[spec["jobs"][0]["job_id"]]["input_tokens"])
    write_new(out / "execution_started.json", {
        "at": now(), "budget_usd": budget, "manifest_sha256": file_hash(out / "manifest.json"),
        "authorization_sha256": file_hash(out / "authorize_execute.json"),
        "estimate_sha256": file_hash(out / "estimate.json"), "automatic_retries": 0})
    charged = standard = 0.0
    attempted = completed = 0
    client = None
    active = None
    failure = None
    try:
        with ((out / "responses.jsonl").open("x", encoding="utf-8") as responses,
              (out / "usage.jsonl").open("x", encoding="utf-8") as usage,
              (out / "budget_events.jsonl").open("x", encoding="utf-8") as events,
              (out / "errors.jsonl").open("x", encoding="utf-8") as errors):
            try:
                for j in spec["jobs"]:
                    active = j["job_id"]
                    amount = reserve(charged, budget, estimates[active]["input_tokens"])
                    request = payloads[(j["case_id"], j["version"])]
                    if digest(request) != j["request_sha256"]:
                        raise ReplayStop("Request mutated after preflight")
                    if client is None:
                        client = client_factory()
                    charged += amount
                    log_row(events, {"state": "reserved", "job_id": active, "at": now(),
                                     "reserved_usd": amount, "charged_total_usd": charged})
                    attempted += 1
                    # No recount or retry here; estimate already binds these exact bytes.
                    response = client.responses.create(**request)
                    raw = response.model_dump(mode="json")
                    log_row(responses, {"job_id": active, "request_sha256": j["request_sha256"], "response": raw})
                    bill = usage_cost(raw)
                    charged += bill["conservative_usd"] - amount
                    standard += bill["standard_estimate_usd"]
                    log_row(usage, {"job_id": active, **bill})
                    log_row(events, {"state": "settled", "job_id": active, "at": now(), **bill,
                                     "charged_total_usd": charged})
                    if bill["conservative_usd"] > amount or charged > budget or bill["output_tokens"] > 4000:
                        raise ReplayStop("Usage exceeded reserved bound; manual billing audit required")
                    decision_from_response(raw, j["phase"])
                    completed += 1
            except Exception as exc:
                failure = error_record(exc, "execute", active)
                log_row(errors, failure)
    finally:
        status = {"state": "stopped" if failure or completed != len(spec["jobs"]) else "completed",
                  "at": now(), "planned_calls": len(spec["jobs"]), "attempted_calls": attempted,
                  "valid_responses": completed, "budget_usd": budget, "charged_or_reserved_usd": charged,
                  "standard_estimate_usd": standard, "failure": failure,
                  "no_automatic_resume": True}
        write_new(out / "execution_status.json", status)
        if client is not None:
            client.close()
    if failure or status["state"] != "completed":
        raise ReplayStop("Execution stopped; audit saved, do not resend automatically")
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--versions", default="v0,v1")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20261006)
    parser.add_argument("--output-root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--estimate", action="store_true", help="NETWORK: count tokens only; requires explicit user permission")
    mode.add_argument("--execute", action="store_true", help="NETWORK: requires user-written authorization")
    parser.add_argument("--budget-usd", type=float)
    parser.add_argument("--batch", action="store_true", help="Offline batch plan / discounted estimate only; execution disabled")
    args = parser.parse_args(argv)
    try:
        out = experiment_root(args.output_root)
        if args.execute and args.batch:
            raise ReplayStop("Batch execution disabled under immediate-stop requirements")
        if args.budget_usd is not None and not args.execute:
            raise ReplayStop("--budget-usd is only valid with --execute")
        if args.estimate or args.execute:
            spec, payloads, _ = verify_run(out)
            if (spec["versions"] != args.versions.split(",") or spec["repeats"] != args.repeats
                    or spec["seed"] != args.seed or Path(spec["case_dir"]) != args.case_dir.resolve()
                    or (spec["mode"] == "batch-preparation-only") != args.batch):
                raise ReplayStop("CLI options differ from frozen dry-run")
            result = execute(out, spec, payloads, args.budget_usd) if args.execute else estimate(out, spec, payloads)
        else:
            spec, payloads, _ = build_plan(args.case_dir, args.versions.split(","), args.repeats, args.seed, args.batch)
            prepare(out, spec, payloads)
            result = read_json(out / "dry_run.json")
        print(json.dumps(result if not args.estimate else {"totals": result["totals"], "inference_calls": 0}, indent=2))
        return 0
    except (ReplayStop, ValueError, KeyError, OSError) as exc:
        print(f"STOP: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
