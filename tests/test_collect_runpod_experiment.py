import importlib.util
import json
import tarfile
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "aggregate"
    / "collect_runpod_experiment.py"
)
SPEC = importlib.util.spec_from_file_location("collect_runpod_experiment", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_collects_small_results_and_excludes_unlisted_data(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run_manifest.json").write_text('{"model": "example"}\n')
    (run_dir / "detail_predictions.csv").write_text("gt,pred\nKEEP,KEEP\n")
    (run_dir / "summary_accuracy.json").write_text('[{"accuracy": 1.0}]\n')
    (run_dir / "diagnostic.png").write_bytes(b"not-a-real-image")
    (run_dir / "source.mat").write_bytes(b"not-a-real-mat")
    extra_log = tmp_path / "pod-console.log"
    extra_log.write_text("server started\n")

    collection_dir, archive_path = MODULE.collect_experiment(
        run_dir=run_dir,
        output_root=tmp_path / "collected",
        run_name="qwen-ch30-smoke",
        extra_files=[extra_log],
        repo_root=Path(__file__).resolve().parents[1],
    )

    assert (collection_dir / "artifacts" / "detail_predictions.csv").is_file()
    assert (collection_dir / "logs" / "pod-console.log").is_file()
    assert not list(collection_dir.rglob("*.png"))
    assert not list(collection_dir.rglob("*.mat"))

    manifest = json.loads((collection_dir / "collection_manifest.json").read_text())
    assert manifest["schema_version"] == "runpod-experiment-collection-v1"
    assert len(manifest["artifacts"]) == 4
    assert all(len(item["sha256"]) == 64 for item in manifest["artifacts"])
    assert manifest["policy"]["raw_data_images_model_weights_and_env_excluded"] is True

    assert archive_path is not None and archive_path.is_file()
    with tarfile.open(archive_path, "r:gz") as archive:
        names = archive.getnames()
    assert any(name.endswith("collection_manifest.json") for name in names)
    assert not any(name.endswith("diagnostic.png") for name in names)


@pytest.mark.parametrize("filename", ["raw.mat", "diagnostic.png", ".env"])
def test_rejects_forbidden_extra_files(tmp_path: Path, filename: str) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run_manifest.json").write_text("{}\n")
    forbidden = tmp_path / filename
    forbidden.write_text("secret-or-large-data\n")

    with pytest.raises(ValueError):
        MODULE.collect_experiment(
            run_dir=run_dir,
            output_root=tmp_path / "collected",
            run_name="forbidden-extra",
            extra_files=[forbidden],
            create_archive=False,
            repo_root=Path(__file__).resolve().parents[1],
        )
