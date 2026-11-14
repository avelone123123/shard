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
                except Exception:
                    pass
                if data.get("created_at"):
                    try:
                        self._created_at = float(data["created_at"])
                    except Exception:
                        pass
            nxt = cur + 1
            self.disc_seq = nxt
            self._persist_unlocked(created_at=self._created_at or None)
            return nxt
    def sign_envelope(self, action: str, body_dict: dict) -> dict:
        if isinstance(action, (bytes, bytearray)):
            action = _DOMAIN_TO_ACTION.get(bytes(action), "")
        if not isinstance(action, str) or action not in _ACTION_DOMAINS:
            raise ValueError("Unsupported envelope action")
        if body_dict is None:
            body = {}
        elif isinstance(body_dict, dict):
            body = copy.deepcopy(body_dict)
        else:
            raise TypeError("body_dict must be a dict")
        canonical_json(body)
        env = {
            "protocol": PROTOCOL_DISC_V1,
            "action": action,
            "node_id": self.node_id,
            "pubkey": self.pubkey_b64,
            "ts": round(time.time(), 3),
            "seq": self.next_seq(),
            "body": body,
        }
        payload = canonical_json(env)
        env["sig"] = self.sign_bytes(_ACTION_DOMAINS[action], payload)
        return env
def verify_sig(pubkey_b64: str, expected_node_id: str, domain_prefix: bytes, data_bytes: bytes, sig_b64: str) -> bool:
    if not isinstance(pubkey_b64, str) or not isinstance(sig_b64, str):
        return False
    if not isinstance(domain_prefix, (bytes, bytearray)) or not isinstance(data_bytes, (bytes, bytearray)):
        return False
    try:
        pk_raw = b64u_decode(pubkey_b64)
        if len(pk_raw) != 32:
            return False
        derived = derive_node_id(pk_raw)
        if expected_node_id is not None:
            if not isinstance(expected_node_id, str) or not NODE_ID_RE.match(expected_node_id):
                return False
            if not hmac.compare_digest(derived, expected_node_id):
                return False
        sig_raw = b64u_decode(sig_b64)
        if len(sig_raw) != 64:
            return False
        pk = Ed25519PublicKey.from_public_bytes(pk_raw)
        pk.verify(sig_raw, bytes(domain_prefix) + bytes(data_bytes))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
    except Exception:
        return False
def verify_envelope(envelope: dict, expected_action: str | None = None, max_skew_s: float = MAX_CLOCK_SKEW_S) -> tuple[bool, str]:
    if not isinstance(envelope, dict):
        return False, "invalid_envelope"
    if envelope.get("protocol") != PROTOCOL_DISC_V1:
        return False, "invalid_protocol"
    act = envelope.get("action")
    if isinstance(act, (bytes, bytearray)):
        act = _DOMAIN_TO_ACTION.get(bytes(act), "")
    if not isinstance(act, str) or act not in _ACTION_DOMAINS:
        return False, "invalid_action"
    if expected_action is not None:
        if isinstance(expected_action, (bytes, bytearray)):
            if _ACTION_DOMAINS[act] != bytes(expected_action):
                return False, "action_mismatch"
        elif act != expected_action:
            return False, "action_mismatch"
    node_id = envelope.get("node_id")
    if not isinstance(node_id, str) or not NODE_ID_RE.match(node_id):
        return False, "invalid_node_id"
    pubkey_b64 = envelope.get("pubkey") if envelope.get("pubkey") is not None else envelope.get("pubkey_b64")
    if not isinstance(pubkey_b64, str) or not pubkey_b64:
        return False, "missing_pubkey"
    try:
        pk_raw = b64u_decode(pubkey_b64)
        if len(pk_raw) != 32:
            return False, "invalid_pubkey"
        derived_nid = derive_node_id(pk_raw)
    except Exception:
        return False, "invalid_pubkey"
    if not hmac.compare_digest(derived_nid, node_id):
        return False, "node_id_mismatch"
    ts = envelope.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(float(ts)):
        return False, "invalid_timestamp"
    try:
        skew_limit = float(max_skew_s)
    except Exception:
        return False, "invalid_max_skew"
    if not math.isfinite(skew_limit) or skew_limit < 0:
        return False, "invalid_max_skew"
    if abs(time.time() - float(ts)) > skew_limit:
        return False, "timestamp_skew"
    seq = envelope.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        return False, "invalid_seq"
    if "body" in envelope:
        body = envelope.get("body")
    elif "payload" in envelope:
        body = envelope.get("payload")
    else:
        body = None
    if not isinstance(body, dict):
        return False, "invalid_body"
    sig_b64 = envelope.get("sig")
    if not isinstance(sig_b64, str) or not sig_b64:
        return False, "missing_sig"
    env_no_sig = {k: v for k, v in envelope.items() if k != "sig"}
    try:
        payload = canonical_json(env_no_sig)
    except Exception:
        return False, "invalid_payload"
    if not verify_sig(pubkey_b64, node_id, _ACTION_DOMAINS[act], payload, sig_b64):
        return False, "invalid_signature"
    return True, "ok"
def x25519_keypair():
    priv = X25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv, pub_bytes
def x25519_shared_secret(priv, peer_pub_bytes: bytes) -> bytes:
    if not isinstance(peer_pub_bytes, (bytes, bytearray)):
        raise TypeError("peer_pub_bytes must be bytes")
    raw = bytes(peer_pub_bytes)
    if len(raw) != 32:
        raise ValueError("X25519 public key must be 32 bytes")
    peer_pub = X25519PublicKey.from_public_bytes(raw)
    secret = priv.exchange(peer_pub)
    if len(secret) != 32 or hmac.compare_digest(secret, b"\x00" * 32):
        raise ValueError("Invalid X25519 shared secret")
    return secret
def derive_handshake_keys(shared_secret: bytes, h1: bytes) -> tuple[bytes, bytes]:
    if not isinstance(shared_secret, (bytes, bytearray)) or len(shared_secret) < 32:
        raise ValueError("Invalid shared_secret")
    if hmac.compare_digest(bytes(shared_secret)[:32], b"\x00" * 32):
        raise ValueError("All-zero shared_secret")
    if not isinstance(h1, (bytes, bytearray)) or not h1:
        raise ValueError("h1 must be non-empty bytes")
    okm = HKDF(algorithm=hashes.SHA256(), length=64, salt=bytes(h1), info=b"SHARD-HS-KEYS-V1").derive(bytes(shared_secret))
    return okm[:32], okm[32:64]
def derive_session_keys(shared_secret: bytes, h_final: bytes) -> tuple[bytes, bytes, bytes, bytes]:
    if not isinstance(shared_secret, (bytes, bytearray)) or len(shared_secret) < 32:
        raise ValueError("Invalid shared_secret")
    if hmac.compare_digest(bytes(shared_secret)[:32], b"\x00" * 32):
        raise ValueError("All-zero shared_secret")
    if not isinstance(h_final, (bytes, bytearray)) or not h_final:
        raise ValueError("h_final must be non-empty bytes")
    okm = HKDF(algorithm=hashes.SHA256(), length=72, salt=bytes(h_final), info=b"SHARD-SESSION-KEYS-V1").derive(bytes(shared_secret))
    return okm[0:32], okm[32:64], okm[64:68], okm[68:72]
def pack_frame_aad(ver: int, msg_type: int, flags: int, stream_id: int, seq: int) -> bytes:
    for val in (ver, msg_type, flags, stream_id, seq):
        if isinstance(val, bool) or not isinstance(val, int):
            raise TypeError("AAD header fields must be integers")
    return _FRAME_AAD_STRUCT.pack(ver, msg_type, flags, stream_id, seq)
def unpack_frame_aad(raw16: bytes) -> tuple:
    if not isinstance(raw16, (bytes, bytearray)) or len(raw16) != 16:
        raise ValueError("Frame AAD must be exactly 16 bytes")
    return _FRAME_AAD_STRUCT.unpack(bytes(raw16))
def _build_nonce(iv4: bytes, seq: int) -> bytes:
    if not isinstance(iv4, (bytes, bytearray)) or len(iv4) != 4:
        raise ValueError("iv4 must be 4 bytes")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0 or seq > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("seq must be uint64")
    return bytes(iv4) + struct.pack(">Q", seq)
def aead_encrypt(key32: bytes, iv4: bytes, seq: int, aad16: bytes, plaintext: bytes) -> bytes:
    if not isinstance(key32, (bytes, bytearray)) or len(key32) != 32:
        raise ValueError("key32 must be 32 bytes")
    if not isinstance(aad16, (bytes, bytearray)):
        raise TypeError("aad16 must be bytes")
    if not isinstance(plaintext, (bytes, bytearray)):
        raise TypeError("plaintext must be bytes")
    nonce = _build_nonce(iv4, seq)
    return ChaCha20Poly1305(bytes(key32)).encrypt(nonce, bytes(plaintext), bytes(aad16))
def aead_decrypt(key32: bytes, iv4: bytes, seq: int, aad16: bytes, ciphertext: bytes) -> bytes:
    if not isinstance(key32, (bytes, bytearray)) or len(key32) != 32:
        raise ValueError("key32 must be 32 bytes")
    if not isinstance(aad16, (bytes, bytearray)):
        raise TypeError("aad16 must be bytes")
    if not isinstance(ciphertext, (bytes, bytearray)):
        raise TypeError("ciphertext must be bytes")
    nonce = _build_nonce(iv4, seq)
    return ChaCha20Poly1305(bytes(key32)).decrypt(nonce, bytes(ciphertext), bytes(aad16))
class InviteManager:
    def __init__(self, invites_path=None):
        self._path = os.path.abspath(os.fspath(invites_path)) if invites_path else os.path.abspath(paths.network_invites_file())
        self._lock = threading.RLock()
    def _empty_state(self):
        return {
            "version": 1,
            "pepper_b64": b64u_encode(secrets.token_bytes(32)),
            "invites": [],
        }
    def _load_unlocked(self, create_if_missing=False):
        data = storage.read_json_safe(self._path, default=None, expected_type=dict, quarantine=True, use_backup=True)
        changed = False
        if not isinstance(data, dict):
            data = self._empty_state()
            changed = create_if_missing
        if not isinstance(data.get("invites"), list):
            data["invites"] = []
            changed = create_if_missing
        pepper = b""
        if isinstance(data.get("pepper_b64"), str):
            try:
                pepper = b64u_decode(data["pepper_b64"])
            except Exception:
                pepper = b""
        if len(pepper) < 16:
            pepper = secrets.token_bytes(32)
            data["pepper_b64"] = b64u_encode(pepper)
            if create_if_missing or os.path.exists(self._path):
                changed = True
        if changed:
            self._save_unlocked(data)
        return data, pepper
    def _save_unlocked(self, data):
        storage.atomic_write_json(self._path, data, indent=2, fsync=True, backup=True, restrict=True)
    def _hmac_token(self, pepper: bytes, invite_id: str, secret: str) -> str:
        msg = f"{invite_id}:{secret}".encode("utf-8")
        return hmac.new(pepper, msg, hashlib.sha256).hexdigest()
    def _public_record(self, rec: dict, now: float | None = None) -> dict:
        cur = time.time() if now is None else now
        exp = float(rec.get("expires_at") or 0)
        max_u = rec.get("max_uses")
        uses = int(rec.get("uses_count") or 0)
        rem = max(0, int(max_u) - uses) if max_u is not None else None
        out = {
            "invite_id": str(rec.get("invite_id") or ""),
            "label": str(rec.get("label") or ""),
            "network_id": rec.get("network_id"),
            "created_at": float(rec.get("created_at") or 0),
            "expires_at": exp,
            "ttl_s": float(rec.get("ttl_s") or 0),
            "max_uses": int(max_u) if max_u is not None else None,
            "uses_count": uses,
            "remaining_uses": rem,
            "revoked": bool(rec.get("revoked")),
            "revoked_at": rec.get("revoked_at"),
            "expired": cur >= exp,
            "consumed_by": copy.deepcopy(rec.get("consumed_by") or []),
        }
        if rec.get("raw_token"):
            out["raw_token"] = str(rec.get("raw_token"))
        return out
    def create_invite(self, label: str = "", ttl_s: float = 86400, max_uses: int | None = 1, network_id: str | None = None) -> dict:
        if isinstance(ttl_s, bool) or not isinstance(ttl_s, (int, float)) or not math.isfinite(float(ttl_s)) or float(ttl_s) <= 0:
            raise ValueError("ttl_s must be positive")
        ttl = float(ttl_s)
        if max_uses is not None:
            if isinstance(max_uses, bool) or not isinstance(max_uses, int) or max_uses < 1:
                raise ValueError("max_uses must be >= 1 or None")
        clean_label = "".join(ch for ch in str(label or "") if 32 <= ord(ch) < 127).strip()[:128]
        clean_net = "".join(ch for ch in str(network_id) if 32 < ord(ch) < 127)[:64] if network_id else None
        invite_id = "inv" + secrets.token_hex(6)
        secret = secrets.token_hex(24)
        raw_token = f"shinv1_{invite_id}_{secret}"
        now = time.time()
        with self._lock, storage.file_lock(self._path):
            data, pepper = self._load_unlocked(create_if_missing=True)
            rec = {
                "invite_id": invite_id,
                "token_hmac": self._hmac_token(pepper, invite_id, secret),
                "raw_token": raw_token,
                "label": clean_label,
                "network_id": clean_net,
                "created_at": now,
                "expires_at": now + ttl,
                "ttl_s": ttl,
                "max_uses": max_uses,
                "uses_count": 0,
                "revoked": False,
                "revoked_at": None,
                "consumed_by": [],
            }
            data["invites"].append(rec)
            self._save_unlocked(data)
            out = self._public_record(rec, now=now)
            out["raw_token"] = raw_token
            return out
    def list_invites(self, include_revoked: bool = False) -> list[dict]:
        with self._lock, storage.file_lock(self._path):
            if not os.path.isfile(self._path) and not os.path.isfile(self._path + ".bak"):
                return []
            data, _ = self._load_unlocked(create_if_missing=False)
            now = time.time()
            out = []
            for rec in data.get("invites") or []:
                if not isinstance(rec, dict):
                    continue
                if not include_revoked and rec.get("revoked"):
                    continue
                out.append(self._public_record(rec, now=now))
            return out
    def verify_and_consume(self, raw_token: str, peer_node_id: str | None = None) -> tuple[bool, str, dict | None]:
        if not isinstance(raw_token, str) or len(raw_token) > 512:
            return False, "invalid_token_format", None
        tok = raw_token.strip()
        if tok != raw_token:
            return False, "invalid_token_format", None
        m = _INVITE_TOKEN_RE.match(tok)
        if not m:
            return False, "invalid_token_format", None
        invite_id, secret = m.group(1), m.group(2)
        peer_clean = None
        if peer_node_id is not None:
            if not isinstance(peer_node_id, str):
                return False, "invalid_peer_node_id", None
            peer_clean = "".join(ch for ch in peer_node_id.strip() if 32 < ord(ch) < 127)[:64]
        with self._lock, storage.file_lock(self._path):
            if not os.path.isfile(self._path) and not os.path.isfile(self._path + ".bak"):
                return False, "invite_not_found", None
            data, pepper = self._load_unlocked(create_if_missing=False)
            candidate_hmac = self._hmac_token(pepper, invite_id, secret)
            target_rec = None
            for rec in data.get("invites") or []:
                if isinstance(rec, dict) and rec.get("invite_id") == invite_id:
                    target_rec = rec
                    break
            if target_rec is None:
                hmac.compare_digest(candidate_hmac, "0" * 64)
                return False, "invite_not_found", None
            stored_hmac = str(target_rec.get("token_hmac") or "")
            if len(stored_hmac) != 64 or not hmac.compare_digest(candidate_hmac, stored_hmac):
                return False, "invalid_token", None
            if target_rec.get("revoked"):
                return False, "invite_revoked", None
            now = time.time()
            if now >= float(target_rec.get("expires_at") or 0):
                return False, "invite_expired", None
            uses = int(target_rec.get("uses_count") or 0)
            max_u = target_rec.get("max_uses")
            if max_u is not None and uses >= int(max_u):
                return False, "invite_exhausted", None
            target_rec["uses_count"] = uses + 1
            consumed = target_rec.get("consumed_by")
            if not isinstance(consumed, list):
                consumed = []
            consumed.append({"node_id": peer_clean, "ts": now})
            if len(consumed) > 256:
                consumed = consumed[-256:]
            target_rec["consumed_by"] = consumed
            self._save_unlocked(data)
            return True, "ok", self._public_record(target_rec, now=now)
    def revoke_invite(self, invite_id: str) -> bool:
        if not isinstance(invite_id, str) or not invite_id.strip():
            return False
        iid = invite_id.strip()
        with self._lock, storage.file_lock(self._path):
            if not os.path.isfile(self._path) and not os.path.isfile(self._path + ".bak"):
                return False
            data, _ = self._load_unlocked(create_if_missing=False)
            for rec in data.get("invites") or []:
                if isinstance(rec, dict) and rec.get("invite_id") == iid:
                    if not rec.get("revoked"):
                        rec["revoked"] = True
                        rec["revoked_at"] = time.time()
                        self._save_unlocked(data)
                    return True
            return False
def _clean_uri_items(items, max_items=8, max_len=512):
    if items is None:
        return []
    if isinstance(items, (str, bytes)):
        raise TypeError("Expected sequence of strings")
    out = []
    seen = set()
    for item in items:
        if not isinstance(item, str):
            raise TypeError("URI entry must be str")
        val = item.strip()
        if not val:
            continue
        if len(val) > max_len or any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in val):
            raise ValueError("Invalid URI list entry")
        if val not in seen:
            seen.add(val)
            out.append(val)
        if len(out) >= max_items:
            break
    return out
def pack_invite_uri(node_id: str, invite_token: str | None = None, name: str = "", relays=None, direct_endpoints=None, discovery_urls=None) -> str:
    if not isinstance(node_id, str) or not NODE_ID_RE.match(node_id):
        raise ValueError("Invalid node_id")
    params = [("node_id", node_id)]
    if invite_token is not None:
        if not isinstance(invite_token, str):
            raise TypeError("invite_token must be str")
        tok = invite_token.strip()
        if not tok or len(tok) > 256 or any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in tok):
            raise ValueError("Invalid invite_token")
        params.append(("token", tok))
    clean_name = "".join(ch for ch in str(name or "") if ord(ch) >= 32 and ord(ch) != 127).strip()[:64]
    if clean_name:
        params.append(("name", clean_name))
    for r in _clean_uri_items(relays, max_items=8, max_len=512):
        params.append(("relay", r))
    for ep in _clean_uri_items(direct_endpoints, max_items=8, max_len=256):
        params.append(("direct", ep))
    for disc in _clean_uri_items(discovery_urls, max_items=8, max_len=512):
        params.append(("discovery", disc))
    uri = "shard://connect?" + urlencode(params, doseq=True)
    if len(uri) > 4096:
        raise ValueError("Invite URI exceeds maximum length")
    return uri
def parse_invite_uri(uri: str) -> dict:
    if not isinstance(uri, str):
        raise ValueError("URI must be a string")
    raw = uri.strip()
    if not raw or raw != uri or len(raw) > 4096 or any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in raw):
        raise ValueError("Malformed invite URI")
    if "\x23" in raw:
        raise ValueError("Fragments not allowed in invite URI")
    parsed = urlparse(raw)
    if parsed.scheme != "shard" or parsed.netloc != "connect":
        raise ValueError("Invalid invite URI scheme or host")
    if parsed.path not in ("", "/") or parsed.username is not None or parsed.password is not None:
        raise ValueError("Invalid invite URI structure")
    qs = parse_qs(parsed.query, keep_blank_values=False)
    nid_vals = qs.get("node_id") or qs.get("node") or []
    if len(nid_vals) != 1 or not NODE_ID_RE.match(nid_vals[0]):
        raise ValueError("Missing or invalid node_id in invite URI")
    tok_vals = qs.get("token") or qs.get("invite_token") or []
    if len(tok_vals) > 1:
        raise ValueError("Multiple tokens in invite URI")
    invite_token = None
    if tok_vals:
        t = tok_vals[0].strip()
        if not t or len(t) > 256 or any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in t):
            raise ValueError("Invalid token in invite URI")
        invite_token = t
    name_vals = qs.get("name") or []
    clean_name = "".join(ch for ch in str(name_vals[0]) if ord(ch) >= 32 and ord(ch) != 127).strip()[:64] if name_vals else ""
    return {
        "node_id": nid_vals[0],
        "invite_token": invite_token,
        "name": clean_name,
        "relays": _clean_uri_items(qs.get("relay") or [], max_items=8, max_len=512),
        "direct_endpoints": _clean_uri_items(qs.get("direct") or [], max_items=8, max_len=256),
        "discovery_urls": _clean_uri_items(qs.get("discovery") or [], max_items=8, max_len=512),
    }
def _classify_ip_block(ip_obj, allow_loopback: bool) -> str | None:
    if isinstance(ip_obj, ipaddress.IPv6Address):
        if ip_obj.teredo is not None:
            return "teredo_blocked"
        if ip_obj.sixtofour is not None:
            sub = _classify_ip_block(ip_obj.sixtofour, allow_loopback)
            if sub:
                return f"sixtofour_{sub}"
        if ip_obj.ipv4_mapped is not None:
            sub = _classify_ip_block(ip_obj.ipv4_mapped, allow_loopback)
            if sub:
                return f"ipv4_mapped_{sub}"
            return None
        if ip_obj.is_loopback:
            return None if allow_loopback else "loopback_blocked"
        if ip_obj.is_unspecified:
            return "unspecified_blocked"
        if ip_obj.is_multicast:
            return "multicast_blocked"
        if ip_obj.is_link_local:
            return "link_local_blocked"
        if ip_obj.is_private or ip_obj.is_reserved:
            return "private_or_reserved_blocked"
        for net in _BLOCKED_V6_NETS:
            if ip_obj in net:
                return "blocked_ipv6_range"
        if not ip_obj.is_global:
            return "non_global_ip_blocked"
        return None
    if ip_obj.is_loopback:
        return None if allow_loopback else "loopback_blocked"
    if ip_obj.is_unspecified:
        return "unspecified_blocked"
    if ip_obj.is_multicast:
        return "multicast_blocked"
    if ip_obj.is_link_local:
        return "link_local_blocked"
    if ip_obj.is_private or ip_obj.is_reserved:
        return "private_or_reserved_blocked"
    for net in _BLOCKED_V4_NETS:
        if ip_obj in net:
            return "blocked_ipv4_range"
    if not ip_obj.is_global:
        return "non_global_ip_blocked"
    return None
def _is_loopback_ip(ip_obj) -> bool:
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        return ip_obj.ipv4_mapped.is_loopback
    return ip_obj.is_loopback
def validate_external_service_url(url: str, kind: str = "discovery", allow_insecure_local: bool | None = None) -> tuple[bool, str, dict]:
    if allow_insecure_local is None:
        allow_local = os.environ.get("SHARD_ALLOW_INSECURE_LOCAL") == "1"
    else:
        allow_local = bool(allow_insecure_local)
    if kind not in ("discovery", "relay"):
        return False, "invalid_kind", {}
    if not isinstance(url, str) or not url or len(url) > 512:
        return False, "invalid_length", {}
    if url != url.strip() or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        return False, "whitespace_or_control_chars", {}
    if "\\" in url or "\x23" in url:
        return False, "forbidden_url_characters", {}
    try:
        parsed = urlsplit(url)
    except Exception:
        return False, "invalid_url", {}
    scheme = (parsed.scheme or "").lower()
    secure_schemes = ("https",) if kind == "discovery" else ("wss",)
    local_schemes = ("http",) if kind == "discovery" else ("ws",)
    if scheme in secure_schemes:
        is_insecure_scheme = False
    elif scheme in local_schemes:
        if not allow_local:
            return False, "insecure_scheme_blocked", {}
        is_insecure_scheme = True
    else:
        return False, "unsupported_scheme", {}
    if parsed.fragment:
        return False, "fragment_not_allowed", {}
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return False, "credentials_not_allowed", {}
    try:
        port_val = parsed.port
    except ValueError:
        return False, "invalid_port", {}
    if port_val is not None and not (1 <= port_val <= 65535):
        return False, "invalid_port", {}
    port = port_val if port_val is not None else (443 if scheme in ("https", "wss") else 80)
    raw_host = parsed.hostname or ""
    if "%" in raw_host or "%" in parsed.netloc:
        return False, "zone_id_or_encoding_not_allowed", {}
    try:
        host = raw_host.strip().lower().rstrip(".").encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        return False, "invalid_host", {}
    if not host or len(host) > 253:
        return False, "invalid_host", {}
    is_localhost_name = host == "localhost" or host.endswith(".localhost")
    if is_localhost_name and not allow_local:
        return False, "blocked_hostname", {}
    if host in ("metadata", "instance-data") or host.startswith("metadata.") or any(host.endswith(suf) for suf in (".local", ".internal", ".arpa")):
        return False, "blocked_hostname", {}
    resolved_objs = []
    literal_ip = None
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        resolved_objs.append(literal_ip)
    else:
        if _NONCANONICAL_IP_RE.match(host):
            return False, "noncanonical_ip_blocked", {}
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            return False, "dns_resolution_failed", {}
        seen_ips = set()
        for info in infos:
            sockaddr = info[4]
            if not sockaddr:
                continue
            ip_str = str(sockaddr[0]).split("%", 1)[0]
            try:
                ip_obj = ipaddress.ip_address(ip_str)
            except ValueError:
                return False, "invalid_resolved_ip", {}
            norm_ip = str(ip_obj)
            if norm_ip not in seen_ips:
                seen_ips.add(norm_ip)
                resolved_objs.append(ip_obj)
        if not resolved_objs:
            return False, "dns_resolution_empty", {}
    for ip_obj in resolved_objs:
        reason = _classify_ip_block(ip_obj, allow_loopback=allow_local)
        if reason:
            return False, reason, {}
        if is_insecure_scheme and not _is_loopback_ip(ip_obj):
            return False, "insecure_scheme_non_local", {}
    return True, "ok", {
        "url": url,
        "kind": kind,
        "scheme": scheme,
        "host": host,
        "port": port,
        "path": parsed.path or "/",
        "resolved_ips": [str(ip_obj) for ip_obj in resolved_objs],
        "insecure_local": is_insecure_scheme or any(_is_loopback_ip(x) for x in resolved_objs),
    }
def _has_secret_or_path(val: str) -> bool:
    low = val.lower()
    if any(tok in low for tok in _SECRET_SUBSTRINGS):
        return True
    if _PATH_HINT_RE.search(val):
        return True
    if "\\" in val:
        return True
    if val.count("/") > 1:
        return True
    return False
def _clean_text(val, max_len: int) -> str | None:
    if not isinstance(val, str):
        return None
    cleaned = "".join(ch for ch in val if ord(ch) >= 32 and ord(ch) != 127).strip()
    if not cleaned or _has_secret_or_path(cleaned):
        return None
    return cleaned[:max_len]
def _sanitize_model_id(val) -> str | None:
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s or len(s) > 192 or _has_secret_or_path(s):
        return None
    if s.lower().endswith((".gguf", ".safetensors", ".bin", ".pt", ".pth", ".ckpt")) and ("/" in s or "\\" in s):
        return None
    if not _SAFE_MODEL_RE.match(s):
        return None
    return s
def _is_public_ip_str(val) -> str | None:
    if not isinstance(val, str):
        return None
    s = val.strip().strip("[]")
    if not s or "%" in s:
        return None
    try:
        ip_obj = ipaddress.ip_address(s)
    except ValueError:
        return None
    if _classify_ip_block(ip_obj, allow_loopback=False) is not None:
        return None
    return str(ip_obj)
def _sanitize_relay_url(val) -> str | None:
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s or len(s) > 512 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in s):
        return None
    if "\\" in s or "\x23" in s or any(tok in s.lower() for tok in _SECRET_SUBSTRINGS):
        return None
    try:
        parsed = urlsplit(s)
    except Exception:
        return None
    if (parsed.scheme or "").lower() != "wss":
        return None
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc or parsed.query or parsed.fragment:
        return None
    if ".." in (parsed.path or ""):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None and not (1 <= port <= 65535):
        return None
    try:
        host = (parsed.hostname or "").strip().lower().rstrip(".").encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        return None
    if not host or "%" in host:
        return None
    if host in _BLOCKED_HOST_EXACT or host.startswith("metadata.") or any(host.endswith(suf) for suf in _BLOCKED_HOST_SUFFIXES):
        return None
    try:
        ip_obj = ipaddress.ip_address(host)
        if _classify_ip_block(ip_obj, allow_loopback=False) is not None:
            return None
    except ValueError:
        if _NONCANONICAL_IP_RE.match(host):
            return None
    return s
def _sanitize_public_endpoint(val) -> str | None:
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s or len(s) > 256 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in s):
        return None
    if any(tok in s.lower() for tok in _SECRET_SUBSTRINGS) or "@" in s or "\\" in s or "\x23" in s or "?" in s:
        return None
    if s.lower().startswith(("https://", "wss://")):
        try:
            parsed = urlsplit(s)
            port = parsed.port
            if port is not None and not (1 <= port <= 65535):
                return None
            if ".." in (parsed.path or ""):
                return None
            host = (parsed.hostname or "").strip().lower().rstrip(".")
        except Exception:
            return None
    else:
        host = s
        if s.startswith("[") and "]:" in s:
            host, port_s = s[1:].split("]:", 1)
            if not port_s.isdigit() or not (1 <= int(port_s) <= 65535):
                return None
        elif s.startswith("[") and s.endswith("]"):
            host = s[1:-1]
        elif ":" in s and s.count(":") == 1:
            host, port_s = s.rsplit(":", 1)
            if not port_s.isdigit() or not (1 <= int(port_s) <= 65535):
                return None
        host = host.strip().lower().rstrip(".")
    if not host or "%" in host:
        return None
    try:
        host = host.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        return None
    if host in _BLOCKED_HOST_EXACT or host.startswith("metadata.") or any(host.endswith(suf) for suf in _BLOCKED_HOST_SUFFIXES):
        return None
    try:
        ip_obj = ipaddress.ip_address(host)
        if _classify_ip_block(ip_obj, allow_loopback=False) is not None:
            return None
    except ValueError:
        if _NONCANONICAL_IP_RE.match(host):
            return None
        if "/" in host or not re.match(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$", host):
            return None
    return s
def sanitize_public_listing(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return {}
    out = {}
    nid = raw.get("node_id")
    if isinstance(nid, str) and NODE_ID_RE.match(nid.strip()):
        out["node_id"] = nid.strip()
    for pk_key in ("pubkey", "pubkey_b64"):
        pk_val = raw.get(pk_key)
        if isinstance(pk_val, str) and pk_val.strip():
            try:
                if len(b64u_decode(pk_val.strip())) == 32:
                    out[pk_key] = pk_val.strip()
            except Exception:
                pass
    name = _clean_text(raw.get("name"), 64)
    if name:
        out["name"] = name
    for txt_key in ("network_name", "host_name"):
        tv = _clean_text(raw.get(txt_key), 64)
        if tv:
            out[txt_key] = tv
    desc = _clean_text(raw.get("description"), 256)
    if desc:
        out["description"] = desc
    role = raw.get("role")
    if isinstance(role, str) and role.strip().lower() in _PUBLIC_LISTING_ROLES:
        out["role"] = role.strip().lower()
    st_val = raw.get("status")
    if isinstance(st_val, str) and st_val.strip().lower() in ("online", "offline", "busy", "idle"):
        out["status"] = st_val.strip().lower()
    for acc_key in ("access_mode", "access"):
        av = raw.get(acc_key)
        if isinstance(av, str) and av.strip().lower() in ("open", "invite", "invite_only", "invite-only"):
            out[acc_key] = av.strip().lower()
    for m_key in ("model", "active_model"):
        model = _sanitize_model_id(raw.get(m_key))
        if model:
            out[m_key] = model
    for ml_key in ("models", "supported_models"):
        if isinstance(raw.get(ml_key), list):
