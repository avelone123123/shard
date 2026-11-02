import requests
from hardware import evaluate_compatibility, get_hardware_profile
CURATED_MODELS = [
    {"id": "Qwen/Qwen2.5-Coder-7B-Instruct", "category": "coding", "desc": "Лидер в кодинге для Cursor/OpenCode. Поддержка 128k контекста."},
    {"id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B", "category": "reasoning", "desc": "Рассуждающая модель с логикой DeepSeek-R1. Решает сложные алгоритмы."},
    {"id": "google/gemma-2-9b-it", "category": "general", "desc": "Флагманская открытая модель Google Gemma-2. Высокая эрудиция и стиль."},
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
                    results.append({
                        "id": mid,
                        "author": author,
                        "name": name,
                        "category": "search",
                        "desc": f"Hugging Face открытая модель. Скачиваний: {item.get('downloads', 0):,}",
                        "downloads": item.get("downloads", 0),
                        "likes": item.get("likes", 0),
                        "compatibility": evaluate_compatibility(mid, hw)
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
        results.append({
            "id": mid,
            "author": author,
            "name": name,
            "category": item["category"],
            "desc": item["desc"],
            "downloads": 580000,
            "likes": 12400,
            "compatibility": evaluate_compatibility(mid, hw)
        })
    return results