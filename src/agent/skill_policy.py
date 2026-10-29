"""Validation shared by the decider and feedback optimizers; no model or dataset imports."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def validate_skill(skill):
    if not isinstance(skill, dict) or not isinstance(skill.get("version"), str) or not skill["version"].strip():
        raise ValueError("Skill needs a nonempty version")
    rules = skill.get("rules")
    if not isinstance(rules, list) or not 1 <= len(rules) <= 100:
        raise ValueError("Skill needs 1–100 rules")
    ids = set()
    for rule in rules:
        if (not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or not rule["id"].strip()
                or rule["id"] in ids or rule.get("scope") not in ("single", "pair", "both")
                or not isinstance(rule.get("text"), str) or not rule["text"].strip()):
            raise ValueError("Invalid or duplicate skill rule")
        ids.add(rule["id"])
    if sum(len(r["text"]) for r in rules) > 40000:
        raise ValueError("Skill exceeds the fixed character budget")
    return skill


def load_skill(path):
    raw = Path(path).read_bytes()
    return validate_skill(json.loads(raw)), hashlib.sha256(raw).hexdigest()
