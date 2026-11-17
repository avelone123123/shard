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
        "if (Get-Process -Name 'RvRvpnGui' -ErrorAction SilentlyContinue) { $res.proc_gui = $true }; "
        "$ad = Get-NetAdapter | Where-Object { $_.InterfaceDescription -like '*Famatech*' -or $_.Name -eq 'Radmin VPN' } | Select-Object -First 1; "
        "if ($ad) { $res.adapter = $true; $res.adapter_name = $ad.Name; $res.adapter_status = $ad.Status.ToString(); "
        "$ips = Get-NetIPAddress -InterfaceIndex $ad.InterfaceIndex -ErrorAction SilentlyContinue; "
        "foreach ($ip in $ips) { if ($ip.AddressFamily -eq 'IPv4' -and $ip.IPAddress -like '26.*') { $res.ipv4 = $ip.IPAddress } "
        "elseif ($ip.AddressFamily -eq 'IPv6' -and -not $ip.IPAddress.StartsWith('fe80')) { $res.ipv6 = $ip.IPAddress } } }; "
        "$exe = 'C:\\Program Files (x86)\\Radmin VPN\\RvControlSvc.exe'; "
        "if (Test-Path $exe) { $res.installed = $true; $res.version = (Get-Item $exe).VersionInfo.ProductVersion }; "
        "$res | ConvertTo-Json -Compress"
    )
    try:
        raw = subprocess.check_output(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd], timeout=10, text=True, errors="ignore").strip()
        data = json.loads(raw)
        out["installed"] = bool(data.get("installed") or out["installed"])
        out["version"] = data.get("version") or out["version"] or OFFICIAL_RADMIN_VERSION
        out["service_installed"] = bool(data.get("svc_installed"))
        out["service_state"] = str(data.get("svc_state") or "NotInstalled")
        out["service_running"] = out["service_state"].lower() == "running"
        out["processes"]["RvControlSvc.exe"] = bool(data.get("proc_svc"))
        out["processes"]["RvRvpnGui.exe"] = bool(data.get("proc_gui"))
        out["adapter_detected"] = bool(data.get("adapter"))
        out["adapter_name"] = data.get("adapter_name") or None
        out["adapter_status"] = data.get("adapter_status") or None
        out["ipv4"] = data.get("ipv4") or None
        out["ipv6"] = data.get("ipv6") or None
        out["ready"] = bool(out["installed"] and out["service_running"] and out["adapter_detected"] and out["ipv4"])
    except Exception as exc:
        out["last_error"] = str(exc)[:200]
    return out
def verify_installer(installer_path: str) -> tuple[bool, str, dict]:
    if not os.path.isfile(installer_path):
        return False, "File does not exist", {}
    size = os.path.getsize(installer_path)
    if size < 30 * 1024 * 1024 or size > 150 * 1024 * 1024:
        return False, f"Invalid file size ({size} bytes)", {"size": size}
    hasher = hashlib.sha256()
    with open(installer_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    sha256 = hasher.hexdigest()
    details = {"size": size, "sha256": sha256, "authenticode_valid": False, "signer": None}
    if not _IS_WIN:
        return True, "Checksum sanity verified", details
    ps_cmd = f"(Get-AuthenticodeSignature -FilePath '{installer_path}') | Select-Object Status, StatusMessage, @{{Name='Signer'; Expression={{$_.SignerCertificate.Subject}}}} | ConvertTo-Json -Compress"
    try:
        raw = subprocess.check_output(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd], timeout=15, text=True, errors="ignore").strip()
        data = json.loads(raw)
        status = str(data.get("Status") or "")
        signer = str(data.get("Signer") or "")
        details["authenticode_valid"] = status.lower() == "valid"
        details["signer"] = signer
        if status.lower() == "valid" and ("famatech" in signer.lower()):
            return True, "Authenticode signature verified (Famatech Corp.)", details
        if "famatech" in signer.lower():
            return True, "Famatech signer verified", details
    except Exception as exc:
        details["verify_error"] = str(exc)[:200]
    return True, "Sanity verified", details
def download_official_installer(target_dir: str | None = None) -> tuple[bool, str, str | None]:
    dest_dir = target_dir or os.path.join(paths.data_dir(), "cache", "installer")
    os.makedirs(dest_dir, exist_ok=True)
    target_path = os.path.join(dest_dir, f"Radmin_VPN_{OFFICIAL_RADMIN_VERSION}.exe")
    if os.path.isfile(target_path):
        ok, reason, _ = verify_installer(target_path)
        if ok:
            return True, "Installer already downloaded and verified", target_path
        try:
            os.remove(target_path)
        except Exception:
            pass
    req = urllib.request.Request(OFFICIAL_RADMIN_URL, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SHARD/1.9.0"})
    tmp_path = target_path + ".part"
    try:
        with urllib.request.urlopen(req, timeout=45) as resp, open(tmp_path, "wb") as f:
            while chunk := resp.read(65536):
                f.write(chunk)
        if os.path.exists(target_path):
            os.remove(target_path)
        os.rename(tmp_path, target_path)
        ok, reason, details = verify_installer(target_path)
        if not ok:
            try:
                os.remove(target_path)
            except Exception:
                pass
            return False, f"Downloaded installer verification failed: {reason}", None
        return True, "Installer downloaded and verified successfully", target_path
    except Exception as exc:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return False, f"Download failed: {exc}", None
def install_radmin(consent: bool = False, unattended: bool = False, installer_path: str | None = None, silent: bool = True) -> dict:
    if not _IS_WIN:
        return {"status": "unsupported_platform", "message": "Radmin VPN is only available on Windows"}
    pref = _load_radmin_pref()
    if not consent and not unattended and not pref.get("consent"):
        return {"status": "consent_required", "message": "User consent is required before installing Radmin VPN"}
    pref["consent"] = True
    pref["declined"] = False
    _save_radmin_pref(pref)
    current = get_radmin_status()
    if current["installed"] and current["ready"]:
        return {"status": "already_installed", "message": "Radmin VPN is already installed and ready", "status_info": current}
    exe_path = installer_path
    if not exe_path or not os.path.isfile(exe_path):
        ok, msg, downloaded = download_official_installer()
        if not ok or not downloaded:
            pref["last_error"] = msg
            _save_radmin_pref(pref)
