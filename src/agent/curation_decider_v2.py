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


# ====================================================================== harness v2 (§3.7.5-3.7.7)
# Contract + versioned skill + observation v2, and an output that records each image before the action.
from src.agent import curation_observation_v2 as obs_v2

CONTRACT = ROOT / "configs/controller_v2/contract.txt"
SKILL_V1 = ROOT / "configs/controller_v2/skills/skill_v1.json"
OUTPUT_VERSION = "observe-then-act-v1"
ACTION_TEXT = {
    "single": {"KEEP": "retain the cluster.",
               "SPLIT": "undo the cluster's last hierarchical merge; both parts are judged next.",
               "DISCARD": "remove the cluster from the active sorting."},
    "pair": {"MERGE": "combine the source and the target into one neuron.",
             "NOT_MERGE": "reject this pair without deleting the source.",
             "DISCARD": "remove the source because it is itself noise, not merely because it differs from the target."},
}
# (field, image it reads, categories, what to look at). Order = generation order under the JSON schema.
OBSERVATIONS = {
    "single": [
        ("waveform", "waveform", ("one_family", "multiple_families", "not_spike_like", "unclear"),
         "Do the spikes form one consistent waveform family, several distinct families, or shapes that do not "
         "look like spikes?"),
        ("refractory", "isi_acg", ("clean", "partly_contaminated", "absent", "noise_like", "unclear"),
         "Is the autocorrelogram's ±2 ms zone empty relative to its shoulders, partly filled, as full as the "
         "shoulders, or is the autocorrelogram noise-like (triangular or strongly periodic)?"),
        ("amplitude_time", "amplitude", ("stable", "drifting", "abrupt_segments", "unclear"),
         "Right panel: is the amplitude stable over the recording, slowly drifting, or changing abruptly in some "
         "periods?"),
        ("tree", "tree", ("several_dense_components", "single_component", "single_leaf"),
         "Does the tree contain several dense components each made of many subclusters, essentially one component, "
         "or a single leaf?"),
        ("split_preview", "split_preview", ("different_units", "same_unit", "unclear"),
         "Do the two parts look like different units (different waveform families, no refractory gap in their "
         "cross-correlogram) or the same unit (similar waveforms, refractory gap)?"),
    ],
    "pair": [
        ("overlay", "overlay", ("similar", "different", "unclear"),
         "On the shared axes, are source and target similar in waveform shape and amplitude?"),
        ("ccg", "ccg_acg", ("refractory_clean", "contaminated", "short_latency_asymmetric_peak", "unclear"),
         "Left panel, against the time-reversed control: is the cross-correlogram's ±2 ms zone clean, "
         "contaminated, or is there a short-latency asymmetric peak?"),
        ("amplitude_time", "amplitude_time", ("continuous_or_handoff", "separate", "unclear"),
         "Do the two amplitudes continue into each other over time (overlapping, or one fading as the other "
         "appears), or are they separate?"),
        ("source_validity", None, ("valid", "noise_like", "unclear"),
         "All images: is the source itself a plausible neuronal cluster? Judge this apart from whether it matches "
         "the target."),
    ],
}


def load_skill(path=SKILL_V1):
    raw = Path(path).read_bytes()
    skill = json.loads(raw)
    ids = [r["id"] for r in skill["rules"]]
    if len(set(ids)) != len(ids) or any(r["scope"] not in ("single", "pair", "both") or not r["text"].strip()
                                        for r in skill["rules"]):
        raise ValueError("Invalid skill rules")
    return skill, hashlib.sha256(raw).hexdigest()


def fields_for(kind, allowed):
    return [f for f in OBSERVATIONS[kind] if f[0] != "split_preview" or "SPLIT" in allowed]


def instructions_v2(contract, skill, kind, allowed):
    order = list(ACTION_TEXT[kind])
    actions = [a for a in order if a in allowed]
    rules = [f"- [{r['id']}] {r['text']}" for r in skill["rules"] if r["scope"] in (kind, "both")]
    what = "a single-cluster judgement" if kind == "single" else "a pair judgement (source = smaller cluster)"
    lines = [contract.rstrip(), "", f"# Domain skill ({skill['version']})", *rules, "",
             f"# This request: {what}", "Offered actions:", *[f"- {a}: {ACTION_TEXT[kind][a]}" for a in actions], "",
             "# Output fields, in this order"]
    for name, image, labels, question in fields_for(kind, allowed):
        where = f"image '{image}'" if image else "all images"
        lines.append(f"- {name} ({where}): label one of {', '.join(labels)}; note: one sentence on what the image "
                     f"shows. {question}")
    lines += [f"- action: one of {', '.join(actions)}.",
              "- rationale: at most two sentences naming the observations above that led to the action.",
              "Return only the JSON object."]
    return "\n".join(lines) + "\n"


def schema_v2(kind, allowed):
    props = {name: {"type": "object", "properties": {"label": {"type": "string", "enum": list(labels)},
                                                     "note": {"type": "string"}},
                    "required": ["label", "note"], "additionalProperties": False}
             for name, _, labels, _ in fields_for(kind, allowed)}
    props["action"] = {"type": "string", "enum": [a for a in ACTION_TEXT[kind] if a in allowed]}
    props["rationale"] = {"type": "string"}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def check_answer(answer, kind, allowed):
    fields = fields_for(kind, allowed)
    if not isinstance(answer, dict) or set(answer) != {f[0] for f in fields} | {"action", "rationale"}:
        raise ValueError("Answer fields differ from the offered schema")
    for name, _, labels, _ in fields:
        value = answer[name]
        if not isinstance(value, dict) or set(value) != {"label", "note"} or value["label"] not in labels \
                or not isinstance(value["note"], str):
            raise ValueError(f"Invalid observation field {name}")
    if answer["action"] not in allowed or not isinstance(answer["rationale"], str):
        raise ValueError("Answer outside the offered schema")
    return answer


class SkillDecider:
    """Harness v2 decider: observation v2 + frozen contract + versioned skill; observe-then-act output.
    prepare() renders and writes the request (serial, matplotlib); answer() only calls the provider, so the
    controller may run answers in parallel."""

    kind = "SKILL_MODEL_VIA_PROVIDER"

    def __init__(self, provider, *, skill_path=SKILL_V1, contract_path=CONTRACT, numbers="default"):
        self.provider, self.numbers = provider, numbers
        self.contract = Path(contract_path).read_text(encoding="utf-8")
        self.contract_sha256 = hashlib.sha256(self.contract.encode()).hexdigest()
        self.skill, self.skill_sha256 = load_skill(skill_path)

    def describe(self):
        return dict(observation=obs_v2.VERSION, numbers=self.numbers, output=OUTPUT_VERSION,
                    contract_sha256=self.contract_sha256, skill_version=self.skill["version"],
                    skill_sha256=self.skill_sha256, provider=getattr(self.provider, "kind", None))

    def prepare(self, m, kind, cid, target, allowed, *, folder, sampling_rate):
        values, pngs, info = obs_v2.observe(m, kind, cid, target, sampling_rate=sampling_rate, numbers=self.numbers)
        names = [i["name"] for i in info["images"]]
        if kind == "single" and ("split_preview" in names) != ("SPLIT" in allowed):
            raise ValueError("Split preview and offered SPLIT disagree")
        question = (f"Single-cluster judgement on cluster {cid}." if kind == "single"
                    else f"Pair judgement: source cluster {cid}, target cluster {target}.")
        preface = "\n".join([question, "Images, in order:"] +
                            [f"{n}. {i['name']}: {i['description']}" for n, i in enumerate(info["images"], 1)])
        text = json.dumps(dict(measurements=values, definitions=obs_v2.definitions(values)), sort_keys=True,
                          allow_nan=False)
        content = dict(instructions=instructions_v2(self.contract, self.skill, kind, allowed), preface=preface,
                       text=text, images=pngs, images_first=True, schema=schema_v2(kind, allowed),
                       schema_name=f"curation_{kind}_observe_then_act")
        for png, meta in zip(pngs, info["images"]):
            (folder / f"{meta['name']}.png").write_bytes(png)
        write_json(folder / "request_content.json", dict(
            {k: v for k, v in content.items() if k != "images"}, images=info["images"], measurements=values,
            **self.describe()))
        return content

    def answer(self, content, allowed, *, folder):
        kind = "single" if content["schema_name"].startswith("curation_single") else "pair"
        return check_answer(self.provider.respond(content, folder=folder), kind, allowed)

    def decide(self, m, kind, cid, target, allowed, *, folder, sampling_rate):
        return self.answer(self.prepare(m, kind, cid, target, allowed, folder=folder, sampling_rate=sampling_rate),
                           allowed, folder=folder)
