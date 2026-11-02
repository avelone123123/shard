import requests
from agents.config import load_social_config
def send_telegram_update(text):
    cfg = load_social_config()
    tg = cfg.get("telegram", {})
    token = tg.get("bot_token")
    chat_id = tg.get("chat_id")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        r = requests.post(url, json=payload, timeout=8)
        return r.status_code == 200 and r.json().get("ok", False)
    except Exception:
        return False
def send_discord_update(title, description, fields=None):
    cfg = load_social_config()
    dc = cfg.get("discord", {})
    token = dc.get("bot_token")
    channel_id = "1566083280867037424"
    if not token:
        return False
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    embed = {
        "title": title,
        "description": description,
        "color": 392572,
        "fields": fields or []
    }
    payload = {"embeds": [embed]}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=8)
        return r.status_code in (200, 201)
    except Exception:
        return False
def broadcast_release(agent_name, version, feature_summary, benchmark_text=""):
    tg_msg = (
        f"<b>[SHARD ENGINE] Autonomous Update</b>\n\n"
        f"<b>Agent:</b> {agent_name}\n"
        f"<b>Version:</b> {version}\n"
        f"<b>Improvement:</b> {feature_summary}\n"
    )
    if benchmark_text:
        tg_msg += f"<b>Telemetry:</b> {benchmark_text}\n"
    tg_msg += "\n<b>GitHub:</b> https://github.com/avelone123123/shard"
    send_telegram_update(tg_msg)
    fields = [
        {"name": "Agent", "value": agent_name, "inline": True},
        {"name": "Version", "value": version, "inline": True},
        {"name": "Improvement", "value": feature_summary, "inline": False}
    ]
    if benchmark_text:
        fields.append({"name": "Telemetry", "value": benchmark_text, "inline": False})
    send_discord_update(f"Shard Evolution: {agent_name}", feature_summary, fields)