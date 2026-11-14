import asyncio
import copy
import hashlib
import hmac
import ipaddress
import os
import secrets
import socket
import struct
import sys
import threading
import time
from urllib.parse import urlsplit
import requests
from . import lan
from . import net_discovery
from . import net_identity
from . import net_platform_linux
from . import net_platform_win
from . import net_tunnel
from . import net_wireguard
from . import paths
from . import storage
TunnelState = net_tunnel.TunnelState
DEFAULT_STUN_SERVERS = (
    ("stun.l.google.com", 19302),
    ("stun.cloudflare.com", 3478),
)
PATH_PRIORITY = ("same_machine", "direct_lan", "direct_p2p", "relay")
VALID_VISIBILITY = frozenset({"public", "private", "local"})
VALID_ACCESS_MODES = frozenset({"open", "invite_only", "owner_confirm"})
VALID_CONNECT_MODES = frozenset({"auto", "direct", "relay", "lan", "p2p"})
_INVITE_ID_RE = net_identity._INVITE_TOKEN_RE
_VPN_NETS = (
    ipaddress.IPv4Network("26.0.0.0/8"),
    ipaddress.IPv4Network("100.64.0.0/10"),
    ipaddress.IPv4Network("25.0.0.0/8"),
)
_LAN_NETS_V4 = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("169.254.0.0/16"),
)
_LAN_NETS_V6 = (
    ipaddress.IPv6Network("fc00::/7"),
    ipaddress.IPv6Network("fe80::/10"),
)
_STUN_MAGIC = 0x2112A442
_PUNCH_PREFIX = b"SHARD_PUNCH_v1:"
_PUNCH_ACK_PREFIX = b"SHARD_PUNCH_ACK_v1:"
def default_network_config():
    return {
        "version": 1,
        "discovery_urls": [],
        "relay_urls": [],
        "discovery_auth_tokens": {},
        "relay_auth_token": "",
        "stun_servers": [f"{h}:{p}" for h, p in DEFAULT_STUN_SERVERS],
        "offline_only": False,
        "allow_relay_fallback": True,
        "allow_p2p_direct": True,
        "direct_p2p_port": 0,
        "local_http_url": "",
        "region_hint": "",
        "display_name": "",
        "visibility": "private",
        "access_mode": "invite_only",
        "access_policy": "invite_only",
        "max_participants": 16,
        "max_peers": 16,
        "bandwidth_kbps": 0,
        "per_peer_rpm": 0,
        "wg_enabled": False,
        "wg_interface": "shard0",
        "published": False,
        "allow_insecure_local": False,
        "heartbeat_interval_s": 25.0,
        "listing_ttl_s": 60,
        "rpc_allowed_ports": [],
    }
def _default_peers_state():
    return {
        "version": 1,
        "tofu_pins": {},
        "connection_history": [],
        "authorized_peers": {},
        "pending_peers": {},
        "revoked_peers": {},
    }
def _normalize_access_mode(val):
    raw = str(val or "invite_only").strip().lower()
    if raw in ("invite", "invite_only", "invite-only"):
        return "invite_only"
    if raw in ("open", "owner_confirm"):
        return raw
    raise ValueError(f"Unsupported access_mode: {val}")
def _normalize_visibility(val):
    raw = str(val or "private").strip().lower()
    if raw not in VALID_VISIBILITY:
        raise ValueError(f"Unsupported visibility: {val}")
    return raw
def _parse_stun_servers(stun_servers):
    if stun_servers is None:
        return list(DEFAULT_STUN_SERVERS)
    out = []
    for item in stun_servers:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            host = str(item[0] or "").strip()
            port = int(item[1])
            if host and 1 <= port <= 65535:
                out.append((host, port))
            continue
        raw = str(item or "").strip()
        if not raw:
            continue
        if ":" in raw and raw.count(":") == 1:
            host, port_s = raw.rsplit(":", 1)
            port = int(port_s)
        else:
            host, port = raw, 3478
        host = host.strip()
        if host and 1 <= port <= 65535:
            out.append((host, port))
    return out or list(DEFAULT_STUN_SERVERS)
def _parse_stun_response(data, expected_tx):
    if len(data) < 20:
        return None
    msg_type, msg_len, r_magic, r_tx = struct.unpack("!HHI12s", data[:20])
    if msg_type != 0x0101 or r_magic != _STUN_MAGIC or r_tx != expected_tx:
        return None
    pos = 20
    end = min(len(data), 20 + msg_len)
    mapped_fallback = None
    while pos + 4 <= end:
        attr_type, attr_len = struct.unpack("!HH", data[pos:pos + 4])
        pos += 4
        if pos + attr_len > end:
            break
        val = data[pos:pos + attr_len]
        pos += (attr_len + 3) & ~3
        if attr_type == 0x0020 and len(val) >= 8 and val[1] == 0x01:
            xport = struct.unpack("!H", val[2:4])[0] ^ (_STUN_MAGIC >> 16)
            xip = struct.unpack("!I", val[4:8])[0] ^ _STUN_MAGIC
            return str(ipaddress.IPv4Address(xip)), int(xport)
        if attr_type == 0x0001 and len(val) >= 8 and val[1] == 0x01 and mapped_fallback is None:
            p = struct.unpack("!H", val[2:4])[0]
            ip_raw = struct.unpack("!I", val[4:8])[0]
            mapped_fallback = (str(ipaddress.IPv4Address(ip_raw)), int(p))
    return mapped_fallback
def stun_nat_profile(timeout=2.0, stun_servers=None, offline_only=False):
    if offline_only:
        return {
            "ok": False,
            "offline_only": True,
            "nat_type": "unknown",
            "symmetric_nat": None,
            "behind_nat": None,
            "public_ip": None,
            "public_port": None,
            "local_port": None,
            "endpoints": [],
            "reason": "offline_only",
        }
    servers = _parse_stun_servers(stun_servers)
    to_s = max(0.2, float(timeout))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if sys.platform.startswith("win") and hasattr(sock, "ioctl"):
        try:
            sock.ioctl(0x9800000C, False)
        except Exception:
            pass
    endpoints = []
    local_port = None
    try:
        sock.bind(("0.0.0.0", 0))
        local_port = sock.getsockname()[1]
        for host, port in servers:
            tx_id = secrets.token_bytes(12)
            req = struct.pack("!HHI12s", 0x0001, 0, _STUN_MAGIC, tx_id)
            t0 = time.perf_counter()
            ep_res = {
                "server": host,
                "port": int(port),
                "ok": False,
                "public_ip": None,
                "public_port": None,
                "rtt_ms": None,
                "error": None,
            }
            try:
                sock.sendto(req, (host, int(port)))
                deadline = time.monotonic() + to_s
                while True:
                    rem = deadline - time.monotonic()
                    if rem <= 0:
                        ep_res["error"] = "timeout"
                        break
                    sock.settimeout(rem)
                    data, _ = sock.recvfrom(2048)
                    parsed = _parse_stun_response(data, tx_id)
                    if parsed is not None:
                        ep_res["ok"] = True
                        ep_res["public_ip"] = parsed[0]
                        ep_res["public_port"] = parsed[1]
                        ep_res["rtt_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
                        break
            except socket.timeout:
                ep_res["error"] = "timeout"
            except Exception as exc:
                ep_res["error"] = f"{type(exc).__name__}:{exc}"
            endpoints.append(ep_res)
    except Exception as exc:
        return {
            "ok": False,
            "offline_only": False,
            "nat_type": "unknown",
            "symmetric_nat": None,
            "behind_nat": None,
            "public_ip": None,
