"""GPU smoke test for harness v2 (项目总文档 §3.7.9): a few real requests, no rollout.

Sends one single-cluster request with a split preview (5 images), one without (4 images) and one pair
request (3 images) built from real CH31 states, then the pair request N times concurrently. Records per
request: seconds, prompt/completion tokens, finish reason, whether reasoning was returned, whether the
answer's fields came back in schema order, and the parsed answer. Labels are never loaded into a request.

  .venv/bin/python scripts/run/smoke_local_model.py --model gemma4 --output-root output/smoke_gemma4_YYYYMMDD
--scripted-offline checks the script itself without a server.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.analysis import run_annotated_offline_references as existing
from scripts.run.run_local_rollout import PROFILES, ScriptedServer
from src.agent import curation_decider_v2 as dec
from src.agent.vllm_provider import VllmProvider
from src.pipeline.curation_controller_v2 import split_available

ref = existing.ref


def request(decider, m, kind, cid, target, folder, fs):
    allowed = (["KEEP", "DISCARD"] + (["SPLIT"] if split_available(m, cid) else [])) if kind == "single" \
        else ["MERGE", "NOT_MERGE", "DISCARD"]
    folder.mkdir(parents=True)
    content = decider.prepare(m, kind, cid, target, allowed, folder=folder, sampling_rate=fs)
    return content, allowed


def measure(decider, content, allowed, folder):
    started = monotonic()
    error, answer = None, None
    try:
        answer = decider.answer(content, allowed, folder=folder)
    except Exception as exc:  # recorded: the smoke test exists to find these
        error = repr(exc)
    seconds = monotonic() - started
    raws = sorted(folder.glob("response_*.json"))
    raw = json.loads(raws[-1].read_text()) if raws else {}
    choice = (raw.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    try:
        order = [k for k, _ in json.loads(message.get("content") or "{}", object_pairs_hook=list)]
    except ValueError:
        order = None
    return dict(seconds=seconds, error=error, attempts=len(raws), finish_reason=choice.get("finish_reason"),
                usage=raw.get("usage"), reasoning_chars=len(message.get("reasoning_content") or
                                                             message.get("reasoning") or ""),
                field_order_ok=order == list(content["schema"]["properties"]), answer=answer,
                images=len(content["images"]))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", required=True, choices=sorted(json.loads(PROFILES.read_text())["models"]))
    p.add_argument("--output-root", required=True)
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--dataset", default="cM2-e004_004-006_CH31")
    p.add_argument("--concurrent", type=int, default=4)
    p.add_argument("--scripted-offline", action="store_true")
    args = p.parse_args(argv)
    out = ref.stage6.output_path(args.output_root)
    out.mkdir(parents=True, exist_ok=False)
    data = existing.load_data(ref.stage6.selected_rows([args.dataset])[0])
    m, fs = ref.fresh(data), data["Fs"]
    profile = json.loads(PROFILES.read_text())["models"][args.model]
    provider = VllmProvider(profile, out / "ledger.jsonl", base_url=args.base_url,
                            transport=ScriptedServer(profile) if args.scripted_offline else None)
    server = provider.check()
    decider = dec.SkillDecider(provider)
    by_size = sorted(m.get_active_clusters(), key=lambda c: -int((m.assigns == c).sum()))
    leaf = next(c for c in by_size if not split_available(m, c))
    cases = [("single_split", "single", by_size[0], None), ("single_leaf", "single", leaf, None),
             ("pair", "pair", by_size[2], by_size[1])]
    results = {}
    for name, kind, cid, target in cases:
        content, allowed = request(decider, m, kind, cid, target, out / name, fs)
        results[name] = measure(decider, content, allowed, out / name)
        print(name, {k: results[name][k] for k in ("seconds", "finish_reason", "usage", "reasoning_chars",
                                                   "field_order_ok", "error")}, flush=True)
    content, allowed = request(decider, m, "pair", by_size[2], by_size[1], out / "concurrent_template", fs)
    folders = [out / f"concurrent_{i}" for i in range(args.concurrent)]
    for f in folders:
        f.mkdir()
    started = monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrent) as pool:
        batch = list(pool.map(lambda f: measure(decider, content, allowed, f), folders))
    wall = monotonic() - started
    results["concurrent"] = dict(n=args.concurrent, wall_seconds=wall, per_request=[b["seconds"] for b in batch],
                                 errors=[b["error"] for b in batch if b["error"]],
                                 speedup=sum(b["seconds"] for b in batch) / wall if wall > 0 else None)
    print("concurrent", {k: v for k, v in results["concurrent"].items() if k != "per_request"}, flush=True)
    summary = dict(model=args.model, served=profile["served_name"], server=server, scripted_offline=args.scripted_offline,
                   provider=provider.stats(), results=results, decider=decider.describe())
    (out / "smoke.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    ok = all(not r["error"] and r["field_order_ok"] for k, r in results.items() if k != "concurrent")
    print("SMOKE", "OK" if ok else "PROBLEMS", out, flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
