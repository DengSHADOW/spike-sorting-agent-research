"""Safety regressions for real-data VLM decisions."""

from __future__ import annotations

import numpy as np
import pytest

from src.agent import runner
from src.agent.context import build_phase2_prompt
from src.ablation import runner as ablation_runner
from src.ablation.pipeline import PureVLMCurationPipeline as AblationPipeline
from src.cluster.manager import ClusterManager
from src.pipeline.pure import PureVLMCurationPipeline


def _manager(assigns: np.ndarray) -> ClusterManager:
    n_spikes = len(assigns)
    return ClusterManager(
        initial_assigns=assigns,
        overcluster_assigns=assigns.copy(),
        hierarchy_tree=np.empty((4, 0), dtype=float),
        spike_times=np.arange(n_spikes, dtype=float) * 0.01,
        waveforms=np.tile(np.linspace(0, 1, 8), (n_spikes, 1)),
    )


def test_parse_failure_abstains_instead_of_discarding(monkeypatch) -> None:
    monkeypatch.setattr(runner, "call_vlm_api", lambda **kwargs: "not json")
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    decision = runner.vlm_phase1_cluster_decision(
        cluster_id=1,
        waveforms=np.tile(np.linspace(0, 1, 8), (5, 1)),
        spike_times=np.arange(5, dtype=float) * 0.01,
        overcluster_composition=[1],
        hierarchy_tree=np.empty((4, 0), dtype=float),
    )
    assert decision["action"] == "ABSTAIN"
    assert decision["decision_status"] == "parse_error"


def test_provider_failure_never_substitutes_mock(monkeypatch) -> None:
    def fail_call(**kwargs):
        raise ConnectionError("model server unavailable")

    monkeypatch.setattr(runner, "VLM_AVAILABLE", True)
    monkeypatch.setattr(runner, "call_vlm", fail_call)
    with pytest.raises(RuntimeError, match="no mock fallback"):
        runner.call_vlm_api(
            prompt="real run",
            images=[],
            provider="vllm",
            model="local-model",
            max_retries=1,
        )


def test_phase2_without_large_target_preserves_clusters() -> None:
    manager = _manager(np.array([1, 1, 2, 2]))
    pipeline = PureVLMCurationPipeline(
        manager=manager,
        features=None,
        small_cluster_threshold=10,
        auto_discard_threshold=0,
        final_minimum_threshold=0,
        use_mock=True,
    )
    result = pipeline.run_phase2_vlm_merge_decisions([1, 2])
    assert result == [1, 2]
    assert manager.get_active_clusters() == [1, 2]
    assert {row["action"] for row in pipeline.actions} == {"ABSTAIN"}


def test_all_not_merge_preserves_distinct_small_cluster(monkeypatch) -> None:
    manager = _manager(np.array([1, 1, 2, 2, 2, 2, 2]))
    monkeypatch.setattr(
        "src.pipeline.pure.vlm_phase1_cluster_decision",
        lambda **kwargs: {"action": "KEEP", "rationale": "valid"},
    )
    monkeypatch.setattr(
        "src.pipeline.pure.vlm_phase2_merge_decision",
        lambda **kwargs: {"action": "NOT_MERGE", "rationale": "distinct"},
    )
    pipeline = PureVLMCurationPipeline(
        manager=manager,
        features=None,
        small_cluster_threshold=4,
        auto_discard_threshold=0,
        final_minimum_threshold=0,
    )
    result = pipeline.run_phase2_vlm_merge_decisions([1, 2])
    assert result == [1, 2]
    assert manager.get_active_clusters() == [1, 2]
    assert any(row["action"] == "KEEP" and row["cluster_ids"] == [1] for row in pipeline.actions)


@pytest.mark.parametrize(
    ("pipeline_path", "pipeline_class"),
    [
        ("src.pipeline.pure", PureVLMCurationPipeline),
        ("src.ablation.pipeline", AblationPipeline),
    ],
)
def test_split_large_cluster_is_not_used_as_merge_target(
    monkeypatch, pipeline_path, pipeline_class
) -> None:
    manager = _manager(np.array([1, 1, 2, 2, 2, 2, 2]))
    monkeypatch.setattr(
        f"{pipeline_path}.vlm_phase1_cluster_decision",
        lambda **kwargs: {"action": "SPLIT", "rationale": "mixed unit"},
    )

    def unexpected_merge(**kwargs):
        raise AssertionError("Unvalidated large cluster was used as a merge target")

    monkeypatch.setattr(f"{pipeline_path}.vlm_phase2_merge_decision", unexpected_merge)
    pipeline = pipeline_class(
        manager=manager,
        features=None,
        small_cluster_threshold=4,
        auto_discard_threshold=0,
        final_minimum_threshold=0,
    )
    result = pipeline.run_phase2_vlm_merge_decisions([1, 2])
    assert result == [1, 2]
    assert manager.get_active_clusters() == [1, 2]
    assert {row["action"] for row in pipeline.actions} == {"ABSTAIN"}


def test_zero_size_filters_are_disabled() -> None:
    manager = _manager(np.array([1, 1, 2]))
    pipeline = PureVLMCurationPipeline(
        manager=manager,
        features=None,
        auto_discard_threshold=0,
        final_minimum_threshold=0,
        use_mock=True,
    )
    assert pipeline.run_phase0_automatic_filtering() == [1, 2]
    assert pipeline.run_phase3_final_filter([1, 2]) == [1, 2]
    assert manager.get_active_clusters() == [1, 2]


def test_merge_prompt_matches_preserve_semantics() -> None:
    prompt = build_phase2_prompt(
        small_cluster_id=1,
        n_small=2,
        small_isi_rate=0.0,
        large_cluster_id=2,
        n_large=5,
        large_isi_rate=0.0,
        correlation=0.0,
        merged_isi_rate=0.0,
    )
    assert 'If all candidates are "NOT_MERGE", preserve the small cluster' in prompt
    assert '"NOT_MERGE" for ALL large clusters → small cluster will be DISCARDED' not in prompt


def test_ablation_parse_failure_abstains(monkeypatch) -> None:
    monkeypatch.setattr(ablation_runner, "call_vlm_api", lambda **kwargs: "not json")
    monkeypatch.setattr(ablation_runner.time, "sleep", lambda _: None)
    decision = ablation_runner.vlm_phase1_cluster_decision(
        cluster_id=1,
        waveforms=np.tile(np.linspace(0, 1, 8), (5, 1)),
        spike_times=np.arange(5, dtype=float) * 0.01,
        overcluster_composition=[1],
        hierarchy_tree=np.empty((4, 0), dtype=float),
    )
    assert decision["action"] == "ABSTAIN"
    assert decision["decision_status"] == "parse_error"


def test_ablation_provider_failure_never_substitutes_mock(monkeypatch) -> None:
    def fail_call(**kwargs):
        raise ConnectionError("model server unavailable")

    monkeypatch.setattr(ablation_runner, "VLM_AVAILABLE", True)
    monkeypatch.setattr(ablation_runner, "call_vlm", fail_call)
    with pytest.raises(ConnectionError, match="model server unavailable"):
        ablation_runner.call_vlm_api(
            prompt="real ablation",
            images=[],
            provider="vllm",
            model="local-model",
            max_retries=1,
        )


def test_ablation_without_large_target_preserves_clusters() -> None:
    manager = _manager(np.array([1, 1, 2, 2]))
    pipeline = AblationPipeline(
        manager=manager,
        features=None,
        small_cluster_threshold=10,
        auto_discard_threshold=0,
        final_minimum_threshold=0,
        use_mock=True,
    )
    result = pipeline.run_phase2_vlm_merge_decisions([1, 2])
    assert result == [1, 2]
    assert manager.get_active_clusters() == [1, 2]
    assert {row["action"] for row in pipeline.actions} == {"ABSTAIN"}


def test_ablation_all_not_merge_preserves_distinct_cluster(monkeypatch) -> None:
    manager = _manager(np.array([1, 1, 2, 2, 2, 2, 2]))
    monkeypatch.setattr(
        "src.ablation.pipeline.vlm_phase1_cluster_decision",
        lambda **kwargs: {"action": "KEEP", "rationale": "valid"},
    )
    monkeypatch.setattr(
        "src.ablation.pipeline.vlm_phase2_merge_decision",
        lambda **kwargs: {"action": "NOT_MERGE", "rationale": "distinct"},
    )
    pipeline = AblationPipeline(
        manager=manager,
        features=None,
        small_cluster_threshold=4,
        auto_discard_threshold=0,
        final_minimum_threshold=0,
    )
    result = pipeline.run_phase2_vlm_merge_decisions([1, 2])
    assert result == [1, 2]
    assert manager.get_active_clusters() == [1, 2]
    assert any(row["action"] == "KEEP" and row["cluster_ids"] == [1] for row in pipeline.actions)
