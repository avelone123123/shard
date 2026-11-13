import base64
import ctypes
import ipaddress
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from . import paths
from . import storage
_WIN = os.name == "nt" or sys.platform == "win32"
_CREATE_NO_WINDOW = 0x08000000 if _WIN else 0
_PROFILE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,15}$")
_HOST_RE = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$")
_PERM_ERROR_HINTS = (
    "operation not permitted",
    "permission denied",
    "access is denied",
    "must be root",
    "requires elevation",
    "not authorized",
)
_NO_IFACE_HINTS = (
    "no such device",
    "does not exist",
    "cannot find",
    "not found",
)
_HANDSHAKE_STALE_S = 180
def validate_profile_name(profile_name):
    if not isinstance(profile_name, str):
        raise ValueError("Invalid WireGuard profile name: expected string")
    if "\x00" in profile_name or "/" in profile_name or "\\" in profile_name or ".." in profile_name:
        raise ValueError("Invalid WireGuard profile name: unsafe path characters")
    if not _PROFILE_NAME_RE.fullmatch(profile_name):
        raise ValueError("Invalid WireGuard profile name: must match ^[a-zA-Z0-9_-]{1,15}$")
    return profile_name
def is_shard_tunnel_name(name):
    low = str(name or "").strip().lower()
    return low.startswith("shard") or low.startswith("wg-shard") or low.startswith("wireguardtunnel$shard")
def _run_readonly_cmd(args, timeout=3.0):
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
        stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
        return proc.returncode, stdout, stderr, None
    except FileNotFoundError as exc:
        return -1, "", "", str(exc)
    except PermissionError as exc:
        return -1, "", str(exc), str(exc)
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out", "Command timed out"
    except OSError as exc:
        return -1, "", str(exc), str(exc)
def _is_permission_error(text):
    low = str(text or "").lower()
    return any(hint in low for hint in _PERM_ERROR_HINTS)
def _is_missing_iface_error(text):
    low = str(text or "").lower()
    return any(hint in low for hint in _NO_IFACE_HINTS)
def is_admin():
    if _WIN:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    geteuid = getattr(os, "geteuid", None)
    if callable(geteuid):
        try:
            return geteuid() == 0
        except Exception:
            return False
    return False
def _find_binary(names, extra_paths=()):
    for name in names:
        hit = shutil.which(name)
        if hit and os.path.isfile(hit):
            return hit
    for cand in extra_paths:
        if cand and os.path.isfile(cand):
            return cand
    return None
def _windows_wg_candidates(filename):
    dirs = []
    for env_key in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        base = os.environ.get(env_key)
        if base:
            dirs.append(os.path.join(base, "WireGuard", filename))
    dirs.append(os.path.join(r"C:\Program Files\WireGuard", filename))
    dirs.append(os.path.join(r"C:\Program Files (x86)\WireGuard", filename))
    seen = []
    for item in dirs:
        norm = os.path.normcase(item)
        if norm not in {os.path.normcase(x) for x in seen}:
            seen.append(item)
    return seen
def _find_wg_binary():
    if _WIN:
        return _find_binary(["wg", "wg.exe"], _windows_wg_candidates("wg.exe"))
    return _find_binary(
        ["wg"],
        ("/usr/bin/wg", "/usr/local/bin/wg", "/opt/homebrew/bin/wg", "/sbin/wg", "/usr/sbin/wg"),
    )
def _find_wg_quick_binary():
    if _WIN:
        return _find_binary(["wg-quick"])
    return _find_binary(
        ["wg-quick"],
        ("/usr/bin/wg-quick", "/usr/local/bin/wg-quick", "/opt/homebrew/bin/wg-quick", "/sbin/wg-quick", "/usr/sbin/wg-quick"),
    )
def _find_wireguard_exe():
    if not _WIN:
        return None
    return _find_binary(["wireguard.exe", "wireguard"], _windows_wg_candidates("wireguard.exe"))
def _check_windows_wintun():
    sys_root = os.environ.get("SystemRoot") or r"C:\Windows"
    driver_path = os.path.join(sys_root, "System32", "drivers", "wintun.sys")
    driver_exists = os.path.isfile(driver_path)
    rc, stdout, stderr, _ = _run_readonly_cmd(["sc.exe", "query", "wintun"], timeout=3.0)
    sc_present = rc == 0 and "wintun" in stdout.lower()
    service_state = None
    if sc_present:
        for line in stdout.splitlines():
            if "STATE" in line.upper():
                if "RUNNING" in line.upper() or ": 4" in line:
                    service_state = "RUNNING"
                elif "STOPPED" in line.upper() or ": 1" in line:
                    service_state = "STOPPED"
                else:
                    service_state = line.split(":", 1)[-1].strip()
                break
    return {
        "present": bool(driver_exists or sc_present),
        "informational": True,
        "driver_file_present": driver_exists,
        "driver_path": driver_path if driver_exists else None,
        "service_registered": sc_present,
        "service_state": service_state,
        "query_error": stderr.strip() if rc not in (0, -1) and not sc_present and stderr.strip() else None,
    }
def _discover_windows_shard_services():
    rc, stdout, _, _ = _run_readonly_cmd(["sc.exe", "query", "type=", "service", "state=", "all"], timeout=4.0)
    if rc != 0 or not stdout:
        return []
    services = []
    cur_name = None
    cur_state = "UNKNOWN"
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if line.upper().startswith("SERVICE_NAME:"):
            if cur_name and cur_name.lower().startswith("wireguardtunnel$shard"):
                services.append({
                    "service_name": cur_name,
                    "name": cur_name.split("$", 1)[1],
                    "state": cur_state,
                    "active": cur_state == "RUNNING",
                    "source": "windows_service",
                })
            cur_name = line.split(":", 1)[1].strip()
            cur_state = "UNKNOWN"
        elif cur_name and line.upper().startswith("STATE"):
            upper = line.upper()
            if "RUNNING" in upper or ": 4" in upper:
                cur_state = "RUNNING"
            elif "STOPPED" in upper or ": 1" in upper:
                cur_state = "STOPPED"
            else:
                cur_state = line.split(":", 1)[-1].strip() or "UNKNOWN"
    if cur_name and cur_name.lower().startswith("wireguardtunnel$shard"):
        services.append({
            "service_name": cur_name,
            "name": cur_name.split("$", 1)[1],
            "state": cur_state,
            "active": cur_state == "RUNNING",
            "source": "windows_service",
        })
    return services
def _discover_shard_net_interfaces():
    found = {}
    try:
        import psutil
        stats = {}
        try:
            stats = psutil.net_if_stats()
        except Exception:
            stats = {}
        addrs = psutil.net_if_addrs()
        for iface, items in addrs.items():
            if not is_shard_tunnel_name(iface):
                continue
            ips = []
            for item in items:
                addr = str(getattr(item, "address", "") or "").strip()
                if not addr or "%" in addr:
                    continue
                try:
                    ipaddress.ip_address(addr)
                    ips.append(addr)
                except ValueError:
                    continue
            st = stats.get(iface)
            found[iface] = {
                "name": iface,
                "active": bool(st.isup) if st is not None else True,
                "ips": ips,
                "source": "net_if",
            }
    except Exception:
        pass
    return found
def _discover_shard_profiles():
    profiles = []
    wg_dir = Path(paths.wireguard_dir())
    if wg_dir.is_dir():
        try:
            for item in sorted(wg_dir.iterdir(), key=lambda p: p.name.lower()):
                if item.is_file() and item.suffix == ".conf" and is_shard_tunnel_name(item.stem):
                    profiles.append({
                        "name": item.stem,
                        "path": str(item),
                        "has_keys": (wg_dir / f"{item.stem}.keys.json").is_file(),
                        "scope": "shard_config",
                    })
        except OSError:
            pass
    if not _WIN and os.path.isdir("/etc/wireguard"):
        try:
            for entry in sorted(os.listdir("/etc/wireguard")):
                if entry.endswith(".conf"):
                    stem = entry[:-5]
                    if is_shard_tunnel_name(stem):
                        profiles.append({
                            "name": stem,
                            "path": f"/etc/wireguard/{entry}",
                            "has_keys": False,
                            "scope": "system_etc_wireguard",
                        })
        except OSError:
            pass
    return profiles
def detect_wireguard():
    wg_path = _find_wg_binary()
    wg_quick_path = _find_wg_quick_binary()
    wireguard_exe_path = _find_wireguard_exe()
    elevated = is_admin()
    wireguard_ready = bool(wg_path or wireguard_exe_path)
    wintun_info = _check_windows_wintun() if _WIN else None
    wintun_present = bool(wintun_info["present"]) if wintun_info else False
    wg_version = None
    wg_show_interfaces = []
    permission_denied = False
    wg_show_error = None
    if wg_path:
        v_rc, v_out, _, _ = _run_readonly_cmd([wg_path, "--version"], timeout=2.5)
        if v_rc == 0 and v_out.strip():
            wg_version = v_out.strip().splitlines()[0].strip()
        s_rc, s_out, s_err, s_exc = _run_readonly_cmd([wg_path, "show", "interfaces"], timeout=3.0)
        if s_rc == 0:
            for tok in s_out.split():
                if is_shard_tunnel_name(tok):
                    wg_show_interfaces.append(tok)
        else:
            combined = "\n".join(x for x in (s_err.strip(), s_out.strip(), str(s_exc or "").strip()) if x)
            if _is_permission_error(combined):
                permission_denied = True
            wg_show_error = combined or f"wg show exited with code {s_rc}"
    tunnels = {}
    for iface in wg_show_interfaces:
        tunnels[iface] = {"name": iface, "active": True, "source": "wg_show", "ips": []}
    if _WIN:
        for svc in _discover_windows_shard_services():
            tunnels.setdefault(svc["name"], {"name": svc["name"], "active": svc["active"], "source": "windows_service", "ips": []})
            tunnels[svc["name"]]["service_name"] = svc["service_name"]
            tunnels[svc["name"]]["service_state"] = svc["state"]
            if svc["active"]:
                tunnels[svc["name"]]["active"] = True
    for iface, info in _discover_shard_net_interfaces().items():
        if iface in tunnels:
            tunnels[iface]["ips"] = info["ips"]
            if info["active"]:
                tunnels[iface]["active"] = True
        else:
            tunnels[iface] = info
    kernel_module_loaded = None
    if sys.platform.startswith("linux"):
        kernel_module_loaded = os.path.isdir("/sys/module/wireguard")
    shard_tunnels = sorted(tunnels.values(), key=lambda x: x["name"].lower())
    active_shard = [x["name"] for x in shard_tunnels if x.get("active")]
    return {
        "platform": "windows" if _WIN else ("darwin" if sys.platform == "darwin" else sys.platform),
        "wireguard_ready": wireguard_ready,
        "wg_installed": bool(wg_path),
        "wg_quick_installed": bool(wg_quick_path),
        "wireguard_app_installed": bool(wireguard_exe_path),
        "wg_path": wg_path,
        "wg_quick_path": wg_quick_path,
        "wireguard_exe_path": wireguard_exe_path,
        "wg_version": wg_version,
        "wintun_present": wintun_present,
        "wintun": wintun_info,
        "is_admin": elevated,
        "permission_denied": permission_denied,
        "wg_show_error": wg_show_error,
        "kernel_module_loaded": kernel_module_loaded,
        "shard_tunnels": shard_tunnels,
        "active_shard_interfaces": active_shard,
        "shard_profiles": _discover_shard_profiles(),
        "wireguard_dir": paths.wireguard_dir(),
    }
def _decode_wg_key(key_b64):
    if not isinstance(key_b64, str):
        raise ValueError("WireGuard key must be a string")
    cleaned = key_b64.strip()
    if len(cleaned) != 44:
        raise ValueError("WireGuard key must be 44-character base64")
    try:
        raw = base64.b64decode(cleaned.encode("ascii"), validate=True)
    except Exception as exc:
        raise ValueError("WireGuard key is not valid base64") from exc
    if len(raw) != 32:
        raise ValueError("WireGuard key must decode to 32 bytes")
    return raw, cleaned
def is_valid_wg_key(key_b64):
    try:
        _decode_wg_key(key_b64)
        return True
    except ValueError:
        return False
def public_key_from_private(private_key_b64):
    raw, _ = _decode_wg_key(private_key_b64)
    priv = X25519PrivateKey.from_private_bytes(raw)
    pub_raw = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(pub_raw).decode("ascii")
def generate_wg_keypair():
    raw = bytearray(
        X25519PrivateKey.generate().private_bytes(
            Encoding.Raw,
            PrivateFormat.Raw,
            NoEncryption(),
        )
    )
    raw[0] &= 248
    raw[31] = (raw[31] & 127) | 64
    priv_bytes = bytes(raw)
    pub_bytes = (
        X25519PrivateKey.from_private_bytes(priv_bytes)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    return {
        "private_key": base64.b64encode(priv_bytes).decode("ascii"),
        "public_key": base64.b64encode(pub_bytes).decode("ascii"),
    }
def _resolve_wg_file(profile_name, suffix):
    name = validate_profile_name(profile_name)
    wg_dir = Path(paths.wireguard_dir()).resolve()
    target = (wg_dir / f"{name}{suffix}").resolve()
    try:
        target.relative_to(wg_dir)
    except ValueError as exc:
        raise ValueError("Path traversal rejected for WireGuard profile") from exc
    return name, wg_dir, target
def _ensure_secure_dir(wg_dir):
    os.makedirs(str(wg_dir), exist_ok=True)
    if not _WIN:
        try:
            os.chmod(str(wg_dir), 0o700)
        except OSError:
            pass
def ensure_node_wg_keys(profile_name="shard0"):
    name, wg_dir, key_path = _resolve_wg_file(profile_name, ".keys.json")
    _ensure_secure_dir(wg_dir)
    key_path_str = str(key_path)
    with storage.file_lock(key_path_str):
        existing = storage.read_json_safe(
            key_path_str,
            default=None,
            expected_type=dict,
            quarantine=True,
            use_backup=True,
        )
        if isinstance(existing, dict) and is_valid_wg_key(existing.get("private_key")):
            priv = existing["private_key"].strip()
            pub = public_key_from_private(priv)
            if existing.get("public_key") != pub or existing.get("profile") != name:
                existing["profile"] = name
                existing["private_key"] = priv
                existing["public_key"] = pub
                storage.atomic_write_json(key_path_str, existing, indent=2, fsync=True, backup=True, restrict=True)
            else:
                storage.restrict_file_permissions(key_path_str)
            return {
                "profile": name,
                "public_key": pub,
                "path": key_path_str,
                "created": False,
            }
        kp = generate_wg_keypair()
        payload = {
            "profile": name,
            "public_key": kp["public_key"],
            "private_key": kp["private_key"],
            "created_at": time.time(),
        }
        storage.atomic_write_json(key_path_str, payload, indent=2, fsync=True, backup=True, restrict=True)
        return {
            "profile": name,
            "public_key": kp["public_key"],
            "path": key_path_str,
            "created": True,
        }
def load_node_wg_keys(profile_name="shard0"):
    name, _, key_path = _resolve_wg_file(profile_name, ".keys.json")
    data = storage.read_json_safe(
        str(key_path),
        default=None,
        expected_type=dict,
        quarantine=False,
        use_backup=True,
    )
    if not isinstance(data, dict) or not is_valid_wg_key(data.get("private_key")):
        return None
    priv = data["private_key"].strip()
    return {
        "profile": name,
        "private_key": priv,
        "public_key": public_key_from_private(priv),
        "path": str(key_path),
    }
def _normalize_list_arg(val):
    if val is None:
        return []
    if isinstance(val, str):
        return [part.strip() for part in val.split(",") if part.strip()]
    if isinstance(val, (list, tuple)):
        out = []
        for item in val:
            if not isinstance(item, str):
                raise ValueError("Expected string items")
            for part in item.split(","):
                stripped = part.strip()
                if stripped:
                    out.append(stripped)
        return out
    raise ValueError("Expected string or list of strings")
def _validate_endpoint(endpoint):
    if not isinstance(endpoint, str):
        raise ValueError("Endpoint must be a string")
    s = endpoint.strip()
    if not s or any(ch.isspace() or ch == "\x00" for ch in s):
        raise ValueError("Invalid WireGuard endpoint")
    if s.startswith("["):
        if "]:" not in s:
            raise ValueError("Invalid IPv6 WireGuard endpoint")
        host_part, port_part = s[1:].split("]:", 1)
        ipaddress.IPv6Address(host_part)
    elif s.count(":") == 1:
        host_part, port_part = s.split(":", 1)
        if not host_part:
            raise ValueError("Empty host in WireGuard endpoint")
        try:
            ipaddress.IPv4Address(host_part)
        except ValueError:
            if not _HOST_RE.fullmatch(host_part):
                raise ValueError("Invalid hostname in WireGuard endpoint")
    else:
        raise ValueError("WireGuard endpoint must be host:port")
    if not port_part.isdigit():
        raise ValueError("Invalid port in WireGuard endpoint")
    port = int(port_part)
    if not (1 <= port <= 65535):
        raise ValueError("WireGuard endpoint port out of range")
    return s
def _validate_int_range(val, name, low, high):
    if isinstance(val, bool) or not isinstance(val, int):
        raise ValueError(f"{name} must be an integer")
    if not (low <= val <= high):
        raise ValueError(f"{name} must be between {low} and {high}")
    return val
def build_peer_config(local_privkey, local_cidr, listen_port=None, peers=None, dns=None, mtu=1420):
    _, priv_clean = _decode_wg_key(local_privkey)
    cidr_items = _normalize_list_arg(local_cidr)
    if not cidr_items:
        raise ValueError("local_cidr is required")
    validated_cidrs = [str(ipaddress.ip_interface(item)) for item in cidr_items]
    lines = [
        "[Interface]",
        f"PrivateKey = {priv_clean}",
        f"Address = {', '.join(validated_cidrs)}",
    ]
    if listen_port is not None:
        port = _validate_int_range(listen_port, "listen_port", 1, 65535)
        lines.append(f"ListenPort = {port}")
    if dns is not None:
        dns_items = _normalize_list_arg(dns)
        if dns_items:
            validated_dns = [str(ipaddress.ip_address(item)) for item in dns_items]
            lines.append(f"DNS = {', '.join(validated_dns)}")
    if mtu is not None:
        mtu_val = _validate_int_range(mtu, "mtu", 576, 9000)
        lines.append(f"MTU = {mtu_val}")
    if peers:
        if not isinstance(peers, (list, tuple)):
            raise ValueError("peers must be a sequence of dicts")
        for peer in peers:
            if not isinstance(peer, dict):
                raise ValueError("Each peer entry must be a dict")
            _, peer_pub = _decode_wg_key(peer.get("public_key"))
            lines.append("[Peer]")
            lines.append(f"PublicKey = {peer_pub}")
            psk = peer.get("preshared_key")
            if psk:
                _, psk_clean = _decode_wg_key(psk)
                lines.append(f"PresharedKey = {psk_clean}")
            allowed = _normalize_list_arg(peer.get("allowed_ips"))
            if allowed:
                nets = [str(ipaddress.ip_network(item, strict=False)) for item in allowed]
                lines.append(f"AllowedIPs = {', '.join(nets)}")
            ep = peer.get("endpoint")
            if ep:
                lines.append(f"Endpoint = {_validate_endpoint(ep)}")
            ka = peer.get("persistent_keepalive")
            if ka is not None:
                ka_val = _validate_int_range(ka, "persistent_keepalive", 0, 65535)
                lines.append(f"PersistentKeepalive = {ka_val}")
    return "\n".join(lines) + "\n"
def save_profile(profile_name, config_text):
    _, wg_dir, conf_path = _resolve_wg_file(profile_name, ".conf")
    if not isinstance(config_text, str) or not config_text.strip():
        raise ValueError("config_text must be a non-empty string")
    if "\x00" in config_text:
        raise ValueError("config_text contains null bytes")
    if "[Interface]" not in config_text:
        raise ValueError("config_text must contain a [Interface] section")
    _ensure_secure_dir(wg_dir)
    normalized = config_text.replace("\r\n", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    storage.atomic_write_text(str(conf_path), normalized, fsync=True, backup=False, restrict=True)
    return str(conf_path)
def check_peer_handshake(interface="shard0"):
    iface = validate_profile_name(interface)
    wg_path = _find_wg_binary()
    if not wg_path:
        return {
            "ok": False,
            "interface": iface,
            "wg_installed": False,
            "permission_denied": False,
            "interface_found": False,
            "peers": [],
            "active_peers": 0,
            "total_peers": 0,
            "error": "WireGuard CLI (wg) is not installed",
        }
    hs_rc, hs_out, hs_err, hs_exc = _run_readonly_cmd([wg_path, "show", iface, "latest-handshakes"], timeout=3.0)
    if hs_rc != 0:
        combined = "\n".join(x for x in (hs_err.strip(), hs_out.strip(), str(hs_exc or "").strip()) if x)
        perm_denied = _is_permission_error(combined)
        missing = _is_missing_iface_error(combined)
        return {
            "ok": False,
            "interface": iface,
            "wg_installed": True,
            "permission_denied": perm_denied,
            "interface_found": False if missing else None,
            "peers": [],
            "active_peers": 0,
            "total_peers": 0,
            "error": combined or f"wg show exited with code {hs_rc}",
        }
    peers_map = {}
    now = time.time()
    for line in hs_out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        pub = parts[0].strip()
        try:
            ts = int(parts[1])
        except ValueError:
            ts = 0
        age = round(max(0.0, now - ts), 3) if ts > 0 else None
        peers_map[pub] = {
            "public_key": pub,
            "latest_handshake": ts,
            "handshake_age_s": age,
            "active": bool(ts > 0 and (now - ts) <= _HANDSHAKE_STALE_S),
            "rx_bytes": 0,
            "tx_bytes": 0,
        }
    tr_rc, tr_out, _, _ = _run_readonly_cmd([wg_path, "show", iface, "transfer"], timeout=3.0)
    if tr_rc == 0:
        for line in tr_out.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            pub = parts[0].strip()
            try:
                rx = int(parts[1])
            except ValueError:
                rx = 0
            try:
                tx = int(parts[2])
            except ValueError:
                tx = 0
            entry = peers_map.setdefault(
                pub,
                {
                    "public_key": pub,
                    "latest_handshake": 0,
                    "handshake_age_s": None,
                    "active": False,
                    "rx_bytes": 0,
                    "tx_bytes": 0,
                },
            )
            entry["rx_bytes"] = rx
            entry["tx_bytes"] = tx
    peers_list = list(peers_map.values())
    return {
        "ok": True,
        "interface": iface,
        "wg_installed": True,
        "permission_denied": False,
        "interface_found": True,
        "peers": peers_list,
        "active_peers": sum(1 for p in peers_list if p["active"]),
        "total_peers": len(peers_list),
        "error": None,
    }
def install_plan():
    status = detect_wireguard()
    windows_plan = {
        "primary_command": "winget install --id WireGuard.WireGuard -e --source winget",
        "commands": ["winget install --id WireGuard.WireGuard -e --source winget"],
        "official_installer_url": "https://www.wireguard.com/install/",
        "fallback": "Download and run the official WireGuard Windows installer from https://www.wireguard.com/install/",
        "requires_uac": True,
        "uac_warning": "Installing WireGuard on Windows triggers a UAC elevation prompt to install kernel driver components (wintun/wireguard) and the tunnel manager service. SHARD never installs drivers silently.",
        "wintun_note": "The presence of wintun.sys alone (for example from another VPN client) does not provide wg.exe or wireguard.exe.",
    }
    linux_plan = {
        "distro_commands": {
            "debian_ubuntu": "sudo apt install wireguard-tools",
            "fedora_rhel": "sudo dnf install wireguard-tools",
            "arch": "sudo pacman -S wireguard-tools",
        },
        "kernel_module_check": "lsmod | grep wireguard || sudo modprobe wireguard",
        "kernel_module_loaded": status.get("kernel_module_loaded"),
        "official_url": "https://www.wireguard.com/install/",
        "requires_root": True,
        "non_root_limits": "Non-root users cannot create WireGuard network interfaces, run wg-quick up, or query wg show without CAP_NET_ADMIN / root (Operation not permitted). Generating keys and SHARD .conf files works unprivileged.",
    }
    macos_plan = {
        "primary_command": "brew install wireguard-tools",
        "commands": ["brew install wireguard-tools"],
        "official_url": "https://www.wireguard.com/install/",
        "requires_root": True,
        "non_root_limits": "Bringing tunnels up with wg-quick requires sudo and uses wireguard-go in userspace; alternatively use the official WireGuard macOS app.",
    }
    cur = status["platform"]
    recommended = windows_plan if cur == "windows" else (macos_plan if cur == "darwin" else linux_plan)
    return {
        "platform": cur,
        "already_ready": status["wireguard_ready"],
        "wintun_present": status["wintun_present"],
        "silent_install_allowed": False,
        "auto_executed": False,
        "requires_elevation": True,
        "recommended": recommended,
        "windows": windows_plan,
        "linux": linux_plan,
        "macos": macos_plan,
    }
def activation_plan(profile_path, interface="shard0"):
    iface = validate_profile_name(interface)
    if not isinstance(profile_path, (str, os.PathLike)) or not str(profile_path).strip():
        raise ValueError("profile_path is required")
    conf = Path(profile_path).resolve()
    win_cmd = f'wireguard.exe /installtunnelservice "{conf}"'
    win_down = f"wireguard.exe /uninstalltunnelservice {iface}"
    posix_conf = shlex.quote(str(conf))
    linux_up = f"sudo wg-quick up {posix_conf}"
    linux_down = f"sudo wg-quick down {posix_conf}"
    linux_systemd_copy = f"sudo install -m 600 {posix_conf} /etc/wireguard/{iface}.conf"
    linux_systemd_up = f"sudo systemctl enable --now wg-quick@{iface}"
    linux_systemd_down = f"sudo systemctl disable --now wg-quick@{iface}"
    macos_up = f"sudo wg-quick up {posix_conf}"
    macos_down = f"sudo wg-quick down {posix_conf}"
    cur = "windows" if _WIN else ("darwin" if sys.platform == "darwin" else sys.platform)
    primary = win_cmd if _WIN else linux_up
    return {
        "platform": cur,
        "interface": iface,
        "profile_path": str(conf),
        "profile_exists": conf.is_file(),
        "requires_elevation": True,
        "auto_executed": False,
        "primary_command": primary,
        "verify_command": f"wg show {iface}" if _WIN else f"sudo wg show {iface}",
        "windows": {
            "service_name": f"WireGuardTunnel${iface}",
            "install_and_start_command": win_cmd,
            "stop_and_remove_command": win_down,
            "uac_warning": f"Requires an Administrator terminal / UAC approval to register and start service WireGuardTunnel${iface}.",
        },
        "linux": {
            "up_command": linux_up,
            "down_command": linux_down,
            "systemd_copy_command": linux_systemd_copy,
            "systemd_enable_command": linux_systemd_up,
            "systemd_disable_command": linux_systemd_down,
            "warning": "Requires root / sudo (CAP_NET_ADMIN). SHARD never executes wg-quick or systemctl automatically.",
        },
        "macos": {
            "up_command": macos_up,
            "down_command": macos_down,
            "warning": "Requires sudo to launch userspace wireguard-go tunnel.",
        },
    }
def uninstall_plan(profile_name="shard0", remove_package=False):
    name, _, conf_path = _resolve_wg_file(profile_name, ".conf")
    if not is_shard_tunnel_name(name):
        raise ValueError("uninstall_plan only targets SHARD-prefixed profiles (shard* / wg-shard*)")
    _, _, key_path = _resolve_wg_file(name, ".keys.json")
    files_to_remove = [
        str(conf_path),
        str(conf_path) + ".bak",
        str(key_path),
        str(key_path) + ".bak",
    ]
    posix_conf = shlex.quote(str(conf_path))
    win_commands = [f"wireguard.exe /uninstalltunnelservice {name}"]
    linux_commands = [
        f"sudo wg-quick down {posix_conf}",
        f"sudo systemctl disable --now wg-quick@{name}",
        f"sudo rm -f /etc/wireguard/{name}.conf",
    ]
    macos_commands = [f"sudo wg-quick down {posix_conf}"]
    package_commands = {}
    if remove_package:
        package_commands = {
            "windows": ["winget uninstall --id WireGuard.WireGuard -e"],
            "linux": {
                "debian_ubuntu": "sudo apt remove wireguard-tools",
                "fedora_rhel": "sudo dnf remove wireguard-tools",
                "arch": "sudo pacman -R wireguard-tools",
            },
            "macos": ["brew uninstall wireguard-tools"],
        }
        warning = "WARNING: remove_package=True will uninstall the system WireGuard package and can break ALL non-SHARD WireGuard tunnels or shared driver consumers on this host. Confirm no other VPN tunnels depend on it before running package removal commands."
    else:
        warning = f"Removes ONLY SHARD tunnel service ({name}) and SHARD-owned WireGuard profile/key files; unrelated tunnels and the OS WireGuard package remain untouched."
    cur = "windows" if _WIN else ("darwin" if sys.platform == "darwin" else sys.platform)
    return {
        "platform": cur,
        "profile_name": name,
        "shard_only": True,
        "remove_package": bool(remove_package),
        "auto_executed": False,
        "requires_elevation": True,
        "service_name": f"WireGuardTunnel${name}" if _WIN else f"wg-quick@{name}",
        "files_to_remove": files_to_remove,
        "windows": {"commands": win_commands},
        "linux": {"commands": linux_commands},
        "macos": {"commands": macos_commands},
        "package_removal_commands": package_commands,
        "warning": warning,
    }