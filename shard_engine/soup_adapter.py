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
