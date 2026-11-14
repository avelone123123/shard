import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from shard_engine import __version__, cli, feedback, model_browser, paths, soup_adapter, storage, training, updater
from shard_engine.server import app
class TestPhaseIProductHardening(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        self._env_patch = patch.dict(os.environ, {
            "SHARD_DATA_DIR": str(self._root / "data"),
            "SHARD_CONFIG_DIR": str(self._root / "config"),
            "SHARD_CACHE_DIR": str(self._root / "cache"),
            "SHARD_RUN_DIR": str(self._root / "run"),
            "SHARD_MODELS_DIR": str(self._root / "models"),
        })
        self._env_patch.start()
        for p in paths.all_dirs().values():
            Path(p).mkdir(parents=True, exist_ok=True)
        self.client = TestClient(app)
        self.owner_headers = {"X-Shard-Owner-Token": training.get_owner_token()}
    def tearDown(self):
        try:
            training._ACTIVE_PROCS.clear()
        except Exception:
            pass
        self._env_patch.stop()
