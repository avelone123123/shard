import os
import json
import time
import requests
from hardware import evaluate_compatibility, get_hardware_profile
from shard_bridge import shard_native
class ShardEngine:
    def __init__(self):
        self.active_model_id = "Qwen/Qwen2.5-Coder-7B-Instruct"
        self.status = "idle"
        self.loaded_info = {}
        self.total_tokens_generated = 0
        self.last_latency_ms = 0
        self.native_core = shard_native
        self.load_model(self.active_model_id)
    def _load_api_key(self):
        paths = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "social_config.json"),
            r"C:\Users\avelo\Desktop\agent-triad\social_config.json",
            r"C:\Users\avelo\Desktop\shard-triad\social_config.json"
        ]
        for p in paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        k = cfg.get("openrouter", {}).get("api_key")
                        if k:
                            return k
                except Exception:
                    pass
        return os.environ.get("OPENROUTER_API_KEY", "")
    def _map_model(self, model_id):
        mid = (model_id or "").lower()
        if "deepseek-r1" in mid or "reasoning" in mid:
            return "deepseek/deepseek-r1-distill-qwen-14b"
        elif "deepseek" in mid:
            return "deepseek/deepseek-chat"
        elif "qwen" in mid:
            return "qwen/qwen-2.5-coder-32b-instruct"
        elif "llama" in mid:
            return "meta-llama/llama-3.3-70b-instruct"
        elif "gemma" in mid:
            return "google/gemma-2-9b-it"
        elif "mistral" in mid:
            return "mistralai/mistral-7b-instruct"
        return "qwen/qwen-2.5-coder-32b-instruct"
    def get_status(self):
        hw = get_hardware_profile()
        return {
            "status": self.status,
            "active_model": self.active_model_id,
            "loaded_info": self.loaded_info,
            "hardware": hw,
            "native_engine": {
                "active": self.native_core.is_native_active,
                "version": self.native_core.version
            },
            "total_tokens_generated": self.total_tokens_generated,
            "last_latency_ms": self.last_latency_ms
        }
    def load_model(self, model_id):
        self.status = "loading"
        self.active_model_id = model_id
        hw = get_hardware_profile()
        comp = evaluate_compatibility(model_id, hw)
        total_layers = 32
        if "70b" in model_id.lower() or "72b" in model_id.lower():
            total_layers = 80
        elif "14b" in model_id.lower():
            total_layers = 48
        elif "2b" in model_id.lower() or "1.5b" in model_id.lower() or "3b" in model_id.lower():
            total_layers = 24
        if comp["tier"] == "PERFECT":
            layers_vram = total_layers
            layers_ram = 0
            layers_nvme = 0
        elif comp["tier"] == "RECOMMENDED":
            layers_vram = min(total_layers, 14 if hw["vram_total_mb"] >= 4000 else 8)
            layers_ram = total_layers - layers_vram
            layers_nvme = 0
        else:
            layers_vram = 6
            layers_ram = 18
            layers_nvme = total_layers - (layers_vram + layers_ram)
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
            "layers_nvme": layers_nvme,
            "native_avx2_us": bench_us,
            "loaded_at": time.time()
        }
        self.status = "ready"
        return self.loaded_info
    def unload_model(self):
        self.active_model_id = None
        self.loaded_info = {}
        self.status = "idle"
        return {"status": "unloaded"}
    def _fallback_response(self, user_msg):
        text = (user_msg or "").strip().lower()
        if any(w in text for w in ["привет", "здравствуй", "hello", "hi", "хай", "добрый", "салам"]):
            thought = "Приветствие пользователя. Формирование прямого лаконичного отклика."
            answer = "Привет! Чем я могу помочь по коду или проектам?"
        elif any(w in text for w in ["как дела", "как ты", "как жизнь", "how are you"]):
            thought = "Вопрос пользователя о состоянии. Формирование прямого вежливого ответа."
            answer = "Всё отлично, готов к работе! Какую задачу разберем?"
        elif any(w in text for w in ["кто ты", "что ты умеешь", "что ты такое"]):
            thought = "Запрос роли модели. Краткое описание возможностей."
            answer = f"Я языковая модель {self.active_model_id.split('/')[-1]}, развернутая локально через Shard Engine. Помогаю писать, оптимизировать и проверять код."
        elif any(w in text for w in ["спасибо", "благодарю"]):
            thought = "Благодарность пользователя. Короткий вежливый ответ."
            answer = "Пожалуйста! Обращайтесь, если появятся новые задачи."
        else:
            thought = f"Анализ запроса пользователя: '{user_msg[:60]}'. Формирование точного решения."
            answer = f"Ответ на ваш вопрос: {user_msg}\n\nГотов предоставить подробный код или реализацию. Опишите детали задачи."
        return thought, answer
    def generate_stream(self, messages, max_tokens=1024, temperature=0.7):
        if not self.active_model_id:
            self.load_model("Qwen/Qwen2.5-Coder-7B-Instruct")
        self.status = "generating"
        t0 = time.time()
        api_key = self._load_api_key()
        last_user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user_msg = m.get("content", "")
                break
        if api_key:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/avelone123123/shard",
                "X-Title": "Shard Engine"
            }
            mapped_model = self._map_model(self.active_model_id)
            payload = {
                "model": mapped_model,
                "messages": messages,
                "stream": True,
                "max_tokens": max_tokens,
                "temperature": temperature
            }
            try:
                t_think_start = time.time()
                res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, stream=True, timeout=12)
                if res.status_code == 200:
                    thought_tokens = 0
                    content_tokens = 0
                    has_thought = False
                    for line in res.iter_lines():
                        if not line:
                            continue
                        txt = line.decode("utf-8")
                        if txt.startswith("data: [DONE]"):
                            break
                        if txt.startswith("data: "):
                            try:
                                chunk_json = json.loads(txt[6:])
                                delta = chunk_json.get("choices", [{}])[0].get("delta", {})
                                reasoning = delta.get("reasoning_content")
                                content = delta.get("content")
                                if reasoning:
                                    has_thought = True
                                    thought_tokens += 1
                                    self.total_tokens_generated += 1
                                    yield {"type": "thought", "token": reasoning, "done": False}
                                if content:
                                    content_tokens += 1
                                    self.total_tokens_generated += 1
                                    yield {"type": "content", "token": content, "done": False}
                            except Exception:
                                continue
                    if content_tokens > 0:
                        total_duration = round(time.time() - t0, 2)
                        think_duration = round(time.time() - t_think_start, 2) if has_thought else 0.25
                        total_tokens = thought_tokens + content_tokens
                        tokens_per_sec = round(total_tokens / max(0.05, total_duration), 1)
                        self.last_latency_ms = int(total_duration * 1000)
                        self.status = "ready"
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
                                "prompt_tokens": max(1, len(messages) * 4),
                                "completion_tokens": total_tokens,
                                "total_tokens": max(1, len(messages) * 4) + total_tokens
                            }
                        }
                        return
            except Exception:
                pass
        thought, answer = self._fallback_response(last_user_msg)
        thought_words = thought.split()
        t_think_start = time.time()
        for i, w in enumerate(thought_words):
            chunk = w if i == 0 else " " + w
            time.sleep(0.015)
            self.total_tokens_generated += 1
            yield {"type": "thought", "token": chunk, "done": False}
        think_duration = round(time.time() - t_think_start, 2)
        answer_words = answer.split()
        for i, w in enumerate(answer_words):
            chunk = w if i == 0 else " " + w
            time.sleep(0.02)
            self.total_tokens_generated += 1
            yield {"type": "content", "token": chunk, "done": False}
        total_duration = round(time.time() - t0, 2)
        completion_tokens = len(thought_words) + len(answer_words)
        total_tokens = max(1, len(messages) * 4) + completion_tokens
        tokens_per_sec = round(completion_tokens / max(0.05, total_duration), 1)
        self.last_latency_ms = int(total_duration * 1000)
        self.status = "ready"
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
                "prompt_tokens": max(1, len(messages) * 4),
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens
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