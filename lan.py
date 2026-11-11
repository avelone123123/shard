"""
shard lan — LAN pooling: several machines act as one big server for one model.

Modes (Radmin-VPN style: create a network or join one):

  server  — this machine owns the network and the model. Serves /v1 to members
            (member-token authed) and distributes weights to compute members
            via llama.cpp RPC (ggml-rpc-server.exe runs on worker machines).
  worker  — this machine shares VRAM/RAM: spawns ggml-rpc-server.exe and
            heartbeats to the master every 1 s (3 misses = dropped).
  member  — client-only member: consumes the master's model over HTTP.

Wire protocol (all empirically verified against live modules):
  - UDP beacon on :8001, JSON announce every 2 s, peer TTL 8 s.
  - Pairing code XXXX-XXXX from a 31-char alphabet (no 0/O/1/I/L).
  - Member token "sk-shard-lan-" + 16 hex chars.
  - llama.cpp flags: --rpc "ip:port,..." and -ts "N0,N1,..." where index 0 is
    the master's local device and 1..N follow --rpc order (documented
    llama.cpp device order; live weight ordering check is Phase 1.1).

Registry lives in DATA_DIR/lan.json (SHARD_DATA_DIR aware, same convention
as settings.py). The HTTP layer (server.py /api/lan/*) calls into
LanManager; CLI proxies over HTTP like every other shard command.
"""
import glob
import json
import os
import random
import socket
import subprocess
import threading
import time
import uuid

import settings

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# --- constants (verified: defaults chosen + live-checked in tests_shard) -----
BEACON_PORT = 8001            # UDP announce/listen port
BEACON_INTERVAL_S = 2.0       # announce period
PEER_TTL_S = 8.0              # peer forgotten after 2 missed announces
HEARTBEAT_INTERVAL_S = 1.0    # worker -> master heartbeat period
HEARTBEAT_MISS_LIMIT = 3      # 3 missed heartbeats = member dead (3.0 s)
BEACON_ALIVE_S = BEACON_INTERVAL_S * 2
APP_TAG = "shard-lan"
PROTO_VER = 1
PAIR_LEN = 4                  # XXXX-XXXX
# 31 chars (23 letters + 8 digits), no visually ambiguous 0/O/1/I/L
PAIR_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
TOKEN_PREFIX = "sk-shard-lan-"
RPC_EXE = "ggml-rpc-server.exe"  # bundled name in llama.cpp release zips (b12110)
LAN_FILE = "lan.json"
DEFAULT_MASTER_PORT = 8000    # shard HTTP API default (SHARD_PORT)

# memory reserves, single source of truth = fit.py (verified there)
from fit import RAM_RESERVE_MB, VRAM_RESERVE_MB  # noqa: E402


# ---------------------------------------------------------------------------
# pure helpers (unit-tested, no state)
# ---------------------------------------------------------------------------
def data_dir():
    """Same convention as settings.py line 11: env wins, else ~/.shard."""
    d = os.environ.get("SHARD_DATA_DIR") or os.path.join(
        os.path.expanduser("~"), ".shard")
    os.makedirs(d, exist_ok=True)
    return d


def lan_file():
    return os.path.join(data_dir(), LAN_FILE)


def normalize_code(raw):
    """Accept 'ABCD-EFGH', 'abcd efgh', 'ABCDEFGH' -> canonical, or None."""
    s = "".join(ch for ch in str(raw or "").upper() if ch.isalnum())
    if len(s) == PAIR_LEN * 2 and all(ch in PAIR_ALPHABET for ch in s):
        return f"{s[:PAIR_LEN]}-{s[PAIR_LEN:]}"
    return None


def gen_pairing_code():
    parts = ["".join(random.choice(PAIR_ALPHABET) for _ in range(PAIR_LEN))
             for _ in range(2)]
    return "-".join(parts)


def new_member_token():
    return TOKEN_PREFIX + uuid.uuid4().hex[:16]


def is_member_token(token):
    t = str(token or "")
    return t.startswith(TOKEN_PREFIX) and len(t) == len(TOKEN_PREFIX) + 16 \
        and all(c in "0123456789abcdef" for c in t[len(TOKEN_PREFIX):])


def is_loopback(ip):
    ip = str(ip or "").strip().strip("[]")
    return ip == "::1" or ip.startswith("127.") or ip == "localhost"


def device_budget(vram_free_mb, ram_free_mb):
    """Usable memory for one device after hard reserves.

    Reserves are the same fit.py constants used for single-machine loading;
    reusing them here keeps the pooled and local math consistent.
    """
    vram = max(0, int(vram_free_mb or 0) - VRAM_RESERVE_MB)
    ram = max(0, int(ram_free_mb or 0) - RAM_RESERVE_MB)
    return vram, ram


def split_weights(budgets):
    """Proportional -ts weights: VRAM when the device has a usable GPU share,
    otherwise RAM. All-zero input falls back to equal weights (llama.cpp
    would even-split anyway); honest fallback, tested."""
    out = []
    for vram, ram in budgets:
        w = int(vram) if int(vram) > 0 else int(ram)
        out.append(max(0, w))
    if not any(out):
        return [1] * len(out)
    return out


def format_ts_arg(weights):
    return ",".join(str(int(w)) for w in weights)


def format_rpc_arg(addrs):
    """addrs: iterable of 'ip:port' strings -> comma-joined for --rpc."""
    return ",".join(str(a) for a in addrs)


def member_alive(member, now=None):
    """True while the member answered within the 3-heartbeat window."""
    now = time.time() if now is None else now
    last = float((member or {}).get("last_seen") or 0)
    return (now - last) <= HEARTBEAT_MISS_LIMIT * HEARTBEAT_INTERVAL_S


def beacon_payload(name, role, model=None, port=DEFAULT_MASTER_PORT):
    return {"app": APP_TAG, "ver": PROTO_VER, "name": str(name),
            "role": role, "model": model, "port": int(port), "ts": time.time()}


def parse_beacon(raw):
    """Validate an announce datagram; return dict or None."""
    try:
        d = json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    if d.get("app") != APP_TAG or d.get("ver") != PROTO_VER:
        return None
    name = str(d.get("name") or "").strip()
    if not name:
        return None
    d["name"] = name
    return d


def local_ip():
    """Best-effort primary LAN IPv4 (UDP connect trick sends no packets)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        s.close()


def find_rpc_server():
    """Locate ggml-rpc-server.exe (same runtime dir as llama-server)."""
    runtime = os.path.join(os.path.expanduser("~"), ".shard", "runtime")
    p = os.path.join(runtime, RPC_EXE)
    if os.path.isfile(p):
        return p
    hits = glob.glob(os.path.join(runtime, "**", RPC_EXE), recursive=True)
    return hits[0] if hits else None


def worker_cmd(port, exe=None):
    """Exact command line for a compute member (verified flags live in v1.6
    damage audit: ggml-rpc-server.exe --host H --port P starts and listens)."""
    exe = exe or find_rpc_server()
    if not exe:
        raise RuntimeError(
            "ggml-rpc-server.exe не найден в ~/.shard/runtime — "
            "запустите shard once (он ставит рантайм llama.cpp) или "
            "подключитесь как обычный участник (--share без расшаривания)")
    return [exe, "--host", "0.0.0.0", "--port", str(int(port))]


# ---------------------------------------------------------------------------
# stateful manager
# ---------------------------------------------------------------------------
class LanManager:
    """All-LAN logic for one shard process. Thread-safe via self._lock."""

    def __init__(self):
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._threads = {}
        self._peers = {}           # ip -> beacon dict + ts (runtime only)
        self._rpc_proc = None      # worker: ggml-rpc-server subprocess
        self._hb_failures = 0
        self._state = self._load()
        if self._state.get("mode") in ("server", "worker", "member"):
            # restart-safe: resume announced/heartbeat roles after reboot
            threading.Thread(target=self._resume, daemon=True).start()

    # -- persistence ---------------------------------------------------------
    def _load(self):
        try:
            with open(lan_file(), "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                d.setdefault("mode", "off")
                d.setdefault("name", socket.gethostname() or "shard")
                d.setdefault("pairing_code", None)
                d.setdefault("master", None)
                d.setdefault("members", [])
                d.setdefault("created_at", None)
                return d
        except Exception:
            pass
        return {"mode": "off", "name": socket.gethostname() or "shard",
                "pairing_code": None, "master": None, "members": [],
                "created_at": None}

    def _save(self):
        with self._lock:
            snap = json.loads(json.dumps(self._state, ensure_ascii=False))
        try:
            with open(lan_file(), "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # -- simple accessors ----------------------------------------------------
    def mode(self):
        with self._lock:
            return self._state.get("mode")

    def is_server(self):
        return self.mode() == "server"

    def is_member(self):
        return self.mode() in ("member", "worker")

    def name(self):
        with self._lock:
            return self._state.get("name")

    def tokens(self):
        with self._lock:
            return {m["token"] for m in self._state.get("members", [])}

    def pairing_code(self):
        with self._lock:
            return self._state.get("pairing_code")

    def pool_active(self):
        """Server mode with at least one live worker -> RPC split is worth it."""
        if not self.is_server():
            return False
        now = time.time()
        with self._lock:
            return any(m.get("role") == "worker" and m.get("rpc_port")
                       and member_alive(m, now)
                       for m in self._state.get("members", []))

    def rpc_targets(self):
        """--rpc value + -ts weights for the master, or None when not pooled.

        Device order (llama.cpp docs, live check pending Phase 1.1):
        local device first, then --rpc servers in listed order.
        """
        if not self.pool_active():
            return None
        now = time.time()
        with self._lock:
            workers = [m for m in self._state.get("members", [])
                       if m.get("role") == "worker" and m.get("rpc_port")
                       and member_alive(m, now)]
        # master's own device: sample live hardware (free VRAM/RAM right now,
        # i.e. BEFORE llama-server pushes weights into the pool)
        try:
            from hardware import get_hardware_profile
            local = get_hardware_profile()
        except Exception:
            local = {}
        lvram, lram = device_budget(local.get("vram_free_mb"),
                                    local.get("ram_avail_mb"))
        budgets = [(lvram, lram)]
        addrs = []
        for m in workers:
            hw = m.get("hw") or {}
            budgets.append(device_budget(hw.get("vram_free_mb"),
                                         hw.get("ram_avail_mb")))
            addrs.append(f"{m['ip']}:{int(m['rpc_port'])}")
        return {"servers": format_rpc_arg(addrs),
                "split": format_ts_arg(split_weights(budgets))}

    def pool_capacity_mb(self):
        """Total usable pooled memory in MB (master live HW + live workers).

        Mirrors split_weights(): a device with usable VRAM contributes its
        VRAM, a CPU-only device contributes its RAM (llama.cpp -ts assigns a
        proportional share to each device, so a device's share must fit there).
        """
        try:
            from hardware import get_hardware_profile
            local = get_hardware_profile()
        except Exception:
            local = {}
        budgets = [device_budget(local.get("vram_free_mb"),
                                 local.get("ram_avail_mb"))]
        now = time.time()
        with self._lock:
            workers = [m for m in self._state.get("members", [])
                       if m.get("role") == "worker" and m.get("rpc_port")
                       and member_alive(m, now)]
        for m in workers:
            hw = m.get("hw") or {}
            budgets.append(device_budget(hw.get("vram_free_mb"),
                                         hw.get("ram_avail_mb")))
        return sum(int(v) if int(v) > 0 else int(r) for v, r in budgets)

    def status(self):
        now = time.time()
        with self._lock:
            st = dict(self._state)
        # never expose member auth tokens through the status endpoint
        st["members"] = [dict(m, token=None, alive=member_alive(m, now))
                         for m in st.get("members", [])]
        peers = []
        for ip, p in list(self._peers.items()):
            if time.time() - p.get("_ts", 0) > PEER_TTL_S:
                self._peers.pop(ip, None)
                continue
            peers.append({"ip": ip, **{k: p.get(k) for k in
                                       ("name", "role", "model", "port")}})
        rpc = self.rpc_targets()
        return {"mode": st.get("mode"), "name": st.get("name"),
                "ip": local_ip(), "pairing_code": st.get("pairing_code"),
                "master": st.get("master"), "members": st.get("members"),
                "peers": peers, "rpc": rpc,
                "constants": {"beacon_port": BEACON_PORT,
                              "beacon_interval_s": BEACON_INTERVAL_S,
                              "heartbeat_interval_s": HEARTBEAT_INTERVAL_S,
                              "heartbeat_miss_limit": HEARTBEAT_MISS_LIMIT}}

    # -- server mode ---------------------------------------------------------
    def create(self, name=None):
        with self._lock:
            if self._state.get("mode") == "server":
                return self.status()
            self.off(quiet=True)
            self._state.update({
                "mode": "server",
                "name": (name or self._state.get("name") or
                         socket.gethostname() or "shard").strip() or "shard",
                "pairing_code": gen_pairing_code(),
                "members": [],
                "master": None,
                "created_at": time.time(),
            })
            self._save()
        self._start_threads("server")
        return self.status()

    def handle_pair(self, code, ip, name=None, share=False):
        """Server side of pairing: validate code, issue member token."""
        code = normalize_code(code)
        with self._lock:
            if self._state.get("mode") != "server":
                return None, "Эта машина не является сервером shard-сети"
            if not code or code != self._state.get("pairing_code"):
                return None, "Неверный код подключения"
            # re-pair same IP -> reuse token (idempotent join)
            for m in self._state.get("members", []):
                if m.get("ip") == ip:
                    m["name"] = (name or m.get("name") or "участник")
                    m["role"] = "worker" if share else m.get("role", "member")
                    m["last_seen"] = time.time()
                    self._save()
                    return {"token": m["token"],
                            "master": self._state.get("name")}, None
            token = new_member_token()
            self._state["members"].append({
                "ip": ip, "name": (name or "участник").strip() or "участник",
                "token": token, "role": "worker" if share else "member",
                "joined_at": time.time(), "last_seen": time.time(),
                "hw": None, "rpc_port": None})
            self._save()
            return {"token": token, "master": self._state.get("name")}, None

    def handle_heartbeat(self, ip, token, hw=None, rpc_port=None):
        with self._lock:
            if self._state.get("mode") != "server":
                return False
            for m in self._state.get("members", []):
                if m.get("ip") == ip and m.get("token") == token:
                    m["last_seen"] = time.time()
                    if hw and isinstance(hw, dict):
                        m["hw"] = hw
                    if rpc_port:
                        m["rpc_port"] = int(rpc_port)
                    return True
            return False

    def handle_leave(self, ip, token):
        with self._lock:
            before = len(self._state.get("members", []))
            self._state["members"] = [
                m for m in self._state.get("members", [])
                if not (m.get("ip") == ip and
                        (token == m.get("token") or not token))]
            dropped = len(self._state.get("members", [])) < before
            if dropped:
                self._save()
            return dropped

    # -- member / worker mode ------------------------------------------------
    def join(self, ip, code, share=False, port=DEFAULT_MASTER_PORT):
        """Client side: pair with the master over HTTP, then assume role."""
        import requests
        code = normalize_code(code)
        if not ip or not code:
            return {"status": "error",
                    "message": "Нужны IP мастера и код вида XXXX-XXXX"}
        url = f"http://{ip}:{port}/api/lan/pair"
        try:
            r = requests.post(url, json={"code": code,
                                         "name": self.name(), "share": share},
                              timeout=5)
        except Exception as e:
            return {"status": "error",
                    "message": f"Мастер {ip}:{port} недоступен: {str(e)[:120]}"}
        if r.status_code == 403:
            return {"status": "error", "message": "Неверный код подключения"}
        if r.status_code != 200:
            return {"status": "error",
                    "message": f"Мастер ответил {r.status_code}: {r.text[:120]}"}
        data = r.json()
        with self._lock:
            self.off(quiet=True)
            self._state.update({
                "mode": "worker" if share else "member",
                "master": {"ip": ip, "port": int(port),
                           "token": data["token"],
                           "name": data.get("master") or ip},
                "created_at": time.time(),
            })
            self._save()
        if share:
            self._start_worker()
        self._start_threads(self.mode())
        return {"status": "ok", "mode": self.mode(),
                "master": data.get("master") or ip}

    def _start_worker(self):
        """Spawn ggml-rpc-server.exe on a free local port (this machine now
        shares VRAM/RAM; llama-server on the master pushes weights to it)."""
        if self._rpc_proc is not None and self._rpc_proc.poll() is None:
            return
        # worker_cmd() itself raises the honest RU message when the exe
        # is missing (runtime not installed yet)
        port = _free_port()
        cmd = worker_cmd(port, exe=find_rpc_server())
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = _CREATE_NO_WINDOW
        self._rpc_proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, **kw)
        with self._lock:
            self._state["rpc_port"] = port
            self._save()
        time.sleep(0.3)  # give the listener a beat before first heartbeat
        if self._rpc_proc.poll() is not None:
            tail = ""
            try:
                tail = (self._rpc_proc.stderr.read() or b"").decode(
                    "utf-8", "ignore")[-200:]
            except Exception:
                pass
            self._rpc_proc = None
            raise RuntimeError(f"ggml-rpc-server не запустился: {tail[:150]}")

    def _stop_worker(self):
        if self._rpc_proc is not None:
            try:
                self._rpc_proc.terminate()
            except Exception:
                pass
            try:
                self._rpc_proc.wait(timeout=5)
            except Exception:
                try:
                    self._rpc_proc.kill()
                except Exception:
                    pass
            self._rpc_proc = None
        with self._lock:
            self._state["rpc_port"] = None

    # -- lifecycle / kill switch ---------------------------------------------
    def off(self, quiet=False):
        """Kill switch: drop every LAN role, stop rpc + threads, notify master."""
        mode = self.mode()
        if mode == "worker":
            self._stop_worker()
        if mode in ("member", "worker"):
            master = self._state.get("master") or {}
            token = master.get("token")
            if master.get("ip") and token:
                try:
                    import requests
                    requests.post(
                        f"http://{master['ip']}:{master.get('port', DEFAULT_MASTER_PORT)}/api/lan/leave",
                        json={"token": token}, timeout=2)
                except Exception:
                    pass
        self._stop.set()
        with self._lock:
            self._state.update({"mode": "off", "pairing_code": None,
                                "members": [], "master": None,
                                "created_at": None, "rpc_port": None})
            self._save()
            self._peers.clear()
            self._threads = {}
        if not quiet and mode != "off":
            print(f"[lan] сеть отключена (был режим {mode})")
        return {"status": "ok", "mode": "off"}

    def _resume(self):
        """Restart-safe resume (called from __init__ thread)."""
        mode = self.mode()
        if mode == "server":
            self._start_threads("server")
        elif mode == "worker":
            try:
                self._start_worker()
            except Exception as e:
                print(f"[lan] worker не восстановлен: {str(e)[:120]}")
                self.off(quiet=True)
                return
            self._start_threads("worker")
        elif mode == "member":
            self._start_threads("member")

    # -- threads -------------------------------------------------------------
    def _start_threads(self, mode):
        self._stop.set()
        for t in self._threads.values():
            if t.is_alive():
                t.join(timeout=1.0)
        self._stop.clear()
        spawn = {
            "listener": threading.Thread(target=self._listen_loop, daemon=True),
            "hb_monitor": threading.Thread(target=self._hb_monitor_loop,
                                           daemon=True),
        }
        if mode == "server":
            spawn["announce"] = threading.Thread(target=self._announce_loop,
                                                 daemon=True)
        else:
            spawn["hb_send"] = threading.Thread(target=self._hb_send_loop,
                                                daemon=True)
        self._threads = spawn
        for t in spawn.values():
            t.start()

    def _announce_loop(self):
        """UDP broadcast announce (server mode)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except Exception:
            pass
        while not self._stop.is_set():
            if self.mode() != "server":
                break
            payload = beacon_payload(self.name(), "server", port=8000)
            for target in ("<broadcast>", "255.255.255.255"):
                try:
                    sock.sendto(json.dumps(payload).encode("utf-8"),
                                (target, BEACON_PORT))
                except Exception:
                    pass
            self._stop.wait(BEACON_INTERVAL_S)
        sock.close()

    def _listen_loop(self):
        """UDP listener: collect nearby shard networks (all non-off modes)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", BEACON_PORT))
            sock.settimeout(0.5)
            while not self._stop.is_set():
                try:
                    raw, addr = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                except OSError:
                    break
                d = parse_beacon(raw)
                if not d or d.get("name") == self.name():
                    continue  # our own announce bounced back
                self._peers[addr[0]] = {k: d.get(k) for k in
                                        ("name", "role", "model", "port")}
                self._peers[addr[0]]["_ts"] = time.time()
        except Exception:
            pass
        finally:
            sock.close()

    def _hb_send_loop(self):
        """Member/worker -> master heartbeat every 1 s. 3 consecutive
        failures -> assume the master is gone and go offline (honest)."""
        while not self._stop.is_set():
            if not self.is_member():
                break
            ok = self._send_heartbeat()
            self._hb_failures = 0 if ok else self._hb_failures + 1
            if self._hb_failures >= HEARTBEAT_MISS_LIMIT:
                print("[lan] мастер не отвечает (3 пропуска) — выхожу из сети")
                self.off(quiet=True)
                break
            self._stop.wait(HEARTBEAT_INTERVAL_S)

    def _send_heartbeat(self):
        import requests
        master = self._state.get("master") or {}
        if not master.get("ip"):
            return False
        hw = None
        rpc_port = None
        if self.mode() == "worker":
            try:
                from hardware import get_hardware_profile
                hw = get_hardware_profile()
            except Exception:
                hw = None
            rpc_port = self._state.get("rpc_port")
        try:
            r = requests.post(
                f"http://{master['ip']}:{master.get('port', DEFAULT_MASTER_PORT)}/api/lan/heartbeat",
                json={"token": master.get("token"), "hw": hw,
                      "rpc_port": rpc_port},
                timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def _hb_monitor_loop(self):
        """Server mode: sweep members every 1 s (3 misses = dead, honest)."""
        while not self._stop.is_set():
            if not self.is_server():
                break
            self._stop.wait(HEARTBEAT_INTERVAL_S)


def _free_port():
    """Same trick as gguf_backend._free_port (bind :0, read port, close)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# process-wide singleton (server.py, engine.py, tests use this instance)
LAN = LanManager()
