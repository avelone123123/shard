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
