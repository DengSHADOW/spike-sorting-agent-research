"""Explicit skill learning commands; no dataset/controller changes or implicit model calls.

See docs/HUMAN_FEEDBACK_OPTIMIZATION.md. Each operation writes a fresh output directory/file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.agent.feedback_optimization import build_bundle, optimize_once, review_template
from src.agent.hosted_provider import HostedProvider
from src.agent.vllm_provider import VllmProvider
from src.pipeline.curation_engineering_v1 import write_json


def provider(profile_path, ledger, max_calls):
    config = json.loads(Path(profile_path).read_text())
    if config.get("backend") == "vllm":
        if type(max_calls) is not int or max_calls < 1:
            raise ValueError("Positive model request cap required")
        p = VllmProvider(config["profile"], ledger, base_url=config.get("base_url", "http://127.0.0.1:8000"))
        class Capped:
            def __init__(self):
                self.calls = 0

            def respond(self, content, *, folder):
                if self.calls >= max_calls:
                    raise RuntimeError("Local evaluation request cap reached")
                self.calls += 1
                return p.respond(content, folder=folder)

            def describe(self):
                return dict(backend="vllm", config=config, max_calls=max_calls,
                            note="cap counts decisions; local provider may retry a truncation once")
        return Capped()
    if config.get("backend") != "hosted":
        raise ValueError("Profile backend must be hosted or vllm")
    return HostedProvider(config["profile"], ledger, max_calls=max_calls)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    template = sub.add_parser("template", help="Export exact-state review template from a v2 rollout directory")
    template.add_argument("--run", required=True)
    template.add_argument("--output", required=True)
    bundle = sub.add_parser("bundle", help="Validate selected reviews and freeze their evidence")
    bundle.add_argument("--run", required=True)
    bundle.add_argument("--feedback", required=True)
    bundle.add_argument("--output", required=True)
    opt = sub.add_parser("optimize", help="One offline skill update; no workflow modifications")
    opt.add_argument("--base-skill", default=str(ROOT / "configs/controller_v2/skills/skill_v1.json"))
    opt.add_argument("--learning", required=True)
    opt.add_argument("--validation", required=True)
    group = opt.add_mutually_exclusive_group(required=True)
    group.add_argument("--candidate", help="Evaluate an already proposed skill JSON")
    group.add_argument("--proposer-profile", help="Generate a candidate from learning feedback only")
    opt.add_argument("--provider-profile", required=True, help="Fixed execution model for paired evaluation")
    opt.add_argument("--max-calls", type=int, required=True)
    opt.add_argument("--version", default="feedback_v1")
    opt.add_argument("--min-gain", type=float, default=0.0)
    opt.add_argument("--max-regressions", type=int, default=0)
    opt.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "template":
        write_json(Path(args.output), review_template(args.run))
    elif args.command == "bundle":
        build_bundle(args.run, args.feedback, args.output)
    else:
        # Provider constructors make no network calls; optimize_once owns output creation.
        output = Path(args.output)
        evaluator = provider(args.provider_profile, output / "evaluation_ledger.jsonl", args.max_calls)
        proposer = (provider(args.proposer_profile, output / "proposal_ledger.jsonl", 1)
                    if args.proposer_profile else None)
        result = optimize_once(args.base_skill, args.learning, args.validation, evaluator, output,
                               candidate_path=args.candidate, proposer=proposer, version=args.version,
                               min_gain=args.min_gain, max_regressions=args.max_regressions)
        print(json.dumps({k: v for k, v in result.items() if k != "evaluations"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
