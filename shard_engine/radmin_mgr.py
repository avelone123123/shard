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
        "adapter_name": None,
        "adapter_status": None,
        "ipv4": None,
        "ipv6": None,
        "ready": False,
        "last_error": pref.get("last_error"),
        "consent": pref.get("consent"),
        "declined": bool(pref.get("declined")),
    }
    if not _IS_WIN:
        return out
    candidates = [
        r"C:\Program Files (x86)\Radmin VPN\RvControlSvc.exe",
        r"C:\Program Files\Radmin VPN\RvControlSvc.exe",
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Radmin VPN", "RvControlSvc.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Radmin VPN", "RvControlSvc.exe"),
    ]
    svc_exe = None
    for c in candidates:
        if os.path.isfile(c):
            svc_exe = c
            break
    if svc_exe:
        out["installed"] = True
        out["path"] = os.path.dirname(svc_exe)
    ps_cmd = (
        "$res = @{ installed=$false; version=''; svc_installed=$false; svc_state='NotInstalled'; "
        "proc_svc=$false; proc_gui=$false; adapter=$false; adapter_name=''; adapter_status=''; "
        "ipv4=''; ipv6='' }; "
        "$svc = Get-Service -Name 'RvControlSvc' -ErrorAction SilentlyContinue; "
        "if ($svc) { $res.svc_installed = $true; $res.svc_state = $svc.Status.ToString() }; "
        "if (Get-Process -Name 'RvControlSvc' -ErrorAction SilentlyContinue) { $res.proc_svc = $true }; "
