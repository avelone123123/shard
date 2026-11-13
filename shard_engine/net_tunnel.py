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
