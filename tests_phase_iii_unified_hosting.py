import os
import shutil
import tempfile
import unittest
from unittest import mock
from fastapi.testclient import TestClient
from shard_engine import engine, lan, paths, server, soup_adapter
class TestPhaseIIIUnifiedHosting(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="shard_phase3_test_")
        self.orig_env = os.environ.get("SHARD_DATA_DIR")
        os.environ["SHARD_DATA_DIR"] = self.tmp_dir
        self.client = TestClient(server.app, client=("127.0.0.1", 50000))
        server._PAIR_FAIL_LIMITER._hits.clear()
        server._PAIR_TOTAL_LIMITER._hits.clear()
        server._OWNER_FAIL_LIMITER._hits.clear()
    def tearDown(self):
        if self.orig_env is not None:
            os.environ["SHARD_DATA_DIR"] = self.orig_env
        else:
            os.environ.pop("SHARD_DATA_DIR", None)
        if os.path.isdir(self.tmp_dir):
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
    def test_model_first_server_creation_guards(self):
        with mock.patch.object(server.engine_instance, "is_loaded", return_value=False):
            r = self.client.post("/api/server/create", json={"name": "test-srv", "enable_lan": True})
            self.assertEqual(r.status_code, 409)
            data = r.json()
            self.assertEqual(data.get("code"), "MODEL_NOT_READY")
            self.assertIn("Model must be loaded", data.get("message", ""))
    def test_model_first_net_publish_guard(self):
        with mock.patch.object(server.engine_instance, "is_loaded", return_value=False):
            r = self.client.post("/api/net/publish", json={"name": "pub-srv", "models": ["qwen"]})
            self.assertEqual(r.status_code, 409)
            self.assertEqual(r.json().get("code"), "MODEL_NOT_READY")
    def test_model_first_lan_create_guard(self):
        with mock.patch.object(server.engine_instance, "is_loaded", return_value=False):
            r = self.client.post("/api/lan/create", json={"name": "lan-srv", "model": "test-model"})
            self.assertEqual(r.status_code, 409)
            self.assertEqual(r.json().get("code"), "MODEL_NOT_READY")
    def test_model_first_lan_model_guard(self):
        with mock.patch.object(server.engine_instance, "is_loaded", return_value=False):
            r = self.client.post("/api/lan/model", json={"model": "test-model"})
            self.assertEqual(r.status_code, 409)
            self.assertEqual(r.json().get("code"), "MODEL_NOT_READY")
    def test_server_creation_succeeds_when_model_ready(self):
        with mock.patch.object(server.engine_instance, "is_loaded", return_value=True), \
             mock.patch.object(server.engine_instance, "is_model_active", return_value=True), \
             mock.patch.object(server.engine_instance, "detailed_lifecycle_state", return_value="READY"), \
             mock.patch.object(server.engine_instance, "generate_sync", return_value={"content": "pong"}):
            r = self.client.post("/api/server/create", json={"name": "ready-srv", "enable_lan": True, "model": "ready-model"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json().get("status"), "ok")
    def test_private_chat_isolation_across_participants(self):
        tok_a = "shard_lan_tok_alice_12345678"
        tok_b = "shard_lan_tok_bob_87654321"
        client_a = TestClient(server.app, client=("192.168.1.10", 51000), headers={"Authorization": f"Bearer {tok_a}"})
        client_b = TestClient(server.app, client=("192.168.1.20", 52000), headers={"Authorization": f"Bearer {tok_b}"})
        with mock.patch.object(server.LAN, "tokens", return_value={tok_a, tok_b}):
            with mock.patch.object(server.engine_instance, "is_loaded", return_value=True), \
                 mock.patch.object(server.engine_instance, "generate_sync", return_value={"content": "Private response for Alice"}):
                r_a = client_a.post("/api/server/chats", json={"prompt": "Confidential prompt from Alice"})
                self.assertEqual(r_a.status_code, 200)
                chat_id_a = r_a.json()["chat_id"]
            list_b = client_b.get("/api/server/chats")
            self.assertEqual(list_b.status_code, 200)
            self.assertEqual(len(list_b.json().get("chats", [])), 0)
            get_b = client_b.get(f"/api/server/chats/{chat_id_a}")
            self.assertEqual(get_b.status_code, 404)
            del_b = client_b.delete(f"/api/server/chats/{chat_id_a}")
            self.assertEqual(del_b.status_code, 404)
            ws_r = client_b.get("/api/server/workspace")
            self.assertEqual(ws_r.status_code, 200)
            self.assertNotIn("shared_chats", ws_r.json())
            self.assertIn("private_session_count", ws_r.json())
            self.assertEqual(ws_r.json()["private_session_count"], 1)
            del_a = client_a.delete(f"/api/server/chats/{chat_id_a}")
            self.assertEqual(del_a.status_code, 200)
    def test_compute_contribution_roles_and_quotas(self):
        st = self.client.get("/api/lan/compute/status")
        self.assertEqual(st.status_code, 200)
        data = st.json()
        self.assertIn("wan_experimental_warning", data)
        with mock.patch("shard_engine.lan.find_rpc_server", return_value="/bin/ggml-rpc-server"):
            with mock.patch.object(server.LAN, "_start_worker", return_value=None):
                c_res = self.client.post("/api/lan/compute/consent", json={"opt_in": True, "max_vram_gb": 6.0, "max_threads": 8})
                self.assertEqual(c_res.status_code, 200)
                quota = c_res.json().get("compute_quota", {})
                self.assertEqual(quota.get("max_vram_gb"), 6.0)
                self.assertEqual(quota.get("max_threads"), 8)
                opt_out = self.client.post("/api/lan/compute/opt_out")
                self.assertEqual(opt_out.status_code, 200)
    def test_soup_install_cancellation_and_progress_parsing(self):
        soup_adapter._parse_and_update_pip_progress("Collecting torch>=2.1.0")
        st = soup_adapter.get_soup_install_status()
        self.assertEqual(st.get("pkg_current"), "torch")
        soup_adapter._parse_and_update_pip_progress("Downloading torch-2.2.0-cp312.whl (755.5 MB)")
        st = soup_adapter.get_soup_install_status()
        self.assertIn("755.5 MB", st.get("stage", ""))
        mock_proc = mock.MagicMock()
        mock_proc.poll.return_value = None
        soup_adapter._CURRENT_SOUP_PROC = mock_proc
        cancel_res = self.client.post("/api/training/soup/install/cancel")
        self.assertEqual(cancel_res.status_code, 200)
        c_st = cancel_res.json().get("installer", {})
        self.assertEqual(c_st.get("lifecycle_state"), "CANCELLED")
        self.assertEqual(c_st.get("status"), "cancelled")
    def test_training_servers_endpoint(self):
        with mock.patch.object(server.engine_instance, "is_loaded", return_value=True), \
             mock.patch.object(server.engine_instance, "active_model_id", "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF"):
            r = self.client.get("/api/training/servers")
            self.assertEqual(r.status_code, 200)
            srvs = r.json().get("servers", [])
            self.assertGreaterEqual(len(srvs), 1)
            primary = srvs[0]
            self.assertTrue(primary.get("is_loaded"))
            self.assertEqual(primary.get("training_base_model"), "Qwen/Qwen2.5-Coder-1.5B-Instruct")
            self.assertTrue(primary.get("trainable"))
if __name__ == "__main__":
    unittest.main()