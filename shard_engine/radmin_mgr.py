import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from . import net_identity, paths, storage
_IS_WIN = sys.platform == "win32"
OFFICIAL_RADMIN_URL = "https://download.radmin-vpn.com/download/files/Radmin_VPN_2.1.4952.2.exe"
OFFICIAL_RADMIN_VERSION = "2.1.4952.2"
RADMIN_CONFIG_FILE = "radmin_state.json"
def _radmin_config_path() -> str:
    return os.path.join(paths.config_dir(), RADMIN_CONFIG_FILE)
def _load_radmin_pref() -> dict:
    return storage.read_json_safe(_radmin_config_path(), default={"consent": None, "declined": False, "installed_at": None, "last_error": None}, expected_type=dict)
def _save_radmin_pref(data: dict) -> None:
    storage.atomic_write_json(_radmin_config_path(), data, indent=2, fsync=True, restrict=True)
def is_windows() -> bool:
    return _IS_WIN
def get_radmin_status() -> dict:
    pref = _load_radmin_pref()
    out = {
        "supported": _IS_WIN,
        "installed": False,
        "path": None,
        "version": None,
        "service_installed": False,
        "service_state": "NotInstalled",
        "service_running": False,
        "processes": {"RvControlSvc.exe": False, "RvRvpnGui.exe": False},
        "adapter_detected": False,
