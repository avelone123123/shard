import gc
import json
import os
import re
import sys
import threading
import time
from . import hardware as _hardware
from . import paths as _paths
from . import settings as _shard_settings
from . import storage as _storage
from .shard_bridge import shard_native
if "HF_HOME" not in os.environ:
    try:
        _models_root = _shard_settings.models_dir()
    except Exception:
        _models_root = None
    if not _models_root:
        _models_root = _paths.default_models_dir()
    try:
        os.makedirs(_models_root, exist_ok=True)
    except Exception:
        _models_root = _paths.default_models_dir()
        os.makedirs(_models_root, exist_ok=True)
    os.environ["HF_HOME"] = _models_root
    os.environ["TRANSFORMERS_CACHE"] = _models_root
_active_engine = None
def _progress_callback(curr, total, rate, desc):
    if _active_engine:
        _active_engine.on_tqdm_update(curr, total, rate, desc)
try:
    import tqdm
    import tqdm.auto
    _orig_tqdm = tqdm.tqdm
    class ShardTqdm(_orig_tqdm):
        def update(self, n=1):
            res = super().update(n)
            try:
                d = self.format_dict
                _progress_callback(d.get("n", 0), d.get("total", 0), d.get("rate", 0), d.get("prefix", "") or d.get("desc", ""))
            except Exception:
                pass
            return res
    tqdm.tqdm = ShardTqdm
    tqdm.auto.tqdm = ShardTqdm
except Exception:
    ShardTqdm = None
try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, StoppingCriteria, StoppingCriteriaList, TextIteratorStreamer
    TORCH_AVAILABLE = True
    torch.set_num_threads(max(2, min(12, os.cpu_count() or 4)))
except Exception:
    torch = None
    AutoModelForCausalLM = None
    AutoTokenizer = None
    StoppingCriteria = object
    StoppingCriteriaList = list
    TextIteratorStreamer = None
    TORCH_AVAILABLE = False
try:
    from . import fit as _fit_mod
    from . import gguf_backend as gguf_mod
    from . import lan as _lan_mod
    FIT_AVAILABLE = True
except Exception:
    _fit_mod = None
    gguf_mod = None
    _lan_mod = None
    FIT_AVAILABLE = False
MODEL_LIFECYCLE_STATES = (
    "DISCOVERED",
    "COMPATIBILITY_CHECK",
    "QUEUED",
    "DOWNLOADING",
    "VERIFYING",
    "DOWNLOADED",
    "LOADING",
    "READY",
    "DOWNLOAD_FAILED",
    "VALIDATION_FAILED",
    "LOAD_FAILED",
    "CANCELLED",
)
def _fix_mojibake_utf8(text):
    if not text or not isinstance(text, str):
        return text
    if "Р" not in text and "С" not in text and "Ð" not in text and "Ñ" not in text:
        return text
    for enc in ("cp1251", "latin1"):
        try:
            raw = text.encode(enc)
            if not any(0x80 <= b <= 0xBF for b in raw):
                continue
            fixed = raw.decode("utf-8")
            if fixed != text and any("\u0400" <= ch <= "\u04ff" for ch in fixed):
                return fixed
        except Exception:
            pass
    return text
class _StreamSanitizer:
    STRIP_SEQ = (
        "<|im_start|>assistant\r\n",
        "<|im_start|>assistant\n",
        "<|im_start|>assistant",
        "<|im_sep|>\r\n",
        "<|im_sep|>\n",
        "<|im_sep|>",
    )
    STOP_SEQ = (
        "<|im_end|>",
        "<|endoftext|>",
        "<|im_start|>user",
        "<|im_start|>system",
        "<|im_start|>",
        "\nuser:",
    )
    ALL_WATCH = STRIP_SEQ + STOP_SEQ + ("<think>", "</think>")
    def __init__(self):
        self.buf = ""
        self.in_think = False
        self.stopped = False
        self.think_started_at = None
        self.think_duration = 0.0
    def _hold_len(self, text):
        max_k = min(len(text), 26)
        for k in range(max_k, 0, -1):
            suffix = text[-k:]
            for pat in self.ALL_WATCH:
                if len(pat) > k and pat.startswith(suffix):
                    return k
        return 0
    def feed(self, chunk, now_ts=None):
        if self.stopped or not chunk:
            return []
        now_ts = now_ts or time.time()
        self.buf += chunk
        events = []
        while self.buf and not self.stopped:
            changed = False
            for s in self.STRIP_SEQ:
                if s in self.buf:
                    self.buf = self.buf.replace(s, "")
                    changed = True
            if "<think>" in self.buf:
                idx = self.buf.find("<think>")
                before = self.buf[:idx]
                if before:
                    events.append(("thought" if self.in_think else "content", before))
                self.in_think = True
                if self.think_started_at is None:
                    self.think_started_at = now_ts
                self.buf = self.buf[idx + len("<think>"):]
                changed = True
                continue
            if "</think>" in self.buf:
                idx = self.buf.find("</think>")
                before = self.buf[:idx]
                if before:
                    events.append(("thought", before))
                self.in_think = False
                if self.think_started_at is not None:
                    self.think_duration = round(now_ts - self.think_started_at, 2)
                self.buf = self.buf[idx + len("</think>"):]
                changed = True
                continue
            earliest_stop = None
            earliest_pos = None
            for st in self.STOP_SEQ:
                pos = self.buf.find(st)
                if pos != -1 and (earliest_pos is None or pos < earliest_pos):
                    earliest_pos = pos
                    earliest_stop = st
            if earliest_stop is not None:
                before = self.buf[:earliest_pos]
                if before:
                    events.append(("thought" if self.in_think else "content", before))
                self.buf = ""
                self.stopped = True
                break
            hold = self._hold_len(self.buf)
            emit_len = len(self.buf) - hold
            if emit_len > 0:
                out_txt = _fix_mojibake_utf8(self.buf[:emit_len])
                self.buf = self.buf[emit_len:]
                events.append(("thought" if self.in_think else "content", out_txt))
            if not changed:
                break
        return events
    def flush(self):
        if self.stopped or not self.buf:
            self.buf = ""
            return []
        for s in self.STRIP_SEQ:
            self.buf = self.buf.replace(s, "")
        for st in self.STOP_SEQ:
            if st in self.buf:
                self.buf = self.buf.split(st, 1)[0]
                self.stopped = True
        self.buf = self.buf.replace("<think>", "").replace("</think>", "")
        if not self.buf:
            return []
        out = [("thought" if self.in_think else "content", _fix_mojibake_utf8(self.buf))]
        self.buf = ""
        return out
def _lan_pooled():
    return _lan_mod is not None and _lan_mod.LAN is not None and _lan_mod.LAN.pool_active()
def _lan_pool_fit_ok(fit):
    if fit is None or _lan_mod is None or _lan_mod.LAN is None or not _lan_mod.LAN.pool_active():
        return False
    if fit.get("disk_ok") is False:
        return False
    try:
        need = next(q.get("total_gb") for q in fit.get("quants", [])
                    if q.get("quant") == fit.get("recommended"))
    except StopIteration:
        need = fit.get("fp16_gb")
    if not need:
        return False
    cap = _lan_mod.LAN.pool_capacity_mb()
    return cap / 1024.0 >= float(need)
_airllm_env = os.environ.get("SHARD_AIRLLM_PATH", "").strip()
if _airllm_env and os.path.exists(_airllm_env) and _airllm_env not in sys.path:
    sys.path.append(_airllm_env)
class _ShardCancelCriteria(StoppingCriteria):
    def __init__(self, engine_ref):
        self.engine_ref = engine_ref
    def __call__(self, input_ids, scores, **kwargs):
        return bool(self.engine_ref._cancel_gen)
def _all_hub_roots():
    dirs = []
    candidates = [
        os.environ.get("HF_HOME"),
        _shard_settings.models_dir(),
        _paths.default_models_dir(),
        os.path.join(os.path.expanduser("~"), ".shard", "models"),
        os.path.join(os.path.expanduser("~"), ".cache", "huggingface"),
        str(_paths.data_dir() / "models"),
    ]
    for d in candidates:
        if d:
            h = os.path.join(d, "hub") if not str(d).endswith("hub") else d
            norm = os.path.normcase(os.path.abspath(h))
            if os.path.isdir(h) and norm not in [os.path.normcase(os.path.abspath(x)) for x in dirs]:
                dirs.append(h)
    return dirs
def _hub_dir_complete(hub_dir):
    if not hub_dir or not os.path.exists(hub_dir):
        return False
    blobs_dir = os.path.join(hub_dir, "blobs")
    if os.path.exists(blobs_dir):
        now_t = time.time()
        for fn in os.listdir(blobs_dir):
            if fn.endswith(".incomplete"):
                fp = os.path.join(blobs_dir, fn)
                try:
                    if now_t - os.path.getmtime(fp) < 120:
                        return False
                except Exception:
                    return False
    snap_dir = os.path.join(hub_dir, "snapshots")
    if os.path.isdir(snap_dir):
        for r, _, files in os.walk(snap_dir):
            for fn in files:
                if fn.endswith(".part") or fn.endswith(".incomplete"):
                    return False
                if fn.endswith((".safetensors", ".bin", ".gguf")):
                    fp = os.path.join(r, fn)
                    try:
                        if os.path.getsize(fp) > 0:
                            return True
                    except Exception:
                        pass
    return False
def _is_model_cached_complete(model_id):
    if not model_id:
        return False
    raw = str(model_id).strip()
    if raw.startswith("\\\\") or raw.startswith("//"):
        return False
    if os.path.isfile(raw) and raw.lower().endswith(".gguf") and os.path.getsize(raw) > 0:
        return True
    if os.path.isdir(raw):
        has_cfg = os.path.isfile(os.path.join(raw, "config.json")) or os.path.isfile(os.path.join(raw, "adapter_config.json"))
        if has_cfg:
            return True
    if gguf_mod is not None:
        try:
            loc = gguf_mod.find_local_gguf(raw)
            if loc and os.path.isfile(loc.get("path", "")):
                return True
        except Exception:
            pass
    repo = raw if "/" in raw else f"Qwen/{raw}"
    tail = repo.split("/")[-1].lower()
    tail_clean = tail[:-9] if tail.endswith("-instruct") else tail
    tail_clean = tail_clean[:-5] if tail_clean.endswith("-gguf") else tail_clean
    for hub_root in _all_hub_roots():
        direct_dir = os.path.join(hub_root, f"models--{repo.replace('/', '--')}")
        if _hub_dir_complete(direct_dir):
            return True
        if os.path.isdir(hub_root):
            try:
                for entry in os.listdir(hub_root):
                    low = entry.lower()
                    if not low.startswith("models--"):
                        continue
                    if tail in low or (tail_clean and tail_clean in low):
                        if _hub_dir_complete(os.path.join(hub_root, entry)):
                            return True
            except Exception:
                pass
    return False
class ShardEngine:
    def __init__(self):
        global _active_engine
        _active_engine = self
        self.active_model_file = _paths.active_model_file()
        os.makedirs(os.path.dirname(self.active_model_file), exist_ok=True)
        saved_model = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
        saved_obj = _storage.read_json_safe(
            self.active_model_file,
            default=None,
            expected_type=dict,
            quarantine=True,
            use_backup=True,
        )
        if isinstance(saved_obj, dict) and saved_obj.get("model_id"):
            saved_model = str(saved_obj.get("model_id")).strip() or saved_model
        self.active_model_id = saved_model
        self.status = "idle"
        self.detailed_state = "DISCOVERED"
        self.model_states = {}
        self.loaded_info = {}
        self.total_tokens_generated = 0
        self.last_latency_ms = 0
        self.native_core = shard_native
        self.model = None
        self.tokenizer = None
        self.gguf = None
        self.current_loaded_id = None
        self._cancel_load = False
        self._cancel_gen = False
        self._load_seq = 0
        self.load_progress = {"status": "idle", "lifecycle_state": "DISCOVERED", "percent": 0, "message": "Готов к выбору и запуску модели", "eta_text": "", "stage": "Ожидание", "speed_mb": 0}
        if os.environ.get("SHARD_AUTOLOAD", "0") == "1" and _is_model_cached_complete(self.active_model_id):
            self.load_model(self.active_model_id)
    def cancel_operation(self):
        self._cancel_gen = True
        self._cancel_load = True
        self._load_seq += 1
        was_loading = (self.status == "loading")
        if was_loading and self.active_model_id:
            self.model_states[self.active_model_id] = "CANCELLED"
            self.detailed_state = "CANCELLED"
        if self.status in ("loading", "generating"):
            self.status = "ready" if self.is_loaded() else "idle"
        if not self.is_loaded():
            self.active_model_id = self.current_loaded_id
        self.load_progress = {"status": "idle", "lifecycle_state": "CANCELLED", "percent": 0, "message": "Операция остановлена пользователем", "eta_text": "", "stage": "Остановлено", "speed_mb": 0}
        return {"status": "cancelled", "engine_status": self.status, "lifecycle_state": "CANCELLED"}
    def on_tqdm_update(self, curr, total, rate, desc):
        if self.status != "loading" or self._cancel_load or not total or total <= 0:
            return
        if total > 100000:
            pct = min(88, max(5, round((curr / total) * 88, 1)))
            speed_mb = round(rate / (1024 * 1024), 1) if rate else 18.0
            eff_rate = rate if (rate and rate > 0) else (18 * 1024 * 1024)
            eta_sec = max(1, int((total - curr) / eff_rate))
            eta_text = f"~{eta_sec} сек" if eta_sec < 60 else f"~{eta_sec // 60} мин {eta_sec % 60} сек"
            curr_mb = round(curr / (1024 * 1024), 1)
            total_mb = round(total / (1024 * 1024), 1)
            self.detailed_state = "DOWNLOADING"
            if self.active_model_id:
                self.model_states[self.active_model_id] = "DOWNLOADING"
            self.load_progress = {
                "status": "loading",
                "lifecycle_state": "DOWNLOADING",
                "model_id": self.active_model_id,
                "percent": pct,
                "stage": "Скачивание весов с Hugging Face",
                "curr_mb": curr_mb,
                "total_mb": total_mb,
                "speed_mb": speed_mb,
                "eta_seconds": eta_sec,
                "eta_text": eta_text,
                "message": f"Скачано {curr_mb} из {total_mb} МБ ({pct}%) | {speed_mb} МБ/с | Осталось: {eta_text}"
            }
        elif total > 1:
            pct = min(99, max(86, 86 + int((curr / total) * 13)))
            eta_sec = max(1, int((total - curr) * 0.05))
            eta_text = f"~{eta_sec} сек"
            prev_c = self.load_progress.get("curr_mb", 0)
            prev_t = self.load_progress.get("total_mb", 0)
            prev_s = self.load_progress.get("speed_mb", 0)
            self.detailed_state = "LOADING"
            if self.active_model_id:
                self.model_states[self.active_model_id] = "LOADING"
            self.load_progress = {
                "status": "loading",
                "lifecycle_state": "LOADING",
                "model_id": self.active_model_id,
                "percent": pct,
                "stage": "Размещение тензоров в памяти",
                "curr_mb": prev_c,
                "total_mb": prev_t,
                "speed_mb": prev_s,
                "eta_seconds": eta_sec,
                "eta_text": eta_text,
                "message": f"Инициализация слоев ({curr}/{total}) в памяти | Осталось: {eta_text}"
            }
    def get_status(self):
        hw = _hardware.get_hardware_profile()
        gguf_info = None
        if self.gguf is not None:
            try:
                gguf_info = self.gguf.info()
            except Exception:
                gguf_info = None
        loaded = self.is_loaded()
        effective_active = self.current_loaded_id if loaded else (self.active_model_id if self.status == "loading" else None)
        return {
            "status": self.status,
            "lifecycle_state": self.lifecycle_state(),
            "detailed_state": self.detailed_lifecycle_state(),
            "active_model": effective_active,
            "selected_model": self.active_model_id,
            "current_loaded": self.current_loaded_id,
            "is_loaded": loaded,
            "load_progress": self.load_progress,
            "loaded_info": self.loaded_info,
            "hardware": hw,
            "gguf": gguf_info,
            "native_engine": {
                "active": self.native_core.is_native_active,
                "version": self.native_core.version
            },
            "total_tokens_generated": self.total_tokens_generated,
            "last_latency_ms": self.last_latency_ms
        }
