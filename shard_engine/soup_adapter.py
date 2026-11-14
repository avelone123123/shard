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
