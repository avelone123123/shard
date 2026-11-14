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
