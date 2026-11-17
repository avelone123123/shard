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
