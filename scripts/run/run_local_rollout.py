"""Single-channel rollout of harness v2 with a local vLLM model (项目总文档 §3.7). First-round scope: no resume.

Round-synchronous controller + observation v2 + frozen contract + versioned skill + observe-then-act output,
answered by a model served by vLLM on this machine. The model sees only rendered observations; MAT expert
labels are read only by the scorer after the run. A run that hits a limit is scored separately as a
"stopped_state" and must not be mixed with completed runs.

--scripted-offline replaces the server with a fixed KEEP / NOT_MERGE script (no model, no network) to check
the whole pipeline on real data before a GPU is available. Its outputs are labelled SCRIPTED.

Example (on the GPU Pod, after `scripts/deploy/local_models.py serve gemma4`):
  .venv/bin/python scripts/run/run_local_rollout.py --model gemma4 --parallel 4 \
      --output-root output/local_rollout_gemma4_ch31_YYYYMMDD
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import threading
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.analysis import run_annotated_offline_references as existing
from src.agent import curation_decider_v2 as dec
from src.agent.vllm_provider import VllmProvider
from src.pipeline.curation_controller_v2 import RoundsController

ref = existing.ref
PROFILES = ROOT / "configs/controller_v2/models.json"
DEFAULT_DATASET = "cM2-e004_004-006_CH31"
FROZEN = ("src/agent/curation_observation_v2.py", "src/agent/curation_observation_v1.py",
          "src/agent/curation_decider_v2.py", "src/agent/vllm_provider.py", "src/pipeline/curation_controller_v2.py",
          "src/pipeline/curation_engineering_v1.py", "src/cluster/manager.py", "configs/controller_v2/contract.txt",
          "configs/controller_v2/models.json", "scripts/run/run_local_rollout.py")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_hashes(skill):
    return {**{p: sha(ROOT / p) for p in FROZEN}, str(Path(skill).resolve().relative_to(ROOT)): sha(skill)}


class ScriptedServer:
    """Offline stand-in for vLLM: KEEP every cluster, NOT_MERGE every pair, first label of every field."""

    def __init__(self, profile):
        self.name, self.lock, self.calls = profile["served_name"], threading.Lock(), 0

    def __call__(self, method, path, body=None):
        if path == "/v1/models":
            return {"data": [{"id": self.name}]}
        if path == "/version":
            return {"version": "SCRIPTED-OFFLINE"}
        with self.lock:
            self.calls += 1
        props = body["response_format"]["json_schema"]["schema"]["properties"]
        answer = {k: {"label": v["properties"]["label"]["enum"][0], "note": "SCRIPTED, not a model"}
                  for k, v in props.items() if k not in ("action", "rationale")}
        answer.update(action="KEEP" if "KEEP" in props["action"]["enum"] else "NOT_MERGE",
                      rationale="SCRIPTED offline check, not a model")
        return {"model": self.name, "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}


def f1(scores):
    return scores["one_to_one"]["full_output"]["f1"] if scores else None


def report(out, result, manifest):
    s = result["scores"]
    lines = [f"# harness v2 rollout: {manifest['model_alias']} on {manifest['dataset']['dataset_id']}", "",
             "**SCRIPTED OFFLINE CHECK: no model was called.**" if manifest["scripted_offline"] else
             f"Model `{manifest['profile']['served_name']}`, vLLM {manifest['server'].get('version')}.", "",
             f"- Status: {result['status']}; evaluation: {result['evaluation'] or 'not scored'}",
             f"- Decisions: {result['decisions']} in {result['rounds']} rounds; parallel {manifest['parallel']}",
             f"- Provider: {result['provider']}", f"- Seconds: {result['seconds']:.0f}"]
    if s:
        o = s["one_to_one"]
        lines += [f"- One-to-one full output: P {o['full_output']['precision']:.4f}, R {o['full_output']['recall']:.4f}, "
                  f"F1 {o['full_output']['f1']:.4f}; units {o['n_matched_units']}/{o['n_expert_units']}; "
                  f"predicted clusters {o['n_predicted_units']}; expert spikes discarded {o['discarded_expert_spikes']}"]
    if result["evaluation"] == "stopped_state":
        lines += ["", "Stopped at a limit: this score is a stopped state, reported separately from completed runs."]
    if result["error"]:
        lines += ["", f"Error: `{result['error']}`"]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", required=True, choices=sorted(json.loads(PROFILES.read_text())["models"]))
    p.add_argument("--dataset", default=DEFAULT_DATASET)
    p.add_argument("--output-root", required=True)
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("-k", type=int, default=3)
    p.add_argument("--no-final-sweep", action="store_true")
    p.add_argument("--max-decisions", type=int, default=5000)
    p.add_argument("--max-rounds", type=int)
    p.add_argument("--max-seconds", type=float)
    p.add_argument("--max-total-tokens", type=int)
    p.add_argument("--skill", default=str(dec.SKILL_V4))
    p.add_argument("--numbers", choices=("default", "full"), default="default")
    p.add_argument("--scripted-offline", action="store_true")
    args = p.parse_args(argv)

    out = ref.stage6.output_path(args.output_root)
    out.mkdir(parents=True, exist_ok=False)
    row = ref.stage6.selected_rows([args.dataset])[0]
    data = existing.load_data(row)
    profile = json.loads(PROFILES.read_text())["models"][args.model]
    provider = VllmProvider(profile, out / "ledger.jsonl", base_url=args.base_url,
                            max_total_tokens=args.max_total_tokens,
                            transport=ScriptedServer(profile) if args.scripted_offline else None)
    server = provider.check()
    decider = dec.SkillDecider(provider, skill_path=args.skill, numbers=args.numbers)
    hashes = code_hashes(args.skill)
    manifest = dict(run="harness-v2-local-rollout", model_alias=args.model, profile=profile, server=server,
                    scripted_offline=args.scripted_offline, dataset=row, parallel=args.parallel,
                    code_hashes=hashes, argv=sys.argv[1:] if argv is None else list(argv),
                    labels="MAT expert labels are read only by the scorer after the run", **decider.describe())
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    c = RoundsController(ref.fresh(data), args.dataset, out / "rollout", decider=decider, sampling_rate=data["Fs"],
                         k=args.k, parallel=args.parallel, final_sweep=not args.no_final_sweep,
                         max_rounds=args.max_rounds, max_decisions=args.max_decisions, max_seconds=args.max_seconds,
                         manifest=dict(model_alias=args.model, scripted_offline=args.scripted_offline,
                                       **decider.describe()))
    started, error = monotonic(), None
    try:
        c.run()
    except Exception as exc:  # recorded, not hidden; the state at the error is not scored
        error = repr(exc)
    evaluation = {"completed": "completed", "stopped_limit": "stopped_state"}.get(c.status)
    scores = ref.score(c.manager.assigns.copy(), data) if evaluation else None
    # A change of frozen code, skill or MAT invalidates the run, but its record is still written.
    changed = sorted(k for k, v in code_hashes(args.skill).items() if hashes.get(k) != v)
    if existing.sha(ROOT / row["mat_path"]) != row["mat_sha256"]:
        changed.append(row["mat_path"])
    if changed:
        error = error or "Frozen code, skill or MAT changed during the run"
        evaluation = None
    result = dict(status=c.status, evaluation=evaluation, error=error, decisions=c.calls, rounds=c.round,
                  seconds=monotonic() - started, provider=provider.stats(), scores=scores, changed_during_run=changed)
    (out / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True, default=float), encoding="utf-8")
    report(out, result, manifest)
    print(f"{args.dataset} / {args.model}{' (SCRIPTED)' if args.scripted_offline else ''}: {c.status}, "
          f"evaluation={evaluation}, F1={f1(scores)}, decisions={c.calls}, rounds={c.round}", flush=True)
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
