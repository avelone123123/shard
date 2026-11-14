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
            "ok": True,
            "latest_version": "9.9.9",
            "tag_name": "v9.9.9",
            "source": "github_release",
            "release_notes": "Bug fixes",
            "asset_url": "https://api.github.com/repos/avelone123123/shard/zipball/v9.9.9",
            "asset_name": "shard-9.9.9.zip",
            "asset_sha256": upd_sha,
            "error": None,
        }
        def _fake_http_get(url, timeout=3.0, headers=None, stream=False):
            class _FakeStream:
                status_code = 200
                def raise_for_status(self):
                    return None
                def iter_content(self, chunk_size=65536):
                    yield upd_zip.read_bytes()
            return _FakeStream()
        def failing_pip_runner(_ver):
            (fake_pkg / "marker.py").write_text("VALUE = 'corrupted'\n", encoding="utf-8")
            return 1, "simulated pip failure"
        with patch.object(updater, "detect_installation_method", return_value=inst_fake):
            with patch.object(updater, "fetch_latest_remote_info", return_value=remote_mock):
                with patch.object(updater, "_http_get", side_effect=_fake_http_get):
                    res = updater.apply_update(pip_runner=failing_pip_runner)
        self.assertEqual(res["status"], "failed")
        self.assertTrue(res["rolled_back"])
        self.assertEqual((fake_pkg / "marker.py").read_text(encoding="utf-8"), "VALUE = 'original'\n")
        self.assertEqual(json.loads(keys_file.read_text(encoding="utf-8")), ["sk-shard-keepme"])
    def test_04_updater_api_endpoints_and_cli_dash_update_normalization(self):
        with patch.object(updater, "fetch_latest_remote_info", return_value={"ok": True, "latest_version": __version__, "source": "github_release", "tag_name": f"v{__version__}", "release_notes": "", "error": None}):
            r = self.client.get("/api/system/update?force=1", headers=self.owner_headers)
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["check"]["state"], "UP_TO_DATE")
        r_remote = self.client.get("/api/system/update", headers={"Host": "10.0.0.5:8000"})
        self.assertEqual(r_remote.status_code, 403)
        with patch.object(cli, "cmd_update") as mocked_upd:
            cli.main(["-update", "--check", "--json"])
            self.assertTrue(mocked_upd.called)
            ns = mocked_upd.call_args[0][0]
            self.assertTrue(ns.check)
            self.assertTrue(ns.json)
    def test_05_soup_lifecycle_and_python_compat(self):
        self.assertIn("NOT_INSTALLED", soup_adapter.SOUP_LIFECYCLE_STATES)
        self.assertIn("UPDATE_AVAILABLE", soup_adapter.SOUP_LIFECYCLE_STATES)
        self.assertIn("UPDATING", soup_adapter.SOUP_LIFECYCLE_STATES)
        self.assertTrue(soup_adapter._py_compatible((3, 12)))
        self.assertFalse(soup_adapter._py_compatible((3, 14)))
        self.assertFalse(soup_adapter._py_compatible((3, 10)))
        with self.assertRaises((ValueError, PermissionError)):
            soup_adapter._validate_custom_python("C:\\Windows\\notepad.exe; calc")
        with self.assertRaises(ValueError):
            soup_adapter._validate_custom_python("python.exe; calc")
        fake_py = self._root / "python312" / "python.exe"
        fake_py.parent.mkdir(parents=True, exist_ok=True)
        fake_py.write_text("", encoding="utf-8")
        mock_probe = {"py": [3, 12, 4], "soup_version": "0.70.0", "present": {"soup_cli": True, "torch": True, "transformers": True, "peft": True, "trl": True, "datasets": True}, "cuda_available": False}
        mock_run = type("_R", (), {"returncode": 0, "stdout": "soup 0.70.0", "stderr": ""})()
        with patch.object(soup_adapter, "check_latest_soup_version", return_value={"latest_version": "0.77.2", "source": "pypi", "error": None}):
            with patch.object(soup_adapter, "_candidate_python_paths", return_value=[str(fake_py)]):
                with patch.object(soup_adapter, "_probe_python_env", return_value=mock_probe):
                    with patch("subprocess.run", return_value=mock_run):
                        det = soup_adapter.detect_soup(probe_deps=True, check_remote=True)
        self.assertEqual(det["lifecycle_state"], "UPDATE_AVAILABLE")
        self.assertTrue(det["update_available"])
        self.assertEqual(det["latest_version"], "0.77.2")
    def test_06_model_browser_formats_suggestions_and_security(self):
        models_root = self._root / "local_models"
        hf_dir = models_root / "Qwen2.5-Coder-1.5B-Instruct"
        hf_dir.mkdir(parents=True, exist_ok=True)
        (hf_dir / "config.json").write_text('{"model_type":"qwen2","architectures":["Qwen2ForCausalLM"]}', encoding="utf-8")
        (hf_dir / "model.safetensors").write_bytes(b"0" * 16)
        gguf_file = models_root / "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"
        gguf_file.write_bytes(b"GGUF" + b"\x00" * 32)
        lora_dir = models_root / "my-lora"
        lora_dir.mkdir(parents=True, exist_ok=True)
        (lora_dir / "adapter_config.json").write_text('{"base_model_name_or_path":"Qwen/Qwen2.5-1.5B-Instruct"}', encoding="utf-8")
        (lora_dir / "adapter_model.safetensors").write_bytes(b"0" * 16)
        roots_res = self.client.get("/api/models/browser/roots", headers=self.owner_headers)
