from __future__ import annotations
import base64
import ctypes
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
_IS_WIN = os.name == "nt"
_CREATE_NO_WINDOW = 0x08000000 if _IS_WIN else 0
_RULE_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")
_VALID_PROTOCOLS = ("TCP", "UDP")
_PROFILE_ALIASES = {
    "domain": "Domain",
    "domainauthenticated": "Domain",
    "private": "Private",
    "public": "Public",
    "any": "Any",
}
_BAD_PATH_CHARS = ("\x00", "\r", "\n", '"', "'", "`", "$", ";", "|", "&", "<", ">")
_VIRTUAL_HINTS = (
    "hyper-v",
    "vethernet",
    "vmware",
    "vmnet",
    "vbox",
    "virtualbox",
    "wsl",
    "docker",
    "loopback",
    "teredo",
    "isatap",
    "bluetooth",
    "personal area network",
    "wi-fi direct",
    "km-test",
    "npcap",
    "virtual",
)
_VPN_HINTS = (
    ("tailscale", "tailscale"),
    ("zerotier", "zerotier"),
    ("radmin", "radmin"),
    ("wireguard", "wireguard"),
    ("wintun", "wintun"),
    ("hamachi", "hamachi"),
    ("openvpn", "openvpn"),
    ("tap-windows", "openvpn"),
)
def _as_list(val) -> list:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]
def _first_existing(paths: list[str]) -> str | None:
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None
def _powershell_exe() -> str | None:
    if not _IS_WIN:
        return None
    for cand in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        found = shutil.which(cand)
        if found:
            return found
    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    fallback = os.path.join(sys_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return fallback if os.path.isfile(fallback) else None
def _run_powershell(script: str, timeout: float = 12.0) -> tuple[bool, str, str]:
    if not _IS_WIN:
        return False, "", "non-windows"
    exe = _powershell_exe()
    if not exe:
        return False, "", "powershell executable not found"
    wrapped = "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);$ErrorActionPreference='SilentlyContinue';" + script
    try:
        res = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", wrapped],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
        return res.returncode == 0, res.stdout or "", res.stderr or ""
    except Exception as exc:
        return False, "", str(exc)
def _run_powershell_json(script: str, timeout: float = 12.0) -> tuple[bool, object, str | None]:
    ok, stdout, stderr = _run_powershell(script, timeout=timeout)
    if not ok:
        return False, None, (stderr.strip() or "powershell invocation failed")
    raw = stdout.strip().lstrip("\ufeff")
    if not raw:
        return True, None, None
    try:
        return True, json.loads(raw), None
    except Exception as exc:
        return False, None, f"invalid json from powershell: {exc}"
def _validate_port(port: int) -> int:
    if isinstance(port, bool):
        raise ValueError("Invalid port: boolean is not allowed")
    try:
        val = int(port)
    except Exception as exc:
        raise ValueError(f"Invalid port: {port}") from exc
    if not (1 <= val <= 65535):
        raise ValueError(f"Invalid port {val}: must be in 1..65535")
    return val
def _validate_protocol(protocol: str) -> str:
    proto = str(protocol or "").strip().upper()
    if proto not in _VALID_PROTOCOLS:
        raise ValueError(f"Invalid protocol {protocol!r}: must be TCP or UDP")
    return proto
def _validate_rule_name(rule_name: str | None, protocol: str, port: int) -> str:
    if rule_name is None:
        return f"SHARD_{protocol}_{port}"
    if not isinstance(rule_name, str) or not _RULE_NAME_RE.match(rule_name):
        raise ValueError("Invalid rule_name: must match ^[a-zA-Z0-9_.-]{1,64}$")
    return rule_name
def _normalize_profiles(profiles: str) -> tuple[list[str], str, str]:
    raw = str(profiles or "").strip()
    if not raw:
        raise ValueError("Invalid profiles: at least one profile is required")
    items = []
    for tok in raw.split(","):
        key = tok.strip().lower()
        if not key:
            continue
        mapped = _PROFILE_ALIASES.get(key)
        if not mapped:
            raise ValueError(f"Invalid firewall profile {tok!r}: expected Domain, Private, Public, or Any")
        if mapped not in items:
            items.append(mapped)
    if not items:
        raise ValueError("Invalid profiles: at least one valid profile is required")
    if "Any" in items:
        return ["Any"], "Any", "any"
    return items, ",".join(items), ",".join(p.lower() for p in items)
def _sanitize_program_path(program_path: str | None) -> str | None:
    if program_path is None:
        return None
    raw = str(program_path).strip()
    if not raw:
        return None
    if any(ch in raw for ch in _BAD_PATH_CHARS):
        raise ValueError("Invalid program_path: contains forbidden characters")
    return os.path.normpath(raw)
def _norm_win_path(path: str | None) -> str:
    raw = str(path or "").strip().strip('"')
    if not raw or raw.lower() == "any":
        return ""
    expanded = os.path.expandvars(raw)
    return os.path.normcase(os.path.normpath(expanded))
def _is_usable_ipv4(addr: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(str(addr or "").strip())
        if ip_obj.version != 4:
            return False
        if ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_unspecified or ip_obj.is_multicast:
            return False
        return True
    except ValueError:
        return False
def _classify_windows_adapter(name: str, description: str, is_virtual: bool, ip_str: str | None = None) -> tuple[str, str]:
    desc_low = str(description or "").lower()
    name_low = str(name or "").lower()
    for hint, provider in _VPN_HINTS:
        if hint in desc_low or hint in name_low:
            return "vpn", provider
    if ip_str:
        try:
            ip_obj = ipaddress.IPv4Address(ip_str)
            if ip_obj in ipaddress.IPv4Network("26.0.0.0/8"):
                return "vpn", "radmin"
            if ip_obj in ipaddress.IPv4Network("100.64.0.0/10"):
                return "vpn", "tailscale"
            if ip_obj in ipaddress.IPv4Network("25.0.0.0/8"):
                return "vpn", "hamachi"
        except ValueError:
            pass
    if "hyper-v" in desc_low or "vethernet" in name_low:
        return "virtual", "hyper-v"
    if any(h in desc_low or h in name_low for h in _VIRTUAL_HINTS):
        return "virtual", "virtual"
    if is_virtual:
        return "virtual", "virtual"
    return "lan", "lan"
def is_admin() -> bool:
    if not _IS_WIN:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
def windows_network_inventory() -> list[dict]:
    if not _IS_WIN:
        return []
    ps_script = (
        "$adapters = @(Get-NetAdapter | ForEach-Object { @{ "
        "Name = $_.Name; InterfaceDescription = $_.InterfaceDescription; "
        "InterfaceIndex = [int]$_.InterfaceIndex; Status = [string]$_.Status; "
        "MacAddress = $_.MacAddress; LinkSpeed = $_.LinkSpeed; Virtual = [bool]$_.Virtual "
        "} }); "
        "$ips = @(Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object { @{ "
        "InterfaceIndex = [int]$_.InterfaceIndex; InterfaceAlias = $_.InterfaceAlias; "
        "IPAddress = $_.IPAddress; PrefixLength = [int]$_.PrefixLength; "
        "AddressState = [string]$_.AddressState "
        "} }); "
        "$profiles = @(Get-NetConnectionProfile | ForEach-Object { @{ "
        "InterfaceIndex = [int]$_.InterfaceIndex; InterfaceAlias = $_.InterfaceAlias; "
        "Name = $_.Name; NetworkCategory = [string]$_.NetworkCategory; "
        "IPv4Connectivity = [string]$_.IPv4Connectivity "
        "} }); "
        "[PSCustomObject]@{ adapters = $adapters; ips = $ips; profiles = $profiles } | ConvertTo-Json -Depth 4 -Compress"
    )
    ok, data, _ = _run_powershell_json(ps_script, timeout=10.0)
    if not ok or not isinstance(data, dict):
        return []
    adapters = _as_list(data.get("adapters"))
    ip_rows = _as_list(data.get("ips"))
    profile_rows = _as_list(data.get("profiles"))
    ips_by_idx: dict[int, list[dict]] = {}
    for row in ip_rows:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("InterfaceIndex"))
        except Exception:
            continue
        addr = str(row.get("IPAddress") or "").strip()
        if not _is_usable_ipv4(addr):
            continue
        try:
            prefix = int(row.get("PrefixLength"))
        except Exception:
            prefix = None
        cidr = None
        if prefix is not None and 0 <= prefix <= 32:
            try:
                cidr = str(ipaddress.IPv4Interface(f"{addr}/{prefix}").network)
            except Exception:
                cidr = None
        state = str(row.get("AddressState") or "")
        ips_by_idx.setdefault(idx, []).append({
            "ip": addr,
            "prefix_length": prefix,
            "cidr": cidr,
            "address_state": state,
        })
    prof_by_idx: dict[int, dict] = {}
    for prof in profile_rows:
        if not isinstance(prof, dict):
            continue
        try:
            idx = int(prof.get("InterfaceIndex"))
        except Exception:
            continue
        prof_by_idx[idx] = prof
    inventory: list[dict] = []
    for ad in adapters:
        if not isinstance(ad, dict):
            continue
        try:
            idx = int(ad.get("InterfaceIndex"))
        except Exception:
            continue
        name = str(ad.get("Name") or "")
        desc = str(ad.get("InterfaceDescription") or "")
        status = str(ad.get("Status") or "")
        is_virt = bool(ad.get("Virtual"))
        ad_ips = sorted(
            ips_by_idx.get(idx, []),
            key=lambda x: (0 if str(x.get("address_state", "")).lower() in ("preferred", "4") else 1, x["ip"]),
        )
        primary = ad_ips[0] if ad_ips else None
        primary_ip = primary["ip"] if primary else None
        prefix_length = primary["prefix_length"] if primary else None
        cidr = primary["cidr"] if primary else None
        kind, provider = _classify_windows_adapter(name, desc, is_virt, ip_str=primary_ip)
        prof = prof_by_idx.get(idx) or {}
        net_cat = str(prof.get("NetworkCategory") or "") or None
        prof_name = str(prof.get("Name") or "") or None
        ipv4_conn = str(prof.get("IPv4Connectivity") or "") or None
        usable = status.lower() == "up" and primary_ip is not None
        inventory.append({
            "interface_index": idx,
            "name": name,
            "interface_description": desc,
            "status": status,
            "virtual": is_virt,
            "kind": kind,
            "provider": provider,
            "ip": primary_ip,
            "prefix_length": prefix_length,
            "cidr": cidr,
            "ips": ad_ips,
            "network_category": net_cat,
            "profile_name": prof_name,
            "ipv4_connectivity": ipv4_conn,
            "mac_address": ad.get("MacAddress"),
            "link_speed": ad.get("LinkSpeed"),
            "usable": usable,
        })
    rank = {"vpn": 0, "lan": 1, "virtual": 2}
    inventory.sort(key=lambda x: (0 if x["usable"] else 1, rank.get(x["kind"], 3), x["interface_index"]))
    return inventory
def _parse_rule_profiles(profile_str: str | None) -> set[str]:
    raw = str(profile_str or "").strip()
    if not raw:
        return set()
    tokens = {tok.strip().lower() for tok in raw.split(",") if tok.strip()}
    if "any" in tokens:
        return {"Domain", "Private", "Public"}
    out = set()
    for tok in tokens:
        mapped = _PROFILE_ALIASES.get(tok)
        if mapped and mapped != "Any":
            out.add(mapped)
    return out
def _port_filter_matches(local_port, target_port: int) -> tuple[bool, bool]:
    items = local_port if isinstance(local_port, list) else [local_port]
    specific = False
    is_any = False
    for raw_item in items:
        if raw_item is None:
            continue
        for tok in str(raw_item).split(","):
            s = tok.strip().lower()
            if not s:
                continue
            if s == "any":
                is_any = True
                continue
            if s.isdigit():
                if int(s) == target_port:
                    specific = True
                continue
            if "-" in s:
                parts = s.split("-", 1)
                if parts[0].isdigit() and parts[1].isdigit():
                    lo = int(parts[0])
                    hi = int(parts[1])
                    if lo <= target_port <= hi:
                        specific = True
    return specific, is_any
def _query_firewall_snapshot(program_path: str | None = None) -> dict:
    if not _IS_WIN:
        return {"supported": False, "error": "non-windows"}
    target_b64 = ""
    if program_path:
        expanded = os.path.abspath(os.path.expandvars(program_path))
        target_b64 = base64.b64encode(expanded.encode("utf-8")).decode("ascii")
    ps_script = (
        f"$targetProg = if ('{target_b64}') {{ [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{target_b64}')) }} else {{ '' }}; "
        "$fw = @(Get-NetFirewallProfile | ForEach-Object { @{ "
        "Name = [string]$_.Name; Enabled = [string]$_.Enabled; "
        "DefaultInboundAction = [string]$_.DefaultInboundAction; "
        "DefaultOutboundAction = [string]$_.DefaultOutboundAction "
        "} }); "
        "$cp = @(Get-NetConnectionProfile | ForEach-Object { @{ "
        "InterfaceIndex = [int]$_.InterfaceIndex; InterfaceAlias = $_.InterfaceAlias; "
        "Name = $_.Name; NetworkCategory = [string]$_.NetworkCategory; "
        "IPv4Connectivity = [string]$_.IPv4Connectivity "
        "} }); "
        "$rawRules = @(Get-NetFirewallRule -Direction Inbound -Enabled True | Where-Object { -not $_.PackageFamilyName }); "
        "$rules = @($rawRules | ForEach-Object { @{ "
        "InstanceID = $_.InstanceID; Name = $_.Name; DisplayName = $_.DisplayName; "
        "Action = [string]$_.Action; Profile = [string]$_.Profile; "
        "PackageFamilyName = $_.PackageFamilyName "
        "} }); "
        "$rawById = @{}; foreach ($rr in $rawRules) { if ($rr.InstanceID) { $rawById[$rr.InstanceID] = $rr } }; "
        "$afs = @(Get-NetFirewallApplicationFilter | Where-Object { $rawById.ContainsKey($_.InstanceID) } | ForEach-Object { @{ "
        "InstanceID = $_.InstanceID; Program = $_.Program "
        "} }); "
        "$afMap = @{}; foreach ($a in $afs) { $afMap[$a.InstanceID] = $a.Program }; "
        "$pfMap = @{}; "
        "Get-NetFirewallPortFilter | Where-Object { $rawById.ContainsKey($_.InstanceID) } | ForEach-Object { "
        "$pfMap[$_.InstanceID] = @{ InstanceID = $_.InstanceID; Protocol = [string]$_.Protocol; LocalPort = $_.LocalPort } "
        "}; "
        "$needObjs = @(); "
        "foreach ($id in $rawById.Keys) { "
        "if ($pfMap.ContainsKey($id)) { continue }; "
        "$pg = [string]$afMap[$id]; "
        "$pgExp = if ($pg -and $pg -ne 'Any') { [Environment]::ExpandEnvironmentVariables($pg) } else { '' }; "
        "if ((-not $pg) -or ($pg -eq 'Any') -or ($targetProg -and ($pgExp -ieq $targetProg))) { $needObjs += $rawById[$id] } "
        "}; "
        "if ($needObjs.Count -gt 0) { "
        "$needObjs | Get-NetFirewallPortFilter | ForEach-Object { "
        "$pfMap[$_.InstanceID] = @{ InstanceID = $_.InstanceID; Protocol = [string]$_.Protocol; LocalPort = $_.LocalPort } "
        "} "
        "}; "
        "$pfs = @($pfMap.Values); "
        "[PSCustomObject]@{ fw_profiles = $fw; conn_profiles = $cp; rules = $rules; port_filters = $pfs; app_filters = $afs } | ConvertTo-Json -Depth 5 -Compress"
    )
    ok, data, err = _run_powershell_json(ps_script, timeout=20.0)
    if not ok or not isinstance(data, dict):
        return {"supported": False, "error": err or "failed to query Windows Defender Firewall"}
    fw_profiles: dict[str, dict] = {}
    for row in _as_list(data.get("fw_profiles")):
        if not isinstance(row, dict):
            continue
        pname = str(row.get("Name") or "").strip()
        if not pname:
            continue
        fw_profiles[pname] = {
            "enabled": str(row.get("Enabled") or "").lower() in ("true", "1"),
            "default_inbound_action": str(row.get("DefaultInboundAction") or "NotConfigured"),
            "default_outbound_action": str(row.get("DefaultOutboundAction") or "NotConfigured"),
        }
    conn_profiles: list[dict] = []
    active_categories: list[str] = []
    active_fw_profiles: list[str] = []
    for row in _as_list(data.get("conn_profiles")):
        if not isinstance(row, dict):
            continue
        cat = str(row.get("NetworkCategory") or "").strip()
        conn_profiles.append({
            "interface_index": row.get("InterfaceIndex"),
            "interface_alias": row.get("InterfaceAlias"),
            "name": row.get("Name"),
            "network_category": cat or None,
            "ipv4_connectivity": row.get("IPv4Connectivity"),
        })
        if cat and cat not in active_categories:
            active_categories.append(cat)
        mapped = _PROFILE_ALIASES.get(cat.lower())
        if mapped and mapped != "Any" and mapped not in active_fw_profiles:
            active_fw_profiles.append(mapped)
    pf_by_id = {
        str(row.get("InstanceID")): row
        for row in _as_list(data.get("port_filters"))
        if isinstance(row, dict) and row.get("InstanceID")
    }
    af_by_id = {
        str(row.get("InstanceID")): row
        for row in _as_list(data.get("app_filters"))
        if isinstance(row, dict) and row.get("InstanceID")
    }
    return {
        "supported": True,
        "error": None,
        "fw_profiles": fw_profiles,
        "conn_profiles": conn_profiles,
        "active_categories": active_categories,
        "active_fw_profiles": active_fw_profiles,
        "rules": _as_list(data.get("rules")),
        "pf_by_id": pf_by_id,
        "af_by_id": af_by_id,
    }
def _evaluate_firewall_port(snapshot: dict, port: int, program_path: str | None = None) -> dict:
    port_num = _validate_port(port)
    clean_prog = _sanitize_program_path(program_path)
    norm_target_prog = _norm_win_path(clean_prog)
    if not snapshot.get("supported"):
        return {
            "supported": False,
            "error": snapshot.get("error"),
            "port": port_num,
            "program_path": clean_prog,
            "firewall_active": False,
            "profiles": {},
            "connection_profiles": [],
            "active_profiles": [],
            "active_firewall_profiles": [],
            "port_allowed": False,
            "port_allowed_tcp": False,
            "port_allowed_udp": False,
            "program_allowed": False,
            "program_allowed_tcp": False,
            "program_allowed_udp": False,
            "allowed": False,
            "effective_allowed": False,
            "blocked_by_rule": False,
            "matching_port_rules": [],
            "matching_program_rules": [],
            "blocking_rules": [],
            "missing_profiles": [],
            "warnings": [snapshot["error"]] if snapshot.get("error") else [],
        }
    fw_profiles = snapshot["fw_profiles"]
    active_fw = snapshot["active_fw_profiles"]
    active_set = set(active_fw)
    if active_fw:
        firewall_active = any(fw_profiles.get(p, {}).get("enabled", False) for p in active_fw)
    else:
        firewall_active = any(v.get("enabled", False) for v in fw_profiles.values())
    pf_by_id = snapshot["pf_by_id"]
    af_by_id = snapshot["af_by_id"]
    matching_port_rules: list[dict] = []
    matching_program_rules: list[dict] = []
    blocking_rules: list[dict] = []
    port_allowed_tcp = False
    port_allowed_udp = False
    program_allowed_tcp = False
    program_allowed_udp = False
    covered_profiles: set[str] = set()
    for rule in snapshot["rules"]:
        if not isinstance(rule, dict):
            continue
        if rule.get("PackageFamilyName"):
            continue
        action = str(rule.get("Action") or "").strip()
        if action not in ("Allow", "Block"):
            continue
        iid = str(rule.get("InstanceID") or "")
        pf = pf_by_id.get(iid) or {}
        af = af_by_id.get(iid) or {}
        proto = str(pf.get("Protocol") or "Any").strip().upper()
        if proto not in ("TCP", "UDP", "ANY"):
            continue
        local_port = pf.get("LocalPort")
        port_specific, port_any = _port_filter_matches(local_port, port_num)
        raw_prog = af.get("Program")
        norm_rule_prog = _norm_win_path(raw_prog)
        prog_is_any = not norm_rule_prog
        prog_matches_target = bool(norm_target_prog and norm_rule_prog == norm_target_prog)
        if not port_specific and not (port_any and (prog_is_any or prog_matches_target)):
            continue
        rule_profiles = _parse_rule_profiles(rule.get("Profile"))
        covers_active = active_set.issubset(rule_profiles) if active_set else bool(rule_profiles)
        covers_any_active = bool(active_set & rule_profiles) if active_set else bool(rule_profiles)
        entry = {
            "name": rule.get("Name"),
            "display_name": rule.get("DisplayName"),
            "action": action,
            "profile": rule.get("Profile"),
            "profiles": sorted(rule_profiles),
            "protocol": proto,
            "local_port": local_port,
            "program": None if prog_is_any else raw_prog,
            "covers_active_profiles": covers_active,
            "covers_any_active_profile": covers_any_active,
        }
        if action == "Block":
            if (port_specific and (prog_is_any or prog_matches_target)) or (port_any and prog_matches_target):
                if covers_any_active:
                    blocking_rules.append(entry)
            continue
        is_global_port_match = (port_specific or (port_any and proto == "ANY")) and prog_is_any
        is_scoped_port_match = port_specific and not prog_is_any
        if is_global_port_match or is_scoped_port_match:
            matching_port_rules.append(entry)
        if prog_matches_target and (port_specific or port_any):
            matching_program_rules.append(entry)
        if covers_any_active:
            if is_global_port_match:
                covered_profiles.update(rule_profiles)
                if covers_active:
                    if proto in ("TCP", "ANY"):
                        port_allowed_tcp = True
                    if proto in ("UDP", "ANY"):
                        port_allowed_udp = True
            if prog_matches_target and (port_specific or port_any):
                covered_profiles.update(rule_profiles)
                if covers_active:
                    if proto in ("TCP", "ANY"):
                        program_allowed_tcp = True
                    if proto in ("UDP", "ANY"):
                        program_allowed_udp = True
    port_allowed = port_allowed_tcp or port_allowed_udp
    program_allowed = program_allowed_tcp or program_allowed_udp
    blocked_by_rule = bool(blocking_rules)
    allowed = (port_allowed or program_allowed) and not blocked_by_rule
    effective_allowed = (not firewall_active or allowed) and not blocked_by_rule
    missing_profiles = sorted(active_set - covered_profiles) if active_set else []
    warnings: list[str] = []
    if blocked_by_rule:
        warnings.append(f"Inbound traffic for port {port_num} is explicitly blocked by an active Windows Firewall Block rule.")
    elif firewall_active and not allowed:
        if "Public" in active_fw:
            warnings.append(
                f"Active adapter(s) use the Public profile with Windows Defender Firewall active and no matching Inbound Allow rule for port {port_num} on Public."
            )
        elif active_fw:
            warnings.append(
                f"Windows Defender Firewall is active on {', '.join(active_fw)} with no matching Inbound Allow rule for port {port_num}."
            )
    return {
        "supported": True,
        "error": None,
        "port": port_num,
        "program_path": clean_prog,
        "firewall_active": firewall_active,
        "profiles": fw_profiles,
        "connection_profiles": snapshot["conn_profiles"],
        "active_profiles": snapshot["active_categories"],
        "active_firewall_profiles": active_fw,
        "port_allowed": port_allowed,
        "port_allowed_tcp": port_allowed_tcp,
        "port_allowed_udp": port_allowed_udp,
        "program_allowed": program_allowed,
        "program_allowed_tcp": program_allowed_tcp,
        "program_allowed_udp": program_allowed_udp,
        "allowed": allowed,
        "effective_allowed": effective_allowed,
        "blocked_by_rule": blocked_by_rule,
        "matching_port_rules": matching_port_rules,
        "matching_program_rules": matching_program_rules,
        "blocking_rules": blocking_rules,
        "missing_profiles": missing_profiles,
        "warnings": warnings,
    }
def firewall_status_for_port(port: int, program_path: str | None = None) -> dict:
    port_num = _validate_port(port)
    clean_prog = _sanitize_program_path(program_path)
    snapshot = _query_firewall_snapshot(program_path=clean_prog)
    return _evaluate_firewall_port(snapshot, port_num, program_path=clean_prog)
def firewall_rule_plan(
    port: int,
    protocol: str = "TCP",
    rule_name: str | None = None,
    profiles: str = "Private,Public",
    program_path: str | None = None,
) -> dict:
    port_num = _validate_port(port)
    proto = _validate_protocol(protocol)
    name = _validate_rule_name(rule_name, proto, port_num)
    profile_list, ps_profiles, netsh_profiles = _normalize_profiles(profiles)
    prog = _sanitize_program_path(program_path)
    ps_prog = f' -Program "{prog}"' if prog else ""
    netsh_prog = f' program="{prog}"' if prog else ""
    ps_add = (
        f'New-NetFirewallRule -Name "{name}" -DisplayName "{name}" '
        f"-Direction Inbound -Action Allow -Enabled True -Protocol {proto} "
        f"-LocalPort {port_num} -Profile {ps_profiles}{ps_prog}"
    )
    ps_remove = f'Remove-NetFirewallRule -Name "{name}"'
    netsh_add = (
        f'netsh advfirewall firewall add rule name="{name}" dir=in action=allow '
        f"enable=yes protocol={proto.lower()} localport={port_num} "
        f"profile={netsh_profiles}{netsh_prog}"
    )
    netsh_remove = (
        f'netsh advfirewall firewall delete rule name="{name}" '
        f"dir=in protocol={proto.lower()} localport={port_num}"
    )
    includes_public = "Public" in profile_list or ps_profiles == "Any"
    admin = is_admin()
    explanation = (
        "Windows 10/11 assigns new Wi-Fi networks and VPN interfaces (such as Radmin VPN, WireGuard, or Tailscale) "
        "to the Public network category by default. When Windows Defender Firewall is active, inbound traffic on "
        "those adapters is blocked unless the rule includes the Public profile (e.g. Private,Public) or the "
        "adapter profile is changed to Private."
    )
    if not includes_public:
        explanation += " Note: Public is omitted from this plan, so adapters categorized as Public will still block inbound connections."
    return {
        "port": port_num,
        "protocol": proto,
        "rule_name": name,
        "profiles": profile_list,
        "profiles_arg": ps_profiles,
        "program_path": prog,
        "requires_uac": not admin,
        "is_admin": admin,
        "executed": False,
        "public_profile_applies": includes_public,
        "explanation": explanation,
        "powershell_add": ps_add,
        "powershell_remove": ps_remove,
        "netsh_add": netsh_add,
        "netsh_remove": netsh_remove,
        "commands": {
            "powershell": {"add": ps_add, "remove": ps_remove},
            "netsh": {"add": netsh_add, "remove": netsh_remove},
        },
    }
def run_elevated_powershell(command: str, confirm: bool = False) -> dict:
    if not _IS_WIN:
        return {
            "executed": False,
            "ok": False,
            "reason": "non_windows",
            "error": "Elevated PowerShell is only supported on Windows (nt).",
        }
    if confirm is not True:
        return {
            "executed": False,
            "ok": False,
            "reason": "confirmation_required",
            "error": "Explicit user confirmation (confirm=True) is required before UAC elevation.",
        }
    if not isinstance(command, str) or not command.strip() or "\x00" in command:
        return {
            "executed": False,
            "ok": False,
            "reason": "invalid_command",
            "error": "A non-empty PowerShell command without NUL bytes is required.",
        }
    ps_exe = _powershell_exe()
    if not ps_exe:
        return {
            "executed": False,
            "ok": False,
            "reason": "powershell_missing",
            "error": "PowerShell executable not found.",
        }
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    params = f"-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {encoded}"
    try:
        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        ]
        shell32.ShellExecuteW.restype = ctypes.c_void_p
        rc = shell32.ShellExecuteW(None, "runas", ps_exe, params, None, 0)
        code = int(rc or 0)
        ok = code > 32
        return {
            "executed": ok,
            "ok": ok,
            "launched": ok,
            "shell_execute_code": code,
            "uac_cancelled": code in (5, 1223),
            "reason": "launched" if ok else "shell_execute_failed",
            "error": None if ok else f"ShellExecuteW failed with code {code}",
        }
    except Exception as exc:
        return {
            "executed": False,
            "ok": False,
            "launched": False,
            "reason": "exception",
            "error": str(exc),
        }
def _query_service_state(service_name: str) -> dict:
    if not _IS_WIN:
        return {"installed": False, "state": "UNSUPPORTED", "running": False}
    sc_exe = shutil.which("sc.exe") or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "sc.exe")
    try:
        res = subprocess.run(
            [sc_exe, "query", service_name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4.0,
            creationflags=_CREATE_NO_WINDOW,
        )
        out = (res.stdout or "") + "\n" + (res.stderr or "")
        if res.returncode == 1060 or "1060" in out:
            return {"installed": False, "state": "NOT_INSTALLED", "running": False}
        m = re.search(r"STATE\s*:\s*\d+\s+([A-Z_]+)", out)
        state = m.group(1) if m else ("UNKNOWN" if res.returncode == 0 else "NOT_INSTALLED")
        installed = res.returncode == 0 or state not in ("NOT_INSTALLED",)
        return {"installed": installed, "state": state, "running": state == "RUNNING"}
    except Exception as exc:
        return {"installed": False, "state": "ERROR", "running": False, "error": str(exc)}
def _is_process_running_win(image_name: str) -> bool:
    if not _IS_WIN:
        return False
    tasklist_exe = shutil.which("tasklist.exe") or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tasklist.exe")
    try:
        res = subprocess.run(
            [tasklist_exe, "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4.0,
            creationflags=_CREATE_NO_WINDOW,
        )
        target = f'"{image_name.lower()}"'
        for line in (res.stdout or "").splitlines():
            if line.strip().lower().startswith(target):
                return True
        return False
    except Exception:
        return False
def _named_pipe_exists(pipe_name: str) -> bool:
    if not _IS_WIN:
        return False
    full_path = rf"\\.\pipe\{pipe_name}"
    try:
        if os.path.exists(full_path):
            return True
    except OSError:
        pass
    try:
        return pipe_name.lower() in {p.lower() for p in os.listdir(r"\\.\pipe")}
    except Exception:
        return False
def docker_status() -> dict:
    prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    local_app = os.environ.get("LocalAppData", "")
    docker_bin = shutil.which("docker")
    if not docker_bin and _IS_WIN:
        docker_bin = _first_existing([os.path.join(prog_files, "Docker", "Docker", "resources", "bin", "docker.exe")])
    desktop_cands = [os.path.join(prog_files, "Docker", "Docker", "Docker Desktop.exe")]
    if local_app:
        desktop_cands.append(os.path.join(local_app, "Programs", "Docker", "Docker", "Docker Desktop.exe"))
    desktop_path = shutil.which("Docker Desktop.exe") or (_first_existing(desktop_cands) if _IS_WIN else None)
    desktop_running = _is_process_running_win("Docker Desktop.exe")
    svc = _query_service_state("com.docker.service")
    pipe_name = "docker_engine"
    pipe_ready = _named_pipe_exists(pipe_name)
    installed = bool(docker_bin or desktop_path or svc["installed"])
    running = bool(pipe_ready or svc["running"] or desktop_running)
    ready = bool(docker_bin and (pipe_ready if _IS_WIN else running))
    return {
        "supported": _IS_WIN,
        "installed": installed,
        "running": running,
        "ready": ready,
        "docker_cli_installed": bool(docker_bin),
        "docker_bin": docker_bin,
        "desktop_installed": bool(desktop_path),
        "desktop_path": desktop_path,
        "desktop_running": desktop_running,
        "service_name": "com.docker.service",
        "service_installed": svc["installed"],
        "service_state": svc["state"],
        "service_running": svc["running"],
        "pipe_path": rf"\\.\pipe\{pipe_name}" if _IS_WIN else None,
        "engine_pipe_ready": pipe_ready,
    }
def wireguard_status(adapters: list[dict] | None = None) -> dict:
    prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    wg_bin = shutil.which("wg") or (_first_existing([os.path.join(prog_files, "WireGuard", "wg.exe")]) if _IS_WIN else None)
    wg_gui = shutil.which("wireguard") or (_first_existing([os.path.join(prog_files, "WireGuard", "wireguard.exe")]) if _IS_WIN else None)
    wintun_dll = _first_existing([
        os.path.join(sys_root, "System32", "wintun.dll"),
        os.path.join(prog_files, "WireGuard", "wintun.dll"),
    ]) if _IS_WIN else None
    svc = _query_service_state("WireGuardManager")
    inv = adapters if adapters is not None else (windows_network_inventory() if _IS_WIN else [])
    wg_adapters = [a for a in inv if a.get("provider") in ("wireguard", "wintun")]
    return {
        "supported": _IS_WIN,
        "installed": bool(wg_bin or wg_gui or svc["installed"]),
        "driver_present": bool(wintun_dll or wg_adapters),
        "wg_bin": wg_bin,
        "wireguard_exe": wg_gui,
        "wintun_dll": wintun_dll,
        "manager_service_installed": svc["installed"],
        "manager_service_state": svc["state"],
        "manager_service_running": svc["running"],
        "adapters": wg_adapters,
        "active_tunnels": [a for a in wg_adapters if a.get("usable")],
    }
def wireguard_install_plan() -> dict:
    admin = is_admin()
    return {
        "requires_uac": not admin,
        "is_admin": admin,
        "confirm_required": True,
        "executed": False,
        "install_command": "winget install --id WireGuard.WireGuard --exact --accept-source-agreements --accept-package-agreements",
        "uninstall_command": "winget uninstall --id WireGuard.WireGuard --exact",
        "note": "SHARD never silently installs or removes WireGuard or Wintun drivers; run only after explicit user confirmation.",
    }
def windows_diagnostics(port: int = 8000, extra_ports: tuple[int, ...] = (8001,)) -> dict:
    main_port = _validate_port(port)
    extras: list[int] = []
    for p in extra_ports:
        pv = _validate_port(p)
        if pv != main_port and pv not in extras:
            extras.append(pv)
    admin = is_admin()
    adapters = windows_network_inventory() if _IS_WIN else []
    lan_adapters = [a for a in adapters if a.get("kind") == "lan" and a.get("usable")]
    vpn_adapters = [a for a in adapters if a.get("kind") == "vpn" and a.get("usable")]
    virtual_adapters = [a for a in adapters if a.get("kind") == "virtual"]
    public_adapters = [a for a in adapters if a.get("network_category") == "Public" and a.get("usable")]
    program_exe = sys.executable
    snapshot = _query_firewall_snapshot(program_path=program_exe)
    fw_main = _evaluate_firewall_port(snapshot, main_port, program_path=program_exe)
    fw_extra = {str(p): _evaluate_firewall_port(snapshot, p, program_path=program_exe) for p in extras}
    plans: dict[str, dict] = {
        str(main_port): firewall_rule_plan(main_port, protocol="TCP", profiles="Private,Public", program_path=program_exe),
    }
    for p in extras:
        proto = "UDP" if p == 8001 else "TCP"
        plans[str(p)] = firewall_rule_plan(p, protocol=proto, profiles="Private,Public", program_path=program_exe)
    docker = docker_status()
    wg = wireguard_status(adapters=adapters)
    active_profiles = sorted({
        str(a.get("network_category"))
        for a in adapters
        if a.get("network_category")
    } | set(fw_main.get("active_profiles") or []))
    recommendations: list[str] = []
    for msg in fw_main.get("warnings") or []:
        if msg not in recommendations:
            recommendations.append(msg)
    if fw_main.get("program_allowed") and not fw_main.get("port_allowed"):
        recommendations.append(
            f"Python executable ({program_exe}) already has an Inbound Allow rule on active profile(s), while no dedicated TCP {main_port} port rule exists."
        )
    if public_adapters and fw_main.get("firewall_active"):
        pub_names = ", ".join(f"{a['name']} ({a['ip']})" for a in public_adapters if a.get("ip"))
        if pub_names:
            recommendations.append(
                f"Adapter(s) categorized as Public: {pub_names}. Windows Firewall rules targeting Private only will not cover them."
            )
    if docker.get("docker_cli_installed") and not docker.get("engine_pipe_ready"):
        recommendations.append("Docker CLI is installed, but the Docker Engine named pipe is currently offline.")
    return {
        "supported": _IS_WIN,
        "platform": "windows" if _IS_WIN else sys.platform,
        "is_admin": admin,
        "requires_uac": not admin,
        "port": main_port,
        "extra_ports": extras,
        "program_path": program_exe,
        "adapters": adapters,
        "lan_adapters": lan_adapters,
        "vpn_adapters": vpn_adapters,
        "virtual_adapters": virtual_adapters,
        "public_adapters": public_adapters,
        "active_profiles": active_profiles,
        "firewall": fw_main,
        "extra_port_firewall": fw_extra,
        "firewall_plans": plans,
        "docker": docker,
        "wireguard": wg,
        "wireguard_plan": wireguard_install_plan(),
        "recommendations": recommendations,
    }
