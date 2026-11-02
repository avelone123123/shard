import json
import time
import uuid
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from engine import engine_instance
from hardware import get_hardware_profile
from hf_hub import browse_models, fetch_model_metadata
app = FastAPI(title="Shard Local Model Engine and Server", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
API_KEYS = ["sk-shard-local-avelone-dev", "sk-shard-cursor-default"]
@app.get("/api/system/hardware")
def api_hardware():
    hw = get_hardware_profile()
    engine_st = engine_instance.get_status()
    return {"hardware": hw, "engine": engine_st}
@app.get("/api/hf/models")
def api_hf_models(query: str = None, category: str = "all", limit: int = 20):
    return browse_models(query=query, category=category, limit=limit)
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
    return {"key": new_key, "keys": API_KEYS}
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
def installer_script(tool: str = "claude", os: str = "linux", key: str = "sk-shard-local-avelone-dev"):
    tool_clean = tool.lower().strip()
    os_clean = os.lower().strip()
    if os_clean == "windows":
        if tool_clean == "claude":
            script = '$k = if ($env:SHARD_KEY) { $env:SHARD_KEY } else { "' + key + '" }\n' + \
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
            script = '$k = if ($env:SHARD_KEY) { $env:SHARD_KEY } else { "' + key + '" }\n' + \
                     '$p = Join-Path $HOME ".config\\opencode"\n' + \
                     'if (!(Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }\n' + \
                     '$cfg = @{\n' + \
                     '  openai_base_url = "http://localhost:8000/v1"\n' + \
                     '  api_key = $k\n' + \
                     '  model = "Qwen/Qwen2.5-Coder-7B-Instruct"\n' + \
                     '} | ConvertTo-Json -Depth 5\n' + \
                     'Set-Content -Path (Join-Path $p "config.json") -Value $cfg -Encoding UTF8\n' + \
                     'Write-Host "[OK] Shard: OpenCode configured successfully on Windows!" -ForegroundColor Green\n'
        else:
            script = '$k = if ($env:SHARD_KEY) { $env:SHARD_KEY } else { "' + key + '" }\n' + \
                     '[Environment]::SetEnvironmentVariable("OPENAI_API_BASE", "http://localhost:8000/v1", "User")\n' + \
                     '[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", $k, "User")\n' + \
                     'Write-Host "[OK] Shard: Environment variables set for ' + tool_clean + ' on Windows!" -ForegroundColor Green\n'
        return PlainTextResponse(script, media_type="text/plain; charset=utf-8")
    else:
        if tool_clean == "claude":
            script = '#!/usr/bin/env bash\n' + \
                     'KEY="${1:-' + key + '}"\n' + \
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
                     'KEY="${1:-' + key + '}"\n' + \
                     'mkdir -p "$HOME/.config/opencode"\n' + \
                     'cat << \'EOF\' > "$HOME/.config/opencode/config.json"\n' + \
                     '{\n' + \
                     '  "openai_base_url": "http://localhost:8000/v1",\n' + \
                     '  "api_key": "KEY_PLACEHOLDER",\n' + \
                     '  "model": "Qwen/Qwen2.5-Coder-7B-Instruct"\n' + \
                     '}\n' + \
                     'EOF\n' + \
                     'sed -i "s/KEY_PLACEHOLDER/$KEY/g" "$HOME/.config/opencode/config.json"\n' + \
                     'echo "[OK] Shard: OpenCode config.json configured successfully."\n'
        else:
            script = '#!/usr/bin/env bash\n' + \
                     'KEY="${1:-' + key + '}"\n' + \
                     'export OPENAI_API_BASE="http://localhost:8000/v1"\n' + \
                     'export OPENAI_API_KEY="$KEY"\n' + \
                     'echo "[OK] Shard: OPENAI_API_BASE and OPENAI_API_KEY exported for ' + tool_clean + '."\n'
        return PlainTextResponse(script, media_type="text/plain; charset=utf-8")
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Shard // On-Device Hugging Face Engine</title>
<style>
:root{--bg:#090a0f;--surface:#10131b;--surface-elevated:#151a26;--border:#1d2333;--border-hover:#2a334a;--accent:#3b82f6;--accent-hover:#2563eb;--emerald:#10b981;--emerald-bg:rgba(16,185,129,0.12);--amber:#f59e0b;--amber-bg:rgba(245,158,11,0.12);--purple:#8b5cf6;--purple-bg:rgba(139,92,246,0.12);--text:#f1f5f9;--text-muted:#8492a6;--font:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:var(--font);min-height:100vh;display:flex;flex-direction:column;-webkit-font-smoothing:antialiased;}
header{background:#0c0e14;border-bottom:1px solid var(--border);padding:14px 28px;display:flex;justify-content:space-between;align-items:center;}
.brand{display:flex;align-items:center;gap:12px;}
.brand-badge{background:#1e293b;border:1px solid #334155;color:#f8fafc;font-weight:700;font-size:12px;letter-spacing:1px;padding:4px 9px;border-radius:5px;}
.brand-name{font-size:15px;font-weight:600;color:#fff;}
.brand-desc{font-size:12px;color:var(--text-muted);}
.hw-bar{display:flex;gap:20px;align-items:center;background:var(--surface);padding:7px 16px;border-radius:6px;border:1px solid var(--border);}
.hw-item{display:flex;flex-direction:column;}
.hw-item span{color:var(--text-muted);font-size:10px;text-transform:uppercase;letter-spacing:0.5px;}
.hw-item b{font-size:12px;color:#fff;font-weight:500;margin-top:1px;}
.nav-tabs{display:flex;gap:4px;padding:0 28px;background:#0c0e14;border-bottom:1px solid var(--border);}
.nav-btn{background:transparent;border:none;border-bottom:2px solid transparent;color:var(--text-muted);padding:11px 16px;font-size:13px;cursor:pointer;display:flex;align-items:center;gap:7px;transition:all 0.15s;}
.nav-btn.active{color:#fff;border-bottom-color:var(--accent);font-weight:500;}
.nav-btn:hover:not(.active){color:#cbd5e1;}
main{flex:1;padding:24px 28px;max-width:1440px;margin:0 auto;width:100%;}
.section{display:none;}
.section.active{display:block;}
.search-bar{display:flex;gap:12px;margin-bottom:20px;}
.search-wrapper{flex:1;position:relative;}
.search-icon{position:absolute;left:14px;top:11px;color:var(--text-muted);}
.search-input{width:100%;background:var(--surface);border:1px solid var(--border);color:#fff;padding:9px 12px 9px 38px;border-radius:6px;font-size:13px;outline:none;transition:border-color 0.15s;}
.search-input:focus{border-color:var(--accent);}
.filter-tabs{display:flex;gap:6px;}
.filter-btn{background:var(--surface);border:1px solid var(--border);color:var(--text-muted);padding:0 14px;border-radius:6px;font-size:12px;cursor:pointer;transition:all 0.15s;}
.filter-btn.active{background:#1e293b;color:#fff;border-color:#334155;font-weight:500;}
.models-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:14px;}
.model-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px;display:flex;flex-direction:column;justify-content:space-between;transition:border-color 0.15s,background 0.15s;}
.model-card:hover{border-color:var(--border-hover);background:var(--surface-elevated);}
.card-top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;}
.model-title{font-size:14px;font-weight:600;color:#fff;letter-spacing:0.2px;}
.model-author{font-size:11px;color:var(--text-muted);margin-top:2px;}
.compat-badge{font-size:11px;font-weight:600;padding:3px 8px;border-radius:4px;border:1px solid transparent;white-space:nowrap;}
.card-desc{font-size:12px;color:#94a3b8;line-height:1.5;margin-bottom:12px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
.card-specs{display:flex;gap:14px;font-size:11px;color:var(--text-muted);margin-bottom:14px;padding:8px 10px;background:#0c0e14;border-radius:5px;}
.spec-item{display:flex;align-items:center;gap:5px;}
.card-bottom{display:flex;justify-content:space-between;align-items:center;padding-top:10px;border-top:1px solid var(--border);}
.btn-action{background:#1e293b;border:1px solid #334155;color:#f8fafc;padding:6px 14px;border-radius:5px;font-size:12px;font-weight:500;cursor:pointer;display:inline-flex;align-items:center;gap:6px;transition:all 0.15s;}
.btn-action:hover{background:#2a374a;border-color:#475569;}
.btn-action.active{background:var(--emerald);border-color:var(--emerald);color:#041f14;font-weight:600;}
.connect-container{display:grid;grid-template-columns:1fr 340px;gap:24px;align-items:start;}
.connect-main{display:flex;flex-direction:column;gap:18px;}
.step-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;}
.step-title{font-size:13px;font-weight:600;color:#94a3b8;margin-bottom:12px;display:flex;align-items:center;gap:8px;}
.step-badge{background:#1e293b;color:#38bdf8;font-size:11px;padding:2px 7px;border-radius:4px;font-weight:600;}
.tools-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;}
.tool-btn{background:#0c0e14;border:1px solid var(--border);border-radius:6px;padding:12px;cursor:pointer;text-align:left;transition:all 0.15s;display:flex;flex-direction:column;gap:4px;}
.tool-btn:hover{border-color:#334155;background:#141824;}
.tool-btn.active{border-color:#38bdf8;background:#101a2c;}
.tool-name{font-size:13px;font-weight:600;color:#fff;display:flex;align-items:center;gap:6px;}
.tool-desc{font-size:11px;color:#8492a6;}
.os-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;}
.os-btn{background:#0c0e14;border:1px solid var(--border);border-radius:6px;padding:10px 14px;cursor:pointer;display:flex;align-items:center;gap:8px;font-size:12px;color:#cbd5e1;transition:all 0.15s;}
.os-btn:hover{border-color:#334155;}
.os-btn.active{border-color:#38bdf8;background:#101a2c;color:#fff;font-weight:600;}
.mode-switch{display:flex;gap:4px;background:#0c0e14;padding:3px;border-radius:6px;border:1px solid var(--border);width:fit-content;margin-bottom:14px;}
.mode-btn{background:transparent;border:none;color:#8492a6;padding:6px 14px;font-size:12px;border-radius:4px;cursor:pointer;}
.mode-btn.active{background:#1e293b;color:#fff;font-weight:500;}
.script-box{background:#090a0f;border:1px solid var(--border);border-radius:6px;padding:14px;font-family:Consolas,monospace;font-size:12px;color:#38bdf8;word-break:break-all;margin-bottom:12px;user-select:all;}
.btn-primary-action{background:#2563eb;border:none;color:#fff;padding:10px 20px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:8px;transition:background 0.15s;}
.btn-primary-action:hover{background:#1d4ed8;}
.guide-steps{margin-top:16px;font-size:12px;color:#94a3b8;line-height:1.8;}
.guide-steps ol{padding-left:18px;margin-bottom:8px;}
.guide-note{font-size:11px;color:#64748b;margin-top:8px;}
.ping-box{background:#0c0e14;border:1px solid var(--border);border-radius:6px;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;}
.sidebar-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:18px;margin-bottom:16px;}
.sidebar-title{font-size:13px;font-weight:600;color:#fff;margin-bottom:12px;display:flex;justify-content:space-between;align-items:center;}
.ep-item{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;background:#0c0e14;border:1px solid #1a2233;border-radius:5px;margin-bottom:8px;font-family:Consolas,monospace;font-size:11px;}
.ep-label{color:#8492a6;text-transform:uppercase;font-size:10px;}
.ep-val{color:#cbd5e1;word-break:break-all;}
.ep-copy{background:transparent;border:none;color:#38bdf8;cursor:pointer;padding:2px 6px;display:flex;align-items:center;}
.model-item{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;background:#0c0e14;border:1px solid #1a2233;border-radius:5px;margin-bottom:8px;font-size:12px;}
.model-item:hover{border-color:#334155;}
.chat-container{display:flex;flex-direction:column;height:680px;background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden;}
.chat-header{padding:10px 18px;background:#0c0e14;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;font-size:12px;color:var(--text-muted);}
.chat-header-model{display:flex;align-items:center;gap:8px;color:#fff;font-weight:500;}
.chat-header-live{display:flex;align-items:center;gap:14px;font-family:Consolas,monospace;}
.chat-body{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:18px;}
.msg-wrapper{display:flex;flex-direction:column;gap:6px;}
.msg-user{align-self:flex-end;max-width:70%;background:#1e293b;border:1px solid #334155;color:#fff;padding:10px 16px;border-radius:8px 8px 2px 8px;font-size:13px;line-height:1.5;}
.msg-assistant{align-self:flex-start;max-width:88%;width:100%;background:var(--surface-elevated);border:1px solid var(--border);border-radius:8px 8px 8px 2px;padding:16px;display:flex;flex-direction:column;gap:12px;}
.think-panel{background:#0c0e14;border:1px solid #1e2638;border-radius:6px;overflow:hidden;transition:all 0.2s;}
.think-header{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;cursor:pointer;user-select:none;background:#0e111a;}
.think-header:hover{background:#141824;}
.think-title{font-size:11px;font-weight:600;color:#94a3b8;display:flex;align-items:center;gap:6px;}
.think-pulse{width:7px;height:7px;border-radius:50%;background:#38bdf8;box-shadow:0 0 8px #38bdf8;animation:pulse 1.2s infinite ease-in-out;}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1);}50%{opacity:0.3;transform:scale(0.85);}}
.think-content{padding:10px 12px;font-size:12px;color:#8492a6;line-height:1.6;font-family:Consolas,monospace;white-space:pre-wrap;border-top:1px solid #19202f;max-height:220px;overflow-y:auto;}
.msg-content{font-size:13px;color:#f1f5f9;line-height:1.6;white-space:pre-wrap;}
.msg-content pre{background:#090a0f;border:1px solid var(--border);padding:10px 12px;border-radius:5px;overflow-x:auto;font-family:Consolas,monospace;margin:10px 0;font-size:12px;color:#38bdf8;}
.telemetry-row{display:flex;flex-wrap:wrap;gap:16px;align-items:center;padding-top:10px;border-top:1px solid #1a2133;font-size:11px;color:#718096;font-family:Consolas,monospace;}
.telemetry-item{display:flex;align-items:center;gap:5px;}
.telemetry-item b{color:#cbd5e1;font-weight:600;}
.chat-footer{display:flex;gap:10px;padding:14px;border-top:1px solid var(--border);background:#0c0e14;}
.chat-input{flex:1;background:var(--surface);border:1px solid var(--border);color:#fff;padding:9px 14px;border-radius:6px;font-size:13px;outline:none;transition:border-color 0.15s;}
.chat-input:focus{border-color:var(--accent);}
.toast{position:fixed;top:20px;right:24px;background:#151a26;border:1px solid #334155;color:#f8fafc;padding:12px 18px;border-radius:6px;font-size:12px;box-shadow:0 8px 24px rgba(0,0,0,0.5);transform:translateY(-10px);opacity:0;pointer-events:none;transition:all 0.25s cubic-bezier(0.16,1,0.3,1);z-index:9999;display:flex;align-items:center;gap:10px;}
.toast.visible{transform:translateY(0);opacity:1;pointer-events:auto;}
.toast-title{font-size:13px;font-weight:600;color:#fff;}
.toast-sub{font-size:11px;color:var(--text-muted);margin-top:2px;}
</style>
</head>
<body>
<div id="toast" class="toast"></div>
<header>
<div class="brand">
<div class="brand-badge">SHARD</div>
<div>
<div class="brand-name">Shard Engine Workspace</div>
<div class="brand-desc">On-Device Hugging Face Model Runtime &amp; OpenAI Server</div>
</div>
</div>
<div class="hw-bar">
<div class="hw-item"><span>ЯДРО</span><b id="hwCore">C++20 / MASM AVX2</b></div>
<div class="hw-item"><span>GPU</span><b id="hwGpu">Поиск...</b></div>
<div class="hw-item"><span>VRAM</span><b id="hwVram">0 / 0 MB</b></div>
<div class="hw-item"><span>RAM</span><b id="hwRam">0 / 0 GB</b></div>
<div class="hw-item"><span>МОДЕЛЬ</span><b id="hwModel" style="color:#10b981;">Не загружена</b></div>
</div>
</header>
<div class="nav-tabs">
<button class="nav-btn active" onclick="showTab('market', this)">
<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
Каталог Hugging Face
</button>
<button class="nav-btn" onclick="showTab('connect', this)">
<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
Инструкции и Подключение
</button>
<button class="nav-btn" onclick="showTab('chat', this)">
<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
Чат-песочница
</button>
<button class="nav-btn" onclick="showTab('keys', this)">
<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6"/><path d="m15.5 7.5 3 3"/></svg>
API Ключи
</button>
</div>
<main>
<div id="tab-market" class="section active">
<div class="search-bar">
<div class="search-wrapper">
<svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
<input type="text" id="searchInput" class="search-input" placeholder="Поиск модели на Hugging Face (например: gemma, qwen, deepseek, mistral)..." onkeyup="handleSearch(event)">
</div>
<div class="filter-tabs">
<button class="filter-btn active" onclick="setCategory('all', this)">Все</button>
<button class="filter-btn" onclick="setCategory('coding', this)">Кодинг</button>
<button class="filter-btn" onclick="setCategory('reasoning', this)">Рассуждения</button>
<button class="filter-btn" onclick="setCategory('compact', this)">Компактные (VRAM)</button>
<button class="filter-btn" onclick="setCategory('general', this)">Универсальные</button>
</div>
</div>
<div class="models-grid" id="modelsGrid"></div>
</div>
<div id="tab-connect" class="section">
<div class="connect-container">
<div class="connect-main">
<div class="step-card">
<div class="step-title"><span class="step-badge">ШАГ 1</span> Выберите инструмент для интеграции</div>
<div class="tools-grid">
<div class="tool-btn active" onclick="selectTool('claude', this)">
<div class="tool-name">
<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
Claude CLI
</div>
<div class="tool-desc">Anthropic-совместимый терминал</div>
</div>
<div class="tool-btn" onclick="selectTool('claude-desktop', this)">
<div class="tool-name">
<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
Claude Desktop
</div>
<div class="tool-desc">Официальный клиент Anthropic</div>
</div>
<div class="tool-btn" onclick="selectTool('cursor', this)">
<div class="tool-name">
<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2"><polygon points="3 3 10 21 14 14 21 10 3 3"/></svg>
Cursor IDE
</div>
<div class="tool-desc">OpenAI Custom Gateway</div>
</div>
<div class="tool-btn" onclick="selectTool('opencode', this)">
<div class="tool-name">
<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
OpenCode
</div>
<div class="tool-desc">Автономный кодинг-агент</div>
</div>
<div class="tool-btn" onclick="selectTool('continue', this)">
<div class="tool-name">
<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#8b5cf6" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
Continue
</div>
<div class="tool-desc">VS Code / JetBrains плагин</div>
</div>
<div class="tool-btn" onclick="selectTool('aider', this)">
<div class="tool-name">
<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#ec4899" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
Aider CLI
</div>
<div class="tool-desc">Терминальный парный программист</div>
</div>
<div class="tool-btn" onclick="selectTool('zed', this)">
<div class="tool-name">
<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2"><path d="M4 4h16l-16 16h16"/></svg>
Zed Editor
</div>
<div class="tool-desc">OpenAI-совместимый провайдер</div>
</div>
<div class="tool-btn" onclick="selectTool('jetbrains', this)">
<div class="tool-name">
<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#f97316" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 17l10-10"/></svg>
JetBrains AI
</div>
<div class="tool-desc">Custom OpenAI endpoint</div>
</div>
<div class="tool-btn" onclick="selectTool('sdk', this)">
<div class="tool-name">
<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#cbd5e1" stroke-width="2"><path d="M16 18l6-6-6-6"/><path d="M8 6l-6 6 6 6"/></svg>
API / SDK
</div>
<div class="tool-desc">Python, TypeScript, cURL</div>
</div>
</div>
</div>
<div class="step-card">
<div class="step-title"><span class="step-badge">ШАГ 2</span> Операционная система</div>
<div class="os-grid">
<div class="os-btn active" onclick="selectOS('windows', this)">
<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M0 3.449L9.75 2.1v9.451H0m10.949-9.602L24 0v11.4H10.949M0 12.6h9.75v9.451L0 20.699M10.949 12.6H24V24l-12.9-1.801"/></svg>
Windows
</div>
<div class="os-btn" onclick="selectOS('linux', this)">
<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/></svg>
Linux
</div>
<div class="os-btn" onclick="selectOS('macos', this)">
<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.81-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M15.97 6.37c.61-.75 1.04-1.8 1.01-2.87-.96.04-2.1.65-2.76 1.42-.58.67-1.09 1.74-1 2.8 1.08.08 2.14-.6 2.75-1.35z"/></svg>
macOS
</div>
<div class="os-btn" onclick="selectOS('manual', this)">
<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>
Ручной config
</div>
</div>
</div>
<div class="step-card">
<div class="step-title">
<span class="step-badge">ШАГ 3</span>
<span id="guideTitle">Инструкция: Claude CLI + Windows</span>
</div>
<div class="mode-switch">
<button id="modeBtnScript" class="mode-btn active" onclick="setGuideMode('script')">Скрипт (в 1 клик)</button>
<button id="modeBtnManual" class="mode-btn" onclick="setGuideMode('manual')">Вручную (конфиг)</button>
</div>
<div id="guideScriptSection">
<div class="script-box" id="guideScriptCode"></div>
<button class="btn-primary-action" onclick="copyCurrentScript()">
<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
Скопировать команду
</button>
<div class="guide-steps">
<div style="font-weight:600;color:#cbd5e1;margin-bottom:4px;">Как открыть Терминал и выполнить:</div>
<ol id="guideScriptSteps">
<li>Откройте Терминал: Нажмите <b>Win + X</b>, выберите <b>Терминал</b> или <b>PowerShell</b>.</li>
<li>Вставьте скопированную команду комбинацией <b>Ctrl + V</b> и нажмите <b>Enter</b>.</li>
<li>Готово. Настройки применятся автоматически, локальные модели Shard сразу появятся в CLI.</li>
</ol>
<div class="guide-note">Установщик автоматически прописывает локальный endpoint Shard (http://localhost:8000/v1) и текущий API-ключ.</div>
</div>
</div>
<div id="guideManualSection" style="display:none;">
<div id="guideManualContent"></div>
</div>
</div>
<div class="step-card">
<div class="step-title"><span class="step-badge">ШАГ 4</span> Проверка локального соединения</div>
<div class="ping-box">
<div>
<div style="font-size:13px;font-weight:600;color:#fff;">Тест доступности API</div>
<div id="pingStatusText" style="font-size:12px;color:var(--text-muted);margin-top:2px;">Нажмите кнопку для проверки соединения с C++20 ядром Shard</div>
</div>
<button class="btn-action" onclick="runPingTest()">
<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
Проверить соединение
</button>
</div>
</div>
</div>
<div class="connect-sidebar">
<div class="sidebar-card">
<div class="sidebar-title">
<span>Параметры эндпоинта</span>
<button class="btn-action" style="padding:2px 8px;font-size:11px;" onclick="copyAllEndpoints()">Скопировать всё</button>
</div>
<div class="ep-item">
<div>
<div class="ep-label">Base Endpoint</div>
<div class="ep-val" id="epBase">http://localhost:8000/v1</div>
</div>
<button class="ep-copy" onclick="copyValue('http://localhost:8000/v1', 'Base Endpoint')"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>
</div>
<div class="ep-item">
<div>
<div class="ep-label">Models Endpoint</div>
<div class="ep-val">http://localhost:8000/v1/models</div>
</div>
<button class="ep-copy" onclick="copyValue('http://localhost:8000/v1/models', 'Models Endpoint')"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>
</div>
<div class="ep-item">
<div>
<div class="ep-label">Chat Endpoint</div>
<div class="ep-val">http://localhost:8000/v1/chat/completions</div>
</div>
<button class="ep-copy" onclick="copyValue('http://localhost:8000/v1/chat/completions', 'Chat Endpoint')"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>
</div>
<div class="ep-item">
<div>
<div class="ep-label">Claude Messages</div>
<div class="ep-val">http://localhost:8000/v1/messages</div>
</div>
<button class="ep-copy" onclick="copyValue('http://localhost:8000/v1/messages', 'Claude Messages')"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>
</div>
<div class="ep-item">
<div>
<div class="ep-label">Claude Count Tokens</div>
<div class="ep-val">http://localhost:8000/v1/messages/count_tokens</div>
</div>
<button class="ep-copy" onclick="copyValue('http://localhost:8000/v1/messages/count_tokens', 'Claude Tokens')"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>
</div>
<div class="ep-item">
<div>
<div class="ep-label">API Key</div>
<div class="ep-val" id="sidebarApiKey" style="color:#10b981;">sk-shard-local-avelone-dev</div>
</div>
<button class="ep-copy" onclick="copyValue('sk-shard-local-avelone-dev', 'API Key')"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>
</div>
</div>
<div class="sidebar-card">
<div class="sidebar-title">
<span>Доступные модели</span>
<span style="font-size:11px;color:#10b981;">Локально</span>
</div>
<div class="model-item">
<div>
<div style="font-weight:600;color:#fff;">Qwen2.5-Coder-7B</div>
<div style="font-size:10px;color:var(--text-muted);">Qwen/Qwen2.5-Coder-7B-Instruct</div>
</div>
<button class="btn-action" style="padding:2px 8px;font-size:11px;" onclick="copyValue('Qwen/Qwen2.5-Coder-7B-Instruct', 'ID модели')">Копировать</button>
</div>
<div class="model-item">
<div>
<div style="font-weight:600;color:#fff;">Gemma-2-9B</div>
<div style="font-size:10px;color:var(--text-muted);">google/gemma-2-9b-it</div>
</div>
<button class="btn-action" style="padding:2px 8px;font-size:11px;" onclick="copyValue('google/gemma-2-9b-it', 'ID модели')">Копировать</button>
</div>
<div class="model-item">
<div>
<div style="font-weight:600;color:#fff;">DeepSeek-R1-Distill-7B</div>
<div style="font-size:10px;color:var(--text-muted);">deepseek-ai/DeepSeek-R1-Distill-Qwen-7B</div>
</div>
<button class="btn-action" style="padding:2px 8px;font-size:11px;" onclick="copyValue('deepseek-ai/DeepSeek-R1-Distill-Qwen-7B', 'ID модели')">Копировать</button>
</div>
<div class="model-item">
<div>
<div style="font-weight:600;color:#fff;">Llama-3.2-3B</div>
<div style="font-size:10px;color:var(--text-muted);">meta-llama/Llama-3.2-3B-Instruct</div>
</div>
<button class="btn-action" style="padding:2px 8px;font-size:11px;" onclick="copyValue('meta-llama/Llama-3.2-3B-Instruct', 'ID модели')">Копировать</button>
</div>
<div class="model-item">
<div>
<div style="font-weight:600;color:#fff;">Mistral-7B-v0.3</div>
<div style="font-size:10px;color:var(--text-muted);">mistralai/Mistral-7B-Instruct-v0.3</div>
</div>
<button class="btn-action" style="padding:2px 8px;font-size:11px;" onclick="copyValue('mistralai/Mistral-7B-Instruct-v0.3', 'ID модели')">Копировать</button>
</div>
</div>
</div>
</div>
</div>
<div id="tab-chat" class="section">
<div class="chat-container">
<div class="chat-header">
<div class="chat-header-model">
<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2"><circle cx="12" cy="12" r="10"/><polygon points="10 8 16 12 10 16 10 8"/></svg>
<span id="chatActiveModel">Qwen2.5-Coder-7B-Instruct</span>
</div>
<div class="chat-header-live">
<span id="chatCoreBadge">C++20 Zero-Copy / AVX2 ASM</span>
<span id="chatLiveSpeed" style="color:#38bdf8;">Готов к запросам</span>
</div>
</div>
<div class="chat-body" id="chatBody">
<div class="msg-wrapper">
<div class="msg-assistant">
<div class="msg-content">Локальный движок Shard готов к работе. Напишите задачу или вопрос в поле ниже. Вы увидите ход размышлений модели, время обдумывания, живую скорость токенов и подробный отчет по утилизации ресурсов.</div>
</div>
</div>
</div>
<div class="chat-footer">
<input type="text" id="chatInput" class="chat-input" placeholder="Напишите задачу, вопрос или запросите код..." onkeypress="if(event.key==='Enter')sendChat()">
<button id="chatSendBtn" class="btn-action" onclick="sendChat()">
<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
Отправить
</button>
</div>
</div>
</div>
<div id="tab-keys" class="section">
<div class="info-card">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
<div>
<h3>Локальные API Ключи</h3>
<p style="margin:0;">Ключи для авторизации Cursor, OpenCode, Claude Code и внешних клиентов.</p>
</div>
<button class="btn-action" onclick="generateKey()">+ Создать новый ключ</button>
</div>
<div id="keysList" style="display:flex;flex-direction:column;gap:8px;"></div>
</div>
</div>
</main>
<script>
let currentCategory = 'all';
let activeModelId = 'Qwen/Qwen2.5-Coder-7B-Instruct';
let currentTool = 'claude';
let currentOS = 'windows';
let currentGuideMode = 'script';
let toastTimeout = null;
let isChatStreaming = false;
const activeApiKey = 'sk-shard-local-avelone-dev';
function showToast(title, sub) {
    const el = document.getElementById('toast');
    if (toastTimeout) clearTimeout(toastTimeout);
    el.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg><div><div class="toast-title">' + title + '</div><div class="toast-sub">' + sub + '</div></div>';
    el.classList.add('visible');
    toastTimeout = setTimeout(() => { el.classList.remove('visible'); }, 3200);
}
function copyValue(val, label) {
    navigator.clipboard.writeText(val);
    showToast('Скопировано', label + ' скопирован в буфер обмена');
}
function copyAllEndpoints() {
    const all = 'BASE_URL: http://localhost:8000/v1\nAPI_KEY: ' + activeApiKey + '\nMODEL: ' + activeModelId;
    navigator.clipboard.writeText(all);
    showToast('Скопировано всё', 'Все параметры эндпоинта скопированы');
}
function showTab(id, btn) {
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-' + id).classList.add('active');
    btn.classList.add('active');
    if (id === 'connect') updateGuide();
    if (id === 'keys') loadKeys();
}
function setCategory(cat, btn) {
    currentCategory = cat;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    loadModels();
}
function handleSearch(e) {
    if (e.key === 'Enter') loadModels();
}
function selectTool(tool, el) {
    currentTool = tool;
    document.querySelectorAll('.tool-btn').forEach(b => b.classList.remove('active'));
    el.classList.add('active');
    updateGuide();
}
function selectOS(os, el) {
    currentOS = os;
    document.querySelectorAll('.os-btn').forEach(b => b.classList.remove('active'));
    el.classList.add('active');
    if (os === 'manual') {
        setGuideMode('manual');
    } else {
        updateGuide();
    }
}
function setGuideMode(mode) {
    currentGuideMode = mode;
    document.getElementById('modeBtnScript').classList.toggle('active', mode === 'script');
    document.getElementById('modeBtnManual').classList.toggle('active', mode === 'manual');
    document.getElementById('guideScriptSection').style.display = (mode === 'script') ? 'block' : 'none';
    document.getElementById('guideManualSection').style.display = (mode === 'manual') ? 'block' : 'none';
    updateGuide();
}
function updateGuide() {
    const toolNames = {
        'claude': 'Claude CLI',
        'claude-desktop': 'Claude Desktop',
        'cursor': 'Cursor IDE',
        'opencode': 'OpenCode',
        'continue': 'Continue',
        'aider': 'Aider CLI',
        'zed': 'Zed Editor',
        'jetbrains': 'JetBrains AI',
        'sdk': 'API / SDK'
    };
    const osNames = {'windows': 'Windows', 'linux': 'Linux', 'macos': 'macOS', 'manual': 'Ручной config'};
    document.getElementById('guideTitle').innerText = 'Инструкция: ' + (toolNames[currentTool] || currentTool) + ' + ' + (osNames[currentOS] || currentOS);
    const scriptBox = document.getElementById('guideScriptCode');
    const stepsList = document.getElementById('guideScriptSteps');
    if (currentOS === 'windows') {
        if (currentTool === 'claude') {
            scriptBox.innerText = '$env:SHARD_KEY="' + activeApiKey + '"; irm "http://localhost:8000/i?os=windows&tool=claude" | iex';
        } else if (currentTool === 'opencode') {
            scriptBox.innerText = '$env:SHARD_KEY="' + activeApiKey + '"; irm "http://localhost:8000/i?os=windows&tool=opencode" | iex';
        } else if (currentTool === 'cursor') {
            scriptBox.innerText = '$env:SHARD_KEY="' + activeApiKey + '"; irm "http://localhost:8000/i?os=windows&tool=cursor" | iex';
        } else {
            scriptBox.innerText = '$env:SHARD_KEY="' + activeApiKey + '"; irm "http://localhost:8000/i?os=windows&tool=' + currentTool + '" | iex';
        }
        stepsList.innerHTML = '<li>Откройте Терминал: Нажмите <b>Win + X</b>, выберите <b>Терминал</b> или <b>PowerShell</b>.</li>' +
            '<li>Вставьте команду комбинацией <b>Ctrl + V</b> и нажмите <b>Enter</b>.</li>' +
            '<li>Готово. Локальный endpoint Shard подключен, модели появятся в приложении.</li>';
    } else {
        const toolArg = (currentTool === 'claude-desktop') ? 'claude' : currentTool;
        scriptBox.innerText = 'bash <(curl -fsSL "http://localhost:8000/i?tool=' + toolArg + '") ' + activeApiKey;
        stepsList.innerHTML = '<li>Откройте Терминал: Нажмите <b>Ctrl + Alt + T</b> (Linux) или <b>Cmd + Пробел -> Terminal</b> (macOS).</li>' +
            '<li>Вставьте команду комбинацией <b>Ctrl + Shift + V</b> (или <b>Cmd + V</b>) и нажмите <b>Enter</b>.</li>' +
            '<li>Готово. Конфигурация применена к окружению без перезагрузки системы.</li>';
    }
    const manualBox = document.getElementById('guideManualContent');
    if (currentTool === 'claude' || currentTool === 'claude-desktop') {
        const filePath = (currentOS === 'windows') ? '%USERPROFILE%\\.claude\\settings.json' : '~/.claude/settings.json';
        manualBox.innerHTML = '<div class="guide-steps">' +
            '<div style="font-weight:600;color:#cbd5e1;margin-bottom:6px;">1. Путь к файлу конфигурации:</div>' +
            '<div class="script-box" style="margin-bottom:12px;">' + filePath + '</div>' +
            '<div style="font-weight:600;color:#cbd5e1;margin-bottom:6px;">2. Создайте или замените settings.json блоком ниже:</div>' +
            '<div class="code-panel" style="margin-bottom:12px;"><pre id="codeClaudeManual">{\n  "hasCompletedOnboarding": true,\n  "env": {\n    "ANTHROPIC_BASE_URL": "http://localhost:8000/v1",\n    "ANTHROPIC_AUTH_TOKEN": "' + activeApiKey + '",\n    "ANTHROPIC_DEFAULT_OPUS_MODEL": "' + activeModelId + '",\n    "ANTHROPIC_DEFAULT_SONNET_MODEL": "' + activeModelId + '",\n    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "' + activeModelId + '"\n  }\n}</pre><button class="btn-copy" onclick="copyText(\'codeClaudeManual\', \'Конфигурация Claude скопирована\')">Скопировать</button></div>' +
            '<div style="font-weight:600;color:#cbd5e1;margin-bottom:4px;">3. Перезапустите Claude CLI / Desktop:</div>' +
            '<div>Claude читает ANTHROPIC_BASE_URL и ANTHROPIC_AUTH_TOKEN из settings.json. Endpoint единый — отдаёт и Claude, и OpenAI совместимые модели через локальный C++20 / AVX2 пайплайн.</div>' +
        '</div>';
    } else if (currentTool === 'cursor') {
        manualBox.innerHTML = '<div class="guide-steps">' +
            '<div style="font-weight:600;color:#cbd5e1;margin-bottom:6px;">1. Откройте настройки Cursor:</div>' +
            '<div>Нажмите <b>Ctrl + ,</b> -> выберите раздел <b>Models</b> -> включите <b>OpenAI API Key</b>.</div>' +
            '<div style="font-weight:600;color:#cbd5e1;margin:10px 0 6px;">2. Укажите локальный адрес и ключ Shard:</div>' +
            '<div class="code-panel" style="margin-bottom:12px;"><pre id="codeCursorManual">Base URL: http://localhost:8000/v1\nAPI Key:  ' + activeApiKey + '\nModel:    ' + activeModelId + '</pre><button class="btn-copy" onclick="copyText(\'codeCursorManual\', \'Настройки Cursor скопированы\')">Скопировать</button></div>' +
            '<div style="font-weight:600;color:#cbd5e1;margin-bottom:4px;">3. Сохраните настройки:</div>' +
            '<div>Cursor будет отправлять запросы напрямую на локальный сервер Shard без задержек сети.</div>' +
        '</div>';
    } else if (currentTool === 'opencode') {
        const filePath = (currentOS === 'windows') ? '%USERPROFILE%\\.config\\opencode\\config.json' : '~/.config/opencode/config.json';
        manualBox.innerHTML = '<div class="guide-steps">' +
            '<div style="font-weight:600;color:#cbd5e1;margin-bottom:6px;">1. Путь к конфигу OpenCode:</div>' +
            '<div class="script-box" style="margin-bottom:12px;">' + filePath + '</div>' +
            '<div style="font-weight:600;color:#cbd5e1;margin-bottom:6px;">2. Вставьте блок настроек:</div>' +
            '<div class="code-panel" style="margin-bottom:12px;"><pre id="codeOpenCodeManual">{\n  "openai_base_url": "http://localhost:8000/v1",\n  "api_key": "' + activeApiKey + '",\n  "model": "' + activeModelId + '"\n}</pre><button class="btn-copy" onclick="copyText(\'codeOpenCodeManual\', \'Конфиг OpenCode скопирован\')">Скопировать</button></div>' +
            '<div>OpenCode подключится к локальному пайплайну Shard для автономного кодинга.</div>' +
        '</div>';
    } else {
        manualBox.innerHTML = '<div class="guide-steps">' +
            '<div style="font-weight:600;color:#cbd5e1;margin-bottom:6px;">Переменные окружения (Environment Variables):</div>' +
            '<div class="code-panel" style="margin-bottom:12px;"><pre id="codeEnvManual">export OPENAI_API_BASE="http://localhost:8000/v1"\nexport OPENAI_API_KEY="' + activeApiKey + '"</pre><button class="btn-copy" onclick="copyText(\'codeEnvManual\', \'Команды env скопированы\')">Скопировать</button></div>' +
            '<div>Любой совместимый инструмент подтянет локальный сервер автоматически.</div>' +
        '</div>';
    }
}
function copyCurrentScript() {
    const text = document.getElementById('guideScriptCode').innerText;
    navigator.clipboard.writeText(text);
    showToast('Команда скопирована', 'Вставьте команду в Терминал и нажмите Enter');
}
function copyText(id, toastMsg) {
    const text = document.getElementById(id).innerText;
    navigator.clipboard.writeText(text);
    showToast('Скопировано', toastMsg);
}
async function runPingTest() {
    const el = document.getElementById('pingStatusText');
    el.innerHTML = '<span style="color:#38bdf8;">Выполняется проверка соединения...</span>';
    try {
        const t0 = performance.now();
        const res = await fetch('/v1/ping');
        const dt = (performance.now() - t0).toFixed(1);
        const data = await res.json();
        el.innerHTML = '<span style="color:#10b981;font-weight:600;">200 OK — Эндпоинт активен!</span> Задержка: <b>' + dt + ' мс</b> | Ядро: <b>' + data.engine + '</b>';
        showToast('Эндпоинт доступен', 'Отклик: ' + dt + ' мс');
    } catch(e) {
        el.innerHTML = '<span style="color:#ef4444;font-weight:600;">Ошибка соединения</span> Проверьте запущен ли локальный сервер Shard';
    }
}
async function fetchHardware() {
    try {
        const res = await fetch('/api/system/hardware');
        const data = await res.json();
        const hw = data.hardware;
        const eng = data.engine;
        document.getElementById('hwGpu').innerText = hw.gpu_name.replace(' Laptop GPU', '').replace('NVIDIA GeForce ', '');
        document.getElementById('hwVram').innerText = hw.vram_used_mb + ' / ' + hw.vram_total_mb + ' MB';
        document.getElementById('hwRam').innerText = hw.ram_used_gb + ' / ' + hw.ram_total_gb + ' GB';
        if (eng.active_model) {
            activeModelId = eng.active_model;
            const shortName = eng.active_model.split('/').pop();
            document.getElementById('hwModel').innerText = shortName;
            const chatModelEl = document.getElementById('chatActiveModel');
            if (chatModelEl) chatModelEl.innerText = shortName;
        }
    } catch(e) {}
}
async function loadModels() {
    const q = document.getElementById('searchInput').value.trim();
    const grid = document.getElementById('modelsGrid');
    try {
        const res = await fetch('/api/hf/models?category=' + currentCategory + (q ? '&query=' + encodeURIComponent(q) : ''));
        const models = await res.json();
        grid.innerHTML = '';
        models.forEach(m => {
            const card = document.createElement('div');
            card.className = 'model-card';
            let badgeBg = 'var(--amber-bg)';
            let badgeColor = 'var(--amber)';
            let badgeBorder = 'rgba(245,158,11,0.25)';
            if (m.compat_badge.includes('VRAM)')) {
                badgeBg = 'var(--emerald-bg)';
                badgeColor = 'var(--emerald)';
                badgeBorder = 'rgba(16,185,129,0.25)';
            } else if (m.compat_badge.includes('SSD')) {
                badgeBg = 'var(--purple-bg)';
                badgeColor = 'var(--purple)';
                badgeBorder = 'rgba(139,92,246,0.25)';
            }
            const isActive = (m.id === activeModelId);
            const btnClass = isActive ? 'btn-action active' : 'btn-action';
            const btnText = isActive ? 'АКТИВНА' : 'Запустить';
            card.innerHTML = '<div class="card-top">' +
                '<div>' +
                    '<div class="model-title">' + m.name + '</div>' +
                    '<div class="model-author">' + m.author + '</div>' +
                '</div>' +
                '<div class="compat-badge" style="background:' + badgeBg + ';color:' + badgeColor + ';border-color:' + badgeBorder + ';">' + m.compat_badge + '</div>' +
            '</div>' +
            '<div class="card-desc">' + m.description + '</div>' +
            '<div class="card-specs">' +
                '<div class="spec-item"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg> Скорость: ' + m.speed_tier + '</div>' +
                '<div class="spec-item"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg> ' + m.memory_slot + '</div>' +
            '</div>' +
            '<div class="card-bottom">' +
                '<span style="font-size:11px;color:var(--text-muted);">' + m.downloads + ' загрузок</span>' +
                '<button id="btn-' + m.id.replace(/[^a-zA-Z0-9]/g, '_') + '" class="' + btnClass + '" onclick="loadModel(\'' + m.id + '\')">' + btnText + '</button>' +
            '</div>';
            grid.appendChild(card);
        });
    } catch(e) {}
}
async function loadModel(modelId) {
    showToast('Инициализация пайплайна', 'Выделение тензорных слоев для ' + modelId.split('/').pop() + '...');
    try {
        const res = await fetch('/api/models/load', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({model_id: modelId})
        });
        const data = await res.json();
        activeModelId = modelId;
        const shortName = modelId.split('/').pop();
        document.getElementById('hwModel').innerText = shortName;
        const chatModelEl = document.getElementById('chatActiveModel');
        if (chatModelEl) chatModelEl.innerText = shortName;
        updateGuide();
        showToast('Модель готова', shortName + ' подключена к C++20 / AVX2 ядру');
        loadModels();
    } catch(e) {
        showToast('Ошибка', 'Не удалось загрузить модель');
    }
}
async function loadKeys() {
    try {
        const res = await fetch('/api/keys');
        const data = await res.json();
        const list = document.getElementById('keysList');
        list.innerHTML = '';
        data.keys.forEach(k => {
            const item = document.createElement('div');
            item.style.display = 'flex';
            item.style.justifyContent = 'space-between';
            item.style.alignItems = 'center';
            item.style.padding = '8px 12px';
            item.style.background = '#090a0f';
            item.style.border = '1px solid var(--border)';
            item.style.borderRadius = '5px';
            item.style.fontFamily = 'Consolas, monospace';
            item.style.fontSize = '12px';
            item.innerHTML = '<span>' + k + '</span><button class="btn-action" style="padding:3px 10px;font-size:11px;" onclick="copyValue(\'' + k + '\', \'Ключ API\')">Скопировать</button>';
            list.appendChild(item);
        });
    } catch(e) {}
}
async function generateKey() {
    try {
        const res = await fetch('/api/keys/generate', {method: 'POST'});
        const data = await res.json();
        showToast('Ключ создан', data.key);
        loadKeys();
    } catch(e) {}
}
function formatDuration(sec) {
    if (sec >= 60) {
        const m = Math.floor(sec / 60);
        const s = Math.round(sec % 60);
        return m + ' мин ' + s + ' сек';
    }
    return sec.toFixed(2) + ' сек';
}
function toggleThink(id) {
    const body = document.getElementById('think-body-' + id);
    const arrow = document.getElementById('think-arrow-' + id);
    if (!body) return;
    if (body.style.display === 'none') {
        body.style.display = 'block';
        if (arrow) arrow.style.transform = 'rotate(0deg)';
    } else {
        body.style.display = 'none';
        if (arrow) arrow.style.transform = 'rotate(-90deg)';
    }
}
async function sendChat() {
    if (isChatStreaming) return;
    const input = document.getElementById('chatInput');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    isChatStreaming = true;
    document.getElementById('chatSendBtn').disabled = true;
    const chatBody = document.getElementById('chatBody');
    const msgId = 'msg-' + Date.now();
    const userWrap = document.createElement('div');
    userWrap.className = 'msg-wrapper';
    userWrap.innerHTML = '<div class="msg-user">' + text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</div>';
    chatBody.appendChild(userWrap);
    chatBody.scrollTop = chatBody.scrollHeight;
    const asstWrap = document.createElement('div');
    asstWrap.className = 'msg-wrapper';
    asstWrap.innerHTML = '<div class="msg-assistant">' +
        '<div id="think-panel-' + msgId + '" class="think-panel">' +
            '<div class="think-header" onclick="toggleThink(\'' + msgId + '\')">' +
                '<div class="think-title">' +
                    '<span id="think-pulse-' + msgId + '" class="think-pulse"></span>' +
                    '<span id="think-label-' + msgId + '">Думает (0.0 сек)...</span>' +
                '</div>' +
                '<svg id="think-arrow-' + msgId + '" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="transition:transform 0.15s;"><polyline points="6 9 12 15 18 9"/></svg>' +
            '</div>' +
            '<div id="think-body-' + msgId + '" class="think-content"></div>' +
        '</div>' +
        '<div id="content-' + msgId + '" class="msg-content"></div>' +
        '<div id="telemetry-' + msgId + '" class="telemetry-row" style="display:none;"></div>' +
    '</div>';
    chatBody.appendChild(asstWrap);
    chatBody.scrollTop = chatBody.scrollHeight;
    const thinkLabel = document.getElementById('think-label-' + msgId);
    const thinkBody = document.getElementById('think-body-' + msgId);
    const thinkPulse = document.getElementById('think-pulse-' + msgId);
    const contentBox = document.getElementById('content-' + msgId);
    const telemetryBox = document.getElementById('telemetry-' + msgId);
    const liveSpeed = document.getElementById('chatLiveSpeed');
    const tStart = performance.now();
    let hasThoughtEnded = false;
    let tokensCount = 0;
    const thinkTimer = setInterval(() => {
        if (!hasThoughtEnded) {
            const elapsed = (performance.now() - tStart) / 1000;
            thinkLabel.innerText = 'Думает (' + elapsed.toFixed(1) + ' сек)...';
        }
    }, 100);
    try {
        const res = await fetch('/v1/chat/completions', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + activeApiKey},
            body: JSON.stringify({
                model: activeModelId,
                messages: [{role: 'user', content: text}],
                stream: true
            })
        });
        const reader = res.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        let thoughtSec = 0;
        let totalSec = 0;
        let speedVal = 0;
        let usageData = {prompt_tokens: 0, completion_tokens: 0, total_tokens: 0};
        while (true) {
            const {done, value} = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, {stream: true});
            const lines = buffer.split('\\n');
            buffer = lines.pop();
            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed || trimmed === 'data: [DONE]') continue;
                if (trimmed.startsWith('data: ')) {
                    try {
                        const payload = JSON.parse(trimmed.slice(6));
                        const choice = payload.choices && payload.choices[0];
                        if (choice && choice.delta) {
                            if (choice.delta.reasoning_content) {
                                thinkBody.innerText += choice.delta.reasoning_content;
                                tokensCount++;
                            }
                            if (choice.delta.content) {
                                if (!hasThoughtEnded) {
                                    hasThoughtEnded = true;
                                    clearInterval(thinkTimer);
                                    thoughtSec = (performance.now() - tStart) / 1000;
                                    thinkPulse.style.display = 'none';
                                    thinkLabel.innerText = 'Думал ' + formatDuration(thoughtSec);
                                }
                                contentBox.innerText += choice.delta.content;
                                tokensCount++;
                                const curElapsed = (performance.now() - tStart) / 1000;
                                const curSpeed = (tokensCount / Math.max(0.1, curElapsed)).toFixed(1);
                                if (liveSpeed) liveSpeed.innerText = curSpeed + ' ток/сек';
                            }
                        }
                        if (payload.telemetry) {
                            thoughtSec = payload.telemetry.thinking_seconds;
                            totalSec = payload.telemetry.total_seconds;
                            speedVal = payload.telemetry.tokens_per_sec;
                        }
                        if (payload.usage) {
                            usageData = payload.usage;
                        }
                    } catch(e) {}
                }
            }
            chatBody.scrollTop = chatBody.scrollHeight;
        }
        clearInterval(thinkTimer);
        hasThoughtEnded = true;
        thinkPulse.style.display = 'none';
        if (thoughtSec > 0) {
            thinkLabel.innerText = 'Думал ' + formatDuration(thoughtSec);
        }
        if (!totalSec) {
            totalSec = (performance.now() - tStart) / 1000;
        }
        if (!speedVal) {
            speedVal = (tokensCount / Math.max(0.1, totalSec)).toFixed(1);
        }
        if (liveSpeed) liveSpeed.innerText = speedVal + ' ток/сек';
        telemetryBox.style.display = 'flex';
        telemetryBox.innerHTML = '<div class="telemetry-item"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 14 14"/></svg> Время: <b>' + formatDuration(totalSec) + '</b> (размышления: ' + formatDuration(thoughtSec) + ')</div>' +
            '<div class="telemetry-item"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg> Скорость: <b>' + speedVal + ' токенов/сек</b></div>' +
            '<div class="telemetry-item"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/></svg> Потрачено: <b>' + usageData.total_tokens + ' токенов</b> (промпт: ' + usageData.prompt_tokens + ', ответ: ' + usageData.completion_tokens + ')</div>' +
            '<div class="telemetry-item"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#8b5cf6" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/></svg> <b>C++20 Zero-Copy / AVX2 SIMD</b></div>';
        chatBody.scrollTop = chatBody.scrollHeight;
    } catch(err) {
        clearInterval(thinkTimer);
        contentBox.innerText = 'Ошибка соединения с локальным сервером Shard.';
    } finally {
        isChatStreaming = false;
        document.getElementById('chatSendBtn').disabled = false;
        chatBody.scrollTop = chatBody.scrollHeight;
    }
}
window.onload = function() {
    fetchHardware();
    loadModels();
    updateGuide();
    setInterval(fetchHardware, 5000);
};
</script>
</body>
</html>
"""
@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML
