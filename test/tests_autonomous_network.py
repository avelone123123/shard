import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from fastapi.testclient import TestClient
from shard_engine import net_strategy, radmin_mgr, server, training
class TestAutonomousNetworkSuite(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="shard_auto_net_")
        self.client = TestClient(server.app)
        self.owner_hdr = {"X-Shard-Owner-Token": training.get_owner_token()}
    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
    def test_radmin_status_detection(self):
        st = radmin_mgr.get_radmin_status()
        self.assertIn("supported", st)
        self.assertIn("installed", st)
        self.assertIn("service_state", st)
        self.assertIn("processes", st)
        self.assertIn("adapter_detected", st)
        self.assertIn("ready", st)
        if sys.platform == "win32":
            self.assertTrue(st["supported"])
    def test_radmin_cluster_network_credentials(self):
        c1 = radmin_mgr.cluster_network_credentials("cluster-alpha-001")
        c2 = radmin_mgr.cluster_network_credentials("cluster-beta-002")
        self.assertTrue(c1["network_name"].startswith("SHARD-"))
        self.assertTrue(c2["network_name"].startswith("SHARD-"))
        self.assertNotEqual(c1["network_name"], c2["network_name"])
        self.assertNotEqual(c1["network_password"], c2["network_password"])
        self.assertGreaterEqual(len(c1["network_password"]), 10)
    def test_radmin_installer_verification_sanity(self):
        undersized_path = os.path.join(self.tmp_dir, "small_installer.exe")
        with open(undersized_path, "wb") as f:
            f.write(b"MZ" + b"\x00" * 1024)
        ok, reason, details = radmin_mgr.verify_installer(undersized_path)
        self.assertFalse(ok)
        self.assertIn("Invalid file size", reason)
        missing_path = os.path.join(self.tmp_dir, "nonexistent.exe")
        ok_miss, reason_miss, _ = radmin_mgr.verify_installer(missing_path)
        self.assertFalse(ok_miss)
        self.assertIn("does not exist", reason_miss)
    def test_production_default_infrastructure_and_failover(self):
        self.assertGreaterEqual(len(net_strategy.DEFAULT_PUBLIC_DISCOVERY_URLS), 1)
        self.assertGreaterEqual(len(net_strategy.DEFAULT_PUBLIC_RELAY_URLS), 1)
        self.assertTrue(all(u.startswith("https://") for u in net_strategy.DEFAULT_PUBLIC_DISCOVERY_URLS))
        self.assertTrue(all(u.startswith("wss://") for u in net_strategy.DEFAULT_PUBLIC_RELAY_URLS))
        orig_disc = os.environ.pop("SHARD_DISCOVERY_URL", None)
        orig_relay = os.environ.pop("SHARD_RELAY_URL", None)
        try:
            cfg = net_strategy.default_network_config()
            self.assertEqual(cfg["discovery_urls"], list(net_strategy.DEFAULT_PUBLIC_DISCOVERY_URLS))
            self.assertEqual(cfg["relay_urls"], list(net_strategy.DEFAULT_PUBLIC_RELAY_URLS))
