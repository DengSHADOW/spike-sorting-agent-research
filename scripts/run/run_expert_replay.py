"""Strict expert replay: step-level agreement of the v2 decider on the expert's own curation path. No resume.

Questions are the expert's recorded edits in order (retractions dropped) on the exact pre-edit state, plus one
derived KEEP per cluster the expert never touches after it appears. The model answers each question; the
expert edit is executed whatever it answered. With --learning-session the simulated expert fills each round's
reviews and ACE+GEPA may activate a new skill at round boundaries (judgement layer only; no scheduler here).

The end state equals the expert replay by construction: it is not an agent score, and none is reported.

Examples:
  # offline pipeline check, no model
  .venv/bin/python scripts/run/run_expert_replay.py --model gemma4 --scripted-offline \
      --dataset cM2-e004_004-006_CH31 --output-root output/replay_scripted_ch31
  # fixed-skill baseline on the GPU Pod
  .venv/bin/python scripts/run/run_expert_replay.py --model gemma4 --parallel 4 \
      --dataset cM2-e004_004-006_CH31 --output-root output/replay_gemma4_ch31_baseline
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.run import run_local_rollout as rollout
from src.agent import curation_decider_v2 as dec
from src.agent import expert_simulation as sim
from src.agent import replay_learning as learning
from src.agent.vllm_provider import VllmProvider

ref, existing = rollout.ref, rollout.existing
# The upstream default skill (the newest one the harness ships with); the Tianmin copy falls back to v1.
DEFAULT_SKILL = getattr(dec, "SKILL_V4", dec.SKILL_V1)


def code_hashes(skill):
    own = ("scripts/run/run_expert_replay.py", "src/agent/expert_simulation.py", "src/agent/replay_learning.py")
    return {**rollout.code_hashes(skill), **{p: rollout.sha(ROOT / p) for p in own}}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    model = p.add_mutually_exclusive_group(required=True)
    model.add_argument("--model", choices=sorted(json.loads(rollout.PROFILES.read_text())["models"]))
    model.add_argument("--api-profile", help="Hosted profile JSON (backend=hosted); credentials from environment")
    p.add_argument("--max-api-calls", type=int, help="Required request cap for hosted APIs")
    p.add_argument("--dataset", default=rollout.DEFAULT_DATASET)
    p.add_argument("--output-root", required=True)
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--skill", default=str(DEFAULT_SKILL))
    p.add_argument("--numbers", choices=("default", "full"), default="default")
    p.add_argument("--scripted-offline", action="store_true")
    p.add_argument("--no-keep-questions", action="store_true", help="Ask only the recorded expert edits")
    p.add_argument("--keep-before-merge", action="store_true",
                   help="Also ask KEEP before a cluster's next expert merge (follows v2's scheduler, not the expert)")
    p.add_argument("--max-steps", type=int, help="Smoke limit; the partial run is reported as stopped_limit")
    p.add_argument("--no-thinking", action="store_true",
                   help="Override the profile's chat_template_kwargs.enable_thinking to false for this run only")
    p.add_argument("--prefetch", action="store_true",
                   help="No learning only: render the whole expert path first, then answer --parallel at a time")
    p.add_argument("--learning-session", help="ACE+GEPA session; skill may change only at round boundaries")
    p.add_argument("--simulated-feedback", choices=("mismatch", "all"), default="mismatch")
    p.add_argument("--feedback-sources", choices=("recorded", "all"), default="all",
                   help="recorded: only real expert edits become feedback; all: also derived KEEP labels")
    p.add_argument("--sim-window", type=int, default=20)
    p.add_argument("--online-learning", action="store_true",
                   help="Joint skill optimisation every N feedback cases, including correct answers by default")
    p.add_argument("--feedback-batch-size", type=int, default=1, help="N eligible feedback cases per joint update")
    p.add_argument("--online-feedback-scope", choices=("all", "mismatch"), default="all",
                   help="all includes successful answers; mismatch collects errors only")
    p.add_argument("--no-flush-online-feedback", action="store_true",
                   help="Leave the final incomplete batch pending instead of optimising it at dataset end")
    p.add_argument("--regression-k", type=int, default=3, help="Earlier replayed steps re-asked by the regression filter")
    p.add_argument("--protect-correct", action="store_true",
                   help="Reject a candidate that loses any correctly answered batch case (default: net gain only)")
    p.add_argument("--max-feedback", type=int, help="Cap processed feedback batches, including all-correct no-op batches")
    args = p.parse_args(argv)
    if args.feedback_batch_size < 1 or args.regression_k < 0 or (args.max_feedback is not None and args.max_feedback < 1):
        p.error("Feedback batch size and attempt cap must be positive; regression K must be non-negative")
    if not args.online_learning and (args.feedback_batch_size != 1 or args.online_feedback_scope != "all"
                                    or args.no_flush_online_feedback or args.protect_correct):
        p.error("Online batching options require --online-learning")
    if args.api_profile and (args.scripted_offline or not args.max_api_calls or args.max_api_calls < 1):
        p.error("Hosted mode requires positive --max-api-calls and no --scripted-offline")
    if args.learning_session and args.scripted_offline:
        p.error("Scripted replay cannot trigger real learning providers")
    if args.prefetch and (args.learning_session or args.online_learning):
        p.error("--prefetch cannot be combined with learning (skills change between rounds)")
    if args.online_learning and (args.learning_session or args.scripted_offline):
        p.error("--online-learning needs a real model and replaces --learning-session")
    hook = None
    if args.learning_session:
        from src.agent.feedback_runtime import RoundLearningHook
        hook = RoundLearningHook(args.learning_session, pause=False,
                                 reviewer=sim.simulated_reviewer(args.simulated_feedback, args.feedback_sources))
        args.skill = str(hook.session.active())

    out = ref.stage6.output_path(args.output_root)
    out.mkdir(parents=True, exist_ok=False)
    row = ref.stage6.selected_rows([args.dataset])[0]
    data = existing.load_data(row)
    if args.api_profile:
        from src.agent.hosted_provider import HostedProvider  # Tianmin copy only; not in upstream
        config = json.loads(Path(args.api_profile).read_text())
        if config.get("backend") != "hosted":
            raise ValueError("--api-profile requires backend=hosted")
        profile = config["profile"]
        provider = HostedProvider(profile, out / "ledger.jsonl", max_calls=args.max_api_calls)
        server = dict(version=None, connection="hosted; compatibility not independently verified")
        args.model = profile["model"]
    else:
        profile = json.loads(rollout.PROFILES.read_text())["models"][args.model]
        if args.no_thinking:
            profile = dict(profile, chat_template_kwargs=dict(profile.get("chat_template_kwargs", {}), enable_thinking=False),
                           override="enable_thinking=false via --no-thinking; sampling unchanged")
        provider = VllmProvider(profile, out / "ledger.jsonl", base_url=args.base_url,
                                transport=rollout.ScriptedServer(profile) if args.scripted_offline else None)
        server = provider.check()
    decider = dec.SkillDecider(provider, skill_path=args.skill, numbers=args.numbers)
    oracle = sim.ExpertOracle.for_dataset(args.dataset, data, ref.fresh)
    if args.online_learning:  # same served model and settings; separate ledgers for proposals and filter re-asks
        def side(name):
            if args.api_profile:
                return HostedProvider(profile, out / f"{name}_ledger.jsonl", max_calls=args.max_api_calls)
            return VllmProvider(profile, out / f"{name}_ledger.jsonl", base_url=args.base_url)
        hook = learning.OnlineLearner(out / "learning", proposer=side("proposer"), evaluator=side("evaluator"),
                                      k=args.regression_k, sources=args.feedback_sources,
                                      max_feedback=args.max_feedback, numbers=args.numbers,
                                      batch_size=args.feedback_batch_size, feedback_scope=args.online_feedback_scope,
                                      protect_correct=args.protect_correct)
    hashes = code_hashes(args.skill)
    manifest = dict(run="strict-expert-replay", model_alias=args.model, profile=profile, server=server,
                    provider_kind=provider.kind, scripted_offline=args.scripted_offline, dataset=row,
                    parallel=args.parallel, learning_session=args.learning_session,
                    simulated_feedback=(args.online_feedback_scope if args.online_learning
                                        else args.simulated_feedback if hook else None),
                    feedback_sources=args.feedback_sources if hook else None, code_hashes=hashes,
                    online_learning=hook.describe() if args.online_learning else None,
                    flush_online_feedback=not args.no_flush_online_feedback if args.online_learning else None,
                    argv=sys.argv[1:] if argv is None else list(argv),
                    labels="Recorded expert actions define the questions and the executed edits; the model sees "
                           "only rendered observations; MAT terminal labels are read only for the unified-replay check",
                    **decider.describe())
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    replay = sim.ExpertReplay(ref.fresh(data), oracle, args.dataset, out / "replay", decider=decider,
                              sampling_rate=data["Fs"], parallel=args.parallel, keep_questions=not args.no_keep_questions,
                              keep_before_merge=args.keep_before_merge, prefetch=args.prefetch,
                              max_steps=args.max_steps, after_round=hook,
                              manifest=dict(model_alias=args.model, run_profile=manifest, **decider.describe()))
    started, error = monotonic(), None
    try:
        replay.run()
        if hook and replay.status == "completed":
            if args.online_learning:
                hook.finish(replay, flush=not args.no_flush_online_feedback)
            else:
                hook.update(replay, flush=True)
    except Exception as exc:  # recorded, not hidden
        error = repr(exc)
    if hashes != code_hashes(args.skill) or existing.sha(ROOT / row["mat_path"]) != row["mat_sha256"]:
        raise RuntimeError("Frozen code, skill or MAT changed during the run")
    summary, _ = sim.summarize(replay.out, window=args.sim_window, oracle=oracle)
    # Always export the active skill, even when no candidate was accepted, so chain runners never guess.
    final_skill_path = out / "final_skill.json"
    final_skill_path.write_text(json.dumps(replay.decider.skill, indent=2, sort_keys=True), encoding="utf-8")
    (out / "simulation_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    result = dict(status=replay.status, error=error, steps=replay.calls, rounds=replay.round,
                  seconds=monotonic() - started, provider=provider.stats(),
                  simulation={k: v for k, v in summary.items() if k != "sliding_window"},
                  online_learning=hook.summary() if args.online_learning else None,
                  final_skill=dict(version=replay.decider.skill["version"], sha256=replay.decider.skill_sha256,
                                   export_path="final_skill.json", export_sha256=rollout.sha(final_skill_path)))
    (out / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True, default=float), encoding="utf-8")
    o = summary["overall"]
    lines = [f"# strict expert replay: {args.model} on {args.dataset}", "",
             "**SCRIPTED OFFLINE CHECK: no model was called.**" if args.scripted_offline else
             f"Model `{profile.get('served_name', profile.get('model'))}`, provider {provider.kind}.", "",
             f"- Status: {replay.status}; steps {replay.calls} in {replay.round} rounds; parallel {args.parallel}",
             f"- Agreement {o['correct']}/{o['n']} labeled steps; by expert action "
             f"{ {a: (v['correct'], v['n']) for a, v in summary['by_expert_action'].items()} }",
             f"- Provenance {summary['provenance_counts']}", f"- Expert operations {summary['expert_operations']}",
             f"- Seconds {result['seconds']:.0f} (render {summary['timing']['prepare_seconds']:.0f}, "
             f"model {summary['timing']['answer_seconds']:.0f}); provider {result['provider']}",
             "", "The end state is the expert's by construction and is not an agent score."]
    if args.online_learning:
        ol = result["online_learning"]
        lines[-2:-2] = [f"- Online learning: N={ol['batch_size']}, {ol['feedback_count']} feedback cases "
                        f"({ol['correct_feedback_count']} correct), {ol['attempts']} batches, outcomes {ol['outcomes']}, "
                        f"{ol['seconds']:.0f} s; final skill {result['final_skill']['version']}"]
    if error:
        lines += ["", f"Error: `{error}`"]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{args.dataset} / {args.model}{' (SCRIPTED)' if args.scripted_offline else ''}: {replay.status}, "
          f"steps={replay.calls}, agreement={o['correct']}/{o['n']}", flush=True)
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
