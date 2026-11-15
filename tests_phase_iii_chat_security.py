import concurrent.futures
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock
from fastapi.testclient import TestClient
from shard_engine import lan, net_tunnel, paths, server, session_registry, storage
class TestPhaseIIIChatSecurity(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="shard_chat_sec_test_")
        self.orig_env = os.environ.get("SHARD_DATA_DIR")
        os.environ["SHARD_DATA_DIR"] = self.tmp_dir
        self.client_owner = TestClient(server.app, client=("127.0.0.1", 50000))
        self.client_remote = TestClient(server.app, client=("192.168.1.100", 50000))
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
    def test_unknown_and_forged_tokens_rejected_401(self):
        r1 = self.client_remote.get("/api/server/chats", headers={"X-Session-Token": "forged_session_xyz"})
        self.assertEqual(r1.status_code, 401)
        r2 = self.client_remote.get("/api/server/chats", headers={"Authorization": "Bearer forged_bearer_token"})
        self.assertEqual(r2.status_code, 401)
        r3 = self.client_remote.get("/api/server/chats", headers={"X-Participant-Id": "fake_owner_pid"})
        self.assertEqual(r3.status_code, 401)
        r4 = self.client_remote.get("/api/server/chats")
        self.assertEqual(r4.status_code, 401)
    def test_session_issuance_unauthorized_when_closed(self):
        with mock.patch.object(lan.LAN, "is_open", return_value=False):
            r = self.client_remote.post("/api/server/session", json={"name": "Attacker"})
            self.assertEqual(r.status_code, 401)
            self.assertEqual(r.json().get("code"), "PAIRING_REQUIRED")
    def test_session_issuance_and_complete_isolation(self):
        r_a = self.client_owner.post("/api/server/session", json={"name": "Alice"})
        self.assertEqual(r_a.status_code, 200)
        tok_a = r_a.json()["session_token"]
        pid_a = r_a.json()["participant_id"]
        r_b = self.client_owner.post("/api/server/session", json={"name": "Bob"})
        self.assertEqual(r_b.status_code, 200)
        tok_b = r_b.json()["session_token"]
        pid_b = r_b.json()["participant_id"]
        self.assertNotEqual(tok_a, tok_b)
        self.assertNotEqual(pid_a, pid_b)
        r_create = self.client_remote.post("/api/server/chats", headers={"X-Session-Token": tok_a}, json={"title": "Alice Secret Chat", "create_only": True})
        self.assertEqual(r_create.status_code, 200)
        chat_id_a = r_create.json()["chat_id"]
        r_b_list = self.client_remote.get("/api/server/chats", headers={"X-Session-Token": tok_b})
        self.assertEqual(r_b_list.status_code, 200)
        self.assertEqual(len(r_b_list.json()["chats"]), 0)
        r_b_get = self.client_remote.get(f"/api/server/chats/{chat_id_a}", headers={"X-Session-Token": tok_b})
        self.assertEqual(r_b_get.status_code, 404)
        r_b_del = self.client_remote.delete(f"/api/server/chats/{chat_id_a}", headers={"X-Session-Token": tok_b})
        self.assertEqual(r_b_del.status_code, 404)
        r_a_get = self.client_remote.get(f"/api/server/chats/{chat_id_a}", headers={"X-Session-Token": tok_a})
        self.assertEqual(r_a_get.status_code, 200)
        self.assertEqual(r_a_get.json()["chat"]["title"], "Alice Secret Chat")
    def test_participant_id_spoofing_prevented(self):
        r_a = self.client_owner.post("/api/server/session", json={"name": "Alice"})
        tok_a = r_a.json()["session_token"]
        pid_a = r_a.json()["participant_id"]
        r_b = self.client_owner.post("/api/server/session", json={"name": "Bob"})
        tok_b = r_b.json()["session_token"]
        pid_b = r_b.json()["participant_id"]
        r_a_create = self.client_remote.post("/api/server/chats", headers={"X-Session-Token": tok_a}, json={"title": "Alice Original", "create_only": True})
        chat_id_a = r_a_create.json()["chat_id"]
        r_spoof = self.client_remote.post("/api/server/chats", headers={"X-Session-Token": tok_b, "X-Participant-Id": pid_a}, json={"title": "Bob Spoof", "create_only": True})
        self.assertEqual(r_spoof.status_code, 200)
        chat_id_b = r_spoof.json()["chat_id"]
        r_a_chats = self.client_remote.get("/api/server/chats", headers={"X-Session-Token": tok_a}).json()["chats"]
        self.assertEqual(len(r_a_chats), 1)
        self.assertEqual(r_a_chats[0]["id"], chat_id_a)
        r_b_chats = self.client_remote.get("/api/server/chats", headers={"X-Session-Token": tok_b}).json()["chats"]
        self.assertEqual(len(r_b_chats), 1)
        self.assertEqual(r_b_chats[0]["id"], chat_id_b)
    def test_session_revocation(self):
        r_s = self.client_owner.post("/api/server/session", json={"name": "Carol"})
        tok = r_s.json()["session_token"]
        r_valid = self.client_remote.get("/api/server/chats", headers={"X-Session-Token": tok})
        self.assertEqual(r_valid.status_code, 200)
        r_rev = self.client_remote.post("/api/server/session/revoke", json={"session_token": tok})
        self.assertEqual(r_rev.status_code, 200)
        self.assertTrue(r_rev.json()["revoked"])
        r_after = self.client_remote.get("/api/server/chats", headers={"X-Session-Token": tok})
        self.assertEqual(r_after.status_code, 401)
    def test_session_expiration(self):
        tok, rec = session_registry.issue_session(role="member", name="TempUser", ttl_seconds=300.0)
        with mock.patch("time.time", return_value=rec["expires_at"] + 10.0):
            r = self.client_remote.get("/api/server/chats", headers={"X-Session-Token": tok})
            self.assertEqual(r.status_code, 401)
    def test_nat_guest_isolation_behind_same_ip(self):
        with mock.patch.object(lan.LAN, "is_open", return_value=True):
            r1 = self.client_remote.post("/api/server/session", json={"name": "Guest1"})
            r2 = self.client_remote.post("/api/server/session", json={"name": "Guest2"})
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(r2.status_code, 200)
            tok1 = r1.json()["session_token"]
            pid1 = r1.json()["participant_id"]
            tok2 = r2.json()["session_token"]
            pid2 = r2.json()["participant_id"]
            self.assertNotEqual(tok1, tok2)
            self.assertNotEqual(pid1, pid2)
            r_create = self.client_remote.post("/api/server/chats", headers={"X-Session-Token": tok1}, json={"title": "Guest1 Only", "create_only": True})
            chat1_id = r_create.json()["chat_id"]
            r_list2 = self.client_remote.get("/api/server/chats", headers={"X-Session-Token": tok2})
            self.assertEqual(len(r_list2.json()["chats"]), 0)
            r_get2 = self.client_remote.get(f"/api/server/chats/{chat1_id}", headers={"X-Session-Token": tok2})
            self.assertEqual(r_get2.status_code, 404)
    def test_workspace_isolation_owner_cannot_leak_participant_chats(self):
        r_s = self.client_owner.post("/api/server/session", json={"name": "PrivateUser"})
        tok = r_s.json()["session_token"]
        self.client_remote.post("/api/server/chats", headers={"X-Session-Token": tok}, json={"title": "TopSecretConversation", "create_only": True})
        r_ws = self.client_owner.get("/api/server/workspace")
        self.assertEqual(r_ws.status_code, 200)
        data = r_ws.json()
        self.assertGreaterEqual(data.get("private_session_count", 0), 1)
        dumped = json.dumps(data)
        self.assertNotIn("TopSecretConversation", dumped)
    def test_lan_pairing_token_and_tunnel_authentication(self):
        with mock.patch.object(lan.LAN, "tokens", return_value={"valid-lan-token"}):
            r_sess = self.client_remote.post("/api/server/session", json={"pairing_token": "valid-lan-token"})
            self.assertEqual(r_sess.status_code, 200)
            tok = r_sess.json()["session_token"]
            r_lan = self.client_remote.get("/api/server/chats", headers={"X-Session-Token": tok})
            self.assertEqual(r_lan.status_code, 200)
        with mock.patch.object(net_tunnel, "is_tunnel_request", return_value=True), \
             mock.patch.object(net_tunnel, "tunnel_peer_id", return_value="node_xyz123"):
            r_tun = self.client_remote.get("/api/server/chats")
            self.assertEqual(r_tun.status_code, 200)
            self.assertTrue(r_tun.json()["participant_id"].startswith("peer_"))
    def test_concurrent_chat_file_locking_preserves_messages(self):
        r_s = self.client_owner.post("/api/server/session", json={"name": "ConcurUser"})
        tok = r_s.json()["session_token"]
        pid = r_s.json()["participant_id"]
        chat_file = server._participant_chats_file(pid)
        with storage.file_lock(chat_file):
            server._save_participant_chats(pid, [{"id": "pchat-concurrent", "title": "Concurrent Chat", "created_at": time.time(), "updated_at": time.time(), "messages": []}])
        def append_msg(idx):
            with storage.file_lock(chat_file):
                chats = server._load_participant_chats(pid)
                chats[0]["messages"].append({"id": f"m-{idx}", "role": "user", "content": f"msg {idx}"})
                chats[0]["updated_at"] = time.time()
                server._save_participant_chats(pid, chats)
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(append_msg, i) for i in range(25)]
            for f in concurrent.futures.as_completed(futures):
                f.result()
        with storage.file_lock(chat_file):
            final_chats = server._load_participant_chats(pid)
        self.assertEqual(len(final_chats[0]["messages"]), 25)
if __name__ == "__main__":
    unittest.main()