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
