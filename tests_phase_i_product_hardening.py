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
        good_zip = self._root / "good.zip"
        with zipfile.ZipFile(good_zip, "w") as zf:
            zf.writestr("shard-v1.8.1/shard_engine/__init__.py", '__version__ = "1.8.1"\n')
        good_sha = storage.sha256_file(good_zip)
        v_good = updater.verify_archive_artifact(good_zip, expected_sha256=good_sha)
        self.assertTrue(v_good["valid"])
        self.assertEqual(v_good["sha256"], good_sha.lower())
        with self.assertRaises(ValueError):
            updater.verify_archive_artifact(good_zip, expected_sha256="0" * 64)
        bad_zip = self._root / "zipslip.zip"
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr("../../evil.txt", "pwn")
        with self.assertRaises(ValueError):
            updater.verify_archive_artifact(bad_zip)
    def test_03_updater_check_states_and_rollback_preserves_user_data(self):
        cfg_file = Path(paths.config_dir()) / "settings.json"
        keys_file = Path(paths.keys_file())
        storage.atomic_write_json(cfg_file, {"models_dir": str(self._root / "models")})
        storage.atomic_write_json(keys_file, ["sk-shard-keepme"])
        fake_pkg = self._root / "fake_site" / "shard_engine"
        fake_pkg.mkdir(parents=True, exist_ok=True)
        (fake_pkg / "__init__.py").write_text('__app_name__ = "shard-engine"\n__version__ = "1.8.0"\n', encoding="utf-8")
        (fake_pkg / "marker.py").write_text("VALUE = 'original'\n", encoding="utf-8")
        inst_fake = {"method": "pip", "package_dir": str(fake_pkg), "writable": True, "git_root": None, "git_dirty": False}
        with patch.object(updater, "fetch_latest_remote_info", side_effect=ConnectionError("offline")):
            offline_chk = updater.check_for_updates(force=True, allow_network=True)
        self.assertEqual(offline_chk["state"], "GITHUB_UNAVAILABLE")
        upd_zip = self._root / "upd.zip"
        with zipfile.ZipFile(upd_zip, "w") as zf:
            zf.writestr("shard-main/shard_engine/marker.py", "VALUE = 'updated'\n")
        upd_sha = storage.sha256_file(upd_zip)
        remote_mock = {
