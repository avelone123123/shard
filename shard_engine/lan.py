import glob
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from . import hardware as _hardware
from . import paths as _paths
from . import storage as _storage
from .fit import RAM_RESERVE_MB, VRAM_RESERVE_MB
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
BEACON_PORT = 8001
BEACON_INTERVAL_S = 2.0
PEER_TTL_S = 8.0
HEARTBEAT_INTERVAL_S = 1.0
HEARTBEAT_MISS_LIMIT = 3
REBALANCE_INTERVAL_S = 2.0
REBALANCE_ERROR_COOLDOWN_TICKS = 30
BEACON_ALIVE_S = BEACON_INTERVAL_S * 2
RECONNECT_BACKOFF_MAX_S = 10.0
MAX_PEERS = 128
DEFAULT_RPC_PORT = 50052
APP_TAG = "shard-lan"
PROTO_VER = 1
PAIR_LEN = 4
PAIR_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
TOKEN_PREFIX = "sk-shard-lan-"
RPC_EXE = "ggml-rpc-server.exe" if os.name == "nt" else "ggml-rpc-server"
LAN_FILE = "lan.json"
DEFAULT_MASTER_PORT = 8000
_VIRTUAL_IFACE_HINTS = (
    "vethernet",
    "hyper-v",
    "wsl",
    "docker",
    "vmnet",
    "vmware",
    "vbox",
    "virtualbox",
    "virbr",
    "loopback",
    "teredo",
    "isatap",
)
_VPN_HINTS = (
    ("radmin", "radmin"),
    ("tailscale", "tailscale"),
    ("zerotier", "zerotier"),
    ("wireguard", "wireguard"),
    ("wg", "wireguard"),
    ("hamachi", "hamachi"),
)
_GPU_SCORE = {
    "4090": 0.95, "4080": 0.90, "4070": 0.80, "4060": 0.65,
    "3090": 0.80, "3080": 0.75, "3070": 0.65, "3060": 0.50, "3050": 0.40,
    "a100": 1.00, "h100": 1.00, "v100": 0.70, "l40": 0.85, "l4": 0.60,
    "5090": 1.00, "5080": 0.95, "5070": 0.85, "5060": 0.70, "5050": 0.50,
    "2080": 0.65, "2070": 0.55, "2060": 0.45,
    "1660": 0.30, "1650": 0.25,
    "1050": 0.20, "mx450": 0.15, "mx330": 0.12, "mx250": 0.10,
    "2050": 0.45,
    "t1200": 0.35, "t1000": 0.30, "quadro": 0.40, "nvs": 0.20,
    "apple": 0.55, "m1": 0.45, "m2": 0.55, "m3": 0.60, "m4": 0.65,
    "integrated": 0.10, "uhd": 0.05, "iris": 0.08,
}
_HOST_RE = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$")
_SAFE_NODE_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,64}$")
def active_http_port():
    try:
        p = int(os.environ.get("SHARD_PORT") or DEFAULT_MASTER_PORT)
        if 1 <= p <= 65535:
            return p
    except Exception:
        pass
    return DEFAULT_MASTER_PORT
def data_dir():
    d = str(_paths.data_dir())
    os.makedirs(d, exist_ok=True)
    return d
def lan_file():
    p = _paths.lan_file()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p
def normalize_code(raw):
    s = "".join(ch for ch in str(raw or "").upper() if ch.isalnum())
    if len(s) == PAIR_LEN * 2 and all(ch in PAIR_ALPHABET for ch in s):
        return f"{s[:PAIR_LEN]}-{s[PAIR_LEN:]}"
    return None
def gen_pairing_code():
    parts = ["".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_LEN)) for _ in range(2)]
    return "-".join(parts)
def new_member_token():
    return TOKEN_PREFIX + secrets.token_hex(8)
def is_member_token(token):
    t = str(token or "")
    return t.startswith(TOKEN_PREFIX) and len(t) == len(TOKEN_PREFIX) + 16 and all(c in "0123456789abcdef" for c in t[len(TOKEN_PREFIX):])
def is_loopback(ip):
    ip = str(ip or "").strip().strip("[]")
    return ip == "::1" or ip.startswith("127.") or ip == "localhost"
def _to_int(v):
    try:
        return int(float(v))
    except Exception:
        return 0
def device_budget(vram_free_mb, ram_free_mb):
    vram = max(0, int(vram_free_mb or 0) - VRAM_RESERVE_MB)
    ram = max(0, int(ram_free_mb or 0) - RAM_RESERVE_MB)
    return vram, ram
def split_weights(budgets):
    out = []
    for vram, ram in budgets:
        w = int(vram) if int(vram) > 0 else int(ram)
        out.append(max(0, w))
    if not any(out):
        return [1] * len(out)
    return out
def speed_score(hw):
    if not hw:
        return None
    raw_name = hw.get("gpu_name")
    vram = _to_int(hw.get("vram_total_mb"))
    ram = _to_int(hw.get("ram_total_mb"))
    if raw_name is None and vram <= 0 and ram <= 0:
        return None
    name = str(raw_name or "").lower()
    has_gpu = bool(name) and not ("none" in name or name in ("cpu", "-", "n/a", "unknown"))
    if has_gpu:
        score = None
        for kw, s in _GPU_SCORE.items():
            if kw in name:
                score = s
                break
        if score is None:
            score = 0.40
        score += min(0.15, max(0, vram) / 24576.0)
    else:
        score = 0.10
        if ram >= 32768:
            score += 0.05
    return round(min(1.0, max(0.0, score)), 3)
def split_weights_scored(budgets, scores):
    if any(s is None for s in scores):
        return split_weights(budgets)
    out = [int(b_v if b_v > 0 else b_r) * s for (b_v, b_r), s in zip(budgets, scores)]
    out = [int(round(w)) for w in out]
    if not any(out):
        return [1] * len(out)
    return out
def format_ts_arg(weights):
    return ",".join(str(int(w)) for w in weights)
def format_rpc_arg(addrs):
    return ",".join(str(a) for a in addrs)
def member_alive(member, now=None):
    now = time.time() if now is None else now
    last = float((member or {}).get("last_seen") or 0)
    return (now - last) <= HEARTBEAT_MISS_LIMIT * HEARTBEAT_INTERVAL_S
def beacon_payload(name, role, model=None, port=DEFAULT_MASTER_PORT, open_net=False, node_id=None):
    out = {
        "app": APP_TAG,
        "ver": PROTO_VER,
        "name": str(name)[:64],
        "role": role,
        "model": model,
        "port": int(port),
        "open": bool(open_net),
        "ts": time.time(),
    }
    if node_id:
        out["node_id"] = str(node_id)[:64]
    return out
def parse_beacon(raw):
    try:
        d = json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    if d.get("app") != APP_TAG or d.get("ver") != PROTO_VER:
        return None
    name = str(d.get("name") or "").strip()
    if not name or len(name) > 64:
        return None
    role = str(d.get("role") or "server")
    if role not in ("server", "worker", "member"):
        return None
    try:
        port = int(d.get("port") or DEFAULT_MASTER_PORT)
        if not (1 <= port <= 65535):
            return None
    except Exception:
        return None
    nid = str(d.get("node_id") or "").strip()
    if nid and not _SAFE_NODE_RE.match(nid):
        nid = ""
    out = {
        "app": APP_TAG,
        "ver": PROTO_VER,
        "name": name,
        "role": role,
        "model": str(d.get("model"))[:160] if d.get("model") else None,
        "port": port,
        "open": bool(d.get("open")),
        "ts": float(d.get("ts") or time.time()),
    }
    if nid:
        out["node_id"] = nid
    return out
def _is_usable_lan_ipv4(addr):
    try:
        ip_obj = ipaddress.ip_address(str(addr))
        if ip_obj.version != 4 or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_unspecified or ip_obj.is_multicast:
            return False
        return True
    except ValueError:
        return False
def _classify_iface(iface_name, ip_str, netmask=None, is_default=False):
    low = str(iface_name or "").lower()
    ip_obj = ipaddress.IPv4Address(ip_str)
    kind = "lan"
    provider = None
    for hint, prov in _VPN_HINTS:
        if hint in low:
            kind = "vpn"
            provider = prov
            break
    if kind == "lan":
        if ip_obj in ipaddress.IPv4Network("26.0.0.0/8"):
            kind = "vpn"
            provider = "radmin"
        elif ip_obj in ipaddress.IPv4Network("100.64.0.0/10"):
            kind = "vpn"
            provider = "tailscale"
        elif ip_obj in ipaddress.IPv4Network("25.0.0.0/8"):
            kind = "vpn"
            provider = "hamachi"
    if kind == "lan" and any(h in low for h in _VIRTUAL_IFACE_HINTS):
        kind = "virtual"
        provider = "virtual"
    bcast = None
    cidr = None
    if netmask:
        try:
            net_if = ipaddress.IPv4Interface(f"{ip_str}/{netmask}")
            cidr = str(net_if.network)
            if net_if.network.prefixlen < 31:
                bcast = str(net_if.network.broadcast_address)
        except Exception:
            bcast = None
    if bcast is None:
        bcast = ip_str.rsplit(".", 1)[0] + ".255"
    return {
        "ip": ip_str,
        "interface": str(iface_name or "unknown"),
        "kind": kind,
        "provider": provider or kind,
        "cidr": cidr,
        "broadcast": bcast,
        "is_default": bool(is_default),
    }
def _default_route_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        return ip if _is_usable_lan_ipv4(ip) else None
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass
def _route_local_ip_for(target_host):
    if not target_host or is_loopback(target_host):
        return "127.0.0.1"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((str(target_host), 8000))
        ip = s.getsockname()[0]
        if _is_usable_lan_ipv4(ip) or is_loopback(ip):
            return ip
    except Exception:
        pass
    finally:
        try:
            s.close()
        except Exception:
            pass
    return None
def local_interfaces():
    def_ip = _default_route_ip()
    entries = {}
    try:
        import psutil
        stats = {}
        try:
            stats = psutil.net_if_stats()
        except Exception:
            stats = {}
        addrs = psutil.net_if_addrs()
        for iface, items in addrs.items():
            st = stats.get(iface)
            if st is not None and not st.isup:
                continue
            for item in items:
                if getattr(item, "family", None) == socket.AF_INET:
                    addr = str(getattr(item, "address", "") or "").strip()
                    if not _is_usable_lan_ipv4(addr):
                        continue
                    mask = getattr(item, "netmask", None)
                    info = _classify_iface(iface, addr, netmask=mask, is_default=(addr == def_ip))
                    if addr not in entries or info["kind"] != "virtual":
                        entries[addr] = info
    except Exception:
        pass
    if def_ip and def_ip not in entries:
        entries[def_ip] = _classify_iface("default", def_ip, is_default=True)
    try:
        for res in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = str(res[4][0] or "").strip()
            if _is_usable_lan_ipv4(addr) and addr not in entries:
                entries[addr] = _classify_iface("host", addr, is_default=(addr == def_ip))
    except Exception:
        pass
    rank = {"lan": 0, "vpn": 1, "virtual": 2}
    ordered = sorted(
        entries.values(),
        key=lambda x: (
            rank.get(x["kind"], 3),
            0 if x["is_default"] else 1,
            0 if x["ip"].startswith("192.168.") else 1,
            x["ip"],
        ),
    )
    return ordered
def local_ips():
    ifaces = local_interfaces()
    real = [x["ip"] for x in ifaces if x["kind"] != "virtual"]
    if real:
        return real
    return [x["ip"] for x in ifaces]
def local_ip():
    ips = local_ips()
    if ips:
        return ips[0]
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if _is_usable_lan_ipv4(ip):
            return ip
    except Exception:
        pass
    return "127.0.0.1"
def _broadcast_targets():
    seen = set()
    targets = []
    for item in local_interfaces():
        if item["kind"] == "virtual":
            continue
        src = item["ip"]
        bcast = item.get("broadcast")
        if bcast and (src, bcast) not in seen:
            seen.add((src, bcast))
            targets.append((src, bcast))
    for bcast in ("255.255.255.255", "<broadcast>"):
        if ("", bcast) not in seen:
            seen.add(("", bcast))
            targets.append(("", bcast))
    return targets
def detect_tailscale():
    exe = shutil.which("tailscale")
    if not exe and os.name == "nt":
        cand = r"C:\Program Files\Tailscale\tailscale.exe"
        if os.path.isfile(cand):
            exe = cand
    if not exe:
        return {"installed": False, "running": False, "ip": None, "dns_name": None, "online_peers": 0}
    try:
        r = subprocess.run(
            [exe, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=2.5,
            creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return {"installed": True, "running": False, "ip": None, "dns_name": None, "online_peers": 0}
        data = json.loads(r.stdout)
        self_info = data.get("Self") or {}
        ts_ips = [ip for ip in (self_info.get("TailscaleIPs") or []) if "." in str(ip)]
        peers = data.get("Peer") or {}
        online = sum(1 for p in peers.values() if isinstance(p, dict) and p.get("Online"))
        return {
            "installed": True,
            "running": str(data.get("BackendState") or "").lower() == "running",
            "state": data.get("BackendState"),
            "ip": ts_ips[0] if ts_ips else None,
            "dns_name": (self_info.get("DNSName") or "").rstrip(".") or None,
            "online_peers": online,
        }
    except Exception:
        return {"installed": True, "running": False, "ip": None, "dns_name": None, "online_peers": 0}
def stun_probe(timeout=2.0):
    servers = [("stun.l.google.com", 19302), ("stun.cloudflare.com", 3478)]
    tx_id = secrets.token_bytes(12)
    magic = 0x2112A442
    req = struct.pack("!HHI12s", 0x0001, 0, magic, tx_id)
    for host, port in servers:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(req, (host, port))
            data, _ = sock.recvfrom(1024)
            if len(data) < 20:
                continue
            msg_type, msg_len, r_magic, r_tx = struct.unpack("!HHI12s", data[:20])
            if msg_type != 0x0101 or r_magic != magic or r_tx != tx_id:
                continue
            pos = 20
            end = min(len(data), 20 + msg_len)
            while pos + 4 <= end:
                attr_type, attr_len = struct.unpack("!HH", data[pos:pos + 4])
                pos += 4
                val = data[pos:pos + attr_len]
                pos += (attr_len + 3) & ~3
                if attr_type == 0x0020 and len(val) >= 8 and val[1] == 0x01:
                    xport = struct.unpack("!H", val[2:4])[0] ^ (magic >> 16)
                    xip = struct.unpack("!I", val[4:8])[0] ^ magic
                    return {"ok": True, "public_ip": str(ipaddress.IPv4Address(xip)), "public_port": xport, "server": host}
                if attr_type == 0x0001 and len(val) >= 8 and val[1] == 0x01:
                    p = struct.unpack("!H", val[2:4])[0]
                    ip_raw = struct.unpack("!I", val[4:8])[0]
                    return {"ok": True, "public_ip": str(ipaddress.IPv4Address(ip_raw)), "public_port": p, "server": host}
        except Exception:
            continue
        finally:
            try:
                sock.close()
            except Exception:
                pass
    return {"ok": False, "public_ip": None, "public_port": None, "server": None}
def upnp_igd_probe(timeout=1.2):
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        "MAN: \"ssdp:discover\"\r\n"
        "MX: 1\r\n"
        "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n"
    ).encode("ascii")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(msg, ("239.255.255.250", 1900))
        data, addr = sock.recvfrom(2048)
        text = data.decode("utf-8", "ignore")
        if "HTTP/1.1 200" in text or "InternetGatewayDevice" in text:
            return {"found": True, "gateway_ip": addr[0]}
    except Exception:
        pass
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return {"found": False, "gateway_ip": None}
def connectivity_report(probe_public=False):
    ifaces = local_interfaces()
    lan_list = [x for x in ifaces if x["kind"] == "lan"]
    vpn_list = [x for x in ifaces if x["kind"] == "vpn"]
    virt_list = [x for x in ifaces if x["kind"] == "virtual"]
    ts = detect_tailscale()
    port = active_http_port()
    report = {
        "port": port,
        "beacon_port": BEACON_PORT,
        "lan_interfaces": lan_list,
        "vpn_interfaces": vpn_list,
        "virtual_excluded": virt_list,
        "tailscale": ts,
        "recommended_join_ip": (vpn_list[0]["ip"] if vpn_list else (lan_list[0]["ip"] if lan_list else local_ip())),
        "internet_modes": [
            {
                "mode": "mesh_vpn",
                "ready": bool(vpn_list or ts.get("running")),
                "providers": sorted(set([x["provider"] for x in vpn_list] + (["tailscale"] if ts.get("running") else []))),
                "note": "Cross-country / behind-CGNAT connectivity works directly via Radmin VPN, Tailscale, or ZeroTier mesh IP without router port forwarding.",
            },
            {
                "mode": "direct_port_forward",
                "ready": False,
                "note": f"Requires router port forwarding of TCP {port} (or public VPS/domain) plus firewall rule allowing TCP {port}.",
            },
            {
                "mode": "nat_traversal_built_in",
                "ready": True,
                "note": "Built-in SHARD Network encrypted tunnel (Ed25519 + X25519 + ChaCha20-Poly1305) supports direct P2P and relay transport plus optional WireGuard mesh.",
            },
        ],
    }
    if probe_public:
        stun = stun_probe()
        upnp = upnp_igd_probe()
        local_ip_set = {x["ip"] for x in ifaces}
        behind_nat = bool(stun.get("ok") and stun.get("public_ip") and stun["public_ip"] not in local_ip_set)
        report["stun"] = stun
        report["upnp_igd"] = upnp
        report["behind_nat"] = behind_nat
    return report
def validate_master_host(host):
    raw = str(host or "").strip()
    if not raw:
        return None, "Нужен IP мастера (или выберите сервер из списка)"
    if raw.startswith(("http://", "https://")):
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0].strip()
    if raw.startswith("[") and "]" in raw:
        raw = raw[1:raw.index("]")]
    elif ":" in raw and raw.count(":") == 1:
        raw = raw.split(":", 1)[0].strip()
    if not raw:
        return None, "Укажите корректный IP или hostname мастера"
    try:
        ip_obj = ipaddress.ip_address(raw)
        if ip_obj.is_unspecified or ip_obj.is_multicast or ip_obj.is_link_local:
            return None, f"Некорректный адрес мастера: {raw}"
        return str(ip_obj), None
    except ValueError:
        pass
    if all(ch.isdigit() or ch == "." for ch in raw):
        return None, f"Некорректный IPv4-адрес мастера: {raw}"
    if _HOST_RE.match(raw):
        return raw, None
    return None, f"Некорректный адрес мастера: {raw}"
def find_rpc_server():
    env_rpc = os.environ.get("SHARD_RPC_SERVER")
    if env_rpc and os.path.isfile(env_rpc):
        return env_rpc
    names = ["ggml-rpc-server.exe"] if os.name == "nt" else ["ggml-rpc-server", "llama-rpc-server"]
    here = os.path.dirname(os.path.abspath(__file__))
    search_dirs = [
        _paths.runtime_dir(),
        os.path.join(os.path.expanduser("~"), ".shard", "runtime"),
        os.path.join(here, "native"),
        os.path.join(os.path.dirname(here), "native"),
