import os
import shutil
from . import paths
from . import storage
def __getattr__(name):
    if name == "DEFAULT_MODELS_DIR":
        return paths.default_models_dir()
    if name == "_SETTINGS_FILE":
        return paths.settings_file()
    if name == "_DATA_DIR":
        return str(paths.config_dir())
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
def _load():
    data = storage.read_json_safe(
        paths.settings_file(),
        default={},
        expected_type=dict,
        quarantine=True,
        use_backup=True,
    )
    return data if isinstance(data, dict) else {}
def _save(data):
    storage.atomic_write_json(
        paths.settings_file(),
        data if isinstance(data, dict) else {},
        fsync=True,
        backup=True,
    )
def models_dir():
    custom = (_load().get("models_dir") or "").strip()
    if custom:
        try:
            os.makedirs(custom, exist_ok=True)
            return custom
        except Exception:
            pass
    return paths.default_models_dir()
def is_custom_models_dir():
    return bool((_load().get("models_dir") or "").strip())
def reset_models_dir():
    s = _load()
    s.pop("models_dir", None)
    _save(s)
    return models_dir()
def set_models_dir(path):
    raw = str(path or "").strip()
    if not raw or "\x00" in raw:
        raise ValueError("Путь не указан")
    if raw.startswith("\\\\") or raw.startswith("//"):
        raise ValueError("Сетевые UNC-пути запрещены")
    drive, tail = os.path.splitdrive(raw)
    if drive and not tail.strip(os.sep + "/"):
        raise ValueError("Укажите папку, а не только букву диска (например, E:\\models)")
    resolved = os.path.abspath(os.path.expanduser(raw))
    if resolved.startswith("\\\\"):
        raise ValueError("Сетевые UNC-пути запрещены")
    os.makedirs(resolved, exist_ok=True)
    probe = os.path.join(resolved, ".shard_write_test")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
    except Exception as e:
        raise ValueError(f"Папка недоступна для записи ({type(e).__name__})")
    s = _load()
    s["models_dir"] = resolved
    _save(s)
    return resolved
def disk_info(path=None):
    try:
        target = path or models_dir()
        while target and not os.path.exists(target):
            parent = os.path.dirname(target)
            if parent == target:
                break
            target = parent
        if not target or not os.path.exists(target):
            target = os.path.expanduser("~")
        total, used, free = shutil.disk_usage(target)
        return {
            "total_gb": round(total / (1024 ** 3), 1),
            "free_gb": round(free / (1024 ** 3), 1),
            "used_gb": round(used / (1024 ** 3), 1),
        }
    except Exception:
        return None
def available_drives():
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
            drive = letter + ":\\"
            if os.path.exists(drive):
                info = disk_info(drive)
                if info:
                    out.append({"path": drive, "free_gb": info["free_gb"], "total_gb": info["total_gb"]})
    if not out and os.name != "nt":
        info = disk_info("/")
        if info:
            out.append({"path": "/", "free_gb": info["free_gb"], "total_gb": info["total_gb"]})
    out.sort(key=lambda d: d["path"])
    return out
