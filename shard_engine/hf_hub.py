import os
import re
import time
from . import settings
from .hardware import evaluate_compatibility, get_hardware_profile
CURATED_MODELS = [
    {"id": "Qwen/Qwen2.5-Coder-1.5B-Instruct", "category": "coding", "desc": "Компактная быстрая модель для кодинга, автодополнения и CLI-агентов."},
    {"id": "Qwen/Qwen2.5-Coder-3B-Instruct", "category": "coding", "desc": "Оптимальный баланс скорости и точности кодогенерации под 4-6 ГБ VRAM."},
    {"id": "Qwen/Qwen2.5-Coder-7B-Instruct", "category": "coding", "desc": "Флагман для кодинга и рефакторинга. Контекст 128k токенов."},
    {"id": "Qwen/Qwen2.5-Coder-14B-Instruct", "category": "coding", "desc": "Высокоточная архитектура для сложных системных задач."},
    {"id": "Qwen/Qwen2.5-Coder-32B-Instruct", "category": "coding", "desc": "Экспертный уровень генерации кода уровня закрытых облачных систем."},
    {"id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B", "category": "reasoning", "desc": "Ультрабыстрая рассуждающая модель логического анализа для ноутбуков."},
    {"id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B", "category": "reasoning", "desc": "Продвинутая модель пошаговых рассуждений на базе Qwen-7B."},
    {"id": "deepseek-ai/DeepSeek-R1-Distill-Llama-8B", "category": "reasoning", "desc": "Рассуждающая архитектура на базе весов Llama-3.1."},
    {"id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B", "category": "reasoning", "desc": "Глубокий математический и архитектурный анализ."},
    {"id": "deepseek-ai/DeepSeek-V3", "category": "reasoning", "desc": "Полномасштабная MoE архитектура нового поколения."},
    {"id": "HuggingFaceTB/SmolLM2-360M-Instruct", "category": "compact", "desc": "Сверхлегкая модель 360M параметров с мгновенным откликом на любом ПК."},
    {"id": "Qwen/Qwen2.5-0.5B-Instruct", "category": "compact", "desc": "Минимальное потребление ОЗУ и мгновенная генерация текста."},
    {"id": "meta-llama/Llama-3.2-1B-Instruct", "category": "compact", "desc": "Ультралегкая Llama 3.2 для мгновенного локального инференса."},
    {"id": "HuggingFaceTB/SmolLM2-1.7B-Instruct", "category": "compact", "desc": "Легковесная открытая модель 1.7B с высокой скоростью на CPU и GPU."},
    {"id": "google/gemma-2-2b-it", "category": "compact", "desc": "Энергоэффективная компактная модель от Google для 4 ГБ VRAM."},
    {"id": "meta-llama/Llama-3.2-3B-Instruct", "category": "compact", "desc": "Быстрая компактная Llama 3B. Полное размещение в VRAM."},
    {"id": "microsoft/Phi-3.5-mini-instruct", "category": "compact", "desc": "Мини-модель 3.8B с высокой логической и кодовой связностью."},
    {"id": "tiiuae/Falcon3-3B-Instruct", "category": "compact", "desc": "Энергоэффективная модель третьего поколения под скромные GPU."},
    {"id": "01-ai/Yi-1.5-6B-Chat", "category": "compact", "desc": "Компактная быстрая двуязычная языковая архитектура."},
    {"id": "google/gemma-2-9b-it", "category": "general", "desc": "Высокая плотность знаний и сильный универсальный интеллект."},
    {"id": "meta-llama/Llama-3.1-8B-Instruct", "category": "general", "desc": "Индустриальный стандарт открытых языковых моделей 8B."},
    {"id": "mistralai/Mistral-7B-Instruct-v0.3", "category": "general", "desc": "Классическая быстрая модель с поддержкой вызова инструментов."},
    {"id": "mistralai/Mistral-Nemo-Instruct-2407", "category": "general", "desc": "Модель 12B с контекстным окном 128k токенов."},
    {"id": "microsoft/phi-4", "category": "reasoning", "desc": "Модель 14B с сильным уклоном в инженерию, математику и логику."},
    {"id": "mistralai/Codestral-22B-v0.1", "category": "coding", "desc": "Профессиональная кодогенерация для 80+ языков программирования."},
    {"id": "bigcode/starcoder2-3b", "category": "coding", "desc": "Быстрый локальный ассистент автодополнения кода."},
    {"id": "bigcode/starcoder2-7b", "category": "coding", "desc": "Специализированная среда для аудита и генерации репозиториев."},
    {"id": "01-ai/Yi-1.5-9B-Chat", "category": "general", "desc": "Сбалансированная модель для аналитики и длинных диалогов."},
    {"id": "tiiuae/Falcon3-7B-Instruct", "category": "general", "desc": "Открытая архитектура третьего поколения для рабочих станций."},
    {"id": "THUDM/glm-4-9b-chat", "category": "general", "desc": "Продвинутая мультиязычная модель общего профиля."},
    {"id": "google/gemma-2-27b-it", "category": "general", "desc": "Высокая производительность в комплексных корпоративных задачах."},
    {"id": "cohereforai/c4ai-command-r-v01", "category": "general", "desc": "Модель для корпоративных RAG-сценариев и работы с документами."},
    {"id": "meta-llama/Llama-3.3-70B-Instruct", "category": "general", "desc": "Флагман 70B с контекстом 128k и поддержкой NVMe стриминга."}
]
_hub_cache = {}
def get_local_cached_ids():
    cached = set()
    hf_home = os.environ.get("HF_HOME") or settings.models_dir()
    hub_dir = os.path.join(hf_home, "hub")
    if os.path.exists(hub_dir):
        try:
            for item in os.listdir(hub_dir):
                if item.startswith("models--"):
                    snap_dir = os.path.join(hub_dir, item, "snapshots")
                    if os.path.exists(snap_dir) and os.listdir(snap_dir):
                        cached.add(item.replace("models--", "").replace("--", "/").lower())
        except Exception:
            pass
    return cached
def fetch_model_metadata(model_id, hw=None):
    import requests
    if hw is None:
        hw = get_hardware_profile()
    comp = evaluate_compatibility(model_id, hw)
    author = model_id.split("/")[0] if "/" in model_id else "Community"
    name = model_id.split("/")[-1] if "/" in model_id else model_id
    cached = get_local_cached_ids()
    d_count = None
    likes_count = None
    try:
        r = requests.get(f"https://huggingface.co/api/models/{model_id}", timeout=3.5)
        if r.status_code == 200:
            m_json = r.json()
            d_count = m_json.get("downloads")
            likes_count = m_json.get("likes")
    except Exception:
        pass
    return {
        "id": model_id,
        "author": author,
        "name": name,
        "desc": f"Открытая архитектура {name} ({author}).",
        "description": f"Открытая архитектура {name} ({author}).",
        "compat_badge": comp["tier_label"],
        "speed_tier": comp["speed_est"],
        "memory_slot": comp["mode"],
        "size_est_mb": comp.get("size_est_mb", 1000),
        "is_downloaded": model_id.lower() in cached,
        "downloads": d_count,
        "likes": likes_count,
        "compatibility": comp
    }
def _build_entry(mid, cat, desc, d_count, likes_count, hw, cached):
    author = mid.split("/")[0] if "/" in mid else "Community"
    name = mid.split("/")[-1] if "/" in mid else mid
    comp = evaluate_compatibility(mid, hw)
    return {
        "id": mid,
        "author": author,
        "name": name,
        "category": cat,
        "desc": desc,
        "description": desc,
        "compat_badge": comp["tier_label"],
        "speed_tier": comp["speed_est"],
        "memory_slot": comp["mode"],
        "size_est_mb": comp.get("size_est_mb", 1000),
        "is_downloaded": mid.lower() in cached,
        "downloads": d_count,
        "likes": likes_count,
        "compatibility": comp
    }
def browse_models(query=None, category=None, limit=80, hw=None):
    import requests
    if hw is None:
        hw = get_hardware_profile()
    query_clean = (query or "").strip()
    category_clean = (category or "all").strip().lower()
    cached = get_local_cached_ids()
    cache_key = (query_clean.lower(), category_clean, int(limit), hw.get("vram_total_mb", 0), hw.get("ram_total_mb", 0))
    now = time.time()
    if cache_key in _hub_cache and (now - _hub_cache[cache_key][0] < 60.0):
        items = _hub_cache[cache_key][1]
        for it in items:
            it["is_downloaded"] = it["id"].lower() in cached
        return items
    if category_clean == "ideal":
        ideal_list = []
        for item in CURATED_MODELS:
            entry = _build_entry(item["id"], item["category"], item["desc"], None, None, hw, cached)
            tier = entry["compatibility"]["tier"]
            if tier in ("PERFECT", "RECOMMENDED"):
                if not query_clean or query_clean.lower() in item["id"].lower() or query_clean.lower() in item["desc"].lower():
                    ideal_list.append(entry)
        ideal_list.sort(key=lambda x: (0 if x["compatibility"]["tier"] == "PERFECT" else 1, x.get("size_est_mb", 9999)))
        _hub_cache[cache_key] = (now, ideal_list)
        return ideal_list
    live_results = []
    seen_ids = set()
    if not query_clean:
        for item in CURATED_MODELS:
            if category_clean == "all" or item.get("category") == category_clean:
                mid = item["id"]
                seen_ids.add(mid)
                live_results.append(_build_entry(mid, item["category"], item["desc"], None, None, hw, cached))
    if query_clean and "/" in query_clean:
        try:
            r_single = requests.get(f"https://huggingface.co/api/models/{query_clean}", timeout=3.5)
            if r_single.status_code == 200:
                item = r_single.json()
                mid = item.get("id", query_clean)
                seen_ids.add(mid)
                d_count = item.get("downloads")
                likes_count = item.get("likes")
                desc = "Репозиторий Hugging Face"
                live_results.append(_build_entry(mid, "all", desc, d_count, likes_count, hw, cached))
        except Exception:
            pass
    hf_url = f"https://huggingface.co/api/models?sort=downloads&direction=-1&limit={limit}"
    if query_clean:
        hf_url += f"&search={query_clean}"
    elif category_clean == "coding":
        hf_url += "&search=coder&filter=text-generation"
    elif category_clean == "reasoning":
        hf_url += "&search=r1&filter=text-generation"
    elif category_clean == "compact":
        hf_url += "&search=instruct&filter=text-generation"
    elif category_clean == "general":
        hf_url += "&search=instruct&filter=text-generation"
    else:
        hf_url += "&filter=text-generation"
    try:
        r = requests.get(hf_url, timeout=4.0)
        if r.status_code == 200:
            data = r.json()
            for item in data:
                mid = item.get("id")
                if not mid or mid in seen_ids:
                    continue
                dl = item.get("downloads")
                desc = "Открытая архитектура Hugging Face"
                if dl is not None:
                    desc += f". Скачиваний: {dl:,}"
                entry = _build_entry(mid, category_clean if category_clean != "all" else "general", desc, dl, item.get("likes"), hw, cached)
                if category_clean == "compact" and entry["compatibility"].get("params_b", 7.0) > 4.2:
                    continue
                seen_ids.add(mid)
                live_results.append(entry)
    except Exception:
        pass
    if live_results:
        _hub_cache[cache_key] = (now, live_results)
        return live_results
    fallback_results = []
    for item in CURATED_MODELS:
        if category_clean != "all" and item.get("category") != category_clean:
            continue
        mid = item["id"]
        if query_clean and query_clean.lower() not in mid.lower():
            continue
        fallback_results.append(_build_entry(mid, item["category"], item["desc"], None, None, hw, cached))
    _hub_cache[cache_key] = (now, fallback_results)
    return fallback_results
def fetch_model_card(model_id, hw=None):
    import requests
    if hw is None:
        hw = get_hardware_profile()
    comp = evaluate_compatibility(model_id, hw)
    author = model_id.split("/")[0] if "/" in model_id else "Community"
    name = model_id.split("/")[-1] if "/" in model_id else model_id
    meta_url = f"https://huggingface.co/api/models/{model_id}"
    raw_url = f"https://huggingface.co/{model_id}/raw/main/README.md"
    d_count = 0
    likes_count = 0
    pipeline_tag = "text-generation"
    tags = []
    card_data = {}
    try:
        r_meta = requests.get(meta_url, timeout=4)
        if r_meta.status_code == 200:
            m_json = r_meta.json()
            d_count = m_json.get("downloads", 0)
            likes_count = m_json.get("likes", 0)
            pipeline_tag = m_json.get("pipeline_tag", "text-generation")
            tags = m_json.get("tags", [])
            card_data = m_json.get("cardData", {})
    except Exception:
        pass
    readme_text = ""
    try:
        r_raw = requests.get(raw_url, timeout=4)
        if r_raw.status_code == 200:
            readme_text = r_raw.text
        else:
            r_master = requests.get(f"https://huggingface.co/{model_id}/raw/master/README.md", timeout=4)
            if r_master.status_code == 200:
                readme_text = r_master.text
    except Exception:
        pass
    if readme_text:
        base_resolve = f"https://huggingface.co/{model_id}/resolve/main/"
        def repl_img(m):
            alt = m.group(1)
            src = m.group(2).strip()
            if not src.startswith(("http://", "https://", "data:")):
                clean_src = re.sub(r"^\.?\/?(resolve\/main\/)?", "", src)
                src = base_resolve + clean_src
            return f"![{alt}]({src})"
        readme_text = re.sub(r"!\[(.*?)\]\((.*?)\)", repl_img, readme_text)
    return {
        "id": model_id,
        "author": author,
        "name": name,
        "downloads": d_count,
        "likes": likes_count,
        "pipeline_tag": pipeline_tag,
        "tags": tags[:15],
        "readme": readme_text,
        "card_data": card_data,
        "compatibility": comp
    }

