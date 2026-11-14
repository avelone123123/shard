<p align="center">
  <a href="https://avelone123123.github.io/shard/"><img src="https://img.shields.io/badge/САЙТ-Онлайн-00E5FF?style=for-the-badge&labelColor=0A1022" alt="Сайт"></a>
  <a href="README.md">English</a> &nbsp;|&nbsp;
  <a href="README.ru.md">Русская версия</a> &nbsp;|&nbsp;
  <strong>Қазақша нұсқасы</strong>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/branding/shard-logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/branding/shard-logo-light.svg">
    <img src="assets/branding/shard-logo.svg" alt="SHARD — Таратылған жергілікті LLM инференс қозғалтқышы" width="760">
  </picture>
</p>

<h1 align="center">SHARD</h1>

<p align="center">
  <strong>Бір желі. Көптеген серверлер. Ортақ ЖИ инфрақұрылымы.</strong><br>
  <sub>Жергілікті желідегі (LAN) компьютерлердің бейнежады (VRAM) мен жедел жадын (RAM) біріктіретін, PyTorch, FastAPI, <code>llama.cpp</code> RPC және нативті C++20 / MASM AVX2 SIMD ядролары негізінде жасалған таратылған LLM инференс қозғалтқышы.</sub>
</p>

<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/LANG-English-00E5FF?style=for-the-badge&labelColor=0A1022" alt="English README"></a>
  <a href="README.ru.md"><img src="https://img.shields.io/badge/ЯЗЫК-Русский-38BDF8?style=for-the-badge&labelColor=0A1022" alt="Русская версия"></a>
  <a href="README.kz.md"><img src="https://img.shields.io/badge/ТІЛ-Қазақша-A855F7?style=for-the-badge&labelColor=0A1022" alt="Қазақша версия"></a>
  <a href="docs/LAN_SHARDING_DESIGN.md"><img src="https://img.shields.io/badge/ҚҰЖАТТАМА-LAN_Sharding-34D399?style=for-the-badge&labelColor=0A1022" alt="Documentation"></a>
  <a href="#орнату-және-жылдам-бастау"><img src="https://img.shields.io/badge/БАСТАУ-Жылдам_бастау-00E5FF?style=for-the-badge&labelColor=0A1022" alt="Getting Started"></a>
  <a href="#орнату"><img src="https://img.shields.io/badge/БАПТАУ-Орнату-38BDF8?style=for-the-badge&labelColor=0A1022" alt="Installation"></a>
  <a href="https://github.com/avelone123123/shard/discussions"><img src="https://img.shields.io/badge/ҚАУЫМДАСТЫҚ-Discussions-A855F7?style=for-the-badge&labelColor=0A1022" alt="GitHub Discussions"></a>
  <a href="https://github.com/avelone123123/shard/issues"><img src="https://img.shields.io/badge/ТРЕКЕР-Issues-F59E0B?style=for-the-badge&labelColor=0A1022" alt="Issues"></a>
  <a href="https://github.com/avelone123123/shard/releases"><img src="https://img.shields.io/github/downloads/avelone123123/shard/total?style=for-the-badge&labelColor=0A1022&color=34D399&label=%D0%96%D2%AE%D0%9A%D0%A2%D0%95%D0%A3%D0%9B%D0%95%D0%A0" alt="Жалпы жүктеулер"></a>
</p>

<p align="center">
  <a href="https://github.com/avelone123123/shard/releases"><img src="https://img.shields.io/github/v/release/avelone123123/shard?style=flat-square&labelColor=0D1326&color=00E5FF&label=release" alt="Соңғы шығарылым"></a>
  <a href="https://github.com/avelone123123/shard/releases/latest"><img src="https://img.shields.io/github/downloads/avelone123123/shard/latest/total?style=flat-square&labelColor=0D1326&color=34D399&label=latest%20downloads&logo=github&logoColor=white" alt="Соңғы нұсқа жүктеулері"></a>
  <a href="https://github.com/avelone123123/shard/stargazers"><img src="https://img.shields.io/github/stars/avelone123123/shard?style=flat-square&labelColor=0D1326&color=F59E0B&label=stars&logo=github&logoColor=white" alt="GitHub Stars"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.8%2B%20%7C%203.10%2B-38BDF8?style=flat-square&labelColor=0D1326&logo=python&logoColor=white" alt="Python 3.8+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-34D399?style=flat-square&labelColor=0D1326" alt="License: MIT"></a>
  <a href="https://github.com/avelone123123/shard/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/avelone123123/shard/tests.yml?branch=master&style=flat-square&labelColor=0D1326&label=tests" alt="Tests CI"></a>
  <img src="https://img.shields.io/badge/backend-PyTorch%20%7C%20GGUF%20%7C%20AVX2-A855F7?style=flat-square&labelColor=0D1326" alt="Backends">
  <img src="https://img.shields.io/badge/API-OpenAI%20%26%20Anthropic-00E5FF?style=flat-square&labelColor=0D1326" alt="OpenAI and Anthropic API">
</p>

<p align="center">
  <img src="assets/animations/hero-network.gif" alt="SHARD кластерінің анимациясы — Таратылған ЖИ инференс желісі" width="800">
  <br>
  <sub><b>SHARD таратылған инференс кластері:</b> Орталық мастер-үйлестіруші UDP-анықтау (<code>:8001</code>) және FastAPI SSE-сервері (<code>:8000</code>) арқылы жергілікті желідегі жұмысшы түйіндердің бейнежадын біріктіреді.</sub>
</p>

<p align="center">
  <a href="#shard-дегеніміз-не">Жоба туралы</a> &middot;
  <a href="#shard-қалай-жұмыс-істейді">Жұмыс принципі</a> &middot;
  <a href="#мүмкіндіктері">Мүмкіндіктері</a> &middot;
  <a href="#орнату-және-жылдам-бастау">Жылдам бастау</a> &middot;
  <a href="#cli-анықтамалығы-және-баптаулар">CLI командалары</a> &middot;
  <a href="#архитектура">Архитектура</a> &middot;
  <a href="#веб-басқару-тақтасы-және-демонстрация">Веб-тақта</a> &middot;
  <a href="#жоба-мәртебесі-және-қауымдастық">Қауымдастық</a>
</p>

---

## SHARD дегеніміз не?

**[SHARD](https://github.com/avelone123123/shard)** (`shard-engine`) — Python ([FastAPI](https://fastapi.tiangolo.com/) + [PyTorch](https://pytorch.org/)) тілінде жазылған, [GGUF / `llama.cpp`](https://github.com/ggml-org/llama.cpp) бэкендімен және нативті C++20 / MASM AVX2 SIMD аппараттық жеделдетуімен жабдықталған ашық бастапқы кодты таратылған үлкен тілдік модельдер (LLM) қозғалтқышы.

### Ол қандай мәселені шешеді?
14B, 32B немесе 70B+ параметрлі заманауи ашық тілдік модельдерді жергілікті компьютерде іске қосу кезінде ең жиі кездесетін кедергі — **бір бейнекартаның VRAM көлемінің жетіспеушілігі**. Көптеген әзірлеушілер мен зертханаларда бір жергілікті желіге қосылған бірнеше құрылғы (жұмыс компьютері, ноутбук, қосымша сервер) болады, бірақ олардың жады бір-бірімен байланыспағандықтан, үлкен модельді жеке-дара көтере алмайды.

### SHARD не үшін жасалды?
SHARD жергілікті желідегі (LAN) бос тұрған GPU мен RAM ресурстарын бірыңғай есептеу кластеріне біріктіреді:
- **Бұлттық сервистерсіз жадты біріктіру:** Трансформер қабаттарын гигабиттік жергілікті желі (`lan.py`, `gguf_backend.py`) арқылы бірнеше бейнекарта мен жедел жадқа бөліп орналастырыңыз — сыртқы API төлемдерінсіз және деректерді сыртқа жібермей.
- **Жүктемес бұрын үйлесімділікті тексеру (`shard fit`):** Көп гигабайттық модель салмақтарын жүктемес бұрын, оның сіздің GPU-ға, гибридті CPU+GPU режиміне немесе біріктірілген LAN-кластерге сыятынын алдын ала есептеңіз (`fit.py`).
- **ЖИ-агенттер мен IDE-ге дайын сервер:** Порт `8000` арқылы OpenAI (`/v1/chat/completions`, `/v1/completions`, `/v1/models`) және Anthropic (`/v1/messages`) стандартты эндпоинттерін ұсынады.

### SHARD кімдерге арналған?
- **Жергілікті ЖИ әзірлеушілері мен зерттеушілеріне** — 7B–70B GGUF немесе SafeTensors модельдерін өз жабдықтарында іске қосу үшін.
- **Инженерлік топтарға** — [Cursor](https://www.cursor.com/), [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview), [Aider](https://aider.chat/), [Continue](https://www.continue.dev/), [Zed](https://zed.dev/) және [OpenCode](https://github.com/opencode-ai/opencode) үшін құпия локалды кодтау көмекшілерін ұйымдастыру үшін.
- **ЖИ-агент құрастырушыларына** — [OpenAI Python SDK](https://github.com/openai/openai-python), [LangChain](https://www.langchain.com/) немесе [LlamaIndex](https://www.llamaindex.ai/) негізіндегі агенттерді өз серверлерінде қосу үшін.

---

## SHARD қалай жұмыс істейді?

SHARD жұмыс процесі төрт негізгі қадамнан тұрады: **ҚҰРУ (CREATE) → БӨЛІСУ (SHARE) → ҚОСЫЛУ (CONNECT) → ҚОЛДАНУ (USE)**.

<table>
  <tr>
    <td width="50%" valign="top">
      <h3 align="center">1. CREATE — Өз серверіңізді іске қосыңыз</h3>
      <p align="center">
        <img src="assets/animations/create-server.gif" alt="1-қадам: SHARD серверін құру және іске қосу" width="100%">
      </p>
      <p>
        Жергілікті түйінді <code>shard start</code> немесе <code>python main.py</code> командасымен қосыңыз. <code>hardware.py</code> модулі CPU, RAM және GPU (NVIDIA CUDA / Apple MPS) көрсеткіштерін анықтап, таңдалған GGUF немесе Hugging Face моделін жүктейді және <code>http://localhost:8000</code> адресінде FastAPI сервері мен веб-интерфейсті ашады.
      </p>
    </td>
    <td width="50%" valign="top">
      <h3 align="center">2. SHARE — Желімен бөлісіңіз</h3>
      <p align="center">
        <img src="assets/animations/share-network.gif" alt="2-қадам: Серверді жергілікті желіде бөлісу" width="100%">
      </p>
      <p>
        <code>shard lan create --open</code> командасымен ашық LAN есептеу пулын құрыңыз (немесе <code>shard lan create</code> арқылы жұптастыру кодын алыңыз). SHARD <code>8001</code> портында UDP-маяктарды таратып, желідегі басқа компьютерлердің VRAM мен RAM жадын біріктіру үшін <code>/api/lan/*</code> маршруттарын іске қосады.
      </p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3 align="center">3. CONNECT — Жұмысшы түйіндерді қосыңыз</h3>
      <p align="center">
        <img src="assets/animations/connect-server.gif" alt="3-қадам: SHARD серверіне қосылу" width="100%">
      </p>
      <p>
        Желідегі кез келген қосымша компьютерде <code>shard lan join &lt;мастер-IP&gt; --share</code> командасын орындаңыз. Жұмысшы түйін авторизациядан өтіп (<code>POST /api/lan/pair</code>), жергілікті RPC-воркерді іске қосады, телеметрия жіберіп тұрады және өз GPU/RAM ресурстарын ортақ кластерге қосады.
      </p>
    </td>
    <td width="50%" valign="top">
      <h3 align="center">4. USE — ЖИ-агенттерді іске қосыңыз</h3>
      <p align="center">
        <img src="assets/animations/ai-agents.gif" alt="4-қадам: SHARD ішіндегі ЖИ-агенттер" width="100%">
      </p>
      <p>
        Код редакторларын, автономды ЖИ-агенттерді немесе Python қосымшаларын <code>http://&lt;мастер-IP&gt;:8000/v1/chat/completions</code> не <code>/v1/messages</code> адресіне бағыттаңыз. Мастер-түйін модель қабаттарының орындалуын үйлестіріп, токендерді Server-Sent Events (SSE) арқылы нақты уақытта қайтарады.
      </p>
    </td>
  </tr>
</table>

---

## Мүмкіндіктері

Төмендегі барлық мүмкіндіктер [SHARD](https://github.com/avelone123123/shard) (`v1.8.0`) репозиторийінің нақты бастапқы коды бойынша тексерілген.

### Іске асырылған мүмкіндіктер

| Санат | Мүмкіндік | Бастапқы код модулі | Сипаттамасы |
| :--- | :--- | :--- | :--- |
| **LAN-кластер және шардинг** | **UDP авто-анықтау және жад пулингі** | [`lan.py`](lan.py), [`docs/LAN_SHARDING_DESIGN.md`](docs/LAN_SHARDING_DESIGN.md) | UDP-маяктарды (`:8001`) таратады, код немесе `--open` режимі арқылы түйіндерді жұптастырады, `/api/lan/heartbeat` арқылы күйді бақылайды және VRAM/RAM біріктіреді. |
| **Инференс бэкендтері** | **Қос PyTorch және GGUF (`llama.cpp`) қозғалтқышы** | [`engine.py`](engine.py), [`gguf_backend.py`](gguf_backend.py) | Hugging Face (`transformers` / `safetensors`) модельдерін CUDA/CPU/MPS-те және квантталған GGUF модельдерін `llama-server` мен `ggml-rpc-server` арқылы GPU қабаттарын (`-ngl`) баптай отырып орындайды. |
| **Жабдықты бағалау** | **Алдын ала үйлесімділік кеңесшісі (`shard fit`)** | [`fit.py`](fit.py), [`hardware.py`](hardware.py) | Модельді жүктемес бұрын оның архитектурасын талдап, `GPU`, `HYBRID` немесе `IMPOSSIBLE` вердиктін, максималды контекст терезесін, болжамды жылдамдықты (tok/s) және оңтайлы кванттауды көрсетеді. |
| **Нативті жеделдету** | **C++20 және MASM AVX2 SIMD ядролары** | [`native/shard_core.cpp`](native/shard_core.cpp), [`native/shard_asm.asm`](native/shard_asm.asm), [`shard_bridge.py`](shard_bridge.py) | `ctypes` арқылы қосылатын `RMSNorm`, `LayerNorm`, айналмалы эмбеддингтер (`RoPE`), `Softmax`, `GELU`, `SiLU` және `MatMul` үшін C++20 және x86-64 AVX2 ассемблерлік функциялары. |
| **API және стриминг** | **OpenAI және Anthropic API үйлесімділігі** | [`server.py`](server.py) | Нақты уақыттағы SSE стримингі және API-кілт қорғанысы (`shard key`) бар `/v1/chat/completions`, `/v1/completions`, `/v1/models`, `/v1/messages` (Anthropic) және `/api/generate` маршруттары. |
| **Веб-басқару** | **Тәуелсіз басқару тақтасы (`dashboard.html`)** | [`dashboard.html`](dashboard.html) | `http://localhost:8000` адресіндегі GPU/VRAM/RAM телеметриясы, Hugging Face модельдерін іздеу/жүктеу, интерактивті чат, LAN-пул менеджері және IDE интеграция блоктары бар веб-интерфейс. |
| **Контейнерлеу** | **Docker және NVIDIA GPU Compose стегі** | [`Dockerfile`](Dockerfile), [`docker-compose.yml`](docker-compose.yml) | Тұрақты томдары (`shard_models_volume`, `shard_data_volume`) және NVIDIA GPU қолдауы бар дайын контейнерлік конфигурация. |

### Эксперименттік мүмкіндіктер
- **Көп-хосттық `llama.cpp` RPC тензорлық тарату (`gguf_backend.py`, `lan.py`):** LAN ішіндегі әртүрлі бейнекарталар арасында TCP/RPC арқылы қабаттарды бөліп есептеу сымды Gigabit / 2.5GbE Ethernet желілерінде ең жоғары өнімділік көрсетеді; Wi-Fi желісінде кідіріс жоғары болуы мүмкін.
- **Нативті Win32 десктоп интерфейсі (`native/shard_gui.cpp`):** Веб-тақтамен қатар Windows жүйесіне арналған C++ графикалық қосымшасы.

### Жоспарланған мүмкіндіктер
- **Түйін үзілгенде қабаттарды автоматты қайта бөлу:** Генерация барысында жұмысшы түйін желіден ажырап қалса, жүктемені үзіліссіз қайта бөлу (`docs/LAN_SHARDING_DESIGN.md`).
- **Linux / macOS ARM64 NEON нұсқаулықтары:** Нативті SIMD ядроларын (`native/`) Apple Silicon және ARM64 процессорларына бейімдеу.

---

## Орнату және жылдам бастау

### Алғышарттар
- **ОЖ:** Windows 10/11 (64-bit), Linux (Ubuntu 20.04+ / Debian 12+) немесе macOS.
- **Python:** `3.8+` нұсқасы (`3.10+` ұсынылады).
- **Аппараттық құралдар:** CUDA драйверлері бар NVIDIA GPU (ұсынылады), Apple Silicon (MPS) немесе AVX2 қолдайтын x86-64 процессоры.
- **Желі (LAN-кластер үшін):** Бір ішкі желіде орналасқан, TCP `8000` (API/UI) және UDP/TCP `8001` (LAN анықтау және RPC) порттары ашық екі немесе одан көп компьютер.

### Орнату

#### 1-нұсқа: PyPI арқылы орнату (ұсынылады)
SHARD пакетін тікелей ресми PyPI каталогынан орнатыңыз:
```bash
pip install shard-engine

pip install "git+https://github.com/avelone123123/shard.git"

# Толық орнату: PyTorch/Transformers safetensors және Hugging Face модульдерін қосады
pip install "shard-engine[full] @ git+https://github.com/avelone123123/shard.git"
```
*PyPI туралы ескертпе:* `shard-engine` пакеті PyPI индексіндегі алғашқы жарияланымды күтуде. PyPI-де жарияланғаннан кейін `pip install shard-engine` және `pip install "shard-engine[full]"` тікелей PyPI-ден қолжетімді болады.

Орнатудан кейін бірден ортаны тексеріңіз:
```bash
shard doctor
```
> **Windows PATH ескертпесі:** Егер Windows жүйесі `shard.exe` PATH ішінде табылмағанын хабарласа, SHARD-ты Python арқылы тікелей іске қоса аласыз: `python -m shard_engine <команда>` (мысалы, `python -m shard_engine help` немесе `python -m shard_engine start`).
> **GPU және runtime ескертпесі:** GGUF бэкенді алғашқы іске қосуда `llama-server` / `ggml-rpc-server` бинарлық файлдарын автоматты түрде жүктейді және қосымша ML пакеттерін қажет етпейді. CUDA-мен PyTorch қолдану үшін алдымен үйлесімді `torch` нұсқасын орнатыңыз, содан кейін `pip install "shard-engine[full]"` орындаңыз.

#### 2-нұсқа: Әзірлеушілерге арналған бастапқы кодтан орнату
```bash
git clone https://github.com/avelone123123/shard.git
cd shard
pip install -e ".[dev]"
```

#### 3-нұсқа: Docker Compose арқылы іске қосу (NVIDIA GPU қолдауымен)
```bash
git clone https://github.com/avelone123123/shard.git
cd shard
docker-compose up --build
```

#### 4-нұсқа: Нативті C++20 / MASM AVX2 ядроларын компиляциялау (қосымша)
`shard_core.dll` (Windows MSVC `cl.exe` + `ml64.exe`) немесе `shard_core.so` (Linux GCC/Clang) жинау үшін:
```bash
python native/build_native.py
```

#### Жаңарту және жою
```bash
shard update --check
pip install --upgrade shard-engine
pip uninstall shard-engine
```
Python пакетін жойған кезде жүктелген модельдер, баптаулар және API кілттері пайдаланушы бумасында сақталады.

---

### Жылдам бастау қадамдары

#### 1. Ортаны тексеріп, SHARD серверін іске қосыңыз
```bash
# Python, тәуелділіктер, GPU VRAM, LAN IP, порттар және бума құқықтарын тексеру
shard doctor

# Серверді фондық режимде қосу және күйін тексеру
shard start --daemon
shard status

# Немесе http://localhost:8000 веб-тақтасын бірден браузерде ашу
shard gui

# Фондық серверді қауіпсіз қайта қосу және тоқтату
shard restart
shard stop
```

#### 2. Жабдық күйін және модель сыйымдылығын тексеру
```bash
# Модельді жүктемес бұрын оның жүйеге сәйкестігін тексеру
shard fit Qwen/Qwen2.5-Coder-7B-Instruct
shard fit meta-llama/Llama-3.1-8B-Instruct
```

#### 3. Модельді жүктеу және іске қосу
```bash
# Модельді жүктеп, терминалдағы интерактивті чатты ашу
shard run Qwen/Qwen2.5-Coder-7B-Instruct

# Жергілікті және қолжетімді модельдер тізімін көру
shard list
```

#### 4. Таратылған LAN-кластерді құру немесе оған қосылу
**Басты компьютерде (Master)** `shard lan create` осы хосттың нақты локалды IPv4 адрестерін бірден көрсетеді:
```bash
# Ашық LAN пулын құрып, мақсатты модельді орнату
shard lan create --open --share --model Qwen/Qwen2.5-Coder-7B-Instruct

# Немесе кодпен қорғалған LAN пулын құру
shard lan create --share
```

Бір желідегі әрбір **жұмысшы компьютерде (Worker)** Мастер көрсеткен `<SERVER_IP>` адресін қойыңыз:
```bash
# Ашық желіге қосылып, GPU/RAM ресурсын ортақ пулға беру
shard lan join <SERVER_IP> --share

# Немесе жұптастыру коды арқылы қосылу
shard lan join <SERVER_IP> <PAIR_CODE> --share

# Кластер түйіндерін, IP адрестерін және жалпы жад көлемін тексеру
shard lan status

# LAN пулынан шығу
shard lan off
```

---

## CLI анықтамалығы және баптаулар

### CLI командалары (`shard_engine/cli.py`)

| Команда | Аргументтер мен жалаушалар | Сипаттамасы |
| :--- | :--- | :--- |
| `shard start` | `[--host HOST] [--port PORT] [-d\|--daemon] [--no-browser]` | SHARD FastAPI серверін және Uvicorn ортасын іске қосады (әдепкі `0.0.0.0:8000`). |
| `shard stop` | *(жоқ)* | PID тексеруімен фондық SHARD серверін қауіпсіз тоқтатады. |
| `shard restart` | `[--host HOST] [--port PORT] [--foreground] [--no-browser]` | SHARD серверін тоқтатып, қайта іске қосады. |
| `shard gui` | *(жоқ)* | Серверді қосып, браузерде веб-тақтаны (немесе Win32 GUI) ашады. |
| `shard status` | *(жоқ)* | Сервер күйін, CPU, RAM, GPU VRAM, белсенді модельді және LAN IPv4 адрестерін көрсетеді. |
| `shard doctor` | *(жоқ)* | Python, бума ресурстарын, тәуелділіктерді, GPU, желіні, порттарды және дискіні тексереді. |
| `shard version` | `[-V\|--version]` | Орнатылған SHARD нұсқасын, Python жолын, ОЖ және деректер бумаларын шығарады. |
| `shard update` | `[--check]` | PyPI-де `shard-engine` жаңа нұсқасын тексеріп, `pip` арқылы жаңартады. |
| `shard fit` | `<model_id> [--ctx N] [--no-fetch]` | Жүктеу алдында VRAM/RAM талаптарын, кванттау деңгейін және жылдамдықты есептейді. |
| `shard run` | `[model_id]` | Модельді жүктеп (қажет болса), терминалдағы REPL чатын іске қосады. |
| `shard list` | `[--category CAT]` | Жергілікті және каталогтағы модельдерді тізімдейді (`all`, `ideal`, `coding`, `reasoning`, `compact`). |
| `shard lan` | `[status\|create\|join\|model\|off] [extra] [--share] [--open] [--model ID] [--port N]` | Жергілікті желідегі есептеу пулын құру, қосылу, баптау, тексеру немесе тоқтату. |
| `shard key` | `[--gen]` | Жергілікті API кілтін көрсетеді немесе жаңа токен жасайды (`--gen`). |
| `shard models-dir` | `[path \| reset]` | Модель салмақтары сақталатын бума жолын көрсетеді, өзгертеді немесе бастапқы қалпына келтіреді. |
| `shard test` | `[--strict-native]` | 12 тензорлық ядроны және AVX2 / Python fallback жылдамдығын тексереді. |
| `shard help` | `[-h\|--help]` | Барлық CLI командалары бойынша толық анықтаманы шығарады. |

### Жүйелік бумалар және қоршаған орта айнымалылары (`shard_engine/paths.py`)

SHARD пайдаланушы деректерін `site-packages` бумасынан тыс сақтайды (және бұрынғы `~/.shard` файлдарын сақтайды):
- **Windows:** `%LOCALAPPDATA%\SHARD` (баптаулар, `keys_db.json`, `logs/`, `run/`, `models/`)
- **Linux:** `~/.local/share/shard`, `~/.config/shard`, `~/.cache/shard`, `~/.local/state/shard` (`XDG_*` қолдауымен)
- **macOS:** `~/Library/Application Support/SHARD`, `~/Library/Caches/SHARD`, `~/Library/Logs/SHARD`

| Айнымалы / Файл | Әдепкі мәні | Қызметі |
| :--- | :--- | :--- |
| `SHARD_HOST` | `0.0.0.0` | Сервер байланысатын желілік интерфейс IP-адресі. |
| `SHARD_PORT` | `8000` | API сервері мен веб-тақтаға арналған HTTP порты. |
| `SHARD_DATA_DIR` | Жүйелік бума (Docker-де `/data/shard`) | `settings.json`, `keys_db.json`, `lan.json`, журналдар мен PID түбір бумасын өзгертеді. |
| `SHARD_MODELS_DIR` | `<data_dir>/models` (Docker-де `/data/models`) | Жүктелген Hugging Face SafeTensors және GGUF модельдерін сақтау бумасы. |
| `SHARD_NO_BROWSER` / `SHARD_DOCKER` | *(орнатылмаған)* | Сервер қосылғанда браузерді автоматты ашуды өшіреді. |
| `SHARD_NATIVE_LIB` | *(автоіздеу)* | `shard_core.dll`/`.so`/`.dylib` жолы (`none` — Python fallback режимі). |
| `HF_HOME` / `TRANSFORMERS_CACHE` | `~/.cache/huggingface` | Hugging Face модельдерінің кэш бумасы. |
| `engine_optimizations.json` | `shard_engine` пакет ресурсы | `importlib.resources` арқылы оқылатын оңтайландыру профилі. |

---

## Архитектура

<p align="center">
  <img src="assets/animations/network-ecosystem.gif" alt="SHARD желілік экожүйесі" width="800">
  <br>
  <sub><b>SHARD экожүйесінің топологиясы:</b> Кодтау орталары, агенттік фреймворктер, орталық үйлестіруші және LAN жұмысшы түйіндері біртұтас есептеу жүйесі ретінде жұмыс істейді.</sub>
</p>

### Жүйе архитектурасының диаграммасы

```mermaid
flowchart TB
    subgraph Clients["ЖИ-агенттер, IDE және SDK клиенттері"]
        IDE["Cursor / Claude Code / Aider / Continue / Zed"]
        SDK["OpenAI SDK / Anthropic SDK / LangChain / LlamaIndex"]
        Browser["Веб-басқару тақтасы (dashboard.html)"]
    end

    subgraph Master["SHARD Мастер-түйіні (FastAPI :8000)"]
        Server["server.py\n(/v1/chat/completions, /v1/messages, /api/*)"]
        Fit["fit.py & hardware.py\n(Алдын ала VRAM/RAM кеңесшісі)"]
        Engine["engine.py\n(PyTorch Transformers және токен стримері)"]
        GGUF["gguf_backend.py\n(llama-server және -ngl қабаттарын бөлу)"]
        Bridge["shard_bridge.py\n(ctypes C++20 / MASM AVX2 SIMD ядросы)"]
        LANCoord["lan.py\n(UDP анықтау :8001 және кластер үйлестірушісі)"]
    end

    subgraph Workers["Таратылған LAN жұмысшы түйіндері (:8001)"]
        W1["Worker 1 түйіні\nggml-rpc-server / GPU VRAM шарды"]
        W2["Worker 2 түйіні\nggml-rpc-server / GPU VRAM шарды"]
        W3["Worker N түйіні\nggml-rpc-server / System RAM + GPU"]
    end

    IDE -->|HTTP / SSE :8000| Server
    SDK -->|HTTP / SSE :8000| Server
    Browser -->|REST / SSE :8000| Server

    Server --> Fit
    Server --> Engine
    Server --> LANCoord
    Engine --> GGUF
    Engine --> Bridge

    LANCoord <-->|UDP-маяктар және Heartbeat :8001| W1
    LANCoord <-->|UDP-маяктар және Heartbeat :8001| W2
    LANCoord <-->|UDP-маяктар және Heartbeat :8001| W3

    GGUF <-->|TCP Tensor RPC ағыны| W1
    GGUF <-->|TCP Tensor RPC ағыны| W2
    GGUF <-->|TCP Tensor RPC ағыны| W3
```

---

## Веб-басқару тақтасы және демонстрация

### Интерактивті басқару тақтасы (`dashboard.html`)
Браузерде `http://localhost:8000` адресін ашып, SHARD басқару тақтасына өтіңіз:

<p align="center">
  <img src="assets/screenshots/dashboard-overview.png" alt="SHARD веб-басқару тақтасы және LAN пулының телеметриясы" width="960">
  <br>
  <sub><b>SHARD веб-тақтасы:</b> Нақты уақыттағы VRAM/RAM көрсеткіштері, модель сыйымдылығының кеңесшісі, IDE-ні бір батырмамен баптау және стриминг чаты.</sub>
</p>

### CLI үйлесімділік кеңесшісі (`shard fit`) және LAN кластер мәртебесі (`shard lan status`)

<p align="center">
  <img src="assets/screenshots/cli-fit-advisor.png" alt="SHARD CLI: shard fit және shard lan status" width="900">
  <br>
  <sub><b>Алдын ала тексеру:</b> Модель салмақтарын жүктемес бұрын кванттау деңгейін, контекст көлемін және LAN кластеріне сыйымдылығын бағалау.</sub>
</p>

### OpenAI Python SDK арқылы қосылу мысалы

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="shard-local",  # Немесе `shard key --gen` арқылы жаңа кілт жасаңыз
)

stream = client.chat.completions.create(
    model="Qwen/Qwen2.5-Coder-7B-Instruct",
    messages=[{"role": "user", "content": "Жергілікті желідегі тензорлық параллелизм қалай жұмыс істейді?"}],
    stream=True,
)

for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

---

## Жоба мәртебесі және қауымдастық

### Технологиялық стек
- **Орындау ортасы және API:** Python 3.8+ / 3.10+, [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/), [Pydantic v2](https://docs.pydantic.dev/)
- **ML және кванттау:** [PyTorch](https://pytorch.org/), Hugging Face [`transformers`](https://huggingface.co/docs/transformers), [`accelerate`](https://huggingface.co/docs/accelerate), [`safetensors`](https://huggingface.co/docs/safetensors), [`huggingface_hub`](https://huggingface.co/docs/huggingface_hub), [`llama.cpp` GGUF](https://github.com/ggml-org/llama.cpp)
- **Нативті жеделдету:** C++20 (`native/shard_core.cpp`) + x86-64 MASM AVX2 ассемблері (`native/shard_asm.asm`)
- **Желі және телеметрия:** `psutil`, UDP broadcast анықтау (`:8001`), HTTP/SSE стриминг (`:8000`)

### Құжаттама және жобаға үлес қосу
- **LAN шардинг архитектурасы:** [`docs/LAN_SHARDING_DESIGN.md`](docs/LAN_SHARDING_DESIGN.md)
- **Үлес қосушыларға нұсқаулық:** [`CONTRIBUTING.md`](CONTRIBUTING.md)
- **Мінез-құлық кодексі:** [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
- **Өзгерістер тарихы:** [`CHANGELOG.md`](CHANGELOG.md) (ағымдағы нұсқа: `v1.8.0`)
- **Қателер мен ұсыныстар:** [GitHub Issues](https://github.com/avelone123123/shard/issues)
- **Талқылаулар мен сұрақтар:** [GitHub Discussions](https://github.com/avelone123123/shard/discussions)
- **Қауіпсіздік саясаты:** Қауіпсіздік осалдықтары туралы [GitHub Security Advisories](https://github.com/avelone123123/shard/security) арқылы хабарлаңыз.
- **Лицензия:** [MIT License](LICENSE) лицензиясы бойынша таратылады.

---

<p align="center">
  <img src="assets/branding/shard-icon.svg" alt="SHARD Icon" width="64"><br>
  <strong>SHARD — Бір желі. Көптеген серверлер. Ортақ ЖИ инфрақұрылымы.</strong><br>
  <sub>Ашық жергілікті ЖИ қауымдастығы үшін жасалған &middot; <a href="README.md">English</a> &middot; <a href="README.ru.md">Русский</a> &middot; <a href="README.kz.md">Қазақша</a></sub>
</p>
