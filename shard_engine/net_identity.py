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
_PUBLIC_LISTING_ROLES = {"server", "worker", "relay", "host", "node"}
_SAFE_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
_SAFE_VER_RE = re.compile(r"^[A-Za-z0-9._+-]{1,32}$")
_SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}(?:/[A-Za-z0-9][A-Za-z0-9._:-]{0,95})?$")
_SECRET_SUBSTRINGS = ("shinv1_", "sk-shard-", "shard-owner-", "shard://", "privkey", "bearer ")
_PATH_HINT_RE = re.compile(r"(?:^[A-Za-z]:[\\/]|^\\\\|^~[\\/]|^/|\.\.[\\/]|[\\/]\.\.)")
def b64u_encode(raw: bytes) -> str:
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("raw must be bytes")
    return base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode("ascii")
def b64u_decode(s: str) -> bytes:
    if not isinstance(s, str):
        raise ValueError("Expected base64url string")
    val = s.strip()
    if val == "":
        return b""
    if not _B64U_RE.match(val):
        raise ValueError("Invalid base64url characters")
    if "=" in val and len(val) % 4 != 0:
        raise ValueError("Invalid base64url padding")
    raw_val = val.rstrip("=")
    if len(raw_val) % 4 == 1:
        raise ValueError("Invalid base64url length")
    pad = "=" * ((4 - (len(raw_val) % 4)) % 4)
    return base64.urlsafe_b64decode((raw_val + pad).encode("ascii"))
def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
def derive_node_id(pk_raw: bytes) -> str:
    if not isinstance(pk_raw, (bytes, bytearray)):
        raise TypeError("pk_raw must be bytes")
    raw = bytes(pk_raw)
    if len(raw) != 32:
        raise ValueError("Ed25519 raw public key must be 32 bytes")
    return "shn_" + hashlib.sha256(b"shard-node-id-v1:" + raw).digest()[:20].hex()
class NodeIdentity:
    def __init__(self, identity_path=None):
        self._path = os.path.abspath(os.fspath(identity_path)) if identity_path else os.path.abspath(paths.network_identity_file())
        self._lock = threading.RLock()
        self.sk = None
        self.pk = None
        self.node_id = ""
        self.disc_seq = 0
        self._created_at = 0.0
        self._load_or_create()
    def _persist_unlocked(self, created_at=None):
        sk_raw = self.sk.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        if created_at is not None:
            self._created_at = float(created_at)
        elif not self._created_at:
            self._created_at = time.time()
        payload = {
            "version": 1,
            "alg": "ed25519",
            "node_id": self.node_id,
            "pubkey_b64": self.pubkey_b64,
            "privkey_b64": b64u_encode(sk_raw),
            "disc_seq": int(self.disc_seq),
            "created_at": self._created_at,
        }
        storage.atomic_write_json(self._path, payload, indent=2, fsync=True, backup=True, restrict=True)
    def _load_or_create(self):
        with self._lock, storage.file_lock(self._path):
            data = storage.read_json_safe(self._path, default=None, expected_type=dict, quarantine=True, use_backup=True)
            if isinstance(data, dict) and isinstance(data.get("privkey_b64"), str):
                try:
                    sk_raw = b64u_decode(data["privkey_b64"])
                    if len(sk_raw) == 32:
                        sk = Ed25519PrivateKey.from_private_bytes(sk_raw)
                        pk = sk.public_key()
                        pk_raw = pk.public_bytes(Encoding.Raw, PublicFormat.Raw)
                        nid = derive_node_id(pk_raw)
                        seq = max(0, int(data.get("disc_seq") or 0))
                        self.sk = sk
                        self.pk = pk
                        self.node_id = nid
                        self.disc_seq = seq
                        self._created_at = float(data.get("created_at") or time.time())
                        if data.get("node_id") != nid or data.get("pubkey_b64") != self.pubkey_b64:
                            self._persist_unlocked(created_at=self._created_at)
                        else:
                            storage.restrict_file_permissions(self._path)
                        return
                except Exception:
                    pass
            self.sk = Ed25519PrivateKey.generate()
            self.pk = self.sk.public_key()
            pk_raw = self.pk.public_bytes(Encoding.Raw, PublicFormat.Raw)
            self.node_id = derive_node_id(pk_raw)
            self.disc_seq = 0
            self._created_at = time.time()
            self._persist_unlocked(created_at=self._created_at)
    def pubkey_bytes(self) -> bytes:
        return self.pk.public_bytes(Encoding.Raw, PublicFormat.Raw)
    @property
    def pubkey_b64(self) -> str:
        return b64u_encode(self.pubkey_bytes())
    def sign_bytes(self, domain_prefix: bytes, data: bytes) -> str:
        if not isinstance(domain_prefix, (bytes, bytearray)):
            raise TypeError("domain_prefix must be bytes")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes")
        sig = self.sk.sign(bytes(domain_prefix) + bytes(data))
        return b64u_encode(sig)
    def next_seq(self) -> int:
        with self._lock, storage.file_lock(self._path):
            data = storage.read_json_safe(self._path, default=None, expected_type=dict, quarantine=False, use_backup=True)
            cur = int(self.disc_seq)
            if isinstance(data, dict):
                try:
                    cur = max(cur, int(data.get("disc_seq") or 0))
