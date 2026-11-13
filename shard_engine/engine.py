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
    pass
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
    snap_dir = os.path.join(hub_dir, "snapshots")
    if os.path.isdir(snap_dir):
        for r, _, files in os.walk(snap_dir):
            for fn in files:
                if fn.endswith((".safetensors", ".bin", ".gguf")):
                    fp = os.path.join(r, fn)
                    try:
                        if os.path.getsize(fp) > 0:
                            return True
                    except Exception:
                        pass
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
        self.load_progress = {"status": "idle", "percent": 0, "message": "Готов к выбору и запуску модели", "eta_text": "", "stage": "Ожидание", "speed_mb": 0}
        if os.environ.get("SHARD_AUTOLOAD", "0") == "1" and _is_model_cached_complete(self.active_model_id):
            self.load_model(self.active_model_id)
    def cancel_operation(self):
        self._cancel_gen = True
        self._cancel_load = True
        self._load_seq += 1
        if self.status in ("loading", "generating"):
            self.status = "ready" if self.model is not None else "idle"
        self.load_progress = {"status": "idle", "percent": 0, "message": "Операция остановлена пользователем", "eta_text": "", "stage": "Остановлено", "speed_mb": 0}
        return {"status": "cancelled", "engine_status": self.status}
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
            self.load_progress = {
                "status": "loading",
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
            self.load_progress = {
                "status": "loading",
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
        return {
            "status": self.status,
            "lifecycle_state": self.lifecycle_state(),
            "active_model": self.active_model_id,
            "current_loaded": self.current_loaded_id,
            "is_loaded": self.is_loaded(),
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
    def _run_loader(self, model_id, seq_id):
        self.status = "loading"
        self._cancel_load = False
        if not TORCH_AVAILABLE:
            self.status = "error"
            self.load_progress = {"status": "error", "model_id": model_id, "percent": 0, "stage": "Ошибка", "eta_text": "Ошибка", "speed_mb": 0, "message": "PyTorch/Transformers не установлены. Выполните: pip install \"shard-engine[full]\""}
            return
        is_local_path = os.path.exists(str(model_id or ""))
        target_repo = model_id if is_local_path else (model_id if "/" in model_id else f"Qwen/{model_id}")
        params_b = _hardware.estimate_model_params(model_id)
        already_cached = is_local_path or _is_model_cached_complete(target_repo)
        est_mb = max(120, int(params_b * (1950 if params_b <= 3.0 else 650)))
        est_sec = 3 if already_cached else max(4, int(est_mb / 20))
        eta_init = f"~{est_sec} сек" if est_sec < 60 else f"~{est_sec // 60} мин {est_sec % 60} сек"
        self.load_progress = {
            "status": "loading",
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
            hf_home = os.environ.get("HF_HOME") or _shard_settings.models_dir()
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
            self.load_progress = {
                "status": "ready",
                "model_id": model_id,
                "percent": 100,
                "stage": "Готова к работе",
                "curr_mb": est_mb,
                "total_mb": est_mb,
                "speed_mb": 0,
                "eta_seconds": 0,
                "eta_text": "Готово",
                "message": f"Модель {model_id} активна и готова к работе."
            }
            try:
                _storage.atomic_write_json(
                    self.active_model_file,
                    {"model_id": model_id, "updated_at": time.time()},
                    fsync=True,
                    backup=True,
                )
            except Exception:
                pass
        except Exception as e:
            stop_ticker = True
            if seq_id != self._load_seq:
                return
            self.status = "error"
            self.load_progress = {
                "status": "error",
                "model_id": model_id,
                "percent": 0,
                "stage": "Ошибка загрузки",
                "curr_mb": 0,
                "total_mb": est_mb,
                "speed_mb": 0,
                "eta_seconds": 0,
                "eta_text": "Ошибка",
                "message": f"Сбой загрузки: {str(e)[:140]}"
            }
    def _run_loader_gguf(self, model_id, seq_id, fit):
        self.status = "loading"
        self._cancel_load = False
        def _fail(stage, message):
            if seq_id != self._load_seq:
                return
            self.status = "error"
            self.load_progress = {
                "status": "error",
                "model_id": model_id,
                "percent": 0,
                "stage": stage,
                "curr_mb": 0,
                "total_mb": 0,
                "speed_mb": 0,
                "eta_seconds": 0,
                "eta_text": "Ошибка",
                "message": message
            }
        def _stop_backend():
            if self.gguf is not None:
                try:
                    self.gguf.stop()
                except Exception:
                    pass
        pooled_ok = _lan_pool_fit_ok(fit)
        if fit is None or (fit.get("verdict") not in ("GPU", "HYBRID")
                           and not pooled_ok):
            msg = (fit or {}).get("message_ru") or "Модель не подходит под ваше железо."
            mmap_note = (fit or {}).get("mmap_note") or ""
            note = f"{msg} {mmap_note}".strip()
            if _lan_pooled():
                note += " Модель не влезает даже в объединённую память shard-сети."
            _fail("Модель не подходит", note)
            return
        try:
            loc = gguf_mod.find_local_gguf(model_id, fit.get("recommended")) if gguf_mod else None
            is_local_gguf = os.path.isfile(str(model_id or "")) and str(model_id).lower().endswith(".gguf")
            if loc and os.path.isfile(loc.get("path", "")) and loc.get("size_bytes", 0) > 0:
                sel_path = loc["path"]
                repo_id = loc.get("repo_id") or model_id
                f_sz = loc["size_bytes"]
                total_mb = round(f_sz / (1024 * 1024), 1)
                sel = {
                    "path": os.path.basename(sel_path),
                    "size_bytes": f_sz,
                    "quant": loc.get("quant") or fit.get("recommended") or "CUSTOM",
                    "multipart": False,
                    "parts": [sel_path],
                }
                self.load_progress = {
                    "status": "loading",
                    "model_id": model_id,
                    "percent": 88,
                    "stage": "Чтение весов с локального SSD",
                    "curr_mb": total_mb,
                    "total_mb": total_mb,
                    "speed_mb": 0,
                    "eta_seconds": 1,
                    "eta_text": "Запуск...",
                    "message": f"Локальные веса найдены на SSD: {os.path.basename(sel_path)} ({total_mb} МБ, квант {sel['quant']})"
                }
            elif is_local_gguf:
                sel_path = model_id
                repo_id = "local"
                f_sz = os.path.getsize(sel_path)
                total_mb = round(f_sz / (1024 * 1024), 1)
                sel = {
                    "path": os.path.basename(sel_path),
                    "size_bytes": f_sz,
                    "quant": fit.get("recommended") or "CUSTOM",
                    "multipart": False,
                    "parts": [sel_path],
                }
            else:
                self.load_progress = {
                    "status": "loading",
                    "model_id": model_id,
                    "percent": 2,
                    "stage": "Поиск GGUF-репозитория",
                    "curr_mb": 0,
                    "total_mb": 0,
                    "speed_mb": 0,
                    "eta_seconds": 0,
                    "eta_text": "Поиск...",
                    "message": f"Ищу готовый GGUF для {model_id} (квант {fit.get('recommended')})..."
                }
                repo_id = gguf_mod.find_gguf_repo(model_id)
                if self._cancel_load or seq_id != self._load_seq:
                    return
                if not repo_id:
                    _fail("GGUF не найден", f"Для {model_id} не нашёл готовый GGUF-репозиторий на Hugging Face. Попробуйте модель с официально выложенным GGUF (например, Qwen или Gemma).")
                    return
                files = gguf_mod.list_gguf_files(repo_id)
                if self._cancel_load or seq_id != self._load_seq:
                    return
                sel = gguf_mod.select_gguf_file(files, wanted_quant=fit.get("recommended"))
                if not sel:
                    _fail("GGUF не найден", f"В {repo_id} нет подходящего GGUF-файла (квант {fit.get('recommended')}).")
                    return
                total_mb = round(sel["size_bytes"] / (1024 * 1024), 1)
                loc_cand = gguf_mod.find_local_gguf(repo_id, sel.get("quant")) if gguf_mod else None
                if loc_cand and os.path.isfile(loc_cand.get("path", "")) and loc_cand.get("size_bytes", 0) > 0:
                    sel_path = loc_cand["path"]
                else:
                    sel_path = None
                    hf_home = os.environ.get("HF_HOME") or _shard_settings.models_dir()
                    hub_dir = os.path.join(hf_home, "hub", f"models--{repo_id.replace('/', '--')}")
                    if os.path.isdir(hub_dir):
                        found_parts = {}
                        for root, _, fns in os.walk(hub_dir):
                            for p in sel["parts"]:
                                base = os.path.basename(p)
                                if base in fns and base not in found_parts:
                                    found_parts[base] = os.path.join(root, base)
                        if len(found_parts) == len(sel["parts"]):
                            sel_path = found_parts[os.path.basename(sel["parts"][0])]
                if sel_path is None:
                    est_sec = max(5, int(total_mb / 20))
                    eta_init = f"~{est_sec} сек" if est_sec < 60 else f"~{est_sec // 60} мин {est_sec % 60} сек"
                    self.load_progress = {
                        "status": "loading",
                        "model_id": model_id,
                        "percent": 4,
                        "stage": "Подключение к Hugging Face Hub",
                        "curr_mb": 0,
                        "total_mb": total_mb,
                        "speed_mb": 0,
                        "eta_seconds": est_sec,
                        "eta_text": eta_init,
                        "message": f"Скачивание {repo_id}/{sel['path']} ({total_mb:.0f} МБ, квант {sel['quant']})..."
                    }
                    t0 = time.time()
                    stop_ticker = False
                    def _ticker():
                        nonlocal stop_ticker
                        blobs_dir = os.path.join(hub_dir, "blobs")
                        snap_dir = os.path.join(hub_dir, "snapshots")
                        last_bytes = 0
                        last_t = t0
                        while not stop_ticker and self.status == "loading" and seq_id == self._load_seq:
                            time.sleep(0.8)
                            if stop_ticker or self._cancel_load:
                                break
                            curr_bytes = 0
                            if os.path.isdir(blobs_dir):
                                try:
                                    for fn in os.listdir(blobs_dir):
                                        fp = os.path.join(blobs_dir, fn)
                                        if os.path.isfile(fp):
                                            mtime = os.path.getmtime(fp)
                                            if not fn.endswith(".incomplete") or mtime >= t0 - 2.0:
                                                curr_bytes += os.path.getsize(fp)
                                except Exception:
                                    pass
                            if os.path.isdir(snap_dir):
                                try:
                                    for r, _, fns in os.walk(snap_dir):
                                        for fn in fns:
                                            if fn.endswith(".gguf"):
                                                fp = os.path.join(r, fn)
                                                if os.path.isfile(fp):
                                                    curr_bytes = max(curr_bytes, os.path.getsize(fp))
                                except Exception:
                                    pass
                            if curr_bytes <= 0:
                                continue
                            cur_mb = round(curr_bytes / (1024 * 1024), 1)
                            now_t = time.time()
                            dt_int = max(0.2, now_t - last_t)
                            spd = round(((curr_bytes - last_bytes) / (1024 * 1024)) / dt_int, 1)
                            if spd <= 0.1:
                                spd = round(cur_mb / max(0.5, now_t - t0), 1)
                            last_bytes = curr_bytes
                            last_t = now_t
                            target_total = max(total_mb, int(cur_mb * 1.02))
                            cur_p = min(88, max(4, int((cur_mb / target_total) * 88)))
                            rem_sec = max(1, int((target_total - cur_mb) / max(0.5, spd)))
                            eta_t = f"~{rem_sec} сек" if rem_sec < 60 else f"~{rem_sec // 60} мин {rem_sec % 60} сек"
                            stage_name = "Реконструкция и проверка весов" if cur_mb >= target_total * 0.98 else "Скачивание весов GGUF"
                            if self.load_progress.get("status") == "loading":
                                self.load_progress.update({
                                    "percent": cur_p,
                                    "curr_mb": cur_mb,
                                    "total_mb": target_total,
                                    "speed_mb": spd,
                                    "eta_seconds": rem_sec,
                                    "eta_text": eta_t,
                                    "stage": stage_name,
                                    "message": f"{stage_name}: {cur_mb} из {target_total} МБ ({cur_p}%) | {spd} МБ/с | Осталось: {eta_t}"
                                })
                    threading.Thread(target=_ticker, daemon=True).start()
                    try:
                        sel_path = gguf_mod.download_gguf(repo_id, sel)
                    finally:
                        stop_ticker = True
                    if self._cancel_load or seq_id != self._load_seq:
                        return
            def _runtime_progress(got, total, speed):
                if not total:
                    if self.load_progress.get("status") == "loading":
                        self.load_progress.update({"stage": "Подготовка движка llama.cpp", "message": "Загружаю llama-server..."})
                    return
                mb_g = round(got / (1024 * 1024), 1)
                mb_t = round(total / (1024 * 1024), 1)
                spd = round(speed / (1024 * 1024), 1) if speed else 0
                pct = min(94, 90 + int((got / total) * 4))
                if self.load_progress.get("status") == "loading":
                    self.load_progress.update({
                        "percent": pct,
                        "curr_mb": mb_g,
                        "total_mb": mb_t,
                        "speed_mb": spd,
                        "stage": "Подготовка движка llama.cpp",
                        "message": f"Движок llama.cpp: {mb_g} из {mb_t} МБ ({spd} МБ/с)..."
                    })
            self.load_progress = {
                "status": "loading",
                "model_id": model_id,
                "percent": 90 if self.load_progress.get("percent", 0) < 90 else self.load_progress.get("percent", 90),
                "stage": "Подготовка движка llama.cpp",
                "curr_mb": total_mb,
                "total_mb": total_mb,
                "speed_mb": 0,
                "eta_seconds": 0,
                "eta_text": "Запуск...",
                "message": "Подготовка движка llama.cpp (~50 МБ, один раз)..."
            }
            server_path = gguf_mod.ensure_llama_server(progress_cb=_runtime_progress)
            if self._cancel_load or seq_id != self._load_seq:
                return
            ngl = gguf_mod.compute_ngl(fit.get("verdict"), fit.get("ngl", 0))
            if _lan_pooled():
                ngl = 999
            threads = gguf_mod.default_threads()
            ngl_desc = "все слои на GPU" if ngl >= 999 else f"{ngl} слоёв на GPU"
            def _wait_beat(elapsed, timeout):
                if self.load_progress.get("status") != "loading":
                    return
                pct = min(99, 94 + int((elapsed / max(1.0, timeout)) * 5))
                rem = max(1, int(timeout - elapsed))
                eta_t = f"~{rem} сек"
                self.load_progress.update({
                    "percent": pct,
                    "eta_seconds": rem,
                    "eta_text": eta_t,
                    "stage": "Запуск инференс-процесса",
                    "message": f"Загружаю GGUF в память ({'VRAM' if ngl > 0 else 'RAM'}, {ngl_desc})... Осталось: {eta_t}"
                })
            try:
                backend = self._gguf()
                if backend is None:
                    raise RuntimeError("GGUF backend недоступен в этом окружении.")
                start_kw = {}
                try:
                    if _lan_pooled():
                        rpc = _lan_mod.LAN.rpc_targets()
                        if rpc:
                            start_kw["rpc_servers"] = rpc["servers"]
                            start_kw["tensor_split"] = rpc["split"]
                except Exception:
                    start_kw = {}
                wait_t = 360 if start_kw.get("rpc_servers") else 180
                backend.start(sel_path, int(fit.get("ctx_tokens") or 4096), ngl, threads, model_id,
                              server_path=server_path, wait_timeout=wait_t, on_wait=_wait_beat,
                              **start_kw)
            except RuntimeError as e:
                if self._cancel_load or seq_id != self._load_seq:
                    _stop_backend()
                    return
                params_b = fit.get("params_b") or 0
                if params_b <= 1.2 and TORCH_AVAILABLE:
                    self._run_loader(model_id, seq_id)
                    return
                _fail("Ошибка запуска GGUF", f"Движок llama.cpp не запустился: {str(e)[:140]}")
                return
            if self._cancel_load or seq_id != self._load_seq:
                _stop_backend()
                return
            rec_row = None
            for r in fit.get("quants", []):
                if r.get("quant") == fit.get("recommended"):
                    rec_row = r
                    break
            layers_total = int(fit.get("layers") or 0)
            layers_vram_final = layers_total if ngl >= 999 else min(layers_total, max(0, ngl))
            self.model = None
            self.tokenizer = None
            self.current_loaded_id = model_id
            self.active_model_id = model_id
            self.status = "ready"
            pres = self._verdict_presentation(fit.get("verdict"))
            bench_us = self.native_core.benchmark_avx2(1000)
            self.loaded_info = {
                "model_id": model_id,
                "tier": pres[0],
                "tier_label": pres[1],
                "color": pres[2],
                "mode": f"GGUF (llama.cpp), {'полностью на GPU' if ngl >= 999 else ngl_desc}",
                "speed_est": fit.get("tok_s_est") or 0,
                "vram_alloc_mb": int(round((rec_row or {}).get("vram_gb", 0) * 1024)),
                "ram_alloc_mb": int(round((rec_row or {}).get("ram_gb", 0) * 1024)),
                "total_layers": layers_total,
                "layers_vram": layers_vram_final,
                "layers_ram": max(0, layers_total - layers_vram_final),
                "layers_nvme": 0,
                "native_avx2_us": bench_us,
                "backend": "gguf",
                "quant": sel["quant"],
                "fit": {
                    "params_b": fit.get("params_b"),
                    "params_source": fit.get("params_source"),
                    "verdict": fit.get("verdict"),
                    "quant": fit.get("recommended"),
                    "est_tok_s": fit.get("tok_s_est"),
                    "backend": "gguf",
                    "gguf_repo": repo_id
                },
                "loaded_at": time.time()
            }
            self.load_progress = {
                "status": "ready",
                "model_id": model_id,
                "percent": 100,
                "stage": "Готова к работе",
                "curr_mb": total_mb,
                "total_mb": total_mb,
                "speed_mb": 0,
                "eta_seconds": 0,
                "eta_text": "Готово",
                "message": f"Модель {model_id} активна и готова к работе (GGUF {sel['quant']})."
            }
            try:
                _storage.atomic_write_json(
                    self.active_model_file,
                    {"model_id": model_id, "updated_at": time.time()},
                    fsync=True,
                    backup=True,
                )
            except Exception:
                pass
        except Exception as e:
            if seq_id != self._load_seq:
                return
            _fail("Ошибка загрузки GGUF", f"Сбой загрузки: {str(e)[:140]}")
    def load_model(self, model_id):
        raw_mid = str(model_id or "").strip()
        if not raw_mid or raw_mid.startswith("\\\\") or raw_mid.startswith("//"):
            raise ValueError("Недопустимый идентификатор или UNC-путь модели")
        def _norm_mid(x):
            return re.sub(r"(?i)[-_]?(instruct)?[-_]?gguf$", "", str(x or "").split("/")[-1].lower())
        m_norm = _norm_mid(model_id)
        if self.status == "ready":
            if self.model is not None and (_norm_mid(self.current_loaded_id) == m_norm or self.current_loaded_id == model_id):
                return self.loaded_info
            if self.gguf is not None and self.gguf.is_running() and (_norm_mid(self.gguf.model_id) == m_norm or _norm_mid(self.current_loaded_id) == m_norm or self.gguf.model_id == model_id):
                return self.loaded_info
        if self.status == "loading" and (_norm_mid(self.active_model_id) == m_norm or self.active_model_id == model_id):
            return self.loaded_info
        self.active_model_id = model_id
        hw = _hardware.get_hardware_profile()
        is_local_gguf = os.path.isfile(str(model_id or "")) and str(model_id).lower().endswith(".gguf")
        is_local_dir = os.path.isdir(str(model_id or ""))
        loc_gguf = None
        if is_local_gguf:
            sz_gb = round(os.path.getsize(model_id) / (1024 ** 3), 2)
            q_name = (gguf_mod.parse_quant_from_name(model_id) if gguf_mod else None) or "CUSTOM"
            loc_gguf = {"path": model_id, "size_bytes": os.path.getsize(model_id), "quant": q_name}
        elif gguf_mod is not None:
            loc_gguf = gguf_mod.find_local_gguf(model_id)
            if loc_gguf:
                sz_gb = round(loc_gguf["size_bytes"] / (1024 ** 3), 2)
                q_name = loc_gguf.get("quant") or "CUSTOM"
        if loc_gguf:
            vram_ok = hw.get("cuda_available") and sz_gb * 1024 <= max(0, hw.get("vram_free_mb", 0))
            verdict = "GPU" if vram_ok else "HYBRID"
            fit = {
                "verdict": verdict,
                "recommended": q_name,
                "layers": 32,
                "ctx_tokens": 4096,
                "ngl": 999 if vram_ok else 0,
                "params_b": round(max(0.5, sz_gb * 1.3), 2),
                "params_source": "local_gguf",
                "torch_gpu_ok": False,
                "tok_s_est": 25.0 if vram_ok else 8.0,
                "quants": [{"quant": q_name, "ngl": 999 if vram_ok else 0, "vram_gb": sz_gb if vram_ok else 0.0, "ram_gb": 0.0 if vram_ok else sz_gb}],
            }
        elif is_local_dir:
            fit = None
        else:
            fit = self._fit_for(model_id)
        if fit is not None and fit.get("verdict") == "IMPOSSIBLE" and not _lan_pool_fit_ok(fit):
            pres = self._verdict_presentation("IMPOSSIBLE")
            msg = fit.get("message_ru") or "Модель не подходит под ваше железо."
            mmap_note = fit.get("mmap_note") or ""
            self.loaded_info = {
                "model_id": model_id,
                "tier": pres[0],
                "tier_label": pres[1],
                "color": pres[2],
                "mode": "veto",
                "speed_est": 0,
                "vram_alloc_mb": 0,
                "ram_alloc_mb": 0,
                "total_layers": int(fit.get("layers") or 0),
                "layers_vram": 0,
                "layers_ram": int(fit.get("layers") or 0),
                "layers_nvme": 0,
                "fit": {
                    "params_b": fit.get("params_b"),
                    "params_source": fit.get("params_source"),
                    "verdict": "IMPOSSIBLE",
                    "quant": fit.get("recommended"),
                    "est_tok_s": 0.0,
                    "backend": "gguf"
                },
                "loaded_at": time.time()
            }
            self.status = "error"
            self.load_progress = {
                "status": "error",
                "model_id": model_id,
                "percent": 0,
                "stage": "Модель не подходит",
                "curr_mb": 0,
                "total_mb": 0,
                "speed_mb": 0,
                "eta_seconds": 0,
                "eta_text": "Ошибка",
                "message": f"{msg} {mmap_note}".strip()
            }
            return self.loaded_info
        hw = _hardware.get_hardware_profile()
        comp = _hardware.evaluate_compatibility(model_id, hw)
        total_layers = 32
        mid_low = model_id.lower()
        if "70b" in mid_low or "72b" in mid_low:
            total_layers = 80
        elif "30b" in mid_low or "32b" in mid_low:
            total_layers = 64
        elif "14b" in mid_low:
            total_layers = 48
        elif any(k in mid_low for k in ["1b", "1.5b", "1.7b", "2b", "3b", "4b"]):
            total_layers = 28
        elif any(k in mid_low for k in ["0.5b", "0.6b", "360m", "gpt2"]):
            total_layers = 12
        layers_vram = min(total_layers, 28 if comp["tier"] == "PERFECT" else (14 if hw["vram_total_mb"] >= 4000 else 8))
        layers_ram = total_layers - layers_vram
        fit_summary = None
        if fit is not None:
            pres = self._verdict_presentation(fit.get("verdict"))
            rec_quant = fit.get("recommended")
            rec_row = None
            for r in fit.get("quants", []):
                if r.get("quant") == rec_quant:
                    rec_row = r
                    break
            total_layers = int(fit.get("layers") or total_layers)
            verdict = fit.get("verdict")
            if verdict == "GPU":
                layers_vram = total_layers
                layers_ram = 0
            elif verdict == "HYBRID" and rec_row is not None:
                layers_vram = min(total_layers, max(0, int(rec_row.get("ngl", 0) or 0)))
                layers_ram = total_layers - layers_vram
            fit_summary = {
                "params_b": fit.get("params_b"),
                "params_source": fit.get("params_source"),
                "verdict": verdict,
                "quant": rec_quant,
                "est_tok_s": fit.get("tok_s_est"),
                "backend": "gguf"
            }
            if rec_row is not None and verdict in ("GPU", "HYBRID"):
                fit_summary["vram_gb"] = rec_row.get("vram_gb")
                fit_summary["ram_gb"] = rec_row.get("ram_gb")
            bench_us = self.native_core.benchmark_avx2(1000)
            self.loaded_info = {
                "model_id": model_id,
                "tier": pres[0],
                "tier_label": pres[1],
                "mode": f"GGUF (llama.cpp), квант {rec_quant}",
                "speed_est": fit.get("tok_s_est") or comp["speed_est"],
                "vram_alloc_mb": int(round((rec_row or {}).get("vram_gb", 0) * 1024)),
                "ram_alloc_mb": int(round((rec_row or {}).get("ram_gb", 0) * 1024)),
                "total_layers": total_layers,
                "layers_vram": layers_vram,
                "layers_ram": layers_ram,
                "layers_nvme": 0,
                "native_avx2_us": bench_us,
                "fit": fit_summary,
                "loaded_at": time.time()
            }
        else:
            bench_us = self.native_core.benchmark_avx2(1000)
            self.loaded_info = {
                "model_id": model_id,
                "tier": comp["tier"],
                "tier_label": comp["tier_label"],
                "mode": comp["mode"],
                "speed_est": comp["speed_est"],
                "vram_alloc_mb": comp["vram_alloc_mb"],
                "ram_alloc_mb": comp["ram_alloc_mb"],
                "total_layers": total_layers,
                "layers_vram": layers_vram,
                "layers_ram": layers_ram,
                "layers_nvme": 0,
                "native_avx2_us": bench_us,
                "loaded_at": time.time()
            }
        if self.gguf is not None and self.gguf.is_running() and self.gguf.model_id != model_id:
            try:
                self.gguf.stop()
            except Exception:
                pass
        if self.model is not None and self.current_loaded_id != model_id:
            self.model = None
            self.tokenizer = None
            self.current_loaded_id = None
            if TORCH_AVAILABLE and torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
            gc.collect()
        self._load_seq += 1
        seq_id = self._load_seq
        force_torch = os.environ.get("SHARD_FORCE_TORCH", "0") == "1"
        use_gguf = (not force_torch) and (loc_gguf is not None or (fit is not None and (not fit.get("torch_gpu_ok")))) and gguf_mod is not None
        if use_gguf:
            t = threading.Thread(target=self._run_loader_gguf, args=(model_id, seq_id, fit), daemon=True)
        else:
            t = threading.Thread(target=self._run_loader, args=(model_id, seq_id), daemon=True)
        t.start()
        return self.loaded_info
    def unload_model(self):
        self._cancel_load = True
        self._cancel_gen = True
        self._load_seq += 1
        if self.gguf is not None:
            try:
                self.gguf.stop()
            except Exception:
                pass
        self.current_loaded_id = None
        self.model = None
        self.tokenizer = None
        self.loaded_info = {}
        if TORCH_AVAILABLE and torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()
        self.status = "idle"
        self.load_progress = {"status": "idle", "percent": 0, "stage": "Выгружена", "eta_text": "", "speed_mb": 0, "message": "Модель выгружена, ОЗУ и VRAM освобождены."}
        if os.environ.get("SHARD_CLEAR_ACTIVE_ON_UNLOAD") == "1":
            try:
                _storage.atomic_write_json(
                    self.active_model_file,
                    {"model_id": "", "unloaded_at": time.time()},
                    fsync=False,
                    backup=True,
                )
            except Exception:
                pass
        return {"status": "unloaded"}
    def _build_prompt(self, messages):
        if self.tokenizer is not None and hasattr(self.tokenizer, "apply_chat_template") and getattr(self.tokenizer, "chat_template", None):
            try:
                return self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            except Exception:
                pass
        parts = []
        for m in messages:
            role = m.get("role", "user")
            c = m.get("content", "")
            parts.append(f"<|im_start|>{role}\n{c}<|im_end|>")
        parts.append("<|im_start|>assistant\n")
        return "\n".join(parts)
    def generate_stream(self, messages, max_tokens=512, temperature=0.7):
        t0 = time.time()
        self._cancel_gen = False
        gguf_ready = self.gguf is not None and self.gguf.is_running()
        if self.model is None and not gguf_ready and self.status == "idle" and self.active_model_id and _is_model_cached_complete(self.active_model_id):
            self.load_model(self.active_model_id)
            wait_start = time.time()
            while self.status == "loading" and (time.time() - wait_start) < 25.0:
                time.sleep(0.15)
        if self.status == "loading" or self.load_progress.get("status") == "loading":
            pct = self.load_progress.get("percent", 0)
            eta = self.load_progress.get("eta_text", "~15 сек")
            short_id = (self.active_model_id or "модель").split("/")[-1]
            cur_mb = self.load_progress.get("curr_mb", 0)
            tot_mb = self.load_progress.get("total_mb", 0)
            spd_mb = self.load_progress.get("speed_mb", 0)
            ans = f"Модель {short_id} сейчас загружается ({pct}%). Осталось: {eta} ({cur_mb} / {tot_mb} МБ, {spd_mb} МБ/с). Дождитесь завершения индикатора сверху."
            yield {"type": "content", "token": ans, "done": False}
            yield {"type": "done", "token": "", "done": True, "telemetry": {"thinking_seconds": 0.0, "total_seconds": 0.05, "tokens_per_sec": 0.0}, "usage": {"prompt_tokens": 1, "completion_tokens": len(ans.split()), "total_tokens": len(ans.split()) + 1}}
            return
        if self.gguf is not None and self.gguf.is_running():
            yield from self._generate_gguf(messages, max_tokens, temperature)
            return
        if not (self.gguf is not None and self.gguf.is_running()) and (self.model is None or self.tokenizer is None):
            ans = "Модель еще не запущена в память. Откройте вкладку «Каталог Hugging Face» или «Подбор под Железо» и нажмите кнопку «Запустить» на выбранной модели."
            yield {"type": "content", "token": ans, "done": False}
            yield {"type": "done", "token": "", "done": True, "telemetry": {"thinking_seconds": 0.0, "total_seconds": 0.02, "tokens_per_sec": 0.0}, "usage": {"prompt_tokens": 1, "completion_tokens": len(ans.split()), "total_tokens": len(ans.split()) + 1}}
            return
        self.status = "generating"
        prompt = self._build_prompt(messages)
        think_duration = 0.0
        t_think_start = time.time()
        in_think_tag = False
        token_count = 0
        prompt_tokens = 1
        try:
            inputs = self.tokenizer(prompt, return_tensors="pt")
            prompt_tokens = int(inputs["input_ids"].shape[-1])
            try:
                dev = next(self.model.parameters()).device
                inputs = {k: v.to(dev) for k, v in inputs.items()}
            except Exception:
                pass
            streamer = TextIteratorStreamer(self.tokenizer, skip_prompt=True, skip_special_tokens=True, timeout=15.0)
            pad_id = self.tokenizer.pad_token_id if self.tokenizer.pad_token_id is not None else self.tokenizer.eos_token_id
            gen_kwargs = {
                "input_ids": inputs["input_ids"],
                "streamer": streamer,
                "max_new_tokens": max(16, min(int(max_tokens), 2048)),
                "pad_token_id": pad_id,
                "eos_token_id": self.tokenizer.eos_token_id,
                "use_cache": True,
                "do_sample": True if temperature > 0.05 else False,
                "repetition_penalty": 1.1,
                "stopping_criteria": StoppingCriteriaList([_ShardCancelCriteria(self)])
            }
            if "attention_mask" in inputs:
                gen_kwargs["attention_mask"] = inputs["attention_mask"]
            if temperature > 0.05:
                gen_kwargs["temperature"] = max(0.1, min(1.5, float(temperature)))
            def _runner():
                try:
                    with torch.inference_mode():
                        self.model.generate(**gen_kwargs)
                except Exception:
                    pass
                finally:
                    try:
                        streamer.end()
                    except Exception:
                        pass
            gen_thread = threading.Thread(target=_runner, daemon=True)
            gen_thread.start()
            for chunk in streamer:
                if self._cancel_gen:
                    break
                if not chunk:
                    continue
                if "<think>" in chunk:
                    in_think_tag = True
                    chunk = chunk.replace("<think>", "")
                if "</think>" in chunk:
                    parts_t = chunk.split("</think>", 1)
                    if parts_t[0]:
                        token_count += 1
                        self.total_tokens_generated += 1
                        yield {"type": "thought", "token": parts_t[0], "done": False}
                    in_think_tag = False
                    think_duration = round(time.time() - t_think_start, 2)
                    chunk = parts_t[1]
                if not chunk:
                    continue
                for stop_seq in ("\nuser:", "<|im_end|>", "<|endoftext|>"):
                    if stop_seq in chunk:
                        chunk = chunk.split(stop_seq)[0]
                        self._cancel_gen = True
                if chunk:
                    token_count += 1
                    self.total_tokens_generated += 1
                    yield {"type": "thought" if in_think_tag else "content", "token": chunk, "done": False}
            gen_thread.join(timeout=1.0)
        except Exception as e:
            yield {"type": "content", "token": f"\n[Ошибка инференса: {str(e)}]", "done": False}
            token_count = max(1, token_count)
        total_duration = round(time.time() - t0, 2)
        tokens_per_sec = round(token_count / max(0.05, total_duration), 1)
        self.last_latency_ms = int(total_duration * 1000)
        self.status = "ready" if self.model is not None else "idle"
        yield {
            "type": "done",
            "token": "",
            "done": True,
            "telemetry": {
                "thinking_seconds": think_duration,
                "total_seconds": total_duration,
                "tokens_per_sec": tokens_per_sec
            },
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": token_count,
                "total_tokens": prompt_tokens + token_count
            }
        }
    def _generate_gguf(self, messages, max_tokens=512, temperature=0.7):
        t0 = time.time()
        self.status = "generating"
        self._cancel_gen = False
        think_duration = 0.0
        t_think_start = time.time()
        in_think_tag = False
        token_count = 0
        prompt_tokens = 1
        usage = None
        try:
            for kind, payload in self.gguf.chat_stream(
                messages, int(max_tokens), float(temperature),
                cancel_check=lambda: self._cancel_gen
            ):
                chunk = payload or ""
                if kind == "delta":
                    if self._cancel_gen:
                        break
                    if not chunk:
                        continue
                    if "\u003cthink\u003e" in chunk:
                        in_think_tag = True
                        chunk = chunk.replace("\u003cthink\u003e", "")
                    if "\u003c/think\u003e" in chunk:
                        parts_t = chunk.split("\u003c/think\u003e", 1)
                        if parts_t[0]:
                            token_count += 1
                            self.total_tokens_generated += 1
                            yield {"type": "thought", "token": parts_t[0], "done": False}
                        in_think_tag = False
                        think_duration = round(time.time() - t_think_start, 2)
                        chunk = parts_t[1]
                    if not chunk:
                        continue
                    for stop_seq in ("\nuser:", "\u003c|im_end|\u003e", "\u003c|endoftext|\u003e"):
                        if stop_seq in chunk:
                            chunk = chunk.split(stop_seq)[0]
                            self._cancel_gen = True
                    if chunk:
                        token_count += 1
                        self.total_tokens_generated += 1
                        yield {"type": "thought" if in_think_tag else "content", "token": chunk, "done": False}
                elif kind == "error":
                    yield {"type": "content", "token": f"[Ошибка инференса: {chunk}]", "done": False}
                    break
                elif kind == "usage":
                    usage = payload if isinstance(payload, dict) else None
        except Exception as e:
            token_count = max(1, token_count)
            yield {"type": "content", "token": f"\n[Ошибка инференса: {str(e)}]", "done": False}
        total_duration = round(time.time() - t0, 2)
        tokens_per_sec = round(token_count / max(0.05, total_duration), 1)
        self.last_latency_ms = int(total_duration * 1000)
        self.status = "ready" if (self.model is not None or (self.gguf is not None and self.gguf.is_running())) else "idle"
        if usage is None:
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": token_count,
                "total_tokens": prompt_tokens + token_count
            }
        yield {
            "type": "done",
            "token": "",
            "done": True,
            "telemetry": {
                "thinking_seconds": think_duration,
                "total_seconds": total_duration,
                "tokens_per_sec": tokens_per_sec
            },
            "usage": usage
        }
    def generate_sync(self, messages, max_tokens=512, temperature=0.7):
        thought_chunks = []
        content_chunks = []
        usage = {}
        telemetry = {}
        for item in self.generate_stream(messages, max_tokens, temperature):
            if item.get("type") == "thought":
                thought_chunks.append(item["token"])
            elif item.get("type") == "content":
                content_chunks.append(item["token"])
            elif item.get("type") == "done":
                usage = item.get("usage", {})
                telemetry = item.get("telemetry", {})
        return {
            "content": "".join(content_chunks),
            "reasoning_content": "".join(thought_chunks),
            "usage": usage,
            "telemetry": telemetry
        }
engine_instance = ShardEngine()