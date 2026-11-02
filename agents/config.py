import os
import json
CONFIG_PATHS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "social_config.json"),
    r"C:\Users\avelo\Desktop\agent-triad\social_config.json",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "social_config.json")
]
def load_social_config():
    for p in CONFIG_PATHS:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    if cfg and isinstance(cfg, dict):
                        return cfg
            except Exception:
                pass
    return {
        "engine_version": "1.0.0",
        "telegram": {
            "bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            "chat_id": os.environ.get("TELEGRAM_CHAT_ID", "-1004218721826")
        },
        "discord": {
            "bot_token": os.environ.get("DISCORD_BOT_TOKEN", ""),
            "guild_id": os.environ.get("DISCORD_GUILD_ID", ""),
            "webhook_url": os.environ.get("DISCORD_WEBHOOK_URL", "")
        },
        "openrouter": {
            "api_key": os.environ.get("OPENROUTER_API_KEY", ""),
            "text_model": "nvidia/nemotron-3.5-lightning:free",
            "vision_model": "nvidia/nemotron-3.5-super-vl:free"
        },
        "instagram": {
            "username": os.environ.get("INSTAGRAM_USER", "avelone.dev"),
            "password": os.environ.get("INSTAGRAM_PASS", "")
        }
    }