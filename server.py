import json
import time
import uuid
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
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
    current = st.get("model", "Qwen/Qwen2.5-Coder-7B-Instruct")
    return {"object": "list", "data": [{"id": current, "object": "model", "created": int(time.time()), "owned_by": "shard-local"}]}
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
.info-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:16px;}
.info-card h3{font-size:14px;font-weight:600;color:#fff;margin-bottom:4px;display:flex;align-items:center;gap:8px;}
.info-card p{font-size:12px;color:var(--text-muted);margin-bottom:12px;}
.code-panel{background:#090a0f;border:1px solid var(--border);border-radius:6px;padding:12px 14px;font-family:Consolas,monospace;font-size:12px;color:#cbd5e1;position:relative;overflow-x:auto;}
.btn-copy{position:absolute;top:9px;right:9px;background:#1e293b;border:1px solid #334155;color:#f8fafc;padding:4px 10px;border-radius:4px;font-size:11px;cursor:pointer;display:inline-flex;align-items:center;gap:4px;}
.btn-copy:hover{background:#334155;}
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
Cursor / OpenCode
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
<div class="info-card">
<h3>
<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
Подключение к Cursor IDE
</h3>
<p>В настройках Cursor (Settings -> Models -> OpenAI API Key / Base URL) укажите локальный адрес Shard:</p>
<div class="code-panel">
<pre id="codeCursor">Base URL: http://localhost:8000/v1
API Key:  sk-shard-local-avelone-dev
Model:    Qwen/Qwen2.5-Coder-7B-Instruct</pre>
<button class="btn-copy" onclick="copyText('codeCursor', 'Конфигурация Cursor скопирована')">Скопировать</button>
</div>
</div>
<div class="info-card">
<h3>
<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
Подключение к Claude Code / OpenCode / CLI
</h3>
<p>Экспортируйте переменные окружения в PowerShell или терминале:</p>
<div class="code-panel">
<pre id="codeCli">$env:OPENAI_API_BASE = "http://localhost:8000/v1"
$env:OPENAI_API_KEY = "sk-shard-local-avelone-dev"</pre>
<button class="btn-copy" onclick="copyText('codeCli', 'Команды CLI скопированы')">Скопировать</button>
</div>
</div>
<div class="info-card">
<h3>
<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 14 14"/></svg>
CURL Проверка локального API
</h3>
<div class="code-panel">
<pre id="codeCurl">curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-shard-local-avelone-dev" \
  -d '{"model":"shard-local-model","messages":[{"role":"user","content":"Напиши быструю сортировку на C++"}]}'</pre>
<button class="btn-copy" onclick="copyText('codeCurl', 'CURL команда скопирована')">Скопировать</button>
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
let toastTimeout = null;
let isChatStreaming = false;
function showToast(title, sub) {
    const el = document.getElementById('toast');
    if (toastTimeout) clearTimeout(toastTimeout);
    el.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg><div><div class="toast-title">' + title + '</div><div class="toast-sub">' + sub + '</div></div>';
    el.classList.add('visible');
    toastTimeout = setTimeout(() => { el.classList.remove('visible'); }, 3200);
}
function copyText(id, toastMsg) {
    const text = document.getElementById(id).innerText;
    navigator.clipboard.writeText(text);
    showToast('Скопировано', toastMsg);
}
function showTab(id, btn) {
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-' + id).classList.add('active');
    btn.classList.add('active');
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
                '<button id="btn-' + m.id.replace(/[^a-zA-Z0-9]/g, '_') + '" class="' + btnClass + '" onclick="loadModel('' + m.id + '')">' + btnText + '</button>' +
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
        const cursorPanel = document.getElementById('codeCursor');
        if (cursorPanel) {
            cursorPanel.innerText = 'Base URL: http://localhost:8000/v1\nAPI Key:  sk-shard-local-avelone-dev\nModel:    ' + modelId;
        }
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
            item.innerHTML = '<span>' + k + '</span><button class="btn-action" style="padding:3px 10px;font-size:11px;" onclick="navigator.clipboard.writeText('' + k + '');showToast('Скопировано', 'Ключ добавлен в буфер обмена')">Скопировать</button>';
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
            '<div class="think-header" onclick="toggleThink('' + msgId + '')">' +
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
            headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer sk-shard-local-avelone-dev'},
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
            const lines = buffer.split('\n');
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
    setInterval(fetchHardware, 5000);
};
</script>
</body>
</html>
"""
@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML