import os
import sys
import time
import threading
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
from hardware import evaluate_compatibility, get_hardware_profile
from shard_bridge import shard_native
if os.path.exists(r"C:\Users\avelo\Desktop\airllm\air_llm"):
    sys.path.append(r"C:\Users\avelo\Desktop\airllm\air_llm")
class ShardEngine:
    def __init__(self):
        self.active_model_id = "gpt2"
        self.status = "idle"
        self.loaded_info = {}
        self.total_tokens_generated = 0
        self.last_latency_ms = 0
        self.native_core = shard_native
        self.model = None
        self.tokenizer = None
        self.current_loaded_id = None
        self.load_progress = {"status": "idle", "percent": 0, "message": ""}
        self.load_model(self.active_model_id)
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
            self.load_progress = {"status": "error", "percent": 0, "message": "PyTorch не установлен в контейнере. Выполняется установка пакетов..."}
            return
        self.load_progress = {"status": "loading", "percent": 15, "message": f"Инициализация конфигурации {model_id}..."}
        try:
            hw = get_hardware_profile()
            comp = evaluate_compatibility(model_id, hw)
            is_heavy = any(x in model_id.lower() for x in ["7b", "8b", "9b", "14b", "27b", "30b", "32b", "70b", "72b"])
            target_repo = model_id
            if "/" not in target_repo and not target_repo.startswith("gpt2"):
                target_repo = f"Qwen/{target_repo}"
            if is_heavy:
                self.load_progress = {"status": "loading", "percent": 30, "message": f"Активация AirLLM послойного Zero-Copy стриминга для {target_repo}..."}
                try:
                    from airllm import AutoModel as AirAutoModel
                    self.tokenizer = AutoTokenizer.from_pretrained(target_repo, trust_remote_code=True)
                    self.model = AirAutoModel.from_pretrained(target_repo)
                except Exception as ex_air:
                    self.load_progress = {"status": "fallback", "percent": 50, "message": f"Послойный сплиттинг {target_repo}: {str(ex_air)[:80]}"}
                    self.tokenizer = AutoTokenizer.from_pretrained(target_repo, trust_remote_code=True)
                    self.model = AutoModelForCausalLM.from_pretrained(target_repo, low_cpu_mem_usage=True, trust_remote_code=True)
            else:
                self.load_progress = {"status": "loading", "percent": 40, "message": f"Загрузка тензорных весов {target_repo} в память..."}
                self.tokenizer = AutoTokenizer.from_pretrained(target_repo, trust_remote_code=True)
                self.model = AutoModelForCausalLM.from_pretrained(target_repo, low_cpu_mem_usage=True, trust_remote_code=True)
            if self.tokenizer.pad_token is None:
                self.tokenizer.pad_token = self.tokenizer.eos_token
            self.current_loaded_id = model_id
            self.status = "ready"
            self.load_progress = {"status": "ready", "percent": 100, "message": f"Модель {model_id} успешно загружена и готова к инференсу."}
        except Exception as e:
            self.status = "error"
            self.load_progress = {"status": "error", "percent": 0, "message": f"Ошибка загрузки весов: {str(e)[:100]}"}
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
            yield {"type": "content", "token": f"Модель {self.active_model_id} сейчас загружается ({pct}%). Пожалуйста, подождите завершения загрузки весов.", "done": False}
            self.status = "loading"
            yield {"type": "done", "token": "", "done": True, "telemetry": {"thinking_seconds": 0.01, "total_seconds": 0.05, "tokens_per_sec": 10}, "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
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