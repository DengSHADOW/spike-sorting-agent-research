"""An unfinished run must not silently improve the reported batch score."""

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/analysis/summarize_pinned_upstream_baseline.py"
SPEC = importlib.util.spec_from_file_location("pinned_collection", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def make_batch(tmp_path):
    repo = tmp_path / "repo"
    root = repo / "output" / "new"
    channels = ["CH3", "CH20", "CH30", "CH31"]
    write_json(root / "protocol.json", {"channels": channels, "upstream_commit": "frozen"})
    for channel in channels:
        write_json(repo / "output/main_gpt-5.1" / channel / "evaluation_report.json",
                   {"overall_performance": {"overall_f1_score": 0.9}})
    return repo, root, channels


def complete(root, channel, score):
    write_json(root / channel / "status.json", {
        "status": "complete", "api_calls": 0, "accepted_decisions": 0,
        "final_units": int(score > 0), "assigned_spikes": int(score > 0),
        "performance": {"overall_precision": score, "overall_recall": score,
                        "overall_f1_score": score},
    })
    (root / channel / "final_assigns.npy").touch()


def test_empty_completed_channel_is_included_in_batch_mean(tmp_path):
    repo, root, channels = make_batch(tmp_path)
    for i, channel in enumerate(channels):
        complete(root, channel, 0.0 if i == 0 else 1.0)
    MODULE.collect(root, repo)
    result = json.loads((root / "collected_metrics.json").read_text())
    assert result["all_channels_complete"]
    assert result["macro_mean"]["f1"] == 0.75


def test_failed_channel_cannot_reuse_stale_performance(tmp_path):
    repo, root, channels = make_batch(tmp_path)
    for channel in channels:
        complete(root, channel, 1.0)
    p = root / "CH20/status.json"
    status = json.loads(p.read_text())
    status["status"] = "failed"
    write_json(p, status)
    MODULE.collect(root, repo)
    result = json.loads((root / "collected_metrics.json").read_text())
    assert not result["all_channels_complete"]
    assert result["macro_mean"] is None
    assert next(r for r in result["channels"] if r["channel"] == "CH20")["f1"] is None


def test_report_uses_frozen_budget(tmp_path):
    repo, root, channels = make_batch(tmp_path)
    p = root / "protocol.json"
    protocol = json.loads(p.read_text())
    protocol.update(max_output_tokens_per_request=4000, protocol_variant="upstream-budget-only-4000")
    write_json(p, protocol)
    MODULE.collect(root, repo)
    assert "4,000 output tokens/request" in (root / "REPORT.md").read_text()


def test_budget_override_changes_no_other_request_fields():
    import copy
    import pytest
    spec = importlib.util.spec_from_file_location("pinned_run", SCRIPT.parents[1] / "run/run_pinned_upstream_baseline.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    request = {"max_output_tokens": 1000, "model": "gpt-5.1", "reasoning": {"effort": "medium"},
               "input": [{"role": "user", "content": ["same text", "same images"]}], "text": {"format": "same schema"}}
    original = copy.deepcopy(request)
    changed = runner.apply_output_budget(request, 4000)
    assert request == original
    assert changed.pop("max_output_tokens") == 4000
    original.pop("max_output_tokens")
    assert changed == original
    with pytest.raises(runner.ExecutionStop):
        runner.apply_output_budget(request, 8000)
