import ipaddress
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
_VIRTUAL_PREFIXES = (
    "veth",
    "br-",
    "docker",
    "virbr",
    "cni",
    "flannel",
    "calico",
    "cali",
    "podman",
    "lxcbr",
    "tunl",
    "kube-ipvs",
    "vmnet",
    "vboxnet",
)
_VIRTUAL_LINK_KINDS = frozenset({
    "veth",
    "vxlan",
    "geneve",
    "dummy",
    "ipip",
    "macvlan",
    "ipvlan",
    "ifb",
    "nlmon",
})
_PERM_ERROR_TOKENS = (
    "operation not permitted",
    "permission denied",
    "you need to be root",
    "must be root",
    "must be run as root",
    "access denied",
    "eperm",
    "eacces",
)
def _is_perm_error_text(text):
    low = str(text or "").lower()
    return any(tok in low for tok in _PERM_ERROR_TOKENS)
def _read_text_safe(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None
def _hex_le_to_ipv4(hex_str):
    raw = str(hex_str or "").strip()
    if len(raw) != 8:
        return None
    try:
        val = int(raw, 16)
        packed = struct.pack("=I", val) if sys.byteorder == "little" else struct.pack("<I", val)
        return socket.inet_ntoa(packed)
    except Exception:
        return None
def parse_proc_net_route(text):
    routes = []
    if not text:
        return {
            "default_interface": None,
            "default_gateway": None,
            "default_metric": None,
            "default_interfaces": [],
            "default_routes": [],
            "routes": routes,
        }
    for idx, line in enumerate(str(text).splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if idx == 0 and parts[0].lower() == "iface":
            continue
        if len(parts) < 8:
            continue
        iface = parts[0]
        dest_ip = _hex_le_to_ipv4(parts[1])
        gw_ip = _hex_le_to_ipv4(parts[2])
        mask_ip = _hex_le_to_ipv4(parts[7])
        if dest_ip is None or gw_ip is None or mask_ip is None:
            continue
        try:
            flags_int = int(parts[3], 16)
        except ValueError:
            flags_int = 0
        try:
            metric_int = int(parts[6])
        except ValueError:
            metric_int = 0
        is_up = bool(flags_int & 0x0001)
        has_gateway = bool(flags_int & 0x0002)
        is_default = (dest_ip == "0.0.0.0" and mask_ip == "0.0.0.0" and is_up)
        routes.append({
            "interface": iface,
            "destination": dest_ip,
            "gateway": None if gw_ip == "0.0.0.0" else gw_ip,
            "mask": mask_ip,
            "flags": flags_int,
            "metric": metric_int,
            "is_up": is_up,
            "has_gateway": has_gateway,
            "is_default": is_default,
        })
    default_routes = sorted(
        [r for r in routes if r["is_default"]],
        key=lambda r: (0 if r["has_gateway"] else 1, r["metric"], r["interface"]),
    )
    primary = default_routes[0] if default_routes else None
    seen_ifaces = []
    for r in default_routes:
        if r["interface"] not in seen_ifaces:
            seen_ifaces.append(r["interface"])
    return {
        "default_interface": primary["interface"] if primary else None,
        "default_gateway": primary["gateway"] if primary else None,
        "default_metric": primary["metric"] if primary else None,
        "default_interfaces": seen_ifaces,
        "default_routes": default_routes,
        "routes": routes,
    }
def _classify_linux_interface(name, link_kind=None, ipv4_list=None, is_loopback=False, is_default=False, is_sys_bridge=False, is_sys_tun=False):
    low = str(name or "").lower()
    lkind = str(link_kind or "").lower() or None
    if is_loopback or low == "lo":
        return "virtual", "loopback"
    for prefix in _VIRTUAL_PREFIXES:
        if low.startswith(prefix):
            return "virtual", prefix.rstrip("-")
    if lkind in _VIRTUAL_LINK_KINDS:
        return "virtual", lkind
    if (lkind == "bridge" or is_sys_bridge) and not is_default:
        return "virtual", "bridge"
    if low.startswith("wg") or lkind == "wireguard":
        return "vpn", "wireguard"
    if low.startswith("tailscale"):
        return "vpn", "tailscale"
    if low.startswith("zt"):
        return "vpn", "zerotier"
    for addr_item in ipv4_list or []:
        ip_str = addr_item.get("ip") if isinstance(addr_item, dict) else str(addr_item)
        try:
            ip_obj = ipaddress.IPv4Address(ip_str)
            if ip_obj in ipaddress.IPv4Network("100.64.0.0/10"):
                return "vpn", "tailscale"
            if ip_obj in ipaddress.IPv4Network("26.0.0.0/8"):
                return "vpn", "radmin"
            if ip_obj in ipaddress.IPv4Network("25.0.0.0/8"):
                return "vpn", "hamachi"
        except ValueError:
            pass
    if low.startswith(("tun", "tap")) or lkind in ("tun", "tap") or is_sys_tun:
        return "vpn", "tun"
    return "lan", "lan"
def _load_json_arg(raw):
    if raw is None:
        return []
    if isinstance(raw, (list, dict)):
        return raw if isinstance(raw, list) else [raw]
    text = str(raw).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
    except Exception:
        pass
    return []
def parse_ip_json(addr_json, link_json=None):
    addr_items = _load_json_arg(addr_json)
    link_items = _load_json_arg(link_json)
    merged = {}
    order = []
    for item in link_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("ifname") or "").strip()
        if not name:
            continue
        if name not in merged:
            merged[name] = {}
            order.append(name)
        merged[name]["link"] = item
    for item in addr_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("ifname") or "").strip()
        if not name:
            continue
        if name not in merged:
            merged[name] = {}
            order.append(name)
        merged[name]["addr"] = item
    out = []
    for name in order:
        link_obj = merged[name].get("link") or {}
        addr_obj = merged[name].get("addr") or {}
        base = link_obj if link_obj else addr_obj
        linkinfo = link_obj.get("linkinfo") or addr_obj.get("linkinfo") or {}
        link_kind = linkinfo.get("info_kind") if isinstance(linkinfo, dict) else None
        link_type = addr_obj.get("link_type") or link_obj.get("link_type")
        flags = addr_obj.get("flags") or link_obj.get("flags") or []
        flags_upper = {str(f).upper() for f in flags} if isinstance(flags, list) else set()
        operstate_raw = addr_obj.get("operstate") or link_obj.get("operstate")
        operstate = str(operstate_raw).lower() if operstate_raw is not None else None
        mtu = addr_obj.get("mtu") if addr_obj.get("mtu") is not None else link_obj.get("mtu")
        is_loopback = ("LOOPBACK" in flags_upper) or (name == "lo") or (link_type == "loopback")
        is_up = ("UP" in flags_upper) or (operstate == "up")
        ipv4_list = []
        ipv6_list = []
        for a in addr_obj.get("addr_info") or []:
            if not isinstance(a, dict):
                continue
            fam = a.get("family")
            local_ip = str(a.get("local") or "").strip()
            if not local_ip:
                continue
            plen = a.get("prefixlen")
            if fam == "inet":
                cidr = None
                bcast = a.get("broadcast")
                try:
                    iface_net = ipaddress.IPv4Interface(f"{local_ip}/{plen}") if plen is not None else None
                    if iface_net is not None:
                        cidr = str(iface_net.network)
                        if not bcast and iface_net.network.prefixlen < 31:
                            bcast = str(iface_net.network.broadcast_address)
                except Exception:
                    pass
                ipv4_list.append({
                    "ip": local_ip,
                    "prefixlen": plen,
                    "cidr": cidr,
                    "broadcast": bcast,
                    "scope": a.get("scope"),
                })
            elif fam == "inet6":
                ipv6_list.append({
                    "ip": local_ip,
                    "prefixlen": plen,
                    "scope": a.get("scope"),
                })
        kind, provider = _classify_linux_interface(
            name,
            link_kind=link_kind,
            ipv4_list=ipv4_list,
            is_loopback=is_loopback,
        )
        primary_ip = ipv4_list[0]["ip"] if ipv4_list else None
        out.append({
            "interface": name,
            "ifindex": base.get("ifindex"),
            "kind": kind,
            "provider": provider,
            "link_kind": link_kind,
            "link_type": link_type,
            "operstate": operstate,
            "mtu": int(mtu) if isinstance(mtu, (int, float)) else None,
            "flags": list(flags) if isinstance(flags, list) else [],
            "is_up": is_up,
            "is_loopback": is_loopback,
            "mac": addr_obj.get("address") or link_obj.get("address"),
            "ipv4": ipv4_list,
            "ipv6": ipv6_list,
            "ips": [x["ip"] for x in ipv4_list],
            "ip": primary_ip,
        })
    return out
def _read_sys_class_net(root="/sys/class/net"):
    if not os.path.isdir(root):
        return {}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return {}
    out = {}
    for name in names:
        base = os.path.join(root, name)
        if not os.path.isdir(base):
            continue
        operstate = _read_text_safe(os.path.join(base, "operstate"))
        carrier_raw = _read_text_safe(os.path.join(base, "carrier"))
        carrier = True if carrier_raw == "1" else (False if carrier_raw == "0" else None)
        mtu_raw = _read_text_safe(os.path.join(base, "mtu"))
        try:
            mtu = int(mtu_raw) if mtu_raw is not None else None
        except ValueError:
            mtu = None
        flags_raw = _read_text_safe(os.path.join(base, "flags"))
        try:
            flags_int = int(flags_raw, 16) if flags_raw is not None else None
        except ValueError:
            flags_int = None
        type_raw = _read_text_safe(os.path.join(base, "type"))
        try:
            arp_type = int(type_raw) if type_raw is not None else None
        except ValueError:
            arp_type = None
        mac = _read_text_safe(os.path.join(base, "address"))
        dev_path = os.path.join(base, "device")
        has_device = os.path.islink(dev_path) or os.path.exists(dev_path)
        is_bridge = os.path.isdir(os.path.join(base, "bridge"))
        is_tun = os.path.exists(os.path.join(base, "tun_flags"))
        out[name] = {
            "interface": name,
            "operstate": operstate.lower() if operstate is not None else None,
            "carrier": carrier,
            "mtu": mtu,
            "flags_int": flags_int,
            "flags_hex": flags_raw,
            "arp_type": arp_type,
            "mac": mac,
            "has_device": has_device,
            "is_sys_bridge": is_bridge,
            "is_sys_tun": is_tun,
            "is_up": bool(flags_int & 0x1) if flags_int is not None else (operstate == "up"),
            "is_loopback": (bool(flags_int & 0x8) if flags_int is not None else False) or (arp_type == 772) or (name == "lo"),
        }
    return out
def _run_ip_json():
    ip_bin = shutil.which("ip")
    if not ip_bin:
        return []
    addr_out = None
    link_out = None
    try:
        r_addr = subprocess.run([ip_bin, "-j", "addr", "show"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=2.5)
        if r_addr.returncode == 0:
            addr_out = r_addr.stdout
    except Exception:
        pass
    try:
        r_link = subprocess.run([ip_bin, "-j", "-d", "link", "show"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=2.5)
        if r_link.returncode == 0:
            link_out = r_link.stdout
    except Exception:
        pass
    if addr_out is None and link_out is None:
        return []
    return parse_ip_json(addr_out, link_out)
def _psutil_ipv4_fallback(iface_names):
    result = {}
    if not iface_names:
        return result
    try:
        import psutil
        addrs = psutil.net_if_addrs()
        for name in iface_names:
            items = addrs.get(name) or []
            ipv4_list = []
            for item in items:
                if getattr(item, "family", None) == socket.AF_INET:
                    ip_str = str(getattr(item, "address", "") or "").strip()
                    mask = getattr(item, "netmask", None)
                    if not ip_str:
                        continue
                    plen = None
                    cidr = None
                    bcast = getattr(item, "broadcast", None)
                    if mask:
                        try:
                            iface_net = ipaddress.IPv4Interface(f"{ip_str}/{mask}")
                            plen = iface_net.network.prefixlen
                            cidr = str(iface_net.network)
                            if not bcast and plen < 31:
                                bcast = str(iface_net.network.broadcast_address)
                        except Exception:
                            pass
                    ipv4_list.append({
                        "ip": ip_str,
                        "prefixlen": plen,
                        "cidr": cidr,
                        "broadcast": bcast,
                        "scope": "host" if ip_str.startswith("127.") else "global",
                    })
            if ipv4_list:
                result[name] = ipv4_list
    except Exception:
        pass
    return result
def linux_network_inventory():
    route_text = _read_text_safe("/proc/net/route")
    route_info = parse_proc_net_route(route_text)
    sys_map = _read_sys_class_net("/sys/class/net")
    ip_items = _run_ip_json()
    ip_map = {item["interface"]: item for item in ip_items}
    psutil_map = {}
    if sys_map and not ip_map:
        psutil_map = _psutil_ipv4_fallback(list(sys_map.keys()))
    names = []
    for key in list(sys_map.keys()) + list(ip_map.keys()):
        if key not in names:
            names.append(key)
    default_iface = route_info.get("default_interface")
    default_gw = route_info.get("default_gateway")
    default_set = set(route_info.get("default_interfaces") or [])
    inventory = []
    for name in names:
        sys_item = sys_map.get(name) or {}
        ip_item = ip_map.get(name) or {}
        ipv4_list = ip_item.get("ipv4") or psutil_map.get(name) or []
        ipv6_list = ip_item.get("ipv6") or []
        has_device = bool(sys_item.get("has_device", False))
        is_sys_bridge = bool(sys_item.get("is_sys_bridge", False))
        is_sys_tun = bool(sys_item.get("is_sys_tun", False))
        is_loopback = bool(sys_item.get("is_loopback") or ip_item.get("is_loopback") or name == "lo")
        is_default = (name == default_iface) or (name in default_set)
        link_kind = ip_item.get("link_kind")
        link_type = ip_item.get("link_type")
        if link_type is None and sys_item.get("arp_type") == 1:
            link_type = "ether"
        elif link_type is None and sys_item.get("arp_type") == 772:
            link_type = "loopback"
        kind, provider = _classify_linux_interface(
            name,
            link_kind=link_kind,
            ipv4_list=ipv4_list,
            is_loopback=is_loopback,
            is_default=is_default,
            is_sys_bridge=is_sys_bridge,
            is_sys_tun=is_sys_tun,
        )
        operstate = sys_item.get("operstate") if sys_item.get("operstate") is not None else ip_item.get("operstate")
        mtu = sys_item.get("mtu") if sys_item.get("mtu") is not None else ip_item.get("mtu")
        is_up = bool(sys_item["is_up"]) if "is_up" in sys_item else bool(ip_item.get("is_up", False))
        is_physical = (kind == "lan") and (has_device or (link_type == "ether" and not is_sys_bridge))
        ips = [x["ip"] for x in ipv4_list]
        inventory.append({
            "interface": name,
            "kind": kind,
            "provider": provider,
            "is_physical": is_physical,
            "has_device": has_device,
            "is_default": name == default_iface,
            "has_default_route": is_default,
            "default_gateway": default_gw if name == default_iface else None,
            "operstate": operstate,
            "carrier": sys_item.get("carrier"),
            "mtu": mtu,
            "flags": sys_item.get("flags_int"),
            "flags_hex": sys_item.get("flags_hex"),
            "ip_flags": ip_item.get("flags") or [],
            "is_up": is_up,
            "is_loopback": is_loopback,
            "link_kind": link_kind,
            "link_type": link_type,
            "mac": sys_item.get("mac") or ip_item.get("mac"),
            "ipv4": ipv4_list,
            "ipv6": ipv6_list,
            "ips": ips,
            "ip": ips[0] if ips else None,
        })
    rank = {"lan": 0, "vpn": 1, "virtual": 2}
    inventory.sort(key=lambda x: (rank.get(x["kind"], 3), 0 if x["is_default"] else 1, 0 if x["is_up"] else 1, x["interface"]))
    return inventory
def _parse_wg_show(text):
    interfaces = []
    cur_iface = None
    cur_peer = None
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("interface:"):
            cur_peer = None
            cur_iface = {
                "interface": line.split(":", 1)[1].strip(),
                "public_key": None,
                "listening_port": None,
                "peers": [],
            }
            interfaces.append(cur_iface)
        elif line.startswith("peer:") and cur_iface is not None:
            cur_peer = {
                "public_key": line.split(":", 1)[1].strip(),
                "endpoint": None,
                "allowed_ips": [],
                "latest_handshake": None,
            }
            cur_iface["peers"].append(cur_peer)
        elif cur_peer is not None and ":" in line:
            k, v = [p.strip() for p in line.split(":", 1)]
            kl = k.lower()
            if kl == "endpoint":
                cur_peer["endpoint"] = v
            elif kl == "allowed ips":
                cur_peer["allowed_ips"] = [x.strip() for x in v.split(",") if x.strip()]
            elif kl == "latest handshake":
                cur_peer["latest_handshake"] = v
        elif cur_iface is not None and ":" in line:
            k, v = [p.strip() for p in line.split(":", 1)]
            kl = k.lower()
            if kl == "public key":
                cur_iface["public_key"] = v
            elif kl == "listening port":
                try:
                    cur_iface["listening_port"] = int(v)
                except ValueError:
                    pass
    return interfaces
def check_wireguard_linux():
    wg_bin = shutil.which("wg")
    if not wg_bin:
        return {
            "installed": False,
            "binary": None,
            "available": False,
            "permission_error": False,
            "interfaces": [],
            "interface_names": [],
            "peer_count": 0,
            "error": None,
            "remediation": "Install wireguard-tools (for example: sudo apt install wireguard-tools)",
        }
    try:
        res = subprocess.run([wg_bin, "show"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=3.0)
    except PermissionError as e:
        return {
            "installed": True,
            "binary": wg_bin,
            "available": False,
            "permission_error": True,
            "interfaces": [],
            "interface_names": [],
            "peer_count": 0,
            "error": str(e),
            "remediation": "Run sudo wg show or grant CAP_NET_ADMIN (sudo setcap cap_net_admin+ep $(command -v wg))",
        }
    except Exception as e:
        return {
            "installed": True,
            "binary": wg_bin,
            "available": False,
            "permission_error": False,
            "interfaces": [],
            "interface_names": [],
            "peer_count": 0,
            "error": str(e),
            "remediation": "Verify WireGuard kernel module or wireguard-go service is healthy",
        }
    combined = f"{res.stderr or ''}\n{res.stdout or ''}".strip()
    if res.returncode != 0:
        perm_err = _is_perm_error_text(combined)
        return {
            "installed": True,
            "binary": wg_bin,
            "available": False,
            "permission_error": perm_err,
            "interfaces": [],
            "interface_names": [],
            "peer_count": 0,
            "error": combined or f"wg show exited with code {res.returncode}",
            "remediation": (
                "Run sudo wg show or grant CAP_NET_ADMIN (sudo setcap cap_net_admin+ep $(command -v wg))"
                if perm_err else "Check WireGuard interface state with wg show"
            ),
        }
    parsed = _parse_wg_show(res.stdout)
    peer_count = sum(len(x.get("peers") or []) for x in parsed)
    return {
        "installed": True,
        "binary": wg_bin,
        "available": True,
        "permission_error": False,
        "interfaces": parsed,
        "interface_names": [x["interface"] for x in parsed],
        "peer_count": peer_count,
        "error": None,
        "remediation": None,
    }
def _port_protocol(port):
    return "udp" if int(port) == 8001 else "tcp"
def _probe_ufw(req_specs):
    ufw_bin = shutil.which("ufw")
    if not ufw_bin:
        return {"installed": False, "status": "not_installed", "permission_error": False, "allowed_ports": [], "error": None}
    try:
        res = subprocess.run([ufw_bin, "status"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=3.0)
    except PermissionError as e:
        return {"installed": True, "status": "permission_denied", "permission_error": True, "allowed_ports": [], "error": str(e)}
    except Exception as e:
        return {"installed": True, "status": "error", "permission_error": False, "allowed_ports": [], "error": str(e)}
    combined = f"{res.stderr or ''}\n{res.stdout or ''}".strip()
    if res.returncode != 0 or _is_perm_error_text(combined):
        perm_err = _is_perm_error_text(combined)
        return {
            "installed": True,
            "status": "permission_denied" if perm_err else "error",
            "permission_error": perm_err,
            "allowed_ports": [],
            "error": combined or f"ufw status exited with code {res.returncode}",
        }
    low = (res.stdout or "").lower()
    if "status: inactive" in low:
        return {"installed": True, "status": "inactive", "permission_error": False, "allowed_ports": [], "error": None}
    active = "status: active" in low
    allowed = []
    for spec in req_specs:
        p = str(spec["port"])
        proto = spec["protocol"]
        for line in (res.stdout or "").splitlines():
            ll = line.lower()
            if "allow" in ll and (f"{p}/{proto}" in ll or f"{p} " in ll or ll.strip().startswith(p)):
                allowed.append(spec["port"])
                break
    return {
        "installed": True,
        "status": "active" if active else "unknown",
        "permission_error": False,
        "allowed_ports": allowed,
        "error": None,
    }
def _probe_nft(req_specs):
    nft_bin = shutil.which("nft")
    if not nft_bin:
        return {"installed": False, "status": "not_installed", "permission_error": False, "allowed_ports": [], "rules_count": 0, "error": None}
    try:
        res = subprocess.run([nft_bin, "-j", "list", "ruleset"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=3.0)
    except PermissionError as e:
        return {"installed": True, "status": "permission_denied", "permission_error": True, "allowed_ports": [], "rules_count": 0, "error": str(e)}
    except Exception as e:
        return {"installed": True, "status": "error", "permission_error": False, "allowed_ports": [], "rules_count": 0, "error": str(e)}
    combined = f"{res.stderr or ''}\n{res.stdout or ''}".strip()
    if res.returncode != 0 or _is_perm_error_text(combined):
        perm_err = _is_perm_error_text(combined)
        return {
            "installed": True,
            "status": "permission_denied" if perm_err else "error",
            "permission_error": perm_err,
            "allowed_ports": [],
            "rules_count": 0,
            "error": combined or f"nft exited with code {res.returncode}",
        }
    try:
        payload = json.loads(res.stdout or "{}")
    except Exception as e:
        return {"installed": True, "status": "error", "permission_error": False, "allowed_ports": [], "rules_count": 0, "error": str(e)}
    entries = payload.get("nftables") or []
    rules = [e["rule"] for e in entries if isinstance(e, dict) and isinstance(e.get("rule"), dict)]
    chains = [e["chain"] for e in entries if isinstance(e, dict) and isinstance(e.get("chain"), dict)]
    if not rules and not chains:
        return {"installed": True, "status": "inactive", "permission_error": False, "allowed_ports": [], "rules_count": 0, "error": None}
    input_policies = [str(c.get("policy") or "").lower() for c in chains if str(c.get("hook") or "").lower() == "input"]
    raw_rules_text = json.dumps(rules).lower()
    allowed = []
    for spec in req_specs:
        if any(pol == "accept" for pol in input_policies) and not rules:
            allowed.append(spec["port"])
        elif f'"{spec["port"]}"' in raw_rules_text or f": {spec['port']}," in raw_rules_text or f": {spec['port']}}}" in raw_rules_text:
            allowed.append(spec["port"])
    return {
        "installed": True,
        "status": "active" if rules or any(pol in ("drop", "reject") for pol in input_policies) else "inactive",
        "permission_error": False,
        "allowed_ports": allowed,
        "rules_count": len(rules),
        "error": None,
    }
def check_firewall_linux(ports=(8000, 8001)):
    req_ports = [int(p) for p in ports]
    req_specs = [{"port": p, "protocol": _port_protocol(p)} for p in req_ports]
    ufw_info = _probe_ufw(req_specs)
    nft_info = _probe_nft(req_specs)
    perm_error = bool(ufw_info["permission_error"] or nft_info["permission_error"])
    remediation = []
    if ufw_info["installed"]:
        if ufw_info["permission_error"]:
            remediation.append("sudo ufw status")
        if ufw_info["status"] in ("active", "permission_denied", "unknown"):
            for spec in req_specs:
                if spec["port"] not in ufw_info["allowed_ports"]:
                    remediation.append(f"sudo ufw allow {spec['port']}/{spec['protocol']}")
    if nft_info["installed"]:
        if nft_info["permission_error"]:
            remediation.append("sudo nft list ruleset")
        if nft_info["status"] in ("active", "permission_denied", "unknown"):
            for spec in req_specs:
                if spec["port"] not in nft_info["allowed_ports"]:
                    remediation.append(f"sudo nft add rule inet filter input {spec['protocol']} dport {spec['port']} accept")
    if not ufw_info["installed"] and not nft_info["installed"]:
        status = "not_installed"
        for spec in req_specs:
            remediation.append(f"sudo ufw allow {spec['port']}/{spec['protocol']}")
    elif perm_error and ufw_info["status"] != "inactive" and nft_info["status"] != "inactive":
        status = "permission_denied"
    elif ufw_info["status"] == "active":
        missing_ufw = [p for p in req_ports if p not in ufw_info["allowed_ports"]]
        status = "active_missing_rules" if missing_ufw else "active"
    elif nft_info["status"] == "active":
        missing_nft = [p for p in req_ports if p not in nft_info["allowed_ports"]]
        status = "active_missing_rules" if missing_nft else "active"
    elif ufw_info["status"] == "inactive" and nft_info["status"] in ("inactive", "not_installed"):
        status = "inactive"
    elif nft_info["status"] == "inactive" and ufw_info["status"] in ("inactive", "not_installed"):
        status = "inactive"
    elif perm_error:
        status = "permission_denied"
    else:
        status = "unknown"
    return {
        "status": status,
        "permission_error": perm_error,
        "required_ports": req_ports,
        "port_specs": req_specs,
        "remediation_commands": remediation,
        "ufw": ufw_info,
        "nft": nft_info,
    }
def check_systemd_units(interfaces=()):
    systemd_running = os.path.isdir("/run/systemd/system")
    systemctl_bin = shutil.which("systemctl")
    wg_dir = "/etc/wireguard"
    wg_dir_exists = os.path.exists(wg_dir)
    wg_dir_accessible = False
    wg_dir_perm_err = False
    wg_configs = []
    wg_ifaces = []
    if wg_dir_exists:
        try:
            for fname in sorted(os.listdir(wg_dir)):
                if fname.endswith(".conf"):
                    wg_configs.append(os.path.join(wg_dir, fname))
                    wg_ifaces.append(fname[:-5])
            wg_dir_accessible = True
        except PermissionError:
            wg_dir_perm_err = True
        except OSError:
            wg_dir_accessible = False
    target_ifaces = []
    for item in list(interfaces or ()) + wg_ifaces:
        s = str(item or "").strip()
        if s and s not in target_ifaces:
            target_ifaces.append(s)
    units = {}
    unit_perm_err = False
    if systemd_running and systemctl_bin:
        for iface in target_ifaces:
            unit_name = f"wg-quick@{iface}.service"
            try:
                res = subprocess.run(
                    [systemctl_bin, "show", unit_name, "--property=LoadState,ActiveState,SubState,UnitFileState", "--no-pager"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                )
                combined = f"{res.stderr or ''}\n{res.stdout or ''}".strip()
                if res.returncode != 0 and _is_perm_error_text(combined):
                    unit_perm_err = True
                    units[unit_name] = {
                        "unit": unit_name,
                        "interface": iface,
                        "load_state": "unknown",
                        "active_state": "unknown",
                        "sub_state": "unknown",
                        "unit_file_state": "unknown",
                        "active": False,
                        "enabled": False,
                        "permission_error": True,
                        "error": combined,
                    }
                    continue
                props = {}
                for line in (res.stdout or "").splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        props[k.strip()] = v.strip()
                active_state = props.get("ActiveState", "unknown")
                file_state = props.get("UnitFileState", "unknown")
                units[unit_name] = {
                    "unit": unit_name,
                    "interface": iface,
                    "load_state": props.get("LoadState", "unknown"),
                    "active_state": active_state,
                    "sub_state": props.get("SubState", "unknown"),
                    "unit_file_state": file_state,
                    "active": active_state == "active",
                    "enabled": file_state in ("enabled", "enabled-runtime", "static"),
                    "permission_error": False,
                    "error": combined if res.returncode != 0 else None,
                }
            except PermissionError as e:
                unit_perm_err = True
                units[unit_name] = {
                    "unit": unit_name,
                    "interface": iface,
                    "load_state": "unknown",
                    "active_state": "unknown",
                    "sub_state": "unknown",
                    "unit_file_state": "unknown",
                    "active": False,
                    "enabled": False,
                    "permission_error": True,
                    "error": str(e),
                }
            except Exception as e:
                units[unit_name] = {
                    "unit": unit_name,
                    "interface": iface,
                    "load_state": "unknown",
                    "active_state": "unknown",
                    "sub_state": "unknown",
                    "unit_file_state": "unknown",
                    "active": False,
                    "enabled": False,
                    "permission_error": False,
                    "error": str(e),
                }
    remediation = []
    if wg_dir_perm_err:
        remediation.append("sudo ls /etc/wireguard (directory is root-only 0700 by default)")
    if unit_perm_err:
        remediation.append("sudo systemctl status 'wg-quick@*'")
    return {
        "systemd_running": systemd_running,
        "systemctl_available": bool(systemctl_bin),
        "permission_error": wg_dir_perm_err or unit_perm_err,
        "etc_wireguard": {
            "exists": wg_dir_exists,
            "accessible": wg_dir_accessible,
            "permission_error": wg_dir_perm_err,
            "configs": wg_configs,
            "interfaces": wg_ifaces,
        },
        "units": units,
        "remediation": remediation,
    }
def _has_cap_net_bind_service():
    text = _read_text_safe("/proc/self/status")
    if not text:
        return False
    for line in text.splitlines():
        if line.startswith("CapEff:"):
            raw_hex = line.split(":", 1)[1].strip()
            try:
                return bool(int(raw_hex, 16) & (1 << 10))
            except ValueError:
                return False
    return False
def check_port_privileges(port):
    p = int(port)
    privileged = p < 1024
    euid = getattr(os, "geteuid", lambda: None)()
    is_root = (euid == 0)
    has_cap = _has_cap_net_bind_service()
    can_bind = (not privileged) or is_root or has_cap
    if privileged and not can_bind:
        guidance = "Privileged port (< 1024) blocked for non-root: use port >= 1024 or grant cap_net_bind_service"
    elif privileged:
        guidance = "Privileged port (< 1024) permitted via root or cap_net_bind_service"
    else:
        guidance = "Unprivileged port (>= 1024): non-root bind allowed without extra capabilities"
    return {
        "port": p,
        "privileged_port": privileged,
        "euid": euid,
        "is_root": is_root,
        "has_cap_net_bind_service": has_cap,
        "can_bind": can_bind,
        "guidance": guidance,
    }
def linux_diagnostics(ports=(8000, 8001)):
    route_info = parse_proc_net_route(_read_text_safe("/proc/net/route"))
    interfaces = linux_network_inventory()
    wg_info = check_wireguard_linux()
    wg_ifaces = list(wg_info.get("interface_names") or [])
    for item in interfaces:
        if item.get("kind") == "vpn" and item.get("provider") == "wireguard" and item["interface"] not in wg_ifaces:
            wg_ifaces.append(item["interface"])
    fw_info = check_firewall_linux(ports=ports)
    sd_info = check_systemd_units(interfaces=wg_ifaces)
    port_checks = [check_port_privileges(p) for p in ports]
    euid = getattr(os, "geteuid", lambda: None)()
    perm_warnings = []
    if wg_info.get("permission_error"):
        perm_warnings.append("wireguard")
    if fw_info.get("permission_error"):
        perm_warnings.append("firewall")
    if sd_info.get("permission_error"):
        perm_warnings.append("systemd")
    return {
        "platform": sys.platform,
        "is_linux": sys.platform.startswith("linux"),
        "euid": euid,
        "is_root": euid == 0,
        "default_route": route_info,
        "interfaces": interfaces,
        "wireguard": wg_info,
        "firewall": fw_info,
        "systemd": sd_info,
        "ports": port_checks,
        "summary": {
            "lan_interfaces": len([x for x in interfaces if x.get("kind") == "lan"]),
            "vpn_interfaces": len([x for x in interfaces if x.get("kind") == "vpn"]),
            "virtual_interfaces": len([x for x in interfaces if x.get("kind") == "virtual"]),
            "default_interface": route_info.get("default_interface"),
            "permission_warnings": perm_warnings,
        },
    }