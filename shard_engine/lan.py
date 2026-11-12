import glob
import ipaddress
import json
import os
import random
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from . import hardware as _hardware
from . import paths as _paths
from .fit import RAM_RESERVE_MB, VRAM_RESERVE_MB
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
BEACON_PORT = 8001
BEACON_INTERVAL_S = 2.0
PEER_TTL_S = 8.0
HEARTBEAT_INTERVAL_S = 1.0
HEARTBEAT_MISS_LIMIT = 3
REBALANCE_INTERVAL_S = 2.0
REBALANCE_ERROR_COOLDOWN_TICKS = 30
BEACON_ALIVE_S = BEACON_INTERVAL_S * 2
APP_TAG = "shard-lan"
PROTO_VER = 1
PAIR_LEN = 4
PAIR_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
TOKEN_PREFIX = "sk-shard-lan-"
RPC_EXE = "ggml-rpc-server.exe" if os.name == "nt" else "ggml-rpc-server"
LAN_FILE = "lan.json"
DEFAULT_MASTER_PORT = 8000
_GPU_SCORE = {
    "4090": 0.95, "4080": 0.90, "4070": 0.80, "4060": 0.65,
    "3090": 0.80, "3080": 0.75, "3070": 0.65, "3060": 0.50, "3050": 0.40,
    "a100": 1.00, "h100": 1.00, "v100": 0.70, "l40": 0.85, "l4": 0.60,
    "5090": 1.00, "5080": 0.95, "5070": 0.85, "5060": 0.70, "5050": 0.50,
    "2080": 0.65, "2070": 0.55, "2060": 0.45,
    "1660": 0.30, "1650": 0.25,
    "1050": 0.20, "mx450": 0.15, "mx330": 0.12, "mx250": 0.10,
    "2050": 0.45,
    "t1200": 0.35, "t1000": 0.30, "quadro": 0.40, "nvs": 0.20,
    "apple": 0.55, "m1": 0.45, "m2": 0.55, "m3": 0.60, "m4": 0.65,
    "integrated": 0.10, "uhd": 0.05, "iris": 0.08,
}
_HOST_RE = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$")
def data_dir():
    d = str(_paths.data_dir())
    os.makedirs(d, exist_ok=True)
    return d
def lan_file():
    p = _paths.lan_file()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p
def normalize_code(raw):
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
def _to_int(v):
    try:
        return int(float(v))
    except Exception:
        return 0
def device_budget(vram_free_mb, ram_free_mb):
    vram = max(0, int(vram_free_mb or 0) - VRAM_RESERVE_MB)
    ram = max(0, int(ram_free_mb or 0) - RAM_RESERVE_MB)
    return vram, ram
def split_weights(budgets):
    out = []
    for vram, ram in budgets:
        w = int(vram) if int(vram) > 0 else int(ram)
        out.append(max(0, w))
    if not any(out):
        return [1] * len(out)
    return out
def speed_score(hw):
    if not hw:
        return None
    raw_name = hw.get("gpu_name")
    vram = _to_int(hw.get("vram_total_mb"))
    ram = _to_int(hw.get("ram_total_mb"))
    if raw_name is None and vram <= 0 and ram <= 0:
        return None
    name = str(raw_name or "").lower()
    has_gpu = bool(name) and not (
        "none" in name or name in ("cpu", "-", "n/a", "unknown"))
    score = None
    if has_gpu:
        for kw, s in _GPU_SCORE.items():
            if kw in name:
                score = s
                break
        if score is None:
            score = 0.40
        score += min(0.15, max(0, vram) / 24576.0)
    else:
        score = 0.10
        if ram >= 32768:
            score += 0.05
    return round(min(1.0, max(0.0, score)), 3)
def split_weights_scored(budgets, scores):
    if any(s is None for s in scores):
        return split_weights(budgets)
    out = [int(b_v if b_v > 0 else b_r) * s
           for (b_v, b_r), s in zip(budgets, scores)]
    out = [int(round(w)) for w in out]
    if not any(out):
        return [1] * len(out)
    return out
def format_ts_arg(weights):
    return ",".join(str(int(w)) for w in weights)
def format_rpc_arg(addrs):
    return ",".join(str(a) for a in addrs)
def member_alive(member, now=None):
    now = time.time() if now is None else now
    last = float((member or {}).get("last_seen") or 0)
    return (now - last) <= HEARTBEAT_MISS_LIMIT * HEARTBEAT_INTERVAL_S
def beacon_payload(name, role, model=None, port=DEFAULT_MASTER_PORT,
                   open_net=False):
    return {"app": APP_TAG, "ver": PROTO_VER, "name": str(name),
            "role": role, "model": model, "port": int(port),
            "open": bool(open_net), "ts": time.time()}
def parse_beacon(raw):
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
def _is_usable_lan_ipv4(addr):
    try:
        ip_obj = ipaddress.ip_address(str(addr))
        if ip_obj.version != 4 or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_unspecified:
            return False
        return True
    except ValueError:
        return False
def local_ips():
    found = []
    def _add(addr):
        s = str(addr or "").strip()
        if _is_usable_lan_ipv4(s) and s not in found:
            found.append(s)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        _add(s.getsockname()[0])
    except Exception:
        pass
    finally:
        s.close()
    try:
        import psutil
        stats = {}
        try:
            stats = psutil.net_if_stats()
        except Exception:
            stats = {}
        addrs = psutil.net_if_addrs()
        for iface, items in addrs.items():
            st = stats.get(iface)
            if st is not None and not st.isup:
                continue
            for item in items:
                if getattr(item, "family", None) == socket.AF_INET:
                    _add(getattr(item, "address", None))
    except Exception:
        pass
    try:
        for res in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            _add(res[4][0])
    except Exception:
        pass
    return found
def local_ip():
    ips = local_ips()
    if ips:
        return ips[0]
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if _is_usable_lan_ipv4(ip):
            return ip
    except Exception:
        pass
    return "127.0.0.1"
def validate_master_host(host):
    raw = str(host or "").strip()
    if not raw:
        return None, "Нужен IP мастера (или выберите сервер из списка)"
    if raw.startswith(("http://", "https://")):
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0].strip()
    if raw.startswith("[") and "]" in raw:
        raw = raw[1:raw.index("]")]
    elif ":" in raw and raw.count(":") == 1:
        raw = raw.split(":", 1)[0].strip()
    if not raw:
        return None, "Укажите корректный IP или hostname мастера"
    try:
        ip_obj = ipaddress.ip_address(raw)
        if ip_obj.is_unspecified:
            return None, f"Некорректный адрес мастера: {raw}"
        return str(ip_obj), None
    except ValueError:
        pass
    if all(ch.isdigit() or ch == "." for ch in raw):
        return None, f"Некорректный IPv4-адрес мастера: {raw}"
    if _HOST_RE.match(raw):
        return raw, None
    return None, f"Некорректный адрес мастера: {raw}"
def find_rpc_server():
    env_rpc = os.environ.get("SHARD_RPC_SERVER")
    if env_rpc and os.path.isfile(env_rpc):
        return env_rpc
    names = ["ggml-rpc-server.exe"] if os.name == "nt" else ["ggml-rpc-server", "llama-rpc-server"]
    here = os.path.dirname(os.path.abspath(__file__))
    search_dirs = [
        _paths.runtime_dir(),
        os.path.join(os.path.expanduser("~"), ".shard", "runtime"),
        os.path.join(here, "native"),
        os.path.join(os.path.dirname(here), "native"),
        os.path.join(os.getcwd(), "native"),
    ]
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for exe_name in names:
            p = os.path.join(d, exe_name)
            if os.path.isfile(p):
                return p
            hits = glob.glob(os.path.join(d, "**", exe_name), recursive=True)
            if hits:
                return hits[0]
    for exe_name in names:
        which = shutil.which(exe_name)
        if which:
            return which
    return None
def worker_cmd(port, exe=None):
    exe = exe or find_rpc_server()
    if not exe:
        raise RuntimeError(
            f"{RPC_EXE} не найден в {_paths.runtime_dir()} — "
            "установите llama.cpp runtime (или запустите модель с GGUF) "
            "либо подключитесь как обычный участник без --share")
    return [exe, "--host", "0.0.0.0", "--port", str(int(port))]
class LanManager:
    def __init__(self, state_path=None):
        self._state_path = state_path
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._threads = {}
        self._peers = {}
        self._rpc_proc = None
        self._hb_failures = 0
        self._hw_peak = {}
        self._rebal_note = None
        self._rebal_err_ticks = 0
        self._rpc_applied = None
        self._state = self._load()
        threading.Thread(target=self._listen_loop, daemon=True).start()
        if self._state.get("mode") in ("server", "worker", "member"):
            threading.Thread(target=self._resume, daemon=True).start()
    def _load(self):
        try:
            with open(self._state_path or lan_file(), "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                d.setdefault("mode", "off")
                d.setdefault("name", socket.gethostname() or "shard")
                d.setdefault("pairing_code", None)
                d.setdefault("master", None)
                d.setdefault("members", [])
                d.setdefault("open", False)
                d.setdefault("created_at", None)
                d.setdefault("target_model", None)
                return d
        except Exception:
            pass
        return {"mode": "off", "name": socket.gethostname() or "shard",
                "pairing_code": None, "master": None, "members": [],
                "open": False, "created_at": None, "target_model": None}
    def _save(self):
        with self._lock:
            snap = json.loads(json.dumps(self._state, ensure_ascii=False))
        try:
            p = self._state_path or lan_file()
            os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
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
        if not self.is_server():
            return False
        now = time.time()
        with self._lock:
            return any(m.get("role") == "worker" and m.get("rpc_port")
                       and member_alive(m, now)
                       for m in self._state.get("members", []))
    def _record_hw_peak(self, key, vram_free_mb, ram_avail_mb):
        try:
            v = int(float(vram_free_mb or 0))
            r = int(float(ram_avail_mb or 0))
        except Exception:
            return
        with self._lock:
            peak = self._hw_peak.setdefault(key, {"vram": 0, "ram": 0})
            peak["vram"] = max(peak["vram"], max(0, v))
            peak["ram"] = max(peak["ram"], max(0, r))
    def _pool_snapshot(self):
        now = time.time()
        with self._lock:
            workers = [dict(m) for m in self._state.get("members", [])
                       if m.get("role") == "worker" and m.get("rpc_port")
                       and member_alive(m, now)]
        local = {}
        try:
            local = _hardware.get_hardware_profile()
        except Exception:
            local = {}
        if workers:
            self._record_hw_peak("master", local.get("vram_free_mb"),
                                 local.get("ram_avail_mb"))
            with self._lock:
                pk = dict(self._hw_peak.get("master") or {})
            budgets = [device_budget(pk.get("vram"), pk.get("ram"))]
        else:
            budgets = [device_budget(local.get("vram_free_mb"),
                                     local.get("ram_avail_mb"))]
        scores = [speed_score(local) if local else None]
        for m in workers:
            hw = m.get("hw") or {}
            self._record_hw_peak(m["ip"], hw.get("vram_free_mb"),
                                 hw.get("ram_avail_mb"))
            with self._lock:
                pk = dict(self._hw_peak.get(m["ip"]) or {})
            budgets.append(device_budget(pk.get("vram"), pk.get("ram")))
            scores.append(speed_score(hw) if hw else None)
        return workers, budgets, scores
    def rpc_targets(self):
        if not self.pool_active():
            return None
        workers, budgets, scores = self._pool_snapshot()
        addrs = [f"{m['ip']}:{int(m['rpc_port'])}" for m in workers]
        return {"servers": format_rpc_arg(addrs),
                "split": format_ts_arg(split_weights_scored(budgets, scores))}
    def pool_capacity_mb(self):
        _, budgets, _ = self._pool_snapshot()
        return sum(int(v) if int(v) > 0 else int(r) for v, r in budgets)
    def status(self):
        now = time.time()
        with self._lock:
            st = json.loads(json.dumps(self._state, ensure_ascii=False))
            for m in st.get("members", []):
                m["alive"] = member_alive(m, now)
                m["token"] = None
            peers = []
            for ip, info in list(self._peers.items()):
                if (now - info.get("_ts", 0)) > PEER_TTL_S:
                    continue
                peers.append({"ip": ip, **{k: info.get(k) for k in
                                       ("name", "role", "model", "port",
                                        "open")}})
        rpc = self.rpc_targets()
        host_ips = local_ips()
        primary_ip = host_ips[0] if host_ips else local_ip()
        out = {"mode": st.get("mode"), "name": st.get("name"),
               "ip": primary_ip, "ips": host_ips,
               "pairing_code": st.get("pairing_code"),
               "open": bool(st.get("open")),
               "master": st.get("master"), "members": st.get("members"),
               "peers": peers, "rpc": rpc,
               "target_model": st.get("target_model"),
               "rebalance_note": self._rebal_note,
               "constants": {"beacon_port": BEACON_PORT,
                             "beacon_interval_s": BEACON_INTERVAL_S,
                             "heartbeat_interval_s": HEARTBEAT_INTERVAL_S,
                             "heartbeat_miss_limit": HEARTBEAT_MISS_LIMIT,
                             "rebalance_interval_s": REBALANCE_INTERVAL_S}}
        if st.get("mode") == "server":
            try:
                out["pool_capacity_mb"] = self.pool_capacity_mb()
            except Exception:
                pass
        return out
    def create(self, name=None, open_net=False, model=None):
        with self._lock:
            if self._state.get("mode") == "server":
                if model:
                    return self.set_model(model)
                return self.status()
            self.off(quiet=True)
            tm = str(model).strip() if model else None
            self._state.update({
                "mode": "server",
                "name": (name or self._state.get("name") or
                         socket.gethostname() or "shard"),
                "pairing_code": gen_pairing_code(),
                "members": [],
                "master": None,
                "open": bool(open_net),
                "created_at": time.time(),
                "target_model": tm or None,
            })
            self._save()
        self._start_threads("server")
        return self.status()
    def set_model(self, model_id):
        model_id = str(model_id or "").strip()
        with self._lock:
            if self._state.get("mode") != "server":
                return {"status": "error",
                        "message": "Модель задаётся только на сервере shard-сети"}
            if not model_id:
                return {"status": "error", "message": "Пустое имя модели"}
            self._state["target_model"] = model_id
            self._rebal_note = None
            self._rebal_err_ticks = 0
            self._save()
            loaded = self._loaded_model_name()
        if loaded == model_id:
            msg = f"Модель сети: {model_id} (уже загружена)"
        else:
            msg = (f"Модель сети: {model_id} — загрузится автоматически, "
                   f"как только подключится вычислительный участник (--share)")
        return {"status": "ok", "target_model": model_id, "message": msg}
    def set_open(self, open_net):
        with self._lock:
            if self._state.get("mode") != "server":
                return {"status": "error",
                        "message": "Сеть не создана — открыть можно только свою сеть"}
            self._state["open"] = bool(open_net)
            self._save()
        if open_net:
            return {"status": "ok", "open": True,
                    "message": "Сеть открыта: подключение без кода"}
        return {"status": "ok", "open": False,
                "message": "Сеть закрыта: нужен код подключения"}
    def handle_pair(self, code, ip, name=None, share=False):
        code = normalize_code(code)
        with self._lock:
            if self._state.get("mode") != "server":
                return None, "Эта машина не является сервером shard-сети"
            if not self._state.get("open") and \
                    (not code or code != self._state.get("pairing_code")):
                return None, "Неверный код подключения"
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
                        self._record_hw_peak(
                            m["ip"], hw.get("vram_free_mb"),
                            hw.get("ram_avail_mb"))
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
    def join(self, ip, code, share=False, port=DEFAULT_MASTER_PORT):
        clean_ip, ip_err = validate_master_host(ip)
        if ip_err:
            return {"status": "error", "message": ip_err}
        import requests
        url = f"http://{clean_ip}:{int(port)}/api/lan/pair"
        try:
            r = requests.post(url, json={"code": str(code or ""),
                                         "name": self.name(), "share": share},
                              timeout=5)
        except Exception as e:
            return {"status": "error",
                    "message": f"Мастер {clean_ip}:{port} недоступен: {str(e)[:120]}"}
        if r.status_code == 403:
            return {"status": "error",
                    "message": "Сеть закрыта: нужен код подключения"}
        if r.status_code != 200:
            return {"status": "error",
                    "message": f"Мастер ответил {r.status_code}: {r.text[:120]}"}
        data = r.json()
        with self._lock:
            self.off(quiet=True)
            self._state.update({
                "mode": "worker" if share else "member",
                "master": {"ip": clean_ip, "port": int(port),
                           "token": data["token"],
                           "name": data.get("master") or clean_ip},
                "created_at": time.time(),
            })
            self._save()
        warn = None
        if share:
            try:
                self._start_worker()
            except Exception as e:
                warn = str(e)[:160]
                with self._lock:
                    self._state["mode"] = "member"
                    self._state["rpc_port"] = None
                    self._save()
        self._start_threads(self.mode())
        res = {"status": "ok", "mode": self.mode(),
               "master": data.get("master") or clean_ip,
               "master_ip": clean_ip}
        if warn:
            res["warning"] = warn
        return res
    def _start_worker(self):
        if self._rpc_proc is not None and self._rpc_proc.poll() is None:
            return
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
        time.sleep(0.3)
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
    def off(self, quiet=False):
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
                                "created_at": None, "rpc_port": None,
                                "open": False})
            self._hw_peak.clear()
            self._rebal_note = None
            self._rebal_err_ticks = 0
            self._rpc_applied = None
            self._save()
            self._threads = {}
        if not quiet and mode != "off":
            print(f"[lan] сеть отключена (был режим {mode})")
        return {"status": "ok", "mode": "off"}
    def _resume(self):
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
    def _start_threads(self, mode):
        self._stop.set()
        for t in self._threads.values():
            if t.is_alive():
                t.join(timeout=1.0)
        self._stop.clear()
        spawn = {
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
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except Exception:
            pass
        while not self._stop.is_set():
            if self.mode() != "server":
                break
            with self._lock:
                open_net = bool(self._state.get("open"))
            payload = beacon_payload(self.name(), "server",
                                     model=self._loaded_model_name(),
                                     port=DEFAULT_MASTER_PORT, open_net=open_net)
            for target in ("<broadcast>", "255.255.255.255"):
                try:
                    sock.sendto(json.dumps(payload).encode("utf-8"),
                                (target, BEACON_PORT))
                except Exception:
                    pass
            self._stop.wait(BEACON_INTERVAL_S)
        sock.close()
    def _loaded_model_name(self):
        try:
            from .engine import engine_instance
            return engine_instance.get_status().get("current_loaded") or None
        except Exception:
            return None
    def _listen_loop(self):
        sock = None
        while sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("", BEACON_PORT))
                sock.settimeout(0.5)
            except OSError:
                sock.close()
                sock = None
                time.sleep(1.0)
        while True:
            try:
                raw, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            d = parse_beacon(raw)
            if not d or d.get("name") == self.name():
                continue
            self._peers[addr[0]] = {k: d.get(k) for k in
                                    ("name", "role", "model", "port",
                                     "open")}
            self._peers[addr[0]]["_ts"] = time.time()
        sock.close()
    def _hb_send_loop(self):
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
                hw = _hardware.get_hardware_profile()
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
        while not self._stop.is_set():
            if not self.is_server():
                break
            self._stop.wait(HEARTBEAT_INTERVAL_S)
def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
LAN = LanManager()
