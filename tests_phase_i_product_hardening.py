import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from shard_engine import __version__, cli, feedback, model_browser, paths, soup_adapter, storage, training, updater
from shard_engine.server import app
class TestPhaseIProductHardening(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        self._env_patch = patch.dict(os.environ, {
            "SHARD_DATA_DIR": str(self._root / "data"),
            "SHARD_CONFIG_DIR": str(self._root / "config"),
            "SHARD_CACHE_DIR": str(self._root / "cache"),
            "SHARD_RUN_DIR": str(self._root / "run"),
            "SHARD_MODELS_DIR": str(self._root / "models"),
        })
        self._env_patch.start()
        for p in paths.all_dirs().values():
            Path(p).mkdir(parents=True, exist_ok=True)
        self.client = TestClient(app)
        self.owner_headers = {"X-Shard-Owner-Token": training.get_owner_token()}
    def tearDown(self):
        try:
            training._ACTIVE_PROCS.clear()
        except Exception:
            pass
        self._env_patch.stop()
        self._tmp.cleanup()
    def test_01_updater_version_comparison_and_security(self):
        self.assertEqual(updater.compare_versions("1.8.0", "1.8.1"), 1)
        self.assertEqual(updater.compare_versions("1.8.0", "1.8.0"), 0)
        self.assertEqual(updater.compare_versions("2.0.0", "1.9.9"), -1)
        self.assertTrue(updater.is_prerelease_version("1.9.0-rc1"))
        self.assertFalse(updater.is_prerelease_version("1.8.0"))
        self.assertEqual(updater.validate_remote_url("https://github.com/avelone123123/shard/releases"), "https://github.com/avelone123123/shard/releases")
        with self.assertRaises(ValueError):
            updater.validate_remote_url("http://github.com/avelone123123/shard")
        with self.assertRaises(ValueError):
            updater.validate_remote_url("https://evil.example.com/payload.zip")
        with self.assertRaises(ValueError):
            updater.validate_git_ref("v1.8.0; rm -rf /")
        self.assertEqual(updater.validate_git_ref("v1.8.0"), "v1.8.0")
    def test_02_updater_zip_slip_and_sha256_verification(self):
