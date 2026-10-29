"""Manage ACE+GEPA learning sessions. No model calls except the explicit optimize command."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.agent.curation_decider_v2 import SKILL_V1
from src.agent.feedback_runtime import load_config, optimize_session_batch, ingest_reviews
from src.agent.feedback_session import FeedbackSession


def frequency(value):
    if value == "manual":
        return None
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("Use a positive feedback count or manual")
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--session", required=True)
    init.add_argument("--base-skill", default=str(SKILL_V1))
    init.add_argument("--validation", nargs="+", required=True)
    init.add_argument("--config", required=True)
    init.add_argument("--feedback-batch-size", type=frequency, default=10)
    ingest = sub.add_parser("ingest", help="Import already validated learning bundles")
    ingest.add_argument("--session", required=True)
    ingest.add_argument("--bundle", required=True)
    reviews = sub.add_parser("ingest-reviews", help="Bind normalized reviews to a rollout and import")
    reviews.add_argument("--session", required=True)
    reviews.add_argument("--run", required=True)
    reviews.add_argument("--reviews", required=True)
    opt = sub.add_parser("optimize", help="Run at most one batch when N feedbacks are ready")
    opt.add_argument("--session", required=True)
    opt.add_argument("--force", action="store_true", help="Manual trigger or flush fewer than N pending reviews")
    opt.add_argument("--retry-failed", action="store_true")
    configure = sub.add_parser("configure")
    configure.add_argument("--session", required=True)
    configure.add_argument("--feedback-batch-size", type=frequency, required=True)
    for name in ("status", "recover"):
        sub.add_parser(name).add_argument("--session", required=True)
    a = p.parse_args(argv)
    session = FeedbackSession(a.session)
    if a.command == "init":
        session = FeedbackSession.create(a.session, a.base_skill, a.validation,
                                          feedback_batch_size=a.feedback_batch_size, config=load_config(a.config))
        result = session.status()
    elif a.command == "ingest":
        result = session.import_bundle(a.bundle)
    elif a.command == "ingest-reviews":
        result = ingest_reviews(session, a.run, a.reviews)
    elif a.command == "optimize":
        result = session.advance(optimize_session_batch, force=a.force, retry_failed=a.retry_failed)
    elif a.command == "configure":
        session.set_batch_size(a.feedback_batch_size)
        result = session.status()
    elif a.command == "recover":
        session.recover()
        result = session.status()
    else:
        result = session.status()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
