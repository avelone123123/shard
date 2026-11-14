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
