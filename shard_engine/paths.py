import importlib.resources
import os
import shutil
import sys
from pathlib import Path
APP_DIR_WIN_MAC = "SHARD"
APP_DIR_POSIX = "shard"
def _env_path(key):
    val = os.environ.get(key)
    return Path(os.path.expandvars(os.path.expanduser(val))).resolve() if val and val.strip() else None
def _legacy_dir():
    return Path.home() / f".{APP_DIR_POSIX}"
def _is_win():
    return os.name == "nt" or sys.platform == "win32"
def _is_mac():
    return sys.platform == "darwin"
def data_dir():
    env = _env_path("SHARD_DATA_DIR")
    if env is not None:
        return env
    if _is_win():
        base = _env_path("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return base / APP_DIR_WIN_MAC
    if _is_mac():
        return Path.home() / "Library" / "Application Support" / APP_DIR_WIN_MAC
    xdg = _env_path("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return xdg / APP_DIR_POSIX
def config_dir():
    env = _env_path("SHARD_DATA_DIR")
    if env is not None:
        return env
    if _is_win():
        return data_dir() / "config"
    if _is_mac():
        return data_dir() / "config"
    xdg = _env_path("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return xdg / APP_DIR_POSIX
def cache_dir():
    env = _env_path("SHARD_DATA_DIR")
    if env is not None:
        return env / "cache"
    if _is_win():
        return data_dir() / "cache"
    if _is_mac():
        return Path.home() / "Library" / "Caches" / APP_DIR_WIN_MAC
    xdg = _env_path("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return xdg / APP_DIR_POSIX
def logs_dir():
    env = _env_path("SHARD_DATA_DIR")
    if env is not None:
        return env / "logs"
    if _is_win():
        return data_dir() / "logs"
    if _is_mac():
        return Path.home() / "Library" / "Logs" / APP_DIR_WIN_MAC
    xdg = _env_path("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return xdg / APP_DIR_POSIX / "logs"
def run_dir():
    env = _env_path("SHARD_DATA_DIR")
    if env is not None:
        return env / "run"
    if _is_win() or _is_mac():
        return data_dir() / "run"
    xdg_rt = _env_path("XDG_RUNTIME_DIR")
    if xdg_rt is not None and xdg_rt.is_dir() and os.access(str(xdg_rt), os.W_OK):
        return xdg_rt / APP_DIR_POSIX
    xdg = _env_path("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return xdg / APP_DIR_POSIX / "run"
def runtime_dir():
    return str(data_dir() / "runtime")
def default_models_dir():
    if os.environ.get("SHARD_DATA_DIR"):
        return str(data_dir() / "models")
    env_m = _env_path("SHARD_MODELS_DIR")
    if env_m is not None:
        return str(env_m)
    if Path("/data/models").exists():
        return "/data/models"
    primary = data_dir() / "models"
    legacy = _legacy_dir() / "models"
    if not (primary / "hub").exists() and (legacy / "hub").exists():
        return str(legacy)
    return str(primary)
def ensure_dir(p):
    path = Path(p)
    path.mkdir(parents=True, exist_ok=True)
    return str(path)
def resolve_user_file(name, kind="config"):
    base = config_dir() if kind == "config" else (run_dir() if kind == "run" else data_dir())
    target = base / name
    if not target.exists() and not os.environ.get("SHARD_DATA_DIR"):
        legacy = _legacy_dir() / name
        if legacy.is_file():
            try:
                base.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(legacy), str(target))
            except Exception:
                return str(legacy)
    return str(target)
def settings_file():
    return resolve_user_file("settings.json", kind="config")
def keys_file():
    return resolve_user_file("keys_db.json", kind="config")
def active_model_file():
    return resolve_user_file("active_model.json", kind="data")
def lan_file():
    return resolve_user_file("lan.json", kind="data")
def pid_file():
    return str(run_dir() / "shard.pid.json")
def log_file():
    return str(logs_dir() / "shard.log")
def all_dirs():
    return {
        "data_dir": str(data_dir()),
        "config_dir": str(config_dir()),
        "cache_dir": str(cache_dir()),
        "logs_dir": str(logs_dir()),
        "run_dir": str(run_dir()),
        "runtime_dir": runtime_dir(),
        "models_default_dir": default_models_dir(),
    }
def read_package_text(filename):
    try:
        ref = importlib.resources.files("shard_engine").joinpath(filename)
        return ref.read_text(encoding="utf-8")
    except Exception:
        fallback = Path(__file__).resolve().parent / filename
        return fallback.read_text(encoding="utf-8")
def package_resource_exists(filename):
    try:
        ref = importlib.resources.files("shard_engine").joinpath(filename)
        return ref.is_file()
    except Exception:
        return (Path(__file__).resolve().parent / filename).is_file()

