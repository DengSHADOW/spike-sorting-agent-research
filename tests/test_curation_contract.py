import copy
import hashlib
import json
import socket

import pytest

from src.agent.curation_contract import (
    CONFIG_DIR, build_request, load_protocol, observation_text,
    require_development, response_schema,
)
from src.agent.context import build_phase1_prompt, build_phase2_prompt


OBS = {"cluster_id": 1, "n_spikes": 10, "n_overclusters": 2,
       "isi_violation_rate": 0.006, "amplitude_cv": 0.2}
IMAGES = ["data:image/png;base64,iVBORw0KGgo="] * 4


def test_v0_is_exact_local_rule_extraction():
    v0 = json.loads((CONFIG_DIR / "skill_v0.json").read_text())
    assert v0["phase1"] == build_phase1_prompt(1, 1, 1, 0.0, 0.0).split("You are judging Cluster", 1)[0]
    assert v0["phase2"] == build_phase2_prompt(1, 1, 0.0, 2, 2, 0.0, 0.0, 0.0).split("You are deciding whether", 1)[0]
    source = CONFIG_DIR.parents[1] / v0["source"]["path"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == v0["source"]["sha256"]


def test_assembly_needs_no_network_and_changes_only_skill(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("Network access forbidden")
    monkeypatch.setattr(socket, "socket", forbidden)
    old = build_request("phase1", OBS, IMAGES, skill_version="v0")
    new = build_request("phase1", OBS, IMAGES, skill_version="v1")
    assert old["instructions"] == new["instructions"]
    assert old["input"][0]["content"][1:] == new["input"][0]["content"][1:]
    a, b = copy.deepcopy(old), copy.deepcopy(new)
    ta = a["input"][0]["content"].pop(0)["text"]
    tb = b["input"][0]["content"].pop(0)["text"]
    assert a == b and ta != tb
    assert ta.split("\nOBSERVATION\n")[1] == tb.split("\nOBSERVATION\n")[1]
    assert "temperature" not in new
    assert new["model"] == "gpt-6-astra"


def test_units_and_missing_values():
    data = json.loads(observation_text("phase1", OBS, "local_four"))
    assert data["metrics"]["isi_violation_rate"] == {"fraction": 0.006, "percent": 0.6}
    assert data["metrics"]["refractory_ms"] is None


@pytest.mark.parametrize("extra", [{"label_action": "KEEP"}, {"gt_unit": 31},
                                  {"isi_violation_rate": float("nan")}, {"isi_violation_rate": 2}])
def test_rejects_target_fields_and_bad_numbers(extra):
    with pytest.raises(ValueError):
        build_request("phase1", dict(OBS, **extra), IMAGES, skill_version="v1")


def test_explicit_image_layout():
    with pytest.raises(ValueError):
        build_request("phase1", OBS, IMAGES[:3], skill_version="v1")
    request = build_request("phase1", OBS, IMAGES[:3], skill_version="v1", layout="legacy_three")
    assert len(request["input"][0]["content"]) == 4


@pytest.mark.parametrize("phase", ["phase1", "phase2"])
def test_strict_schema_and_abstain_boundary(phase):
    schema = response_schema(phase)
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert "ABSTAIN" not in schema["properties"]["action"]["enum"]


def test_development_scope_is_fail_closed():
    require_development("cM2-e004_004-006_CH3")
    for dataset in ["cM2-e007_012-017_CH3", "cM2-e004_001-003_CH3", "CH3"]:
        with pytest.raises(ValueError):
            require_development(dataset)
    assert load_protocol()["scope"]["clean_held_out_blocks"] == []


def test_phase2_requires_distinct_targets():
    obs = {"small_cluster_id": 1, "large_cluster_id": 1, "n_small": 2, "n_large": 2}
    with pytest.raises(ValueError):
        build_request("phase2", obs, IMAGES[:3], skill_version="v1")
