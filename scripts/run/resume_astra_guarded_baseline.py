"""Continue the stopped Astra batch without repeating any paid inference.

Replay saved responses through the same pinned controller. Reuse saved PNGs,
advance the original waveform-sampling RNG, recompute numeric prompts, and
compare every reconstructed cluster state. At the first unpaid call, regenerate
images and require the original prompt/image/state hashes before any API call.
"""
from __future__ import annotations

import argparse
import base64
import csv
import fcntl
import functools
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import run_astra_guarded_baseline as astra

base = astra.base
LIMIT = 70.0


class ReplayBoundaryReached(base.ExecutionStop):
    pass


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def cached_response(raw):
    # SDK model_dump serializes schema_ rather than schema in nested format
    # objects. Do not revalidate/rewrite the saved response through a new SDK
    # constructor; expose only the same read-only attributes the harness uses.
    text = "".join(part["text"] for item in raw["output"] if item.get("type") == "message"
                   for part in item.get("content", []) if part.get("type") == "output_text")
    if not text:
        raise base.ExecutionStop("Cached response has no action text")
    return SimpleNamespace(model=raw["model"], status=raw["status"], output_text=text,
                           usage=raw.get("usage"), model_dump=lambda **kwargs: raw)


def compare_request(request, expected):
    content = request["input"][0]["content"]
    actual = {
        "model": request["model"], "reasoning": request["reasoning"],
        "max_output_tokens": request["max_output_tokens"],
        "text_format": request["text"]["format"],
        "prompt_sha256": base.digest(content[0]["text"].encode()),
        "image_sha256_in_order": [base.digest(base64.b64decode(x["image_url"].split(",", 1)[1])) for x in content[1:]],
    }
    for key, value in actual.items():
        if value != expected[key]:
            raise base.ExecutionStop(f"Replay request mismatch: {key}")


def compare_state(actual, expected):
    import numpy as np
    with np.load(actual) as a, np.load(expected) as b:
        for key in ("assigns", "hierarchy_tree"):
            if not np.array_equal(a[key], b[key], equal_nan=True):
                raise base.ExecutionStop(f"Replay state mismatch: {key}")


class ReplayExperiment(astra.Experiment):
    def __init__(self, root, source, audit=False):
        super().__init__(root, "CH30")
        self.source = source / "CH30"
        self.audit = audit
        self.requests = rows(self.source / "requests.jsonl")
        self.responses = rows(self.source / "responses.jsonl")
        self.decisions = rows(self.source / "decisions.jsonl")
        self.served = 0
        self.image_index = 0
        with (self.source / "vlm_inputs/vlm_call_log.csv").open() as stream:
            self.images = {int(r["call_id"]): r["image_files"].split(";") for r in csv.DictReader(stream)}
        n = len(self.responses)
        if n != 588 or len(self.requests) != n + 1 or len(self.decisions) != n:
            raise base.ExecutionStop("Unexpected cached trajectory length")
        for i, response in enumerate(self.responses, 1):
            if response["call_id"] != i or self.requests[i-1]["call_id"] != i:
                raise base.ExecutionStop("Cached call IDs are not contiguous")
            raw = response["response"]
            if raw.get("status") != "completed":
                raise base.ExecutionStop("Cannot replay an incomplete response")

    def controller(self, upstream):
        from src.agent import runner
        for name in ("create_waveform_overlay_image", "create_isi_histogram_image", "create_aggregation_tree_image"):
            original = getattr(runner, name)
            signature = inspect.signature(original)

            def make_cached(name, original, signature):
                @functools.wraps(original)
                def cached(*args, **kwargs):
                    if self.served >= len(self.responses):
                        return original(*args, **kwargs)
                    decision = self.decisions[self.served]
                    order = (["create_waveform_overlay_image", "create_isi_histogram_image", "create_aggregation_tree_image"]
                             if decision["stage"] == "phase1" else
                             ["create_waveform_overlay_image", "create_waveform_overlay_image", "create_isi_histogram_image"])
                    if self.image_index >= 3 or order[self.image_index] != name:
                        raise base.ExecutionStop("Cached image generation order differs")
                    # This is the exact only RNG operation in the waveform plot.
                    # Numeric correlation still executes its original sampling.
                    if name == "create_waveform_overlay_image":
                        import numpy as np
                        bound = signature.bind(*args, **kwargs)
                        bound.apply_defaults()
                        n = bound.arguments["waveforms"].shape[0]
                        cap = bound.arguments["max_waveforms"]
                        if n > cap:
                            np.random.choice(n, cap, replace=False)
                    filename = self.images[self.served + 1][self.image_index]
                    data = (self.source / "vlm_inputs" / filename).read_bytes()
                    expected = self.requests[self.served]["image_sha256_in_order"][self.image_index]
                    if base.digest(data) != expected:
                        raise base.ExecutionStop("Saved image hash mismatch")
                    self.image_index += 1
                    return base64.b64encode(data).decode()
                return cached
            setattr(runner, name, make_cached(name, original, signature))
        return upstream

    def send(self, responses, request, out, call_id):
        if call_id <= len(self.responses) + 1:
            compare_request(request, self.requests[call_id - 1])
            compare_state(out / "states" / f"call_{call_id:05d}.npz",
                          self.source / "states" / f"call_{call_id:05d}.npz")
        if call_id <= len(self.responses):
            if call_id != self.served + 1 or self.image_index != 3:
                raise base.ExecutionStop("Replay call/image sequence mismatch")
            raw = self.responses[call_id - 1]["response"]
            base.append_json(out / "replayed_calls.jsonl", {
                "call_id": call_id, "source_response_id": raw["id"], "new_api_call": False,
                "prompt_and_state_verified": True, "saved_images_verified": True})
            self.served += 1
            self.image_index = 0
            return cached_response(raw)
        if call_id == len(self.responses) + 1:
            base.write_json(out / "resume_boundary.json", {
                "status": "verified", "at": base.now(), "cached_calls": self.served,
                "first_unpaid_call_id": call_id, "newly_rendered_images_equal_saved_pending_request": True,
                "prompt_schema_state_equal": True, "audit_only": self.audit})
            if self.audit:
                raise ReplayBoundaryReached("588 cached calls and first unpaid request verified; no inference sent")
        if self.audit:
            raise base.ExecutionStop("Audit mode forbids inference")
        return super().send(responses, request, out, call_id)


def prepare(root, source):
    # Verify the original frozen batch before changing only the new budget.
    astra.verify(source)
    original = json.loads((source / "protocol.json").read_text())
    old_budget = json.loads((source / "budget.json").read_text())
    if len(old_budget["calls"]) != 588 or any(c["state"] != "settled" for c in old_budget["calls"]):
        raise RuntimeError("Old ledger contains unverified/unsettled inference")
    status = json.loads((source / "CH30/status.json").read_text())
    if status.get("error_type") != "BudgetStop" or status["accepted_decisions"] != 588:
        raise RuntimeError("Source is not the known budget interruption")
    astra.LIMIT = LIMIT
    astra.prepare(root)
    p = json.loads((root / "protocol.json").read_text())
    for c in astra.CHANNELS:
        if p["mat_files"][c]["sha256"] != original["mat_files"][c]["sha256"]:
            raise RuntimeError("MAT file changed since interrupted run")
    if p["protected_controller_sha256"] != original["protected_controller_sha256"]:
        raise RuntimeError("Protected controller changed")
    p.update(resume_source=str(source), resume_harness_sha256=base.file_hash(__file__),
             inherited_paid_calls=588, inherited_estimated_standard_usd=old_budget["estimated_standard_usd"],
             inherited_conservative_charged_usd=old_budget["charged_usd"],
             budget_authorization="User raised total budget to $70, inclusive of previous spend")
    p["execution_changes"] = [x.replace("$30", "$70") for x in p["execution_changes"]]
    p["execution_changes"].append("CH30 cached response replay checks all 588 states and regenerates pending-call images before new inference")
    base.write_json(root / "protocol.json", p)
    old_budget.update(limit_usd=LIMIT, inherited_from=str(source))
    base.write_json(root / "budget.json", old_budget)
    (root / "resume_harness_snapshot.py").write_bytes(Path(__file__).read_bytes())
    # Audit exactly the saved originals that the replay will consume.
    paths = [source / "protocol.json", source / "budget.json"]
    paths += [source / "CH30" / f for f in ("requests.jsonl", "responses.jsonl", "decisions.jsonl", "status.json")]
    base.write_json(root / "source_manifest.json", {str(p): base.file_hash(p) for p in paths})
    astra.collect(root)


def verify(root):
    astra.LIMIT = LIMIT
    astra.verify(root)
    p = json.loads((root / "protocol.json").read_text())
    if base.file_hash(__file__) != p["resume_harness_sha256"]:
        raise RuntimeError("Resume harness changed after freeze")
    for path, expected in json.loads((root / "source_manifest.json").read_text()).items():
        if base.file_hash(path) != expected:
            raise RuntimeError("Resume source artifact changed")
    return Path(p["resume_source"])


def collect(root):
    result = astra.collect(root)
    path = root / "collected_metrics.json"
    value = json.loads(path.read_text())
    value["inherited_paid_calls"] = 588
    budget = json.loads((root / "budget.json").read_text())
    value["new_generation_calls"] = len(budget["calls"]) - 588
    value["note"] = "CH30 call/usage totals include 588 inherited responses; budget counts their original charge once"
    base.write_json(path, value)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--source-root", type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--audit", action="store_true")
    group.add_argument("--execute", action="store_true")
    group.add_argument("--collect", action="store_true")
    group.add_argument("--worker", choices=astra.CHANNELS)
    args = parser.parse_args()
    root = args.output_root.resolve()
    if not root.is_relative_to(base.REPO / "output"):
        parser.error("Output must be under output/")
    os.environ["MPLCONFIGDIR"] = str(root / "mpl-cache")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    if args.prepare:
        if args.source_root is None:
            parser.error("--source-root required")
        prepare(root, args.source_root.resolve())
        return
    source = verify(root)
    if args.collect:
        collect(root)
        return
    if args.audit or args.worker:
        channel = "CH30" if args.audit else args.worker
        experiment = ReplayExperiment(root, source, args.audit) if channel == "CH30" else astra.Experiment(root, channel)
        try:
            base.worker(root, channel, experiment=experiment)
        except ReplayBoundaryReached:
            if not args.audit:
                raise
            proof = json.loads((root / "CH30/resume_boundary.json").read_text())
            (root / "CH30").rename(root / "replay_preflight_CH30")
            base.write_json(root / "replay_preflight.json", proof)
            print("Offline replay verified; zero new inference calls", flush=True)
            return
        except base.ExecutionStop as exc:
            print(type(exc).__name__ + ": " + str(exc), flush=True)
            raise SystemExit(2)
        if args.audit:
            raise RuntimeError("Audit unexpectedly completed instead of reaching boundary")
        return
    proof = json.loads((root / "replay_preflight.json").read_text())
    if proof["status"] != "verified" or proof["cached_calls"] != 588:
        raise RuntimeError("Offline replay preflight missing")
    with (root / "batch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if any((root / c).exists() for c in astra.CHANNELS):
            raise RuntimeError("Paid artifacts exist; no automatic rerun")
        for channel in astra.CHANNELS:
            with (root / f"{channel}.log").open("x") as log:
                child = subprocess.Popen([sys.executable, "-u", __file__, "--output-root", str(root), "--worker", channel],
                                         stdout=log, stderr=subprocess.STDOUT)
                base.write_json(root / "batch_status.json", {"status": "running", "channel": channel,
                    "parent_pid": os.getpid(), "worker_pid": child.pid, "at": base.now()})
                code = child.wait()
            results = collect(root)
            if code or next(x for x in results if x["channel"] == channel)["status"] != "complete":
                base.write_json(root / "batch_status.json", {"status": "stopped", "channel": channel,
                    "exit_code": code, "at": base.now()})
                raise SystemExit(2)
        base.write_json(root / "batch_status.json", {"status": "complete", "at": base.now()})


if __name__ == "__main__":
    main()
