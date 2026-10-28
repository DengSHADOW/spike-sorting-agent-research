"""Model decider for controller v2: renders the observation, builds API-neutral request content,
asks a provider, validates the answer. The provider (e.g. VllmProvider) owns the API format.

Observations are the unchanged raw_member_v1 renderer; prompt wording is the frozen minimal
prompt with the action list restricted to the actions the controller offers.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from src.agent.curation_observation_v1 import observe
from src.pipeline.curation_engineering_v1 import write_json

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "configs/controller_v2/prompts.json"
PHASE = {"single": "phase1", "pair": "phase2"}
FIELDS = {"single": {"cluster_id", "n_spikes", "n_overclusters", "isi_violation_rate", "amplitude_cv"},
          "pair": {"small_cluster_id", "large_cluster_id", "n_small", "n_large", "small_isi_rate",
                   "large_isi_rate", "merged_isi_rate", "correlation"}}
COMMON = {"sampling_rate_hz", "waveform_window_ms", "refractory_ms"}
N_IMAGES = {"single": 4, "pair": 3}


def load_prompts():
    return json.loads(PROMPTS.read_text())


def instructions(prompts, kind, allowed):
    p = prompts[kind]
    lines = [f"- {a}: {p['actions'][a]}" for a in p["actions"] if a in allowed]
    names = [f'"{a}"' for a in p["actions"] if a in allowed]
    choice = " or ".join(names) if len(names) == 2 else ", ".join(names[:-1]) + ", or " + names[-1]
    return "\n".join([p["preamble"], *lines, "", "Return only a JSON object with these fields:",
                      f'- "action": one of {choice}.', '- "rationale": a brief justification.']) + "\n"


def schema(kind, allowed):
    order = list(load_prompts()[kind]["actions"])
    return {"type": "object", "properties": {"action": {"type": "string", "enum": [a for a in order if a in allowed]},
            "rationale": {"type": "string"}}, "required": ["action", "rationale"], "additionalProperties": False}


def content(prompts, kind, obs, pngs, allowed):
    if set(obs) != FIELDS[kind] | COMMON or len(pngs) != N_IMAGES[kind]:
        raise ValueError("Unexpected observation fields or image count (labels are prohibited)")
    for key, value in obs.items():
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError("Non-numeric observation")
    definitions = dict(prompts["definitions"]["common"], **prompts["definitions"][kind])
    text = json.dumps(dict(metrics=dict(sorted(obs.items())), definitions=definitions), sort_keys=True, allow_nan=False)
    return dict(instructions=instructions(prompts, kind, allowed), text=text, images=list(pngs),
                schema=schema(kind, allowed), schema_name=f"curation_{kind}_decision")


class ModelDecider:
    kind = "MODEL_VIA_PROVIDER"

    def __init__(self, provider, *, view="raw_member_v1"):
        self.provider, self.view, self.prompts = provider, view, load_prompts()

    def decide(self, m, kind, cid, target, allowed, *, folder, sampling_rate):
        obs, urls, pngs, display = observe(m, PHASE[kind], cid, target, sampling_rate=sampling_rate, view=self.view)
        c = content(self.prompts, kind, obs, pngs, allowed)
        names = []
        for png, meta in zip(pngs, display["images"]):
            if hashlib.sha256(png).hexdigest() != meta["sha256"]:
                raise ValueError("Observation image hash mismatch")
            (folder / (meta["name"] + ".png")).write_bytes(png)
            names.append(dict(local_file=meta["name"] + ".png", sha256=meta["sha256"]))
        write_json(folder / "request_content.json", dict(c, images=names, observation=obs))
        answer = self.provider.respond(c, folder=folder)
        if (not isinstance(answer, dict) or set(answer) != {"action", "rationale"}
                or answer["action"] not in allowed or not isinstance(answer["rationale"], str)):
            raise ValueError("Answer outside the offered schema")
        return answer
