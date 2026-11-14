import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock
import shard_engine
from shard_engine import cli, lan, paths, process
class TestPathsAndPackaging(unittest.TestCase):
    def test_version_single_source_of_truth(self):
        root = Path(__file__).resolve().parent
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        setup_py = (root / "setup.py").read_text(encoding="utf-8")
        self.assertEqual(shard_engine.__version__, "1.8.0")
        self.assertIn('dynamic = ["version"]', pyproject)
        self.assertIn('version = {attr = "shard_engine.__version__"}', pyproject)
        self.assertNotIn("1.4.79", setup_py)
    def test_package_resources_accessible(self):
        self.assertTrue(paths.package_resource_exists("dashboard.html"))
        self.assertTrue(paths.package_resource_exists("engine_optimizations.json"))
        html = paths.read_package_text("dashboard.html")
        opts = json.loads(paths.read_package_text("engine_optimizations.json"))
        self.assertIn("SHARD", html)
        self.assertNotIn("192.168.100.6", html)
        self.assertIsInstance(opts, dict)
    def test_shard_data_dir_with_unicode_and_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "тест shard" / "data dir"
            with mock.patch.dict(os.environ, {"SHARD_DATA_DIR": str(target)}, clear=False):
                self.assertEqual(paths.data_dir(), target.resolve())
                self.assertEqual(paths.config_dir(), target.resolve())
                self.assertEqual(paths.cache_dir(), target.resolve() / "cache")
                self.assertEqual(paths.logs_dir(), target.resolve() / "logs")
                self.assertEqual(paths.run_dir(), target.resolve() / "run")
                self.assertTrue(paths.keys_file().startswith(str(target.resolve())))
                self.assertTrue(paths.pid_file().startswith(str(target.resolve())))
                self.assertTrue(paths.default_models_dir().startswith(str(target.resolve())))
    def test_platform_default_dirs_windows_linux_macos(self):
        clean_env = {k: v for k, v in os.environ.items() if k not in ("SHARD_DATA_DIR", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR")}
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp) / "home"
            fake_local = Path(tmp) / "localappdata"
            with mock.patch.dict(os.environ, {**clean_env, "LOCALAPPDATA": str(fake_local)}, clear=True):
                with mock.patch("shard_engine.paths._is_win", return_value=True), mock.patch("shard_engine.paths._is_mac", return_value=False):
                    self.assertEqual(paths.data_dir(), fake_local.resolve() / "SHARD")
                    self.assertEqual(paths.config_dir(), fake_local.resolve() / "SHARD" / "config")
            with mock.patch.dict(os.environ, clean_env, clear=True):
                with mock.patch("pathlib.Path.home", return_value=fake_home), mock.patch("shard_engine.paths._is_win", return_value=False), mock.patch("shard_engine.paths._is_mac", return_value=True):
                    self.assertEqual(paths.data_dir(), fake_home / "Library" / "Application Support" / "SHARD")
                    self.assertEqual(paths.cache_dir(), fake_home / "Library" / "Caches" / "SHARD")
                    self.assertEqual(paths.logs_dir(), fake_home / "Library" / "Logs" / "SHARD")
            with mock.patch.dict(os.environ, clean_env, clear=True):
                with mock.patch("pathlib.Path.home", return_value=fake_home), mock.patch("shard_engine.paths._is_win", return_value=False), mock.patch("shard_engine.paths._is_mac", return_value=False):
                    self.assertEqual(paths.data_dir(), fake_home / ".local" / "share" / "shard")
                    self.assertEqual(paths.config_dir(), fake_home / ".config" / "shard")
                    self.assertEqual(paths.cache_dir(), fake_home / ".cache" / "shard")
                    self.assertEqual(paths.logs_dir(), fake_home / ".local" / "state" / "shard" / "logs")
class TestCliCommandsAndFallback(unittest.TestCase):
    def test_help_and_version_flags(self):
        for argv in (["help"], ["--help"], ["-h"], ["version"], ["-V"], ["--version"]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.main(argv)
            out = buf.getvalue()
            self.assertIn("1.8.0", out)
    def test_doctor_runs_cleanly(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["doctor"])
        out = buf.getvalue()
        self.assertIn("SHARD Environment Diagnostics", out)
        self.assertIn("Asset dashboard.html", out)
    def test_pure_python_fallback_and_strict_native(self):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"SHARD_NATIVE_LIB": r"Z:\nonexistent\shard_core.missing"}, clear=False):
            with redirect_stdout(buf):
                cli.main(["test"])
            out = buf.getvalue()
            self.assertIn("PYTHON FALLBACK", out)
            self.assertIn("[PASS] Verified 12 math kernel APIs", out)
            with self.assertRaises(SystemExit) as ctx:
                with redirect_stdout(io.StringIO()):
                    cli.main(["test", "--strict-native"])
            self.assertEqual(ctx.exception.code, 1)
    def test_update_check_variants(self):
        class _Resp:
            def __init__(self, code, data=None):
                self.status_code = code
                self._data = data or {}
            def json(self):
                return self._data
        for resp, expected in (
            (_Resp(404), "GITHUB_UNAVAILABLE"),
            (_Resp(200, {"info": {"version": shard_engine.__version__}}), "up to date"),
            (_Resp(200, {"info": {"version": "9.9.9"}}), "Update available"),
        ):
            buf = io.StringIO()
            with mock.patch("requests.get", return_value=resp), mock.patch("subprocess.run") as mrun:
                with redirect_stdout(buf):
                    cli.main(["update", "--check"])
                mrun.assert_not_called()
            self.assertIn(expected, buf.getvalue())
    def test_models_dir_and_key_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            custom = Path(tmp) / "custom_models"
            with mock.patch.dict(os.environ, {"SHARD_DATA_DIR": tmp}, clear=False):
                with mock.patch("shard_engine.process.ping_server", return_value=None):
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        cli.main(["key", "--gen"])
                    self.assertIn("sk-shard-", buf.getvalue())
                    self.assertTrue(os.path.isfile(paths.keys_file()))
                    buf2 = io.StringIO()
                    with redirect_stdout(buf2):
                        cli.main(["models-dir", str(custom)])
                    self.assertTrue(custom.is_dir())
                    buf3 = io.StringIO()
                    with redirect_stdout(buf3):
                        cli.main(["models-dir", "reset"])
                    self.assertIn("reset to default", buf3.getvalue())
class TestProcessManagement(unittest.TestCase):
    def test_stale_pid_is_not_killed_if_unrelated(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"SHARD_DATA_DIR": tmp}, clear=False):
                process.write_pid_info(999999, "127.0.0.1", 18099)
                self.assertTrue(os.path.isfile(paths.pid_file()))
                fake_proc = mock.Mock()
                fake_proc.is_running.return_value = True
                fake_proc.status.return_value = "running"
                fake_proc.cmdline.return_value = ["notepad.exe"]
                fake_proc.name.return_value = "notepad.exe"
                with mock.patch("psutil.Process", return_value=fake_proc):
                    self.assertFalse(process.is_managed_shard_process({"pid": 999999}))
                    ok, msg = process.stop_server()
                    self.assertFalse(ok)
                    self.assertIn("stale", msg.lower())
                    self.assertFalse(os.path.isfile(paths.pid_file()))
    def test_managed_pid_recognition(self):
        fake_proc = mock.Mock()
        fake_proc.is_running.return_value = True
        fake_proc.status.return_value = "running"
        fake_proc.cmdline.return_value = ["python", "-m", "shard_engine.main"]
        fake_proc.name.return_value = "python"
        with mock.patch("psutil.Process", return_value=fake_proc):
            self.assertTrue(process.is_managed_shard_process({"pid": 12345}))
class TestLanDynamicDiscoveryAndClusterFlow(unittest.TestCase):
    def test_local_ips_are_dynamic_valid_ipv4(self):
        ips = lan.local_ips()
        self.assertIsInstance(ips, list)
        for ip in ips:
            self.assertFalse(ip.startswith("127."))
            self.assertFalse(ip.startswith("169.254."))
            self.assertRegex(ip, r"^\d+\.\d+\.\d+\.\d+$")
        primary = lan.local_ip()
        self.assertRegex(primary, r"^\d+\.\d+\.\d+\.\d+$")
    def test_validate_master_host(self):
        self.assertEqual(lan.validate_master_host("127.0.0.1"), ("127.0.0.1", None))
        self.assertEqual(lan.validate_master_host("http://10.0.0.5:8000/"), ("10.0.0.5", None))
        self.assertEqual(lan.validate_master_host("master-node.local"), ("master-node.local", None))
        for bad in ("", "0.0.0.0", "999.1.1.1", "bad host!"):
            clean, err = lan.validate_master_host(bad)
            self.assertIsNone(clean)
            self.assertIsNotNone(err)
    def test_two_node_create_pair_join_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            m_dir = Path(tmp) / "master"
            w_dir = Path(tmp) / "worker"
            m_dir.mkdir()
            w_dir.mkdir()
            master = lan.LanManager(state_path=str(m_dir / "lan.json"))
            worker = lan.LanManager(state_path=str(w_dir / "lan.json"))
            class _Handler(BaseHTTPRequestHandler):
                def log_message(self, format, *args):
                    return
                def _read_json(self):
                    n = int(self.headers.get("Content-Length", "0") or 0)
                    return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
                def _send(self, code, obj):
                    raw = json.dumps(obj).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                def do_POST(self):
                    body = self._read_json()
                    ip = self.client_address[0]
                    if self.path == "/api/lan/pair":
                        res, err = master.handle_pair(body.get("code", ""), ip, body.get("name"), bool(body.get("share")))
                        if err:
                            self._send(403, {"detail": err})
                        else:
                            self._send(200, res)
                    elif self.path == "/api/lan/leave":
                        master.handle_leave(ip, body.get("token", ""))
                        self._send(200, {"ok": True})
                    else:
                        self._send(404, {"detail": "not found"})
            srv = HTTPServer(("127.0.0.1", 0), _Handler)
            port = srv.server_port
            th = threading.Thread(target=srv.serve_forever, daemon=True)
            th.start()
            try:
                with mock.patch("shard_engine.lan.LanManager._start_threads", return_value=None):
                    st = master.create(name="test-master", open_net=False, model="Qwen/Qwen2.5-Coder-7B-Instruct")
                    self.assertEqual(st["mode"], "server")
                    self.assertEqual(st["target_model"], "Qwen/Qwen2.5-Coder-7B-Instruct")
                    self.assertTrue(st["pairing_code"])
                    bad_ip = worker.join("999.999.1.1", st["pairing_code"], share=False, port=port)
                    self.assertEqual(bad_ip["status"], "error")
                    wrong_code = worker.join("127.0.0.1", "WRONG-00", share=False, port=port)
                    self.assertEqual(wrong_code["status"], "error")
                    ok_join = worker.join("127.0.0.1", st["pairing_code"], share=False, port=port)
                    self.assertEqual(ok_join["status"], "ok")
                    self.assertEqual(ok_join["mode"], "member")
                    self.assertEqual(len(master.status()["members"]), 1)
                    worker.off(quiet=True)
                    open_res = master.set_open(True)
                    self.assertTrue(open_res["open"])
                    ok_open = worker.join("127.0.0.1", "", share=False, port=port)
                    self.assertEqual(ok_open["status"], "ok")
            finally:
                master.off(quiet=True)
                worker.off(quiet=True)
                srv.shutdown()
                srv.server_close()
class TestBuiltDistribution(unittest.TestCase):
    def test_wheel_contents_when_built(self):
        whl = Path(__file__).resolve().parent / "dist" / f"shard_engine-{shard_engine.__version__}-py3-none-any.whl"
        if not whl.is_file():
            self.skipTest("Wheel not built in dist/ yet")
        with zipfile.ZipFile(whl) as zf:
            names = zf.namelist()
            self.assertIn("shard_engine/__init__.py", names)
            self.assertIn("shard_engine/cli.py", names)
            self.assertIn("shard_engine/dashboard.html", names)
            self.assertIn("shard_engine/engine_optimizations.json", names)
            ep_names = [n for n in names if n.endswith("entry_points.txt")]
            self.assertTrue(ep_names)
            ep_text = zf.read(ep_names[0]).decode("utf-8")
            self.assertIn("shard = shard_engine.cli:main", ep_text)
            for forbidden in ("keys_db.json", ".exe", ".dll", "tests_shard.py", "tests_cli_pkg.py"):
                self.assertFalse(any(forbidden in n for n in names), f"Forbidden file in wheel: {forbidden}")
if __name__ == "__main__":
    unittest.main()
