import hashlib
import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "run"
    / "run_legacy_prompt_rollout.py"
)
SPEC = importlib.util.spec_from_file_location("run_legacy_prompt_rollout", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_phase1_prompt_matches_legacy_contract() -> None:
    assert MODULE.LEGACY_MAX_WAVEFORMS == 5000
    prompt = MODULE.build_legacy_phase1_prompt(211, 26939, 73)
    assert "Cluster 211" in prompt
    assert "Spike count: 26939" in prompt
    assert "73 overclusters" in prompt
    assert "high violations > 0.006" in prompt
    assert '"KEEP" | "DISCARD" | "SPLIT"' in prompt
    assert "natural waveform variability" in prompt
    assert "Do NOT hallucinate baseline shift" in prompt
    assert "DISCARD immediately" not in prompt
    assert "amplitude_cv" not in prompt


def test_phase1_prompt_matches_retained_artifact_digest() -> None:
    prompt = MODULE.build_legacy_phase1_prompt(1, 88796, 266)
    assert hashlib.sha256(prompt.encode()).hexdigest() == (
        "dd70b6561107c3c70701d3173ac3b105abfaa94b3569790bdd79488eecdb4e29"
    )


def test_phase2_prompt_preserves_historical_wording() -> None:
    prompt = MODULE.build_legacy_phase2_prompt(
        small_cluster_id=443,
        n_small=652,
        small_isi_rate=0.0123,
        large_cluster_id=31,
        n_large=50821,
        large_isi_rate=0.0004,
        correlation=-0.047,
        merged_isi_rate=0.0006,
    )
    assert "small cluster 443 into large cluster 31" in prompt
    assert "NOT_MERGE\" for ALL large clusters" in prompt
    assert "Waveform correlation: -0.047" in prompt
    assert '"MERGE" | "NOT_MERGE" | "DISCARD"' in prompt


def test_phase2_prompt_matches_retained_source_prompt_artifact_digest() -> None:
    prompt = MODULE.build_legacy_phase2_prompt(
        small_cluster_id=31,
        n_small=1228,
        small_isi_rate=0.0024,
        large_cluster_id=1,
        n_large=18774,
        large_isi_rate=0.0036,
        correlation=0.712,
        merged_isi_rate=0.0039,
    )
    assert hashlib.sha256(prompt.encode()).hexdigest() == (
        "c8e7a2c7f3e2e20f3f68e5033f4131a5a9448a0e5f534fe2c621fdb21a6ef35d"
    )


def test_decision_recorder_enforces_budget(tmp_path: Path) -> None:
    recorder = MODULE.DecisionRecorder(output_dir=tmp_path, max_attempts=1)
    assert recorder.begin_attempt() == 1
    try:
        recorder.begin_attempt()
    except RuntimeError as exc:
        assert "budget exhausted" in str(exc)
    else:
        raise AssertionError("expected decision budget failure")


def test_decision_recorder_can_run_without_budget(tmp_path: Path) -> None:
    recorder = MODULE.DecisionRecorder(output_dir=tmp_path, max_attempts=None)
    assert [recorder.begin_attempt() for _ in range(100)] == list(range(1, 101))


def test_unlimited_cli_value_disables_budget() -> None:
    assert MODULE._optional_positive_int("unlimited") is None
    assert MODULE._optional_positive_int("none") is None
    assert MODULE._optional_positive_int("25") == 25
