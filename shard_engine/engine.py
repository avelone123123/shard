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
    def is_loaded(self):
        return bool(self.model is not None or (self.gguf is not None and self.gguf.is_running()))
    def is_model_active(self, model_id):
        if not self.is_loaded() or not model_id:
            return False
        def _c(x):
            return re.sub(r"(?i)[-_]?(instruct)?[-_]?gguf$", "", str(x or "").split("/")[-1].lower())
        tgt = _c(model_id)
        if self.current_loaded_id and _c(self.current_loaded_id) == tgt:
            return True
        if self.gguf and self.gguf.is_running() and self.gguf.model_id and _c(self.gguf.model_id) == tgt:
            return True
        return False
    def detailed_lifecycle_state(self, model_id=None):
        target = model_id or self.current_loaded_id or self.active_model_id
        if self.is_loaded() and (not target or self.is_model_active(target)):
            return "READY"
        if target and target in self.model_states:
            st = self.model_states[target]
            if st in ("DOWNLOADING", "VERIFYING", "LOADING", "QUEUED", "COMPATIBILITY_CHECK") and self.status != "loading":
                if _is_model_cached_complete(target):
                    return "DOWNLOADED"
            return st
        if self.status == "loading":
            return self.detailed_state if self.detailed_state in MODEL_LIFECYCLE_STATES else "LOADING"
        if self.status == "error":
            return self.detailed_state if self.detailed_state in ("DOWNLOAD_FAILED", "VALIDATION_FAILED", "LOAD_FAILED") else "LOAD_FAILED"
        if target and _is_model_cached_complete(target):
            return "DOWNLOADED"
        return "DISCOVERED"
    def lifecycle_state(self, model_id=None):
        target = model_id or self.active_model_id
        if self.is_loaded() and (not target or self.is_model_active(target)):
            return "LOADED_IN_MEMORY"
        if self.status == "loading":
            return "LOADING"
        if self.status == "unloading":
            return "UNLOADING"
        if self.status == "error":
            return "ERROR"
        if target and _is_model_cached_complete(target):
            return "DOWNLOADED"
        return "NOT_DOWNLOADED"
    def _gguf(self):
        if gguf_mod is None:
            return None
        if self.gguf is None:
            self.gguf = gguf_mod.LlamaBackend()
        return self.gguf
    def _fit_for(self, model_id):
        if _fit_mod is None:
            return None
        try:
            return _fit_mod.fit_report(model_id)
        except Exception:
            return None
    def _verdict_presentation(self, verdict):
        return {
            "GPU": ("PERFECT", "ИДЕАЛЬНО (VRAM)", "#22a06b"),
            "HYBRID": ("RECOMMENDED", "ГИБРИД (VRAM+RAM)", "#d4943a"),
            "IMPOSSIBLE": ("BLOCKED", "НЕ ЗАПУСТИТСЯ", "#c96a4b"),
        }.get(verdict, ("UNKNOWN", "НЕИЗВЕСТНО", "#888888"))
    def _select_dtype_and_device(self, params_b, hw):
        if not TORCH_AVAILABLE or torch is None:
            return None, "cpu"
        if torch.cuda.is_available():
            fp16_mb = params_b * 2050
            if fp16_mb <= hw.get("vram_free_mb", 0) + 250:
                return torch.float16, "cuda"
            return torch.float16, "auto"
        if params_b <= 0.9 and hw.get("ram_avail_mb", 4096) >= 2800:
            return torch.float32, "cpu"
        return torch.float16, "cpu"
    def _run_loader(self, model_id, seq_id, prev_loaded_id=None):
        if prev_loaded_id is None and self.current_loaded_id and self.current_loaded_id != model_id:
            prev_loaded_id = self.current_loaded_id
        self.status = "loading"
        self._cancel_load = False
        if not TORCH_AVAILABLE:
            self.status = "error"
            self.detailed_state = "LOAD_FAILED"
            self.model_states[model_id] = "LOAD_FAILED"
            self.current_loaded_id = prev_loaded_id
            self.active_model_id = prev_loaded_id
            self.load_progress = {"status": "error", "lifecycle_state": "LOAD_FAILED", "model_id": model_id, "percent": 0, "stage": "Ошибка", "eta_text": "Ошибка", "speed_mb": 0, "message": "PyTorch/Transformers не установлены. Выполните: pip install \"shard-engine[full]\""}
            return
        is_local_path = os.path.exists(str(model_id or ""))
        target_repo = model_id if is_local_path else (model_id if "/" in model_id else f"Qwen/{model_id}")
        params_b = _hardware.estimate_model_params(model_id)
        already_cached = is_local_path or _is_model_cached_complete(target_repo)
        est_mb = max(120, int(params_b * (1950 if params_b <= 3.0 else 650)))
        est_sec = 3 if already_cached else max(4, int(est_mb / 20))
        eta_init = f"~{est_sec} сек" if est_sec < 60 else f"~{est_sec // 60} мин {est_sec % 60} сек"
        init_state = "LOADING" if already_cached else "DOWNLOADING"
        self.detailed_state = init_state
        self.model_states[model_id] = init_state
        self.load_progress = {
            "status": "loading",
            "lifecycle_state": init_state,
            "model_id": model_id,
            "percent": 75 if already_cached else 5,
            "stage": "Чтение весов с локального SSD" if already_cached else "Подключение к Hugging Face Hub",
            "curr_mb": est_mb if already_cached else 0,
            "total_mb": est_mb,
            "speed_mb": 0,
            "eta_seconds": est_sec,
            "eta_text": eta_init,
            "message": f"Загрузка с локального SSD: {target_repo}" if already_cached else f"Подготовка скачивания {target_repo}..."
        }
        t0 = time.time()
        stop_ticker = False
        def _ticker():
            nonlocal stop_ticker
            hf_home = _shard_settings.models_dir() or os.environ.get("HF_HOME")
            hub_dir = os.path.join(hf_home, "hub", f"models--{target_repo.replace('/', '--')}", "blobs")
            last_bytes = 0
            last_t = t0
            while not stop_ticker and self.status == "loading" and seq_id == self._load_seq:
                time.sleep(0.8)
                if stop_ticker or self._cancel_load:
                    break
                p = self.load_progress.get("percent", 5)
                if p < 88 and os.path.exists(hub_dir):
                    curr_bytes = 0
                    try:
                        for fn in os.listdir(hub_dir):
                            fp = os.path.join(hub_dir, fn)
                            if os.path.isfile(fp):
                                curr_bytes += os.path.getsize(fp)
                    except Exception:
                        pass
                    cur_mb = round(curr_bytes / (1024 * 1024), 1)
                    if cur_mb > 2:
                        now_t = time.time()
                        dt_int = max(0.2, now_t - last_t)
                        spd = round(((curr_bytes - last_bytes) / (1024 * 1024)) / dt_int, 1)
                        if spd <= 0.1:
                            spd = round(cur_mb / max(0.5, now_t - t0), 1)
                        last_bytes = curr_bytes
                        last_t = now_t
                        target_total = max(est_mb, int(cur_mb * 1.08))
                        cur_p = min(88, max(int(p), int((cur_mb / max(1, target_total)) * 88)))
                        rem_sec = max(1, int((target_total - cur_mb) / max(0.5, spd)))
                        eta_t = f"~{rem_sec} сек" if rem_sec < 60 else f"~{rem_sec // 60} мин {rem_sec % 60} сек"
                        if self.load_progress.get("status") == "loading":
                            self.load_progress.update({
                                "lifecycle_state": "DOWNLOADING",
                                "percent": cur_p,
                                "curr_mb": cur_mb,
                                "total_mb": target_total,
                                "speed_mb": spd,
                                "eta_seconds": rem_sec,
                                "eta_text": eta_t,
                                "stage": "Скачивание весов с Hugging Face",
                                "message": f"Скачано {cur_mb} из {target_total} МБ ({cur_p}%) | {spd} МБ/с | Осталось: {eta_t}"
                            })
        if not already_cached:
            threading.Thread(target=_ticker, daemon=True).start()
        try:
            hw = _hardware.get_hardware_profile()
            chosen_dtype, dev_target = self._select_dtype_and_device(params_b, hw)
            adapter_cfg_path = os.path.join(target_repo, "adapter_config.json") if is_local_path and os.path.isdir(target_repo) else None
            is_peft_adapter = bool(adapter_cfg_path and os.path.isfile(adapter_cfg_path) and not os.path.isfile(os.path.join(target_repo, "config.json")))
            base_or_dir = target_repo
            if is_peft_adapter:
                with open(adapter_cfg_path, "r", encoding="utf-8") as af:
                    base_or_dir = json.load(af).get("base_model_name_or_path") or target_repo
            trust_remote = os.environ.get("SHARD_TRUST_REMOTE_CODE", "0") == "1"
            tok = AutoTokenizer.from_pretrained(target_repo if os.path.isfile(os.path.join(target_repo, "tokenizer_config.json")) else base_or_dir, trust_remote_code=trust_remote)
            if self._cancel_load or seq_id != self._load_seq:
                stop_ticker = True
                return
            self.detailed_state = "LOADING"
            self.model_states[model_id] = "LOADING"
            load_kw = {"low_cpu_mem_usage": True, "trust_remote_code": trust_remote}
            if chosen_dtype is not None:
                load_kw["dtype"] = chosen_dtype
            is_heavy = (not is_local_path) and params_b >= 12.0
            mdl = None
            if is_heavy:
                try:
                    from airllm import AutoModel as AirAutoModel
                    mdl = AirAutoModel.from_pretrained(base_or_dir)
                except Exception:
                    mdl = None
            if mdl is None:
                if dev_target == "auto":
                    try:
                        mdl = AutoModelForCausalLM.from_pretrained(base_or_dir, device_map="auto", **load_kw)
                    except Exception:
                        mdl = AutoModelForCausalLM.from_pretrained(base_or_dir, **load_kw)
                else:
                    mdl = AutoModelForCausalLM.from_pretrained(base_or_dir, **load_kw)
                    if dev_target == "cuda" and torch.cuda.is_available():
                        mdl = mdl.to("cuda")
            if is_peft_adapter:
                try:
                    from peft import PeftModel
                    mdl = PeftModel.from_pretrained(mdl, target_repo)
                except ImportError as peft_err:
                    raise RuntimeError("LoRA-адаптер требует установленный peft или предварительное merge-объединение весов.") from peft_err
            if self._cancel_load or seq_id != self._load_seq:
                stop_ticker = True
                del mdl
                gc.collect()
                return
            if hasattr(mdl, "eval"):
                mdl.eval()
            stop_ticker = True
            if tok.pad_token is None:
                tok.pad_token = tok.eos_token
            self.tokenizer = tok
            self.model = mdl
            self.current_loaded_id = model_id
            self.active_model_id = model_id
            self.status = "ready"
            self.detailed_state = "READY"
            self.model_states[model_id] = "READY"
            self.load_progress = {
                "status": "ready",
