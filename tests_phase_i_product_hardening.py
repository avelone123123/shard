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
        self.assertEqual(roots_res.status_code, 200)
        self.assertTrue(len(roots_res.json().get("drives", [])) >= 1 or len(roots_res.json().get("shortcuts", [])) >= 1)
        list_res = self.client.get(f"/api/models/browser/list?path={models_root}", headers=self.owner_headers)
        self.assertEqual(list_res.status_code, 200)
        entries = {e["name"]: e for e in list_res.json()["entries"]}
        self.assertEqual(entries["Qwen2.5-Coder-1.5B-Instruct"]["format"], "hf_model_dir")
        self.assertTrue(entries["Qwen2.5-Coder-1.5B-Instruct"]["compatibility"]["soup_training"]["supported"])
        self.assertEqual(entries["qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"]["format"], "gguf_inference")
        self.assertTrue(entries["qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"]["compatibility"]["gguf_inference"]["supported"])
        self.assertFalse(entries["qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"]["compatibility"]["soup_training"]["supported"])
        self.assertEqual(entries["qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"]["compatibility"]["soup_training"]["suggested_base_model"], "Qwen/Qwen2.5-Coder-1.5B-Instruct")
        self.assertEqual(entries["my-lora"]["format"], "lora_adapter")
        ssh_dir = Path.home() / ".ssh"
        r_ssh = self.client.get(f"/api/models/browser/list?path={ssh_dir}", headers=self.owner_headers)
        self.assertEqual(r_ssh.status_code, 403)
        r_unc = self.client.post("/api/models/browser/inspect", json={"path": "\\\\127.0.0.1\\c$\\Windows\\System32"}, headers=self.owner_headers)
        self.assertEqual(r_unc.status_code, 403)
    def test_07_dataset_creation_metrics_pagination_and_validation(self):
        feedback.update_privacy_config({"require_manual_review": False, "redact_secrets": False, "redact_pii": False})
        for i in range(25):
            r = feedback.submit_feedback({
                "prompt": f"Explain Rust ownership #{i}",
                "response": f"Ownership ensures memory safety example #{i}.",
                "rating": "up",
                "consent_given": True,
            }, source="local")
            feedback.review_feedback(r["id"], "approve")
        dup = feedback.submit_feedback({
            "prompt": "Explain Rust ownership #0",
            "response": "Ownership ensures memory safety example #0.",
            "rating": "up",
            "consent_given": True,
        }, source="local")
        feedback.review_feedback(dup["id"], "approve")
        snap = feedback.build_dataset_snapshot(task="sft", name="rust-help", validate_with_soup=False)
        self.assertEqual(snap["valid_records"], 25)
        self.assertEqual(snap["source_feedback_count"], 26)
        self.assertGreaterEqual(snap["dedup_removed"], 1)
        self.assertEqual(snap["validation_status"], "schema_valid")
        dpo_rej = feedback.submit_feedback({"prompt": "Only up", "response": "Not a pair", "rating": "up", "consent_given": True}, source="local")
        feedback.review_feedback(dpo_rej["id"], "approve")
        fb_ok = feedback.submit_feedback({"prompt": "Good q", "response": "Bad a", "better_response": "Good a", "rating": "correction", "consent_given": True}, source="local")
        feedback.review_feedback(fb_ok["id"], "approve")
        dpo_snap = feedback.build_dataset_snapshot(task="dpo", name="dpo-check", validate_with_soup=False)
        self.assertGreaterEqual(dpo_snap["rejected_records"], 1)
        p1 = self.client.get(f"/api/training/datasets/{snap['snapshot_id']}/preview?page=1&page_size=20", headers=self.owner_headers)
        self.assertEqual(p1.status_code, 200)
        d1 = p1.json()
        self.assertEqual(d1["total_records"], 25)
        self.assertEqual(d1["total_pages"], 2)
        self.assertEqual(len(d1["records"]), 20)
        self.assertEqual(d1["records"][0]["record_type"], "sft_chatml")
        self.assertEqual(d1["records"][0]["validation_status"], "valid")
        p2 = self.client.get(f"/api/training/datasets/{snap['snapshot_id']}/preview?page=2&page_size=20", headers=self.owner_headers)
        self.assertEqual(len(p2.json()["records"]), 5)
        with open(snap["path"], "a", encoding="utf-8") as f:
            f.write("{broken json line\n")
        p_corr = feedback.preview_dataset_paginated(snap["snapshot_id"], page=2, page_size=20)
        self.assertEqual(p_corr["invalid_count"], 1)
        self.assertEqual(p_corr["records"][-1]["record_type"], "corrupted")
        v_res = self.client.post(f"/api/training/datasets/{snap['snapshot_id']}/validate", json={"task": "sft"}, headers=self.owner_headers)
        self.assertEqual(v_res.status_code, 200)
        self.assertFalse(v_res.json()["ok"])
    def test_08_training_preflight_blocks_gguf_and_runs_lifecycle(self):
        feedback.update_privacy_config({"require_manual_review": False, "redact_secrets": False, "redact_pii": False})
        fb = feedback.submit_feedback({"prompt": "Hello", "response": "Hi there!", "rating": "up", "consent_given": True}, source="local")
        feedback.review_feedback(fb["id"], "approve")
        snap = feedback.build_dataset_snapshot(task="sft", name="preflight-ds", validate_with_soup=False)
        gguf_path = self._root / "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"
        gguf_path.write_bytes(b"GGUF" + b"\x00" * 16)
        fake_soup = {
            "available": True,
            "command": ["fake-soup"],
            "version": "0.77.2",
            "soup_version": "0.77.2",
            "python_version": "3.12.4",
            "cuda_available": False,
            "capabilities": {"cli": True, "train": True, "eval": True, "merge": True, "export_gguf": True},
            "missing_train_deps": [],
            "warnings": [],
        }
        with patch.object(soup_adapter, "detect_soup", return_value=fake_soup):
            pf_bad = self.client.post("/api/training/preflight", json={
                "base_model": str(gguf_path),
                "dataset_snapshot_id": snap["snapshot_id"],
                "task": "sft",
            }, headers=self.owner_headers)
            self.assertEqual(pf_bad.status_code, 200)
            self.assertFalse(pf_bad.json()["ready"])
            self.assertTrue(len(pf_bad.json()["suggested_hf_models"]) > 0)
            pf_good = self.client.post("/api/training/preflight", json={
                "base_model": "Qwen/Qwen2.5-Coder-1.5B-Instruct",
                "dataset_snapshot_id": snap["snapshot_id"],
                "task": "sft",
                "params": {"batch_size": 2, "gradient_accumulation_steps": 4, "max_length": 1024},
            }, headers=self.owner_headers)
            self.assertEqual(pf_good.status_code, 200)
            self.assertTrue(pf_good.json()["ready"])
            with self.assertRaises(ValueError):
                training.start_training_job(base_model=str(gguf_path), dataset_snapshot_id=snap["snapshot_id"])
            with patch.object(soup_adapter, "dry_run_train", return_value={"ok": True, "returncode": 0, "stdout": "Dry run ok", "stderr": ""}):
                job = training.start_training_job(
                    base_model="Qwen/Qwen2.5-Coder-1.5B-Instruct",
                    dataset_snapshot_id=snap["snapshot_id"],
                    dry_run=True,
                )
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["lifecycle_state"], "COMPLETED")
        self.assertTrue(isinstance(job["command"], list) and len(job["command"]) > 0)
    def test_09_dashboard_regression_bugs_fixed(self):
        root_html = Path("dashboard.html").read_bytes()
        pkg_html = Path("shard_engine/dashboard.html").read_bytes()
        self.assertEqual(root_html, pkg_html)
        txt = pkg_html.decode("utf-8")
        self.assertNotIn("modalModelBody", txt)
        self.assertNotIn("d.sample_records", txt)
        self.assertIn("id=\"modelModalBody\"", txt)
        self.assertIn("/api/training/datasets/' + encodeURIComponent(snapshotId) + '/preview", txt)
        self.assertIn("const inst = data.installer || {}", txt)
        self.assertIn("/api/system/update", txt)
        self.assertIn("/api/models/browser/roots", txt)
if __name__ == "__main__":
    unittest.main()
