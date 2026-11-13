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
class RelayHub:
    def __init__(
        self,
        max_conns_per_ip: int = DEFAULT_RELAY_MAX_CONNS_PER_IP,
        max_tunnels_per_host: int = DEFAULT_RELAY_MAX_TUNNELS_PER_HOST,
        max_frame_bytes: int = MAX_FRAME_BYTES,
        max_bytes_per_sec: float = DEFAULT_RELAY_MAX_BYTES_PER_SEC,
        max_bandwidth_kbps: float | None = None,
        idle_timeout_s: float = DEFAULT_RELAY_IDLE_TIMEOUT_S,
        auth_token: str | None = None,
        auth_timeout_s: float = 10.0,
        on_frame=None,
    ):
        self.max_conns_per_ip = max(1, int(max_conns_per_ip))
        self.max_tunnels_per_host = max(1, int(max_tunnels_per_host))
        self.max_frame_bytes = max(64, int(max_frame_bytes))
        eff_bytes_sec = float(max_bandwidth_kbps) * 125.0 if max_bandwidth_kbps is not None else float(max_bytes_per_sec)
        self.max_bytes_per_sec = eff_bytes_sec
        self.idle_timeout_s = max(1.0, float(idle_timeout_s))
        self.auth_token = str(auth_token) if auth_token else None
        self.auth_timeout_s = max(1.0, float(auth_timeout_s))
        self.on_frame = on_frame
        self._hosts: dict[str, _RelayHostEntry] = {}
        self._ip_conns: dict[str, int] = {}
        self._bytes_relayed = 0
        self._dropped_frames = 0
        self._started_at = time.monotonic()
        self._sweep_task: asyncio.Task | None = None
    def stats(self) -> dict:
        active_tunnels = sum(len(h.tunnels) for h in self._hosts.values())
        return {
            "host_count": len(self._hosts),
            "active_tunnels": active_tunnels,
            "bytes_relayed": self._bytes_relayed,
            "dropped_frames": self._dropped_frames,
        }
    def _acquire_ip_slot(self, ip: str) -> bool:
        cur = self._ip_conns.get(ip, 0)
        if cur >= self.max_conns_per_ip:
            return False
        self._ip_conns[ip] = cur + 1
        return True
    def _release_ip_slot(self, ip: str) -> None:
        cur = self._ip_conns.get(ip, 0)
        if cur <= 1:
            self._ip_conns.pop(ip, None)
        else:
            self._ip_conns[ip] = cur - 1
    def _emit_frame_hook(self, direction: str, host_id: str, tunnel_id: str, payload: bytes) -> None:
        if self.on_frame is not None:
            try:
                self.on_frame(direction, host_id, tunnel_id, payload)
            except Exception:
                pass
    def _verify_relay_auth(self, host_id: str, challenge: str, msg: dict) -> tuple[bool, str]:
        if not isinstance(msg, dict):
            return False, "invalid_json"
        if self.auth_token is not None:
            cand = str(msg.get("auth_token") or msg.get("token") or "")
            if not cand or not hmac.compare_digest(cand, self.auth_token):
                return False, "invalid_relay_token"
        pubkey = msg.get("pubkey") if msg.get("pubkey") is not None else msg.get("pubkey_b64")
        sig = msg.get("sig")
        ts = msg.get("ts")
        msg_hid = msg.get("host_id")
        if msg_hid is not None and str(msg_hid) != host_id:
            return False, "host_id_mismatch"
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            return False, "invalid_ts"
        if abs(time.time() - float(ts)) > net_identity.MAX_CLOCK_SKEW_S:
            return False, "ts_skew"
        signed_data = f"relay-reg:{host_id}:{challenge}:{ts}".encode("utf-8")
        if net_identity.verify_sig(pubkey, host_id, net_identity.DOMAIN_RELAY_REG, signed_data, sig):
            return True, "ok"
        if isinstance(ts, float) and ts.is_integer():
            alt_data = f"relay-reg:{host_id}:{challenge}:{int(ts)}".encode("utf-8")
            if net_identity.verify_sig(pubkey, host_id, net_identity.DOMAIN_RELAY_REG, alt_data, sig):
                return True, "ok"
        return False, "invalid_sig"
    async def _close_peer_tunnel(self, host_entry: _RelayHostEntry, tunnel_id: str, notify_host: bool = True, code: int = 1000, reason: str = "closed") -> None:
        pt = host_entry.tunnels.pop(tunnel_id, None)
        if pt is None or pt.closed:
            return
        pt.closed = True
        try:
            await asyncio.wait_for(pt.peer_ws.close(code=code, reason=reason), timeout=1.0)
        except Exception:
            pass
        if notify_host and not host_entry.closed:
            try:
                await host_entry.send_text({"type": "peer_close", "tunnel_id": tunnel_id})
            except Exception:
                pass
    async def _close_host_entry(self, entry: _RelayHostEntry, code: int = 1000, reason: str = "replaced") -> None:
        if entry.closed:
            return
        entry.closed = True
        for tid in list(entry.tunnels.keys()):
            await self._close_peer_tunnel(entry, tid, notify_host=False, code=code, reason=reason)
        try:
            await asyncio.wait_for(entry.ws.close(code=code, reason=reason), timeout=1.0)
        except Exception:
            pass
    async def start_watchdog(self) -> None:
        if self._sweep_task is not None and not self._sweep_task.done():
            return
        self._sweep_task = asyncio.create_task(self._watchdog_loop())
    async def stop_watchdog(self) -> None:
        t = self._sweep_task
        self._sweep_task = None
        if t is not None:
            t.cancel()
            try:
                await t
            except BaseException:
                pass
        for entry in list(self._hosts.values()):
            await self._close_host_entry(entry, code=1001, reason="shutdown")
        self._hosts.clear()
    async def _watchdog_loop(self) -> None:
        interval = min(5.0, max(0.25, self.idle_timeout_s / 3.0))
        while True:
            await asyncio.sleep(interval)
            now = time.monotonic()
            for entry in list(self._hosts.values()):
                for tid, pt in list(entry.tunnels.items()):
                    if (now - pt.last_activity) > self.idle_timeout_s:
                        await self._close_peer_tunnel(entry, tid, notify_host=True, code=4408, reason="idle_timeout")
                if (now - entry.last_activity) > self.idle_timeout_s and not entry.tunnels:
                    if self._hosts.get(entry.host_id) is entry:
                        self._hosts.pop(entry.host_id, None)
                    await self._close_host_entry(entry, code=4408, reason="idle_timeout")
    async def handle_host_ws(self, ws: WebSocket, host_id: str) -> None:
        ip = ws.client.host if ws.client else "unknown"
        if not self._acquire_ip_slot(ip):
            await ws.close(code=1008)
            return
        entry: _RelayHostEntry | None = None
        try:
            await ws.accept()
            if not isinstance(host_id, str) or not net_identity.NODE_ID_RE.match(host_id):
                await ws.close(code=4400, reason="invalid_host_id")
                return
            challenge = secrets.token_hex(32)
            await ws.send_text(json.dumps({"type": "challenge", "challenge": challenge}, separators=(",", ":")))
            try:
                raw_auth = await asyncio.wait_for(ws.receive_text(), timeout=self.auth_timeout_s)
                auth_msg = json.loads(raw_auth)
            except Exception:
                await ws.close(code=4401, reason="auth_timeout_or_invalid")
                return
            ok, reason = self._verify_relay_auth(host_id, challenge, auth_msg)
            if not ok:
                await ws.close(code=4401, reason=reason)
                return
            old_entry = self._hosts.get(host_id)
            entry = _RelayHostEntry(host_id, ws, ip)
            self._hosts[host_id] = entry
            if old_entry is not None and old_entry is not entry:
                await self._close_host_entry(old_entry, code=4409, reason="replaced")
            await entry.send_text({"type": "registered", "host_id": host_id})
            while not entry.closed:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                entry.last_activity = time.monotonic()
                raw_bytes = msg.get("bytes")
                raw_text = msg.get("text")
                if raw_bytes is not None:
                    if len(raw_bytes) < 16 or len(raw_bytes) > 16 + self.max_frame_bytes:
                        self._dropped_frames += 1
                        continue
                    try:
                        tid = raw_bytes[:16].decode("ascii")
                    except UnicodeDecodeError:
                        self._dropped_frames += 1
                        continue
                    payload = bytes(raw_bytes[16:])
                    if len(payload) == 0 or len(payload) > self.max_frame_bytes:
                        self._dropped_frames += 1
                        continue
                    pt = entry.tunnels.get(tid)
                    if pt is None or pt.closed:
                        self._dropped_frames += 1
                        continue
                    allowed = await pt.bucket_down.acquire(len(payload))
                    if not allowed:
                        self._dropped_frames += 1
                        continue
                    pt.last_activity = time.monotonic()
                    self._emit_frame_hook("host_to_peer", host_id, tid, payload)
                    try:
                        await pt.peer_ws.send_bytes(payload)
                        self._bytes_relayed += len(payload)
                    except Exception:
                        await self._close_peer_tunnel(entry, tid, notify_host=True, code=1011, reason="peer_send_error")
                elif raw_text is not None:
                    try:
                        ctrl = json.loads(raw_text)
                    except Exception:
                        continue
                    if not isinstance(ctrl, dict):
                        continue
                    ctype = ctrl.get("type")
                    if ctype == "ping":
                        await entry.send_text({"type": "pong"})
                    elif ctype == "peer_close":
                        tid = str(ctrl.get("tunnel_id") or "")
                        if tid in entry.tunnels:
                            await self._close_peer_tunnel(entry, tid, notify_host=False, code=1000, reason="host_closed")
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            if entry is not None:
                if self._hosts.get(host_id) is entry:
                    self._hosts.pop(host_id, None)
                await self._close_host_entry(entry, code=1001, reason="host_disconnected")
            self._release_ip_slot(ip)
    async def handle_peer_ws(self, ws: WebSocket, host_id: str) -> None:
        ip = ws.client.host if ws.client else "unknown"
        if not self._acquire_ip_slot(ip):
            await ws.close(code=1008)
            return
        host_entry: _RelayHostEntry | None = None
        tunnel_id = ""
        try:
            await ws.accept()
            if not isinstance(host_id, str) or not net_identity.NODE_ID_RE.match(host_id):
                await ws.close(code=4400, reason="invalid_host_id")
                return
            host_entry = self._hosts.get(host_id)
            if host_entry is None or host_entry.closed:
                await ws.close(code=4404, reason="host_offline")
                return
            if len(host_entry.tunnels) >= self.max_tunnels_per_host:
                await ws.close(code=4429, reason="too_many_tunnels")
                return
            tunnel_id = secrets.token_hex(8)
            pt = _RelayPeerTunnel(tunnel_id, host_id, ws, ip, self.max_bytes_per_sec, self.max_frame_bytes)
            host_entry.tunnels[tunnel_id] = pt
            try:
                await host_entry.send_text({"type": "peer_connect", "tunnel_id": tunnel_id})
            except Exception:
                await self._close_peer_tunnel(host_entry, tunnel_id, notify_host=False, code=1011, reason="host_send_error")
                return
            while not pt.closed and not host_entry.closed:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                raw_bytes = msg.get("bytes")
                if raw_bytes is None:
                    self._dropped_frames += 1
                    await ws.close(code=1003, reason="binary_only")
                    break
                if len(raw_bytes) == 0 or len(raw_bytes) > self.max_frame_bytes:
                    self._dropped_frames += 1
                    await ws.close(code=1009, reason="frame_too_large")
                    break
                allowed = await pt.bucket_up.acquire(len(raw_bytes))
                if not allowed:
                    self._dropped_frames += 1
                    continue
                now = time.monotonic()
                pt.last_activity = now
                host_entry.last_activity = now
                payload = bytes(raw_bytes)
                self._emit_frame_hook("peer_to_host", host_id, tunnel_id, payload)
                try:
                    await host_entry.send_bytes(pt.tunnel_id_bytes + payload)
                    self._bytes_relayed += len(payload)
                except Exception:
                    break
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            if host_entry is not None and tunnel_id:
                await self._close_peer_tunnel(host_entry, tunnel_id, notify_host=True, code=1000, reason="peer_disconnected")
            self._release_ip_slot(ip)
def create_relay_app(hub: RelayHub | None = None, **hub_kwargs) -> FastAPI:
    relay_hub = hub if hub is not None else RelayHub(**hub_kwargs)
    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        await relay_hub.start_watchdog()
        try:
            yield
        finally:
            await relay_hub.stop_watchdog()
    app = FastAPI(title="SHARD Blind Relay", docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)
    app.state.relay_hub = relay_hub
    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}
    @app.get("/v1/relay/stats")
    async def relay_stats():
        return JSONResponse(content=relay_hub.stats())
    @app.websocket("/v1/relay/host")
    async def relay_host(ws: WebSocket, host_id: str = ""):
        await relay_hub.handle_host_ws(ws, host_id)
    @app.websocket("/v1/relay/connect")
    async def relay_connect(ws: WebSocket, host_id: str = ""):
        await relay_hub.handle_peer_ws(ws, host_id)
    return app
async def start_relay_server(host: str = "127.0.0.1", port: int = 0, hub: RelayHub | None = None, **hub_kwargs):
    import uvicorn
    app = create_relay_app(hub=hub, **hub_kwargs)
    sock = socket.socket(socket.AF_INET6 if ":" in host and host != "0.0.0.0" else socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, int(port)))
    sock.listen(128)
    sock.setblocking(False)
    actual_port = sock.getsockname()[1]
    cfg = uvicorn.Config(app, host=host, port=actual_port, log_level="warning", access_log=False)
    server = uvicorn.Server(cfg)
    server.install_signal_handlers = lambda: None
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started and not task.done():
        await asyncio.sleep(0.02)
    return app.state.relay_hub, server, task, actual_port
def serve_relay(host: str = "0.0.0.0", port: int = 8091, hub: RelayHub | None = None, ssl_certfile: str | None = None, ssl_keyfile: str | None = None, **hub_kwargs) -> None:
    import uvicorn
    app = create_relay_app(hub=hub, **hub_kwargs)
    kwargs = {"host": host, "port": int(port), "log_level": "info", "access_log": False}
    if ssl_certfile:
        kwargs["ssl_certfile"] = ssl_certfile
    if ssl_keyfile:
        kwargs["ssl_keyfile"] = ssl_keyfile
    uvicorn.run(app, **kwargs)
class HostTunnelAgent:
    def __init__(
        self,
        identity: net_identity.NodeIdentity,
        asgi_app=None,
        local_http_url: str | None = None,
        p2p_host: str = "0.0.0.0",
        p2p_port: int | None = None,
        relay_urls=(),
        relay_auth_token: str | None = None,
        peer_authorizer=None,
        on_http_request=None,
        rpc_allowed_ports=(),
        bridge_secret: str | None = None,
        allow_insecure_local: bool | None = None,
        handshake_timeout_s: float = 10.0,
        max_req_body_bytes: int = MAX_REQ_BODY_BYTES,
        max_tunnels: int = DEFAULT_RELAY_MAX_TUNNELS_PER_HOST,
    ):
        self.identity = identity
        self.asgi_app = asgi_app
        self.local_http_url = _validate_local_http_url(local_http_url) if local_http_url else None
        if self.asgi_app is None and self.local_http_url is None:
            raise ValueError("HostTunnelAgent requires asgi_app or local_http_url")
        self.p2p_host = str(p2p_host or "0.0.0.0")
        self.p2p_port = p2p_port
        self.bound_p2p_port: int | None = None
        self.relay_urls = [str(u).strip() for u in (relay_urls or ()) if str(u).strip()]
        self.relay_auth_token = str(relay_auth_token) if relay_auth_token else None
        self.peer_authorizer = peer_authorizer
        self.on_http_request = on_http_request
        self.rpc_allowed_ports = frozenset(int(p) for p in (rpc_allowed_ports or ()) if 1 <= int(p) <= 65535)
        self._bridge_secret = str(bridge_secret) if bridge_secret else _BRIDGE_SECRET
        self.allow_insecure_local = allow_insecure_local
        self.handshake_timeout_s = float(handshake_timeout_s)
        self.max_req_body_bytes = int(max_req_body_bytes)
        self.max_tunnels = max(1, int(max_tunnels))
        self._stop = False
        self._tcp_server: asyncio.AbstractServer | None = None
        self._relay_tasks: list[asyncio.Task] = []
        self._peer_tasks: set[asyncio.Task] = set()
        self._active_channels: set[EncryptedChannel] = set()
        self._relay_connected: dict[str, bool] = {}
        self._http_client: httpx.AsyncClient | None = None
        self._last_error: str | None = None
    @property
    def active_peer_count(self) -> int:
        return len(self._active_channels)
    def status(self) -> dict:
        return {
            "node_id": self.identity.node_id,
            "p2p_port": self.bound_p2p_port,
            "relays": dict(self._relay_connected),
            "active_peers": len(self._active_channels),
            "last_error": self._last_error,
        }
    async def start(self) -> None:
        self._stop = False
        if self.local_http_url and self._http_client is None:
            self._http_client = httpx.AsyncClient(base_url=self.local_http_url, trust_env=False, timeout=httpx.Timeout(60.0, connect=5.0))
        if self.p2p_port is not None and self._tcp_server is None:
            self._tcp_server = await asyncio.start_server(self._handle_tcp_conn, self.p2p_host, int(self.p2p_port))
            if self._tcp_server.sockets:
                self.bound_p2p_port = self._tcp_server.sockets[0].getsockname()[1]
        for rurl in self.relay_urls:
            self._relay_connected[rurl] = False
            task = asyncio.create_task(self._relay_mux_loop(rurl))
            self._relay_tasks.append(task)
    async def stop(self) -> None:
        self._stop = True
        if self._tcp_server is not None:
            self._tcp_server.close()
            try:
                await asyncio.wait_for(self._tcp_server.wait_closed(), timeout=1.5)
            except Exception:
                pass
            self._tcp_server = None
        for ch in list(self._active_channels):
            await ch.send_close("host_shutdown")
            await ch.close()
        self._active_channels.clear()
        for t in list(self._relay_tasks):
            t.cancel()
        for t in list(self._peer_tasks):
            t.cancel()
        for t in list(self._relay_tasks) + list(self._peer_tasks):
            try:
                await t
            except BaseException:
                pass
        self._relay_tasks.clear()
        self._peer_tasks.clear()
        if self._http_client is not None:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
            self._http_client = None
    def _spawn_peer_task(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._peer_tasks.add(t)
        t.add_done_callback(self._peer_tasks.discard)
        return t
    async def _handle_tcp_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._stop or len(self._active_channels) >= self.max_tunnels:
            writer.close()
            return
        tr = TcpFrameTransport(reader, writer)
        self._spawn_peer_task(self._serve_transport(tr))
    async def _relay_mux_loop(self, relay_url: str) -> None:
        backoff = 1.0
        while not self._stop:
            mux_peers: dict[str, MuxPeerTransport] = {}
            ws = None
            try:
                ws_url = _build_relay_ws_url(relay_url, "host", self.identity.node_id, allow_insecure_local=self.allow_insecure_local)
                async with websockets.connect(
                    ws_url,
                    max_size=MAX_FRAME_BYTES + 64,
                    compression=None,
                    proxy=None,
                    open_timeout=10.0,
                    ping_interval=20.0,
                    ping_timeout=20.0,
                ) as ws:
                    raw_chal = await asyncio.wait_for(ws.recv(), timeout=self.handshake_timeout_s)
                    chal_obj = json.loads(raw_chal)
                    challenge = str(chal_obj.get("challenge") or "")
                    if not challenge:
                        raise TunnelAuthError("Missing relay challenge")
                    ts = int(time.time())
                    sign_bytes = f"relay-reg:{self.identity.node_id}:{challenge}:{ts}".encode("utf-8")
                    sig = self.identity.sign_bytes(net_identity.DOMAIN_RELAY_REG, sign_bytes)
                    reg_payload = {
                        "host_id": self.identity.node_id,
                        "pubkey": self.identity.pubkey_b64,
                        "ts": ts,
                        "sig": sig,
                    }
                    if self.relay_auth_token:
                        reg_payload["auth_token"] = self.relay_auth_token
                    await ws.send(json.dumps(reg_payload, separators=(",", ":")))
                    raw_ack = await asyncio.wait_for(ws.recv(), timeout=self.handshake_timeout_s)
                    ack_obj = json.loads(raw_ack)
                    if ack_obj.get("type") != "registered":
                        raise TunnelAuthError("Relay registration failed")
                    self._relay_connected[relay_url] = True
                    backoff = 1.0
                    send_lock = asyncio.Lock()
                    async def _send_mux(data: bytes) -> None:
                        async with send_lock:
                            await ws.send(data)
                    async def _close_mux(tid: str) -> None:
                        mux_peers.pop(tid, None)
                        try:
                            async with send_lock:
                                await ws.send(json.dumps({"type": "peer_close", "tunnel_id": tid}, separators=(",", ":")))
                        except Exception:
                            pass
                    while not self._stop:
                        frame = await ws.recv()
                        if isinstance(frame, str):
                            ev = json.loads(frame)
                            etype = ev.get("type")
                            if etype == "peer_connect":
                                tid = str(ev.get("tunnel_id") or "")
                                if len(tid) == 16 and len(self._active_channels) < self.max_tunnels and tid not in mux_peers:
                                    mtr = MuxPeerTransport(tid, _send_mux, close_cb=_close_mux)
                                    mux_peers[tid] = mtr
                                    self._spawn_peer_task(self._serve_transport(mtr))
                            elif etype == "peer_close":
                                tid = str(ev.get("tunnel_id") or "")
                                mtr = mux_peers.pop(tid, None)
                                if mtr is not None:
                                    mtr.mark_closed()
                        elif isinstance(frame, (bytes, bytearray)):
                            if len(frame) < 16:
                                continue
                            tid = bytes(frame[:16]).decode("ascii", errors="ignore")
                            mtr = mux_peers.get(tid)
                            if mtr is not None:
                                ok = await mtr.feed(bytes(frame[16:]))
                                if not ok:
                                    mux_peers.pop(tid, None)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._last_error = str(exc)
            finally:
                self._relay_connected[relay_url] = False
                for mtr in list(mux_peers.values()):
                    mtr.mark_closed()
                mux_peers.clear()
            if self._stop:
                break
            await asyncio.sleep(backoff)
            backoff = min(10.0, backoff * 2.0)
    async def _serve_transport(self, transport: AsyncTransport) -> None:
        channel = EncryptedChannel(transport, role="host")
        http_bodies: dict[int, tuple[dict, bytearray]] = {}
        http_tasks: set[asyncio.Task] = set()
        rpc_writers: dict[int, asyncio.StreamWriter] = {}
        rpc_tasks: set[asyncio.Task] = set()
        try:
            await channel.perform_host_handshake(self.identity, peer_authorizer=self.peer_authorizer, timeout=self.handshake_timeout_s)
            self._active_channels.add(channel)
            while not self._stop and channel.is_established:
                try:
                    msg_type, flags, stream_id, payload = await channel.recv_frame()
                except TunnelClosedError:
                    break
                if msg_type == net_identity.MSG_PING:
                    await channel.send_frame(net_identity.MSG_PONG, payload=payload, stream_id=0, flags=0)
                elif msg_type == net_identity.MSG_CLOSE:
                    break
                elif msg_type == net_identity.MSG_HTTP_REQ:
                    try:
                        req_meta = json.loads(payload.decode("utf-8"))
                    except Exception:
                        await self._send_http_error(channel, stream_id, 400, "Invalid HTTP request frame")
                        continue
                    if flags & net_identity.FLAG_FIN:
                        t = asyncio.create_task(self._dispatch_http_request(channel, stream_id, req_meta, b""))
                        http_tasks.add(t)
                        t.add_done_callback(http_tasks.discard)
                    else:
                        if len(http_bodies) >= 64:
                            await self._send_http_error(channel, stream_id, 429, "Too many concurrent requests")
                            continue
                        http_bodies[stream_id] = (req_meta, bytearray())
                elif msg_type == net_identity.MSG_HTTP_BODY_CHUNK:
                    entry = http_bodies.get(stream_id)
                    if entry is None:
                        continue
                    req_meta, buf = entry
                    if len(buf) + len(payload) > self.max_req_body_bytes:
                        http_bodies.pop(stream_id, None)
                        await self._send_http_error(channel, stream_id, 413, "Request body too large")
                        continue
                    buf.extend(payload)
                    if flags & net_identity.FLAG_FIN:
                        http_bodies.pop(stream_id, None)
                        t = asyncio.create_task(self._dispatch_http_request(channel, stream_id, req_meta, bytes(buf)))
                        http_tasks.add(t)
                        t.add_done_callback(http_tasks.discard)
                elif msg_type == net_identity.MSG_RPC_OPEN:
                    await self._handle_rpc_open(channel, stream_id, payload, rpc_writers, rpc_tasks)
                elif msg_type == net_identity.MSG_RPC_DATA:
                    writer = rpc_writers.get(stream_id)
                    if writer is not None:
                        try:
                            writer.write(payload)
                            await writer.drain()
                        except Exception:
                            rpc_writers.pop(stream_id, None)
                            try:
                                writer.close()
                                await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
                            except Exception:
                                pass
                            await self._safe_send_rpc_close(channel, stream_id)
                elif msg_type == net_identity.MSG_RPC_CLOSE:
                    writer = rpc_writers.pop(stream_id, None)
                    if writer is not None:
                        try:
                            writer.close()
                            await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
                        except Exception:
                            pass
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._last_error = str(exc)
        finally:
            self._active_channels.discard(channel)
            for t in list(http_tasks) + list(rpc_tasks):
                t.cancel()
            for w in list(rpc_writers.values()):
                try:
                    w.close()
                    await asyncio.wait_for(w.wait_closed(), timeout=0.5)
                except Exception:
                    pass
            await channel.close()
    async def _safe_send_rpc_close(self, channel: EncryptedChannel, stream_id: int, msg: str = "") -> None:
        try:
            if channel.is_established:
                payload = json.dumps({"error": msg}, separators=(",", ":")).encode("utf-8") if msg else b""
                await channel.send_frame(net_identity.MSG_RPC_CLOSE, payload=payload, stream_id=stream_id, flags=net_identity.FLAG_FIN)
        except Exception:
            pass
    async def _handle_rpc_open(self, channel: EncryptedChannel, stream_id: int, payload: bytes, rpc_writers: dict, rpc_tasks: set) -> None:
        try:
            info = json.loads(payload.decode("utf-8"))
            port = info.get("port")
        except Exception:
            await self._safe_send_rpc_close(channel, stream_id, "invalid_rpc_open")
            return
        if isinstance(port, bool) or not isinstance(port, int) or port not in self.rpc_allowed_ports:
            await self._safe_send_rpc_close(channel, stream_id, "rpc_port_not_allowed")
            return
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=5.0)
        except Exception:
            await self._safe_send_rpc_close(channel, stream_id, "rpc_connect_failed")
            return
        rpc_writers[stream_id] = writer
        async def _pump():
            try:
                while channel.is_established:
                    chunk = await reader.read(MAX_CHUNK_BYTES)
                    if not chunk:
                        break
                    await channel.send_frame(net_identity.MSG_RPC_DATA, payload=chunk, stream_id=stream_id, flags=0)
            except Exception:
                pass
            finally:
                rpc_writers.pop(stream_id, None)
                try:
                    writer.close()
                    await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
                except Exception:
                    pass
                await self._safe_send_rpc_close(channel, stream_id)
        t = asyncio.create_task(_pump())
        rpc_tasks.add(t)
        t.add_done_callback(rpc_tasks.discard)
    async def _send_http_error(self, channel: EncryptedChannel, stream_id: int, status: int, message: str) -> None:
        if not channel.is_established:
            return
        body = json.dumps({"status": "error", "message": message}, ensure_ascii=False).encode("utf-8")
        head = json.dumps({"status": int(status), "headers": [["content-type", "application/json"], ["content-length", str(len(body))]]}, separators=(",", ":")).encode("utf-8")
        try:
            await channel.send_frame(net_identity.MSG_HTTP_RESP_HEAD, payload=head, stream_id=stream_id, flags=0)
            await channel.send_frame(net_identity.MSG_HTTP_BODY_CHUNK, payload=body, stream_id=stream_id, flags=net_identity.FLAG_FIN)
        except Exception:
            pass
    async def _dispatch_http_request(self, channel: EncryptedChannel, stream_id: int, req_meta: dict, body: bytes) -> None:
        method = str(req_meta.get("method") or "GET").upper().strip()
        if method not in ALLOWED_TUNNEL_METHODS:
            await self._send_http_error(channel, stream_id, 403, "Method not allowed via tunnel")
            return
        raw_path = req_meta.get("path") or ""
        norm_path = normalize_tunnel_path(raw_path)
        if norm_path is None:
            await self._send_http_error(channel, stream_id, 403, "Path not allowed via tunnel")
            return
        query = str(req_meta.get("query") or "")
        if len(query) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in query):
            await self._send_http_error(channel, stream_id, 400, "Invalid query string")
            return
        clean_headers = sanitize_tunnel_headers(req_meta.get("headers"))
        if self.on_http_request is not None:
            try:
                cb_res = self.on_http_request({
                    "peer_node_id": channel.peer_node_id,
                    "method": method,
                    "path": norm_path,
                    "query": query,
                })
                if asyncio.iscoroutine(cb_res):
                    await cb_res
            except PermissionError as exc:
                await self._send_http_error(channel, stream_id, 403, str(exc))
                return
            except Exception:
                pass
        if self.asgi_app is not None:
            await self._dispatch_asgi(channel, stream_id, method, norm_path, query, clean_headers, body)
        else:
            await self._dispatch_loopback_http(channel, stream_id, method, norm_path, query, clean_headers, body)
    async def _dispatch_asgi(self, channel: EncryptedChannel, stream_id: int, method: str, path: str, query: str, clean_headers: list[tuple[str, str]], body: bytes) -> None:
        asgi_headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in clean_headers]
        asgi_headers.append((b"host", b"127.0.0.1"))
        if body:
            asgi_headers.append((b"content-length", str(len(body)).encode("ascii")))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query.encode("latin-1"),
            "root_path": "",
            "headers": asgi_headers,
            "client": ("127.0.0.1", 0),
            "server": ("127.0.0.1", 8000),
            "shard_tunnel": {"peer_node_id": channel.peer_node_id},
        }
        req_sent = False
        disconnect_ev = asyncio.Event()
        head_sent = False
        fin_sent = False
        async def receive():
            nonlocal req_sent
            if not req_sent:
                req_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnect_ev.wait()
            return {"type": "http.disconnect"}
        async def send(message: dict):
            nonlocal head_sent, fin_sent
            mtype = message.get("type")
            if mtype == "http.response.start":
                head_sent = True
                status = int(message.get("status") or 200)
                hdrs = _sanitize_response_headers(message.get("headers") or ())
                head_bytes = json.dumps({"status": status, "headers": [[k, v] for k, v in hdrs]}, separators=(",", ":")).encode("utf-8")
                await channel.send_frame(net_identity.MSG_HTTP_RESP_HEAD, payload=head_bytes, stream_id=stream_id, flags=0)
            elif mtype == "http.response.body":
                chunk = bytes(message.get("body") or b"")
                more = bool(message.get("more_body", False))
                if not chunk:
                    if not more and not fin_sent:
                        fin_sent = True
                        await channel.send_frame(net_identity.MSG_HTTP_BODY_CHUNK, payload=b"", stream_id=stream_id, flags=net_identity.FLAG_FIN)
                        disconnect_ev.set()
                    return
                offset = 0
                while offset < len(chunk):
                    part = chunk[offset:offset + MAX_CHUNK_BYTES]
                    offset += len(part)
                    is_last = (offset >= len(chunk)) and (not more)
                    fl = net_identity.FLAG_FIN if is_last else 0
                    if is_last:
                        fin_sent = True
                        disconnect_ev.set()
                    await channel.send_frame(net_identity.MSG_HTTP_BODY_CHUNK, payload=part, stream_id=stream_id, flags=fl)
        try:
            await self.asgi_app(scope, receive, send)
            if head_sent and not fin_sent and channel.is_established:
                fin_sent = True
                await channel.send_frame(net_identity.MSG_HTTP_BODY_CHUNK, payload=b"", stream_id=stream_id, flags=net_identity.FLAG_FIN)
            elif not head_sent:
                await self._send_http_error(channel, stream_id, 502, "Empty ASGI response")
        except Exception:
            if not head_sent:
                await self._send_http_error(channel, stream_id, 502, "Local ASGI dispatch failed")
            elif not fin_sent and channel.is_established:
                try:
                    await channel.send_frame(net_identity.MSG_HTTP_BODY_CHUNK, payload=b"", stream_id=stream_id, flags=net_identity.FLAG_FIN)
                except Exception:
                    pass
        finally:
            disconnect_ev.set()
    async def _dispatch_loopback_http(self, channel: EncryptedChannel, stream_id: int, method: str, path: str, query: str, clean_headers: list[tuple[str, str]], body: bytes) -> None:
        if self._http_client is None:
            await self._send_http_error(channel, stream_id, 503, "Local HTTP client unavailable")
            return
        fwd_headers = [(k, v) for k, v in clean_headers if k != "accept-encoding"]
        fwd_headers.append(("accept-encoding", "identity"))
        fwd_headers.append(("x-shard-bridge-token", self._bridge_secret))
        if channel.peer_node_id:
            fwd_headers.append(("x-shard-tunnel-peer", channel.peer_node_id))
        target = f"{path}?{query}" if query else path
        head_sent = False
        fin_sent = False
        try:
            async with self._http_client.stream(method, target, headers=fwd_headers, content=body if body else None) as resp:
                hdrs = _sanitize_response_headers(resp.headers.raw)
                head_bytes = json.dumps({"status": int(resp.status_code), "headers": [[k, v] for k, v in hdrs]}, separators=(",", ":")).encode("utf-8")
                head_sent = True
                await channel.send_frame(net_identity.MSG_HTTP_RESP_HEAD, payload=head_bytes, stream_id=stream_id, flags=0)
                async for chunk in resp.aiter_raw():
                    if not chunk:
                        continue
                    offset = 0
                    while offset < len(chunk):
                        part = chunk[offset:offset + MAX_CHUNK_BYTES]
                        offset += len(part)
                        await channel.send_frame(net_identity.MSG_HTTP_BODY_CHUNK, payload=part, stream_id=stream_id, flags=0)
                fin_sent = True
                await channel.send_frame(net_identity.MSG_HTTP_BODY_CHUNK, payload=b"", stream_id=stream_id, flags=net_identity.FLAG_FIN)
        except Exception:
            if not head_sent:
                await self._send_http_error(channel, stream_id, 502, "Local loopback HTTP target error")
            elif not fin_sent and channel.is_established:
                try:
                    await channel.send_frame(net_identity.MSG_HTTP_BODY_CHUNK, payload=b"", stream_id=stream_id, flags=net_identity.FLAG_FIN)
