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
