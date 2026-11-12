import json
import os
import signal
import socket
import subprocess
import sys
import time
from . import paths
_WIN_DETACHED = 0x00000008
_WIN_NEW_GROUP = 0x00000200
_WIN_NO_WINDOW = 0x08000000
def read_pid_info():
    pf = paths.pid_file()
    if not os.path.isfile(pf):
        return None
    try:
        with open(pf, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("pid"), int):
            return data
    except Exception:
        pass
    return None
def write_pid_info(pid, host, port):
    pf = paths.pid_file()
    os.makedirs(os.path.dirname(pf), exist_ok=True)
    payload = {
        "pid": int(pid),
        "host": str(host),
        "port": int(port),
        "started_at": time.time(),
        "executable": sys.executable,
    }
    tmp = pf + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, pf)
    return payload
def clear_pid_info():
    pf = paths.pid_file()
    try:
        if os.path.exists(pf):
            os.remove(pf)
    except Exception:
        pass
def is_pid_running(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        import psutil
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except ImportError:
        pass
    except Exception:
        return False
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            try:
                code = ctypes.c_ulong(0)
                if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return code.value == 259
                return False
            finally:
                k32.CloseHandle(h)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
def is_managed_shard_process(info):
    if not isinstance(info, dict):
        return False
    pid = info.get("pid")
    if not is_pid_running(pid):
        return False
    try:
        import psutil
        p = psutil.Process(pid)
        cmd = " ".join(p.cmdline() or []).lower()
        name = (p.name() or "").lower()
        if any(tok in cmd for tok in ("shard_engine", "shard-engine", "shard", "uvicorn")):
            return True
        if name.startswith("shard"):
            return True
        return False
    except ImportError:
        return True
    except Exception:
        return False
def probe_host_for_bind(host):
    if not host or host in ("0.0.0.0", "::", "*"):
        return "127.0.0.1"
    return host
def is_port_in_use(host, port, timeout=0.4):
    target = probe_host_for_bind(host)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((target, int(port))) == 0
    except Exception:
        return False
    finally:
        s.close()
def ping_server(host, port, timeout=1.5):
    target = probe_host_for_bind(host)
    url = f"http://{target}:{int(port)}/v1/ping"
    try:
        import requests
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None
def running_status():
    info = read_pid_info()
    if info and not is_managed_shard_process(info):
        clear_pid_info()
        info = None
    return info
def _tail_file(path, max_chars=500):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
        return txt[-max_chars:].strip()
    except Exception:
        return ""
def start_background(host="0.0.0.0", port=8000, no_browser=True, timeout=20.0):
    existing = running_status()
    if existing:
        return False, {"error": f"SHARD is already running (PID {existing['pid']}, http://{probe_host_for_bind(existing['host'])}:{existing['port']})", "info": existing}
    if is_port_in_use(host, port):
        ping = ping_server(host, port)
        if ping:
            return False, {"error": f"A SHARD instance is already listening on port {port}. Stop it or choose --port."}
        return False, {"error": f"Port {port} is already in use by another process. Try --port {int(port) + 1}."}
    lf = paths.log_file()
    os.makedirs(os.path.dirname(lf), exist_ok=True)
    os.makedirs(os.path.dirname(paths.pid_file()), exist_ok=True)
    env = os.environ.copy()
    env["SHARD_HOST"] = str(host)
    env["SHARD_PORT"] = str(port)
    if no_browser:
        env["SHARD_NO_BROWSER"] = "1"
    cmd = [sys.executable, "-m", "shard_engine.main"]
    kw = {"stdin": subprocess.DEVNULL, "env": env}
    if os.name == "nt":
        kw["creationflags"] = _WIN_DETACHED | _WIN_NEW_GROUP | _WIN_NO_WINDOW
    else:
        kw["start_new_session"] = True
    log_handle = open(lf, "a", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(cmd, stdout=log_handle, stderr=subprocess.STDOUT, **kw)
    finally:
        log_handle.close()
    info = write_pid_info(proc.pid, host, port)
    deadline = time.time() + timeout
    while time.time() < deadline:
        rc = proc.poll()
        if rc is not None:
            clear_pid_info()
            tail = _tail_file(lf)
            return False, {"error": f"SHARD exited early (code {rc}).", "log_file": lf, "log_tail": tail}
        if ping_server(host, port, timeout=0.8) is not None:
            actual = read_pid_info() or info
            return True, {"pid": actual["pid"], "host": host, "port": int(port), "log_file": lf, "info": actual}
        time.sleep(0.35)
    if proc.poll() is None:
        actual = read_pid_info() or info
        return True, {"pid": actual["pid"], "host": host, "port": int(port), "log_file": lf, "info": actual, "warming_up": True}
    clear_pid_info()
    return False, {"error": "SHARD failed to become ready in time.", "log_file": lf, "log_tail": _tail_file(lf)}
def _terminate_pid(pid, timeout=6.0):
    try:
        import psutil
        proc = psutil.Process(pid)
        children = proc.children(recursive=True)
        for ch in children:
            try:
                ch.terminate()
            except Exception:
                pass
        proc.terminate()
        _, alive = psutil.wait_procs([proc, *children], timeout=timeout)
        for p in alive:
            try:
                p.kill()
            except Exception:
                pass
        return True
    except ImportError:
        pass
    except Exception:
        return False
    try:
        if os.name == "nt":
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(1, False, pid)
            if h:
                try:
                    k32.TerminateProcess(h, 0)
                finally:
                    k32.CloseHandle(h)
        else:
            os.kill(pid, signal.SIGTERM)
            deadline = time.time() + timeout
            while time.time() < deadline:
                if not is_pid_running(pid):
                    return True
                time.sleep(0.2)
            os.kill(pid, signal.SIGKILL)
        return True
    except Exception:
        return False
def stop_server(timeout=6.0):
    info = read_pid_info()
    if not info:
        return False, "No managed SHARD PID file found (if running in foreground, press Ctrl+C in its terminal)."
    pid = info.get("pid")
    if not is_managed_shard_process(info):
        clear_pid_info()
        return False, f"Removed stale PID file (process {pid} is no longer running)."
    ok = _terminate_pid(pid, timeout=timeout)
    clear_pid_info()
    if ok:
        return True, f"Stopped SHARD server (PID {pid})."
    return False, f"Failed to terminate SHARD process PID {pid}."
