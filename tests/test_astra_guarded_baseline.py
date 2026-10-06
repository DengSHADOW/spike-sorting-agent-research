"""Offline coverage: API contract, conservative budget, and per-channel guards."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

RUN = Path(__file__).resolve().parents[1] / "scripts/run"
sys.path.insert(0, str(RUN))
import run_astra_guarded_baseline as m


def test_request_changes_only_declared_fields(tmp_path):
    request = {"model": "gpt-5.1", "reasoning": {"effort": "medium"},
               "max_output_tokens": 4000, "input": ["same three images and prompt"],
               "text": {"format": "same action+rationale schema"}}
    original = copy.deepcopy(request)
    actual = m.Experiment(tmp_path, "CH30").request(request)
    assert request == original
    assert actual == dict(original, model="gpt-6-astra", reasoning={"effort": "high"}, service_tier="default")


@pytest.mark.parametrize("channel", ["CH30", "CH31"])
def test_unprotected_channel_actions_unchanged(tmp_path, channel):
    ex = m.Experiment(tmp_path, channel)
    sentinel = object()
    assert ex.controller(sentinel) is sentinel
    for stage in ("phase1", "phase2"):
        d = {"action": "DISCARD", "rationale": "test"}
        assert ex.protect(d, stage, {}, tmp_path, 1) == d


@pytest.mark.parametrize("n,composition,expected", [(3999, [1, 2], "DISCARD"),
                           (4000, [1, 2], "SPLIT"), (4000, [1], "ABSTAIN")])
def test_phase1_guard_boundary(tmp_path, n, composition, expected):
    d = {"action": "DISCARD", "rationale": "test"}
    actual = m.Experiment(tmp_path, "CH3").protect(d, "phase1",
             {"spike_times": range(n), "overcluster_composition": composition}, tmp_path, 1)
    assert actual["action"] == expected
    assert d["action"] == "DISCARD"


def test_phase2_guard_and_keep_unchanged(tmp_path):
    ex = m.Experiment(tmp_path, "CH20")
    assert ex.protect({"action": "DISCARD", "rationale": "x"}, "phase2", {}, tmp_path, 1)["action"] == "ABSTAIN"
    for a in ("KEEP", "SPLIT", "MERGE", "NOT_MERGE"):
        assert ex.protect({"action": a, "rationale": "x"}, "phase2", {}, tmp_path, 1)["action"] == a


def test_budget_refuses_request_before_overspend():
    ledger = {"limit_usd": 30, "charged_usd": 29.99, "calls": []}
    original = copy.deepcopy(ledger)
    with pytest.raises(m.BudgetStop):
        m.reserve(ledger, "CH30", 1, 2000, 4000)
    assert ledger == original


def test_reservation_covers_maximum_output_and_cache_writes():
    ledger = {"limit_usd": 30, "charged_usd": 0, "calls": []}
    r = m.reserve(ledger, "CH30", 1, 2000, 4000)
    charge, estimate = m.costs({"input_tokens": 2000, "output_tokens": 4000})
    assert r["reserved_usd"] > charge >= estimate
    assert ledger["charged_usd"] == r["reserved_usd"]


def test_incomplete_response_does_not_become_discard(tmp_path):
    with pytest.raises(m.base.ExecutionStop):
        m.Experiment(tmp_path, "CH3").validate_response({"status": "incomplete"}, "", {})


def test_failed_channel_has_no_score_or_mixed_mean(tmp_path):
    m.base.write_json(tmp_path / "budget.json", {"charged_usd": 1, "estimated_standard_usd": 0.9})
    (tmp_path / "CH3").mkdir()
    m.base.write_json(tmp_path / "CH3/status.json", {"status": "failed", "performance": {"overall_f1_score": 1}})
    rows = m.collect(tmp_path)
    assert next(r for r in rows if r["channel"] == "CH3")["f1"] is None
    assert json.loads((tmp_path / "collected_metrics.json").read_text())["macro_mean"] is None


def test_protected_controller_preserves_when_no_large_target(tmp_path):
    # Import the real protected controller, with an intentionally minimal manager.
    repo = RUN.parents[1]
    sys.path.insert(0, str(repo))
    from src.pipeline.pure import PureVLMCurationPipeline
    manager = SimpleNamespace(get_active_clusters=lambda: [1, 2],
                              get_cluster_info=lambda cid: {"spike_times": [0.1], "waveforms": [], "overclusters": [cid]})
    pipeline = PureVLMCurationPipeline(manager, None, auto_discard_threshold=0, final_minimum_threshold=0)
    assert pipeline.run_phase0_automatic_filtering() == [1, 2]
    assert pipeline.run_phase2_vlm_merge_decisions([1, 2]) == [1, 2]
    assert pipeline.run_phase3_final_filter([1, 2]) == [1, 2]
    assert all(a["action"] == "ABSTAIN" for a in pipeline.actions)
