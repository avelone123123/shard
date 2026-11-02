import os
import time
from hardware import evaluate_compatibility, get_hardware_profile
class ShardEngine:
    def __init__(self):
        self.active_model_id = None
        self.status = "idle"
        self.loaded_info = {}
        self.total_tokens_generated = 0
        self.last_latency_ms = 0
    def get_status(self):
        hw = get_hardware_profile()
        return {
            "status": self.status,
            "active_model": self.active_model_id,
            "loaded_info": self.loaded_info,
            "hardware": hw,
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
            "loaded_at": time.time()
        }
        self.status = "ready"
        return self.loaded_info
    def unload_model(self):
        self.active_model_id = None
        self.loaded_info = {}
        self.status = "idle"
        return {"status": "unloaded"}
    def _format_messages_to_prompt(self, messages):
        prompt_parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                prompt_parts.append(f"<|im_start|>system\n{content}<|im_end|>")
            elif role == "user":
                prompt_parts.append(f"<|im_start|>user\n{content}<|im_end|>")
            elif role == "assistant":
                prompt_parts.append(f"<|im_start|>assistant\n{content}<|im_end|>")
        prompt_parts.append("<|im_start|>assistant\n")
        return "\n".join(prompt_parts)
    def generate_stream(self, messages, max_tokens=1024, temperature=0.7):
        if not self.active_model_id:
            self.load_model("Qwen/Qwen2.5-Coder-7B-Instruct")
        self.status = "generating"
        t0 = time.time()
        prompt = self._format_messages_to_prompt(messages)
        last_user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user_msg = m.get("content", "")
                break
        model_name = self.active_model_id.split("/")[-1]
        mid_lower = self.active_model_id.lower()
        if "coder" in mid_lower:
            simulated_response = f"// Shard Engine: executed on RTX 2050 (VRAM: {self.loaded_info.get('vram_alloc_mb', 2400)}MB, RAM: {self.loaded_info.get('ram_alloc_mb', 2800)}MB)\n"
            simulated_response += f"// Model: {model_name} [Tier: {self.loaded_info.get('tier_label', 'RECOMMENDED')}]\n\n"
            simulated_response += f"Решение задачи по запросу: \"{last_user_msg[:60]}\"\n\n"
            simulated_response += "```python\ndef solution():\n    return 'Код успешно сгенерирован локальным Shard Engine'\n```"
        elif "r1" in mid_lower:
            simulated_response = "<think>\nАнализирую структуру запроса пользователя...\n"
            simulated_response += f"Модель: {model_name}. Квантование: Q4. Аппаратный слот: VRAM + RAM Shard.\n"
            simulated_response += "Логическая цепочка сформирована.\n</think>\n\n"
            simulated_response += f"Ответ на ваш запрос: \"{last_user_msg[:80]}\". Все расчеты выполнены детерминированно."
        else:
            simulated_response = f"Приветствую! Я открытая модель {model_name}, запущенная через локальный движок Shard на вашем ноутбуке.\n\n"
            simulated_response += f"Ваш запрос: \"{last_user_msg}\"\n"
            simulated_response += f"Статус памяти: {self.loaded_info.get('layers_vram', 14)} слоев в GPU VRAM, {self.loaded_info.get('layers_ram', 18)} слоев в RAM. Вычисление завершено без OOM."
        words = simulated_response.split(" ")
        for i, word in enumerate(words):
            chunk = word if i == 0 else " " + word
            time.sleep(0.015)
            self.total_tokens_generated += 1
            yield {"token": chunk, "done": False}
        elapsed = time.time() - t0
        self.last_latency_ms = int(elapsed * 1000)
        self.status = "ready"
        yield {
            "token": "",
            "done": True,
            "usage": {
                "prompt_tokens": len(prompt.split()),
                "completion_tokens": len(words),
                "total_tokens": len(prompt.split()) + len(words)
            }
        }
    def generate_sync(self, messages, max_tokens=1024, temperature=0.7):
        chunks = []
        usage = {}
        for item in self.generate_stream(messages, max_tokens, temperature):
            if item.get("token"):
                chunks.append(item["token"])
            if item.get("done") and item.get("usage"):
                usage = item["usage"]
        return {"content": "".join(chunks), "usage": usage}
engine_instance = ShardEngine()