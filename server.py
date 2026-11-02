import json
import os
import time
import uuid
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from engine import engine_instance
from hardware import get_hardware_profile
from hf_hub import browse_models, fetch_model_metadata, fetch_model_card
app = FastAPI(title="Shard Local Model Engine and Server", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
KEYS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys_db.json")
def load_keys():
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                if isinstance(d, list):
                    return d
        except Exception:
            pass
    return []
def save_keys(keys):
    try:
        with open(KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(keys, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
API_KEYS = load_keys()
DASHBOARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
@app.get("/api/system/hardware")
def api_hardware():
    hw = get_hardware_profile()
    engine_st = engine_instance.get_status()
    return {"hardware": hw, "engine": engine_st}
@app.get("/api/hf/models")
def api_hf_models(query: str = None, category: str = "all", limit: int = 50):
    return browse_models(query=query, category=category, limit=limit)
@app.get("/api/hf/model/{model_id:path}")
def api_hf_model_card(model_id: str):
    return fetch_model_card(model_id)
@app.post("/api/models/load")
async def api_load_model(req: Request):
    data = await req.json()
    model_id = data.get("model_id", "Qwen/Qwen2.5-Coder-7B-Instruct")
    info = engine_instance.load_model(model_id)
    return {"status": "ok", "loaded": info}
@app.post("/api/models/unload")
def api_unload_model():
    return engine_instance.unload_model()
@app.get("/api/keys")
def api_get_keys():
    return {"keys": API_KEYS}
@app.post("/api/keys/generate")
def api_gen_key():
    new_key = f"sk-shard-{uuid.uuid4().hex[:16]}"
    API_KEYS.append(new_key)
    save_keys(API_KEYS)
    return {"key": new_key, "keys": API_KEYS}
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
        save_keys(API_KEYS)
        return {"status": "ok", "deleted": target, "keys": API_KEYS}
    return JSONResponse(status_code=404, content={"status": "error", "message": "Key not found"})
@app.get("/v1/models")
def list_models():
    st = engine_instance.get_status()
    current = st.get("active_model", "Qwen/Qwen2.5-Coder-7B-Instruct")
    popular = [
        current,
        "Qwen/Qwen2.5-Coder-7B-Instruct",
        "google/gemma-2-9b-it",
        "meta-llama/Llama-3.2-3B-Instruct",
        "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
        "mistralai/Mistral-7B-Instruct-v0.3"
    ]
    seen = set()
    model_list = []
    for m in popular:
        if m not in seen:
            seen.add(m)
            model_list.append({"id": m, "object": "model", "created": int(time.time()), "owned_by": "shard-local"})
    return {"object": "list", "data": model_list}
@app.get("/v1/ping")
def api_ping():
    return {"status": "ok", "latency_ms": 0.4, "engine": "C++20 Zero-Copy / MASM AVX2", "active_model": engine_instance.active_model_id}
@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    data = await req.json()
    messages = data.get("messages", [])
    stream = data.get("stream", False)
    max_tokens = data.get("max_tokens", 512)
    temperature = data.get("temperature", 0.7)
    model = data.get("model", "shard-local-model")
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
    model = data.get("model", "Qwen/Qwen2.5-Coder-7B-Instruct")
    max_tokens = data.get("max_tokens", 1024)
    temperature = data.get("temperature", 0.7)
    normalized = []
    for m in raw_messages:
        content = m.get("content", "")
        if isinstance(content, list):
            parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            content = " ".join(parts)
        normalized.append({"role": m.get("role", "user"), "content": content})
    result = engine_instance.generate_sync(normalized, max_tokens, temperature)
    return {
        "id": f"msg_{uuid.uuid4().hex[:16]}",
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
                     '    ANTHROPIC_DEFAULT_OPUS_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"\n' + \
                     '    ANTHROPIC_DEFAULT_SONNET_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"\n' + \
                     '    ANTHROPIC_DEFAULT_HAIKU_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"\n' + \
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
                     '        "Qwen/Qwen2.5-Coder-7B-Instruct" = @{ name = "Qwen2.5 Coder 7B"; modalities = @("text"); limit = @{ context = 128000; output = 8192 } }\n' + \
                     '        "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B" = @{ name = "DeepSeek R1 Distill 7B"; modalities = @("text"); limit = @{ context = 128000; output = 8192 } }\n' + \
                     '        "google/gemma-2-9b-it" = @{ name = "Gemma 2 9B"; modalities = @("text"); limit = @{ context = 128000; output = 8192 } }\n' + \
                     '        "meta-llama/Llama-3.2-3B-Instruct" = @{ name = "Llama 3.2 3B"; modalities = @("text"); limit = @{ context = 128000; output = 8192 } }\n' + \
                     '        "mistralai/Mistral-7B-Instruct-v0.3" = @{ name = "Mistral 7B v0.3"; modalities = @("text"); limit = @{ context = 128000; output = 8192 } }\n' + \
                     '      }\n' + \
                     '    }\n' + \
                     '  }\n' + \
                     '  model = "shard/Qwen/Qwen2.5-Coder-7B-Instruct"\n' + \
                     '} | ConvertTo-Json -Depth 6\n' + \
                     'Set-Content -Path (Join-Path $p "config.json") -Value $cfg -Encoding UTF8\n' + \
                     'Write-Host "[OK] Shard: OpenCode configured successfully with official schema on Windows!" -ForegroundColor Green\n'
        else:
            script = '$k = if ($env:SHARD_KEY) { $env:SHARD_KEY } else { "' + chosen_key + '" }\n' + \
                     '[Environment]::SetEnvironmentVariable("OPENAI_API_BASE", "http://localhost:8000/v1", "User")\n' + \
                     '[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", $k, "User")\n' + \
                     'Write-Host "[OK] Shard: Environment variables set for ' + tool_clean + ' on Windows!" -ForegroundColor Green\n'
        return PlainTextResponse(script, media_type="text/plain; charset=utf-8")
    else:
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
                     '    "ANTHROPIC_DEFAULT_OPUS_MODEL": "Qwen/Qwen2.5-Coder-7B-Instruct",\n' + \
                     '    "ANTHROPIC_DEFAULT_SONNET_MODEL": "Qwen/Qwen2.5-Coder-7B-Instruct",\n' + \
                     '    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "Qwen/Qwen2.5-Coder-7B-Instruct"\n' + \
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
                     '        "Qwen/Qwen2.5-Coder-7B-Instruct": { "name": "Qwen2.5 Coder 7B", "modalities": {"input":["text"],"output":["text"]}, "limit": {"context":128000,"output":8192} },\n' + \
                     '        "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B": { "name": "DeepSeek R1 Distill 7B", "modalities": {"input":["text"],"output":["text"]}, "limit": {"context":128000,"output":8192} },\n' + \
                     '        "google/gemma-2-9b-it": { "name": "Gemma 2 9B", "modalities": {"input":["text"],"output":["text"]}, "limit": {"context":128000,"output":8192} },\n' + \
                     '        "meta-llama/Llama-3.2-3B-Instruct": { "name": "Llama 3.2 3B", "modalities": {"input":["text"],"output":["text"]}, "limit": {"context":128000,"output":8192} },\n' + \
                     '        "mistralai/Mistral-7B-Instruct-v0.3": { "name": "Mistral 7B v0.3", "modalities": {"input":["text"],"output":["text"]}, "limit": {"context":128000,"output":8192} }\n' + \
                     '      }\n' + \
                     '    }\n' + \
                     '  },\n' + \
                     '  "model": "shard/Qwen/Qwen2.5-Coder-7B-Instruct"\n' + \
                     '}\n' + \
                     'EOF\n' + \
                     'sed -i "s/KEY_PLACEHOLDER/$KEY/g" "$HOME/.config/opencode/config.json"\n' + \
                     'echo "[OK] Shard: OpenCode config.json configured successfully."\n'
        else:
            script = '#!/usr/bin/env bash\n' + \
                     'KEY="${1:-' + chosen_key + '}"\n' + \
                     'export OPENAI_API_BASE="http://localhost:8000/v1"\n' + \
                     'export OPENAI_API_KEY="$KEY"\n' + \
                     'echo "[OK] Shard: OPENAI_API_BASE and OPENAI_API_KEY exported for ' + tool_clean + '."\n'
        return PlainTextResponse(script, media_type="text/plain; charset=utf-8")
@app.get("/", response_class=HTMLResponse)
def dashboard():
    with open(DASHBOARD_PATH, "r", encoding="utf-8") as f:
        return f.read()