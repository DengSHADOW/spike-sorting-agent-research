"""Regression checks for Stage 3 simulated RAG-memory lifecycle."""

from pathlib import Path

from src.agent.rag_memory import ContinualRAGMemory, MemoryEntry
from src.trajectories.runner import TrajectoryRunner


def _entry() -> MemoryEntry:
    return MemoryEntry(
        channel_id="ch_000",
        step=0,
        phase="phase1",
        cluster_id=1,
        target_id=None,
        waveform_template=[0.0, 1.0],
        waveform_sample=[[0.0, 1.0]],
        n_spikes=500,
        isi_rate=0.0,
        amplitude_stats={},
        gt_action="KEEP",
        gt_reasoning="seed entry",
    )


def _runner(memory_path: Path, overwrite: bool) -> TrajectoryRunner:
    # These dependencies are not used during construction; this test only
    # verifies memory lifecycle before a simulated trajectory is run.
    return TrajectoryRunner(
        cfg=None,  # type: ignore[arg-type]
        student=None,  # type: ignore[arg-type]
        teacher=None,  # type: ignore[arg-type]
        enable_rag_baseline=True,
        rag_memory_path=memory_path,
        rag_overwrite_memory=overwrite,
    )


def test_overwrite_clears_persisted_memory_once_at_runner_creation(tmp_path: Path) -> None:
    memory_path = tmp_path / "rag_memory.jsonl"
    memory = ContinualRAGMemory(memory_path=memory_path)
    memory.add(_entry())

    runner = _runner(memory_path, overwrite=True)

    assert runner.rag_memory is not None
    assert runner.rag_memory.entries == []
    assert memory_path.read_text(encoding="utf-8") == ""


def test_without_overwrite_reuses_persisted_memory(tmp_path: Path) -> None:
    memory_path = tmp_path / "rag_memory.jsonl"
    memory = ContinualRAGMemory(memory_path=memory_path)
    memory.add(_entry())

    runner = _runner(memory_path, overwrite=False)

    assert runner.rag_memory is not None
    assert len(runner.rag_memory.entries) == 1
