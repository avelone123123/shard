const I18N_DATA = {
en: {
nav_product: "Product",
nav_how: "How It Works",
nav_features: "Features",
nav_arch: "Architecture",
nav_install: "Installation",
nav_docs: "Docs",
nav_cta: "Get Started",
hero_badge: "TRI-LANGUAGE LOCAL NEURAL ENGINE // C++20 + MASM AVX2 + FASTAPI",
hero_title: "One Network. Many Servers. Shared AI Infrastructure.",
hero_subtitle: "Pool GPU VRAM and system memory across local machines to run massive open LLMs. Powered by PyTorch, FastAPI, llama.cpp RPC, and native SIMD kernels.",
hero_cta_primary: "Quick Start",
hero_cta_secondary: "View on GitHub",
hero_telemetry_arch: "ARCHITECTURE",
hero_telemetry_arch_val: "LAYER-SHARDED RPC",
hero_telemetry_hop: "HOP BANDWIDTH",
hero_telemetry_hop_val: "~8 KB / TOKEN",
hero_telemetry_discovery: "DISCOVERY",
hero_telemetry_discovery_val: "UDP 8001 BEACON",
hero_telemetry_api: "API PARITY",
hero_telemetry_api_val: "OPENAI & ANTHROPIC",
hero_caption_title: "LIVE CLUSTER TOPOLOGY",
hero_caption_status: "4 NODES POOLED",
about_tag: "DISTRIBUTED COMPUTE",
about_title: "What Is SHARD?",
about_desc: "SHARD transforms fragmented personal hardware into a unified AI inference engine. No expensive enterprise GPUs or datacenter NVLink interconnects required.",
about_card1_num: "01",
about_card1_title: "The Problem",
about_card1_text: "High-parameter frontier models like 14B, 32B, and 70B exceed the 6-12 GB VRAM capacity of consumer GPUs. Developers are forced into expensive cloud API bills or painfully slow single-CPU offloading.",
about_card2_num: "02",
about_card2_title: "The Solution",
about_card2_text: "SHARD pools video memory and RAM across everyday computers on your local network. A gaming laptop with 8 GB VRAM paired with a desktop holds and runs 32B models collaboratively with zero cloud dependence.",
about_card3_num: "03",
about_card3_title: "The Advantage",
about_card3_text: "Rather than all-reduce tensor parallelism that demands 100 Gbps cables, SHARD uses layer sharding via llama.cpp RPC. Activations hop sequentially at ~8 KB per token, running fluidly over standard Wi-Fi or Gigabit LAN.",
calc_title: "INTERACTIVE HARDWARE FIT SIMULATOR",
calc_badge: "PRE-FLIGHT MATH",
calc_model_label: "Select Open Weight Model",
calc_gpu1_label: "Node 1 (Master) Free VRAM",
calc_gpu2_label: "Node 2 (Worker) Free RAM / VRAM",
calc_total_label: "Total Pooled Capacity:",
calc_req_label: "Model Memory Required:",
calc_ratio_label: "Calculated -ts Split:",
how_tag: "SIMPLE PROTOCOL",
how_title: "How SHARD Works",
how_desc: "From standalone PC to multi-node cluster in four transparent steps. Zero complex Kubernetes configurations or network drivers.",
step1_num: "STEP 01",
step1_title: "CREATE",
step1_text: "Launch your master server. SHARD initiates the FastAPI REST gateway, loads hardware telemetry, and broadcasts UDP discovery beacons on port 8001.",
step2_num: "STEP 02",
step2_title: "SHARE",
step2_text: "Worker machines on your LAN join with one command. Each node contributes spare GPU VRAM or system RAM to the shared cluster memory pool.",
step3_num: "STEP 03",
step3_title: "CONNECT",
step3_text: "Clients and developer tools automatically discover available cluster servers without manual IP configuration. Open networks accept connections with zero friction.",
step4_num: "STEP 04",
step4_title: "USE",
step4_text: "Plug Cursor, Claude Code, Aider, or your custom agents directly into the standard /v1 endpoint. Stream tokens at full local speed.",
feat_tag: "CAPABILITIES",
feat_title: "Engine Features",
feat_desc: "Engineered from the ground up for low-latency inference, memory efficiency, and developer ergonomics.",
feat1_tag: "NETWORKING",
feat1_title: "Zero-Config LAN Discovery",
feat1_text: "UDP beacons announce cluster availability every 2 seconds. Nearby nodes appear in the dashboard automatically with instant one-click pairing.",
feat2_tag: "DISTRIBUTED RUNTIME",
feat2_title: "llama.cpp RPC Layer Sharding",
feat2_text: "Proportional tensor split (-ts) assigns memory slices based on exact device capacity. Layer hops transfer only ~8 KB activation buffers.",
feat3_tag: "DUAL BACKEND",
feat3_title: "PyTorch & GGUF Dual Engines",
feat3_text: "Run pure PyTorch models for research and development or high-efficiency quantized GGUF models for peak tokens-per-second performance.",
feat4_tag: "NATIVE ACCELERATION",
feat4_title: "C++20 & MASM AVX2 SIMD",
feat4_text: "Handcrafted 64-bit assembler kernels deliver sub-microsecond vector operations and zero-copy ring buffers on Windows x64 and Linux.",
feat5_tag: "INTELLIGENT ADVISOR",
feat5_title: "Pre-Flight Hardware Fit Advisor",
feat5_text: "Run shard fit before downloading gigabytes. The analyzer mathematically determines if a model fits single-node or requires LAN pooling.",
feat6_tag: "STANDARD COMPATIBILITY",
feat6_title: "OpenAI & Anthropic /v1 Parity",
feat6_text: "Complete drop-in compatibility for /v1/chat/completions and /v1/messages with streaming Server-Sent Events (SSE) and tool calling.",
showcase_tab_dash: "Web Dashboard Overview",
showcase_tab_cli: "CLI Pre-Flight Fit Advisor",
arch_tag: "INTERNALS",
arch_title: "System Architecture",
arch_desc: "Transparent modular architecture. Click any subsystem on the left to inspect its role, communication protocols, and source location.",
install_tag: "GETTING STARTED",
install_title: "Installation & Quick Start",
install_desc: "Choose your preferred environment. SHARD runs anywhere from standalone developer laptops to dedicated LAN clusters.",
tab_pip: "Source (Editable)",
tab_docker: "Docker Compose",
tab_native: "Native C++20 Build",
pip_step1: "1. Clone Repository",
pip_step2: "2. Install Package with Pip",
pip_step3: "3. Launch Web Dashboard & API Server",
pip_step4: "4. Create Distributed Cluster",
cases_tag: "APPLICATIONS",
cases_title: "Real-World Use Cases",
cases_desc: "How developers and teams deploy SHARD to solve real memory bottlenecks without subscription costs.",
case1_badge: "HOMELAB / DEV CLUSTERS",
case1_title: "The Laptop + Desktop Supernode",
case1_desc: "Combine an RTX 3060 gaming laptop (6 GB VRAM) with an office desktop (16 GB RAM). Together, they pool 22 GB to run 32B coding models effortlessly.",
case1_stat: "POOLED: 22 GB // 32B QUANT FIT: YES",
case2_badge: "LOCAL DEVELOPMENT",
case2_title: "Unlimited AI Coding Agent Backend",
case2_desc: "Point Cursor, Claude Code, and Aider to http://localhost:8000/v1. Enjoy infinite completions and refactoring sessions with zero rate limits or token bills.",
case2_stat: "LATENCY: ~18 MS // COST: $0.00 / MO",
case3_badge: "PRIVACY & SECURITY",
case3_title: "100% Air-Gapped Enterprise Lab",
case3_desc: "Host proprietary codebases and sensitive data. All model weights, KV caches, and tokens remain strictly inside your physical local network.",
case3_stat: "CLOUD EXPOSURE: ZERO // DATA: LOCAL",
faq_tag: "QUESTIONS & ANSWERS",
faq_title: "Frequently Asked Questions",
faq_desc: "Technical specifics, requirements, and hardware compatibility.",
faq_q1: "What hardware do I need to start with SHARD?",
faq_a1: "You can start with a single PC running Windows or Linux with a modern CPU supporting AVX2. Adding an NVIDIA GPU accelerates inference, and connecting additional LAN computers pools their memory automatically.",
faq_q2: "How does SHARD pool memory across different computers?",
faq_a2: "SHARD leverages llama.cpp's native RPC protocol. The master node analyzes available memory budgets across all connected machines and splits model layers proportionally using the -ts flag.",
faq_q3: "Do I need fast 10Gbps or NVLink networking?",
faq_a3: "No. Because SHARD uses layer sharding rather than all-reduce tensor splitting, only the hidden state activations (~8 KB per token hop) are transmitted between nodes. Standard Gigabit Ethernet or Wi-Fi is fully sufficient.",
faq_q4: "Can I connect my AI coding tools like Cursor or Claude Code?",
faq_a4: "Yes. SHARD provides 100% compatible /v1/chat/completions (OpenAI) and /v1/messages (Anthropic) endpoints with streaming SSE support. Simply point your tool base URL to http://localhost:8000/v1.",
faq_q5: "What is an 'Open Network' in SHARD?",
faq_a5: "Introduced in v1.8.0, an open network broadcasts an open flag in its UDP beacon. Other machines on your local subnet can discover and join the cluster with a single click without typing a pairing code.",
faq_q6: "Is SHARD fully open source?",
faq_a6: "Yes. SHARD is 100% open source under the permissive MIT License. You are free to inspect, modify, fork, and deploy it for personal and commercial applications.",
cta_title: "Your server. Your network. Your SHARD.",
cta_subtitle: "Experience genuine local AI autonomy. Pool your hardware and run frontier models today.",
cta_btn_install: "Install SHARD Now",
cta_btn_gh: "Explore GitHub Repository",
footer_desc: "Distributed local LLM inference engine pooling GPU VRAM and RAM across LAN machines. Free, open source, and privacy-first.",
footer_col_product: "Product",
footer_col_docs: "Documentation",
footer_col_community: "Community",
footer_copy: "SHARD Project. Distributed Neural Compute. Released under the MIT License."
},
ru: {
nav_product: "О продукте",
nav_how: "Принцип работы",
nav_features: "Возможности",
nav_arch: "Архитектура",
nav_install: "Установка",
nav_docs: "Документация",
nav_cta: "Быстрый старт",
hero_badge: "ТРЕХЪЯЗЫЧНЫЙ ЛОКАЛЬНЫЙ НЕЙРОДВИЖОК // C++20 + MASM AVX2 + FASTAPI",
hero_title: "Одна сеть. Множество серверов. Общая AI-инфраструктура.",
hero_subtitle: "Объединяйте видеопамять GPU и системную память компьютеров локальной сети для запуска тяжелых открытых LLM. На базе PyTorch, FastAPI, llama.cpp RPC и нативных SIMD-ядер.",
hero_cta_primary: "Быстрый старт",
hero_cta_secondary: "Репозиторий GitHub",
hero_telemetry_arch: "АРХИТЕКТУРА",
hero_telemetry_arch_val: "LAYER-SHARDED RPC",
hero_telemetry_hop: "ПЕРЕДАЧА СЛОЯ",
hero_telemetry_hop_val: "~8 КБ / ТОКЕН",
hero_telemetry_discovery: "ОБНАРУЖЕНИЕ",
hero_telemetry_discovery_val: "UDP 8001 BEACON",
hero_telemetry_api: "СОВМЕСТИМОСТЬ",
hero_telemetry_api_val: "OPENAI И ANTHROPIC",
hero_caption_title: "ТОПОЛОГИЯ КЛАСТЕРА",
hero_caption_status: "ОБЪЕДИНЕНО 4 НОДЫ",
about_tag: "РАСПРЕДЕЛЕННЫЕ ВЫЧИСЛЕНИЯ",
about_title: "Что такое SHARD?",
about_desc: "SHARD превращает разрозненные компьютеры в единый нейросетевой кластер. Никаких дорогих серверных карт A100 или интерконнектов NVLink.",
about_card1_num: "01",
about_card1_title: "Проблема",
about_card1_text: "Тяжелые модели на 14B, 32B и 70B параметров не помещаются в 6-12 ГБ VRAM потребительских видеокарт. Разработчики вынуждены платить за облачные API или терпеть низкую скорость процессора.",
about_card2_num: "02",
about_card2_title: "Решение",
about_card2_text: "SHARD объединяет память нескольких машин в вашей домашней или рабочей сети. Ноутбук с 8 ГБ VRAM вместе с рабочим ПК запускают 32B-модели совместно и полностью автономно.",
about_card3_num: "03",
about_card3_title: "Преимущество",
about_card3_text: "Вместо тензорного параллелизма all-reduce, требующего каналы 100 Гбит/с, SHARD использует послойный шардинг llama.cpp RPC. Активация передается порциями по ~8 КБ, что легко выдерживает обычный Wi-Fi или Ethernet.",
calc_title: "ИНТЕРАКТИВНЫЙ СИМУЛЯТОР СОВМЕСТИМОСТИ",
calc_badge: "ПРЕФЛАЙТ РАСЧЕТ",
calc_model_label: "Выберите открытую нейросеть",
calc_gpu1_label: "Нода 1 (Мастер) Свободная VRAM",
calc_gpu2_label: "Нода 2 (Воркер) Свободная RAM / VRAM",
calc_total_label: "Общая память кластера:",
calc_req_label: "Требуемая память модели:",
calc_ratio_label: "Расчетное деление -ts:",
how_tag: "ПРОСТОЙ ПРОТОКОЛ",
how_title: "Как работает SHARD",
how_desc: "От одиночного ПК до распределенного кластера за 4 прозрачных шага. Никаких сложных конфигураций Kubernetes или специальных драйверов.",
step1_num: "ШАГ 01",
step1_title: "CREATE",
step1_text: "Запустите мастер-сервер. SHARD поднимает шлюз FastAPI, считывает ресурсы оборудования и рассылает маяки UDP-обнаружения на порту 8001.",
step2_num: "ШАГ 02",
step2_title: "SHARE",
step2_text: "Машины в локальной сети подключаются одной командой. Каждый узел отдает свободную видеопамять или RAM в общий пул кластера.",
step3_num: "ШАГ 03",
step3_title: "CONNECT",
step3_text: "Клиенты и инструменты разработчика автоматически находят серверы в сети без ручного ввода IP. Открытые сети подключаются в один клик.",
step4_num: "ШАГ 04",
step4_title: "USE",
step4_text: "Подключите Cursor, Claude Code, Aider или терминальный чат напрямую к стандартному эндпоинту /v1. Модели отдают токены на максимальной локальной скорости.",
feat_tag: "ВОЗМОЖНОСТИ",
feat_title: "Функции движка",
feat_desc: "Спроектирован с нуля для минимальных задержек, бережного расхода памяти и удобства разработчика.",
feat1_tag: "СЕТЬ",
feat1_title: "Автообнаружение в LAN без настроек",
feat1_text: "UDP-маяки каждые 2 секунды сообщают о доступных кластерах. Соседние узлы сразу отображаются в панели управления с кнопкой входа.",
feat2_tag: "РАСПРЕДЕЛЕННЫЙ РАНТАЙМ",
feat2_title: "Послойный шардинг llama.cpp RPC",
feat2_text: "Пропорциональное деление тензоров (-ts) распределяет слои под точные размеры памяти нод. По сети передаются лишь небольшие буферы активаций.",
feat3_tag: "ДВОЙНОЙ БЭКЕНД",
feat3_title: "Движки PyTorch и GGUF",
feat3_text: "Запускайте оригинальные веса PyTorch для исследований или высокоэффективные квантованные GGUF-модели для максимального токенов/сек.",
feat4_tag: "НАТИВНОЕ УСКОРЕНИЕ",
feat4_title: "Ядра C++20 и ассемблер MASM AVX2",
feat4_text: "Ручные 64-битные SIMD-ядра обеспечивают субмикросекундные векторные вычисления и кольцевые буферы с нулевым копированием под Windows и Linux.",
feat5_tag: "ИНТЕЛЛЕКТУАЛЬНЫЙ СОВЕТНИК",
feat5_title: "Префлайт-чекер совместимости моделей",
feat5_text: "Выполните shard fit перед скачиванием десятков гигабайт. Анализатор математически проверит, поместится ли модель на один ПК или в LAN-сеть.",
feat6_tag: "СТАНДАРТНЫЙ API",
feat6_title: "Полная совместимость с OpenAI и Anthropic",
feat6_text: "Поддержка эндпоинтов /v1/chat/completions и /v1/messages со стримингом токенов (SSE) и вызовом внешних функций (tool calling).",
showcase_tab_dash: "Обзор веб-панели управления",
showcase_tab_cli: "Консольный советник совместимости",
arch_tag: "ВНУТРЕННЕЕ УСТРОЙСТВО",
arch_title: "Архитектура системы",
arch_desc: "Модульная архитектура без скрытых слоев. Нажмите на любой компонент слева, чтобы изучить его протоколы, порты и исходный файл.",
install_tag: "БЫСТРЫЙ СТАРТ",
install_title: "Установка и запуск",
install_desc: "Выберите подходящий вариант. SHARD работает как на отдельном ноутбуке разработчика, так и в кластерах локальной сети.",
tab_pip: "Из исходников (Editable)",
tab_docker: "Docker Compose",
tab_native: "Сборка ядер C++20",
pip_step1: "1. Клонирование репозитория",
pip_step2: "2. Установка пакета через Pip",
pip_step3: "3. Запуск веб-панели и сервера API",
pip_step4: "4. Создание распределенной сети",
cases_tag: "ПРИМЕНЕНИЕ",
cases_title: "Сценарии использования",
cases_desc: "Реальные задачи, где SHARD решает нехватку памяти без дорогой подписки на облачные сервисы.",
case1_badge: "ДОМАШНИЙ КЛАСТЕР",
case1_title: "Супернода: Ноутбук + Офисный ПК",
case1_desc: "Объедините игровой ноутбук с RTX 3060 (6 ГБ VRAM) и настольный ПК (16 ГБ RAM). Вместе они дают 22 ГБ памяти и плавно крутят модель 32B.",
case1_stat: "ПУЛ: 22 ГБ // КВАНТ 32B ПОМЕЩАЕТСЯ: ДА",
case2_badge: "РАЗРАБОТКА ПО",
case2_title: "Безлимитный бэкенд для AI-ассистентов",
case2_desc: "Подключите Cursor, Claude Code или Aider к адресу http://localhost:8000/v1. Бесконечный автокомплит и рефакторинг без оплаты токенов.",
case2_stat: "ПИНГ: ~18 МС // СТОИМОСТЬ: 0 РУБ / МЕС",
case3_badge: "БЕЗОПАСНОСТЬ И ПРИВАТНОСТЬ",
case3_title: "100% Изолированная корпоративная лаборатория",
case3_desc: "Работайте с закрытыми репозиториями и конфиденциальными данными. Ни один байт кода или промпта не покидает пределы вашего офиса.",
case3_stat: "ВЫХОД В ОБЛАКО: НОЛЬ // ДАННЫЕ: В LAN",
faq_tag: "ВОПРОСЫ И ОТВЕТЫ",
faq_title: "Часто задаваемые вопросы",
faq_desc: "Технические подробности, системные требования и совместимость с оборудованием.",
faq_q1: "Какое оборудование нужно для старта с SHARD?",
faq_a1: "Достаточно одного компьютера с Windows или Linux и процессором с поддержкой AVX2. Видеокарта NVIDIA ускоряет инференс, а подключение других ПК по сети автоматически объединяет их память.",
faq_q2: "Как именно SHARD объединяет память разных компьютеров?",
faq_a2: "SHARD использует встроенный RPC-протокол llama.cpp. Мастер-сервер рассчитывает бюджеты свободной памяти всех участников и делит слои модели пропорционально через ключ -ts.",
faq_q3: "Требуется ли сеть 10 Гбит/с или мостик NVLink?",
faq_a3: "Нет. Поскольку используется послойный шардинг (pipeline/layer sharding), по сети передаются лишь скрытые состояния (~8 КБ на токен). Стандартного Ethernet 1 Гбит/с или Wi-Fi более чем достаточно.",
faq_q4: "Можно ли подключить редакторы Cursor или Claude Code?",
faq_a4: "Да. SHARD предоставляет полностью совместимые эндпоинты /v1/chat/completions (OpenAI) и /v1/messages (Anthropic) со стримингом SSE. Просто укажите базовый URL http://localhost:8000/v1.",
faq_q5: "Что такое 'Открытая сеть' в SHARD?",
faq_a5: "В версии 1.8.0 появился режим открытой сети: сервер транслирует открытый флаг в UDP-маяке, и другие машины в той же подсети могут подключиться нажатием одной кнопки без ввода кода.",
faq_q6: "Является ли проект полностью открытым?",
faq_a6: "Да. SHARD распространяется по свободной лицензии MIT. Вы можете свободно изучать код, форкать, модифицировать и использовать движок в любых личных и коммерческих целях.",
cta_title: "Ваш сервер. Ваша сеть. Ваш SHARD.",
cta_subtitle: "Обретите подлинную независимость в сфере искусственного интеллекта. Объедините ресурсы и запустите мощную модель прямо сейчас.",
cta_btn_install: "Установить SHARD",
cta_btn_gh: "Открыть репозиторий GitHub",
footer_desc: "Распределенный нейродвижок для локальных LLM, объединяющий VRAM и RAM в локальной сети. Бесплатный, открытый и приватный.",
footer_col_product: "Продукт",
footer_col_docs: "Документация",
footer_col_community: "Сообщество",
footer_copy: "Проект SHARD. Распределенные нейровычисления. Лицензия MIT."
}
};
const ARCH_DETAILS = {
client: {
en: {
name: "Client Applications & AI Agents",
role: "Inference Consumer",
desc: "Coding copilots (Cursor, Claude Code, Aider, Zed), terminal REPL (shard run), and browser UIs connecting over HTTP/SSE. No local model weights needed.",
tags: ["OpenAI /v1", "Anthropic /v1", "Streaming SSE", "Zero Local Weights"]
},
ru: {
name: "Клиентские приложения и AI-агенты",
role: "Потребитель инференса",
desc: "Редакторы кода (Cursor, Claude Code, Aider, Zed), консольный чат (shard run) и веб-интерфейсы, подключающиеся по HTTP/SSE. Локальные веса не требуются.",
tags: ["OpenAI /v1", "Anthropic /v1", "Потоковый SSE", "Без локальных весов"]
}
},
gateway: {
en: {
name: "FastAPI REST Gateway (server.py)",
role: "API Gateway & Router",
desc: "Binds to 0.0.0.0:8000. Provides OpenAI and Anthropic compatible endpoints, token authentication middleware, LAN management routes (/api/lan/*), and serves the dashboard.",
tags: ["FastAPI", "Uvicorn", "Port 8000", "Token Auth", "server.py"]
},
ru: {
name: "Шлюз FastAPI REST (server.py)",
role: "Шлюз API и маршрутизация",
desc: "Слушает 0.0.0.0:8000. Предоставляет эндпоинты OpenAI и Anthropic, проверку токенов, управление LAN-сетью (/api/lan/*) и веб-панель управления.",
tags: ["FastAPI", "Uvicorn", "Порт 8000", "Проверка токенов", "server.py"]
}
},
master: {
en: {
name: "Master llama-server & Tensor Splitter",
role: "Cluster Coordinator (Device 0)",
desc: "Executes llama-server with --rpc targets and -ts weights. Hosts model weights, coordinates inference layers, and handles master GPU/RAM device budget allocation.",
tags: ["llama-server", "--rpc flag", "-ts split", "VRAM Budget", "lan.py"]
},
ru: {
name: "Мастер llama-server и расчет тензоров",
role: "Координатор кластера (Device 0)",
desc: "Запускает llama-server с параметрами --rpc и -ts. Хранит веса модели, координирует расчет слоев и выделяет квоту локальной памяти мастера.",
tags: ["llama-server", "Ключ --rpc", "Деление -ts", "Бюджет VRAM", "lan.py"]
}
},
beacon: {
en: {
name: "UDP Beacon Discovery Daemon (lan.py)",
role: "Zero-Config Peer Discovery",
desc: "Permanent background thread broadcasting JSON announces on UDP port 8001 every 2s. Advertises cluster name, active model, open-network flag, and RPC port.",
tags: ["UDP 8001", "JSON Announce", "2s Interval", "SO_REUSEADDR", "lan.py"]
},
ru: {
name: "Демон UDP-маяков (lan.py)",
role: "Автообнаружение в подсети",
desc: "Фоновый поток, рассылающий JSON-маяки на UDP-порт 8001 каждые 2 секунды. Сообщает имя кластера, загруженную модель, флаг открытой сети и RPC-порт.",
tags: ["UDP 8001", "JSON Маяк", "Интервал 2с", "SO_REUSEADDR", "lan.py"]
}
},
worker: {
en: {
name: "Worker Node (ggml-rpc-server.exe)",
role: "Distributed Compute Member",
desc: "Runs bundled ggml-rpc-server on port 50052. Sends 1s telemetry heartbeats to master. Evaluates its assigned model layers directly in local VRAM or RAM.",
tags: ["ggml-rpc-server", "Port 50052", "1s Heartbeat", "Zero Weights Download"]
},
ru: {
name: "Вычислительный узел (ggml-rpc-server.exe)",
role: "Участник распределенных вычислений",
desc: "Запускает штатный ggml-rpc-server на порту 50052. Отправляет телеметрию мастеру раз в секунду. Обрабатывает выделенные слои модели в своей памяти.",
tags: ["ggml-rpc-server", "Порт 50052", "Пульс 1с", "Без скачивания весов"]
}
},
native: {
en: {
name: "Native C++20 & MASM AVX2 Kernels",
role: "Hardware Acceleration Layer",
desc: "Handcrafted 64-bit assembler kernels in native/shard_asm.asm and Win32 C++20 bridge in native/shard_core.cpp. Provides zero-copy memory buffers and sub-microsecond vector operations.",
tags: ["MASM x64", "AVX2 SIMD", "C++20 DLL", "Zero-Copy", "shard_core.cpp"]
},
ru: {
name: "Нативные ядра C++20 и MASM AVX2",
role: "Слой аппаратного ускорения",
desc: "64-битные ассемблерные ядра в native/shard_asm.asm и Win32 C++20 мост в native/shard_core.cpp. Обеспечивают кольцевые буферы с нулевым копированием и быстрые векторные инструкции.",
tags: ["MASM x64", "AVX2 SIMD", "C++20 DLL", "Zero-Copy", "shard_core.cpp"]
}
}
};
const MODELS_DATA = [
{ id: "qwen15", name: "Qwen 2.5 Coder 1.5B (Q4_K_M)", sizeMb: 1250, reqGb: 1.25 },
{ id: "qwen7", name: "Qwen 2.5 Coder 7B (Q4_K_M)", sizeMb: 4700, reqGb: 4.70 },
{ id: "qwen14", name: "Qwen 2.5 Coder 14B (Q4_K_M)", sizeMb: 9200, reqGb: 9.20 },
{ id: "qwen32", name: "Qwen 2.5 32B Instruct (Q4_K_M)", sizeMb: 19800, reqGb: 19.80 },
{ id: "deepseek32", name: "DeepSeek R1 Distill 32B (Q4_K_M)", sizeMb: 19800, reqGb: 19.80 },
{ id: "llama70", name: "Llama 3.3 70B Instruct (Q4_K_M)", sizeMb: 42500, reqGb: 42.50 }
];
let currentLang = localStorage.getItem("shard-lang") || "en";
let currentTheme = localStorage.getItem("shard-theme") || "paper";
function setLanguage(lang) {
currentLang = lang;
localStorage.setItem("shard-lang", lang);
document.documentElement.lang = lang;
const dict = I18N_DATA[lang] || I18N_DATA.en;
document.querySelectorAll("[data-i18n]").forEach(el => {
const key = el.getAttribute("data-i18n");
if (dict[key]) {
el.textContent = dict[key];
}
});
const langBtnText = document.getElementById("lang-btn-text");
if (langBtnText) {
langBtnText.textContent = lang === "en" ? "RU" : "EN";
}
updateCalculator();
const selectedNode = document.querySelector(".arch-node.selected");
if (selectedNode) {
inspectArchNode(selectedNode.getAttribute("data-node"));
}
}
function setTheme(theme) {
currentTheme = theme;
localStorage.setItem("shard-theme", theme);
if (theme === "terminal") {
document.documentElement.setAttribute("data-theme", "terminal");
} else {
document.documentElement.removeAttribute("data-theme");
}
const themeBtnText = document.getElementById("theme-btn-text");
if (themeBtnText) {
themeBtnText.textContent = theme === "terminal" ? "PAPER" : "TERMINAL";
}
}
function inspectArchNode(nodeKey) {
document.querySelectorAll(".arch-node").forEach(el => {
el.classList.toggle("selected", el.getAttribute("data-node") === nodeKey);
});
const info = ARCH_DETAILS[nodeKey] ? (ARCH_DETAILS[nodeKey][currentLang] || ARCH_DETAILS[nodeKey].en) : null;
if (!info) return;
const titleEl = document.getElementById("inspector-node-title");
const roleEl = document.getElementById("inspector-node-role");
const descEl = document.getElementById("inspector-node-desc");
const tagsEl = document.getElementById("inspector-tags-list");
if (titleEl) titleEl.textContent = info.name;
if (roleEl) roleEl.textContent = info.role;
if (descEl) descEl.textContent = info.desc;
if (tagsEl) {
tagsEl.innerHTML = "";
info.tags.forEach(t => {
const sp = document.createElement("span");
sp.className = "inspector-tag";
sp.textContent = t;
tagsEl.appendChild(sp);
});
}
}
function updateCalculator() {
const modelSelect = document.getElementById("calc-model");
const gpu1Slider = document.getElementById("calc-gpu1");
const gpu2Slider = document.getElementById("calc-gpu2");
if (!modelSelect || !gpu1Slider || !gpu2Slider) return;
const modelId = modelSelect.value;
const model = MODELS_DATA.find(m => m.id === modelId) || MODELS_DATA[1];
const node1Vram = parseInt(gpu1Slider.value, 10);
const node2Mem = parseInt(gpu2Slider.value, 10);
document.getElementById("calc-gpu1-val").textContent = node1Vram + " GB";
document.getElementById("calc-gpu2-val").textContent = node2Mem + " GB";
const node1BudgetMb = Math.max(0, (node1Vram * 1024) - 400);
const node2BudgetMb = Math.max(0, (node2Mem * 1024) - 2048);
const poolCapMb = node1BudgetMb + node2BudgetMb;
const poolCapGb = (poolCapMb / 1024).toFixed(1);
const totalPooledEl = document.getElementById("calc-total-val");
const reqValEl = document.getElementById("calc-req-val");
const splitValEl = document.getElementById("calc-split-val");
const badgeEl = document.getElementById("calc-status-badge");
if (totalPooledEl) totalPooledEl.textContent = poolCapGb + " GB";
if (reqValEl) reqValEl.textContent = model.reqGb.toFixed(2) + " GB";
const ratio1 = Math.round((node1BudgetMb / (node1BudgetMb + node2BudgetMb || 1)) * 100);
const ratio2 = 100 - ratio1;
if (splitValEl) splitValEl.textContent = ratio1 + "% / " + ratio2 + "% (ts: " + node1BudgetMb + "," + node2BudgetMb + ")";
if (badgeEl) {
badgeEl.className = "calc-status-badge";
if (node1BudgetMb >= model.sizeMb) {
badgeEl.classList.add("fits");
badgeEl.textContent = currentLang === "ru" ? "Помещается на одной локальной ноде (VRAM OK)" : "Fits on Single Local Node (VRAM OK)";
} else if (poolCapMb >= model.sizeMb) {
badgeEl.classList.add("needs-pool");
badgeEl.textContent = currentLang === "ru" ? "Кластер активен: модель распределена по сети LAN" : "Cluster Active: Model Pooled Across LAN";
} else {
badgeEl.classList.add("too-big");
const deficit = ((model.sizeMb - poolCapMb) / 1024).toFixed(1);
badgeEl.textContent = currentLang === "ru" ? "Не хватает " + deficit + " ГБ — добавьте еще одну ноду в LAN" : "Short by " + deficit + " GB — Add another node to LAN";
}
}
}
function setupTabs() {
document.querySelectorAll(".showcase-tab-btn").forEach(btn => {
btn.addEventListener("click", () => {
document.querySelectorAll(".showcase-tab-btn").forEach(b => b.classList.remove("active"));
document.querySelectorAll(".showcase-panel").forEach(p => p.classList.remove("active"));
btn.classList.add("active");
const target = btn.getAttribute("data-tab");
const panel = document.getElementById(target);
if (panel) panel.classList.add("active");
});
});
document.querySelectorAll(".install-tab-btn").forEach(btn => {
btn.addEventListener("click", () => {
document.querySelectorAll(".install-tab-btn").forEach(b => b.classList.remove("active"));
document.querySelectorAll(".install-panel").forEach(p => p.classList.remove("active"));
btn.classList.add("active");
const target = btn.getAttribute("data-install-tab");
const panel = document.getElementById(target);
if (panel) panel.classList.add("active");
});
});
}
function setupCopyButtons() {
document.querySelectorAll(".copy-code-btn").forEach(btn => {
btn.addEventListener("click", () => {
const codeBlock = btn.closest(".terminal-snippet, .code-block-box");
if (!codeBlock) return;
const code = codeBlock.querySelector("code, pre");
if (!code) return;
navigator.clipboard.writeText(code.innerText.trim()).then(() => {
const origHtml = btn.innerHTML;
btn.innerHTML = '<svg class="control-icon" viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>';
setTimeout(() => { btn.innerHTML = origHtml; }, 2000);
});
});
});
}
function setupFaq() {
document.querySelectorAll(".faq-question-btn").forEach(btn => {
btn.addEventListener("click", () => {
const item = btn.closest(".faq-item");
if (!item) return;
const wasOpen = item.classList.contains("open");
document.querySelectorAll(".faq-item").forEach(i => i.classList.remove("open"));
if (!wasOpen) {
item.classList.add("open");
}
});
});
}
function setupMobileNav() {
const toggle = document.querySelector(".nav-mobile-toggle");
const navLinks = document.querySelector(".nav-links");
if (!toggle || !navLinks) return;
toggle.addEventListener("click", () => {
navLinks.classList.toggle("mobile-open");
});
navLinks.querySelectorAll(".nav-link").forEach(link => {
link.addEventListener("click", () => {
navLinks.classList.remove("mobile-open");
});
});
}
document.addEventListener("DOMContentLoaded", () => {
setTheme(currentTheme);
setLanguage(currentLang);
setupTabs();
setupCopyButtons();
setupFaq();
setupMobileNav();
const langToggleBtn = document.getElementById("lang-toggle-btn");
if (langToggleBtn) {
langToggleBtn.addEventListener("click", () => {
setLanguage(currentLang === "en" ? "ru" : "en");
});
}
const themeToggleBtn = document.getElementById("theme-toggle-btn");
if (themeToggleBtn) {
themeToggleBtn.addEventListener("click", () => {
setTheme(currentTheme === "paper" ? "terminal" : "paper");
});
}
const modelSelect = document.getElementById("calc-model");
const gpu1Slider = document.getElementById("calc-gpu1");
const gpu2Slider = document.getElementById("calc-gpu2");
if (modelSelect) modelSelect.addEventListener("change", updateCalculator);
if (gpu1Slider) gpu1Slider.addEventListener("input", updateCalculator);
if (gpu2Slider) gpu2Slider.addEventListener("input", updateCalculator);
updateCalculator();
document.querySelectorAll(".arch-node").forEach(node => {
node.addEventListener("click", () => {
inspectArchNode(node.getAttribute("data-node"));
});
});
inspectArchNode("master");
});