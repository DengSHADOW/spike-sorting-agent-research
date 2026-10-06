"""Offline-only, versioned curation request assembly; no SDK or network calls.

Existing runners are deliberately unchanged. Callers must explicitly integrate
this contract before claiming a live v1 run. Audit targets never enter requests.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "curation"
ACTIONS = {"phase1": ["KEEP", "SPLIT", "DISCARD"],
           "phase2": ["MERGE", "NOT_MERGE", "DISCARD"]}
FIELDS = {
    "phase1": {"cluster_id", "n_spikes", "n_overclusters", "isi_violation_rate",
               "amplitude_cv", "sampling_rate_hz", "waveform_window_ms", "refractory_ms"},
    "phase2": {"small_cluster_id", "large_cluster_id", "n_small", "n_large",
               "small_isi_rate", "large_isi_rate", "merged_isi_rate", "correlation",
               "sampling_rate_hz", "waveform_window_ms", "refractory_ms"},
}
REQUIRED = {"phase1": {"cluster_id", "n_spikes", "n_overclusters"},
            "phase2": {"small_cluster_id", "large_cluster_id", "n_small", "n_large"}}
RATES = {"isi_violation_rate", "small_isi_rate", "large_isi_rate", "merged_isi_rate"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_protocol() -> dict[str, Any]:
    return json.loads((CONFIG_DIR / "protocol_v1.json").read_text())


def require_development(dataset_id: str, scope: dict[str, Any] | None = None) -> None:
    scope = load_protocol()["scope"] if scope is None else scope
    block, sep, channel = dataset_id.rpartition("_CH")
    if not sep or not channel.isdigit() or block not in scope["development_blocks"]:
        raise ValueError(f"Not an allowed development dataset: {dataset_id}")
    if block in scope["reserved_after_prior_exposure"] or block in scope["quarantined_blocks"]:
        raise ValueError("Development and reserved/quarantined scopes overlap")


def response_schema(phase: str) -> dict[str, Any]:
    return {"type": "object", "properties": {
        "action": {"type": "string", "enum": ACTIONS[phase]},
        "rationale": {"type": "string"}},
        "required": ["action", "rationale"], "additionalProperties": False}


def observation_text(phase: str, observation: dict[str, Any], layout: str) -> str:
    if phase not in FIELDS or layout not in {"local_four", "legacy_three"}:
        raise ValueError("Unknown phase or image layout")
    if set(observation) - FIELDS[phase] or not REQUIRED[phase] <= set(observation):
        raise ValueError("Unexpected (possibly target-bearing) or missing observation fields")
    normalized = {}
    for name in sorted(FIELDS[phase]):
        value = observation.get(name)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Invalid numeric observation: {name}")
            if name in REQUIRED[phase] and (not isinstance(value, int) or value < 1):
                raise ValueError(f"Expected positive integer: {name}")
            if name in RATES and not 0 <= value <= 1:
                raise ValueError(f"ISI rates must be fractions in [0,1]: {name}")
            if name == "correlation" and not -1 <= value <= 1:
                raise ValueError("Correlation outside [-1,1]")
            if name in {"amplitude_cv", "sampling_rate_hz", "waveform_window_ms", "refractory_ms"} and value < 0:
                raise ValueError(f"Negative observation: {name}")
        elif name in REQUIRED[phase]:
            raise ValueError(f"Missing observation: {name}")
        normalized[name] = ({"fraction": value, "percent": None if value is None else value * 100}
                            if name in RATES else value)
    if phase == "phase2" and observation["small_cluster_id"] == observation["large_cluster_id"]:
        raise ValueError("A cluster cannot be merged with itself")
    protocol = load_protocol()
    key = "replay_legacy_phase1" if phase == "phase1" and layout == "legacy_three" else phase
    views = protocol["observations"][key]
    return json.dumps({"phase": phase, "image_order": views, "metrics": normalized},
                      sort_keys=True, ensure_ascii=False, allow_nan=False)


def build_request(phase: str, observation: dict[str, Any], image_urls: list[str], *,
                  skill_version: str, layout: str = "local_four") -> dict[str, Any]:
    """Build a Responses request, but NEVER send it or initialize an API client."""
    if skill_version not in {"v0", "v1"}:
        raise ValueError("Only explicitly versioned skills v0/v1 are accepted")
    observed = observation_text(phase, observation, layout)
    views = json.loads(observed)["image_order"]
    if len(image_urls) != len(views):
        raise ValueError("Image count does not match declared order")
    if any(not u.startswith("data:image/png;base64,") for u in image_urls):
        raise ValueError("Only local PNG data URLs are accepted; no remote fetch")
    skill = json.loads((CONFIG_DIR / f"skill_{skill_version}.json").read_text())
    protocol = load_protocol()
    return {
        "model": protocol["model"]["name"],
        "instructions": (CONFIG_DIR / "system_v1.txt").read_text(),
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": "DOMAIN SKILL\n" + skill[phase] + "\nOBSERVATION\n" + observed},
            *[{"type": "input_image", "image_url": u, "detail": "high"} for u in image_urls]]}],
        "reasoning": {"effort": protocol["model"]["reasoning_effort"]},
        "max_output_tokens": protocol["model"]["max_output_tokens"],
        "text": {"format": {"type": "json_schema", "name": "curation_decision_v1",
                            "strict": True, "schema": response_schema(phase)}},
    }


def config_hashes() -> dict[str, str]:
    return {p.name: sha256(p) for p in sorted(CONFIG_DIR.iterdir()) if p.is_file()}
