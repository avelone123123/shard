import re
import requests
from hardware import evaluate_compatibility, get_hardware_profile
CURATED_MODELS = [
    {"id": "Qwen/Qwen2.5-Coder-7B-Instruct", "category": "coding", "desc": "Лидер в кодинге для Cursor и OpenCode. Контекст 128k токенов."},
    {"id": "Qwen/Qwen2.5-Coder-1.5B-Instruct", "category": "coding", "desc": "Сверхлегкий кодер. Мгновенный отклик для автокомплита строк кода."},
    {"id": "Qwen/Qwen2.5-Coder-14B-Instruct", "category": "coding", "desc": "Продвинутый кодер среднего размера с глубоким пониманием репозиториев."},
    {"id": "Qwen/Qwen2.5-Coder-32B-Instruct", "category": "coding", "desc": "Флагманский кодер уровня GPT-4o для сложных архитектурных систем."},
    {"id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B", "category": "reasoning", "desc": "Флагманская модель рассуждений с логикой DeepSeek-R1."},
    {"id": "deepseek-ai/DeepSeek-R1-Distill-Llama-8B", "category": "reasoning", "desc": "DeepSeek R1 рассуждения на базе архитектуры Llama-3.1."},
    {"id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B", "category": "reasoning", "desc": "Компактная reasoning модель для моментального логического вывода."},
    {"id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B", "category": "reasoning", "desc": "Мощная дистилляция DeepSeek R1 с математической строгостью."},
    {"id": "deepseek-ai/DeepSeek-V3", "category": "reasoning", "desc": "Универсальный MoE флагман мирового уровня от DeepSeek AI."},
    {"id": "google/gemma-2-9b-it", "category": "general", "desc": "Высокая эрудиция и стиль рассуждений от Google DeepMind."},
    {"id": "google/gemma-2-2b-it", "category": "compact", "desc": "Ультракомпактная модель Google. Занимает минимум памяти VRAM."},
    {"id": "google/gemma-2-27b-it", "category": "general", "desc": "Большая языковая модель Google с глубоким анализом контекста."},
    {"id": "meta-llama/Llama-3.2-3B-Instruct", "category": "compact", "desc": "Компактная быстрая Llama от Meta. 100% помещается в VRAM ноута."},
    {"id": "meta-llama/Llama-3.2-1B-Instruct", "category": "compact", "desc": "Легковесная версия Llama 3.2 с околонулевой задержкой генерации."},
    {"id": "meta-llama/Llama-3.1-8B-Instruct", "category": "general", "desc": "Основной индустриальный стандарт открытых LLM от Meta."},
    {"id": "meta-llama/Llama-3.3-70B-Instruct", "category": "general", "desc": "Флагман Llama 3.3 с качеством на уровне топовых коммерческих моделей."},
    {"id": "microsoft/Phi-3.5-mini-instruct", "category": "compact", "desc": "Мини-модель Microsoft 3.8B параметров с качеством больших моделей."},
    {"id": "microsoft/phi-4", "category": "reasoning", "desc": "Передовая модель Microsoft 14B с сильным уклоном в математику и логику."},
    {"id": "HuggingFaceTB/SmolLM2-1.7B-Instruct", "category": "compact", "desc": "Сверхбыстрая компактная модель Hugging Face для слабых ПК."},
    {"id": "HuggingFaceTB/SmolLM2-360M-Instruct", "category": "compact", "desc": "Микро-модель 360M параметров с мгновенным откликом."},
    {"id": "mistralai/Mistral-7B-Instruct-v0.3", "category": "general", "desc": "Классический надежный универсал Mistral с поддержкой вызова функций."},
    {"id": "mistralai/Mistral-Nemo-Instruct-2407", "category": "general", "desc": "Модель Mistral 12B, разработанная совместно с NVIDIA. 128k контекст."},
    {"id": "mistralai/Codestral-22B-v0.1", "category": "coding", "desc": "Специализированная кодовая модель Mistral для 80+ языков программирования."},
    {"id": "bigcode/starcoder2-7b", "category": "coding", "desc": "Индустриальная модель генерации кода от BigCode и Hugging Face."},
    {"id": "bigcode/starcoder2-3b", "category": "coding", "desc": "Компактный StarCoder для автодополнения строк кода на лету."},
    {"id": "01-ai/Yi-1.5-9B-Chat", "category": "general", "desc": "Модель от 01.AI с сильным балансом кодинга, математики и логики."},
    {"id": "01-ai/Yi-1.5-6B-Chat", "category": "compact", "desc": "Облегченная Yi 6B с быстрым временем первого токена."},
    {"id": "tiiuae/Falcon3-7B-Instruct", "category": "general", "desc": "Свежее поколение открытых моделей Falcon от Института TII."},
    {"id": "tiiuae/Falcon3-3B-Instruct", "category": "compact", "desc": "Высокоэффективный Falcon 3B для работы на потребительских GPU."},
    {"id": "THUDM/glm-4-9b-chat", "category": "general", "desc": "Мощная открытая мультиязычная модель от исследователей Tsinghua."},
    {"id": "cohereforai/c4ai-command-r-v01", "category": "general", "desc": "Модель Cohere для RAG-архитектур и корпоративных агентов."}
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
        "desc": f"Открытая модель {name} от {author}.",
        "description": f"Открытая модель {name} от {author}.",
        "compat_badge": comp["tier_label"],
        "speed_tier": comp["speed_est"],
        "memory_slot": comp["mode"],
        "downloads": 125000,
        "likes": 4200,
        "compatibility": comp
    }
def browse_models(query=None, category=None, limit=36, hw=None):
    if hw is None:
        hw = get_hardware_profile()
    query_clean = (query or "").strip()
    category_clean = (category or "all").strip().lower()
    hf_url = "https://huggingface.co/api/models?filter=text-generation&sort=downloads&direction=-1"
    if query_clean:
        hf_url += f"&search={query_clean}&limit={limit}"
    elif category_clean == "coding":
        hf_url += f"&search=coder&limit={limit}"
    elif category_clean == "reasoning":
        hf_url += f"&search=r1&limit={limit}"
    elif category_clean == "compact":
        hf_url += f"&search=mini&limit={limit}"
    elif category_clean == "general":
        hf_url += f"&search=instruct&limit={limit}"
    else:
        hf_url += f"&limit={limit}"
    live_results = []
    try:
        r = requests.get(hf_url, timeout=4)
        if r.status_code == 200:
            data = r.json()
            seen_ids = set()
            for item in data:
                mid = item.get("id")
                if not mid or mid in seen_ids:
                    continue
                seen_ids.add(mid)
                author = mid.split("/")[0] if "/" in mid else "Community"
                name = mid.split("/")[-1] if "/" in mid else mid
                comp = evaluate_compatibility(mid, hw)
                d_count = item.get("downloads", 0)
                likes_count = item.get("likes", 0)
                desc = f"Hugging Face открытая модель. Скачиваний: {d_count:,}"
                live_results.append({
                    "id": mid,
                    "author": author,
                    "name": name,
                    "category": category_clean if category_clean != "all" else "general",
                    "desc": desc,
                    "description": desc,
                    "compat_badge": comp["tier_label"],
                    "speed_tier": comp["speed_est"],
                    "memory_slot": comp["mode"],
                    "downloads": d_count,
                    "likes": likes_count,
                    "compatibility": comp
                })
    except Exception:
        pass
    if live_results:
        return live_results
    fallback_results = []
    for item in CURATED_MODELS:
        if category_clean != "all" and item.get("category") != category_clean:
            continue
        mid = item["id"]
        if query_clean and query_clean.lower() not in mid.lower():
            continue
        author = mid.split("/")[0] if "/" in mid else "Community"
        name = mid.split("/")[-1] if "/" in mid else mid
        comp = evaluate_compatibility(mid, hw)
        fallback_results.append({
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
    return fallback_results
def fetch_model_card(model_id, hw=None):
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
        r_raw = requests.get(raw_url, timeout=5)
        if r_raw.status_code == 200:
            readme_text = r_raw.text
        else:
            r_master = requests.get(f"https://huggingface.co/{model_id}/raw/master/README.md", timeout=5)
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