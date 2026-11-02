import os
import requests
from agents.config import load_social_config
from agents.agents_registry import get_agent
FALLBACK_MODELS = [
    "qwen/qwen-2.5-coder-32b-instruct",
    "deepseek/deepseek-chat",
    "nvidia/nemotron-3.5-lightning:free"
]
def query_agent(agent_id, prompt, system_prompt=None, max_tokens=300):
    agent_info = get_agent(agent_id)
    preferred = agent_info.get("model", "qwen/qwen-2.5-coder-32b-instruct")
    models_to_try = [preferred] + [m for m in FALLBACK_MODELS if m != preferred]
    cfg = load_social_config()
    api_key = cfg.get("openrouter", {}).get("api_key", os.environ.get("OPENROUTER_API_KEY", ""))
    if not api_key:
        return None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/avelone123123/shard",
        "X-Title": "Shard 50-Agent Autonomous Studio"
    }
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    for m in models_to_try:
        payload = {
            "model": m,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3
        }
        try:
            r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=8)
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"]
                if content and content.strip():
                    return content.strip()
        except Exception:
            continue
    return None