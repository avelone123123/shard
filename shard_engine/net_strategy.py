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
            "public_port": None,
            "local_port": local_port,
            "endpoints": endpoints,
            "reason": f"{type(exc).__name__}:{exc}",
        }
    finally:
        try:
            sock.close()
        except Exception:
            pass
    ok_eps = [e for e in endpoints if e["ok"]]
    if not ok_eps:
        return {
            "ok": False,
            "offline_only": False,
            "nat_type": "udp_blocked",
            "symmetric_nat": None,
            "behind_nat": None,
            "public_ip": None,
            "public_port": None,
            "local_port": local_port,
            "endpoints": endpoints,
            "reason": "no_stun_response",
        }
    mappings = {(e["public_ip"], e["public_port"]) for e in ok_eps}
    ports_seen = {e["public_port"] for e in ok_eps}
    if len(ok_eps) >= 2:
        if len(mappings) > 1 or len(ports_seen) > 1:
            symmetric_nat = True
            nat_type = "symmetric_nat"
        else:
            symmetric_nat = False
            nat_type = "cone_or_compatible"
    else:
        symmetric_nat = None
        nat_type = "unknown"
    pub_ip = ok_eps[0]["public_ip"]
    pub_port = ok_eps[0]["public_port"]
    try:
        local_ip_set = set(lan.local_ips())
        behind_nat = pub_ip not in local_ip_set
    except Exception:
        behind_nat = True
    return {
        "ok": True,
        "offline_only": False,
        "nat_type": nat_type,
        "symmetric_nat": symmetric_nat,
        "behind_nat": behind_nat,
        "public_ip": pub_ip,
        "public_port": pub_port,
        "local_port": local_port,
        "endpoints": endpoints,
        "reason": "ok",
    }
def udp_punch_exchange(bind_port, peer_host, peer_port, nonce_token, timeout_s=1.5):
    tok = str(nonce_token or "").strip()
    if not tok or len(tok) > 128 or any(ord(c) < 33 or ord(c) == 127 for c in tok):
        return {"ok": False, "rtt_ms": None, "local_port": None, "remote": None, "error": "invalid_nonce_token"}
    try:
        bp = int(bind_port or 0)
        pp = int(peer_port)
        if not (0 <= bp <= 65535) or not (1 <= pp <= 65535):
            return {"ok": False, "rtt_ms": None, "local_port": None, "remote": None, "error": "invalid_port"}
    except Exception:
        return {"ok": False, "rtt_ms": None, "local_port": None, "remote": None, "error": "invalid_port"}
    host_s = str(peer_host or "").strip()
    if not host_s:
        return {"ok": False, "rtt_ms": None, "local_port": None, "remote": None, "error": "invalid_peer_host"}
    tok_b = tok.encode("utf-8")
    punch_pkt = _PUNCH_PREFIX + tok_b
    ack_pkt = _PUNCH_ACK_PREFIX + tok_b
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if sys.platform.startswith("win") and hasattr(sock, "ioctl"):
        try:
            sock.ioctl(0x9800000C, False)
        except Exception:
            pass
    local_port = None
    try:
        sock.bind(("0.0.0.0", bp))
        local_port = sock.getsockname()[1]
        peer_ip = socket.gethostbyname(host_s)
        target = (peer_ip, pp)
        deadline = time.monotonic() + max(0.1, float(timeout_s))
        t_first = time.perf_counter()
        next_send = 0.0
        while True:
            now_m = time.monotonic()
            if now_m >= deadline:
                return {"ok": False, "rtt_ms": None, "local_port": local_port, "remote": None, "error": "timeout"}
            if now_m >= next_send:
                try:
                    sock.sendto(punch_pkt, target)
                except OSError:
                    pass
                next_send = now_m + 0.18
            rem = max(0.02, min(0.15, deadline - time.monotonic()))
            sock.settimeout(rem)
            try:
                data, addr = sock.recvfrom(1024)
            except (socket.timeout, ConnectionResetError, BlockingIOError):
                continue
            if len(data) == len(ack_pkt) and hmac.compare_digest(data, ack_pkt):
                return {
                    "ok": True,
                    "rtt_ms": round((time.perf_counter() - t_first) * 1000.0, 3),
                    "local_port": local_port,
                    "remote": f"{addr[0]}:{addr[1]}",
                    "mode": "ack",
                }
            if len(data) == len(punch_pkt) and hmac.compare_digest(data, punch_pkt):
                try:
                    sock.sendto(ack_pkt, addr)
                    sock.sendto(ack_pkt, addr)
                except OSError:
                    pass
                return {
                    "ok": True,
                    "rtt_ms": round((time.perf_counter() - t_first) * 1000.0, 3),
                    "local_port": local_port,
                    "remote": f"{addr[0]}:{addr[1]}",
                    "mode": "echo",
                }
    except Exception as exc:
        return {"ok": False, "rtt_ms": None, "local_port": local_port, "remote": None, "error": f"{type(exc).__name__}:{exc}"}
    finally:
        try:
            sock.close()
        except Exception:
            pass
def _normalize_http_base(url):
    raw = str(url or "").strip()
    if not raw:
        return None
    if not raw.lower().startswith(("http://", "https://")):
        raw = "http://" + raw
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc}".rstrip("/")
def probe_direct_http(url, expected_node_id=None, identity=None, timeout=2.0, session=None):
    base = _normalize_http_base(url)
    if not base:
        return {"ok": False, "url": str(url or ""), "rtt_ms": None, "ping": None, "bootstrap": None, "node_id": None, "reason": "invalid_url"}
    to_s = max(0.2, float(timeout))
    own_sess = session is None
    sess = session
    if own_sess:
        sess = requests.Session()
        sess.trust_env = False
    try:
        headers = {"Accept": "application/json"}
        if identity is not None and getattr(identity, "node_id", None):
            headers["X-Shard-Peer-Node"] = str(identity.node_id)
        t0 = time.perf_counter()
        resp = sess.get(f"{base}/v1/ping", headers=headers, timeout=to_s, allow_redirects=False)
        rtt_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        if int(resp.status_code) != 200:
            return {"ok": False, "url": base, "rtt_ms": rtt_ms, "ping": None, "bootstrap": None, "node_id": None, "reason": f"http_{resp.status_code}"}
        try:
            ping_data = resp.json()
        except Exception:
            return {"ok": False, "url": base, "rtt_ms": rtt_ms, "ping": None, "bootstrap": None, "node_id": None, "reason": "invalid_ping_json"}
        if not isinstance(ping_data, dict) or (ping_data.get("status") != "ok" and "version" not in ping_data):
            return {"ok": False, "url": base, "rtt_ms": rtt_ms, "ping": ping_data if isinstance(ping_data, dict) else None, "bootstrap": None, "node_id": None, "reason": "invalid_ping_payload"}
        boot_data = None
        try:
            b_resp = sess.get(f"{base}/api/client/bootstrap", headers=headers, timeout=to_s, allow_redirects=False)
            if int(b_resp.status_code) == 200:
                cand = b_resp.json()
                if isinstance(cand, dict):
                    boot_data = cand
        except Exception:
            boot_data = None
        found_nid = ping_data.get("node_id") or (boot_data.get("node_id") if isinstance(boot_data, dict) else None)
        found_nid = str(found_nid).strip() if found_nid else None
        if expected_node_id:
            exp = str(expected_node_id).strip()
            if not found_nid:
                return {"ok": False, "url": base, "rtt_ms": rtt_ms, "ping": ping_data, "bootstrap": boot_data, "node_id": None, "reason": "node_id_unverified"}
            if not hmac.compare_digest(found_nid, exp):
                return {"ok": False, "url": base, "rtt_ms": rtt_ms, "ping": ping_data, "bootstrap": boot_data, "node_id": found_nid, "reason": "node_id_mismatch"}
        return {"ok": True, "url": base, "rtt_ms": rtt_ms, "ping": ping_data, "bootstrap": boot_data, "node_id": found_nid, "reason": "ok"}
    except Exception as exc:
        return {"ok": False, "url": base, "rtt_ms": None, "ping": None, "bootstrap": None, "node_id": None, "reason": f"{type(exc).__name__}:{exc}"}
    finally:
        if own_sess:
            try:
                sess.close()
            except Exception:
                pass
def _run_coro_sync(coro, timeout=None):
    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False
    if not in_loop:
        return asyncio.run(coro)
    box = {}
    err = {}
    def _worker():
        try:
            box["val"] = asyncio.run(coro)
        except BaseException as exc:
            err["exc"] = exc
    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    th.join(timeout=timeout)
    if th.is_alive():
        raise TimeoutError("Async operation timed out")
    if "exc" in err:
        raise err["exc"]
    return box.get("val")
def probe_direct_e2e_tcp(host, port, expected_host_id, identity, auth_proof=None, timeout=3.0):
    host_s = str(host or "").strip().strip("[]")
    if not host_s:
        return {"ok": False, "endpoint": "", "rtt_ms": None, "node_id": None, "reason": "invalid_host"}
    try:
        port_num = int(port)
        if not (1 <= port_num <= 65535):
            return {"ok": False, "endpoint": f"{host_s}:{port}", "rtt_ms": None, "node_id": None, "reason": "invalid_port"}
    except Exception:
        return {"ok": False, "endpoint": f"{host_s}:{port}", "rtt_ms": None, "node_id": None, "reason": "invalid_port"}
    ep = f"[{host_s}]:{port_num}" if ":" in host_s else f"{host_s}:{port_num}"
    if not isinstance(expected_host_id, str) or not net_identity.NODE_ID_RE.match(expected_host_id):
        return {"ok": False, "endpoint": ep, "rtt_ms": None, "node_id": None, "reason": "invalid_host_id"}
    to_s = max(0.3, float(timeout))
    async def _probe():
        agent = net_tunnel.ClientTunnelAgent(
            identity=identity,
            target_host_id=expected_host_id,
            mode="direct",
            direct_endpoints=[ep],
            auth_proof=auth_proof,
            connect_timeout_s=to_s,
            ping_timeout_s=to_s,
            reconnect=False,
            verify_ping=True,
        )
        try:
            ok = await agent.connect()
            st = agent.status()
            if ok and agent.state == TunnelState.CONNECTED and st.get("rtt_ms") is not None:
                return {"ok": True, "endpoint": ep, "rtt_ms": st.get("rtt_ms"), "node_id": expected_host_id, "reason": "ok"}
            return {"ok": False, "endpoint": ep, "rtt_ms": None, "node_id": None, "reason": st.get("last_error") or "probe_failed"}
        except Exception as exc:
            return {"ok": False, "endpoint": ep, "rtt_ms": None, "node_id": None, "reason": f"{type(exc).__name__}:{exc}"}
        finally:
            try:
                await agent.disconnect()
            except Exception:
                pass
    return _run_coro_sync(_probe(), timeout=to_s * 3.0 + 2.0)
def _extract_host_port(endpoint, default_port=None):
    raw = str(endpoint or "").strip()
    if not raw:
        return "", default_port
    if "://" in raw:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").strip()
        port = parsed.port or default_port
        return host, port
    if raw.startswith("[") and "]:" in raw:
        host, port_s = raw[1:].split("]:", 1)
        try:
            return host.strip(), int(port_s)
        except Exception:
            return host.strip(), default_port
    if raw.startswith("[") and raw.endswith("]"):
        return raw[1:-1].strip(), default_port
    if ":" in raw and raw.count(":") == 1:
        host, port_s = raw.rsplit(":", 1)
        try:
            return host.strip(), int(port_s)
        except Exception:
            return host.strip(), default_port
    return raw, default_port
def _local_vpn_networks():
    nets = list(_VPN_NETS)
    try:
        for item in lan.local_interfaces():
            if item.get("kind") == "vpn" and item.get("cidr"):
                try:
                    nets.append(ipaddress.ip_network(item["cidr"], strict=False))
                except Exception:
                    pass
    except Exception:
        pass
    return nets
def classify_endpoint_path(endpoint):
    raw = str(endpoint or "").strip()
    if raw.lower().startswith(("ws://", "wss://")):
        return "relay"
    host, _ = _extract_host_port(raw)
    low = host.lower()
    if not low:
        return "direct_p2p"
    if lan.is_loopback(low) or low == "localhost.localdomain":
        return "same_machine"
    try:
        ip_obj = ipaddress.ip_address(low)
    except ValueError:
        return "direct_p2p"
    if ip_obj.is_loopback:
        return "same_machine"
    if ip_obj.version == 4:
        for net in _local_vpn_networks():
            if ip_obj in net:
                return "direct_p2p"
        for net in _LAN_NETS_V4:
            if ip_obj in net:
                return "direct_lan"
        return "direct_p2p"
    for net in _LAN_NETS_V6:
        if ip_obj in net:
            return "direct_lan"
    return "direct_p2p"
def _is_local_or_lan_endpoint(endpoint):
    ptype = classify_endpoint_path(endpoint)
    return ptype in ("same_machine", "direct_lan")
def parse_connect_target(target, default_port=None):
    raw = str(target or "").strip()
    if not raw:
        raise ValueError("Connection target is required")
    if raw.lower().startswith("shard://"):
        parsed = net_identity.parse_invite_uri(raw)
        return {"kind": "shard_uri", **parsed}
    if net_identity.NODE_ID_RE.match(raw):
        return {"kind": "node_id", "node_id": raw}
    if raw.lower().startswith(("ws://", "wss://")):
        return {"kind": "relay", "relay_url": raw}
    if raw.lower().startswith(("http://", "https://")):
        host, port = _extract_host_port(raw, default_port=default_port or lan.active_http_port())
        return {"kind": "http", "host": host, "port": int(port), "url": _normalize_http_base(raw)}
    host, port = _extract_host_port(raw, default_port=default_port)
    clean_host, err = lan.validate_master_host(host)
    if err:
        raise ValueError(err)
    return {"kind": "host_port", "host": clean_host, "port": int(port) if port is not None else None}
class _LoopRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self._loop = None
        self._thread = None
        self._ready = threading.Event()
    def _ensure_started(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive() and self._loop is not None:
                return self._loop
            self._ready.clear()
            loop = asyncio.new_event_loop()
            self._loop = loop
            def _run():
                asyncio.set_event_loop(loop)
                self._ready.set()
                try:
                    loop.run_forever()
                finally:
                    loop.close()
            self._thread = threading.Thread(target=_run, daemon=True)
            self._thread.start()
        self._ready.wait(timeout=5.0)
        return self._loop
    def run_coro(self, coro, timeout=20.0):
        loop = self._ensure_started()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)
    def stop(self):
        with self._lock:
            loop = self._loop
            thread = self._thread
            self._loop = None
            self._thread = None
        if loop is None or thread is None:
            return
        if loop.is_running():
            async def _drain():
                cur = asyncio.current_task()
                tasks = [t for t in asyncio.all_tasks() if t is not cur and not t.done()]
                for t in tasks:
                    t.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                loop.stop()
            try:
                fut = asyncio.run_coroutine_threadsafe(_drain(), loop)
                fut.result(timeout=4.0)
            except Exception:
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except Exception:
                    pass
        if thread.is_alive():
            thread.join(timeout=3.0)
def _build_failure_alternatives(offline_only, relay_urls, allow_relay_fallback, direct_endpoints, http_endpoints):
    alts = []
    if offline_only:
        alts.append("offline_only is enabled: disable offline_only to allow P2P over internet or Relay fallback")
    if not relay_urls:
        alts.append("Configure one or more self-hosted or community Relay URLs (wss://...) for NAT traversal fallback")
    elif not allow_relay_fallback:
        alts.append("Enable allow_relay_fallback=True to allow E2E encrypted Relay fallback when direct connection fails")
    if direct_endpoints or http_endpoints:
        alts.append("Use a mesh VPN (WireGuard, Tailscale, Radmin VPN, or ZeroTier) and connect directly to the peer mesh IP")
        alts.append("Ensure firewall allows inbound TCP on the SHARD port or forward the TCP port on your router")
    else:
        alts.append("Provide a valid shard:// invite URI, direct host:port, or configure Discovery/Relay URLs")
    return alts
def resolve_and_connect(
    identity,
    target_host_id=None,
    direct_endpoints=(),
    http_endpoints=(),
    relay_urls=(),
    auth_proof=None,
    preferred_mode="auto",
    offline_only=False,
    allow_relay_fallback=True,
    allow_p2p_direct=True,
    allow_insecure_local=False,
    connect_timeout_s=5.0,
    ping_timeout_s=3.0,
    reconnect=True,
    verify_node_id=None,
    runner=None,
):
    pref = str(preferred_mode or "auto").strip().lower()
    if pref not in VALID_CONNECT_MODES:
        raise ValueError(f"Unsupported preferred_mode: {preferred_mode}")
    exp_node_id = verify_node_id or target_host_id
    if exp_node_id and (not isinstance(exp_node_id, str) or not net_identity.NODE_ID_RE.match(exp_node_id)):
        raise ValueError("Invalid target or verify node_id")
    buckets = {p: [] for p in PATH_PRIORITY}
    seen_candidates = set()
    for ep in direct_endpoints or ():
        s = str(ep or "").strip()
        if not s:
            continue
