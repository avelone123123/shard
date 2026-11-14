import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from fastapi.testclient import TestClient
from shard_engine import engine, gguf_backend, hf_hub, paths, server, settings, soup_adapter, training
class TestModelLifecycleAndProgress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.hub_dir = self.tmp_path / "hub"
        self.hub_dir.mkdir(parents=True, exist_ok=True)
    def tearDown(self):
        self.tmp.cleanup()
    def test_find_local_gguf_exact_and_fuzzy(self):
        repo_dir = self.hub_dir / "models--Qwen--Qwen2.5-Coder-1.5B-Instruct-GGUF" / "snapshots" / "snap1"
        repo_dir.mkdir(parents=True, exist_ok=True)
        target_file = repo_dir / "qwen2.5-coder-1.5b-instruct-q8_0.gguf"
        target_file.write_bytes(b"GGUF_TEST_WEIGHTS_MOCK")
        with mock.patch.object(gguf_backend, "all_hub_dirs", return_value=[str(self.hub_dir)]):
            found1 = gguf_backend.find_local_gguf("Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF")
            self.assertIsNotNone(found1)
            self.assertEqual(found1["path"], str(target_file))
            self.assertEqual(found1["quant"], "Q8_0")
            found2 = gguf_backend.find_local_gguf("Qwen2.5-Coder-1.5B-Instruct-GGUF")
            self.assertIsNotNone(found2)
            self.assertEqual(found2["path"], str(target_file))
            found3 = gguf_backend.find_local_gguf("Qwen2.5-Coder-1.5B-Instruct")
            self.assertIsNotNone(found3)
            self.assertEqual(found3["path"], str(target_file))
    def test_find_gguf_repo_resolves_canonical_owner(self):
        repo_dir = self.hub_dir / "models--Qwen--Qwen2.5-Coder-1.5B-Instruct-GGUF" / "snapshots" / "snap1"
        repo_dir.mkdir(parents=True, exist_ok=True)
        (repo_dir / "model.gguf").write_bytes(b"GGUF")
        with mock.patch.object(gguf_backend, "all_hub_dirs", return_value=[str(self.hub_dir)]):
            resolved = gguf_backend.find_gguf_repo("Qwen2.5-Coder-1.5B-Instruct-GGUF")
            self.assertEqual(resolved, "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF")
    def test_is_model_cached_complete_with_incomplete_blobs(self):
        repo_dir = self.hub_dir / "models--Qwen--Qwen2.5-Coder-1.5B-Instruct-GGUF"
        snap_dir = repo_dir / "snapshots" / "snap1"
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "qwen2.5-coder-1.5b-instruct-q8_0.gguf").write_bytes(b"GGUF_FULL")
        blobs_dir = repo_dir / "blobs"
        blobs_dir.mkdir(parents=True, exist_ok=True)
        old_incomplete = blobs_dir / "old_download.1234.incomplete"
        old_incomplete.write_bytes(b"INCOMPLETE_PARTIAL")
        os.utime(str(old_incomplete), (time.time() - 500, time.time() - 500))
        with mock.patch.object(engine, "_all_hub_roots", return_value=[str(self.hub_dir)]):
            complete = engine._is_model_cached_complete("Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF")
            self.assertTrue(complete)
    def test_duplicate_load_prevention(self):
        eng = engine.ShardEngine()
        eng.status = "ready"
        eng.current_loaded_id = "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF"
        mock_backend = mock.MagicMock()
        mock_backend.is_running.return_value = True
        mock_backend.model_id = "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF"
        eng.gguf = mock_backend
        eng.loaded_info = {"status": "already_loaded", "model_id": "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF"}
        with mock.patch.object(eng, "_run_loader_gguf") as mock_loader:
            res1 = eng.load_model("Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF")
            self.assertEqual(res1.get("status"), "already_loaded")
            res2 = eng.load_model("Qwen2.5-Coder-1.5B-Instruct-GGUF")
            self.assertEqual(res2.get("status"), "already_loaded")
            mock_loader.assert_not_called()
    def test_tqdm_progress_callback_preserves_telemetry(self):
        eng = engine.ShardEngine()
        eng.status = "loading"
        eng.active_model_id = "test-model"
        eng.on_tqdm_update(500 * 1024 * 1024, 1000 * 1024 * 1024, 25 * 1024 * 1024, "Downloading")
        self.assertEqual(eng.load_progress["curr_mb"], 500.0)
        self.assertEqual(eng.load_progress["total_mb"], 1000.0)
        self.assertEqual(eng.load_progress["speed_mb"], 25.0)
        self.assertEqual(eng.load_progress["stage"], "Скачивание весов с Hugging Face")
        eng.on_tqdm_update(16, 32, 0, "Loading layers")
        self.assertEqual(eng.load_progress["curr_mb"], 500.0)
        self.assertEqual(eng.load_progress["total_mb"], 1000.0)
        self.assertEqual(eng.load_progress["speed_mb"], 25.0)
        self.assertEqual(eng.load_progress["stage"], "Размещение тензоров в памяти")
    def test_lifecycle_state_transitions(self):
        eng = engine.ShardEngine()
        eng.active_model_id = "non-existent-model"
        self.assertEqual(eng.lifecycle_state("non-existent-model"), "NOT_DOWNLOADED")
        eng.status = "loading"
        self.assertEqual(eng.lifecycle_state("non-existent-model"), "LOADING")
        eng.status = "idle"
        repo_dir = self.hub_dir / "models--TestOrg--TestModel-GGUF" / "snapshots" / "snap1"
        repo_dir.mkdir(parents=True, exist_ok=True)
        (repo_dir / "model.gguf").write_bytes(b"GGUF_WEIGHTS")
        with mock.patch.object(engine, "_all_hub_roots", return_value=[str(self.hub_dir)]):
            self.assertEqual(eng.lifecycle_state("TestOrg/TestModel-GGUF"), "DOWNLOADED")
            mock_backend = mock.MagicMock()
            mock_backend.is_running.return_value = True
            mock_backend.model_id = "TestOrg/TestModel-GGUF"
            eng.gguf = mock_backend
            eng.current_loaded_id = "TestOrg/TestModel-GGUF"
            self.assertEqual(eng.lifecycle_state("TestOrg/TestModel-GGUF"), "LOADED_IN_MEMORY")
            eng.status = "unloading"
            eng.gguf = None
            self.assertEqual(eng.lifecycle_state("TestOrg/TestModel-GGUF"), "UNLOADING")
            eng.status = "error"
            self.assertEqual(eng.lifecycle_state("TestOrg/TestModel-GGUF"), "ERROR")
    def test_settings_models_dir_invalid_path_fallback(self):
        fake_path = "Z:\\nonexistent_invalid_disk_path\\models"
        with mock.patch.object(settings, "_load", return_value={"models_dir": fake_path}):
            resolved = settings.models_dir()
            self.assertNotEqual(resolved, fake_path)
            self.assertEqual(resolved, paths.default_models_dir())
    def test_server_models_status_and_downloaded_endpoints(self):
        client = TestClient(server.app)
        owner_tok = training.get_owner_token()
        headers = {"X-Shard-Owner-Token": owner_tok}
        res_st = client.get("/api/models/status", headers=headers)
        self.assertEqual(res_st.status_code, 200)
        st_data = res_st.json()
        self.assertIn("lifecycle_state", st_data)
        self.assertIn("load_progress", st_data)
        self.assertIn("is_loaded", st_data)
        res_dl = client.get("/api/models/downloaded", headers=headers)
        self.assertEqual(res_dl.status_code, 200)
        dl_data = res_dl.json()
        self.assertIn("models", dl_data)
        self.assertIn("total_models", dl_data)
    def test_server_load_model_already_loaded_shortcircuit(self):
        client = TestClient(server.app)
        owner_tok = training.get_owner_token()
        headers = {"X-Shard-Owner-Token": owner_tok}
        with mock.patch.object(server.engine_instance, "is_model_active", return_value=True), mock.patch.object(server.engine_instance, "loaded_info", {"test": "ok"}):
            res = client.post("/api/models/load", json={"model_id": "test-model"}, headers=headers)
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data.get("already_loaded"))
    def test_hf_hub_is_id_downloaded(self):
        cached = {"qwen/qwen2.5-coder-1.5b-instruct-gguf", "qwen/qwen2.5-coder-1.5b-instruct", "qwen2.5-coder-1.5b-instruct"}
        self.assertTrue(hf_hub.is_id_downloaded("Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF", cached))
        self.assertTrue(hf_hub.is_id_downloaded("Qwen/Qwen2.5-Coder-1.5B-Instruct", cached))
        self.assertTrue(hf_hub.is_id_downloaded("Qwen2.5-Coder-1.5B-Instruct-GGUF", cached))
        self.assertFalse(hf_hub.is_id_downloaded("DeepSeek-R1-Distill-Llama-70B", cached))
    def test_find_gguf_repo_rejects_safetensors_and_prefers_gguf_variant(self):
        gguf_backend._repo_cache.clear()
        def fake_repo_has_gguf(repo_id, timeout=5.0):
            return repo_id == "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
        with mock.patch.object(gguf_backend, "all_hub_dirs", return_value=[str(self.hub_dir)]), mock.patch.object(gguf_backend, "_repo_has_gguf", side_effect=fake_repo_has_gguf), mock.patch.object(gguf_backend, "_search_gguf_repo", return_value=None):
            resolved = gguf_backend.find_gguf_repo("Qwen/Qwen2.5-Coder-7B-Instruct")
            self.assertEqual(resolved, "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF")
            none_resolved = gguf_backend.find_gguf_repo("SomeOrg/SafeTensorsOnlyModel")
            self.assertIsNone(none_resolved)
    def test_stream_sanitizer_strips_qwen_tokens_and_fixes_utf8_mojibake(self):
        san = engine._StreamSanitizer()
        out1 = san.feed("<|im_st")
        out2 = san.feed("art|>assistant\nПривет, мир!<|im_")
        out3 = san.feed("end|><|endoftext|>")
        out4 = san.flush()
        combined = "".join(t for _, t in (out1 + out2 + out3 + out4))
        self.assertEqual(combined, "Привет, мир!")
        mojibake = "Слушание".encode("utf-8").decode("cp1251")
        repaired = engine._fix_mojibake_utf8(mojibake)
        self.assertEqual(repaired, "Слушание")
        self.assertEqual(engine._fix_mojibake_utf8("Чистый русский текст"), "Чистый русский текст")
    def test_failed_validation_reverts_active_model_and_reports_validation_failed(self):
        eng = engine.ShardEngine()
        eng.active_model_id = "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF"
        eng.current_loaded_id = "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF"
        eng._load_seq = 1
        target_id = "Qwen/Qwen2.5-Coder-0.5B-Instruct"
        fit = {"verdict": "GPU", "recommended": "Q4_K_M"}
        with mock.patch.object(gguf_backend, "find_local_gguf", return_value=None), mock.patch.object(gguf_backend, "find_gguf_repo", return_value=None):
            eng._run_loader_gguf(target_id, 1, fit)
            self.assertEqual(eng.detailed_lifecycle_state(target_id), "VALIDATION_FAILED")
            self.assertFalse(eng.is_model_active(target_id))
            self.assertEqual(eng.active_model_id, "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF")
    def test_invite_uri_persisted_across_list_invites(self):
        client = TestClient(server.app)
        owner_tok = training.get_owner_token()
        headers = {"X-Shard-Owner-Token": owner_tok}
        create_res = client.post("/api/net/invites", json={"ttl_s": 3600, "max_uses": 2, "label": "test-uri"}, headers=headers)
        self.assertEqual(create_res.status_code, 200)
        created = create_res.json()
        self.assertTrue(str(created.get("uri") or "").startswith("shard://"))
        created_id = (created.get("invite") or {}).get("invite_id") or created.get("invite_id")
        list_res = client.get("/api/net/invites", headers=headers)
        self.assertEqual(list_res.status_code, 200)
        invites = list_res.json().get("invites") or []
        matching = [i for i in invites if (i.get("id") or i.get("invite_id")) == created_id]
        self.assertTrue(len(matching) >= 1)
        self.assertEqual(matching[0].get("uri"), created.get("uri"))
    def test_hosted_server_workspace_and_prejoin_preview_endpoints(self):
        client = TestClient(server.app)
        owner_tok = training.get_owner_token()
        headers = {"X-Shard-Owner-Token": owner_tok}
        prev = client.get("/api/lan/preview")
        self.assertEqual(prev.status_code, 200)
        prev_data = prev.json()
        self.assertIn("access_mode", prev_data)
        self.assertIn("roles", prev_data)
        ws = client.get("/api/server/workspace", headers=headers)
        self.assertEqual(ws.status_code, 200)
        ws_data = ws.json()
        self.assertIn("lan", ws_data)
        self.assertIn("network_host", ws_data)
        comm_post = client.post("/api/server/community", json={"author": "Alice", "text": "Hello SHARD cluster!"}, headers=headers)
        self.assertEqual(comm_post.status_code, 200)
        iss_post = client.post("/api/server/issues", json={"title": "Test hallucination", "prompt": "2+2", "expected_output": "4"}, headers=headers)
        self.assertEqual(iss_post.status_code, 200)
        issue_id = iss_post.json()["issue"]["id"]
        res_post = client.post(f"/api/server/issues/{issue_id}/resolve", json={"status": "resolved"}, headers=headers)
        self.assertEqual(res_post.status_code, 200)
        self.assertEqual(res_post.json()["issue"]["status"], "resolved")
    def test_soup_compatible_python_discovery_and_status(self):
        client = TestClient(server.app)
        owner_tok = training.get_owner_token()
        headers = {"X-Shard-Owner-Token": owner_tok}
        py_info = soup_adapter.find_compatible_python()
        self.assertIn("found", py_info)
        st_res = client.get("/api/training/soup/install/status", headers=headers)
        self.assertEqual(st_res.status_code, 200)
        self.assertIn("status", st_res.json())
if __name__ == "__main__":
