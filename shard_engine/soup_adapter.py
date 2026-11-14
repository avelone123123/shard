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
from . import paths
from . import settings
from . import storage
SOUP_VALID_TASKS = ("sft", "dpo", "orpo", "kto", "simpo", "ipo", "grpo")
SOUP_VALID_FORMATS = ("chatml", "dpo", "alpaca", "sharegpt", "kto", "plaintext", "auto")
SOUP_VALID_BACKENDS = ("transformers", "unsloth", "mlx")
SOUP_VALID_QUANTS = ("4bit", "8bit", "none")
SOUP_VALID_GGUF_QUANTS = ("q4_0", "q4_k_m", "q5_k_m", "q8_0", "f16", "f32")
_WIN_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_INSTALL_LOCK = threading.RLock()
_INSTALL_STATE = {
    "status": "idle",
    "action": None,
    "progress_pct": 0,
    "step": "",
    "logs": [],
    "venv_dir": None,
    "python_executable": None,
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
        Path(paths.data_dir()) / "soup-venv",
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
def find_compatible_python(custom_python=None):
    raw_cands = []
    if custom_python:
        raw_cands.append(str(custom_python).strip())
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
            raw_cands.append(os.path.join(f"C:\\Python{ver_tag}", "python.exe"))
            raw_cands.append(os.path.join(prog_files, f"Python{ver_tag}", "python.exe"))
    else:
        for bin_name in ("python3.12", "python3.13", "python3.11", "python3"):
            w = shutil.which(bin_name)
            if w:
                raw_cands.append(w)
    uv_bin = shutil.which("uv")
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
        "candidates": compatible,
    }
def get_soup_install_status():
    with _INSTALL_LOCK:
        return json.loads(json.dumps(_INSTALL_STATE, ensure_ascii=False))
def _append_install_log(line, step=None, pct=None):
    with _INSTALL_LOCK:
        if line:
            _INSTALL_STATE["logs"].append(str(line))
            if len(_INSTALL_STATE["logs"]) > 200:
                _INSTALL_STATE["logs"] = _INSTALL_STATE["logs"][-200:]
        if step is not None:
            _INSTALL_STATE["step"] = str(step)
        if pct is not None:
            _INSTALL_STATE["progress_pct"] = int(pct)
def _run_install_worker(action, custom_python=None):
    venv_dir = Path(paths.data_dir()) / "soup-venv"
    sub = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    venv_py = venv_dir.joinpath(*sub)
    try:
        if action == "repair" and venv_dir.exists():
            _append_install_log(f"Removing existing venv at {venv_dir} for clean repair...", step="Очистка окружения", pct=10)
            shutil.rmtree(venv_dir, ignore_errors=True)
        _append_install_log("Searching for compatible Python (3.11 - 3.13)...", step="Поиск Python 3.11–3.13", pct=15)
        compat = find_compatible_python(custom_python=custom_python)
        uv_bin = shutil.which("uv")
        if not venv_py.is_file():
            if compat["found"]:
                base_py = compat["python_executable"]
                _append_install_log(f"Found compatible Python {compat['python_version']}: {base_py}", step="Создание soup-venv", pct=25)
                venv_dir.parent.mkdir(parents=True, exist_ok=True)
                r_venv = subprocess.run(
                    [base_py, "-m", "venv", str(venv_dir)],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    creationflags=_WIN_NO_WINDOW,
                )
                if r_venv.returncode != 0 or not venv_py.is_file():
                    raise RuntimeError(f"Failed to create venv: {(r_venv.stderr or r_venv.stdout)[:300]}")
            elif uv_bin:
                _append_install_log("Using uv to provision Python 3.12 venv...", step="Создание Python 3.12 venv (uv)", pct=25)
                venv_dir.parent.mkdir(parents=True, exist_ok=True)
                r_uv = subprocess.run(
                    [uv_bin, "venv", "--python", "3.12", str(venv_dir)],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    creationflags=_WIN_NO_WINDOW,
                )
                if r_uv.returncode != 0 or not venv_py.is_file():
                    raise RuntimeError(f"uv venv failed: {(r_uv.stderr or r_uv.stdout)[:300]}")
            else:
                raise RuntimeError(
                    "Compatible Python (>=3.11,<3.14) not found on this machine. Install Python 3.12 (e.g. 'winget install Python.Python.3.12' on Windows or 'uv python install 3.12') and retry."
                )
        with _INSTALL_LOCK:
            _INSTALL_STATE["venv_dir"] = str(venv_dir)
            _INSTALL_STATE["python_executable"] = str(venv_py)
        _append_install_log(f"Upgrading pip in {venv_py}...", step="Обновление pip", pct=45)
        subprocess.run(
            [str(venv_py), "-m", "pip", "install", "--upgrade", "pip"],
            capture_output=True,
            text=True,
            timeout=180,
            creationflags=_WIN_NO_WINDOW,
        )
        _append_install_log("Installing soup-cli[train] (this may take several minutes)...", step="Установка soup-cli[train]", pct=65)
        pip_cmd = [str(venv_py), "-m", "pip", "install", "--upgrade", "soup-cli[train]"]
        proc = subprocess.Popen(
            pip_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=_WIN_NO_WINDOW,
        )
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
