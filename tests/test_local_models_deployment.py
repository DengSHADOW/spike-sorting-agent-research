"""Offline checks for download integrity and explicit GPU-only startup."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/deploy/local_models.py"
SPEC = importlib.util.spec_from_file_location("local_models_deployment", SCRIPT)
deployment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deployment)


class LocalModelsDeploymentTests(unittest.TestCase):
    def test_same_size_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weight.safetensors"
            path.write_bytes(b"bad!")
            expected = {"size": 4, "sha256": hashlib.sha256(b"good").hexdigest()}
            with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                deployment.verify_file(path, expected)

    def test_git_blob_hash_includes_git_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_bytes(b"{}\n")
            expected = {"size": 3, "git_blob_sha1": hashlib.sha1(b"blob 3\0{}\n").hexdigest()}
            self.assertEqual(deployment.verify_file(path, expected), expected["git_blob_sha1"])

    def test_index_with_missing_shard_cannot_be_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = json.dumps({"weight_map": {"layer": "missing.safetensors"}}).encode()
            config = {"cache_dir": str(root), "models": {"qwen27b": {
                "repo_id": "Qwen/test", "revision": "f" * 40, "dtype": "bfloat16",
                "files": [{"name": "model.safetensors.index.json", "size": len(payload),
                           "sha256": hashlib.sha256(payload).hexdigest()}],
            }}}
            snapshot = deployment.snapshot_path(config, "qwen27b")
            snapshot.mkdir(parents=True)
            (snapshot / "model.safetensors.index.json").write_bytes(payload)
            with patch.object(deployment, "STATE_DIR", root / "status"):
                with self.assertRaisesRegex(ValueError, "missing weight shards"):
                    deployment.verify_model(config, "qwen27b")
                state = json.loads((root / "status/qwen27b_status.json").read_text())
                self.assertEqual(state["status"], "failed_verification")

    def test_verified_model_does_not_start_on_cpu(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = deployment.read_config()
            config["cache_dir"] = str(root / "cache")
            config["models"]["qwen27b"]["files"] = []
            state_dir = root / "status"
            state_dir.mkdir()
            (state_dir / "qwen27b_status.json").write_text(json.dumps({
                "status": "verified", "revision": config["models"]["qwen27b"]["revision"],
                "files": [],
            }))
            with patch.object(deployment, "STATE_DIR", state_dir), \
                 patch.object(deployment.shutil, "which", return_value=None), \
                 patch.object(deployment.os, "execve") as execute:
                with self.assertRaisesRegex(RuntimeError, "No NVIDIA runtime"):
                    deployment.serve_model(config, "qwen27b", None, False)
                execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
