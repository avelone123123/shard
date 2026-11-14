import base64
import copy
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import struct
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from . import paths
from . import storage
PROTOCOL_DISC_V1 = "shard-disc-v1"
PROTOCOL_TUNNEL_V1 = 1
DOMAIN_DISC_REG = b"SHARD-DISC-REG-V1\x00"
DOMAIN_DISC_HB = b"SHARD-DISC-HB-V1\x00"
DOMAIN_DISC_UNREG = b"SHARD-DISC-UNREG-V1\x00"
DOMAIN_RELAY_REG = b"SHARD-RELAY-REG-V1\x00"
DOMAIN_TUNNEL_HOST = b"SHARD-TUNNEL-HOST-AUTH-V1\x00"
DOMAIN_TUNNEL_PEER = b"SHARD-TUNNEL-PEER-AUTH-V1\x00"
MSG_HS_INIT = 0x01
MSG_HS_RESP = 0x02
MSG_HS_AUTH = 0x03
MSG_HTTP_REQ = 0x10
MSG_HTTP_RESP_HEAD = 0x11
MSG_HTTP_BODY_CHUNK = 0x12
MSG_PING = 0x13
MSG_PONG = 0x14
MSG_CLOSE = 0x15
MSG_AUTH_ERR = 0x16
MSG_RPC_OPEN = 0x20
MSG_RPC_DATA = 0x21
MSG_RPC_CLOSE = 0x22
FLAG_FIN = 0x0001
FLAG_PADDED = 0x0002
NODE_ID_RE = re.compile(r"^shn_[0-9a-f]{40}$")
MAX_CLOCK_SKEW_S = 60
_B64U_RE = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")
_INVITE_TOKEN_RE = re.compile(r"^shinv1_([A-Za-z0-9-]{4,64})_([A-Za-z0-9-]{16,128})$")
_SAFE_LABEL_RE = re.compile(r"^[\x20-\x7e]{0,128}$")
_NONCANONICAL_IP_RE = re.compile(r"^(?:0x[0-9a-f]+|0[0-7]+|\d+)(?:\.(?:0x[0-9a-f]+|0[0-7]+|\d+)){0,3}$")
_FRAME_AAD_STRUCT = struct.Struct(">BBHIQ")
_ACTION_DOMAINS = {
    "register": DOMAIN_DISC_REG,
    "disc_reg": DOMAIN_DISC_REG,
    "disc.reg": DOMAIN_DISC_REG,
    "disc.register": DOMAIN_DISC_REG,
    "heartbeat": DOMAIN_DISC_HB,
    "disc_hb": DOMAIN_DISC_HB,
    "disc.hb": DOMAIN_DISC_HB,
    "disc.heartbeat": DOMAIN_DISC_HB,
    "unregister": DOMAIN_DISC_UNREG,
    "disc_unreg": DOMAIN_DISC_UNREG,
    "disc.unreg": DOMAIN_DISC_UNREG,
    "disc.unregister": DOMAIN_DISC_UNREG,
    "relay_register": DOMAIN_RELAY_REG,
    "relay_reg": DOMAIN_RELAY_REG,
    "relay.reg": DOMAIN_RELAY_REG,
    "relay.register": DOMAIN_RELAY_REG,
}
_DOMAIN_TO_ACTION = {
    DOMAIN_DISC_REG: "register",
    DOMAIN_DISC_HB: "heartbeat",
    DOMAIN_DISC_UNREG: "unregister",
    DOMAIN_RELAY_REG: "relay_register",
}
_BLOCKED_V4_NETS = (
    ipaddress.IPv4Network("0.0.0.0/8"),
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("100.64.0.0/10"),
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("169.254.0.0/16"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.0.0.0/24"),
    ipaddress.IPv4Network("192.0.2.0/24"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("198.18.0.0/15"),
    ipaddress.IPv4Network("198.51.100.0/24"),
    ipaddress.IPv4Network("203.0.113.0/24"),
    ipaddress.IPv4Network("224.0.0.0/4"),
    ipaddress.IPv4Network("240.0.0.0/4"),
)
_BLOCKED_V6_NETS = (
    ipaddress.IPv6Network("::/128"),
    ipaddress.IPv6Network("::1/128"),
    ipaddress.IPv6Network("::ffff:0:0/96"),
    ipaddress.IPv6Network("64:ff9b::/96"),
    ipaddress.IPv6Network("64:ff9b:1::/48"),
    ipaddress.IPv6Network("100::/64"),
    ipaddress.IPv6Network("2001::/32"),
    ipaddress.IPv6Network("2001:db8::/32"),
    ipaddress.IPv6Network("2002::/16"),
    ipaddress.IPv6Network("fc00::/7"),
    ipaddress.IPv6Network("fe80::/10"),
    ipaddress.IPv6Network("ff00::/8"),
)
_BLOCKED_HOST_EXACT = {"localhost", "metadata", "instance-data"}
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".arpa")
