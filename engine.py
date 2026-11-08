import gc
import json
import os
import sys
import time
import threading
if "HF_HOME" not in os.environ:
    if os.path.exists("/data/models") or os.environ.get("SHARD_MODELS_DIR"):
        os.environ["HF_HOME"] = os.environ.get("SHARD_MODELS_DIR", "/data/models")
        os.environ["TRANSFORMERS_CACHE"] = os.environ["HF_HOME"]
    else:
        h_dir = os.path.join(os.path.expanduser("~"), ".shard", "models")
        os.makedirs(h_dir, exist_ok=True)
        os.environ["HF_HOME"] = h_dir
        os.environ["TRANSFORMERS_CACHE"] = h_dir
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
from hardware import evaluate_compatibility, get_hardware_profile, estimate_model_params
from shard_bridge import shard_native
if os.path.exists(r"C:\Users\avelo\Desktop\airllm\air_llm"):
    sys.path.append(r"C:\Users\avelo\Desktop\airllm\air_llm")
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
def _is_model_cached_complete(model_id):
    if not model_id:
        return False
    repo = model_id if "/" in model_id else f"Qwen/{model_id}"
    hub_dir = os.path.join(os.environ.get("HF_HOME", "/data/models"), "hub", f"models--{repo.replace('/', '--')}")
    if not os.path.exists(hub_dir):
        return False
    blobs_dir = os.path.join(hub_dir, "blobs")
    if os.path.exists(blobs_dir):
        for fn in os.listdir(blobs_dir):
            if fn.endswith(".incomplete"):
                return False
    snap_dir = os.path.join(hub_dir, "snapshots")
    if not os.path.exists(snap_dir):
        return False
    for root, _, files in os.walk(snap_dir):
        for fn in files:
            if fn.endswith((".safetensors", ".bin", ".gguf")):
                return True
    return False
class ShardEngine:
    def __init__(self):
        global _active_engine
        _active_engine = self
        data_dir = os.environ.get("SHARD_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".shard")
        os.makedirs(data_dir, exist_ok=True)
        self.active_model_file = os.path.join(data_dir, "active_model.json")
        saved_model = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
        if os.path.exists(self.active_model_file):
            try:
                with open(self.active_model_file, "r", encoding="utf-8") as f:
                    saved_model = json.load(f).get("model_id", saved_model)
            except Exception:
                pass
        self.active_model_id = saved_model
        self.status = "idle"
        self.loaded_info = {}
        self.total_tokens_generated = 0
        self.last_latency_ms = 0
        self.native_core = shard_native
        self.model = None
        self.tokenizer = None
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
            self.load_progress = {
                "status": "loading",
                "model_id": self.active_model_id,
                "percent": pct,
                "stage": "Размещение тензоров в памяти",
                "curr_mb": 0,
                "total_mb": 0,
                "speed_mb": 0,
                "eta_seconds": eta_sec,
                "eta_text": eta_text,
                "message": f"Инициализация слоев ({curr}/{total}) в памяти | Осталось: {eta_text}"
            }
    def get_status(self):
        hw = get_hardware_profile()
        return {
            "status": self.status,
            "active_model": self.active_model_id,
            "current_loaded": self.current_loaded_id,
            "is_loaded": self.model is not None,
            "load_progress": self.load_progress,
            "loaded_info": self.loaded_info,
            "hardware": hw,
            "native_engine": {
                "active": self.native_core.is_native_active,
                "version": self.native_core.version
            },
            "total_tokens_generated": self.total_tokens_generated,
            "last_latency_ms": self.last_latency_ms
        }
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
            self.load_progress = {"status": "error", "model_id": model_id, "percent": 0, "stage": "Ошибка", "eta_text": "Ошибка", "speed_mb": 0, "message": "PyTorch не найден в окружении Python."}
            return
        target_repo = model_id if "/" in model_id else f"Qwen/{model_id}"
        params_b = estimate_model_params(model_id)
        already_cached = _is_model_cached_complete(target_repo)
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
            hub_dir = os.path.join(os.environ.get("HF_HOME", "/data/models"), "hub", f"models--{target_repo.replace('/', '--')}", "blobs")
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
            hw = get_hardware_profile()
            chosen_dtype, dev_target = self._select_dtype_and_device(params_b, hw)
            tok = AutoTokenizer.from_pretrained(target_repo, trust_remote_code=True)
            if self._cancel_load or seq_id != self._load_seq:
                stop_ticker = True
                return
            load_kw = {"low_cpu_mem_usage": True, "trust_remote_code": True}
            if chosen_dtype is not None:
                load_kw["dtype"] = chosen_dtype
            is_heavy = params_b >= 12.0
            mdl = None
            if is_heavy:
                try:
                    from airllm import AutoModel as AirAutoModel
                    mdl = AirAutoModel.from_pretrained(target_repo)
                except Exception:
                    mdl = None
            if mdl is None:
                if dev_target == "auto":
                    try:
                        mdl = AutoModelForCausalLM.from_pretrained(target_repo, device_map="auto", **load_kw)
                    except Exception:
                        mdl = AutoModelForCausalLM.from_pretrained(target_repo, **load_kw)
                else:
                    mdl = AutoModelForCausalLM.from_pretrained(target_repo, **load_kw)
                    if dev_target == "cuda" and torch.cuda.is_available():
                        mdl = mdl.to("cuda")
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
                with open(self.active_model_file, "w", encoding="utf-8") as f:
                    json.dump({"model_id": model_id}, f, ensure_ascii=False)
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
    def load_model(self, model_id):
        if self.model is not None and self.current_loaded_id == model_id and self.status == "ready":
            return self.loaded_info
        self.active_model_id = model_id
        hw = get_hardware_profile()
        comp = evaluate_compatibility(model_id, hw)
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
        if self.model is not None and self.current_loaded_id != model_id:
            self.model = None
            self.tokenizer = None
            self.current_loaded_id = None
            if TORCH_AVAILABLE and torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
            gc.collect()
        self._load_seq += 1
        seq_id = self._load_seq
        t = threading.Thread(target=self._run_loader, args=(model_id, seq_id), daemon=True)
        t.start()
        return self.loaded_info
    def unload_model(self):
        self._cancel_load = True
        self._cancel_gen = True
        self._load_seq += 1
        self.current_loaded_id = None
        self.model = None
        self.tokenizer = None
        self.loaded_info = {}
        if TORCH_AVAILABLE and torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()
        self.status = "idle"
        self.load_progress = {"status": "idle", "percent": 0, "stage": "Выгружена", "eta_text": "", "speed_mb": 0, "message": "Модель выгружена, ОЗУ и VRAM освобождены."}
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
        if self.model is None and self.status == "idle" and self.active_model_id and _is_model_cached_complete(self.active_model_id):
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
        if self.model is None or self.tokenizer is None:
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
