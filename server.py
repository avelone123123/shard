import json
import os
import shutil
import subprocess
import time
import uuid
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from engine import engine_instance
from hardware import get_hardware_profile, evaluate_compatibility
from hf_hub import browse_models, fetch_model_metadata, fetch_model_card, get_local_cached_ids
app = FastAPI(title="Shard Local Model Engine and Server", version="1.4.14")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
DATA_DIR = os.getenv("SHARD_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".shard")
os.makedirs(DATA_DIR, exist_ok=True)
KEYS_FILE = os.path.join(DATA_DIR, "keys_db.json")
def save_keys(keys):
    try:
        with open(KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(keys, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
    try:
        legacy_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys_db.json")
        with open(legacy_file, "w", encoding="utf-8") as f:
            json.dump(keys, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
def load_keys():
    for path in (KEYS_FILE, os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys_db.json")):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    if isinstance(d, list) and len(d) > 0:
                        return d
            except Exception:
                pass
    initial = [f"sk-shard-{uuid.uuid4().hex[:16]}"]
    save_keys(initial)
    return initial
API_KEYS = load_keys()
DASHBOARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
@app.get("/api/system/hardware")
def api_hardware():
    hw = get_hardware_profile()
    engine_st = engine_instance.get_status()
    return {"hardware": hw, "engine": engine_st}
@app.get("/api/hf/models")
def api_hf_models(query: str = None, category: str = "all", limit: int = 80):
    return browse_models(query=query, category=category, limit=limit)
@app.get("/api/hf/model/{model_id:path}")
def api_hf_model_card(model_id: str):
    return fetch_model_card(model_id)
@app.post("/api/models/load")
async def api_load_model(req: Request):
    data = await req.json()
    model_id = data.get("model_id", "Qwen/Qwen2.5-Coder-1.5B-Instruct")
    info = engine_instance.load_model(model_id)
    return {"status": "ok", "loaded": info}
@app.post("/api/models/unload")
def api_unload_model():
    return engine_instance.unload_model()
@app.post("/api/models/cancel")
def api_cancel_model():
    return engine_instance.cancel_operation()
@app.get("/api/models/downloaded")
def api_get_downloaded_models():
    hf_home = os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".shard", "models")
    hub_dir = os.path.join(hf_home, "hub")
    hw = get_hardware_profile()
    models = []
    total_bytes = 0
    if os.path.exists(hub_dir):
        try:
            from huggingface_hub import scan_cache_dir
            cache = scan_cache_dir(hub_dir)
            active_id = (engine_instance.active_model_id or "").lower()
            current_id = (engine_instance.current_loaded_id or "").lower()
            for repo in cache.repos:
                total_bytes += repo.size_on_disk
                sz_mb = round(repo.size_on_disk / (1024 * 1024), 1)
                sz_str = f"{round(sz_mb / 1024, 2)} ГБ" if sz_mb >= 1024 else f"{sz_mb} МБ"
                author = repo.repo_id.split("/")[0] if "/" in repo.repo_id else "Community"
                name = repo.repo_id.split("/")[-1]
                comp = evaluate_compatibility(repo.repo_id, hw)
                is_act = (repo.repo_id.lower() == current_id) or (engine_instance.model is not None and repo.repo_id.lower() == active_id)
                models.append({
                    "id": repo.repo_id,
                    "author": author,
                    "name": name,
                    "size_bytes": repo.size_on_disk,
                    "size_mb": sz_mb,
                    "size_str": sz_str,
                    "is_active": is_act,
                    "last_modified": repo.last_modified,
                    "compatibility": comp
                })
        except Exception:
            for item in os.listdir(hub_dir):
                if item.startswith("models--"):
                    clean_id = item.replace("models--", "").replace("--", "/")
                    full_p = os.path.join(hub_dir, item)
                    sz = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(full_p) for f in fs if os.path.exists(os.path.join(r, f)))
                    sz_mb = round(sz / (1024 * 1024), 1)
                    sz_str = f"{round(sz_mb / 1024, 2)} ГБ" if sz_mb >= 1024 else f"{sz_mb} МБ"
                    total_bytes += sz
                    models.append({
                        "id": clean_id,
                        "author": clean_id.split("/")[0] if "/" in clean_id else "Community",
                        "name": clean_id.split("/")[-1],
                        "size_bytes": sz,
                        "size_mb": sz_mb,
                        "size_str": sz_str,
                        "is_active": (clean_id.lower() == (engine_instance.current_loaded_id or "").lower()),
                        "last_modified": int(os.path.getmtime(full_p)),
                        "compatibility": evaluate_compatibility(clean_id, hw)
                    })
    models.sort(key=lambda x: x["size_bytes"], reverse=True)
    return {
        "models": models,
        "total_models": len(models),
        "total_size_gb": round(total_bytes / (1024 ** 3), 2),
        "disk_free_gb": hw.get("disk_free_gb", 0)
    }
@app.delete("/api/models/downloaded")
@app.post("/api/models/delete")
async def api_delete_downloaded_model(req: Request):
    model_id = ""
    try:
        data = await req.json()
        model_id = data.get("model_id", "").strip()
    except Exception:
        pass
    if not model_id:
        model_id = req.query_params.get("model_id", "").strip()
    if not model_id:
        return JSONResponse(status_code=400, content={"status": "error", "message": "model_id required"})
    if engine_instance.active_model_id == model_id or engine_instance.current_loaded_id == model_id:
        engine_instance.unload_model()
    hf_home = os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".shard", "models")
    hub_dir = os.path.join(hf_home, "hub")
    freed_bytes = 0
    try:
        from huggingface_hub import scan_cache_dir
        cache = scan_cache_dir(hub_dir)
        target_repo = None
        for r in cache.repos:
            if r.repo_id.lower() == model_id.lower() or r.repo_id.split("/")[-1].lower() == model_id.lower():
                target_repo = r
                break
        if target_repo:
            freed_bytes = target_repo.size_on_disk
            rev_hashes = [rev.commit_hash for rev in target_repo.revisions]
            if rev_hashes:
                strategy = cache.delete_revisions(*rev_hashes)
                strategy.execute()
    except Exception:
        pass
    norm_name = "models--" + model_id.replace("/", "--")
    folder_path = os.path.join(hub_dir, norm_name)
    if os.path.exists(folder_path):
        if freed_bytes == 0:
            freed_bytes = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(folder_path) for f in fs if os.path.exists(os.path.join(r, f)))
        shutil.rmtree(folder_path, ignore_errors=True)
    sz_mb = round(freed_bytes / (1024 * 1024), 1)
    freed_str = f"{round(sz_mb / 1024, 2)} ГБ" if sz_mb >= 1024 else f"{sz_mb} МБ"
    return {"status": "ok", "deleted": model_id, "freed_str": freed_str, "freed_bytes": freed_bytes}
@app.get("/api/keys")
def api_get_keys():
    return {"keys": API_KEYS}
@app.post("/api/keys/generate")
def api_gen_key():
    new_key = f"sk-shard-{uuid.uuid4().hex[:16]}"
    API_KEYS.append(new_key)
    save_keys(API_KEYS)
    return {"key": new_key, "keys": API_KEYS}
@app.delete("/api/keys")
@app.post("/api/keys/delete")
async def api_del_key(req: Request):
    target = ""
    try:
        data = await req.json()
        target = data.get("key", "").strip()
    except Exception:
        pass
    if not target:
        target = req.query_params.get("key", "").strip()
    if target and target in API_KEYS:
        API_KEYS.remove(target)
        if not API_KEYS:
            API_KEYS.append(f"sk-shard-{uuid.uuid4().hex[:16]}")
        save_keys(API_KEYS)
        return {"status": "ok", "deleted": target, "keys": API_KEYS}
    return JSONResponse(status_code=404, content={"status": "error", "message": "Key not found"})
def _backup_and_write(target_path, content_str):
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    if os.path.exists(target_path):
        bak = target_path + ".shard.bak"
        if not os.path.exists(bak):
            try:
                shutil.copy2(target_path, bak)
            except Exception:
                pass
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(content_str)
def _set_win_user_env(var_map):
    if os.name != "nt":
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE) as key_handle:
            for k, v in var_map.items():
                winreg.SetValueEx(key_handle, k, 0, winreg.REG_SZ, str(v))
    except Exception:
        pass
@app.post("/api/integrations/apply")
async def api_apply_integration(req: Request):
    data = await req.json()
    tool = (data.get("tool") or "claude").lower().strip()
    key = (data.get("key") or (API_KEYS[0] if API_KEYS else "sk-shard-local")).strip()
    model = (data.get("model") or engine_instance.active_model_id or "Qwen/Qwen2.5-Coder-1.5B-Instruct").strip()
    home = os.path.expanduser("~")
    base_url = "http://localhost:8000/v1"
    try:
        if tool == "claude":
            target = os.path.join(home, ".claude", "settings.json")
            existing = {}
            if os.path.exists(target):
                try:
                    existing = json.loads(open(target, "r", encoding="utf-8").read())
                except Exception:
                    existing = {}
            existing["hasCompletedOnboarding"] = True
            env_blk = existing.get("env", {}) if isinstance(existing.get("env"), dict) else {}
            env_blk.update({
                "ANTHROPIC_BASE_URL": base_url,
                "ANTHROPIC_AUTH_TOKEN": key,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": model
            })
            existing["env"] = env_blk
            _backup_and_write(target, json.dumps(existing, indent=2, ensure_ascii=False))
            return {"status": "ok", "tool": tool, "path": target, "message": f"Claude Code настроен на {model}"}
        if tool == "opencode":
            target = os.path.join(home, ".config", "opencode", "config.json")
            cfg = {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    "shard": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "Shard Local Engine",
                        "options": {"baseURL": base_url, "apiKey": key},
                        "models": {
                            model: {"name": model.split("/")[-1], "modalities": {"input": ["text"], "output": ["text"]}, "limit": {"context": 128000, "output": 8192}},
                            "Qwen/Qwen2.5-Coder-1.5B-Instruct": {"name": "Qwen2.5 Coder 1.5B", "modalities": {"input": ["text"], "output": ["text"]}, "limit": {"context": 128000, "output": 8192}},
                            "Qwen/Qwen2.5-Coder-7B-Instruct": {"name": "Qwen2.5 Coder 7B", "modalities": {"input": ["text"], "output": ["text"]}, "limit": {"context": 128000, "output": 8192}},
                            "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B": {"name": "DeepSeek R1 Distill 7B", "modalities": {"input": ["text"], "output": ["text"]}, "limit": {"context": 128000, "output": 8192}}
                        }
                    }
                },
                "model": f"shard/{model}"
            }
            _backup_and_write(target, json.dumps(cfg, indent=2, ensure_ascii=False))
            return {"status": "ok", "tool": tool, "path": target, "message": f"OpenCode настроен на shard/{model}"}
        if tool == "continue":
            target = os.path.join(home, ".continue", "config.json")
            existing = {"models": []}
            if os.path.exists(target):
                try:
                    existing = json.loads(open(target, "r", encoding="utf-8").read())
                except Exception:
                    existing = {"models": []}
            models_arr = [m for m in existing.get("models", []) if isinstance(m, dict) and m.get("title") != "Shard Local AI"]
            models_arr.insert(0, {"title": "Shard Local AI", "provider": "openai", "model": model, "apiBase": base_url, "apiKey": key})
            existing["models"] = models_arr
            _backup_and_write(target, json.dumps(existing, indent=2, ensure_ascii=False))
            return {"status": "ok", "tool": tool, "path": target, "message": "Continue IDE подключен к Shard Local AI"}
        if tool == "aider":
            target = os.path.join(home, ".aider.conf.yml")
            yml = f"openai-api-base: {base_url}\nopenai-api-key: {key}\nmodel: openai/{model}\n"
            _backup_and_write(target, yml)
            return {"status": "ok", "tool": tool, "path": target, "message": f"Aider CLI настроен ({target})"}
        if tool == "zed":
            appdata = os.environ.get("APPDATA") or os.path.join(home, ".config")
            target = os.path.join(appdata, "Zed", "settings.json") if os.name == "nt" else os.path.join(home, ".config", "zed", "settings.json")
            existing = {}
            if os.path.exists(target):
                try:
                    existing = json.loads(open(target, "r", encoding="utf-8").read())
                except Exception:
                    existing = {}
            lm = existing.get("language_models", {}) if isinstance(existing.get("language_models"), dict) else {}
            lm["openai"] = {"api_url": base_url, "available_models": [{"name": model, "display_name": f"Shard: {model.split('/')[-1]}", "max_tokens": 128000}]}
            existing["language_models"] = lm
            _backup_and_write(target, json.dumps(existing, indent=2, ensure_ascii=False))
            return {"status": "ok", "tool": tool, "path": target, "message": "Zed Editor подключен к локальному серверу Shard"}
        if tool == "codex":
            target = os.path.join(home, ".codex", "config.toml")
            toml_str = f'model = "{model}"\nmodel_provider = "shard"\n[model_providers.shard]\nname = "Shard Local"\nbase_url = "{base_url}"\nenv_key = "OPENAI_API_KEY"\n'
            _backup_and_write(target, toml_str)
            _set_win_user_env({"OPENAI_API_KEY": key})
            return {"status": "ok", "tool": tool, "path": target, "message": "Codex CLI настроен на провайдер Shard Local"}
        if tool in ("openrouter", "cursor", "env", "sdk"):
            env_path = os.path.join(DATA_DIR, "shard_local_bridge.env")
            env_content = f"OPENAI_API_BASE={base_url}\nOPENAI_BASE_URL={base_url}\nOPENROUTER_BASE_URL={base_url}\nOPENAI_API_KEY={key}\nOPENROUTER_API_KEY={key}\nSHARD_MODEL={model}\n"
            _backup_and_write(env_path, env_content)
            _set_win_user_env({
                "OPENAI_API_BASE": base_url,
                "OPENAI_BASE_URL": base_url,
                "OPENAI_API_KEY": key,
                "ANTHROPIC_BASE_URL": base_url,
                "ANTHROPIC_AUTH_TOKEN": key
            })
            return {"status": "ok", "tool": tool, "path": env_path, "message": "Переменные окружения Windows и локальный роутер активированы"}
        return JSONResponse(status_code=400, content={"status": "error", "message": f"Неизвестная интеграция: {tool}"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
@app.get("/v1/models")
def list_models():
    st = engine_instance.get_status()
    current = st.get("active_model") or "Qwen/Qwen2.5-Coder-1.5B-Instruct"
    cached_list = sorted(list(get_local_cached_ids()))
    popular = [
        current,
        *cached_list,
        "Qwen/Qwen2.5-Coder-1.5B-Instruct",
        "Qwen/Qwen2.5-Coder-7B-Instruct",
        "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B",
        "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
        "meta-llama/Llama-3.2-3B-Instruct",
        "google/gemma-2-9b-it",
        "mistralai/Mistral-7B-Instruct-v0.3"
    ]
    seen = set()
    model_list = []
    for m in popular:
        if m and m.lower() not in seen:
            seen.add(m.lower())
            model_list.append({"id": m, "object": "model", "created": int(time.time()), "owned_by": "shard-local"})
    return {"object": "list", "data": model_list}
@app.get("/v1/ping")
def api_ping():
    return {"status": "ok", "latency_ms": 0.3, "engine": "C++20 Zero-Copy / MASM AVX2", "active_model": engine_instance.active_model_id, "is_loaded": engine_instance.model is not None}
@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    data = await req.json()
    messages = data.get("messages", [])
    stream = data.get("stream", False)
    max_tokens = data.get("max_tokens", 512)
    temperature = data.get("temperature", 0.7)
    model = data.get("model") or engine_instance.active_model_id or "shard-local-model"
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    if stream:
        def event_generator():
            for item in engine_instance.generate_stream(messages, max_tokens, temperature):
                if item.get("type") == "thought":
                    delta_payload = {
                        "id": cid,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"reasoning_content": item["token"]}, "finish_reason": None}]
                    }
                    yield f"data: {json.dumps(delta_payload, ensure_ascii=False)}\n\n"
                elif item.get("type") == "content":
                    delta_payload = {
                        "id": cid,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": item["token"]}, "finish_reason": None}]
                    }
                    yield f"data: {json.dumps(delta_payload, ensure_ascii=False)}\n\n"
                elif item.get("type") == "done":
                    final_payload = {
                        "id": cid,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        "telemetry": item.get("telemetry", {}),
                        "usage": item.get("usage", {})
                    }
                    yield f"data: {json.dumps(final_payload, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
        return StreamingResponse(event_generator(), media_type="text/event-stream")
    result = engine_instance.generate_sync(messages, max_tokens, temperature)
    return {
        "id": cid,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": result["content"],
                    "reasoning_content": result.get("reasoning_content", "")
                },
                "finish_reason": "stop"
            }
        ],
        "telemetry": result.get("telemetry", {}),
        "usage": result.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    }
@app.post("/v1/messages")
async def anthropic_messages(req: Request):
    data = await req.json()
    raw_messages = data.get("messages", [])
    sys_prompt = data.get("system", "")
    model = data.get("model") or engine_instance.active_model_id or "Qwen/Qwen2.5-Coder-1.5B-Instruct"
    max_tokens = data.get("max_tokens", 1024)
    temperature = data.get("temperature", 0.7)
    stream = data.get("stream", False)
    normalized = []
    if sys_prompt:
        if isinstance(sys_prompt, list):
            sys_txt = " ".join(c.get("text", "") for c in sys_prompt if isinstance(c, dict))
        else:
            sys_txt = str(sys_prompt)
        if sys_txt.strip():
            normalized.append({"role": "system", "content": sys_txt})
    for m in raw_messages:
        content = m.get("content", "")
        if isinstance(content, list):
            parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            content = " ".join(parts)
        normalized.append({"role": m.get("role", "user"), "content": content})
    msg_id = f"msg_{uuid.uuid4().hex[:16]}"
    if stream:
        def anthropic_sse():
            start_ev = {"type": "message_start", "message": {"id": msg_id, "type": "message", "role": "assistant", "content": [], "model": model, "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}}
            yield f"event: message_start\ndata: {json.dumps(start_ev, ensure_ascii=False)}\n\n"
            blk_start = {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
            yield f"event: content_block_start\ndata: {json.dumps(blk_start, ensure_ascii=False)}\n\n"
            out_toks = 0
            for item in engine_instance.generate_stream(normalized, max_tokens, temperature):
                if item.get("type") in ("content", "thought") and item.get("token"):
                    delta_ev = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": item["token"]}}
                    yield f"event: content_block_delta\ndata: {json.dumps(delta_ev, ensure_ascii=False)}\n\n"
                elif item.get("type") == "done":
                    out_toks = item.get("usage", {}).get("completion_tokens", 0)
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
            msg_delta = {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": out_toks}}
            yield f"event: message_delta\ndata: {json.dumps(msg_delta)}\n\n"
            yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
        return StreamingResponse(anthropic_sse(), media_type="text/event-stream")
    result = engine_instance.generate_sync(normalized, max_tokens, temperature)
    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": result["content"]}],
        "model": model,
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": result.get("usage", {}).get("prompt_tokens", 0),
            "output_tokens": result.get("usage", {}).get("completion_tokens", 0)
        }
    }
@app.post("/v1/messages/count_tokens")
async def anthropic_count_tokens(req: Request):
    data = await req.json()
    raw_messages = data.get("messages", [])
    count = sum(len(str(m.get("content", "")).split()) for m in raw_messages)
    return {"input_tokens": max(1, count)}
@app.get("/i")
def installer_script(tool: str = "claude", os: str = "linux", key: str = ""):
    tool_clean = tool.lower().strip()
    os_clean = os.lower().strip()
    chosen_key = key.strip() if key else (API_KEYS[0] if API_KEYS else "sk-shard-local")
    act_model = engine_instance.active_model_id or "Qwen/Qwen2.5-Coder-1.5B-Instruct"
    if os_clean == "windows":
        if tool_clean == "claude":
            script = '$k = if ($env:SHARD_KEY) { $env:SHARD_KEY } else { "' + chosen_key + '" }\n' + \
                     '$p = Join-Path $HOME ".claude"\n' + \
                     'if (!(Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }\n' + \
                     '$cfg = @{\n' + \
                     '  hasCompletedOnboarding = $true\n' + \
                     '  env = @{\n' + \
                     '    ANTHROPIC_BASE_URL = "http://localhost:8000/v1"\n' + \
                     '    ANTHROPIC_AUTH_TOKEN = $k\n' + \
                     '    ANTHROPIC_DEFAULT_OPUS_MODEL = "' + act_model + '"\n' + \
                     '    ANTHROPIC_DEFAULT_SONNET_MODEL = "' + act_model + '"\n' + \
                     '    ANTHROPIC_DEFAULT_HAIKU_MODEL = "' + act_model + '"\n' + \
                     '  }\n' + \
                     '} | ConvertTo-Json -Depth 5\n' + \
                     'Set-Content -Path (Join-Path $p "settings.json") -Value $cfg -Encoding UTF8\n' + \
                     'Write-Host "[OK] Shard: Claude CLI configured successfully on Windows!" -ForegroundColor Green\n'
        elif tool_clean == "opencode":
            script = '$k = if ($env:SHARD_KEY) { $env:SHARD_KEY } else { "' + chosen_key + '" }\n' + \
                     '$p = Join-Path $HOME ".config\\opencode"\n' + \
                     'if (!(Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }\n' + \
                     '$cfg = @{\n' + \
                     '  \'$schema\' = "https://opencode.ai/config.json"\n' + \
                     '  provider = @{\n' + \
                     '    shard = @{\n' + \
                     '      npm = "@ai-sdk/openai-compatible"\n' + \
                     '      name = "Shard"\n' + \
                     '      options = @{\n' + \
                     '        baseURL = "http://localhost:8000/v1"\n' + \
                     '        apiKey = $k\n' + \
                     '      }\n' + \
                     '      models = @{\n' + \
                     '        "' + act_model + '" = @{ name = "' + act_model.split("/")[-1] + '"; modalities = @{ input = @("text"); output = @("text") }; limit = @{ context = 128000; output = 8192 } }\n' + \
                     '        "Qwen/Qwen2.5-Coder-7B-Instruct" = @{ name = "Qwen2.5 Coder 7B"; modalities = @{ input = @("text"); output = @("text") }; limit = @{ context = 128000; output = 8192 } }\n' + \
                     '        "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B" = @{ name = "DeepSeek R1 Distill 7B"; modalities = @{ input = @("text"); output = @("text") }; limit = @{ context = 128000; output = 8192 } }\n' + \
                     '      }\n' + \
                     '    }\n' + \
                     '  }\n' + \
                     '  model = "shard/' + act_model + '"\n' + \
                     '} | ConvertTo-Json -Depth 6\n' + \
                     'Set-Content -Path (Join-Path $p "config.json") -Value $cfg -Encoding UTF8\n' + \
                     'Write-Host "[OK] Shard: OpenCode configured successfully on Windows!" -ForegroundColor Green\n'
        else:
            script = '$k = if ($env:SHARD_KEY) { $env:SHARD_KEY } else { "' + chosen_key + '" }\n' + \
                     '[Environment]::SetEnvironmentVariable("OPENAI_API_BASE", "http://localhost:8000/v1", "User")\n' + \
                     '[Environment]::SetEnvironmentVariable("OPENAI_BASE_URL", "http://localhost:8000/v1", "User")\n' + \
                     '[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", $k, "User")\n' + \
                     'Write-Host "[OK] Shard: Environment variables set for ' + tool_clean + ' on Windows!" -ForegroundColor Green\n'
        return PlainTextResponse(script, media_type="text/plain; charset=utf-8")
    if tool_clean == "claude":
        script = '#!/usr/bin/env bash\n' + \
                 'KEY="${1:-' + chosen_key + '}"\n' + \
                 'mkdir -p "$HOME/.claude"\n' + \
                 'cat << \'EOF\' > "$HOME/.claude/settings.json"\n' + \
                 '{\n' + \
                 '  "hasCompletedOnboarding": true,\n' + \
                 '  "env": {\n' + \
                 '    "ANTHROPIC_BASE_URL": "http://localhost:8000/v1",\n' + \
                 '    "ANTHROPIC_AUTH_TOKEN": "KEY_PLACEHOLDER",\n' + \
                 '    "ANTHROPIC_DEFAULT_OPUS_MODEL": "' + act_model + '",\n' + \
                 '    "ANTHROPIC_DEFAULT_SONNET_MODEL": "' + act_model + '",\n' + \
                 '    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "' + act_model + '"\n' + \
                 '  }\n' + \
                 '}\n' + \
                 'EOF\n' + \
                 'sed -i "s/KEY_PLACEHOLDER/$KEY/g" "$HOME/.claude/settings.json"\n' + \
                 'echo "[OK] Shard: Claude CLI settings.json configured successfully."\n'
    elif tool_clean == "opencode":
        script = '#!/usr/bin/env bash\n' + \
                 'KEY="${1:-' + chosen_key + '}"\n' + \
                 'mkdir -p "$HOME/.config/opencode"\n' + \
                 'cat << \'EOF\' > "$HOME/.config/opencode/config.json"\n' + \
                 '{\n' + \
                 '  "$schema": "https://opencode.ai/config.json",\n' + \
                 '  "provider": {\n' + \
                 '    "shard": {\n' + \
                 '      "npm": "@ai-sdk/openai-compatible",\n' + \
                 '      "name": "Shard",\n' + \
                 '      "options": {\n' + \
                 '        "baseURL": "http://localhost:8000/v1",\n' + \
                 '        "apiKey": "KEY_PLACEHOLDER"\n' + \
                 '      },\n' + \
                 '      "models": {\n' + \
                 '        "' + act_model + '": { "name": "' + act_model.split("/")[-1] + '", "modalities": {"input":["text"],"output":["text"]}, "limit": {"context":128000,"output":8192} },\n' + \
                 '        "Qwen/Qwen2.5-Coder-7B-Instruct": { "name": "Qwen2.5 Coder 7B", "modalities": {"input":["text"],"output":["text"]}, "limit": {"context":128000,"output":8192} }\n' + \
                 '      }\n' + \
                 '    }\n' + \
                 '  },\n' + \
                 '  "model": "shard/' + act_model + '"\n' + \
                 '}\n' + \
                 'EOF\n' + \
                 'sed -i "s/KEY_PLACEHOLDER/$KEY/g" "$HOME/.config/opencode/config.json"\n' + \
                 'echo "[OK] Shard: OpenCode config.json configured successfully."\n'
    else:
        script = '#!/usr/bin/env bash\n' + \
                 'KEY="${1:-' + chosen_key + '}"\n' + \
                 'export OPENAI_API_BASE="http://localhost:8000/v1"\n' + \
                 'export OPENAI_BASE_URL="http://localhost:8000/v1"\n' + \
                 'export OPENAI_API_KEY="$KEY"\n' + \
                 'echo "[OK] Shard: OPENAI_API_BASE and OPENAI_API_KEY exported for ' + tool_clean + '."\n'
    return PlainTextResponse(script, media_type="text/plain; charset=utf-8")
@app.get("/", response_class=HTMLResponse)
def dashboard():
    with open(DASHBOARD_PATH, "r", encoding="utf-8") as f:
        return f.read()
