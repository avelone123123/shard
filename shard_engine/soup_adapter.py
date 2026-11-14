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
