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
        self.assertEqual(ctx.exception.start, 1237)
        child_code = (
            "import sys\n"
            "sys.stdout.buffer.write((b'A' * 1235) + b'\\xe2\\x94\\x98\\n')\n"
            "sys.stdout.buffer.flush()\n"
        )
        thread_errs = []
        orig_hook = threading.excepthook
        threading.excepthook = lambda args: thread_errs.append(args.exc_value)
        try:
            legacy = subprocess.run([sys.executable, "-c", child_code], capture_output=True, text=True, encoding="cp1251", timeout=10)
            self.assertIsNone(legacy.stdout)
            self.assertTrue(any(isinstance(e, UnicodeDecodeError) and e.start == 1237 for e in thread_errs))
            thread_errs.clear()
            res = proc_io.run_subprocess_safe([sys.executable, "-c", child_code], timeout=10)
        finally:
            threading.excepthook = orig_hook
        self.assertEqual(thread_errs, [])
        self.assertEqual(res.returncode, 0)
        self.assertIn("\u2518", res.stdout)
        self.assertEqual(res.stderr, "")
    def test_safe_decode_standalone_0x98_and_mixed_utf8_cyrillic_emoji(self):
        raw_utf8 = "Привет мир 🚀 “smart quotes” ‘single’ — … ┌──┘".encode("utf-8")
        decoded = proc_io.safe_decode_subprocess_output(raw_utf8)
        self.assertEqual(decoded, "Привет мир 🚀 “smart quotes” ‘single’ — … ┌──┘")
        raw_bad = b"prefix \xe2\x94\x98 " + b"\x98" + " Привет 🎉".encode("utf-8")
        decoded_bad = proc_io.safe_decode_subprocess_output(raw_bad)
        self.assertIn("┘", decoded_bad)
        self.assertIn("Привет 🎉", decoded_bad)
        self.assertIn("\ufffd", decoded_bad)
        ansi_enc = str(proc_io._win_ansi_encoding() or "").lower()
        raw_only_0x98 = (b"X" * 1237) + b"\x98" + b" tail"
        decoded_only = proc_io.safe_decode_subprocess_output(raw_only_0x98)
        self.assertTrue(decoded_only.startswith("X" * 1237))
        self.assertTrue(decoded_only.endswith(" tail"))
        if os.name == "nt" and "1252" in ansi_enc:
            self.assertEqual(decoded_only, ("X" * 1237) + "\u02dc tail")
        else:
            self.assertIn("\ufffd", decoded_only)
        invalid_byte = b"\x81" if "1252" in ansi_enc else b"\x98"
        decoded_invalid = proc_io.safe_decode_subprocess_output((b"Y" * 1237) + invalid_byte + b" end")
        self.assertEqual(decoded_invalid, ("Y" * 1237) + "\ufffd end")
        with mock.patch.object(proc_io, "_win_ansi_encoding", return_value="cp1251"):
            decoded_cp1251_0x98 = proc_io.safe_decode_subprocess_output(raw_only_0x98)
            self.assertEqual(decoded_cp1251_0x98, ("X" * 1237) + "\ufffd tail")
        if os.name == "nt":
            cp1251_bytes = "Ошибка доступа в системе Windows".encode("cp1251")
            if "1251" in ansi_enc:
                self.assertEqual(proc_io.safe_decode_subprocess_output(cp1251_bytes), "Ошибка доступа в системе Windows")
            oem_enc = str(proc_io._win_oem_encoding() or "")
            if oem_enc:
                sample_oem = "Служба работает" if "866" in oem_enc.lower() else "Status \u2514\u2500\u2518"
                oem_bytes = sample_oem.encode(oem_enc)
                self.assertEqual(proc_io.safe_decode_subprocess_output(oem_bytes, prefer_utf8=False, try_oem=True), sample_oem)
            with mock.patch.object(proc_io, "_win_oem_encoding", return_value="cp866"):
                self.assertEqual(proc_io.safe_decode_subprocess_output("Служба работает".encode("cp866"), prefer_utf8=False, try_oem=True), "Служба работает")
    def test_subprocess_mixed_stdout_stderr_and_nonzero_exit(self):
        child_code = (
            "import sys;"
            "sys.stdout.buffer.write('ASCII line\\n'.encode('utf-8'));"
            "sys.stdout.buffer.write('Русский текст stdout 🚀 “умные кавычки”\\n'.encode('utf-8'));"
            "sys.stdout.buffer.write((b'B' * 1235) + b'\\xe2\\x94\\x98\\n');"
            "sys.stderr.buffer.write('Ошибка stderr ┘ '.encode('utf-8') + b'\\x98\\n');"
            "sys.stdout.buffer.flush();sys.stderr.buffer.flush();"
            "sys.exit(7)"
        )
        res = proc_io.run_subprocess_safe([sys.executable, "-c", child_code], timeout=10)
        self.assertEqual(res.returncode, 7)
        self.assertIn("ASCII line", res.stdout)
        self.assertIn("Русский текст stdout 🚀 “умные кавычки”", res.stdout)
        self.assertIn("┘", res.stdout)
        self.assertNotIn("Ошибка stderr", res.stdout)
        self.assertIn("Ошибка stderr ┘", res.stderr)
        self.assertIn("\ufffd", res.stderr)
        self.assertNotIn("ASCII line", res.stderr)
    def test_large_concurrent_stdout_and_stderr_no_deadlock(self):
        child_code = (
            "import sys\n"
            "line_out = ('OUT Кириллица 🚀 ' + ('x' * 200) + '\\n').encode('utf-8')\n"
            "line_err = ('ERR Ошибка “test” ' + ('y' * 200) + '\\n').encode('utf-8')\n"
            "for i in range(600):\n"
            "    sys.stdout.buffer.write(line_out)\n"
            "    sys.stderr.buffer.write(line_err)\n"
            "sys.stdout.buffer.flush()\n"
            "sys.stderr.buffer.flush()\n"
        )
        res = proc_io.run_subprocess_safe([sys.executable, "-c", child_code], timeout=15)
        self.assertEqual(res.returncode, 0)
        self.assertGreater(len(res.stdout), 120000)
        self.assertGreater(len(res.stderr), 120000)
        self.assertIn("OUT Кириллица 🚀", res.stdout)
        self.assertIn("ERR Ошибка “test”", res.stderr)
        out_cb = []
        err_cb = []
        sres = proc_io.stream_subprocess_safe(
            [sys.executable, "-c", child_code],
            on_stdout=out_cb.append,
            on_stderr=err_cb.append,
            timeout=15,
        )
        self.assertEqual(sres["returncode"], 0)
        self.assertFalse(sres["cancelled"])
        self.assertEqual(len(out_cb), 600)
        self.assertEqual(len(err_cb), 600)
    def test_streaming_cancellation_while_emitting_unicode_and_0x98(self):
