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
