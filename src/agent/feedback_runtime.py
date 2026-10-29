"""Provider/config wiring and round-boundary integration for ACE+GEPA learning sessions."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path

from src.agent.ace_gepa import run_ace_gepa, digest
from src.agent.curation_decider_v2 import SkillDecider
from src.agent.feedback_optimization import read, build_bundle, review_template
from src.agent.feedback_session import FeedbackSession
from src.agent.hosted_provider import HostedProvider
from src.agent.vllm_provider import VllmProvider
from src.pipeline.curation_engineering_v1 import write_json


def make_provider(config, ledger, max_calls):
    config = deepcopy(config)
    if config.get("backend") == "hosted":
        return HostedProvider(config["profile"], ledger, max_calls=max_calls)
    if config.get("backend") != "vllm":
        raise ValueError("Provider backend must be hosted or vllm")
    local = VllmProvider(config["profile"], ledger, base_url=config.get("base_url", "http://127.0.0.1:8000"))
    class Capped:
        def __init__(self):
            self.calls = 0

        def respond(self, content, *, folder):
            if self.calls >= max_calls:
                raise RuntimeError("Local provider decision limit reached")
            self.calls += 1
            return local.respond(content, folder=folder)

        def describe(self):
            return dict(config=config, max_calls=max_calls, local_truncation_retry_limit=1)
    return Capped()


DEFAULT_SEARCH = dict(max_proposals=4, max_evaluations=200, reflection_chunk_size=2, minibatch_size=2,
                      seed=0, min_gain=0.0, max_regressions=0)


def load_config(path):
    """Resolve profile file references once; sessions persist full configs, never credentials."""
    path = Path(path)
    config = read(path)
    if set(config) - {"execution", "reflection", "proposal", "search", "max_updates_per_boundary"}:
        raise ValueError("Unknown learning config fields")
    for role in ("execution", "reflection", "proposal"):
        value = config.get(role, config.get("reflection") if role == "proposal" else None)
        if isinstance(value, str):
            value = read(path.parent / value)
        if not isinstance(value, dict) or value.get("backend") not in ("hosted", "vllm"):
            raise ValueError(f"Missing/invalid {role} provider config")
        config[role] = value
    search = dict(DEFAULT_SEARCH, **config.get("search", {}))
    if set(search) != set(DEFAULT_SEARCH):
        raise ValueError("Unknown search options")
    for key in ("max_proposals", "max_evaluations", "reflection_chunk_size", "minibatch_size"):
        if type(search[key]) is not int or search[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if search["reflection_chunk_size"] > 8 or not 0 <= search["min_gain"] <= 1:
        raise ValueError("Invalid reflection chunk or minimum gain")
    if type(search["max_regressions"]) is not int or search["max_regressions"] < 0 or type(search["seed"]) is not int:
        raise ValueError("Invalid regression allowance or seed")
    config["search"] = search
    config.setdefault("max_updates_per_boundary", 10)
    if type(config["max_updates_per_boundary"]) is not int or config["max_updates_per_boundary"] < 1:
        raise ValueError("max_updates_per_boundary must be positive")
    return config


def optimize_session_batch(base, learning, validation, output, version, config):
    search = dict(DEFAULT_SEARCH, **config["search"])
    evaluator = make_provider(config["execution"], output / "execution_ledger.jsonl", search["max_evaluations"])
    reflector = make_provider(config["reflection"], output / "ace_ledger.jsonl",
                              math.ceil(len(learning) / search["reflection_chunk_size"]) + 1)
    proposer = make_provider(config["proposal"], output / "gepa_ledger.jsonl", search["max_proposals"])
    return run_ace_gepa(base, learning, validation, evaluator, reflector, proposer, output, version=version, **search)


def ingest_reviews(session, run, reviews_path):
    """Unreviewed template rows remain pending; invalid completed rows fail explicitly."""
    feedback = read(reviews_path)
    if feedback.get("role") != "learning":
        raise ValueError("Live feedback must have role=learning")
    feedback["reviews"] = [r for r in feedback["reviews"] if r.get("expert_action") is not None]
    if not feedback["reviews"]:
        return dict(added=0, revised=0)
    key = digest(feedback)
    # Build outside the long-running session lock; immutable input hash makes imports idempotent.
    with session.locked():
        folder = session.path / "imports" / key
        if not (folder / "bundle" / "bundle.json").exists():
            folder.mkdir(parents=True, exist_ok=True)
            export = folder / "review.json"
            if not export.exists():
                write_json(export, feedback)
            build_bundle(run, export, folder / "bundle")
    return session.import_bundle(folder / "bundle")


class RoundLearningHook:
    def __init__(self, session, *, pause=True, optimizer=optimize_session_batch, reviewer=None):
        """reviewer(run, template) -> filled template replaces the human pause (simulated feedback)."""
        self.session = session if isinstance(session, FeedbackSession) else FeedbackSession(session)
        self.pause, self.optimizer, self.reviewer = pause, optimizer, reviewer

    def check_execution_profile(self, provider):
        with self.session.locked():
            execution = self.session._read()["config"].get("execution")
        if execution and execution["profile"] != getattr(provider, "profile", None):
            raise ValueError("Live execution profile must match the session's evaluation model/settings")

    def __call__(self, controller):
        folder = controller.out / "reviews"
        folder.mkdir(exist_ok=True)
        template = review_template(controller.out)
        template["reviews"] = [r for r in template["reviews"] if
            read(controller.out / r["step"] / "question.json").get("round") == controller.round]
        path = folder / f"round_{controller.round:04d}.json"
        if self.reviewer is not None:
            template = self.reviewer(controller.out, template)
        write_json(path, template)
        if self.pause and self.reviewer is None:
            input(f"Round {controller.round} complete. Review {path}; press Enter when ready (blank rows are skipped): ")
        for export in sorted(folder.glob("round_*.json")):
            ingest_reviews(self.session, controller.out, export)
        self.update(controller)

    def update(self, controller, *, flush=False):
        self.check_execution_profile(controller.decider.provider)
        with self.session.locked():
            config = self.session._read()["config"]
        for _ in range(config.get("max_updates_per_boundary", 10)):
            result = self.session.advance(self.optimizer, force=flush)
            if result["status"] == "waiting":
                break
            controller.log("skill_optimization", **result)
        active = self.session.active()
        decider = SkillDecider(controller.decider.provider, skill_path=active,
                               contract_path=controller.decider.contract_path, numbers=controller.decider.numbers)
        if hasattr(controller.decider, "rewrap"):  # keep teacher forcing across skill activation
            decider = controller.decider.rewrap(decider)
        if decider.skill_sha256 != controller.decider.skill_sha256:
            controller.log("skill_activated", previous_sha256=controller.decider.skill_sha256,
                           next_sha256=decider.skill_sha256, version=decider.skill["version"], round=controller.round)
            controller.decider = decider
