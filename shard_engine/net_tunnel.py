import asyncio
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import struct
import time
from contextlib import asynccontextmanager
from enum import Enum
from http import HTTPStatus
from urllib.parse import quote, unquote, urlsplit, urlunsplit
import httpx
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from . import net_identity
MAX_FRAME_BYTES = 131072
MAX_CHUNK_BYTES = 65536
MAX_HTTP_HEAD_BYTES = 65536
MAX_REQ_BODY_BYTES = 10 * 1024 * 1024
DEFAULT_RELAY_MAX_CONNS_PER_IP = 20
DEFAULT_RELAY_MAX_TUNNELS_PER_HOST = 64
DEFAULT_RELAY_MAX_BYTES_PER_SEC = 2_000_000
DEFAULT_RELAY_IDLE_TIMEOUT_S = 300.0
ALLOWED_TUNNEL_METHODS = frozenset({"GET", "POST"})
ALLOWED_TUNNEL_PATHS = frozenset({
    "/client",
    "/api/client/bootstrap",
    "/v1/models",
    "/v1/ping",
    "/v1/chat/completions",
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/api/lan/status",
    "/api/lan/pair",
    "/api/lan/heartbeat",
    "/api/lan/leave",
    "/api/feedback/policy",
    "/api/feedback/submit",
    "/api/feedback/my",
    "/api/feedback/revoke",
})
STRIP_REQUEST_HEADERS = frozenset({
    "x-shard-owner-token",
    "x-shard-tunnel-peer",
    "x-shard-bridge-token",
    "x-forwarded-for",
    "forwarded",
    "x-real-ip",
    "cf-connecting-ip",
    "true-client-ip",
    "x-client-ip",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-forwarded-port",
    "via",
    "host",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "proxy-authorization",
    "proxy-connection",
    "content-length",
})
STRIP_RESPONSE_HOP_HEADERS = frozenset({
    "connection",
    "keep-alive",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "proxy-connection",
})
_BRIDGE_SECRET = secrets.token_hex(32)
class TunnelState(str, Enum):
    DISCONNECTED = "disconnected"
    READY = "ready"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RELAY_CONNECTED = "relay_connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
class TunnelError(Exception):
    pass
class TunnelClosedError(TunnelError):
    pass
class TunnelFrameError(TunnelError):
    pass
class TunnelReplayError(TunnelFrameError):
    pass
class TunnelCryptoError(TunnelError):
    pass
class TunnelAuthError(TunnelError):
    pass
def get_bridge_secret() -> str:
    return _BRIDGE_SECRET
def verify_bridge_token(token: str, expected: str | None = None) -> bool:
    if not isinstance(token, str) or not token:
        return False
    exp = _BRIDGE_SECRET if expected is None else str(expected or "")
    if not exp:
        return False
    return hmac.compare_digest(token.strip(), exp)
def _extract_scope_and_headers(req_or_scope):
    if isinstance(req_or_scope, dict):
        scope = req_or_scope
        hdrs = req_or_scope.get("headers") or ()
        return scope, hdrs
    scope = getattr(req_or_scope, "scope", None)
    hdrs = getattr(req_or_scope, "headers", None)
    if isinstance(scope, dict):
        return scope, hdrs if hdrs is not None else (scope.get("headers") or ())
    return {}, hdrs if hdrs is not None else ()
def _get_header_ci(hdrs, target_name: str) -> str:
    t = target_name.lower()
    if isinstance(hdrs, dict) or hasattr(hdrs, "get"):
        for k, v in hdrs.items():
            ks = k.decode("latin-1") if isinstance(k, (bytes, bytearray)) else str(k)
            if ks.lower() == t:
                return v.decode("latin-1") if isinstance(v, (bytes, bytearray)) else str(v)
        return ""
    for item in hdrs or ():
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            continue
        k, v = item
        ks = k.decode("latin-1") if isinstance(k, (bytes, bytearray)) else str(k)
        if ks.lower() == t:
            return v.decode("latin-1") if isinstance(v, (bytes, bytearray)) else str(v)
    return ""
def is_tunnel_request(req_or_scope, bridge_secret: str | None = None) -> bool:
    scope, hdrs = _extract_scope_and_headers(req_or_scope)
    st = scope.get("shard_tunnel")
    if isinstance(st, dict) and isinstance(st.get("peer_node_id"), str) and net_identity.NODE_ID_RE.match(st["peer_node_id"]):
        return True
    tok = _get_header_ci(hdrs, "x-shard-bridge-token")
    if tok and verify_bridge_token(tok, expected=bridge_secret):
        return True
    return False
def tunnel_peer_id(req_or_scope, bridge_secret: str | None = None) -> str | None:
    scope, hdrs = _extract_scope_and_headers(req_or_scope)
    st = scope.get("shard_tunnel")
    if isinstance(st, dict):
        nid = st.get("peer_node_id")
        if isinstance(nid, str) and net_identity.NODE_ID_RE.match(nid):
            return nid
    tok = _get_header_ci(hdrs, "x-shard-bridge-token")
    if tok and verify_bridge_token(tok, expected=bridge_secret):
        peer = _get_header_ci(hdrs, "x-shard-tunnel-peer").strip()
        if net_identity.NODE_ID_RE.match(peer):
            return peer
    return None
def normalize_tunnel_path(raw_path: str) -> str | None:
    if not isinstance(raw_path, str) or not raw_path or len(raw_path) > 2048:
        return None
    if not raw_path.startswith("/"):
        return None
    low = raw_path.lower()
    if any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in raw_path):
        return None
    if any(bad in raw_path for bad in ("?", "\x23", "\\", "//", "..")):
        return None
    if any(enc in low for enc in ("%2f", "%5c", "%2e", "%00")):
        return None
    u1 = unquote(raw_path)
    u2 = unquote(u1)
    for cand in (u1, u2):
        if any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in cand):
            return None
        if any(bad in cand for bad in ("?", "\x23", "\\", "//", "..")):
            return None
    norm = "/client" if raw_path == "/" else raw_path
    if norm not in ALLOWED_TUNNEL_PATHS:
        return None
    return norm
def sanitize_tunnel_headers(headers) -> list[tuple[str, str]]:
    out = []
    if isinstance(headers, dict):
        items = list(headers.items())
    else:
        items = list(headers or ())
    for pair in items:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            continue
        k_raw, v_raw = pair
        k = (k_raw.decode("latin-1") if isinstance(k_raw, (bytes, bytearray)) else str(k_raw)).strip().lower()
        v = v_raw.decode("latin-1") if isinstance(v_raw, (bytes, bytearray)) else str(v_raw)
        if not k or len(k) > 128 or len(v) > 8192:
            continue
        if any(c in k for c in ("\r", "\n", "\x00", ":", " ", "\t")):
            continue
        if any(c in v for c in ("\r", "\n", "\x00")):
            continue
        if k in STRIP_REQUEST_HEADERS:
            continue
        out.append((k, v))
    return out
def _sanitize_response_headers(headers) -> list[tuple[str, str]]:
    out = []
    if isinstance(headers, dict):
        items = list(headers.items())
    else:
        items = list(headers or ())
    for pair in items:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            continue
        k_raw, v_raw = pair
        k = (k_raw.decode("latin-1") if isinstance(k_raw, (bytes, bytearray)) else str(k_raw)).strip().lower()
        v = v_raw.decode("latin-1") if isinstance(v_raw, (bytes, bytearray)) else str(v_raw)
        if not k or any(c in k for c in ("\r", "\n", "\x00", ":")) or any(c in v for c in ("\r", "\n", "\x00")):
            continue
        if k in STRIP_RESPONSE_HOP_HEADERS:
            continue
        out.append((k, v))
    return out
def _parse_direct_endpoint(endpoint: str) -> tuple[str, int]:
    s = str(endpoint or "").strip()
    if not s or any(c.isspace() or ord(c) < 32 for c in s):
        raise ValueError("Invalid direct endpoint")
    if s.startswith("[") and "]:" in s:
        host, port_s = s[1:].split("]:", 1)
    elif ":" in s and s.count(":") == 1:
        host, port_s = s.rsplit(":", 1)
    else:
        raise ValueError("Direct endpoint must include host:port")
    port = int(port_s)
    if not host or not (1 <= port <= 65535):
        raise ValueError("Invalid direct endpoint host or port")
    return host, port
def _build_relay_ws_url(base_url: str, endpoint: str, host_id: str, allow_insecure_local: bool | None = None) -> str:
    if not isinstance(host_id, str) or not net_identity.NODE_ID_RE.match(host_id):
        raise ValueError("Invalid host_id")
    ok, reason, _ = net_identity.validate_external_service_url(base_url, kind="relay", allow_insecure_local=allow_insecure_local)
    if not ok:
        raise ValueError(f"Invalid relay URL: {reason}")
    parsed = urlsplit(base_url.strip())
    base_path = (parsed.path or "").rstrip("/")
    suffix = f"/v1/relay/{endpoint}"
    if base_path.endswith(suffix):
        full_path = base_path
    else:
        full_path = f"{base_path}{suffix}"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, full_path, f"host_id={quote(host_id)}", ""))
def _validate_local_http_url(url: str) -> str:
    s = str(url or "").strip()
    parsed = urlsplit(s)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise ValueError("local_http_url must be http(s) loopback URL")
    host = (parsed.hostname or "").strip().lower()
    is_lb = host == "localhost"
    if not is_lb:
        try:
            is_lb = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_lb = False
    if not is_lb:
        raise ValueError("local_http_url must target loopback only")
    return s.rstrip("/")
class TokenBucket:
    def __init__(self, rate_bytes_per_s: float, burst_bytes: int | None = None, max_wait_s: float = 2.0):
        self.rate = float(rate_bytes_per_s)
        self.max_wait_s = max(0.0, float(max_wait_s))
        if self.rate <= 0:
            self.burst = 0.0
            self.tokens = 0.0
        else:
            b = int(burst_bytes) if burst_bytes is not None else max(8192, int(self.rate * 0.5))
            self.burst = float(max(1, b))
            self.tokens = self.burst
        self.last = time.monotonic()
        self._lock = asyncio.Lock()
    async def acquire(self, nbytes: int) -> bool:
        if self.rate <= 0 or nbytes <= 0:
            return True
        wait_s = 0.0
        async with self._lock:
            now = time.monotonic()
            elapsed = max(0.0, now - self.last)
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last = now
            if self.tokens >= nbytes:
                self.tokens -= nbytes
                return True
            deficit = nbytes - self.tokens
            wait_s = deficit / self.rate
            if wait_s > self.max_wait_s:
                return False
            self.tokens -= nbytes
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        return True
class AsyncTransport:
    async def send_bytes(self, data: bytes) -> None:
        raise NotImplementedError
    async def recv_bytes(self) -> bytes:
        raise NotImplementedError
    async def close(self) -> None:
        raise NotImplementedError
    def peer_info(self) -> str:
        return ""
class TcpFrameTransport(AsyncTransport):
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, max_frame_bytes: int = MAX_FRAME_BYTES):
        self.reader = reader
        self.writer = writer
        self.max_frame_bytes = int(max_frame_bytes)
        self._send_lock = asyncio.Lock()
        self._recv_lock = asyncio.Lock()
        self._closed = False
    async def send_bytes(self, data: bytes) -> None:
        if self._closed:
            raise TunnelClosedError("Transport closed")
        raw = bytes(data)
        if len(raw) == 0 or len(raw) > self.max_frame_bytes:
            raise TunnelFrameError("Frame size out of bounds")
        async with self._send_lock:
            if self._closed:
                raise TunnelClosedError("Transport closed")
            try:
                self.writer.write(struct.pack(">I", len(raw)) + raw)
                await self.writer.drain()
            except (OSError, ConnectionError, RuntimeError) as exc:
                await self.close()
                raise TunnelClosedError(str(exc)) from exc
    async def recv_bytes(self) -> bytes:
        if self._closed:
            raise TunnelClosedError("Transport closed")
        async with self._recv_lock:
            if self._closed:
                raise TunnelClosedError("Transport closed")
            try:
                hdr = await self.reader.readexactly(4)
                length = struct.unpack(">I", hdr)[0]
                if length == 0 or length > self.max_frame_bytes:
                    await self.close()
                    raise TunnelFrameError(f"Invalid TCP frame length: {length}")
                return await self.reader.readexactly(length)
            except asyncio.IncompleteReadError as exc:
                await self.close()
                raise TunnelClosedError("TCP EOF") from exc
            except (OSError, ConnectionError, RuntimeError) as exc:
                await self.close()
                raise TunnelClosedError(str(exc)) from exc
    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.writer.close()
            await asyncio.wait_for(self.writer.wait_closed(), timeout=1.5)
        except Exception:
            pass
    def peer_info(self) -> str:
        try:
            pn = self.writer.get_extra_info("peername")
            return f"{pn[0]}:{pn[1]}" if pn else "tcp"
        except Exception:
            return "tcp"
class WebSocketTransport(AsyncTransport):
    def __init__(self, ws, max_frame_bytes: int = MAX_FRAME_BYTES):
        self.ws = ws
        self.max_frame_bytes = int(max_frame_bytes)
        self._send_lock = asyncio.Lock()
        self._recv_lock = asyncio.Lock()
        self._closed = False
    async def send_bytes(self, data: bytes) -> None:
        if self._closed:
            raise TunnelClosedError("Transport closed")
        raw = bytes(data)
        if len(raw) == 0 or len(raw) > self.max_frame_bytes:
            raise TunnelFrameError("Frame size out of bounds")
        async with self._send_lock:
            if self._closed:
                raise TunnelClosedError("Transport closed")
            try:
                if hasattr(self.ws, "send_bytes"):
                    await self.ws.send_bytes(raw)
                else:
                    await self.ws.send(raw)
            except Exception as exc:
                await self.close()
                raise TunnelClosedError(str(exc)) from exc
    async def recv_bytes(self) -> bytes:
        if self._closed:
            raise TunnelClosedError("Transport closed")
        async with self._recv_lock:
            if self._closed:
                raise TunnelClosedError("Transport closed")
            try:
                if hasattr(self.ws, "receive"):
                    msg = await self.ws.receive()
                    if msg.get("type") == "websocket.disconnect":
                        await self.close()
                        raise TunnelClosedError("WebSocket disconnected")
                    data = msg.get("bytes")
                    if data is None:
                        await self.close()
                        raise TunnelFrameError("Unexpected text frame on binary WebSocket")
                else:
                    data = await self.ws.recv()
                    if not isinstance(data, (bytes, bytearray)):
                        await self.close()
                        raise TunnelFrameError("Unexpected text frame on binary WebSocket")
                raw = bytes(data)
                if len(raw) == 0 or len(raw) > self.max_frame_bytes:
                    await self.close()
                    raise TunnelFrameError(f"Oversized WebSocket frame: {len(raw)}")
                return raw
            except TunnelError:
                raise
            except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed, EOFError, ConnectionError) as exc:
                await self.close()
                raise TunnelClosedError(str(exc)) from exc
            except Exception as exc:
                await self.close()
                raise TunnelClosedError(str(exc)) from exc
    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await asyncio.wait_for(self.ws.close(), timeout=1.5)
        except Exception:
            pass
    def peer_info(self) -> str:
        try:
            ra = getattr(self.ws, "remote_address", None)
            if ra:
                return f"{ra[0]}:{ra[1]}"
            cl = getattr(self.ws, "client", None)
            if cl:
                return f"{cl.host}:{cl.port}"
        except Exception:
            pass
        return "ws"
class MuxPeerTransport(AsyncTransport):
    def __init__(self, tunnel_id: str, send_mux_fn, close_cb=None, max_frame_bytes: int = MAX_FRAME_BYTES, max_queue: int = 256):
        if not isinstance(tunnel_id, str) or len(tunnel_id) != 16:
            raise ValueError("tunnel_id must be 16 ASCII characters")
        self.tunnel_id = tunnel_id
        self.tunnel_id_bytes = tunnel_id.encode("ascii")
        self._send_mux_fn = send_mux_fn
        self._close_cb = close_cb
        self.max_frame_bytes = int(max_frame_bytes)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        self._closed = False
    async def feed(self, data: bytes) -> bool:
        if self._closed:
            return False
        if len(data) == 0 or len(data) > self.max_frame_bytes:
            self.mark_closed()
            return False
        try:
            self._queue.put_nowait(bytes(data))
            return True
        except asyncio.QueueFull:
            self.mark_closed()
            return False
    def mark_closed(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            while not self._queue.empty():
                self._queue.get_nowait()
        except Exception:
            pass
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
    async def send_bytes(self, data: bytes) -> None:
        if self._closed:
            raise TunnelClosedError("Mux tunnel closed")
        raw = bytes(data)
        if len(raw) == 0 or len(raw) > self.max_frame_bytes:
            raise TunnelFrameError("Frame size out of bounds")
        try:
            await self._send_mux_fn(self.tunnel_id_bytes + raw)
        except Exception as exc:
            self.mark_closed()
            raise TunnelClosedError(str(exc)) from exc
    async def recv_bytes(self) -> bytes:
        item = await self._queue.get()
        if item is None:
            raise TunnelClosedError("Mux tunnel closed")
        return item
    async def close(self) -> None:
        was_closed = self._closed
        self.mark_closed()
        if not was_closed and self._close_cb is not None:
            try:
                res = self._close_cb(self.tunnel_id)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass
    def peer_info(self) -> str:
        return f"relay-mux:{self.tunnel_id}"
class EncryptedChannel:
    def __init__(self, transport: AsyncTransport, role: str = "client", max_frame_bytes: int = MAX_FRAME_BYTES):
        if role not in ("client", "host"):
            raise ValueError("role must be 'client' or 'host'")
        self.transport = transport
        self.role = role
        self.max_frame_bytes = int(max_frame_bytes)
        self.send_seq = 0
        self.expected_recv_seq = 0
        self.bytes_tx = 0
        self.bytes_rx = 0
        self.peer_node_id: str | None = None
        self.peer_pubkey_b64: str | None = None
        self.auth_proof: str | None = None
        self._send_key: bytes | None = None
        self._recv_key: bytes | None = None
        self._send_iv: bytes | None = None
        self._recv_iv: bytes | None = None
        self._established = False
        self._closed = False
        self._send_lock = asyncio.Lock()
        self._recv_lock = asyncio.Lock()
    @property
    def is_established(self) -> bool:
        return self._established and not self._closed
    @property
    def is_closed(self) -> bool:
        return self._closed
    async def _recv_wire_exact(self, timeout: float | None = None) -> bytes:
        if timeout is None:
            wire = await self.transport.recv_bytes()
        else:
            wire = await asyncio.wait_for(self.transport.recv_bytes(), timeout=timeout)
        if len(wire) < 16 or len(wire) > self.max_frame_bytes:
            await self.close()
            raise TunnelFrameError(f"Wire frame size invalid: {len(wire)}")
        self.bytes_rx += len(wire)
        return wire
    async def _send_wire(self, wire: bytes) -> None:
        if len(wire) < 16 or len(wire) > self.max_frame_bytes:
            raise TunnelFrameError(f"Wire frame size invalid: {len(wire)}")
        await self.transport.send_bytes(wire)
        self.bytes_tx += len(wire)
    async def perform_client_handshake(
        self,
        identity: net_identity.NodeIdentity,
        target_host_id: str,
        auth_proof: str | None = None,
        timeout: float = 10.0,
    ) -> tuple[str, str, str | None]:
        if self.role != "client":
            raise TunnelError("perform_client_handshake requires client role")
        if self._established or self._closed:
            raise TunnelError("Channel already initialized or closed")
        if not isinstance(target_host_id, str) or not net_identity.NODE_ID_RE.match(target_host_id):
            raise TunnelAuthError("Invalid target_host_id")
        if auth_proof is not None and not isinstance(auth_proof, str):
            raise TypeError("auth_proof must be str or None")
        c_x_priv = None
        k_hs_c2s = None
        k_hs_s2c = None
        shared = None
        try:
            c_x_priv, c_x_pub = net_identity.x25519_keypair()
            c_nonce = secrets.token_bytes(32)
            aad1 = net_identity.pack_frame_aad(net_identity.PROTOCOL_TUNNEL_V1, net_identity.MSG_HS_INIT, 0, 0, 0)
            await self._send_wire(aad1 + c_x_pub + c_nonce)
            wire2 = await self._recv_wire_exact(timeout=timeout)
            aad2 = wire2[:16]
            body2 = wire2[16:]
            ver2, mt2, _, sid2, seq2 = net_identity.unpack_frame_aad(aad2)
            if ver2 != net_identity.PROTOCOL_TUNNEL_V1 or mt2 != net_identity.MSG_HS_RESP or sid2 != 0 or seq2 != 0:
                raise TunnelFrameError("Invalid handshake response frame header")
            if len(body2) <= 64:
                raise TunnelFrameError("Truncated handshake response")
            h_x_pub = body2[:32]
            h_nonce = body2[32:64]
            enc_host_auth = body2[64:]
            try:
                shared = net_identity.x25519_shared_secret(c_x_priv, h_x_pub)
            except Exception as exc:
                raise TunnelCryptoError("Invalid host X25519 key") from exc
            h1 = hashlib.sha256(
                b"SHARD-TUNNEL-H1\x00" + aad1 + c_x_pub + c_nonce + h_x_pub + h_nonce + target_host_id.encode("ascii")
            ).digest()
            k_hs_c2s, k_hs_s2c = net_identity.derive_handshake_keys(shared, h1)
            hs_iv_s2c = hashlib.sha256(b"hs-iv-s2c:" + h1).digest()[:4]
            try:
                host_plain = net_identity.aead_decrypt(k_hs_s2c, hs_iv_s2c, 0, aad2 + h_x_pub + h_nonce, enc_host_auth)
                host_obj = json.loads(host_plain.decode("utf-8"))
            except Exception as exc:
                raise TunnelCryptoError("Host handshake authentication decryption failed") from exc
            if not isinstance(host_obj, dict):
                raise TunnelAuthError("Invalid host auth payload")
            resp_nid = host_obj.get("node_id")
            resp_pub = host_obj.get("pubkey")
            resp_sig = host_obj.get("sig")
            if not isinstance(resp_nid, str) or not hmac.compare_digest(resp_nid, target_host_id):
                raise TunnelAuthError("Target host node_id mismatch")
            if not net_identity.verify_sig(resp_pub, target_host_id, net_identity.DOMAIN_TUNNEL_HOST, h1, resp_sig):
                raise TunnelAuthError("Host Ed25519 signature verification failed")
            h2 = hashlib.sha256(b"SHARD-TUNNEL-H2\x00" + h1 + enc_host_auth).digest()
            peer_sig = identity.sign_bytes(net_identity.DOMAIN_TUNNEL_PEER, h2)
            auth_obj = {
                "node_id": identity.node_id,
                "pubkey": identity.pubkey_b64,
                "sig": peer_sig,
                "auth_proof": auth_proof,
            }
            aad3 = net_identity.pack_frame_aad(net_identity.PROTOCOL_TUNNEL_V1, net_identity.MSG_HS_AUTH, 0, 0, 0)
            hs_iv_c2s = hashlib.sha256(b"hs-iv-c2s:" + h1).digest()[:4]
            enc_peer_auth = net_identity.aead_encrypt(k_hs_c2s, hs_iv_c2s, 0, aad3, net_identity.canonical_json(auth_obj))
            await self._send_wire(aad3 + enc_peer_auth)
            h_final = hashlib.sha256(b"SHARD-TUNNEL-HFINAL\x00" + h2 + enc_peer_auth).digest()
            k_c2s, k_s2c, iv_c2s, iv_s2c = net_identity.derive_session_keys(shared, h_final)
            self._send_key = k_c2s
            self._recv_key = k_s2c
            self._send_iv = iv_c2s
            self._recv_iv = iv_s2c
            self.send_seq = 0
            self.expected_recv_seq = 0
            self.peer_node_id = target_host_id
            self.peer_pubkey_b64 = resp_pub
            self.auth_proof = auth_proof
            self._established = True
            return target_host_id, resp_pub, auth_proof
        except Exception:
            await self.close()
            raise
        finally:
            c_x_priv = None
            k_hs_c2s = None
            k_hs_s2c = None
            shared = None
    async def perform_host_handshake(
        self,
        identity: net_identity.NodeIdentity,
        peer_authorizer=None,
        timeout: float = 10.0,
    ) -> tuple[str, str, str | None]:
        if self.role != "host":
            raise TunnelError("perform_host_handshake requires host role")
        if self._established or self._closed:
            raise TunnelError("Channel already initialized or closed")
        h_x_priv = None
        k_hs_c2s = None
        k_hs_s2c = None
        shared = None
        try:
            wire1 = await self._recv_wire_exact(timeout=timeout)
            aad1 = wire1[:16]
            body1 = wire1[16:]
            ver1, mt1, _, sid1, seq1 = net_identity.unpack_frame_aad(aad1)
            if ver1 != net_identity.PROTOCOL_TUNNEL_V1 or mt1 != net_identity.MSG_HS_INIT or sid1 != 0 or seq1 != 0:
                raise TunnelFrameError("Invalid handshake init frame header")
            if len(body1) != 64:
                raise TunnelFrameError("Invalid handshake init body length")
            c_x_pub = body1[:32]
            c_nonce = body1[32:64]
            h_x_priv, h_x_pub = net_identity.x25519_keypair()
            h_nonce = secrets.token_bytes(32)
            try:
                shared = net_identity.x25519_shared_secret(h_x_priv, c_x_pub)
            except Exception as exc:
                raise TunnelCryptoError("Invalid client X25519 key") from exc
            h1 = hashlib.sha256(
                b"SHARD-TUNNEL-H1\x00" + aad1 + c_x_pub + c_nonce + h_x_pub + h_nonce + identity.node_id.encode("ascii")
            ).digest()
            k_hs_c2s, k_hs_s2c = net_identity.derive_handshake_keys(shared, h1)
            host_sig = identity.sign_bytes(net_identity.DOMAIN_TUNNEL_HOST, h1)
            host_payload = net_identity.canonical_json({
                "node_id": identity.node_id,
                "pubkey": identity.pubkey_b64,
                "sig": host_sig,
            })
            aad2 = net_identity.pack_frame_aad(net_identity.PROTOCOL_TUNNEL_V1, net_identity.MSG_HS_RESP, 0, 0, 0)
            hs_iv_s2c = hashlib.sha256(b"hs-iv-s2c:" + h1).digest()[:4]
            enc_host_auth = net_identity.aead_encrypt(k_hs_s2c, hs_iv_s2c, 0, aad2 + h_x_pub + h_nonce, host_payload)
            await self._send_wire(aad2 + h_x_pub + h_nonce + enc_host_auth)
            wire3 = await self._recv_wire_exact(timeout=timeout)
            aad3 = wire3[:16]
            enc_peer_auth = wire3[16:]
            ver3, mt3, _, sid3, seq3 = net_identity.unpack_frame_aad(aad3)
            if ver3 != net_identity.PROTOCOL_TUNNEL_V1 or mt3 != net_identity.MSG_HS_AUTH or sid3 != 0 or seq3 != 0:
                raise TunnelFrameError("Invalid handshake auth frame header")
            hs_iv_c2s = hashlib.sha256(b"hs-iv-c2s:" + h1).digest()[:4]
            try:
                peer_plain = net_identity.aead_decrypt(k_hs_c2s, hs_iv_c2s, 0, aad3, enc_peer_auth)
                peer_obj = json.loads(peer_plain.decode("utf-8"))
            except Exception as exc:
                raise TunnelCryptoError("Peer handshake authentication decryption failed") from exc
            if not isinstance(peer_obj, dict):
                raise TunnelAuthError("Invalid peer auth payload")
            peer_nid = peer_obj.get("node_id")
            peer_pub = peer_obj.get("pubkey")
            peer_sig = peer_obj.get("sig")
            auth_proof = peer_obj.get("auth_proof")
            if auth_proof is not None and not isinstance(auth_proof, str):
                raise TunnelAuthError("Invalid auth_proof type")
            h2 = hashlib.sha256(b"SHARD-TUNNEL-H2\x00" + h1 + enc_host_auth).digest()
            if not isinstance(peer_nid, str) or not net_identity.NODE_ID_RE.match(peer_nid):
                raise TunnelAuthError("Invalid peer node_id")
            if not net_identity.verify_sig(peer_pub, peer_nid, net_identity.DOMAIN_TUNNEL_PEER, h2, peer_sig):
                raise TunnelAuthError("Peer Ed25519 signature verification failed")
            h_final = hashlib.sha256(b"SHARD-TUNNEL-HFINAL\x00" + h2 + enc_peer_auth).digest()
            k_c2s, k_s2c, iv_c2s, iv_s2c = net_identity.derive_session_keys(shared, h_final)
            if peer_authorizer is not None:
                ok_res = peer_authorizer(peer_nid, peer_pub, auth_proof)
                if asyncio.iscoroutine(ok_res):
                    ok_res = await ok_res
                if not ok_res:
                    try:
                        err_aad = net_identity.pack_frame_aad(net_identity.PROTOCOL_TUNNEL_V1, net_identity.MSG_AUTH_ERR, net_identity.FLAG_FIN, 0, 0)
                        err_ct = net_identity.aead_encrypt(k_s2c, iv_s2c, 0, err_aad, b"auth_rejected")
                        await self._send_wire(err_aad + err_ct)
                    except Exception:
                        pass
                    raise TunnelAuthError("Peer rejected by authorizer")
            self._send_key = k_s2c
            self._recv_key = k_c2s
            self._send_iv = iv_s2c
            self._recv_iv = iv_c2s
            self.send_seq = 0
            self.expected_recv_seq = 0
            self.peer_node_id = peer_nid
            self.peer_pubkey_b64 = peer_pub
            self.auth_proof = auth_proof
            self._established = True
            return peer_nid, peer_pub, auth_proof
        except Exception:
            await self.close()
            raise
        finally:
            h_x_priv = None
            k_hs_c2s = None
            k_hs_s2c = None
            shared = None
    async def send_frame(
        self,
        msg_type: int,
        payload: bytes = b"",
        stream_id: int = 0,
        flags: int = 0,
        pad_to: int = 0,
    ) -> None:
        if not self._established or self._closed:
            raise TunnelClosedError("Encrypted channel not open")
        raw_payload = bytes(payload)
        eff_flags = int(flags)
        if pad_to > 0 or (eff_flags & net_identity.FLAG_PADDED):
            eff_flags |= net_identity.FLAG_PADDED
            pad_len = max(0, int(pad_to) - 4 - len(raw_payload))
            plaintext = struct.pack(">I", len(raw_payload)) + raw_payload + (b"\x00" * pad_len)
        else:
            plaintext = raw_payload
        async with self._send_lock:
            if not self._established or self._closed:
                raise TunnelClosedError("Encrypted channel not open")
            seq = self.send_seq
            if seq > 0xFFFFFFFFFFFFFFFF:
                await self.close()
                raise TunnelFrameError("Send sequence space exhausted")
            aad16 = net_identity.pack_frame_aad(net_identity.PROTOCOL_TUNNEL_V1, int(msg_type), eff_flags, int(stream_id), seq)
            ct = net_identity.aead_encrypt(self._send_key, self._send_iv, seq, aad16, plaintext)
            wire = aad16 + ct
            if len(wire) > self.max_frame_bytes:
                raise TunnelFrameError(f"Encrypted frame exceeds max size ({len(wire)} > {self.max_frame_bytes})")
            self.send_seq = seq + 1
            await self._send_wire(wire)
    async def recv_frame(self, timeout: float | None = None) -> tuple[int, int, int, bytes]:
        if not self._established or self._closed:
            raise TunnelClosedError("Encrypted channel not open")
        async with self._recv_lock:
            if not self._established or self._closed:
                raise TunnelClosedError("Encrypted channel not open")
            wire = await self._recv_wire_exact(timeout=timeout)
            aad16 = wire[:16]
            ct = wire[16:]
            try:
                ver, msg_type, flags, stream_id, seq = net_identity.unpack_frame_aad(aad16)
            except Exception as exc:
                await self.close()
                raise TunnelFrameError("Malformed AAD header") from exc
            if ver != net_identity.PROTOCOL_TUNNEL_V1:
                await self.close()
                raise TunnelFrameError(f"Unsupported tunnel version: {ver}")
            if seq != self.expected_recv_seq:
                await self.close()
                raise TunnelReplayError(f"Unexpected frame seq {seq}, expected {self.expected_recv_seq}")
            try:
                plaintext = net_identity.aead_decrypt(self._recv_key, self._recv_iv, seq, aad16, ct)
            except Exception as exc:
                await self.close()
                raise TunnelCryptoError("AEAD authentication failed") from exc
            self.expected_recv_seq = seq + 1
            if flags & net_identity.FLAG_PADDED:
                if len(plaintext) < 4:
                    await self.close()
                    raise TunnelFrameError("Malformed padded frame")
                plen = struct.unpack(">I", plaintext[:4])[0]
                if 4 + plen > len(plaintext):
                    await self.close()
                    raise TunnelFrameError("Padded length out of bounds")
                plaintext = plaintext[4:4 + plen]
            return msg_type, flags, stream_id, plaintext
    async def send_ping(self, payload: bytes = b"") -> None:
        await self.send_frame(net_identity.MSG_PING, payload=payload, stream_id=0, flags=0)
    async def send_close(self, reason: str = "") -> None:
        try:
            if self._established and not self._closed:
                await self.send_frame(net_identity.MSG_CLOSE, payload=reason.encode("utf-8")[:256], stream_id=0, flags=net_identity.FLAG_FIN)
        except Exception:
            pass
    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._established = False
        self._send_key = None
        self._recv_key = None
        self._send_iv = None
        self._recv_iv = None
        await self.transport.close()
class _RelayPeerTunnel:
    def __init__(self, tunnel_id: str, host_id: str, peer_ws: WebSocket, peer_ip: str, max_bytes_per_sec: float, max_frame_bytes: int):
        self.tunnel_id = tunnel_id
        self.tunnel_id_bytes = tunnel_id.encode("ascii")
        self.host_id = host_id
        self.peer_ws = peer_ws
        self.peer_ip = peer_ip
        self.bucket_up = TokenBucket(max_bytes_per_sec, burst_bytes=max(max_frame_bytes, int(max_bytes_per_sec * 0.5)) if max_bytes_per_sec > 0 else None)
        self.bucket_down = TokenBucket(max_bytes_per_sec, burst_bytes=max(max_frame_bytes, int(max_bytes_per_sec * 0.5)) if max_bytes_per_sec > 0 else None)
        self.last_activity = time.monotonic()
        self.closed = False
class _RelayHostEntry:
    def __init__(self, host_id: str, ws: WebSocket, ip: str):
        self.host_id = host_id
        self.ws = ws
        self.ip = ip
        self.tunnels: dict[str, _RelayPeerTunnel] = {}
        self.last_activity = time.monotonic()
        self.send_lock = asyncio.Lock()
        self.closed = False
    async def send_text(self, obj: dict) -> None:
        raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        async with self.send_lock:
            await self.ws.send_text(raw)
    async def send_bytes(self, data: bytes) -> None:
        async with self.send_lock:
            await self.ws.send_bytes(data)
