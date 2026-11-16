import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock
import shard_engine
from shard_engine import cli, paths, proc_io, soup_adapter, storage, updater
class TestWindowsSubprocessEncodingAndStreaming(unittest.TestCase):
    def test_reproduce_cp1251_0x98_at_1237_and_safe_decode(self):
        payload = (b"A" * 1235) + b"\xe2\x94\x98" + b"\r\nDry run ok"
        idx = payload.index(0x98)
        self.assertEqual(idx, 1237)
        with self.assertRaises(UnicodeDecodeError) as ctx:
            payload.decode("cp1251")
