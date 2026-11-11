"""Persistent user settings for shard (JSON file in the shard data dir).

Currently stores the custom models directory so downloads can be pointed at
an external SSD instead of the default ~/.shard/models. The file lives in
SHARD_DATA_DIR (or ~/.shard) next to active_model.json.
"""
import json
import os
import shutil

_DATA_DIR = os.environ.get("SHARD_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".shard")
_SETTINGS_FILE = os.path.join(_DATA_DIR, "settings.json")
DEFAULT_MODELS_DIR = os.path.join(os.path.expanduser("~"), ".shard", "models")


def _load():
    try:
        with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def models_dir():
    """Effective models directory: explicit UI setting wins, then the
    SHARD_MODELS_DIR / /data/models fallback, then the default. Never raises."""
    custom = (_load().get("models_dir") or "").strip()
    if custom:
        return custom
    env_dir = os.environ.get("SHARD_MODELS_DIR")
    if env_dir:
        return env_dir
    if os.path.exists("/data/models"):
        return "/data/models"
    return DEFAULT_MODELS_DIR


def is_custom_models_dir():
    return bool((_load().get("models_dir") or "").strip())


def reset_models_dir():
    """Drop the custom dir and return to the default."""
    s = _load()
    s.pop("models_dir", None)
    _save(s)
    return models_dir()


def set_models_dir(path):
    """Validate and persist a custom models directory. Returns the normalized path."""
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("Путь не указан")
    drive, tail = os.path.splitdrive(raw)
    if drive and not tail.strip(os.sep + "/"):
        raise ValueError("Укажите папку, а не только букву диска (например, E:\\models)")
    path = os.path.abspath(os.path.expandvars(os.path.expanduser(raw)))
    os.makedirs(path, exist_ok=True)
    probe = os.path.join(path, ".shard_write_test")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
    except Exception as e:
        raise ValueError(f"Папка недоступна для записи: {e}")
    s = _load()
    s["models_dir"] = path
    _save(s)
    return path


def disk_info(path=None):
    """{'total_gb', 'free_gb', 'used_gb'} for a path, or None on failure."""
    try:
        total, used, free = shutil.disk_usage(path or models_dir())
        return {
            "total_gb": round(total / (1024 ** 3), 1),
            "free_gb": round(free / (1024 ** 3), 1),
            "used_gb": round(used / (1024 ** 3), 1),
        }
    except Exception:
        return None


def available_drives():
    """Mounted volumes for the folder picker: [{'path','free_gb','total_gb'}]."""
    out = []
    try:
        import psutil
        for part in psutil.disk_partitions(all=False):
            try:
                usage = shutil.disk_usage(part.mountpoint)
                out.append({
                    "path": part.mountpoint,
                    "free_gb": round(usage.free / (1024 ** 3), 1),
                    "total_gb": round(usage.total / (1024 ** 3), 1),
                })
            except Exception:
                continue
    except Exception:
        pass
    if not out and os.name == "nt":
        import string
        for letter in string.ascii_uppercase:
            drive = letter + ":\\" if os.name == "nt" else letter + ":"
            if os.path.exists(drive):
                info = disk_info(drive)
                if info:
                    out.append({"path": drive, "free_gb": info["free_gb"], "total_gb": info["total_gb"]})
    out.sort(key=lambda d: d["path"])
    return out
