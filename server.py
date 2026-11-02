import json
import time
import uuid
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from engine import engine_instance
from hardware import get_hardware_profile
from hf_hub import browse_models, fetch_model_metadata
app = FastAPI(title="Shard Local Model Engine & Server", version="1.0.0")
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
def openai_models():
    cur = engine_instance.active_model_id or "Qwen/Qwen2.5-Coder-7B-Instruct"
    return {
        "object": "list",
        "data": [
            {
                "id": cur,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "shard-local",
                "permission": [],
                "root": cur,
                "parent": None
            }
        ]
    }
@app.post("/v1/chat/completions")
async def openai_chat_completions(req: Request):
    data = await req.json()
    messages = data.get("messages", [])
    model = data.get("model", engine_instance.active_model_id or "Qwen/Qwen2.5-Coder-7B-Instruct")
    stream = data.get("stream", False)
    max_tokens = data.get("max_tokens", 1024)
    temperature = data.get("temperature", 0.7)
    if engine_instance.active_model_id != model:
        engine_instance.load_model(model)
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    if stream:
        def event_generator():
            for item in engine_instance.generate_stream(messages, max_tokens, temperature):
                if not item.get("done"):
                    chunk_payload = {
                        "id": cid,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": item["token"]},
                                "finish_reason": None
                            }
                        ]
                    }
                    yield f"data: {json.dumps(chunk_payload, ensure_ascii=False)}\n\n"
                else:
                    final_payload = {
                        "id": cid,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": "stop"
                            }
                        ]
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
                "message": {"role": "assistant", "content": result["content"]},
                "finish_reason": "stop"
            }
        ],
        "usage": result.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    }
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Shard // Local Hugging Face Engine & OpenAI Server</title>
<style>
:root{--bg:#07080d;--card:#0f121d;--card-border:#1d2436;--accent:#06b6d4;--emerald:#10b981;--amber:#f59e0b;--text:#f8fafc;--text-dim:#94a3b8;--font:'Segoe UI',sans-serif;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:var(--font);min-height:100vh;display:flex;flex-direction:column;}
header{background:#0b0e18;border-bottom:1px solid var(--card-border);padding:14px 28px;display:flex;justify-content:space-between;align-items:center;}
.brand{display:flex;align-items:center;gap:12px;}
.logo-badge{background:linear-gradient(135deg,#06b6d4,#3b82f6);color:#000;font-weight:900;font-size:15px;padding:4px 10px;border-radius:6px;letter-spacing:1px;}
.brand-title{font-size:18px;font-weight:800;letter-spacing:0.5px;}
.brand-sub{font-size:11px;color:var(--text-dim);}
.hw-bar{display:flex;gap:20px;align-items:center;background:#111524;padding:8px 16px;border-radius:8px;border:1px solid var(--card-border);}
.hw-item{display:flex;flex-direction:column;font-size:11px;}
.hw-item b{font-size:13px;color:var(--accent);}
.nav-tabs{display:flex;gap:8px;padding:12px 28px;background:#090c15;border-bottom:1px solid var(--card-border);}
.nav-btn{background:transparent;border:1px solid transparent;color:var(--text-dim);font-size:13px;font-weight:600;padding:8px 16px;border-radius:6px;cursor:pointer;transition:all 0.15s;}
.nav-btn.active{background:#151c2d;color:var(--accent);border-color:#06b6d4;}
.nav-btn:hover:not(.active){color:var(--text);background:#101522;}
main{flex:1;padding:24px 28px;max-width:1440px;margin:0 auto;width:100%;}
.section{display:none;}
.section.active{display:block;}
.search-row{display:flex;gap:12px;margin-bottom:20px;}
.search-input{flex:1;background:var(--card);border:1px solid var(--card-border);color:var(--text);padding:10px 16px;border-radius:8px;font-size:14px;outline:none;}
.search-input:focus{border-color:var(--accent);}
.cat-btn{background:var(--card);border:1px solid var(--card-border);color:var(--text-dim);padding:8px 14px;border-radius:8px;cursor:pointer;font-size:12px;}
.cat-btn.active{background:var(--accent);color:#000;font-weight:700;border-color:var(--accent);}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:16px;}
.model-card{background:var(--card);border:1px solid var(--card-border);border-radius:10px;padding:16px;display:flex;flex-direction:column;justify-content:space-between;transition:border 0.2s;}
.model-card:hover{border-color:#38bdf8;}
.card-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;}
.model-name{font-size:15px;font-weight:700;color:#fff;}
.model-author{font-size:11px;color:var(--text-dim);}
.badge{padding:3px 8px;border-radius:4px;font-size:11px;font-weight:700;letter-spacing:0.5px;}
.card-desc{font-size:12px;color:#cbd5e1;margin-bottom:12px;line-height:1.4;}
.card-meta{display:flex;justify-content:space-between;align-items:center;padding-top:12px;border-top:1px solid #1a2235;font-size:11px;color:var(--text-dim);}
.btn-run{background:var(--accent);color:#000;border:none;padding:7px 16px;border-radius:6px;font-weight:800;cursor:pointer;font-size:12px;transition:opacity 0.2s;}
.btn-run:hover{opacity:0.9;}
.panel-box{background:var(--card);border:1px solid var(--card-border);border-radius:10px;padding:20px;margin-bottom:20px;}
.code-block{background:#090c15;border:1px solid #1a2235;border-radius:8px;padding:14px;font-family:Consolas,monospace;font-size:13px;color:#38bdf8;position:relative;margin-top:8px;overflow-x:auto;}
.copy-btn{position:absolute;top:10px;right:10px;background:#1e293b;border:1px solid #334155;color:#fff;font-size:11px;padding:4px 10px;border-radius:4px;cursor:pointer;}
.chat-container{display:flex;flex-direction:column;height:550px;}
.chat-history{flex:1;overflow-y:auto;padding:12px;background:#090c15;border:1px solid var(--card-border);border-radius:8px;margin-bottom:12px;display:flex;flex-direction:column;gap:10px;}
.chat-msg{max-width:80%;padding:10px 14px;border-radius:8px;font-size:13px;line-height:1.5;}
.chat-msg.user{align-self:flex-end;background:#0284c7;color:#fff;}
.chat-msg.assistant{align-self:flex-start;background:#151c2d;color:#f8fafc;border:1px solid #23314d;}
.chat-input-row{display:flex;gap:10px;}
.chat-input{flex:1;background:#0e1322;border:1px solid var(--card-border);color:#fff;padding:10px 14px;border-radius:8px;outline:none;font-size:13px;}
</style>
</head>
<body>
<header>
<div class="brand">
<div class="logo-badge">SHARD</div>
<div>
<div class="brand-title">SHARD // LOCAL HUGGING FACE RUNNER</div>
<div class="brand-sub">Tri-Language Architecture: Python Server + C++20 Memory Pipeline + MASM x64 AVX2 Assembly</div>
</div>
</div>
<div class="hw-bar" id="hwStats">
<div class="hw-item"><span>CORE</span><b id="hwCore" style="color:#38bdf8;">C++20 / MASM AVX2</b></div>
<div class="hw-item"><span>GPU</span><b id="hwGpu">Detecting...</b></div>
<div class="hw-item"><span>VRAM</span><b id="hwVram">0 / 0 MB</b></div>
<div class="hw-item"><span>RAM</span><b id="hwRam">0 / 0 MB</b></div>
<div class="hw-item"><span>ACTIVE MODEL</span><b id="hwModel" style="color:#10b981;">None</b></div>
</div>
</header>
<div class="nav-tabs">
<button class="nav-btn active" onclick="showTab('market')">Hugging Face Hub</button>
<button class="nav-btn" onclick="showTab('connect')">IDE Integration (Cursor / OpenCode)</button>
<button class="nav-btn" onclick="showTab('chat')">Chat Playground</button>
<button class="nav-btn" onclick="showTab('keys')">API Keys</button>
</div>
<main>
<div id="tab-market" class="section active">
<div class="search-row">
<input type="text" id="searchInput" class="search-input" placeholder="Поиск любой модели с Hugging Face (например: gemma-2-9b, qwen2.5-coder, llama-3.2, deepseek)..." onkeydown="if(event.key==='Enter')searchHF()">
<button class="cat-btn active" onclick="setCategory('all', this)">Все</button>
<button class="cat-btn" onclick="setCategory('coding', this)">Кодинг</button>
<button class="cat-btn" onclick="setCategory('reasoning', this)">Рассуждения</button>
<button class="cat-btn" onclick="setCategory('compact', this)">Компактные (VRAM)</button>
<button class="cat-btn" onclick="setCategory('general', this)">Универсальные</button>
</div>
<div class="grid" id="modelsGrid"></div>
</div>
<div id="tab-connect" class="section">
<div class="panel-box">
<h3 style="margin-bottom:8px;color:#38bdf8;">Подключение к Cursor IDE</h3>
<p style="font-size:13px;color:var(--text-dim);margin-bottom:12px;">Откройте Cursor → Settings → Models → Добавьте модель с локальным эндпоинтом Shard:</p>
<div class="code-block">
<div><b>Model Name:</b> <span id="copyModel">Qwen/Qwen2.5-Coder-7B-Instruct</span></div>
<div><b>OpenAI Base URL:</b> http://localhost:8000/v1</div>
<div><b>API Key:</b> <span id="copyKey">sk-shard-local-avelone-dev</span></div>
<button class="copy-btn" onclick="copyCursorConfig()">Скопировать настройки</button>
</div>
</div>
<div class="panel-box">
<h3 style="margin-bottom:8px;color:#10b981;">Подключение к Claude Code / OpenCode / Continue</h3>
<p style="font-size:13px;color:var(--text-dim);margin-bottom:12px;">Конфиг для <code>config.json</code> или переменных окружения:</p>
<div class="code-block">
<pre>{
  "model": "Qwen/Qwen2.5-Coder-7B-Instruct",
  "api_base": "http://localhost:8000/v1",
  "api_key": "sk-shard-local-avelone-dev"
}</pre>
<button class="copy-btn" onclick="navigator.clipboard.writeText('http://localhost:8000/v1')">Копировать URL</button>
</div>
</div>
</div>
<div id="tab-chat" class="section">
<div class="panel-box chat-container">
<div class="chat-history" id="chatHistory">
<div class="chat-msg assistant">Привет! Я модель, запущенная локально через движок Shard. Готова писать код и отвечать на запросы прямо с твоего железа без ограничений памяти!</div>
</div>
<div class="chat-input-row">
<input type="text" id="chatInput" class="chat-input" placeholder="Напишите сообщение или задачу для кода..." onkeydown="if(event.key==='Enter')sendMessage()">
<button class="btn-run" onclick="sendMessage()">ОТПРАВИТЬ</button>
</div>
</div>
</div>
<div id="tab-keys" class="section">
<div class="panel-box">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
<div>
<h3 style="color:#fff;">Локальные API Ключи</h3>
<p style="font-size:12px;color:var(--text-dim);">Используйте эти ключи для авторизации в Cursor, Claude Code, VS Code и Python</p>
</div>
<button class="btn-run" onclick="generateKey()">+ Создать новый ключ</button>
</div>
<div id="keysList" style="display:flex;flex-direction:column;gap:8px;"></div>
</div>
</div>
</main>
<script>
let currentCat='all';
async function updateHardware(){
const res=await fetch('/api/system/hardware');
const data=await res.json();
if(data.engine&&data.engine.native_engine&&data.engine.native_engine.active){
document.getElementById('hwCore').innerText='C++20 / MASM AVX2';
document.getElementById('hwCore').style.color='#10b981';
}
document.getElementById('hwGpu').innerText=data.hardware.gpu_name;
document.getElementById('hwVram').innerText=`${data.hardware.vram_used_mb} / ${data.hardware.vram_total_mb} MB`;
document.getElementById('hwRam').innerText=`${Math.round((data.hardware.ram_total_mb-data.hardware.ram_avail_mb)/1024)} / ${Math.round(data.hardware.ram_total_mb/1024)} GB`;
if(data.engine.active_model){
document.getElementById('hwModel').innerText=data.engine.active_model.split('/').pop();
document.getElementById('copyModel').innerText=data.engine.active_model;
}
}
async function loadModels(query=''){
const res=await fetch(`/api/hf/models?query=${encodeURIComponent(query)}&category=${currentCat}`);
const models=await res.json();
const grid=document.getElementById('modelsGrid');
grid.innerHTML='';
models.forEach(m=>{
const c=m.compatibility;
const card=document.createElement('div');
card.className='model-card';
card.innerHTML=`
<div>
<div class="card-header">
<div>
<div class="model-name">${m.name}</div>
<div class="model-author">${m.author}</div>
</div>
<span class="badge" style="background:${c.color}22;color:${c.color};border:1px solid ${c.color}">${c.tier_label}</span>
</div>
<div class="card-desc">${m.desc}</div>
<div style="font-size:11px;color:#94a3b8;margin-bottom:10px;">
<div>⚡ Скорость: <b>${c.speed_est}</b></div>
<div>💾 Слот: <b>${c.mode}</b></div>
</div>
</div>
<div class="card-meta">
<span>Скачиваний: ${Number(m.downloads).toLocaleString()}</span>
<button class="btn-run" onclick="loadModel('${m.id}')">ЗАПУСТИТЬ</button>
</div>
`;
grid.appendChild(card);
});
}
async function loadModel(modelId){
alert(`Запуск модели ${modelId} через Shard Engine...`);
const res=await fetch('/api/models/load',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model_id:modelId})});
await updateHardware();
alert(`Модель ${modelId} успешно запущена! Доступна в Cursor и OpenCode по адресу http://localhost:8000/v1`);
}
function searchHF(){
const q=document.getElementById('searchInput').value;
loadModels(q);
}
function setCategory(cat,btn){
document.querySelectorAll('.cat-btn').forEach(b=>b.classList.remove('active'));
btn.classList.add('active');
currentCat=cat;
loadModels();
}
function showTab(tab){
document.querySelectorAll('.nav-btn').forEach(b=>b.classList.remove('active'));
document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
event.target.classList.add('active');
document.getElementById(`tab-${tab}`).classList.add('active');
}
async function sendMessage(){
const inp=document.getElementById('chatInput');
const txt=inp.value.trim();
if(!txt)return;
inp.value='';
const hist=document.getElementById('chatHistory');
hist.innerHTML+=`<div class="chat-msg user">${txt}</div>`;
hist.scrollTop=hist.scrollHeight;
const resp=await fetch('/v1/chat/completions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({messages:[{role:'user',content:txt}]})});
const data=await resp.json();
const ans=data.choices[0].message.content;
hist.innerHTML+=`<div class="chat-msg assistant">${ans.replace(/\\n/g,'<br>')}</div>`;
hist.scrollTop=hist.scrollHeight;
}
function copyCursorConfig(){
const text=`OpenAI Base URL: http://localhost:8000/v1\\nAPI Key: sk-shard-local-avelone-dev\\nModel: ${document.getElementById('copyModel').innerText}`;
navigator.clipboard.writeText(text);
alert('Настройки для Cursor скопированы в буфер обмена!');
}
async function loadKeys(){
const res=await fetch('/api/keys');
const data=await res.json();
const el=document.getElementById('keysList');
el.innerHTML='';
data.keys.forEach(k=>{
el.innerHTML+=`<div class="code-block" style="display:flex;justify-content:space-between;align-items:center;"><code>${k}</code><button class="copy-btn" onclick="navigator.clipboard.writeText('${k}')">Копировать</button></div>`;
});
}
async function generateKey(){
const res=await fetch('/api/keys/generate',{method:'POST'});
await loadKeys();
}
window.onload=()=>{
updateHardware();
setInterval(updateHardware,3000);
loadModels();
loadKeys();
};
</script>
</body>
</html>
"""
@app.get("/", response_class=HTMLResponse)
def index_dashboard():
    return DASHBOARD_HTML