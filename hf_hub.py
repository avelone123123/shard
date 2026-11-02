import requests
from hardware import evaluate_compatibility, get_hardware_profile
CURATED_MODELS = [
    {"id": "Qwen/Qwen2.5-Coder-7B-Instruct", "category": "coding", "desc": "Лидер в кодинге для Cursor и OpenCode. Контекст 128k токенов."},
    {"id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B", "category": "reasoning", "desc": "Флагманская модель рассуждений с логикой DeepSeek-R1."},
    {"id": "google/gemma-2-9b-it", "category": "general", "desc": "Высокая эрудиция и стиль рассуждений от Google DeepMind."},
    {"id": "Qwen/Qwen2.5-Coder-1.5B-Instruct", "category": "coding", "desc": "Сверхлегкий кодер. Мгновенный отклик для автокомплита строк кода."},
    {"id": "deepseek-ai/DeepSeek-R1-Distill-Llama-8B", "category": "reasoning", "desc": "DeepSeek R1 рассуждения на базе архитектуры Llama-3.1."},
    {"id": "meta-llama/Llama-3.2-3B-Instruct", "category": "compact", "desc": "Компактная быстрая Llama от Meta. 100% помещается в VRAM ноута."},
    {"id": "microsoft/Phi-3.5-mini-instruct", "category": "compact", "desc": "Мини-модель Microsoft 3.8B параметров с качеством больших моделей."},
    {"id": "HuggingFaceTB/SmolLM2-1.7B-Instruct", "category": "compact", "desc": "Сверхбыстрая компактная модель Hugging Face для слабых ПК."},
    {"id": "mistralai/Mistral-7B-Instruct-v0.3", "category": "general", "desc": "Классический надежный универсал Mistral с поддержкой вызова функций."}
]
def fetch_model_metadata(model_id, hw=None):
    if hw is None:
        hw = get_hardware_profile()
    comp = evaluate_compatibility(model_id, hw)
    author = model_id.split("/")[0] if "/" in model_id else "Community"
    name = model_id.split("/")[-1] if "/" in model_id else model_id
    return {
        "id": model_id,
        "author": author,
        "name": name,
        "desc": f"Модель {name} от {author}.",
        "description": f"Модель {name} от {author}.",
        "compat_badge": comp["tier_label"],
        "speed_tier": comp["speed_est"],
        "memory_slot": comp["mode"],
        "downloads": 125000,
        "likes": 4200,
        "compatibility": comp
    }
def browse_models(query=None, category=None, limit=20, hw=None):
    if hw is None:
        hw = get_hardware_profile()
    if query:
        url = f"https://huggingface.co/api/models?search={query}&filter=text-generation&sort=downloads&direction=-1&limit={limit}"
        try:
            r = requests.get(url, timeout=6)
            if r.status_code == 200:
                data = r.json()
                results = []
                for item in data:
                    mid = item.get("id")
                    if not mid:
                        continue
                    author = mid.split("/")[0] if "/" in mid else "Community"
                    name = mid.split("/")[-1] if "/" in mid else mid
                    comp = evaluate_compatibility(mid, hw)
                    d_count = item.get("downloads", 0)
                    results.append({
                        "id": mid,
                        "author": author,
                        "name": name,
                        "category": "search",
                        "desc": f"Hugging Face открытая модель. Скачиваний: {d_count:,}",
                        "description": f"Hugging Face открытая модель. Скачиваний: {d_count:,}",
                        "compat_badge": comp["tier_label"],
                        "speed_tier": comp["speed_est"],
                        "memory_slot": comp["mode"],
                        "downloads": d_count,
                        "likes": item.get("likes", 0),
                        "compatibility": comp
                    })
                if results:
                    return results
        except Exception:
            pass
    results = []
    for item in CURATED_MODELS:
        if category and category != "all" and item.get("category") != category:
            continue
        mid = item["id"]
        author = mid.split("/")[0] if "/" in mid else "Community"
        name = mid.split("/")[-1] if "/" in mid else mid
        comp = evaluate_compatibility(mid, hw)
        results.append({
            "id": mid,
            "author": author,
            "name": name,
            "category": item["category"],
            "desc": item["desc"],
            "description": item["desc"],
            "compat_badge": comp["tier_label"],
            "speed_tier": comp["speed_est"],
            "memory_slot": comp["mode"],
            "downloads": 580000,
            "likes": 12400,
            "compatibility": comp
        })
    return results
