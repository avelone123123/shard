import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from . import hardware
from . import model_browser
from . import paths
from . import settings
from . import storage
from . import updater
SOUP_VALID_TASKS = ("sft", "dpo", "orpo", "kto", "simpo", "ipo", "grpo")
SOUP_VALID_FORMATS = ("chatml", "dpo", "alpaca", "sharegpt", "kto", "plaintext", "auto")
SOUP_VALID_BACKENDS = ("transformers", "unsloth", "mlx")
SOUP_VALID_QUANTS = ("4bit", "8bit", "none")
SOUP_VALID_GGUF_QUANTS = ("q4_0", "q4_k_m", "q5_k_m", "q8_0", "f16", "f32")
SOUP_MIN_SHARD_COMPAT = "0.70.0"
SOUP_LIFECYCLE_STATES = (
    "NOT_INSTALLED",
    "CHECKING_ENVIRONMENT",
    "FINDING_PYTHON",
    "CREATING_VENV",
    "INSTALLING",
    "VERIFYING",
    "INSTALLED",
    "UPDATE_AVAILABLE",
    "UPDATING",
    "FAILED",
)
SOUP_UPSTREAM_PYPI_URL = "https://pypi.org/pypi/soup-cli/json"
SOUP_UPSTREAM_RELEASES_URL = "https://api.github.com/repos/MakazhanAlpamys/Soup/releases/latest"
SOUP_VERSION_CACHE_TTL = 4 * 3600
_SAFE_PYTHON_EXE_RE = re.compile(r"^python(\d+(?:\.\d+)*)?(?:\.exe)?$", re.IGNORECASE)
_WIN_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_INSTALL_LOCK = threading.RLock()
_INSTALL_STATE = {
    "status": "idle",
    "lifecycle_state": "NOT_INSTALLED",
    "stage": "Не запущено",
    "action": None,
    "progress_pct": 0,
    "step": "",
    "logs": [],
    "venv_dir": None,
    "python_executable": None,
    "installed_version": None,
    "verification": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
}
def _split_cmd(raw):
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw if str(x).strip()]
    txt = str(raw or "").strip()
    if not txt:
        return []
    if os.path.isfile(txt):
        return [txt]
    try:
        tokens = shlex.split(txt, posix=(os.name != "nt"))
        cleaned = []
        for tok in tokens:
            t = tok.strip()
            if len(t) >= 2 and ((t[0] == '"' and t[-1] == '"') or (t[0] == "'" and t[-1] == "'")):
                t = t[1:-1]
            if t:
                cleaned.append(t)
        return cleaned
    except ValueError:
        return txt.split()
def _validate_custom_python(raw_path):
    if not raw_path:
        return None
    txt = str(raw_path).strip()
    if not txt or "\x00" in txt:
        raise ValueError("Invalid python_executable path.")
    if txt.startswith("\\\\") or txt.startswith("//"):
        raise ValueError("Network UNC paths are not permitted for python_executable.")
    if model_browser.is_sensitive_path(txt):
        raise PermissionError("Refusing python_executable inside protected directory.")
    base = os.path.basename(txt)
    if not _SAFE_PYTHON_EXE_RE.match(base):
        raise ValueError("python_executable must point to a python executable (python / python3.12 / python.exe).")
    if not os.path.isfile(txt):
        raise FileNotFoundError(f"Python executable not found: {txt}")
    return os.path.abspath(txt)
def _soup_version_cache_path():
    return Path(paths.cache_dir()) / "soup_version_cache.json"
def check_latest_soup_version(force=False, timeout=2.5, allow_network=True):
    cp = _soup_version_cache_path()
    cached = storage.read_json_safe(cp, default=None, expected_type=dict, quarantine=False, use_backup=True) if cp.is_file() else None
    if not force and isinstance(cached, dict) and cached.get("latest_version"):
        if (time.time() - float(cached.get("checked_at") or 0)) < SOUP_VERSION_CACHE_TTL:
            out = dict(cached)
            out["cached"] = True
            return out
    if not allow_network:
        return {
            "latest_version": cached.get("latest_version") if isinstance(cached, dict) else None,
            "requires_python": cached.get("requires_python") if isinstance(cached, dict) else ">=3.11,<3.14",
            "source": cached.get("source") if isinstance(cached, dict) else None,
            "release_url": "https://github.com/MakazhanAlpamys/Soup/releases",
            "cached": bool(cached),
            "checked_at": cached.get("checked_at") if isinstance(cached, dict) else None,
            "error": None if cached else "Network check skipped.",
        }
    import requests
    latest = None
    req_py = ">=3.11,<3.14"
    rel_url = "https://github.com/MakazhanAlpamys/Soup/releases"
    src = None
    err = None
    try:
        r = requests.get(SOUP_UPSTREAM_PYPI_URL, timeout=timeout, verify=True, headers={"User-Agent": "SHARD-SOUP-Checker"})
        if r.status_code == 200:
            info = (r.json() or {}).get("info") or {}
            ver = str(info.get("version") or "").strip()
            if updater.is_valid_version_str(ver) and not updater.is_prerelease_version(ver):
                latest = ver
                req_py = info.get("requires_python") or req_py
                rel_url = f"https://pypi.org/project/soup-cli/{ver}/"
                src = "pypi"
    except Exception as e:
        err = str(e)
    if not latest:
        try:
            r2 = requests.get(SOUP_UPSTREAM_RELEASES_URL, timeout=timeout, verify=True, headers={"Accept": "application/vnd.github+json", "User-Agent": "SHARD-SOUP-Checker"})
            if r2.status_code == 200:
                d2 = r2.json() or {}
                ver = str(d2.get("tag_name") or "").strip().lstrip("vV")
                if updater.is_valid_version_str(ver) and not updater.is_prerelease_version(ver) and not d2.get("prerelease"):
                    latest = ver
                    rel_url = d2.get("html_url") or rel_url
                    src = "github_release"
                    err = None
        except Exception as e:
            err = err or str(e)
    if latest:
        payload = {
            "latest_version": latest,
            "requires_python": req_py,
            "source": src,
            "release_url": rel_url,
            "checked_at": time.time(),
            "error": None,
        }
        try:
            cp.parent.mkdir(parents=True, exist_ok=True)
            storage.atomic_write_json(cp, payload, fsync=True, backup=True)
        except Exception:
            pass
        payload["cached"] = False
        return payload
    return {
        "latest_version": cached.get("latest_version") if isinstance(cached, dict) else None,
        "requires_python": req_py,
        "source": cached.get("source") if isinstance(cached, dict) else None,
        "release_url": rel_url,
        "cached": bool(cached),
        "checked_at": cached.get("checked_at") if isinstance(cached, dict) else None,
        "error": err or "Unable to reach upstream SOUP repository or PyPI.",
    }
def _probe_python_env(py_exe, check_deps=True, timeout=8):
    if not py_exe or not os.path.isfile(py_exe):
        return None
    script = (
        "import importlib.util, importlib.metadata, json, sys;"
        "mods=['soup_cli','torch','transformers','peft','trl','datasets','accelerate','bitsandbytes'];"
        "present={m: (importlib.util.find_spec(m) is not None) for m in mods};"
        "sv=None;\n"
        "try:\n"
        " sv=importlib.metadata.version('soup-cli')\n"
        "except Exception:\n"
        " pass\n"
        "cuda=False\n"
        "if present.get('torch') and %r:\n"
        " try:\n"
        "  import torch; cuda=bool(torch.cuda.is_available())\n"
        " except Exception:\n"
        "  pass\n"
        "print(json.dumps({'py': list(sys.version_info[:3]), 'soup_version': sv, 'present': present, 'cuda': cuda}))"
    ) % (bool(check_deps),)
    try:
        res = subprocess.run(
            [py_exe, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_WIN_NO_WINDOW,
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip().splitlines()[-1])
            return data
    except Exception:
        return None
    return None
def _in_process_probe(check_deps=True):
    mods = ("soup_cli", "torch", "transformers", "peft", "trl", "datasets", "accelerate", "bitsandbytes")
    present = {m: (importlib.util.find_spec(m) is not None) for m in mods}
    sv = None
    try:
        import importlib.metadata as _md
        sv = _md.version("soup-cli")
    except Exception:
        sv = None
    cuda = False
    if present.get("torch") and check_deps:
        try:
            import torch
            cuda = bool(torch.cuda.is_available())
        except Exception:
            cuda = False
    return {
        "py": list(sys.version_info[:3]),
        "soup_version": sv,
        "present": present,
        "cuda": cuda,
    }
def _candidate_python_paths():
    cands = []
    env_py = os.environ.get("SHARD_SOUP_PYTHON", "").strip()
    if env_py:
        cands.append(env_py)
    saved = settings._load()
    if saved.get("soup_python"):
        cands.append(str(saved["soup_python"]).strip())
    sub = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    for base in (
        Path(paths.soup_venv_dir()),
        Path.home() / ".soup-venv",
        Path.home() / ".soup" / "venv",
    ):
        cands.append(str(base.joinpath(*sub)))
    cands.append(sys.executable)
    out = []
    seen = set()
    for p in cands:
        if not p:
            continue
        norm = os.path.normcase(os.path.abspath(p))
        if norm not in seen and os.path.isfile(p):
            seen.add(norm)
            out.append(p)
    return out
def _py_compatible(py_tuple):
    if not py_tuple or len(py_tuple) < 2:
        return False
    major, minor = int(py_tuple[0]), int(py_tuple[1])
    return (major == 3) and (11 <= minor < 14)
def _probe_python_version_fast(py_exe, timeout=4):
    if not py_exe or not os.path.isfile(py_exe):
        return None
    try:
        res = subprocess.run(
            [py_exe, "-c", "import json, sys; print(json.dumps({'exe': sys.executable, 'ver': list(sys.version_info[:3])}))"],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_WIN_NO_WINDOW,
        )
        if res.returncode == 0 and res.stdout.strip():
            return json.loads(res.stdout.strip().splitlines()[-1])
    except Exception:
        return None
    return None
def _find_uv_binary():
    w = shutil.which("uv")
    if w:
        return w
    sub = ("bin", "uv.exe") if os.name == "nt" else ("bin", "uv")
    t_uv = Path(paths.tools_dir()) / "uv" / sub[0] / sub[1]
    if t_uv.is_file():
        return str(t_uv)
    if os.name == "nt":
        for cand in (
            Path.home() / ".local" / "bin" / "uv.exe",
            Path.home() / ".cargo" / "bin" / "uv.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "uv.exe",
        ):
            if cand.is_file():
                return str(cand)
    return None
def find_compatible_python(custom_python=None):
    raw_cands = []
    if custom_python:
        raw_cands.append(_validate_custom_python(custom_python))
    raw_cands.extend(_candidate_python_paths())
    if os.name == "nt":
        py_launcher = shutil.which("py")
        if py_launcher:
            for tag in ("-3.12", "-3.13", "-3.11"):
                try:
                    r = subprocess.run(
                        [py_launcher, tag, "-c", "import sys; print(sys.executable)"],
                        capture_output=True,
                        text=True,
                        timeout=4,
                        creationflags=_WIN_NO_WINDOW,
                    )
                    if r.returncode == 0 and r.stdout.strip():
                        raw_cands.append(r.stdout.strip().splitlines()[-1].strip())
                except Exception:
                    pass
        local_app = os.environ.get("LOCALAPPDATA", "")
        prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        for ver_tag in ("312", "313", "311"):
            if local_app:
                raw_cands.append(os.path.join(local_app, "Programs", "Python", f"Python{ver_tag}", "python.exe"))
                raw_cands.append(os.path.join(local_app, "Python", f"pythoncore-{ver_tag[:1]}.{ver_tag[1:]}-64", "python.exe"))
            raw_cands.append(os.path.join(f"C:\\Python{ver_tag}", "python.exe"))
            raw_cands.append(os.path.join(prog_files, f"Python{ver_tag}", "python.exe"))
        uv_py_root = Path(os.environ.get("APPDATA", "")) / "uv" / "python"
        if uv_py_root.is_dir():
            for p_exe in sorted(uv_py_root.glob("cpython-3.1[123]*/*/python.exe")):
                raw_cands.append(str(p_exe))
    else:
        for bin_name in ("python3.12", "python3.13", "python3.11", "python3"):
            w = shutil.which(bin_name)
            if w:
                raw_cands.append(w)
    uv_bin = _find_uv_binary()
    if uv_bin:
        try:
            r_uv = subprocess.run(
                [uv_bin, "python", "find", ">=3.11,<3.14"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=_WIN_NO_WINDOW,
            )
            if r_uv.returncode == 0 and r_uv.stdout.strip():
                raw_cands.append(r_uv.stdout.strip().splitlines()[-1].strip())
        except Exception:
            pass
    seen = set()
    compatible = []
    for cand in raw_cands:
        if not cand or not os.path.isfile(cand):
            continue
        norm = os.path.normcase(os.path.abspath(cand))
        if norm in seen:
            continue
        seen.add(norm)
        if norm == os.path.normcase(os.path.abspath(sys.executable)):
            ver = list(sys.version_info[:3])
            exe_real = sys.executable
        else:
            info = _probe_python_version_fast(cand)
            if not info:
                continue
            ver = info.get("ver") or []
            exe_real = info.get("exe") or cand
        if _py_compatible(ver):
            compatible.append({
                "python_executable": exe_real,
                "python_version": ".".join(str(x) for x in ver[:3]),
            })
    best = compatible[0] if compatible else None
    return {
        "found": best is not None,
        "python_executable": best["python_executable"] if best else None,
        "python_version": best["python_version"] if best else None,
        "uv_available": bool(uv_bin),
        "uv_executable": uv_bin,
        "candidates": compatible,
    }
def get_soup_install_status():
    with _INSTALL_LOCK:
        return json.loads(json.dumps(_INSTALL_STATE, ensure_ascii=False))
def _set_install_state(lifecycle_state=None, status=None, stage=None, line=None, pct=None, **extra):
    with _INSTALL_LOCK:
        if lifecycle_state is not None:
            _INSTALL_STATE["lifecycle_state"] = lifecycle_state
        if status is not None:
            _INSTALL_STATE["status"] = status
        if stage is not None:
            _INSTALL_STATE["stage"] = str(stage)
            _INSTALL_STATE["step"] = str(stage)
        if pct is not None:
            _INSTALL_STATE["progress_pct"] = int(pct)
        if line:
            _INSTALL_STATE["logs"].append(str(line))
            if len(_INSTALL_STATE["logs"]) > 300:
                _INSTALL_STATE["logs"] = _INSTALL_STATE["logs"][-300:]
        for k, v in extra.items():
            _INSTALL_STATE[k] = v
def verify_soup_environment(py_exe, soup_cmd=None, check_train_deps=True):
    checks = {
        "venv_present": False,
        "python_executable_ok": False,
        "python_compatible": False,
        "cli_executable_ok": False,
        "cli_version_ok": False,
        "module_imports_ok": False,
        "train_deps_ok": False,
    }
    venv_dir = Path(paths.soup_venv_dir())
    checks["venv_present"] = bool(venv_dir.is_dir() and ((venv_dir / "pyvenv.cfg").is_file() or (py_exe and os.path.isfile(py_exe))))
    if py_exe and os.path.isfile(py_exe):
        checks["python_executable_ok"] = True
        probe = _probe_python_env(py_exe, check_deps=True, timeout=15)
        if probe:
            checks["python_compatible"] = _py_compatible(probe.get("py"))
            present = probe.get("present") or {}
            checks["module_imports_ok"] = bool(present.get("soup_cli"))
            train_req = ("torch", "transformers", "peft", "trl", "datasets", "accelerate")
            checks["train_deps_ok"] = all(present.get(k) for k in train_req) if check_train_deps else True
    soup_bin_dir = os.path.dirname(py_exe) if py_exe else ""
    soup_bin = os.path.join(soup_bin_dir, "soup.exe" if os.name == "nt" else "soup")
    cmd = list(soup_cmd or ([] if not py_exe else [py_exe, "-m", "soup_cli"]))
    checks["cli_executable_ok"] = bool(os.path.isfile(soup_bin) or shutil.which("soup") or (cmd and os.path.isfile(cmd[0])))
    ver = None
    if cmd:
        try:
            vr = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=12, creationflags=_WIN_NO_WINDOW)
            txt = (vr.stdout or vr.stderr or "").strip()
            m = re.search(r"(\d+\.\d+\.\d+(?:[a-zA-Z0-9._-]*)?)", txt)
            if vr.returncode == 0 and m:
                checks["cli_version_ok"] = True
                ver = m.group(1)
        except Exception:
            pass
    all_core_ok = checks["python_executable_ok"] and checks["python_compatible"] and checks["cli_executable_ok"] and checks["cli_version_ok"] and checks["module_imports_ok"]
    return {
        "verified": bool(all_core_ok and (checks["train_deps_ok"] if check_train_deps else True)),
        "core_verified": bool(all_core_ok),
        "soup_version": ver,
        "checks": checks,
    }
def _bootstrap_uv(log_fn):
    tools_uv = Path(paths.tools_dir()) / "uv"
    tools_uv.mkdir(parents=True, exist_ok=True)
    log_fn("Bootstrapping isolated uv toolchain for Python 3.12 provisioning...")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-input", "--upgrade", "--target", str(tools_uv), "uv"],
        capture_output=True,
        text=True,
        timeout=180,
        creationflags=_WIN_NO_WINDOW,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Failed to bootstrap uv helper: {(r.stderr or r.stdout)[:240]}")
    for cand in (tools_uv / "bin" / "uv.exe", tools_uv / "bin" / "uv", tools_uv / "uv.exe", tools_uv / "uv"):
        if cand.is_file():
            return str(cand)
    uv_pkg_bin = tools_uv / "uv" / "__main__.py"
    if uv_pkg_bin.is_file():
        return str(tools_uv)
    return _find_uv_binary()
def _backup_venv_for_update(venv_dir):
    if not venv_dir.exists():
        return None
    bak = venv_dir.with_name(f"{venv_dir.name}.bak-{int(time.time())}")
    try:
        if bak.exists():
            shutil.rmtree(str(bak), ignore_errors=True)
        shutil.copytree(str(venv_dir), str(bak), symlinks=True)
        return bak
    except Exception:
        return None
def _restore_venv_backup(venv_dir, bak_dir):
    if not bak_dir or not bak_dir.exists():
        return False
    try:
        if venv_dir.exists():
            shutil.rmtree(str(venv_dir), ignore_errors=True)
        shutil.move(str(bak_dir), str(venv_dir))
        return True
    except Exception:
        return False
def _run_install_worker(action, custom_python=None, include_train=True):
    venv_dir = Path(paths.soup_venv_dir())
    sub = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    venv_py = venv_dir.joinpath(*sub)
    bak_dir = None
    try:
        _set_install_state(lifecycle_state="CHECKING_ENVIRONMENT", stage="Проверка текущего окружения SOUP", pct=8, line=f"Checking environment in {venv_dir}...")
        if action == "update" and venv_dir.is_dir():
            _set_install_state(lifecycle_state="UPDATING", stage="Создание резервной копии SOUP venv", pct=12, line="Backing up existing soup-venv before update...")
            bak_dir = _backup_venv_for_update(venv_dir)
        if action == "repair" and venv_dir.exists():
            _set_install_state(lifecycle_state="CHECKING_ENVIRONMENT", stage="Очистка повреждённого venv", pct=14, line=f"Removing {venv_dir} for clean repair...")
            shutil.rmtree(venv_dir, ignore_errors=True)
        _set_install_state(lifecycle_state="FINDING_PYTHON", stage="Поиск совместимого Python 3.11–3.13", pct=20, line="Searching for compatible Python (>=3.11,<3.14)...")
        compat = find_compatible_python(custom_python=custom_python)
        uv_bin = compat.get("uv_executable") or _find_uv_binary()
        if not venv_py.is_file():
            _set_install_state(lifecycle_state="CREATING_VENV", stage="Создание изолированного Python 3.12 venv", pct=30)
            if compat["found"]:
                base_py = compat["python_executable"]
                _set_install_state(line=f"Found compatible Python {compat['python_version']}: {base_py}")
                venv_dir.parent.mkdir(parents=True, exist_ok=True)
                r_venv = subprocess.run(
                    [base_py, "-m", "venv", str(venv_dir)],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    creationflags=_WIN_NO_WINDOW,
                )
                if r_venv.returncode != 0 or not venv_py.is_file():
                    raise RuntimeError(f"Failed to create soup-venv: {(r_venv.stderr or r_venv.stdout)[:300]}")
            else:
                if not uv_bin:
                    uv_bin = _bootstrap_uv(lambda m: _set_install_state(line=m))
                if not uv_bin:
                    raise RuntimeError("Compatible Python (>=3.11,<3.14) not found and automatic uv provisioning unavailable. Install Python 3.12 (e.g. 'winget install Python.Python.3.12') and retry.")
                _set_install_state(line=f"Provisioning isolated Python 3.12 soup-venv using uv ({uv_bin})...")
                venv_dir.parent.mkdir(parents=True, exist_ok=True)
                uv_cmd = [uv_bin, "venv", "--python", "3.12", "--seed", str(venv_dir)]
                if os.path.isdir(uv_bin):
                    uv_cmd = [sys.executable, "-c", f"import sys; sys.path.insert(0, {uv_bin!r}); from uv.__main__ import main; main()", "venv", "--python", "3.12", "--seed", str(venv_dir)]
                r_uv = subprocess.run(uv_cmd, capture_output=True, text=True, timeout=600, creationflags=_WIN_NO_WINDOW)
                for ln in (r_uv.stdout or "").splitlines() + (r_uv.stderr or "").splitlines():
                    if ln.strip():
                        _set_install_state(line=ln.strip())
                if r_uv.returncode != 0 or not venv_py.is_file():
                    raise RuntimeError(f"Failed to provision Python 3.12 venv: {(r_uv.stderr or r_uv.stdout)[:300]}")
        _set_install_state(venv_dir=str(venv_dir), python_executable=str(venv_py))
        pkg_spec = "soup-cli[train]" if include_train else "soup-cli"
        target_lifecycle = "UPDATING" if action == "update" else "INSTALLING"
        _set_install_state(lifecycle_state=target_lifecycle, stage=f"Установка {pkg_spec}", pct=55, line=f"Running pip install --upgrade {pkg_spec}...")
        subprocess.run([str(venv_py), "-m", "pip", "install", "--no-input", "--upgrade", "pip"], capture_output=True, text=True, timeout=180, creationflags=_WIN_NO_WINDOW)
        pip_cmd = [str(venv_py), "-m", "pip", "install", "--no-input", "--upgrade", pkg_spec]
        proc = subprocess.Popen(pip_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=_WIN_NO_WINDOW)
        for line in proc.stdout:
            s = line.strip()
            if s:
                _append_install_log(s)
        rc = proc.wait(timeout=900)
        if rc != 0:
            raise RuntimeError(f"pip install soup-cli[train] exited with code {rc}")
        cfg = settings._load()
        cfg["soup_python"] = str(venv_py)
        settings._save(cfg)
        _append_install_log("Verifying SOUP CLI and training dependencies...", step="Проверка SOUP", pct=90)
        det = detect_soup(probe_deps=True)
        if not det.get("available"):
            raise RuntimeError("soup-cli installation completed but verification failed.")
        with _INSTALL_LOCK:
            _INSTALL_STATE["status"] = "completed"
            _INSTALL_STATE["step"] = f"Готово (soup v{det.get('soup_version') or 'installed'})"
            _INSTALL_STATE["progress_pct"] = 100
            _INSTALL_STATE["finished_at"] = time.time()
    except Exception as exc:
        with _INSTALL_LOCK:
            _INSTALL_STATE["status"] = "failed"
            _INSTALL_STATE["error"] = str(exc)
            _INSTALL_STATE["step"] = "Ошибка установки"
            _INSTALL_STATE["finished_at"] = time.time()
        _append_install_log(f"ERROR: {exc}")
def install_or_repair_soup(action="install", custom_python=None, background=True):
    act = str(action or "install").strip().lower()
    if act not in ("install", "update", "repair"):
        raise ValueError("action must be install, update, or repair")
    with _INSTALL_LOCK:
        if _INSTALL_STATE.get("status") == "running":
            return get_soup_install_status()
        _INSTALL_STATE.update({
            "status": "running",
            "action": act,
            "progress_pct": 5,
            "step": "Инициализация установщика SOUP",
            "logs": [f"Starting SOUP {act}..."],
            "venv_dir": str(Path(paths.data_dir()) / "soup-venv"),
            "python_executable": None,
            "error": None,
            "started_at": time.time(),
            "finished_at": None,
        })
    if background:
        t = threading.Thread(target=_run_install_worker, args=(act, custom_python), daemon=True)
        t.start()
        return get_soup_install_status()
    _run_install_worker(act, custom_python)
    return get_soup_install_status()
def detect_soup(custom_cmd=None, probe_deps=True):
    cmd = []
    source = None
    py_exe = None
    probe = None
    env_cmd = custom_cmd or os.environ.get("SHARD_SOUP_CMD", "").strip() or settings._load().get("soup_command")
    if env_cmd:
        parts = _split_cmd(env_cmd)
        if parts:
            exe = parts[0]
            if os.path.isfile(exe) or shutil.which(exe):
                cmd = parts
                source = "configured"
    if not cmd:
        for cand_py in _candidate_python_paths():
            is_cur = os.path.normcase(os.path.abspath(cand_py)) == os.path.normcase(os.path.abspath(sys.executable))
            info = _in_process_probe(check_deps=probe_deps) if is_cur else _probe_python_env(cand_py, check_deps=probe_deps)
            if info and info.get("present", {}).get("soup_cli"):
                cmd = [cand_py, "-m", "soup_cli"]
                py_exe = cand_py
                probe = info
                source = "current_env" if is_cur else "venv"
                break
    if not cmd:
        soup_bin = shutil.which("soup")
        if soup_bin:
            cmd = [soup_bin]
            source = "path_bin"
    soup_version = probe.get("soup_version") if probe else None
    if cmd and not soup_version:
        try:
            ver_res = subprocess.run(
                cmd + ["--version"],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=_WIN_NO_WINDOW,
            )
            txt = (ver_res.stdout or ver_res.stderr or "").strip()
            m = re.search(r"(\d+\.\d+\.\d+(?:[a-zA-Z0-9._-]*)?)", txt)
            soup_version = m.group(1) if m else (txt[:48] if ver_res.returncode == 0 else None)
        except Exception:
            soup_version = None
    if probe is None:
        probe = _in_process_probe(check_deps=False)
        py_exe = sys.executable
    py_ver = probe.get("py") or list(sys.version_info[:3])
    py_str = ".".join(str(x) for x in py_ver[:3])
    py_ok = _py_compatible(py_ver)
    present = probe.get("present") or {}
    train_req = ("torch", "transformers", "peft", "trl", "datasets", "accelerate")
    missing_train = [k for k in train_req if not present.get(k)]
    hw = hardware.get_hardware_profile()
    hw_cuda = bool(hw.get("cuda_available")) or bool(probe.get("cuda"))
    warnings = []
    if not cmd:
        if not py_ok:
            warnings.append(
                f"Active Python is {py_str}; upstream soup-cli requires Python >=3.11,<3.14. Create a Python 3.12 venv and set SHARD_SOUP_PYTHON."
            )
        else:
            warnings.append("soup-cli is not installed in PATH or SHARD environments.")
    elif not present.get("soup_cli") and source == "configured":
        warnings.append("Using configured custom SOUP command; Python dependencies inside external target were not directly verified.")
    elif missing_train:
        warnings.append(f"soup-cli found, but training dependencies are missing: {', '.join(missing_train)}. Install with pip install \"soup-cli[train]\".")
    if cmd and not hw_cuda:
        warnings.append("No CUDA GPU detected; real LoRA/QLoRA fine-tuning is GPU-bound and may fail or be extremely slow on CPU-only runtimes.")
    available = bool(cmd)
    can_train = bool(available and (source in ("configured", "path_bin") or not missing_train))
    return {
        "available": available,
        "source": source,
        "command": cmd,
        "python_executable": py_exe,
        "python_version": py_str,
        "python_compatible": py_ok,
        "soup_version": soup_version,
        "dependencies": present,
        "missing_train_deps": missing_train,
        "cuda_available": hw_cuda,
        "gpu_name": hw.get("gpu_name"),
        "vram_total_mb": hw.get("vram_total_mb", 0),
        "capabilities": {
            "validate_data": available,
            "train": can_train,
            "eval": can_train,
            "merge": can_train,
            "export_gguf": available,
        },
        "warnings": warnings,
        "install_hint": "python3.12 -m venv ~/.soup-venv && pip install \"soup-cli[train]\"",
        "compatible_python": find_compatible_python(),
        "installer": get_soup_install_status(),
    }
def _yaml_scalar(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value)
    return json.dumps(s, ensure_ascii=False)
def dump_yaml(data, indent=0):
    lines = []
    prefix = "  " * indent
    if isinstance(data, dict):
        for k, v in data.items():
            key_str = str(k)
            if isinstance(v, dict):
                if v:
                    lines.append(f"{prefix}{key_str}:")
                    lines.append(dump_yaml(v, indent + 1))
                else:
                    lines.append(f"{prefix}{key_str}: {{}}")
            elif isinstance(v, list):
                if v:
                    lines.append(f"{prefix}{key_str}:")
                    lines.append(dump_yaml(v, indent + 1))
                else:
                    lines.append(f"{prefix}{key_str}: []")
            else:
                lines.append(f"{prefix}{key_str}: {_yaml_scalar(v)}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                first = True
                for ik, iv in item.items():
                    if first:
                        if isinstance(iv, (dict, list)):
                            lines.append(f"{prefix}- {ik}:")
                            lines.append(dump_yaml(iv, indent + 2))
                        else:
                            lines.append(f"{prefix}- {ik}: {_yaml_scalar(iv)}")
                        first = False
                    else:
                        if isinstance(iv, (dict, list)):
                            lines.append(f"{prefix}  {ik}:")
                            lines.append(dump_yaml(iv, indent + 2))
                        else:
                            lines.append(f"{prefix}  {ik}: {_yaml_scalar(iv)}")
            elif isinstance(item, list):
                lines.append(f"{prefix}-")
                lines.append(dump_yaml(item, indent + 1))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
    else:
        lines.append(f"{prefix}{_yaml_scalar(data)}")
    return "\n".join(lines)
def _sanitize_experiment_name(name):
    if name is None:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name).strip()).strip("-")
    return cleaned[:64] or None
def build_soup_config(base_model, dataset_path, output_dir, task="sft", fmt=None, experiment_name=None, params=None):
    errors = []
    params = dict(params or {})
    base = str(base_model or "").strip()
    if not base or "\x00" in base:
        errors.append("base_model must be a non-empty Hugging Face model ID or local path.")
    train_path = str(dataset_path or "").strip()
    if not train_path or "\x00" in train_path:
        errors.append("dataset_path must be non-empty.")
    out_dir = str(output_dir or "").strip()
    if not out_dir or "\x00" in out_dir:
        errors.append("output_dir must be non-empty.")
    task_norm = str(task or "sft").strip().lower()
    if task_norm not in SOUP_VALID_TASKS:
        errors.append(f"Unsupported task '{task_norm}'. Allowed: {', '.join(SOUP_VALID_TASKS)}.")
    default_fmt = "dpo" if task_norm == "dpo" else "chatml"
    fmt_norm = str(fmt or params.get("data_format") or default_fmt).strip().lower()
    if fmt_norm not in SOUP_VALID_FORMATS:
        errors.append(f"Unsupported data.format '{fmt_norm}'. Allowed: {', '.join(SOUP_VALID_FORMATS)}.")
    backend = str(params.get("backend") or "transformers").strip().lower()
    if backend not in SOUP_VALID_BACKENDS:
        errors.append(f"Unsupported backend '{backend}'. Allowed: {', '.join(SOUP_VALID_BACKENDS)}.")
    try:
        epochs = int(params.get("epochs", 3))
        if epochs < 1 or epochs > 50:
            errors.append("epochs must be between 1 and 50.")
    except (TypeError, ValueError):
        epochs = 3
        errors.append("epochs must be an integer.")
    try:
        lr = float(params.get("lr", 2e-5))
        if lr <= 0 or lr > 0.1:
            errors.append("lr must be > 0 and <= 0.1.")
    except (TypeError, ValueError):
        lr = 2e-5
        errors.append("lr must be a positive number.")
    batch_size = params.get("batch_size", "auto")
    if batch_size != "auto":
        try:
            batch_size = int(batch_size)
            if batch_size < 1:
                errors.append("batch_size must be 'auto' or >= 1.")
        except (TypeError, ValueError):
            errors.append("batch_size must be 'auto' or positive integer.")
    try:
        grad_accum = int(params.get("gradient_accumulation_steps", 4))
        if grad_accum < 1:
            errors.append("gradient_accumulation_steps must be >= 1.")
    except (TypeError, ValueError):
        grad_accum = 4
        errors.append("gradient_accumulation_steps must be an integer.")
    try:
        max_length = int(params.get("max_length", 2048))
        if max_length < 64 or max_length > 1048576:
            errors.append("max_length must be between 64 and 1048576.")
    except (TypeError, ValueError):
        max_length = 2048
        errors.append("max_length must be an integer.")
    try:
        val_split = float(params.get("val_split", 0.1))
        if val_split < 0.0 or val_split > 0.5:
            errors.append("val_split must be between 0.0 and 0.5.")
    except (TypeError, ValueError):
        val_split = 0.1
        errors.append("val_split must be a float.")
    try:
        lora_r = int(params.get("lora_r", 16))
        lora_alpha = int(params.get("lora_alpha", 32))
        lora_dropout = float(params.get("lora_dropout", 0.05))
        if lora_r < 0 or lora_alpha < 1 or lora_dropout < 0.0 or lora_dropout >= 1.0:
            errors.append("Invalid LoRA parameters (r >= 0, alpha >= 1, 0 <= dropout < 1).")
    except (TypeError, ValueError):
        lora_r, lora_alpha, lora_dropout = 16, 32, 0.05
        errors.append("LoRA parameters must be numeric.")
    quant = str(params.get("quantization") or "4bit").strip().lower()
    if quant not in SOUP_VALID_QUANTS:
        errors.append(f"quantization must be one of {', '.join(SOUP_VALID_QUANTS)}.")
    data_cfg = {
        "train": train_path,
        "format": fmt_norm,
        "val_split": val_split,
        "max_length": max_length,
    }
    if task_norm == "sft":
        data_cfg["train_on_responses_only"] = bool(params.get("train_on_responses_only", True))
    cfg = {
        "base": base,
        "task": task_norm,
        "modality": "text",
        "backend": backend,
        "data": data_cfg,
        "training": {
            "epochs": epochs,
            "lr": lr,
            "batch_size": batch_size,
            "gradient_accumulation_steps": grad_accum,
            "quantization": quant,
            "lora": {
                "r": lora_r,
                "alpha": lora_alpha,
                "dropout": lora_dropout,
            },
        },
        "output": out_dir,
    }
    exp = _sanitize_experiment_name(experiment_name)
    if exp:
        cfg["experiment_name"] = exp
    return cfg, errors
def write_soup_config(cfg_dict, config_path):
    target = Path(config_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    yaml_text = dump_yaml(cfg_dict) + "\n"
    storage.atomic_write_text(target, yaml_text, fsync=True, backup=False)
    return str(target)
def _run_soup_cmd(soup_info, args, timeout=120, env_extra=None):
    cmd_base = list((soup_info or {}).get("command") or [])
    if not cmd_base:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": "soup-cli command not available"}
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if env_extra:
        env.update({str(k): str(v) for k, v in env_extra.items()})
    try:
        res = subprocess.run(
            cmd_base + list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            creationflags=_WIN_NO_WINDOW,
        )
        return {
            "ok": res.returncode == 0,
            "returncode": res.returncode,
            "stdout": res.stdout or "",
            "stderr": res.stderr or "",
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "returncode": -2,
            "stdout": (e.stdout or "") if isinstance(e.stdout, str) else "",
            "stderr": f"Command timed out after {timeout}s",
        }
    except Exception as e:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": str(e)}
def validate_dataset(soup_info, dataset_path, fmt="auto", min_valid_fraction=0.8):
    return _run_soup_cmd(
        soup_info,
        ["data", "validate", str(dataset_path), "--format", str(fmt), "--min-valid-fraction", str(min_valid_fraction)],
        timeout=90,
    )
def clean_dataset(soup_info, input_path, output_path):
    return _run_soup_cmd(
        soup_info,
        ["data", "clean", str(input_path), "-o", str(output_path)],
        timeout=120,
    )
def dry_run_train(soup_info, config_path, timeout=90):
    return _run_soup_cmd(
        soup_info,
        ["train", "--config", str(config_path), "--dry-run", "--yes"],
        timeout=timeout,
    )
def launch_train_popen(soup_info, config_path, log_path, resume=None, run_name=None, env_extra=None):
    cmd_base = list((soup_info or {}).get("command") or [])
    if not cmd_base:
        raise RuntimeError("soup-cli is not installed or configured.")
    args = ["train", "--config", str(config_path), "--yes"]
    if run_name:
        safe_name = _sanitize_experiment_name(run_name)
        if safe_name:
            args.extend(["--name", safe_name])
    if resume:
        args.extend(["--resume", str(resume)])
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "a", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update({str(k): str(v) for k, v in env_extra.items()})
    proc = subprocess.Popen(
        cmd_base + args,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        creationflags=_WIN_NO_WINDOW,
    )
    return proc, log_fh
def terminate_process(proc):
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    except Exception:
        pass
def parse_train_progress(log_path):
    if not log_path or not os.path.isfile(log_path):
        return {"lines": [], "last_loss": None, "last_epoch": None, "current_step": None, "total_steps": None}
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except Exception:
        return {"lines": [], "last_loss": None, "last_epoch": None, "current_step": None, "total_steps": None}
    last_loss = None
    last_epoch = None
    current_step = None
    total_steps = None
    for line in lines[-200:]:
        m_loss = re.search(r"['\"]?loss['\"]?\s*:\s*([0-9]+(?:\.[0-9]+)?)", line)
        if m_loss:
            last_loss = float(m_loss.group(1))
        m_ep = re.search(r"['\"]?epoch['\"]?\s*:\s*([0-9]+(?:\.[0-9]+)?)", line)
        if m_ep:
            last_epoch = float(m_ep.group(1))
        m_step = re.search(r"\b(\d+)\s*/\s*(\d+)\b", line)
        if m_step and ("%" in line or "it/s" in line or "s/it" in line or "step" in line.lower()):
            current_step = int(m_step.group(1))
            total_steps = int(m_step.group(2))
    return {
        "lines": lines[-120:],
        "last_loss": last_loss,
        "last_epoch": last_epoch,
        "current_step": current_step,
        "total_steps": total_steps,
    }
def scan_output_artifacts(output_dir):
    out = Path(output_dir)
    if not out.exists():
        return {"checkpoints": [], "adapter_dir": None, "adapter_files": [], "merged_dir": None, "gguf_files": []}
    checkpoints = []
    gguf_files = []
    adapter_dir = None
    adapter_files = []
    merged_dir = None
    for child in sorted(out.iterdir()):
        if child.is_dir() and child.name.startswith("checkpoint-"):
            checkpoints.append(str(child))
        elif child.is_file() and child.suffix.lower() == ".gguf":
            gguf_files.append(str(child))
    if (out / "adapter_config.json").is_file():
        adapter_dir = str(out)
        adapter_files = sorted(str(p) for p in out.iterdir() if p.is_file())
    if (out / "config.json").is_file() and any(out.glob("*.safetensors")) and not adapter_dir:
        merged_dir = str(out)
    elif (out / "merged" / "config.json").is_file():
        merged_dir = str(out / "merged")
    for p in sorted(out.glob("**/*.gguf")):
        sp = str(p)
        if sp not in gguf_files:
            gguf_files.append(sp)
    return {
        "checkpoints": checkpoints,
        "adapter_dir": adapter_dir,
        "adapter_files": adapter_files,
        "merged_dir": merged_dir,
        "gguf_files": gguf_files,
    }
def run_custom_eval(soup_info, model_path, tasks_jsonl, output_json_path, timeout=600):
    Path(output_json_path).parent.mkdir(parents=True, exist_ok=True)
    res = _run_soup_cmd(
        soup_info,
        ["eval", "custom", "--tasks", str(tasks_jsonl), "--model", str(model_path), "--output", str(output_json_path)],
        timeout=timeout,
    )
    metrics = None
    if os.path.isfile(output_json_path):
        try:
            with open(output_json_path, "r", encoding="utf-8") as f:
                metrics = json.load(f)
        except Exception:
            metrics = None
    res["metrics"] = metrics
    return res
def run_merge(soup_info, adapter_path, output_dir, base_model=None, dtype="float16", low_memory=True, timeout=600):
    args = ["merge", "--adapter", str(adapter_path), "--output", str(output_dir), "--dtype", str(dtype)]
    if base_model:
        args.extend(["--base", str(base_model)])
    if low_memory:
        args.append("--low-memory")
    res = _run_soup_cmd(soup_info, args, timeout=timeout)
    res["output_dir"] = str(output_dir) if os.path.isdir(output_dir) else None
    return res
def run_export_gguf(soup_info, model_path, output_file, quant="q4_k_m", base_model=None, timeout=900):
    q = str(quant or "q4_k_m").strip().lower()
    if q not in SOUP_VALID_GGUF_QUANTS:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": f"Unsupported GGUF quant '{q}'", "output_file": None}
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    args = ["export", "--model", str(model_path), "--format", "gguf", "--quant", q, "-o", str(output_file)]
    if base_model:
        args.extend(["--base", str(base_model)])
    res = _run_soup_cmd(soup_info, args, timeout=timeout)
    res["output_file"] = str(output_file) if os.path.isfile(output_file) else None
    return res
