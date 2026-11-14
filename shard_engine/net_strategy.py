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
        ptype = classify_endpoint_path(s)
        if ptype == "relay":
            continue
        key = ("e2e_tcp", s)
        if key not in seen_candidates:
            seen_candidates.add(key)
            buckets[ptype].append({"path_type": ptype, "transport": "e2e_tcp", "endpoint": s})
    for h_url in http_endpoints or ():
        base = _normalize_http_base(h_url)
        if not base:
            continue
        ptype = classify_endpoint_path(base)
        if ptype == "relay":
            continue
        key = ("http", base)
        if key not in seen_candidates:
            seen_candidates.add(key)
            buckets[ptype].append({"path_type": ptype, "transport": "http", "endpoint": base})
    for rurl in relay_urls or ():
        s = str(rurl or "").strip()
        if not s:
            continue
        key = ("relay_ws", s)
        if key not in seen_candidates:
            seen_candidates.add(key)
            buckets["relay"].append({"path_type": "relay", "transport": "relay_ws", "endpoint": s})
    ordered = []
    for ptype in PATH_PRIORITY:
        ordered.extend(buckets[ptype])
    attempts = []
    def _exec(coro):
        if runner is not None:
            return runner.run_coro(coro, timeout=float(connect_timeout_s) + float(ping_timeout_s) + 5.0)
        return _run_coro_sync(coro, timeout=float(connect_timeout_s) + float(ping_timeout_s) + 5.0)
    for cand in ordered:
        ptype = cand["path_type"]
        tr_kind = cand["transport"]
        ep = cand["endpoint"]
        if pref == "relay" and ptype != "relay":
            attempts.append({"path_type": ptype, "transport": tr_kind, "endpoint": ep, "ok": False, "rtt_ms": None, "reason": "skipped_mode_relay"})
            continue
        if pref == "direct" and ptype == "relay":
            attempts.append({"path_type": ptype, "transport": tr_kind, "endpoint": ep, "ok": False, "rtt_ms": None, "reason": "skipped_mode_direct"})
            continue
        if pref == "lan" and ptype not in ("same_machine", "direct_lan"):
            attempts.append({"path_type": ptype, "transport": tr_kind, "endpoint": ep, "ok": False, "rtt_ms": None, "reason": "skipped_mode_lan"})
            continue
        if pref == "p2p" and ptype == "relay":
            attempts.append({"path_type": ptype, "transport": tr_kind, "endpoint": ep, "ok": False, "rtt_ms": None, "reason": "skipped_mode_p2p"})
            continue
        if ptype == "direct_p2p" and not allow_p2p_direct:
            attempts.append({"path_type": ptype, "transport": tr_kind, "endpoint": ep, "ok": False, "rtt_ms": None, "reason": "skipped_p2p_disabled"})
            continue
        if ptype == "relay" and pref != "relay" and not allow_relay_fallback:
            attempts.append({"path_type": ptype, "transport": tr_kind, "endpoint": ep, "ok": False, "rtt_ms": None, "reason": "skipped_relay_disabled"})
            continue
        if offline_only and (ptype == "relay" or (ptype == "direct_p2p" and not _is_local_or_lan_endpoint(ep))):
            attempts.append({"path_type": ptype, "transport": tr_kind, "endpoint": ep, "ok": False, "rtt_ms": None, "reason": "skipped_offline_only"})
            continue
        if tr_kind in ("e2e_tcp", "relay_ws"):
            if not exp_node_id:
                attempts.append({"path_type": ptype, "transport": tr_kind, "endpoint": ep, "ok": False, "rtt_ms": None, "reason": "missing_target_node_id"})
                continue
            agent = net_tunnel.ClientTunnelAgent(
                identity=identity,
                target_host_id=exp_node_id,
                mode="relay" if tr_kind == "relay_ws" else "direct",
                direct_endpoints=[ep] if tr_kind == "e2e_tcp" else (),
                relay_urls=[ep] if tr_kind == "relay_ws" else (),
                auth_proof=auth_proof,
                allow_insecure_local=allow_insecure_local,
                connect_timeout_s=float(connect_timeout_s),
                ping_timeout_s=float(ping_timeout_s),
                reconnect=bool(reconnect),
                verify_ping=True,
            )
            try:
                ok = _exec(agent.connect())
            except Exception as exc:
                ok = False
                agent._last_error = f"{type(exc).__name__}:{exc}"
            st = agent.status()
            expected_state = TunnelState.RELAY_CONNECTED.value if tr_kind == "relay_ws" else TunnelState.CONNECTED.value
            if ok and st.get("state") == expected_state and st.get("rtt_ms") is not None and st.get("local_url"):
                attempts.append({"path_type": ptype, "transport": tr_kind, "endpoint": ep, "ok": True, "rtt_ms": st["rtt_ms"], "reason": "ok"})
                return {
                    "ok": True,
                    "state": st["state"],
                    "mode": "relay" if tr_kind == "relay_ws" else "direct",
                    "path_type": ptype,
                    "transport": tr_kind,
                    "endpoint": ep,
                    "relay_url": ep if tr_kind == "relay_ws" else None,
                    "local_url": st["local_url"],
                    "rtt_ms": st["rtt_ms"],
                    "peer_node_id": exp_node_id,
                    "agent": agent,
                    "attempts": attempts,
                    "reason": "ok",
                    "alternatives": [],
                }
            reason = st.get("last_error") or "tunnel_connect_failed"
            try:
                _exec(agent.disconnect())
            except Exception:
                pass
            attempts.append({"path_type": ptype, "transport": tr_kind, "endpoint": ep, "ok": False, "rtt_ms": None, "reason": reason})
            continue
        if tr_kind == "http":
            res = probe_direct_http(ep, expected_node_id=exp_node_id, identity=identity, timeout=min(float(connect_timeout_s), float(ping_timeout_s)))
            attempts.append({"path_type": ptype, "transport": "http", "endpoint": ep, "ok": bool(res["ok"]), "rtt_ms": res.get("rtt_ms"), "reason": res.get("reason")})
            if res["ok"]:
                return {
                    "ok": True,
                    "state": TunnelState.CONNECTED.value,
                    "mode": "direct",
                    "path_type": ptype,
                    "transport": "http",
                    "endpoint": ep,
                    "relay_url": None,
                    "local_url": res["url"],
                    "rtt_ms": res["rtt_ms"],
                    "peer_node_id": res.get("node_id") or exp_node_id,
                    "ping": res.get("ping"),
                    "bootstrap": res.get("bootstrap"),
                    "agent": None,
                    "attempts": attempts,
                    "reason": "ok",
                    "alternatives": [],
                }
    last_reason = next((a["reason"] for a in reversed(attempts) if a.get("reason") and not str(a["reason"]).startswith("skipped_")), None)
    if not last_reason:
        last_reason = "no_reachable_endpoints" if ordered else "no_endpoints_provided"
    return {
        "ok": False,
        "state": TunnelState.FAILED.value,
        "mode": None,
        "path_type": None,
        "transport": None,
        "endpoint": None,
        "relay_url": None,
        "local_url": None,
        "rtt_ms": None,
        "peer_node_id": exp_node_id,
        "agent": None,
        "attempts": attempts,
        "reason": last_reason,
        "alternatives": _build_failure_alternatives(offline_only, relay_urls, allow_relay_fallback, direct_endpoints, http_endpoints),
    }
def network_diagnostics(probe_public=False, offline_only=False, port=None, stun_servers=None, include_platform=True, include_wireguard=True):
    http_port = int(port or lan.active_http_port())
    lan_rep = lan.connectivity_report(probe_public=False)
    stun_rep = stun_nat_profile(stun_servers=stun_servers, offline_only=offline_only) if probe_public else {
        "ok": False,
        "offline_only": bool(offline_only),
        "nat_type": "unknown",
        "symmetric_nat": None,
        "endpoints": [],
        "reason": "skipped",
    }
    wg_rep = net_wireguard.detect_wireguard() if include_wireguard else None
    plat_rep = None
    if include_platform:
        if os.name == "nt":
            plat_rep = net_platform_win.windows_diagnostics(port=http_port, extra_ports=(lan.BEACON_PORT,))
        elif sys.platform.startswith("linux"):
            plat_rep = net_platform_linux.linux_diagnostics(ports=(http_port, lan.BEACON_PORT))
        else:
            plat_rep = {"platform": sys.platform, "supported": False}
    return {
        "port": http_port,
        "offline_only": bool(offline_only),
        "lan": lan_rep,
        "stun": stun_rep,
        "wireguard": wg_rep,
        "platform": plat_rep,
    }
class NetworkManager:
    def __init__(
        self,
        config_path=None,
        peers_path=None,
        identity_path=None,
        invites_path=None,
        asgi_app=None,
        local_http_url=None,
        model_provider=None,
        load_provider=None,
        status_provider=None,
    ):
        self._lock = threading.RLock()
        self._config_path_override = os.path.abspath(os.fspath(config_path)) if config_path else None
        self._peers_path_override = os.path.abspath(os.fspath(peers_path)) if peers_path else None
        self._identity_path_override = os.path.abspath(os.fspath(identity_path)) if identity_path else None
        self._invites_path_override = os.path.abspath(os.fspath(invites_path)) if invites_path else None
        self._asgi_app = asgi_app
        self._local_http_url_override = local_http_url
        self._model_provider = model_provider
        self._load_provider = load_provider
        self._status_provider = status_provider
        self._identity = None
        self._invites = None
        self._runner = _LoopRunner()
        self._config = self._load_config()
        self._peers_state = self._load_peers()
        self._host_agent = None
        self._host_hb_stop = threading.Event()
        self._host_hb_thread = None
        self._host_last_disc_res = None
        self._host_last_hb_at = None
        self._host_model_override = None
        self._host_models_override = None
        self._client_agent = None
        self._client_state = TunnelState.READY.value
        self._client_mode = None
        self._client_path_type = None
        self._client_endpoint = None
        self._client_relay_url = None
        self._client_local_url = None
        self._client_rtt_ms = None
        self._client_peer_node_id = None
        self._client_server_info = None
        self._client_attempts = []
        self._client_last_error = None
        self._client_alternatives = []
        self._peer_req_windows = {}
        self._peer_bw_windows = {}
    def _config_path(self):
        return self._config_path_override or paths.network_config_file()
    def _peers_path(self):
        return self._peers_path_override or paths.network_peers_file()
    @property
    def identity(self):
        with self._lock:
            if self._identity is None:
                self._identity = net_identity.NodeIdentity(identity_path=self._identity_path_override)
            return self._identity
    @property
    def invite_manager(self):
        with self._lock:
            if self._invites is None:
                self._invites = net_identity.InviteManager(invites_path=self._invites_path_override)
            return self._invites
    def _load_config(self):
        p = self._config_path()
        default = default_network_config()
        data = storage.read_json_safe(p, default=default, expected_type=dict, quarantine=True, use_backup=True)
        if not isinstance(data, dict):
            return default
        merged = copy.deepcopy(default)
        merged.update(data)
        try:
            merged["visibility"] = _normalize_visibility(merged.get("visibility"))
        except ValueError:
            merged["visibility"] = "private"
        try:
            acc = _normalize_access_mode(merged.get("access_mode") or merged.get("access_policy"))
        except ValueError:
            acc = "invite_only"
        merged["access_mode"] = acc
        merged["access_policy"] = acc
        mp = int(merged.get("max_participants") or merged.get("max_peers") or 16)
        mp = max(1, min(128, mp))
        merged["max_participants"] = mp
        merged["max_peers"] = mp
        merged["published"] = False
        return merged
    def _save_config(self):
        with self._lock:
            snap = copy.deepcopy(self._config)
        p = self._config_path()
        storage.atomic_write_json(p, snap, indent=2, fsync=True, backup=True, restrict=True)
    def _load_peers(self):
        p = self._peers_path()
        default = _default_peers_state()
        data = storage.read_json_safe(p, default=default, expected_type=dict, quarantine=True, use_backup=True)
        if not isinstance(data, dict):
            return default
        out = copy.deepcopy(default)
        for key in ("tofu_pins", "authorized_peers", "pending_peers", "revoked_peers"):
            if isinstance(data.get(key), dict):
                out[key] = data[key]
        if isinstance(data.get("connection_history"), list):
            out["connection_history"] = data["connection_history"][-100:]
        return out
    def _save_peers(self):
        with self._lock:
            snap = copy.deepcopy(self._peers_state)
        p = self._peers_path()
        storage.atomic_write_json(p, snap, indent=2, fsync=True, backup=True, restrict=True)
    def _validate_service_urls(self, urls, kind, allow_insecure_local):
        valid = []
        rejected = []
        seen = set()
        for raw in urls or ():
            s = str(raw or "").strip()
            if not s:
                continue
            ok, reason, info = net_identity.validate_external_service_url(s, kind=kind, allow_insecure_local=allow_insecure_local)
            if not ok:
                rejected.append({"url": s, "reason": reason})
                continue
            norm = (info.get("url") or s).strip().rstrip("/")
            if norm not in seen:
                seen.add(norm)
                valid.append(norm)
        return valid, rejected
    def _make_discovery_client(self, extra_urls=None):
        with self._lock:
            cfg = copy.deepcopy(self._config)
        allow_local = bool(cfg.get("allow_insecure_local"))
        offline = bool(cfg.get("offline_only"))
        raw_urls = list(cfg.get("discovery_urls") or [])
        for u in extra_urls or ():
            if u and u not in raw_urls:
                raw_urls.append(u)
        valid_urls, _ = self._validate_service_urls(raw_urls, "discovery", allow_local)
        tokens = dict(cfg.get("discovery_auth_tokens") or {})
        dc = net_discovery.DiscoveryClient(urls=[], offline_only=offline, auth_tokens=tokens)
        if not offline:
            dc.urls = valid_urls
        return dc
    def get_config(self, include_secrets=False):
        with self._lock:
            out = copy.deepcopy(self._config)
        if not include_secrets:
            out["discovery_auth_tokens"] = {}
            out["relay_auth_token"] = ""
        return out
    def set_discovery_urls(self, urls, auth_tokens=None):
        with self._lock:
            allow_local = bool(self._config.get("allow_insecure_local"))
        valid, rejected = self._validate_service_urls(urls, "discovery", allow_local)
        with self._lock:
            self._config["discovery_urls"] = valid
            if isinstance(auth_tokens, dict):
                self._config["discovery_auth_tokens"] = {str(k).strip().rstrip("/"): str(v).strip() for k, v in auth_tokens.items() if str(v or "").strip()}
            self._save_config()
            cfg = copy.deepcopy(self._config)
        return {"status": "ok", "discovery_urls": valid, "rejected_urls": rejected, "config": cfg}
    def set_relay_urls(self, urls, auth_token=None):
        with self._lock:
            allow_local = bool(self._config.get("allow_insecure_local"))
        valid, rejected = self._validate_service_urls(urls, "relay", allow_local)
        with self._lock:
            self._config["relay_urls"] = valid
            if auth_token is not None:
                self._config["relay_auth_token"] = str(auth_token or "").strip()
            self._save_config()
            cfg = copy.deepcopy(self._config)
        return {"status": "ok", "relay_urls": valid, "rejected_urls": rejected, "config": cfg}
    def configure(self, patch=None, **kwargs):
        updates = {}
        if isinstance(patch, dict):
            updates.update(patch)
        updates.update(kwargs)
        rejected = []
        with self._lock:
            cand = copy.deepcopy(self._config)
            if "allow_insecure_local" in updates and updates["allow_insecure_local"] is not None:
                cand["allow_insecure_local"] = bool(updates["allow_insecure_local"])
            allow_local = bool(cand.get("allow_insecure_local"))
            if "discovery_urls" in updates and updates["discovery_urls"] is not None:
                valid_d, rej_d = self._validate_service_urls(updates["discovery_urls"], "discovery", allow_local)
                cand["discovery_urls"] = valid_d
                rejected.extend(rej_d)
            if "relay_urls" in updates and updates["relay_urls"] is not None:
                valid_r, rej_r = self._validate_service_urls(updates["relay_urls"], "relay", allow_local)
                cand["relay_urls"] = valid_r
                rejected.extend(rej_r)
            if "discovery_auth_tokens" in updates and isinstance(updates["discovery_auth_tokens"], dict):
                cand["discovery_auth_tokens"] = {str(k).strip().rstrip("/"): str(v).strip() for k, v in updates["discovery_auth_tokens"].items() if str(v or "").strip()}
            if "relay_auth_token" in updates and updates["relay_auth_token"] is not None:
                cand["relay_auth_token"] = str(updates["relay_auth_token"] or "").strip()
            if "stun_servers" in updates and updates["stun_servers"] is not None:
                parsed_stun = _parse_stun_servers(updates["stun_servers"])
                cand["stun_servers"] = [f"{h}:{p}" for h, p in parsed_stun]
            for bkey in ("offline_only", "allow_relay_fallback", "allow_p2p_direct", "wg_enabled"):
                if bkey in updates and updates[bkey] is not None:
                    cand[bkey] = bool(updates[bkey])
            if "direct_p2p_port" in updates and updates["direct_p2p_port"] is not None:
                p = int(updates["direct_p2p_port"])
                if not (0 <= p <= 65535):
                    raise ValueError("direct_p2p_port must be 0..65535")
                cand["direct_p2p_port"] = p
            if "local_http_url" in updates and updates["local_http_url"] is not None:
                cand["local_http_url"] = str(updates["local_http_url"] or "").strip()
            if "region_hint" in updates and updates["region_hint"] is not None:
                cand["region_hint"] = str(updates["region_hint"] or "").strip()[:32]
            if "display_name" in updates and updates["display_name"] is not None:
                cand["display_name"] = str(updates["display_name"] or "").strip()[:64]
            if "visibility" in updates and updates["visibility"] is not None:
                cand["visibility"] = _normalize_visibility(updates["visibility"])
            if "access_mode" in updates and updates["access_mode"] is not None:
                acc = _normalize_access_mode(updates["access_mode"])
                cand["access_mode"] = acc
                cand["access_policy"] = acc
            elif "access_policy" in updates and updates["access_policy"] is not None:
                acc = _normalize_access_mode(updates["access_policy"])
                cand["access_mode"] = acc
                cand["access_policy"] = acc
            if "max_participants" in updates and updates["max_participants"] is not None:
                mp = int(updates["max_participants"])
                if not (1 <= mp <= 128):
                    raise ValueError("max_participants must be 1..128")
                cand["max_participants"] = mp
                cand["max_peers"] = mp
            elif "max_peers" in updates and updates["max_peers"] is not None:
                mp = int(updates["max_peers"])
                if not (1 <= mp <= 128):
                    raise ValueError("max_peers must be 1..128")
                cand["max_participants"] = mp
                cand["max_peers"] = mp
            if "bandwidth_kbps" in updates and updates["bandwidth_kbps"] is not None:
                cand["bandwidth_kbps"] = max(0, int(updates["bandwidth_kbps"]))
            if "per_peer_rpm" in updates and updates["per_peer_rpm"] is not None:
                cand["per_peer_rpm"] = max(0, int(updates["per_peer_rpm"]))
            if "wg_interface" in updates and updates["wg_interface"] is not None:
                cand["wg_interface"] = net_wireguard.validate_profile_name(str(updates["wg_interface"]).strip())
            if "heartbeat_interval_s" in updates and updates["heartbeat_interval_s"] is not None:
                cand["heartbeat_interval_s"] = max(5.0, min(120.0, float(updates["heartbeat_interval_s"])))
            if "listing_ttl_s" in updates and updates["listing_ttl_s"] is not None:
                cand["listing_ttl_s"] = max(15, min(120, int(updates["listing_ttl_s"])))
            if "rpc_allowed_ports" in updates and updates["rpc_allowed_ports"] is not None:
                cand["rpc_allowed_ports"] = [int(p) for p in updates["rpc_allowed_ports"] if 1 <= int(p) <= 65535]
            self._config = cand
            self._save_config()
            out_cfg = copy.deepcopy(self._config)
        return {"status": "ok", "config": out_cfg, "rejected_urls": rejected}
    def _record_tofu_pin(self, node_id, pubkey_b64=None, name=None, mode=None, endpoint=None):
        if not node_id or not net_identity.NODE_ID_RE.match(str(node_id)):
            return
        now = time.time()
        with self._lock:
            pins = self._peers_state.setdefault("tofu_pins", {})
            prev = pins.get(node_id)
            if isinstance(prev, dict) and prev.get("pubkey_b64") and pubkey_b64 and not hmac.compare_digest(prev["pubkey_b64"], pubkey_b64):
                raise net_tunnel.TunnelAuthError(f"TOFU pubkey mismatch for {node_id}")
            entry = prev if isinstance(prev, dict) else {"node_id": node_id, "first_seen": now}
            if pubkey_b64:
                entry["pubkey_b64"] = pubkey_b64
            if name:
                entry["name"] = str(name)[:64]
            if mode:
                entry["last_mode"] = str(mode)
            if endpoint:
                entry["last_endpoint"] = str(endpoint)[:256]
            entry["last_seen"] = now
            pins[node_id] = entry
            self._save_peers()
    def _append_history(self, record):
        with self._lock:
            hist = self._peers_state.setdefault("connection_history", [])
            hist.append(record)
            if len(hist) > 100:
                self._peers_state["connection_history"] = hist[-100:]
            self._save_peers()
    def _authorize_peer(self, peer_node_id, peer_pubkey_b64, auth_proof):
        if not isinstance(peer_node_id, str) or not net_identity.NODE_ID_RE.match(peer_node_id):
            return False
        now = time.time()
        with self._lock:
            cfg = copy.deepcopy(self._config)
            revoked = self._peers_state.get("revoked_peers") or {}
            if peer_node_id in revoked:
                return False
            auth_map = self._peers_state.setdefault("authorized_peers", {})
            pend_map = self._peers_state.setdefault("pending_peers", {})
            if self._host_agent is not None:
                other_active = {
                    ch.peer_node_id
                    for ch in list(self._host_agent._active_channels)
                    if ch.is_established and ch.peer_node_id and ch.peer_node_id != peer_node_id
                }
                if len(other_active) >= int(cfg.get("max_participants") or 16):
                    return False
            existing = auth_map.get(peer_node_id)
            if isinstance(existing, dict):
                if existing.get("pubkey_b64") and peer_pubkey_b64 and not hmac.compare_digest(existing["pubkey_b64"], peer_pubkey_b64):
                    return False
            acc = _normalize_access_mode(cfg.get("access_mode"))
            if acc == "open":
                if isinstance(existing, dict):
                    existing["last_seen"] = now
                    if not existing.get("pubkey_b64") and peer_pubkey_b64:
                        existing["pubkey_b64"] = peer_pubkey_b64
                else:
                    auth_map[peer_node_id] = {
                        "node_id": peer_node_id,
                        "pubkey_b64": peer_pubkey_b64,
                        "approved_at": now,
                        "invite_id": None,
                        "grant_mode": "open",
                        "approved_manually": False,
                        "label": "open",
                        "last_seen": now,
                        "requests": 0,
                        "max_requests_per_min": None,
                        "bandwidth_kbps": None,
                    }
                pend_map.pop(peer_node_id, None)
                self._save_peers()
                return True
            if acc == "owner_confirm":
                if isinstance(existing, dict) and existing.get("approved_manually") is True:
                    existing["last_seen"] = now
                    self._save_peers()
                    return True
                p_rec = pend_map.get(peer_node_id)
                if isinstance(p_rec, dict):
                    p_rec["last_attempt_at"] = now
                    p_rec["attempts"] = int(p_rec.get("attempts") or 0) + 1
                    p_rec["pubkey_b64"] = peer_pubkey_b64
                    p_rec["auth_proof_present"] = bool(auth_proof)
                else:
                    pend_map[peer_node_id] = {
                        "node_id": peer_node_id,
                        "pubkey_b64": peer_pubkey_b64,
                        "requested_at": now,
                        "last_attempt_at": now,
                        "attempts": 1,
                        "auth_proof_present": bool(auth_proof),
                    }
                self._save_peers()
                return False
            ex_approved_manual = isinstance(existing, dict) and existing.get("approved_manually") is True
            ex_invite_id = existing.get("invite_id") if isinstance(existing, dict) else None
        proof_s = str(auth_proof or "")
        if not proof_s:
            if ex_approved_manual:
                with self._lock:
                    cur = self._peers_state.get("authorized_peers", {}).get(peer_node_id)
                    if isinstance(cur, dict):
                        cur["last_seen"] = now
                        self._save_peers()
                return True
            if ex_invite_id:
                for inv in self.invite_manager.list_invites(include_revoked=True):
                    if inv.get("invite_id") == ex_invite_id and not inv.get("revoked") and not inv.get("expired"):
                        with self._lock:
                            cur = self._peers_state.get("authorized_peers", {}).get(peer_node_id)
                            if isinstance(cur, dict):
                                cur["last_seen"] = now
                                self._save_peers()
                        return True
            return False
        ok, reason, inv_rec = self.invite_manager.verify_and_consume(proof_s, peer_node_id=peer_node_id)
        if not ok:
            if reason == "invite_exhausted" and ex_invite_id:
                m = _INVITE_ID_RE.match(proof_s.strip())
                if m and m.group(1) == ex_invite_id:
                    with self._lock:
                        cur = self._peers_state.get("authorized_peers", {}).get(peer_node_id)
                        if isinstance(cur, dict):
                            cur["last_seen"] = now
                            self._save_peers()
                    return True
            return False
        with self._lock:
            auth_map = self._peers_state.setdefault("authorized_peers", {})
            pend_map = self._peers_state.setdefault("pending_peers", {})
            prev = auth_map.get(peer_node_id) if isinstance(auth_map.get(peer_node_id), dict) else {}
            auth_map[peer_node_id] = {
                "node_id": peer_node_id,
                "pubkey_b64": peer_pubkey_b64,
                "approved_at": now,
                "invite_id": inv_rec.get("invite_id") if isinstance(inv_rec, dict) else None,
                "grant_mode": "invite_only",
                "approved_manually": bool(prev.get("approved_manually")),
                "label": inv_rec.get("label") if isinstance(inv_rec, dict) else "",
                "last_seen": now,
                "requests": int(prev.get("requests") or 0),
                "max_requests_per_min": prev.get("max_requests_per_min"),
                "bandwidth_kbps": prev.get("bandwidth_kbps"),
            }
            pend_map.pop(peer_node_id, None)
            self._save_peers()
        return True
    def _on_host_http_request(self, info):
        peer_id = str((info or {}).get("peer_node_id") or "").strip()
        if not peer_id:
            return
        now_m = time.monotonic()
        with self._lock:
            if peer_id in (self._peers_state.get("revoked_peers") or {}):
                raise PermissionError("Peer revoked")
            auth_rec = (self._peers_state.get("authorized_peers") or {}).get(peer_id)
            if isinstance(auth_rec, dict):
                auth_rec["last_seen"] = time.time()
                auth_rec["requests"] = int(auth_rec.get("requests") or 0) + 1
            rpm_limit = None
            bw_kbps = None
            if isinstance(auth_rec, dict):
                rpm_limit = auth_rec.get("max_requests_per_min")
                bw_kbps = auth_rec.get("bandwidth_kbps")
            if rpm_limit is None:
                rpm_limit = int(self._config.get("per_peer_rpm") or 0) or None
            if bw_kbps is None:
                bw_kbps = int(self._config.get("bandwidth_kbps") or 0) or None
            if rpm_limit is not None and int(rpm_limit) > 0:
                win = self._peer_req_windows.setdefault(peer_id, [])
                cutoff = now_m - 60.0
                win[:] = [t for t in win if t >= cutoff]
                if len(win) >= int(rpm_limit):
                    raise PermissionError("Peer request quota exceeded")
                win.append(now_m)
            if bw_kbps is not None and int(bw_kbps) > 0 and self._host_agent is not None:
                total_bytes = 0
                for ch in list(self._host_agent._active_channels):
                    if ch.peer_node_id == peer_id:
                        total_bytes += int(ch.bytes_tx + ch.bytes_rx)
                prev_t, prev_b = self._peer_bw_windows.get(peer_id, (now_m, total_bytes))
                dt = now_m - prev_t
                if dt >= 1.0:
                    rate_kbps = ((max(0, total_bytes - prev_b)) * 8.0 / 1000.0) / dt
                    self._peer_bw_windows[peer_id] = (now_m, total_bytes)
                    if rate_kbps > float(bw_kbps):
                        raise PermissionError("Peer bandwidth quota exceeded")
                elif peer_id not in self._peer_bw_windows:
                    self._peer_bw_windows[peer_id] = (now_m, total_bytes)
    def _close_peer_channels_sync(self, peer_node_id, reason="kicked"):
        ha = self._host_agent
        if ha is None:
            return 0
        async def _close():
            closed = 0
            for ch in list(ha._active_channels):
                if ch.peer_node_id == peer_node_id:
                    closed += 1
                    ha._active_channels.discard(ch)
                    try:
                        await ch.send_close(reason)
                    except Exception:
                        pass
                    try:
                        await ch.close()
                    except Exception:
                        pass
            return closed
        try:
            return int(self._runner.run_coro(_close(), timeout=5.0) or 0)
        except Exception:
            return 0
    def list_connected_peers(self):
        out = []
        ha = self._host_agent
        if ha is None:
            return out
        with self._lock:
            auth_map = copy.deepcopy(self._peers_state.get("authorized_peers") or {})
        for ch in list(ha._active_channels):
            if not ch.is_established:
                continue
            pid = ch.peer_node_id
            meta = auth_map.get(pid) if pid else None
            out.append({
                "node_id": pid,
                "pubkey_b64": ch.peer_pubkey_b64,
                "peer_info": ch.transport.peer_info(),
                "bytes_tx": int(ch.bytes_tx),
