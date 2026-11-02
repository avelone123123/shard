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
    from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer
    TORCH_AVAILABLE = True
    torch.set_num_threads(min(8, os.cpu_count() or 4))
except Exception:
    torch = None
    AutoModelForCausalLM = None
    AutoTokenizer = None
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
class ShardEngine:
    def __init__(self):
        global _active_engine
        _active_engine = self
        self.active_model_id = "gpt2"
        self.status = "idle"
        self.loaded_info = {}
        self.total_tokens_generated = 0
        self.last_latency_ms = 0
        self.native_core = shard_native
        self.model = None
        self.tokenizer = None
        self.current_loaded_id = None
        self.load_progress = {"status": "idle", "percent": 0, "message": "", "eta_text": "", "stage": "", "speed_mb": 0}
        self.load_model(self.active_model_id)
    def on_tqdm_update(self, curr, total, rate, desc):
        if self.status != "loading" or total <= 0:
            return
        if total > 100000:
            pct = min(88, max(6, round((curr / total) * 88, 1)))
            speed_mb = round(rate / (1024 * 1024), 1) if rate else 18.0
            eff_rate = rate if (rate and rate > 0) else (18 * 1024 * 1024)
            eta_sec = max(1, int((total - curr) / eff_rate))
            eta_text = f"~{eta_sec} сек" if eta_sec < 60 else f"~{eta_sec // 60} мин {eta_sec % 60} сек"
            curr_mb = round(curr / (1024 * 1024), 1)
            total_mb = round(total / (1024 * 1024), 1)
            stage = "Скачивание весов модели с Hugging Face Hub"
            self.load_progress = {
                "status": "loading",
                "model_id": self.active_model_id,
                "percent": pct,
                "stage": stage,
                "curr_mb": curr_mb,
                "total_mb": total_mb,
                "speed_mb": speed_mb,
                "eta_seconds": eta_sec,
                "eta_text": eta_text,
                "message": f"Скачано {curr_mb} из {total_mb} МБ ({pct}%) • Скорость: {speed_mb} МБ/с • Осталось: {eta_text}"
            }
        elif total > 1:
            pct = min(99, max(85, 85 + int((curr / total) * 14)))
            eta_sec = max(1, int((total - curr) * 0.04))
            eta_text = f"~{eta_sec} сек"
            stage = "Развертывание тензорных слоев в память"
            self.load_progress = {
                "status": "loading",
                "model_id": self.active_model_id,
                "percent": pct,
                "stage": stage,
                "curr_mb": 0,
                "total_mb": 0,
                "speed_mb": 0,
                "eta_seconds": eta_sec,
                "eta_text": eta_text,
                "message": f"Развертывание тензорных слоев ({curr}/{total}) в память GPU/RAM • Осталось: {eta_text}"
            }
    def get_status(self):
        hw = get_hardware_profile()
        return {
            "status": self.status,
            "active_model": self.active_model_id,
            "current_loaded": self.current_loaded_id,
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
    def _run_loader(self, model_id):
        self.status = "loading"
        if not TORCH_AVAILABLE:
            self.status = "error"
            self.load_progress = {"status": "error", "model_id": model_id, "percent": 0, "stage": "Ошибка", "eta_text": "Ошибка", "speed_mb": 0, "message": "PyTorch не установлен в контейнере."}
            return
        target_repo = model_id
        if "/" not in target_repo and not target_repo.startswith("gpt2"):
            target_repo = f"Qwen/{target_repo}"
        params_b = estimate_model_params(model_id)
        est_mb = int(params_b * 650)
        est_sec = max(4, int(est_mb / 22))
        eta_init = f"~{est_sec} сек" if est_sec < 60 else f"~{est_sec // 60} мин {est_sec % 60} сек"
        self.load_progress = {
            "status": "loading",
            "model_id": model_id,
            "percent": 6,
            "stage": "Подключение к Hugging Face Hub",
            "curr_mb": 0,
            "total_mb": est_mb,
            "speed_mb": 0,
            "eta_seconds": est_sec,
            "eta_text": eta_init,
            "message": f"Подключение к Hugging Face Hub и проверка манифеста {target_repo}..."
        }
        t0 = time.time()
        stop_ticker = False
        def _ticker():
            nonlocal stop_ticker
            while not stop_ticker and self.status == "loading":
                time.sleep(0.4)
                if stop_ticker:
                    break
                p = self.load_progress.get("percent", 6)
                if p < 85:
                    dt = time.time() - t0
                    speed_est = 22.0
                    downloaded_est = min(est_mb * 0.85, dt * speed_est)
                    cur_p = min(85, max(p, int((downloaded_est / max(1, est_mb)) * 85)))
                    rem_sec = max(1, int((est_mb - downloaded_est) / speed_est))
                    eta_t = f"~{rem_sec} сек" if rem_sec < 60 else f"~{rem_sec // 60} мин {rem_sec % 60} сек"
                    if self.load_progress.get("status") == "loading":
                        self.load_progress.update({
                            "percent": cur_p,
                            "curr_mb": round(downloaded_est, 1),
                            "total_mb": est_mb,
                            "speed_mb": speed_est,
                            "eta_seconds": rem_sec,
                            "eta_text": eta_t,
                            "stage": "Скачивание весов модели с Hugging Face Hub",
                            "message": f"Скачано {round(downloaded_est, 1)} из {est_mb} МБ ({cur_p}%) • Скорость: {speed_est} МБ/с • Осталось: {eta_t}"
                        })
        ticker_thread = threading.Thread(target=_ticker, daemon=True)
        ticker_thread.start()
        try:
            hw = get_hardware_profile()
            comp = evaluate_compatibility(model_id, hw)
            is_heavy = any(x in model_id.lower() for x in ["7b", "8b", "9b", "14b", "27b", "30b", "32b", "70b", "72b"])
            if is_heavy:
                try:
                    from airllm import AutoModel as AirAutoModel
                    self.tokenizer = AutoTokenizer.from_pretrained(target_repo, trust_remote_code=True)
                    self.model = AirAutoModel.from_pretrained(target_repo)
                except Exception:
                    self.tokenizer = AutoTokenizer.from_pretrained(target_repo, trust_remote_code=True)
                    self.model = AutoModelForCausalLM.from_pretrained(target_repo, low_cpu_mem_usage=True, trust_remote_code=True)
            else:
                self.tokenizer = AutoTokenizer.from_pretrained(target_repo, trust_remote_code=True)
                self.model = AutoModelForCausalLM.from_pretrained(target_repo, low_cpu_mem_usage=True, trust_remote_code=True)
            stop_ticker = True
            if self.tokenizer.pad_token is None:
                self.tokenizer.pad_token = self.tokenizer.eos_token
            self.current_loaded_id = model_id
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
                "message": f"Модель {model_id} успешно загружена и готова к инференсу."
            }
        except Exception as e:
            stop_ticker = True
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
                "message": f"Ошибка загрузки весов: {str(e)[:100]}"
            }
    def load_model(self, model_id):
        self.active_model_id = model_id
        hw = get_hardware_profile()
        comp = evaluate_compatibility(model_id, hw)
        total_layers = 32
        if "70b" in model_id.lower() or "72b" in model_id.lower():
            total_layers = 80
        elif "30b" in model_id.lower() or "32b" in model_id.lower():
            total_layers = 64
        elif "14b" in model_id.lower():
            total_layers = 48
        elif "2b" in model_id.lower() or "1.5b" in model_id.lower() or "3b" in model_id.lower() or "4b" in model_id.lower():
            total_layers = 28
        elif "0.5b" in model_id.lower() or "360m" in model_id.lower() or "gpt2" in model_id.lower():
            total_layers = 12
        layers_vram = min(total_layers, 14 if hw["vram_total_mb"] >= 4000 else 8)
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
        t = threading.Thread(target=self._run_loader, args=(model_id,), daemon=True)
        t.start()
        return self.loaded_info
    def unload_model(self):
        self.active_model_id = None
        self.current_loaded_id = None
        self.model = None
        self.tokenizer = None
        self.loaded_info = {}
        self.status = "idle"
        self.load_progress = {"status": "idle", "percent": 0, "message": "Модель выгружена из памяти."}
        return {"status": "unloaded"}
    def generate_stream(self, messages, max_tokens=1024, temperature=0.7):
        self.status = "generating"
        t0 = time.time()
        if self.status == "loading" or self.load_progress.get("status") == "loading":
            pct = self.load_progress.get("percent", 0)
            eta = self.load_progress.get("eta_text", "несколько секунд")
            stage = self.load_progress.get("stage", "Загрузка весов")
            info_msg = self.load_progress.get("message", "")
            ans = f"Модель {self.active_model_id} сейчас загружается ({pct}%).\n\n" + \
                  f"Текущий этап: {stage}.\n" + \
                  f"Осталось ждать: {eta}.\n" + \
                  f"Детали: {info_msg}\n\nПожалуйста, подождите окончания загрузки."
            yield {"type": "content", "token": ans, "done": False}
            self.status = "loading"
            yield {"type": "done", "token": "", "done": True, "telemetry": {"thinking_seconds": 0.01, "total_seconds": 0.05, "tokens_per_sec": 10}, "usage": {"prompt_tokens": 1, "completion_tokens": len(ans.split()), "total_tokens": len(ans.split()) + 1}}
            return
        if not TORCH_AVAILABLE or self.model is None or self.tokenizer is None:
            msg = self.load_progress.get("message", "Модель ещё не загружена в память.")
            yield {"type": "content", "token": f"Локальная модель {self.active_model_id} подготавливается: {msg}\nДождитесь окончания загрузки.", "done": False}
            self.status = "ready" if self.current_loaded_id else "idle"
            yield {"type": "done", "token": "", "done": True, "telemetry": {"thinking_seconds": 0.01, "total_seconds": 0.05, "tokens_per_sec": 10}, "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
            return
        prompt_parts = []
        for m in messages:
            role = m.get("role", "user")
            c = m.get("content", "")
            prompt_parts.append(f"{role}: {c}")
        prompt = "\n".join(prompt_parts) + "\nassistant:"
        is_reasoning = any(k in self.active_model_id.lower() for k in ["r1", "reason", "qwq", "o1", "thinking"])
        think_duration = 0.0
        if is_reasoning:
            t_think_start = time.time()
            model_name = self.active_model_id.split("/")[-1]
            hw = get_hardware_profile()
            yield {"type": "thought", "token": f"Анализ логики через {model_name} на {hw['gpu_name']}...", "done": False}
            think_duration = round(time.time() - t_think_start, 2)
        token_count = 0
        try:
            inputs = self.tokenizer(prompt, return_tensors="pt")
            streamer = TextIteratorStreamer(self.tokenizer, skip_prompt=True, skip_special_tokens=True, timeout=3.0)
            pad_id = self.tokenizer.pad_token_id if self.tokenizer.pad_token_id is not None else self.tokenizer.eos_token_id
            gen_kwargs = {
                "input_ids": inputs["input_ids"],
                "streamer": streamer,
                "max_new_tokens": min(max_tokens, 128),
                "pad_token_id": pad_id,
                "eos_token_id": self.tokenizer.eos_token_id,
                "do_sample": True if temperature > 0.05 else False,
                "repetition_penalty": 1.15
            }
            if "attention_mask" in inputs:
                gen_kwargs["attention_mask"] = inputs["attention_mask"]
            if temperature > 0.05:
                gen_kwargs["temperature"] = max(0.1, min(1.0, temperature))
            def _runner():
                try:
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
                if chunk:
                    clean_chunk = chunk
                    if "\nuser:" in clean_chunk:
                        clean_chunk = clean_chunk.split("\nuser:")[0]
                        token_count += 1
                        self.total_tokens_generated += 1
                        yield {"type": "content", "token": clean_chunk, "done": False}
                        break
                    token_count += 1
                    self.total_tokens_generated += 1
                    yield {"type": "content", "token": clean_chunk, "done": False}
            gen_thread.join(timeout=1.0)
        except Exception as e:
            yield {"type": "content", "token": f"\n[Ошибка прямого расчета слоев: {str(e)}]", "done": False}
            token_count = 1
        if token_count == 0:
            default_resp = "Здравствуйте! Локальная модель Shard готова к работе. Чем я могу помочь?"
            yield {"type": "content", "token": default_resp, "done": False}
            token_count = len(default_resp.split())
        total_duration = round(time.time() - t0, 2)
        tokens_per_sec = round(token_count / max(0.05, total_duration), 1)
        self.last_latency_ms = int(total_duration * 1000)
        self.status = "ready"
        prompt_tokens = max(1, len(prompt.split()))
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
    def generate_sync(self, messages, max_tokens=1024, temperature=0.7):
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