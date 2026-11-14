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
        os.path.join(os.getcwd(), "native"),
    ]
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for exe_name in names:
            p = os.path.join(d, exe_name)
            if os.path.isfile(p):
                return p
            hits = glob.glob(os.path.join(d, "**", exe_name), recursive=True)
            if hits:
                return hits[0]
    for exe_name in names:
        which = shutil.which(exe_name)
        if which:
            return which
    return None
def worker_cmd(port, exe=None, host="0.0.0.0"):
    exe = exe or find_rpc_server()
    if not exe:
        raise RuntimeError(
            f"{RPC_EXE} не найден в {_paths.runtime_dir()} — "
            "установите llama.cpp runtime (или запустите модель с GGUF) "
            "либо подключитесь как обычный участник без --share")
    bind_host = str(host or "0.0.0.0").strip() or "0.0.0.0"
    return [exe, "--host", bind_host, "--port", str(int(port))]
class LanManager:
    def __init__(self, state_path=None, start_background=False):
        self._state_path = state_path
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._runtime_stop = threading.Event()
        self._threads = {}
        self._listen_thread = None
        self._runtime_started = False
        self._peers = {}
        self._rpc_proc = None
        self._hb_failures = 0
        self._hb_state = "idle"
        self._last_hb_save = 0.0
        self._hw_peak = {}
        self._rebal_note = None
        self._rebal_err_ticks = 0
        self._rpc_applied = None
        self._state = self._load()
        if start_background:
            self.start_runtime()
    def _default_state(self):
        return {
            "mode": "off",
            "name": socket.gethostname() or "shard",
            "node_id": secrets.token_hex(8),
            "pairing_code": None,
            "master": None,
            "members": [],
            "blocked": [],
            "open": False,
            "created_at": None,
            "target_model": None,
            "rpc_port": None,
        }
    def _load(self):
        p = self._state_path or lan_file()
        default = self._default_state()
        with _storage.file_lock(p):
            d = _storage.read_json_safe(p, default=default, expected_type=dict, quarantine=True, use_backup=True)
            if not isinstance(d, dict):
                return default
            for k, v in default.items():
                d.setdefault(k, v)
            if not isinstance(d.get("members"), list):
                d["members"] = []
            if not isinstance(d.get("blocked"), list):
                d["blocked"] = []
            if not d.get("node_id"):
                d["node_id"] = secrets.token_hex(8)
            return d
    def _save(self):
        with self._lock:
            snap = json.loads(json.dumps(self._state, ensure_ascii=False))
        p = self._state_path or lan_file()
        try:
            _storage.atomic_write_json(p, snap, indent=2, fsync=True, backup=True, restrict=True)
        except Exception:
            pass
    def start_runtime(self):
        with self._lock:
            if self._runtime_started:
                return
            self._runtime_started = True
            self._runtime_stop.clear()
            if self._listen_thread is None or not self._listen_thread.is_alive():
                self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
                self._listen_thread.start()
            mode = self._state.get("mode")
        if mode in ("server", "worker", "member"):
            threading.Thread(target=self._resume, daemon=True).start()
    def stop_runtime(self):
        with self._lock:
            self._runtime_started = False
            self._stop.set()
            self._runtime_stop.set()
            threads = list(self._threads.values())
            lt = self._listen_thread
        if self._rpc_proc is not None:
            self._stop_worker()
        for t in threads:
            if t.is_alive():
                t.join(timeout=1.5)
        if lt is not None and lt.is_alive():
            lt.join(timeout=1.5)
        self._save()
    def mode(self):
        with self._lock:
            return self._state.get("mode")
    def node_id(self):
        with self._lock:
            return self._state.get("node_id")
    def is_server(self):
        return self.mode() == "server"
    def is_member(self):
        return self.mode() in ("member", "worker")
    def name(self):
        with self._lock:
            return self._state.get("name")
    def tokens(self):
        with self._lock:
            return {m["token"] for m in self._state.get("members", []) if m.get("token")}
    def pairing_code(self):
        with self._lock:
            return self._state.get("pairing_code")
    def pool_active(self):
        if not self.is_server():
            return False
        now = time.time()
        with self._lock:
            return any(m.get("role") == "worker" and m.get("rpc_port") and member_alive(m, now) for m in self._state.get("members", []))
    def _record_hw_peak(self, key, vram_free_mb, ram_avail_mb):
        try:
            v = int(float(vram_free_mb or 0))
            r = int(float(ram_avail_mb or 0))
        except Exception:
            return
        with self._lock:
            peak = self._hw_peak.setdefault(key, {"vram": 0, "ram": 0})
            peak["vram"] = max(peak["vram"], max(0, v))
            peak["ram"] = max(peak["ram"], max(0, r))
    def _pool_snapshot(self):
        now = time.time()
        with self._lock:
            workers = [dict(m) for m in self._state.get("members", []) if m.get("role") == "worker" and m.get("rpc_port") and member_alive(m, now)]
        local = {}
        try:
            local = _hardware.get_hardware_profile()
        except Exception:
            local = {}
        if workers:
            self._record_hw_peak("master", local.get("vram_free_mb"), local.get("ram_avail_mb"))
            with self._lock:
                pk = dict(self._hw_peak.get("master") or {})
            budgets = [device_budget(pk.get("vram"), pk.get("ram"))]
        else:
            budgets = [device_budget(local.get("vram_free_mb"), local.get("ram_avail_mb"))]
        scores = [speed_score(local) if local else None]
        for m in workers:
            hw = m.get("hw") or {}
            pk_key = m.get("client_id") or m["ip"]
            self._record_hw_peak(pk_key, hw.get("vram_free_mb"), hw.get("ram_avail_mb"))
            with self._lock:
                pk = dict(self._hw_peak.get(pk_key) or {})
            budgets.append(device_budget(pk.get("vram"), pk.get("ram")))
            scores.append(speed_score(hw) if hw else None)
        return workers, budgets, scores
    def rpc_targets(self):
        if not self.pool_active():
            return None
        workers, budgets, scores = self._pool_snapshot()
        addrs = [f"{(m.get('rpc_host') or m['ip'])}:{int(m['rpc_port'])}" for m in workers]
        return {"servers": format_rpc_arg(addrs), "split": format_ts_arg(split_weights_scored(budgets, scores))}
    def network_summary(self):
        mod = sys.modules.get("shard_engine.net_strategy")
        if mod is None:
            return None
        mgr = getattr(mod, "_DEFAULT_MANAGER", None)
        if mgr is None:
            return None
        try:
            st = mgr.status()
            host = st.get("host") or {}
            return {
                "node_id": st.get("node_id"),
                "state": st.get("state"),
                "mode": st.get("mode"),
                "local_url": st.get("local_url"),
                "hosting": bool(host.get("published") or host.get("running")),
                "offline_only": bool(st.get("offline_only")),
            }
        except Exception:
            return None
    def pool_capacity_mb(self):
        _, budgets, _ = self._pool_snapshot()
        return sum(int(v) if int(v) > 0 else int(r) for v, r in budgets)
    def status(self, include_secrets=True):
        now = time.time()
        with self._lock:
            st = json.loads(json.dumps(self._state, ensure_ascii=False))
            members_out = []
            for m in st.get("members", []):
                m["alive"] = member_alive(m, now)
                m["token"] = None
                members_out.append(m)
            peers = []
            for key, info in list(self._peers.items()):
                if (now - info.get("_ts", 0)) > PEER_TTL_S:
                    continue
                peers.append({
                    "ip": info.get("_ip") or key,
                    "name": info.get("name"),
                    "role": info.get("role"),
                    "model": info.get("model"),
                    "port": info.get("port"),
                    "open": bool(info.get("open")),
                })
        ifaces = local_interfaces()
        host_ips = [x["ip"] for x in ifaces if x["kind"] != "virtual"] or [x["ip"] for x in ifaces]
        primary_ip = host_ips[0] if host_ips else local_ip()
        master_info = st.get("master")
        if isinstance(master_info, dict) and not include_secrets:
            master_info = {
                "ip": master_info.get("ip"),
                "port": master_info.get("port"),
                "name": master_info.get("name"),
            }
        rpc = self.rpc_targets()
        out = {
            "mode": st.get("mode"),
            "name": st.get("name"),
            "ip": primary_ip,
            "ips": host_ips,
            "interfaces": [x for x in ifaces if x["kind"] != "virtual"] if include_secrets else [],
            "http_port": active_http_port(),
            "pairing_code": st.get("pairing_code") if include_secrets else None,
            "open": bool(st.get("open")),
            "master": master_info,
            "members": members_out,
            "peers": peers,
            "rpc": rpc,
            "target_model": st.get("target_model"),
            "heartbeat_state": self._hb_state,
            "rebalance_note": self._rebal_note,
            "constants": {
                "beacon_port": BEACON_PORT,
                "beacon_interval_s": BEACON_INTERVAL_S,
                "heartbeat_interval_s": HEARTBEAT_INTERVAL_S,
                "heartbeat_miss_limit": HEARTBEAT_MISS_LIMIT,
                "rebalance_interval_s": REBALANCE_INTERVAL_S,
            },
        }
        if st.get("mode") == "server":
            try:
                out["pool_capacity_mb"] = self.pool_capacity_mb()
            except Exception:
                pass
        net_sum = self.network_summary()
        if net_sum is not None:
            out["network"] = net_sum
        return out
    def build_invite(self):
        with self._lock:
            mode = self._state.get("mode")
            code = self._state.get("pairing_code")
            open_net = bool(self._state.get("open"))
            name = self._state.get("name")
        if mode != "server":
            return {"status": "error", "message": "Приглашение доступно только на сервере SHARD"}
        port = active_http_port()
        ifaces = [x for x in local_interfaces() if x["kind"] != "virtual"]
        if not ifaces:
            ifaces = local_interfaces()
        targets = []
        for x in ifaces:
            ip = x["ip"]
            cmd = f"shard lan join {ip}" + ("" if open_net else f" --code {code}") + (f" --port {port}" if port != DEFAULT_MASTER_PORT else "")
            targets.append({
                "ip": ip,
                "kind": x["kind"],
                "provider": x["provider"],
                "interface": x["interface"],
                "url": f"http://{ip}:{port}/",
                "cli_command": cmd,
            })
        return {
            "status": "ok",
            "name": name,
            "port": port,
            "open": open_net,
            "pairing_code": code,
            "targets": targets,
        }
    def create(self, name=None, open_net=False, model=None):
        with self._lock:
            if self._state.get("mode") == "server":
                if model:
                    return self.set_model(model)
                return self.status()
        self.off(quiet=True)
        tm = str(model).strip() if model else None
        with self._lock:
            self._state.update({
                "mode": "server",
                "name": (name or self._state.get("name") or socket.gethostname() or "shard")[:64],
                "pairing_code": gen_pairing_code(),
                "members": [],
                "master": None,
                "open": bool(open_net),
                "created_at": time.time(),
                "target_model": tm or None,
            })
            self._hb_state = "server"
            self._save()
        self._start_threads("server")
        return self.status()
    def set_model(self, model_id):
        model_id = str(model_id or "").strip()
        with self._lock:
            if self._state.get("mode") != "server":
                return {"status": "error", "message": "Модель задаётся только на сервере shard-сети"}
            if not model_id:
                return {"status": "error", "message": "Пустое имя модели"}
            self._state["target_model"] = model_id
            self._rebal_note = None
            self._rebal_err_ticks = 0
            self._save()
            loaded = self._loaded_model_name()
        if loaded == model_id:
            msg = f"Модель сети: {model_id} (уже загружена)"
        else:
            msg = f"Модель сети: {model_id} — загрузится автоматически, как только подключится вычислительный участник (--share)"
        return {"status": "ok", "target_model": model_id, "message": msg}
    def set_open(self, open_net):
        with self._lock:
            if self._state.get("mode") != "server":
                return {"status": "error", "message": "Сеть не создана — открыть можно только свою сеть"}
            self._state["open"] = bool(open_net)
            self._save()
        if open_net:
            return {"status": "ok", "open": True, "message": "Сеть открыта: подключение без кода"}
        return {"status": "ok", "open": False, "message": "Сеть закрыта: нужен код подключения"}
    def handle_pair(self, code, ip, name=None, share=False, client_id=None, transport=None, rpc_host=None, tunnel_peer=None):
        code = normalize_code(code)
        tpeer = str(tunnel_peer or "").strip()[:80] or None
        if tpeer and not _SAFE_NODE_RE.match(tpeer):
            tpeer = None
        cid = str(client_id or "").strip()[:64] or None
        if cid and not _SAFE_NODE_RE.match(cid):
            cid = None
        if not cid and tpeer:
            cid = tpeer[:64]
        clean_rpc_host = None
        if rpc_host:
            cand_host, cand_err = validate_master_host(rpc_host)
            if not cand_err and cand_host:
                clean_rpc_host = cand_host
        clean_transport = str(transport or ("tunnel" if tpeer else "lan")).strip().lower()[:24] or "lan"
        clean_name = (str(name or "участник").strip() or "участник")[:64]
        with self._lock:
            if self._state.get("mode") != "server":
                return None, "Эта машина не является сервером shard-сети"
            blocked = set(self._state.get("blocked") or [])
            if ip in blocked or (cid and cid in blocked) or (tpeer and tpeer in blocked):
                return None, "Доступ к этому серверу заблокирован администратором"
            expected = self._state.get("pairing_code") or ""
            if not self._state.get("open"):
                if not code or not expected or not secrets.compare_digest(code, expected):
                    return None, "Неверный код подключения"
            for m in self._state.get("members", []):
                same_member = (tpeer and m.get("tunnel_peer") == tpeer) or (cid and m.get("client_id") == cid) or ((not cid) and (not tpeer) and (not m.get("client_id")) and m.get("ip") == ip)
                if same_member:
                    m["ip"] = ip
                    m["name"] = clean_name or m.get("name") or "участник"
                    m["role"] = "worker" if share else m.get("role", "member")
                    m["last_seen"] = time.time()
                    m["transport"] = clean_transport
                    if clean_rpc_host:
                        m["rpc_host"] = clean_rpc_host
                    if tpeer:
                        m["tunnel_peer"] = tpeer
                    self._save()
                    return {"token": m["token"], "master": self._state.get("name"), "client_id": m.get("client_id")}, None
            token = new_member_token()
            member = {
                "ip": ip,
                "client_id": cid,
                "name": clean_name,
                "token": token,
                "role": "worker" if share else "member",
                "joined_at": time.time(),
                "last_seen": time.time(),
                "hw": None,
                "rpc_port": None,
                "transport": clean_transport,
            }
            if clean_rpc_host:
                member["rpc_host"] = clean_rpc_host
            if tpeer:
                member["tunnel_peer"] = tpeer
            self._state["members"].append(member)
            self._save()
            return {"token": token, "master": self._state.get("name"), "client_id": cid}, None
    def handle_heartbeat(self, ip, token, hw=None, rpc_port=None, client_id=None, transport=None, rpc_host=None, tunnel_peer=None):
        tok = str(token or "")
        if not tok:
            return False
        tpeer = str(tunnel_peer or "").strip()[:80] or None
        if tpeer and not _SAFE_NODE_RE.match(tpeer):
            tpeer = None
        cid = str(client_id or "").strip()[:64] or None
        clean_rpc_host = None
        if rpc_host:
            cand_host, cand_err = validate_master_host(rpc_host)
            if not cand_err and cand_host:
                clean_rpc_host = cand_host
        now = time.time()
        need_save = False
        with self._lock:
            if self._state.get("mode") != "server":
                return False
            blocked = set(self._state.get("blocked") or [])
            if ip in blocked or (cid and cid in blocked) or (tpeer and tpeer in blocked):
                return False
            for m in self._state.get("members", []):
                mtok = str(m.get("token") or "")
                if not mtok or not secrets.compare_digest(mtok, tok):
                    continue
                if m.get("tunnel_peer"):
                    if tpeer and m.get("tunnel_peer") != tpeer:
                        continue
                    if not tpeer and not (cid and m.get("client_id") == cid) and m.get("ip") != ip:
                        continue
                elif m.get("client_id"):
                    if cid and m.get("client_id") != cid:
                        continue
                    if not cid and m.get("ip") != ip:
                        continue
                else:
                    if m.get("ip") != ip:
                        continue
                if m.get("ip") != ip:
                    m["ip"] = ip
                    need_save = True
                if transport:
                    clean_t = str(transport).strip().lower()[:24]
                    if clean_t and m.get("transport") != clean_t:
                        m["transport"] = clean_t
                        need_save = True
                if tpeer and m.get("tunnel_peer") != tpeer:
                    m["tunnel_peer"] = tpeer
                    need_save = True
                if clean_rpc_host and m.get("rpc_host") != clean_rpc_host:
                    m["rpc_host"] = clean_rpc_host
                    need_save = True
                m["last_seen"] = now
                if hw and isinstance(hw, dict):
                    if not m.get("hw"):
                        need_save = True
                    m["hw"] = hw
                    pk_key = m.get("client_id") or m["ip"]
                    self._record_hw_peak(pk_key, hw.get("vram_free_mb"), hw.get("ram_avail_mb"))
                if rpc_port:
                    rp = int(rpc_port)
                    if 1 <= rp <= 65535 and m.get("rpc_port") != rp:
                        m["rpc_port"] = rp
                        need_save = True
                if not need_save and (now - self._last_hb_save) >= 30.0:
                    need_save = True
                if need_save:
                    self._last_hb_save = now
                    self._save()
                return True
            return False
    def handle_leave(self, ip, token, client_id=None, tunnel_peer=None):
        tok = str(token or "")
        if not tok:
            return False
        tpeer = str(tunnel_peer or "").strip()[:80] or None
        cid = str(client_id or "").strip()[:64] or None
        with self._lock:
            before = len(self._state.get("members", []))
            kept = []
            removed_keys = []
            for m in self._state.get("members", []):
                mtok = str(m.get("token") or "")
                tok_match = bool(mtok) and secrets.compare_digest(mtok, tok)
                if m.get("tunnel_peer") and tpeer:
                    id_match = m.get("tunnel_peer") == tpeer
                elif cid and m.get("client_id"):
                    id_match = m.get("client_id") == cid
                else:
                    id_match = m.get("ip") == ip
                if tok_match and id_match:
                    removed_keys.append(m.get("client_id") or m.get("ip"))
                    continue
                kept.append(m)
            self._state["members"] = kept
            dropped = len(kept) < before
            if dropped:
                for rk in removed_keys:
                    self._hw_peak.pop(rk, None)
                self._save()
            return dropped
    def kick_member(self, target, block=False):
        tgt = str(target or "").strip()
        if not tgt:
            return {"status": "error", "message": "Не указан участник"}
        with self._lock:
            before = len(self._state.get("members", []))
            kept = []
            removed = []
            for m in self._state.get("members", []):
                matches = tgt in (m.get("client_id"), m.get("ip"), m.get("tunnel_peer"), m.get("name"))
                if matches:
                    removed.append(m.get("client_id") or m.get("ip") or tgt)
                    continue
                kept.append(m)
            self._state["members"] = kept
            if block:
                bl = self._state.setdefault("blocked", [])
                for rk in (removed or [tgt]):
                    if rk and rk not in bl:
                        bl.append(rk)
            for rk in removed:
                self._hw_peak.pop(rk, None)
            self._save()
        return {"status": "ok", "kicked": len(removed), "blocked": bool(block), "target": tgt}
    def unblock_member(self, target):
        tgt = str(target or "").strip()
        if not tgt:
            return {"status": "error", "message": "Не указан участник"}
        with self._lock:
            bl = self._state.get("blocked") or []
            was = tgt in bl
            self._state["blocked"] = [x for x in bl if x != tgt]
            self._save()
        return {"status": "ok", "unblocked": was, "target": tgt}
    def preview_info(self):
        now = time.time()
        with self._lock:
            mode = self._state.get("mode")
            name = self._state.get("name")
            nid = self._state.get("node_id")
            open_net = bool(self._state.get("open"))
            target_model = self._state.get("target_model")
            members = [dict(m) for m in self._state.get("members", [])]
        loaded = self._loaded_model_name()
        active_m = loaded or target_model
        alive_members = [m for m in members if member_alive(m, now)]
        workers = [m for m in alive_members if m.get("role") == "worker"]
        return {
            "is_server": mode == "server",
            "mode": mode,
            "server_name": name,
            "node_id": nid,
            "open": open_net,
            "access_mode": "open" if open_net else "pairing_code",
            "active_model": active_m,
            "target_model": target_model,
            "model_loaded": bool(loaded),
            "member_count": len(alive_members),
            "total_members": len(members),
            "worker_count": len(workers),
            "pool_capacity_mb": self.pool_capacity_mb() if mode == "server" else 0,
            "rpc_available_locally": bool(find_rpc_server()),
            "roles": {
                "member": {
                    "title": "Участник (Чат и API)",
                    "requires_model_download": False,
                    "shares_compute": False,
                    "description": "Вы получаете доступ к модели хоста, общему ИИ-чату, чату сообщества и API сервера. Модель скачивать не нужно, ваша видеокарта и ОЗУ не нагружаются.",
                },
                "worker": {
                    "title": "Вычислительный узел (Share Compute / RPC)",
                    "requires_model_download": False,
                    "shares_compute": True,
                    "description": "Запускается локальный ggml-rpc-server: вы выделяете свободную VRAM/RAM для распределённого запуска слоёв модели по TCP. Полную модель скачивать вручную не требуется — мастер-узел передаёт веса назначенных слоёв автоматически.",
                },
            },
            "privacy_notice": "Ваши личные файлы, ключи и локальные настройки остаются только на вашем ПК. Сервер видит только ваше имя, выбранную роль, пульс соединения и сообщения, отправленные вами на сервер.",
        }
    def join(self, ip, code, share=False, port=DEFAULT_MASTER_PORT, bind_host=None):
        clean_ip, ip_err = validate_master_host(ip)
        if ip_err:
            return {"status": "error", "message": ip_err}
        try:
            port_num = int(port)
            if not (1 <= port_num <= 65535):
                return {"status": "error", "message": "Некорректный порт мастера"}
        except Exception:
            return {"status": "error", "message": "Некорректный порт мастера"}
        import requests
        url = f"http://{clean_ip}:{port_num}/api/lan/pair"
        try:
            r = requests.post(
                url,
                json={"code": str(code or ""), "name": self.name(), "share": bool(share), "client_id": self.node_id()},
                timeout=5,
            )
        except Exception as e:
            return {"status": "error", "message": f"Мастер {clean_ip}:{port_num} недоступен: {str(e)[:120]}"}
        if r.status_code == 403:
            return {"status": "error", "message": "Сеть закрыта: нужен код подключения"}
        if r.status_code == 429:
            return {"status": "error", "message": "Слишком много попыток подключения — подождите и повторите"}
        if r.status_code != 200:
            return {"status": "error", "message": f"Мастер ответил {r.status_code}: {r.text[:120]}"}
        data = r.json()
        self.off(quiet=True)
        with self._lock:
            self._state.update({
                "mode": "worker" if share else "member",
                "master": {
                    "ip": clean_ip,
                    "port": port_num,
                    "token": data["token"],
                    "name": data.get("master") or clean_ip,
                    "client_id": data.get("client_id") or self.node_id(),
                },
                "created_at": time.time(),
            })
            self._hb_state = "connecting"
            self._save()
        warn = None
        if share:
            try:
                self._start_worker(master_ip=clean_ip, bind_host=bind_host)
            except Exception as e:
                warn = str(e)[:160]
                with self._lock:
                    self._state["mode"] = "member"
                    self._state["rpc_port"] = None
                    self._save()
        self._start_threads(self.mode())
        res = {
            "status": "ok",
            "mode": self.mode(),
            "master": data.get("master") or clean_ip,
            "master_ip": clean_ip,
        }
        if warn:
            res["warning"] = warn
        return res
    def join_via_tunnel(self, target, code="", share=False, mode="auto"):
        from urllib.parse import urlparse
        raw = str(target or "").strip()
        if not raw:
            return {"status": "error", "message": "Target is required"}
        if raw.startswith(("http://127.0.0.1:", "http://localhost:")):
            parsed = urlparse(raw)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or DEFAULT_MASTER_PORT
            return self.join(host, code=code, share=share, port=port, bind_host=("127.0.0.1" if share else None))
        from . import net_strategy
        mgr = net_strategy.get_network_manager()
        st = mgr.connect(raw, preferred_mode=mode)
        if not st.get("ok") or not st.get("local_url"):
            return {"status": "error", "message": st.get("last_error") or "Tunnel connection failed", "network": st}
        parsed = urlparse(st["local_url"])
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or DEFAULT_MASTER_PORT
        res = self.join(host, code=code, share=share, port=port, bind_host=("127.0.0.1" if share else None))
        res["network"] = st
        return res
    def _choose_worker_port(self):
        env_p = os.environ.get("SHARD_RPC_PORT")
        preferred = []
        if env_p:
            try:
                preferred.append(int(env_p))
            except Exception:
                pass
        preferred.append(DEFAULT_RPC_PORT)
        for cand in preferred:
            if not (1 <= cand <= 65535):
                continue
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind(("0.0.0.0", cand))
                return cand
            except OSError:
                continue
            finally:
                try:
                    s.close()
                except Exception:
                    pass
        return _free_port()
    def _start_worker(self, master_ip=None, bind_host=None):
        if self._rpc_proc is not None and self._rpc_proc.poll() is None:
            return
        port = self._choose_worker_port()
        with self._lock:
            mip = master_ip or ((self._state.get("master") or {}).get("ip"))
        eff_bind = str(bind_host or "").strip() or _route_local_ip_for(mip) or "0.0.0.0"
        cmd = worker_cmd(port, exe=find_rpc_server(), host=eff_bind)
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = _CREATE_NO_WINDOW
        self._rpc_proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **kw)
        with self._lock:
            self._state["rpc_port"] = port
            self._save()
        time.sleep(0.3)
        if self._rpc_proc.poll() is not None:
            tail = ""
            try:
                tail = (self._rpc_proc.stderr.read() or b"").decode("utf-8", "ignore")[-200:]
            except Exception:
                pass
            self._rpc_proc = None
            raise RuntimeError(f"ggml-rpc-server не запустился: {tail[:150]}")
    def _stop_worker(self):
        if self._rpc_proc is not None:
            try:
                self._rpc_proc.terminate()
            except Exception:
                pass
            try:
                self._rpc_proc.wait(timeout=5)
            except Exception:
                try:
                    self._rpc_proc.kill()
                except Exception:
                    pass
            self._rpc_proc = None
        with self._lock:
            self._state["rpc_port"] = None
    def off(self, quiet=False):
        with self._lock:
            mode = self._state.get("mode")
            master = dict(self._state.get("master") or {})
            threads = list(self._threads.values())
        self._stop.set()
        cur = threading.current_thread()
        for t in threads:
            if t is not cur and t.is_alive():
                t.join(timeout=3.0)
        with self._lock:
            self._threads = {}
        if mode == "worker":
            self._stop_worker()
        if mode in ("member", "worker"):
            token = master.get("token")
            if master.get("ip") and token:
                try:
                    import requests
                    requests.post(
                        f"http://{master['ip']}:{master.get('port', DEFAULT_MASTER_PORT)}/api/lan/leave",
                        json={"token": token, "client_id": master.get("client_id") or self.node_id()},
                        timeout=2,
                    )
                except Exception:
                    pass
        with self._lock:
            self._state.update({
                "mode": "off",
                "pairing_code": None,
                "members": [],
                "master": None,
                "created_at": None,
                "rpc_port": None,
                "open": False,
