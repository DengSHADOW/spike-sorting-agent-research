"""HTTP coverage for serving the published Firebase tree from the local backend."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "curation_public_assets_server", ROOT / "mockups/curation-review/backend/server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class PublishedAssetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.public = self.root / "public"
        self.store = self.root / "store"
        self.public.mkdir()
        self.store.mkdir()
        self.patch_static = patch.object(server, "STATIC", self.public)
        self.patch_store = patch.object(server, "STORE", self.store)
        self.patch_static.start()
        self.patch_store.start()

        class QuietHandler(server.Handler):
            def log_message(self, *args):
                pass

        self.http = server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.http.app = SimpleNamespace(
            csrf="fixture-csrf",
            bundle={"dataset_id": "fixture-dataset", "n_spikes": 3},
            datasets=SimpleNamespace(summaries=lambda: [{"cases": 1}]))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.patch_store.stop()
        self.patch_static.stop()
        self.tmp.cleanup()

    def write(self, root, name, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def fetch(self, path, headers=None):
        return urlopen(Request(self.base + path, headers=headers or {}), timeout=5)

    def assert_asset(self, route, value, content_type):
        with self.fetch(route) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), value)
            self.assertEqual(response.headers["Content-Type"], content_type)
            self.assertEqual(int(response.headers["Content-Length"]), len(value))
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def assert_missing(self, route):
        with self.assertRaises(HTTPError) as caught:
            self.fetch(route)
        self.assertEqual(caught.exception.code, 404, route)

    def test_published_root_entry_and_required_new_scripts_are_served_verbatim(self):
        assets = {
            "index.html": (b"<main>Published Studio</main>", "text/html; charset=utf-8"),
            "server-curation.html": (b"<a href='review-studio.html'>Studio</a>", "text/html; charset=utf-8"),
            "public-data.js": (b"window.PublicData={configured:true};", "text/javascript; charset=utf-8"),
            "public-curation.js": (b"/* published mount */", "text/javascript; charset=utf-8"),
            "static-share.js": (b"/* public evidence adapter */", "text/javascript; charset=utf-8"),
            "studio-responsive.css": (b"body{margin:0}", "text/css; charset=utf-8"),
            "interface-ui.js": (b"/* shared interface */", "text/javascript; charset=utf-8"),
            "learning-channel.js": (b"/* learning view */", "text/javascript; charset=utf-8"),
            "data-sources.js": (b"/* public datasets */", "text/javascript; charset=utf-8"),
            "review-v2.html": (b"<main>Separate local draft</main>", "text/html; charset=utf-8"),
        }
        for name, (value, content_type) in assets.items():
            self.write(self.public, name, value)
            with self.subTest(name=name):
                self.assert_asset("/" + name + "?cache=published", value, content_type)
        self.assert_asset("/", assets["index.html"][0], assets["index.html"][1])

    def test_public_manifests_catalogs_and_binary_packages_have_correct_types(self):
        assets = {
            "share/manifest.json": (b'{"bundles":[]}', "application/json; charset=utf-8"),
            "share/catalogs/channel.json": (b'{"cases":[]}', "application/json; charset=utf-8"),
            "public-data/manifest.json": (b'{"datasets":[]}', "application/json; charset=utf-8"),
            "public-data/packages/channel.0123456789abcdef.bin": (b"\x1f\x8bfixture-bytes", "application/octet-stream"),
        }
        for name, (value, content_type) in assets.items():
            self.write(self.public, name, value)
            with self.subTest(name=name):
                self.assert_asset("/" + name, value, content_type)

    def test_published_evidence_wins_over_generated_store_evidence(self):
        for prefix, directory in [("review-pca", "review_pca_v1"), ("review-waveforms", "review_waveforms_full_v1")]:
            for suffix, content_type in [(".png", "image/png"), (".json", "application/json; charset=utf-8")]:
                name = "atoms/shared" + suffix
                published = b"published-" + suffix.encode()
                self.write(self.public, prefix + "/" + name, published)
                self.write(self.store, directory + "/" + name, b"old-generated-store-value")
                with self.subTest(prefix=prefix, suffix=suffix):
                    self.assert_asset("/" + prefix + "/" + name, published, content_type)

    def test_existing_store_evidence_still_loads_when_not_published(self):
        for prefix, directory in [("review-pca", "review_pca_v1"), ("review-waveforms", "review_waveforms_full_v1")]:
            for suffix, content_type in [(".png", "image/png"), (".json", "application/json; charset=utf-8")]:
                name = "atoms/local-only" + suffix
                value = b"valid-local-store-" + suffix.encode()
                self.write(self.store, directory + "/" + name, value)
                with self.subTest(prefix=prefix, suffix=suffix):
                    self.assert_asset("/" + prefix + "/" + name, value, content_type)

    def test_missing_assets_never_fall_back_to_a_different_html_page(self):
        self.write(self.public, "index.html", b"<main>Studio</main>")
        for route in ["/missing.js", "/public-data/packages/missing.bin", "/share/missing.json", "/review-pca/missing.png"]:
            with self.subTest(route=route):
                self.assert_missing(route)

    def test_traversal_hidden_files_and_unpublished_file_types_are_rejected(self):
        self.write(self.root, "secret.js", b"private-project-script")
        self.write(self.root, "secret.json", b"private-project-data")
        self.write(self.public, "firebase.json", b"deployment-config")
        self.write(self.public, "share/.private.json", b"hidden-data")
        self.write(self.public, "share/private.txt", b"not-a-published-type")
        self.write(self.public, "public-data/unapproved.json", b"not-the-manifest")
        routes = ["/../secret.js", "/%2e%2e/secret.js", "/share/../../secret.json",
                  "/share/%2e%2e/%2e%2e/secret.json", "/share/..%5c..%5csecret.json",
                  "/public-data/packages/../../secret.json", "/review-pca/../../secret.json",
                  "/review-waveforms/%2e%2e/%2e%2e/secret.json", "/share/.private.json",
                  "/share/private.txt", "/public-data/unapproved.json", "/firebase.json", "/.git/config"]
        for route in routes:
            with self.subTest(route=route):
                self.assert_missing(route)

    def test_symlink_escapes_cannot_publish_files_outside_the_frontend_tree(self):
        routes = [("root-link.js", "secret.js"), ("share/link.json", "secret.json"),
                  ("public-data/packages/link.bin", "secret.bin"), ("review-pca/link.png", "secret.png")]
        for route, name in routes:
            target = self.write(self.root, name, b"outside-the-published-tree")
            link = self.public / route
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)
            with self.subTest(route=route):
                self.assert_missing("/" + route)
        outside = self.root / "outside-images"
        outside.mkdir()
        (outside / "secret.png").write_bytes(b"outside-directory")
        (self.public / "review-waveforms").symlink_to(outside, target_is_directory=True)
        self.assert_missing("/review-waveforms/secret.png")

    def test_private_api_and_localhost_host_check_keep_their_existing_behavior(self):
        with self.fetch("/api/status") as response:
            status = json.load(response)
        self.assertEqual(status["csrf_token"], "fixture-csrf")
        self.assertEqual(status["dataset_id"], "fixture-dataset")
        self.assertFalse(status["model_calls_enabled"])
        self.write(self.public, "public-data.js", b"published-script")
        with self.assertRaises(HTTPError) as caught:
            self.fetch("/public-data.js", headers={"Host": "attacker.invalid"})
        self.assertEqual(caught.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
