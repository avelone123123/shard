import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from fastapi.testclient import TestClient
from shard_engine import engine, feedback, gguf_backend, lan, paths, server, settings, storage, training
class TestNetworkingAndDiscovery(unittest.TestCase):
    def test_interface_classification_lan_radmin_tailscale_virtual(self):
        lan_if = lan._classify_iface("Wi-Fi", "192.168.100.6", "255.255.255.0", is_default=True)
        self.assertEqual(lan_if["kind"], "lan")
        self.assertEqual(lan_if["broadcast"], "192.168.100.255")
        radmin_if = lan._classify_iface("Radmin VPN", "26.84.23.251", "255.0.0.0")
        self.assertEqual(radmin_if["kind"], "vpn")
        self.assertEqual(radmin_if["provider"], "radmin")
        ts_if = lan._classify_iface("Tailscale", "100.64.5.9", "255.192.0.0")
        self.assertEqual(ts_if["kind"], "vpn")
        self.assertEqual(ts_if["provider"], "tailscale")
        virt_if = lan._classify_iface("vEthernet (Default Switch)", "192.168.0.1", "255.255.240.0")
        self.assertEqual(virt_if["kind"], "virtual")
    def test_client_id_nat_isolation_and_strict_leave(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = lan.LanManager(state_path=str(Path(tmp) / "lan.json"))
            self.addCleanup(mgr.off, True)
            with mock.patch.object(mgr, "_start_threads", return_value=None):
                st = mgr.create("nat-net", open_net=False)
                p1, e1 = mgr.handle_pair(st["pairing_code"], "203.0.113.5", name="pc-a", client_id="cid-a")
                p2, e2 = mgr.handle_pair(st["pairing_code"], "203.0.113.5", name="pc-b", client_id="cid-b")
                self.assertIsNone(e1)
                self.assertIsNone(e2)
                self.assertNotEqual(p1["token"], p2["token"])
                self.assertTrue(mgr.handle_heartbeat("203.0.113.5", p1["token"], client_id="cid-a"))
                self.assertFalse(mgr.handle_heartbeat("203.0.113.5", p1["token"], client_id="cid-b"))
                self.assertFalse(mgr.handle_leave("203.0.113.5", "", client_id="cid-a"))
                self.assertFalse(mgr.handle_leave("203.0.113.5", "sk-shard-lan-0000000000000000", client_id="cid-a"))
                self.assertTrue(mgr.handle_leave("203.0.113.5", p1["token"], client_id="cid-a"))
                self.assertEqual(mgr.tokens(), {p2["token"]})
    def test_reconnect_backoff_vs_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = lan.LanManager(state_path=str(Path(tmp) / "lan.json"))
            self.addCleanup(mgr.off, True)
            mgr._state["mode"] = "member"
            mgr._state["master"] = {"ip": "26.1.2.3", "port": 8000, "token": "sk-shard-lan-1111222233334444"}
            seq = iter(["unreachable", "unreachable", "ok"])
            def _step():
                val = next(seq, "rejected")
                if val == "rejected":
                    mgr._stop.set()
                return val
            with mock.patch.object(mgr, "_send_heartbeat", side_effect=_step), mock.patch.object(mgr._stop, "wait", return_value=False):
                mgr._hb_send_loop()
            self.assertEqual(mgr.mode(), "off")
    def test_connectivity_report_and_invite(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = lan.LanManager(state_path=str(Path(tmp) / "lan.json"))
            self.addCleanup(mgr.off, True)
            fake_ifaces = [
                {"ip": "192.168.100.6", "interface": "Wi-Fi", "kind": "lan", "provider": "lan", "cidr": "192.168.100.0/24", "broadcast": "192.168.100.255", "is_default": True},
                {"ip": "26.84.23.251", "interface": "Radmin VPN", "kind": "vpn", "provider": "radmin", "cidr": "26.0.0.0/8", "broadcast": "26.255.255.255", "is_default": False},
                {"ip": "192.168.0.1", "interface": "vEthernet", "kind": "virtual", "provider": "virtual", "cidr": "192.168.0.0/20", "broadcast": "192.168.15.255", "is_default": False},
            ]
            with mock.patch("shard_engine.lan.local_interfaces", return_value=fake_ifaces), mock.patch("shard_engine.lan.detect_tailscale", return_value={"installed": False, "running": False, "online": False, "ips": []}):
                rep = lan.connectivity_report(probe_public=False)
                self.assertEqual(rep["recommended_join_ip"], "26.84.23.251")
                self.assertEqual(len(rep["lan_interfaces"]), 1)
                self.assertEqual(len(rep["vpn_interfaces"]), 1)
                self.assertEqual(len(rep["virtual_excluded"]), 1)
                with mock.patch.object(mgr, "_start_threads", return_value=None):
                    mgr.create("inv-net", open_net=False)
                    inv = mgr.build_invite()
                    self.assertEqual(inv["status"], "ok")
                    self.assertEqual(len(inv["targets"]), 2)
    def test_route_local_ip_and_worker_port(self):
        self.assertEqual(lan._route_local_ip_for("127.0.0.1"), "127.0.0.1")
        with tempfile.TemporaryDirectory() as tmp:
            mgr = lan.LanManager(state_path=str(Path(tmp) / "lan.json"))
            p = mgr._choose_worker_port()
            self.assertTrue(1 <= p <= 65535)
class TestPersistenceCrashRecoveryAndBackup(unittest.TestCase):
    def test_json_bak_recovery_vs_intentional_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "state.json"
            storage.atomic_write_json(p, {"v": 1}, backup=True)
            storage.atomic_write_json(p, {"v": 2}, backup=True)
            p.write_text("{corrupt", encoding="utf-8")
            recovered = storage.read_json_safe(p, default={"v": 0}, expected_type=dict, quarantine=True, use_backup=True)
            self.assertEqual(recovered, {"v": 1})
            corrupt_files = list(Path(tmp).glob("state.json.corrupt.*"))
            self.assertTrue(corrupt_files)
            os.remove(p)
            missing = storage.read_json_safe(p, default={"v": 0}, expected_type=dict, quarantine=True, use_backup=True)
            self.assertEqual(missing, {"v": 0})
    def test_jsonl_quarantine_and_feedback_backup_restore(self):
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            with mock.patch.dict(os.environ, {"SHARD_DATA_DIR": tmp1}, clear=False):
                feedback.update_privacy_config({"require_manual_review": False})
                e1 = feedback.submit_feedback({"prompt": "How to sort?", "response": "sorted()", "rating": "up", "consent_given": True})
                raw_path = Path(feedback._feedback_path())
                with open(raw_path, "a", encoding="utf-8") as f:
                    f.write('{"broken_truncated_line":\n')
                rows, bad_cnt, qpath = storage.read_jsonl_safe(raw_path, quarantine=True)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["id"], e1["id"])
                self.assertEqual(bad_cnt, 1)
                self.assertTrue(qpath and os.path.isfile(qpath))
                ds = feedback.build_dataset_snapshot(task="sft", name="snap1")
                manifest = feedback.verify_dataset_integrity(ds["snapshot_id"])
                self.assertEqual(manifest["record_count"], 1)
                m_lan = lan.LanManager()
                with mock.patch.object(m_lan, "_start_threads", return_value=None):
                    m_lan.create("persist-net")
                bk = training.create_backup()
                self.assertTrue(os.path.isfile(bk["backup_path"]))
                bk_path = bk["backup_path"]
            with mock.patch.dict(os.environ, {"SHARD_DATA_DIR": tmp2}, clear=False):
                res = training.restore_backup(bk_path, verify_hashes=True)
                self.assertEqual(res["status"], "ok")
                self.assertGreaterEqual(res["restored_files"], 2)
                st = feedback.compute_stats()
                self.assertEqual(st["counts"]["approved"], 1)
                ds_list = feedback.list_datasets()
                self.assertTrue(any(d["snapshot_id"] == ds["snapshot_id"] for d in ds_list))
                zip_slip = Path(tmp2) / "evil.zip"
                with zipfile.ZipFile(zip_slip, "w") as zf:
                    zf.writestr("backup_manifest.json", json.dumps({"format": "shard-backup-v1", "files": {"data/../../evil.txt": {"sha256": "x"}}}))
                    zf.writestr("data/../../evil.txt", b"pwn")
                with self.assertRaises(ValueError):
                    storage.restore_state_backup(zip_slip, tmp2, tmp2)
    def test_reconcile_dead_job_deploy_guard_and_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"SHARD_DATA_DIR": tmp}, clear=False):
                training._save_jobs([{"job_id": "job-dead", "status": "running", "pid": 999999, "host_pid": 999999, "output_dir": str(Path(tmp) / "training" / "runs" / "job-dead")}])
                jobs = training.reconcile_jobs()
                self.assertEqual(jobs[0]["status"], "interrupted")
                training._save_registry({
                    "active_version_id": None,
                    "previous_version_id": None,
                    "previous_engine_model_id": None,
                    "active_engine_model_id": None,
                    "versions": [{"version_id": "v-missing", "approval_status": "approved", "artifact_path": str(Path(tmp) / "does_not_exist"), "merged_dir": None, "gguf_path": None}],
                })
                with self.assertRaises(FileNotFoundError):
                    training.deploy_version("v-missing")
                reg = training.list_model_versions()
                self.assertIsNone(reg.get("active_version_id"))
                feedback.update_privacy_config({"retention_days": 90})
                raw_path = Path(feedback._feedback_path())
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                old_ts = time.time() - (120 * 86400)
                new_ts = time.time()
                with open(raw_path, "w", encoding="utf-8") as f:
                    f.write(json.dumps({"id": "fb-old-rej", "created_at": old_ts, "updated_at": old_ts, "status": "rejected", "task": "sft"}) + "\n")
                    f.write(json.dumps({"id": "fb-old-app", "created_at": old_ts, "updated_at": old_ts, "status": "approved", "task": "sft"}) + "\n")
                    f.write(json.dumps({"id": "fb-new-rej", "created_at": new_ts, "updated_at": new_ts, "status": "rejected", "task": "sft"}) + "\n")
                pr = feedback.prune_expired_feedback()
                self.assertEqual(pr["removed"], 1)
                self.assertEqual(pr["remaining"], 2)
                self.assertEqual(pr["retention_days"], 90)
class TestSecurityEngineAndClientIsolation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env = mock.patch.dict(os.environ, {"SHARD_DATA_DIR": self._tmp.name}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        for lim in (server._PAIR_FAIL_LIMITER, server._PAIR_TOTAL_LIMITER, server._OWNER_FAIL_LIMITER, server._FEEDBACK_LIMITER):
            with lim._lock:
                lim._hits.clear()
        self._mgr = lan.LanManager(state_path=str(Path(self._tmp.name) / "lan.json"))
        self.addCleanup(self._mgr.off, True)
        self._lan_patch = mock.patch.object(server, "LAN", self._mgr)
        self._lan_patch.start()
        self.addCleanup(self._lan_patch.stop)
    def test_client_isolation_v1_gate_owner_headers_csrf_host_and_rate_limit(self):
        remote = TestClient(server.app, client=("192.168.1.50", 51000))
        local = TestClient(server.app, client=("127.0.0.1", 51000))
        r_html = remote.get("/")
        self.assertEqual(r_html.status_code, 200)
        self.assertIn('<script id="shard-boot"', r_html.text)
        self.assertIn('"client_mode": true', r_html.text)
        r_cli = remote.get("/client")
        self.assertEqual(r_cli.status_code, 200)
        self.assertIn('"client_mode": true', r_cli.text)
        l_html = local.get("/")
        self.assertEqual(l_html.status_code, 200)
        self.assertIn('"client_mode": false', l_html.text)
        boot = remote.get("/api/client/bootstrap")
        self.assertEqual(boot.status_code, 200)
        self.assertTrue(boot.json()["client_mode"])
        for ep in ("/api/system/hardware", "/api/hf/models", "/api/models/downloaded", "/api/keys", "/api/settings", "/api/drives", "/api/lan/invite", "/api/lan/diagnose", "/api/training/overview"):
            self.assertEqual(remote.get(ep).status_code, 403, ep)
        for ep, payload in (
            ("/api/keys/generate", {}),
            ("/api/lan/open", {"open": True}),
            ("/api/integrations/apply", {"tool": "claude"}),
            ("/api/training/backup", {}),
        ):
            self.assertEqual(remote.post(ep, json=payload).status_code, 403, ep)
        self.assertEqual(remote.get("/v1/models").status_code, 401)
        self.assertEqual(remote.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]}).status_code, 401)
        keys = server.load_keys()
        self.assertEqual(remote.get("/v1/models", headers={"Authorization": f"Bearer {keys[0]}"}).status_code, 200)
        self.assertEqual(local.get("/api/keys", headers={"X-Forwarded-For": "203.0.113.9"}).status_code, 403)
        owner_tok = training.get_owner_token()
        self.assertEqual(remote.get("/api/keys", headers={"X-Shard-Owner-Token": owner_tok}).status_code, 200)
        self.assertEqual(local.get("/api/system/hardware", headers={"Host": "evil.com"}).status_code, 403)
        self.assertEqual(local.post("/api/keys/generate", headers={"Origin": "http://evil.example"}).status_code, 403)
        self.assertEqual(local.post("/api/keys/generate", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(local.post("/api/models/delete", json={"model_id": "..\\..\\Windows"}).status_code, 400)
        r_prune = local.post("/api/training/feedback/prune", json={})
        self.assertEqual(r_prune.status_code, 200)
        self.assertIn("removed", r_prune.json())
        self.assertIn("retention_days", r_prune.json())
        self.assertIn("d.tailscale.running", l_html.text)
        self.assertIn("d.tailscale.ip", l_html.text)
        self.assertIn("d.removed", l_html.text)
        with mock.patch.object(self._mgr, "_start_threads", return_value=None):
            self._mgr.create("rl-net", open_net=False)
        codes = [remote.post("/api/lan/pair", json={"code": "AAAA-BBBB"}).status_code for _ in range(11)]
        self.assertIn(403, codes[:10])
        self.assertEqual(codes[-1], 429)
    def test_engine_guards_and_archive_zip_slip(self):
        prev_active = engine._active_engine
        try:
            with tempfile.TemporaryDirectory() as tmp:
                loc = Path(tmp) / "local_model"
                loc.mkdir(parents=True, exist_ok=True)
                (loc / "config.json").write_text("{}", encoding="utf-8")
                with mock.patch.dict(os.environ, {"SHARD_DATA_DIR": tmp, "HF_HOME": tmp}, clear=False):
                    snap = Path(tmp) / "hub" / "models--org--Tiny-GGUF" / "snapshots" / "abc"
                    snap.mkdir(parents=True, exist_ok=True)
                    (snap / "tiny-Q4_K_M.gguf").write_bytes(b"GGUF")
                    self.assertTrue(engine._is_model_cached_complete("org/Tiny-GGUF"))
                    eng = engine.ShardEngine()
                    with self.assertRaises(ValueError):
                        eng.load_model("\\\\attacker\\share\\model")
                    tok_mock = mock.MagicMock()
                    tok_mock.from_pretrained.side_effect = RuntimeError("probe-stop")
                    with mock.patch.object(engine, "TORCH_AVAILABLE", True), mock.patch.object(engine, "AutoTokenizer", tok_mock), mock.patch.object(engine._hardware, "get_hardware_profile", return_value={"ram_avail_mb": 4096}), mock.patch.object(eng, "_select_dtype_and_device", return_value=(None, "cpu")):
                        os.environ.pop("SHARD_TRUST_REMOTE_CODE", None)
                        eng._run_loader(str(loc), eng._load_seq)
                        self.assertIs(tok_mock.from_pretrained.call_args.kwargs["trust_remote_code"], False)
                        with mock.patch.dict(os.environ, {"SHARD_TRUST_REMOTE_CODE": "1"}, clear=False):
                            eng._run_loader(str(loc), eng._load_seq)
                            self.assertIs(tok_mock.from_pretrained.call_args.kwargs["trust_remote_code"], True)
                bad_zip = Path(tmp) / "bad.zip"
                with zipfile.ZipFile(bad_zip, "w") as zf:
                    zf.writestr("../../pwn.txt", b"x")
                with self.assertRaises(ValueError):
                    gguf_backend._extract_archive(str(bad_zip), str(Path(tmp) / "out"))
        finally:
            engine._active_engine = prev_active
if __name__ == "__main__":
    unittest.main()
