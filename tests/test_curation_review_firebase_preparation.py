"""Offline boundaries for the private cloud migration packager."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1] / "mockups/curation-review/backend"
sys.path.insert(0,str(BACKEND))
spec=importlib.util.spec_from_file_location("prepare_firebase",BACKEND/"prepare_firebase.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PreparationBoundaries(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)/"repository"
        self.root.mkdir()
        self.patch=patch.object(module,"ROOT",self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_regular_source_allowed(self):
        path=self.root/"evidence.json"
        path.write_text("{}")
        self.assertEqual(module.scoped_file(path),path)

    def test_external_source_rejected(self):
        path=self.root.parent/"secret.json"
        path.write_text("private")
        with self.assertRaises(ValueError):
            module.scoped_file(path)

    def test_symlink_source_rejected_even_within_repository(self):
        path=self.root/"original.json"
        path.write_text("{}")
        link=self.root/"alias.json"
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            module.scoped_file(link)

    def test_destination_cannot_be_original_input_or_arbitrary_directory(self):
        with self.assertRaisesRegex(ValueError,"new child directory"):
            module.prepare(self.root/"data")

    def test_existing_snapshot_cannot_be_overwritten(self):
        path=self.root/"output/firebase_migration/existing"
        path.mkdir(parents=True)
        sentinel=path/"inventory.json"
        sentinel.write_text("unchanged")
        with self.assertRaisesRegex(ValueError,"already exists"):
            module.prepare(path)
        self.assertEqual(sentinel.read_text(),"unchanged")

    def test_destination_symlink_cannot_escape_staging_root(self):
        parent=self.root/"output/firebase_migration"
        parent.mkdir(parents=True)
        (parent/"escape").symlink_to(self.root.parent,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,"new child directory"):
            module.prepare(parent/"escape/new")


if __name__=="__main__":
    unittest.main()
