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
