"""Offline tests for run binding in score_terminal_matching (scores come from the shared cores)."""
import json
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_terminal_matching as stm  # noqa: E402


def manifest(path: Path, **fields):
    path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(json.dumps(fields))


def terminal(run: Path, dataset: str, assigns):
    folder = run / "datasets" / dataset
    folder.mkdir(parents=True, exist_ok=True)
    a, tree = np.asarray(assigns, dtype=np.int64), np.zeros((4, 1))
    np.savez(folder / "terminal.npz", assigns=a, tree=tree)
    return stm.arrays_hash(a, tree)


def test_lineage_follows_source_chain_and_ignores_unrelated_runs(tmp_path):
    root, resume = tmp_path / "root", tmp_path / "resume"
    manifest(root, datasets=[{"dataset_id": "D", "mat_path": "root.mat", "mat_sha256": "a"}])
    manifest(resume, source=str(root))
    manifest(tmp_path / "other", datasets=[{"dataset_id": "D", "mat_path": "other.mat", "mat_sha256": "b"}])
    chain, root_manifest = stm.lineage(resume)
    assert chain == [resume.resolve(), root.resolve()]
    assert root_manifest["datasets"][0]["mat_path"] == "root.mat"  # not the unrelated run's MAT


def test_terminal_is_searched_only_inside_the_lineage(tmp_path):
    run, other = tmp_path / "run", tmp_path / "other"
    h = terminal(other, "D", [0, 1, 1])
    with pytest.raises(ValueError):
        stm.find_terminal([run], "D", h)  # identical state elsewhere is never picked up


def test_identical_terminal_in_two_lineage_runs_does_not_fail(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    h = terminal(a, "D", [0, 1, 1])
    assert terminal(b, "D", [0, 1, 1]) == h
    assert stm.find_terminal([b, a], "D", h) == b / "datasets" / "D" / "terminal.npz"


def test_mat_registration_must_be_unique(tmp_path):
    dup = {"datasets": [{"dataset_id": "D", "mat_path": "x", "mat_sha256": "1"},
                        {"dataset_id": "D", "mat_path": "y", "mat_sha256": "2"}]}
    with pytest.raises(ValueError):
        stm.bind_mat(dup, "D")


def test_report_puts_one_to_one_first_with_cluster_counts_in_the_same_row():
    gt = np.array([1] * 10 + [0] * 30)
    pred = np.array([5] * 10 + [6] * 20 + [7] * 10)
    times = np.arange(len(gt)) * 0.01
    r = dict(dataset_id="D", **stm.score_dataset(pred, gt, gt, times, 30000.0))
    text = "\n".join(stm.report_lines("t", [r]))
    assert text.index("Primary: one-to-one") < text.index("Supplementary: historical many-to-one")
    row = next(l for l in text.splitlines() if l.startswith("| D |"))
    assert "| 2 | 2 |" in row  # unmatched clusters and pure-noise clusters sit next to the scores
    assert r["one_to_one"]["full_output"]["fp"] == 30  # unmatched noise clusters stay in the denominator
