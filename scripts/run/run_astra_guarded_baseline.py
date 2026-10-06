"""Budgeted Astra-high rollouts; protected CH3/20 and upstream CH30/31.

The upstream request builder remains pinned; only the final transport request
changes model/effort/service tier. No prompt rewriting or fallback inference.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import run_pinned_upstream_baseline as base

CHANNELS = ("CH30", "CH31", "CH3", "CH20")
MODEL = "gpt-6-astra"
LIMIT = 30.0


class BudgetStop(base.ExecutionStop):
    pass


def costs(usage):
    """USD: conservative budget charge and standard-rate estimate, not a bill."""
    if "input_tokens" not in usage or "output_tokens" not in usage:
        raise base.ExecutionStop("Usage missing; retain request reservation and stop")
    n_in, n_out = usage["input_tokens"], usage["output_tokens"]
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
    if not all(isinstance(x, int) and x >= 0 for x in (n_in, n_out, cached)) or cached > n_in:
        raise base.ExecutionStop("Invalid token usage; retain reservation and stop")
    if n_in > 272000:
        raise base.ExecutionStop("Long-context pricing outside this protocol")
    # Charge all uncached input at the higher cache-write rate to avoid an
    # optimistic cost guard if the provider writes new cache entries.
    charge = ((n_in - cached) * 12.5 + cached + n_out * 50) / 1e6
    estimate = ((n_in - cached) * 10 + cached + n_out * 50) / 1e6
    return charge, estimate


def reserve(ledger, channel, call_id, n_input, max_output):
    if not isinstance(n_input, int) or n_input <= 0 or n_input > 272000:
        raise base.ExecutionStop("Invalid or oversized input count")
    # Headroom for count/schema accounting differences, plus worst-case output.
    reservation = ((n_input * 1.10 + 1024) * 12.5 + max_output * 50) / 1e6
    if ledger["charged_usd"] + reservation > ledger["limit_usd"]:
        raise BudgetStop("Remaining budget cannot cover next request's reserved maximum")
    record = {"channel": channel, "call_id": call_id, "at": base.now(),
              "counted_input_tokens": n_input, "reserved_usd": reservation,
              "charged_usd": reservation, "state": "reserved"}
    ledger["charged_usd"] += reservation
    ledger["calls"].append(record)
    return record


class Experiment:
    def __init__(self, root, channel):
        self.root, self.channel = root, channel
        self.protected = channel in ("CH3", "CH20")

    def controller(self, upstream):
        if not self.protected:
            return upstream
        path = self.root / "protected_controller.py"
        spec = importlib.util.spec_from_file_location("src.pipeline.astra_protected", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def request(self, request):
        if any(k in request for k in ("temperature", "top_p", "extra_body", "tools")):
            raise base.ExecutionStop("Unexpected transport parameters")
        return dict(request, model=MODEL, reasoning={"effort": "high"}, service_tier="default")

    def send(self, responses, request, out, call_id):
        # A shared lock covers reservation + inference + settlement; even an
        # accidentally launched second worker cannot overspend the batch ledger.
        with (self.root / "budget.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            ledger_path = self.root / "budget.json"
            ledger = json.loads(ledger_path.read_text())
            if any(c["state"] == "reserved" for c in ledger["calls"]):
                raise base.ExecutionStop("Unsettled prior request; manual audit required")
            try:
                counted = responses.input_tokens.count(**{
                    k: request[k] for k in ("model", "input", "reasoning", "text")})
                record = reserve(ledger, self.channel, call_id, counted.input_tokens,
                                 request["max_output_tokens"])
                base.write_json(ledger_path, ledger)
                response = responses.create(**request)
            except base.ExecutionStop:
                raise
            except Exception as exc:
                base.append_json(out / "transport_errors.jsonl", {
                    "call_id": call_id, "type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", None), "at": base.now()})
                # Do not print exception bodies (could contain input data).
                raise base.ExecutionStop("Transport/count failure; no automatic retry") from None
            raw = response.model_dump(mode="json")
            base.append_json(out / "transport_responses.jsonl", {"call_id": call_id, "response": raw})
            charge, estimate = costs(raw.get("usage") or {})
            ledger["charged_usd"] += charge - record["charged_usd"]
            record.update(state="settled", charged_usd=charge, estimated_standard_usd=estimate,
                          response_id=raw.get("id"), usage=raw["usage"], status=raw.get("status"))
            ledger["estimated_standard_usd"] += estimate
            base.write_json(ledger_path, ledger)
            if charge > record["reserved_usd"] or ledger["charged_usd"] > LIMIT:
                raise base.ExecutionStop("Usage exceeded reserved bound; stop for billing audit")
            return response

    def validate_response(self, raw, text, schema):
        if raw.get("status") != "completed":
            raise base.ExecutionStop("Incomplete/refused response; no retry or substitute action")
        if not str(raw.get("model", "")).startswith(MODEL):
            raise base.ExecutionStop("Unexpected actual model")
        if (raw.get("reasoning") or {}).get("effort") != "high":
            raise base.ExecutionStop("Response does not confirm high reasoning")
        if raw.get("service_tier") not in (None, "default"):
            raise base.ExecutionStop("Unexpected service tier")
        try:
            decision = json.loads(text)
            valid = (isinstance(decision, dict) and set(decision) == {"action", "rationale"}
                     and decision["action"] in schema["schema"]["properties"]["action"]["enum"]
                     and isinstance(decision["rationale"], str))
        except (ValueError, TypeError, KeyError):
            valid = False
        if not valid:
            raise base.ExecutionStop("Invalid action schema; no substitute decision")

    def protect(self, decision, stage, kwargs, out, call_id):
        effective = dict(decision)
        if self.protected and decision["action"] == "DISCARD":
            if stage == "phase1" and len(kwargs["spike_times"]) >= 4000:
                effective["action"] = "SPLIT" if len(kwargs["overcluster_composition"]) > 1 else "ABSTAIN"
            elif stage == "phase2":
                effective["action"] = "ABSTAIN"
        guarded = effective["action"] != decision["action"]
        if guarded:
            effective["rationale"] = (f"Deletion guard: DISCARD -> {effective['action']}. "
                                       + decision["rationale"])
        base.append_json(out / "effective_decisions.jsonl", {
            "api_call_id": call_id, "stage": stage, "protected_channel": self.protected,
            "proposed_action": decision["action"], "effective_action": effective["action"],
            "guard_triggered": guarded,
            **{k: kwargs[k] for k in ("cluster_id", "small_cluster_id", "large_cluster_id") if k in kwargs}})
        return effective


def prepare(root):
    base.prepare(root, 4000)
    controller = base.REPO / "src/pipeline/pure.py"
    (root / "protected_controller.py").write_bytes(controller.read_bytes())
    (root / "astra_harness_snapshot.py").write_bytes(Path(__file__).read_bytes())
    protocol = json.loads((root / "protocol.json").read_text())
    protocol.update(model=MODEL, reasoning_effort="high", channels=CHANNELS,
                    protocol_variant="astra-high-channel-specific-guards-v1", budget_usd=LIMIT,
                    astra_harness_sha256=base.file_hash(__file__),
                    protected_controller_sha256=base.file_hash(controller),
                    protected_channels=["CH3", "CH20"], service_tier="default",
                    execution_changes=[
                        "Pinned upstream prompt/three images/schema; final transport changes model to gpt-6-astra and effort to high",
                        "4000 output tokens per request, including reasoning; SDK retries disabled; first incomplete/error stops batch",
                        "CH30/31 use unmodified upstream controller and thresholds 500/4000/5000",
                        "CH3/20 use frozen local protected controller; automatic filters off; phase2 boundary 4000",
                        "CH3/20 Phase1 DISCARD >=4000 -> SPLIT if multiple overclusters, otherwise ABSTAIN",
                        "CH3/20 Phase2 DISCARD -> ABSTAIN; no targets/all NOT_MERGE preserve; only KEEP validates a merge target",
                        "Shared $30 conservative ledger reserves full output budget before generation; no mixed-protocol macro score",
                    ],
                    prices_usd_per_million={"input": 10, "cached_input": 1, "cache_write": 12.5, "output": 50},
                    docs=["https://developers.openai.com/api/docs/models/gpt-6-astra",
                          "https://developers.openai.com/api/docs/guides/token-counting"])
    base.write_json(root / "protocol.json", protocol)
    base.write_json(root / "budget.json", {"limit_usd": LIMIT, "charged_usd": 0.0,
                    "estimated_standard_usd": 0.0, "calls": []})


def verify(root):
    p = json.loads((root / "protocol.json").read_text())
    for path, expected in ((Path(__file__), p["astra_harness_sha256"]),
                           (Path(base.__file__), p["harness_sha256"]),
                           (root / "protected_controller.py", p["protected_controller_sha256"])):
        if base.file_hash(path) != expected:
            raise RuntimeError(f"Frozen source changed: {path.name}")
    if p["model"] != MODEL or p["reasoning_effort"] != "high" or p["budget_usd"] != LIMIT:
        raise RuntimeError("Protocol mismatch")
    ledger = json.loads((root / "budget.json").read_text())
    if ledger["limit_usd"] != LIMIT:
        raise RuntimeError("Budget ledger limit mismatch")


def collect(root):
    rows = []
    for channel in CHANNELS:
        path = root / channel / "status.json"
        status = json.loads(path.read_text()) if path.exists() else {"status": "not_started"}
        perf = status.get("performance", {}) if status["status"] == "complete" else {}
        eff = root / channel / "effective_decisions.jsonl"
        guards = sum(json.loads(s)["guard_triggered"] for s in eff.read_text().splitlines()) if eff.exists() else 0
        rows.append({"channel": channel, "protected": channel in ("CH3", "CH20"),
                     "status": status["status"], "calls": status.get("api_calls", 0), "guards": guards,
                     "units": status.get("final_units"), "spikes": status.get("assigned_spikes"),
                     "precision": perf.get("overall_precision"), "recall": perf.get("overall_recall"),
                     "f1": perf.get("overall_f1_score"), "actual_models": status.get("actual_models", []),
                     "error": status.get("error", "")})
    budget = json.loads((root / "budget.json").read_text())
    base.write_json(root / "collected_metrics.json", {"model": MODEL, "reasoning": "high",
                    "channels": rows, "macro_mean": None, "macro_note": "Different controller protocols; do not pool",
                    "estimated_standard_usd": budget["estimated_standard_usd"],
                    "conservative_charged_usd": budget["charged_usd"], "budget_usd": LIMIT})
    with (root / "collected_metrics.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = ["# GPT-6 Astra high — channel-specific protection", "",
             "Full autonomous real-data rollout, fixed upstream prompt, no SFT/RAG/mock.",
             "CH30/31 upstream controller; CH3/20 frozen protected controller. Do not pool as a model-only baseline.", "",
             "|Channel|Protected|Status|Calls|Guards|Units|Spikes|Precision|Recall|F1|",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        vals = [r[k] for k in ("channel", "protected", "status", "calls", "guards", "units", "spikes", "precision", "recall", "f1")]
        lines.append("|" + "|".join("—" if v is None else f"{v:.4f}" if isinstance(v, float) else str(v) for v in vals) + "|")
    lines += ["", f"Standard-rate usage estimate: ${budget['estimated_standard_usd']:.4f}.",
              f"Conservative budget charged/reserved: ${budget['charged_usd']:.4f} / ${LIMIT:.2f}.",
              "Provider billing is authoritative; reservations may include requests with unknown billing after transport failure.",
              "Failed/budget-stopped channels have no terminal score. Logs and arrays stay in ignored output/.", ""]
    (root / "REPORT.md").write_text("\n".join(lines))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--worker", choices=CHANNELS)
    mode.add_argument("--collect", action="store_true")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    root = args.output_root.resolve()
    if not root.is_relative_to(base.REPO / "output"):
        parser.error("Root must be under output/")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["MPLCONFIGDIR"] = str(root / "mpl-cache")
    if args.prepare:
        prepare(root)
        for channel in ("CH30", "CH3"):
            subprocess.run([sys.executable, __file__, "--output-root", str(root), "--worker", channel, "--dry"], check=True)
        collect(root)
        return
    verify(root)
    if args.collect:
        collect(root)
        return
    if args.worker:
        try:
            base.worker(root, args.worker, args.dry, experiment=Experiment(root, args.worker))
        except base.ExecutionStop as exc:
            print(type(exc).__name__ + ": " + str(exc), flush=True)
            raise SystemExit(2)
        return
    for channel in ("CH30", "CH3"):
        if json.loads((root / f"preflight_{channel}/status.json").read_text())["status"] != "preflight_passed":
            raise RuntimeError("Offline preflight missing")
    # Exclusive batch lock, and refuse to restart any paid run automatically.
    with (root / "batch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if any((root / c).exists() for c in CHANNELS):
            raise RuntimeError("Existing paid artifacts: audit required, no automatic rerun")
        for channel in CHANNELS:
            with (root / f"{channel}.log").open("x") as log:
                process = subprocess.Popen([sys.executable, "-u", __file__, "--output-root", str(root),
                    "--worker", channel], stdout=log, stderr=subprocess.STDOUT)
                base.write_json(root / "batch_status.json", {"status": "running", "channel": channel,
                    "worker_pid": process.pid, "parent_pid": os.getpid(), "at": base.now()})
                code = process.wait()
            rows = collect(root)
            if code != 0 or next(r for r in rows if r["channel"] == channel)["status"] != "complete":
                base.write_json(root / "batch_status.json", {"status": "stopped", "channel": channel,
                    "exit_code": code, "at": base.now()})
                raise SystemExit(2)
        base.write_json(root / "batch_status.json", {"status": "complete", "at": base.now()})


if __name__ == "__main__":
    main()
