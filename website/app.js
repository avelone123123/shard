const I18N = {
  en: {
    nav_story: "How it flows",
    nav_fit: "Fit simulator",
    nav_workflow: "First run",
    nav_arch: "Backends",
    nav_gov: "Limits",
    nav_cli: "CLI",
    nav_install: "Install",
    nav_cta: "Install",
    pill_1: "Motion",
    pill_2: "Fit",
    pill_3: "Cluster",
    pill_4: "Engine",
    pill_5: "Limits",
    hero_title: "Pool everyday GPUs.<br><span class=\"accent-phrase\">Run giant AI together.</span>",
    hero_subtitle: "Combine free VRAM and system RAM across machines on your network to execute open-weight models that exceed one card. Drop-in OpenAI and Anthropic APIs.",
    hero_cta_primary: "Simulate cluster VRAM",
    hero_cta_secondary: "View on GitHub",
    beat_0: "01 Detect",
    beat_1: "02 Partition",
    beat_2: "03 Stream",
    beat_3: "04 Recover",
    tl_0_k: "Stage 01",
    tl_0_v: "Probe & Reserve",
    tl_1_k: "Stage 02",
    tl_1_v: "Fit & Partition",
    tl_2_k: "Stage 03",
    tl_2_v: "Stream /v1 API",
    story_title: "Make idle hardware tangible.<br><span class=\"muted-line\">One pool you can measure.</span><br><span class=\"muted-line\">One API you already use.</span>",
    bento_1_t: "Hard OS safety reserves",
    bento_1_d: "Probes live CUDA VRAM and RAM while locking 400 MB VRAM and 2 GB RAM for your desktop so nothing freezes.",
    bento_2_t: "Pre-flight fit before a single gigabyte downloads",
    bento_2_d: "Reads exact Hugging Face config.json GQA heads, calculates KV cache at 4K–32K context, and scores every quant tier.",
    bento_3_t: "Compact ~8 KB activations over LAN",
    bento_3_d: "Passes hidden-state tensors sequentially from stage to stage over Ethernet or Wi-Fi and streams SSE tokens to your IDE.",
    about_title: "See what fits on your machines before you run it",
    about_desc: "Capacity is not speed. Pooling lets a 32B model fit across everyday cards that crash OOM alone, while single-GPU stays faster whenever weights already fit locally.",
    fact_2_v: "Network hops add latency per token. If a model fits cleanly on one local GPU, local execution stays faster.",
    calc_title: "Pre-flight cluster fit simulator",
    calc_sub: "Exact shard fit reserve math (400 MB VRAM and 2 GB RAM reserved for OS).",
    calc_model_label: "Open-weight model (Q4_K_M + 4K KV cache)",
    calc_gpu1_label: "Node 1 (Coordinator) VRAM",
    calc_gpu2_label: "Node 2 (Worker) VRAM / RAM",
    calc_bar_label: "Memory allocation across nodes",
    calc_req_label: "Model + KV required",
    calc_total_label: "Usable cluster pool",
    calc_ratio_label: "Proportional -ts split",
    calc_ok_single: "FITS ON NODE 1",
    calc_ok_pool: "FITS IN POOL",
    calc_fail: "EXCEEDS POOL",
    calc_leg_1: "Node 1 usable:",
    calc_leg_2: "Node 2 usable:",
    how_title: "One prompt, start to finish",
    how_desc: "Four explicit commands take you from an empty terminal to a real multi-PC cluster serving Cursor, Claude Code, or any OpenAI/Anthropic SDK.",
    shot_catalog: "Hardware & catalog",
    shot_fit: "shard fit CLI",
    shot_cluster: "Cluster stages UI",
    shot_cap_catalog: "Real SHARD Dashboard: live RTX 2050 VRAM/RAM telemetry and model compatibility tiers.",
    shot_cap_fit: "Real shard fit CLI: pre-flight quantization and layer placement report before downloading.",
    shot_cap_cluster: "Real SHARD v1.9.0 Dashboard: partition stages and live worker VRAM telemetry.",
    step1_title: "Install and start the runtime",
    step1_text: "SHARD detects local NVIDIA GPUs, system RAM, CPU threads, and available runtimes, then launches the Web Dashboard and local API gateway on port 8000.",
    step2_title: "Check whether a model fits before downloading",
    step2_text: "Inspect exact Hugging Face architecture parameters, GQA KV-cache size, and quant tiers so you never waste bandwidth on a checkpoint that crashes OOM.",
    step3_title: "Pool a second machine with explicit consent",
    step3_text: "Create a LAN pool or cluster on the coordinator. Other PCs discover it on UDP 8001 (or connect by IP) and opt in with explicit VRAM and RAM quotas.",
    step4_title: "Point your tools at localhost:8000",
    step4_text: "Use Cursor, Claude Code, OpenCode, or the official OpenAI and Anthropic SDKs without changing your workflow or sending prompts to a third-party cloud.",
    btn_copy: "Copy",
    btn_copied: "Copied",
    arch_title: "Inside the execution engine",
    arch_desc: "Scroll or select any subsystem to see its live 40×40 LED schematic, exact Python modules, and CLI behavior.",
    wb_coord: "Coordinator & planner",
    wb_gguf: "GGUF RPC layer split",
    wb_torch: "PyTorch pipeline parallel",
    wb_resilience: "Worker failure resilience",
    wb_security: "Consent, auth & isolation",
    wb_scroll_hint: "scroll to flip",
    gov_title: "The engine works inside limits you set",
    gov_sub: "You choose solo mode or opt-in LAN worker quotas. SHARD enforces OS memory reserves automatically, always asks before sharing hardware, and never runs silently.",
    spec_left: "Handled by planner",
    spec_right: "Locked to your explicit consent",
    gov_k1: "Set by SHARD",
    gov_c1_t: "On its own",
    gov_c1_d: "Local diagnostics that never leave your machine.",
    gov_c1_i1: "Probe NVIDIA VRAM, RAM & CPU",
    gov_c1_i2: "Calculate GQA KV cache & quant fit",
    gov_c1_i3: "Detect worker TCP loss & free KV slots",
    gov_k2: "Set by you",
    gov_c2_t: "Up to you",
    gov_c2_d: "Toggle between solo local mode and opt-in LAN worker sharing.",
    gov_c2_i1: "Expose VRAM/RAM quota",
    gov_c2_i2: "Accept LAN pairing code",
    gov_solo: "Solo GPU",
    gov_worker: "LAN Worker",
    gov_k3: "Set by SHARD",
    gov_c3_t: "Always waits for you",
    gov_c3_d: "Explicit command or consent flag required in every mode.",
    gov_c3_i1: "Join a cluster (--share / consent=true)",
    gov_c3_i2: "Download multi-GB model weights",
    gov_c3_i3: "Export feedback for SOUP fine-tuning",
    gov_k4: "Hard boundary",
    gov_c4_t: "Never",
    gov_c4_d: "Blocked in engine code regardless of configuration.",
    gov_c4_i1: "Silent background GPU sharing (HTTP 403)",
    gov_c4_i2: "Remote peer access to admin /api/* routes",
    gov_c4_i3: "Prompt logging without explicit opt-in",
    mat_title: "What works today, and what is still experimental",
    mat_desc: "Honest engineering boundaries matter more than marketing promises. Local inference, hardware fit, LAN pooling, and worker resilience are implemented and tested; Internet-wide cooperative networking is the active direction.",
    st_impl: "Implemented",
    st_exp: "Experimental",
    st_plan: "Vision / Planned",
    mat_r1_c: "Local GGUF & PyTorch inference + shard fit",
    mat_r1_d: "Runs llama-server GGUF and Hugging Face SafeTensors locally with pre-flight VRAM/RAM fit checks and Web Dashboard.",
    mat_r2_c: "LAN memory pooling & distributed clusters",
    mat_r2_d: "UDP 8001 discovery, pairing codes, llama.cpp RPC (-ts split), and PyTorch pipeline parallelism across 8 decoder families.",
    mat_r3_c: "Worker failure detection & pipeline recovery",
    mat_r3_d: "4-state worker FSM, bounded timeouts, and PyTorch recovery via context replay (24/24 local multi-process resilience tests; GGUF terminates cleanly after token 1).",
    mat_r4_c: "Ed25519 identity, self-hosted Discovery & Blind Relay",
    mat_r4_d: "Implemented and verified on loopback with ChaCha20-Poly1305 AEAD tunnels; cross-machine WAN chaos testing is still pending.",
    mat_r5_c: "Public global compute network",
    mat_r5_d: "No public hosted discovery or relay cluster is deployed yet. Cross-network tests today require self-hosted relay or a VPN (WireGuard, Tailscale, Radmin).",
    cli_title: "One CLI for models, pools, and clusters",
    cli_t_server: "Server",
    cli_t_models: "Models & Fit",
    cli_t_lan: "LAN Pool",
    cli_t_cluster: "Cluster",
    cli_t_net: "Self-hosted Net",
    cli_t_train: "Training (SOUP)",
    inst_title: "What you need",
    inst_sub: "Pick your setup to see practical requirements.",
    req_gpu: "Single NVIDIA GPU",
    req_lan: "Multi-PC LAN pool",
    req_cpu: "CPU + System RAM",
    req_note_gpu: "Runs 1.5B to 14B quantized GGUF models locally in pure GPU or hybrid VRAM+RAM mode via llama-server.",
    spec_py: "Windows, Linux, and macOS. Optional SOUP training uses Python 3.11–3.13.",
    spec_fmt: "PyTorch pipeline supports llama, qwen2, qwen3, mistral, gemma, gemma2, phi3, gpt2.",
    spec_api: "Works with Cursor, Claude Code, OpenCode, Continue, and standard SDKs via /v1/*.",
    spec_lic: "Zero silent GPU sharing, zero prompt logging without explicit consent.",
    faq_title: "Before you install",
    faq_sub: "Direct answers to the engineering questions people ask first.",
    faq_q1: "Does SHARD use the BitTorrent protocol?",
    faq_a1: "No. BitTorrent is a conceptual analogy for cooperative peer-to-peer resource pooling. SHARD does not download torrent chunks or split generated text; it partitions neural network layers across workers using llama.cpp RPC or PyTorch pipeline sockets and passes intermediate activations sequentially.",
    faq_q2: "Does adding more computers always make inference faster?",
    faq_a2: "No. Capacity is not throughput. Pooling memory lets you run models that cannot fit on a single GPU, but each generated token travels sequentially across machines. If a model already fits cleanly in one local GPU, local execution is faster.",
    faq_q3: "Will SHARD use my GPU in the background without permission?",
    faq_a3: "Never. Worker contribution is strictly opt-in (--share or consent=true), rejects unconsented join calls with HTTP 403, enforces hard OS memory reserves, and can be stopped immediately with shard lan off or shard cluster leave.",
    faq_q4: "What happens if a worker crashes mid-generation?",
    faq_a4: "The coordinator detects TCP loss, timeouts, or missed heartbeats, frees KV caches and scheduler slots, and reforms on surviving healthy nodes. PyTorch pipeline mode can resume via full-context prefill replay without duplicating tokens; GGUF RPC restarts before token 1 or returns a structured API error after streaming starts.",
    faq_q5: "Is there already a live public global network I can join?",
    faq_a5: "Not yet. Public discovery and relay hosts are not deployed yet. Today SHARD runs reliably locally, across your LAN, over VPNs like Tailscale or WireGuard, or with your own self-hosted discovery-serve and relay-serve nodes.",
    cta_title: "Start on one machine.<br>Expand when you need more VRAM.",
    footer_desc: "Open, cooperative distributed AI inference infrastructure. MIT License.",
    foot_docs: "Documentation",
    foot_reports: "Engineering Reports",
    foot_proj: "Project",
    footer_legal: "SHARD Engine v1.9.0 · Open-source distributed AI inference · Built for everyday hardware."
  },
  ru: {
    nav_story: "Как работает",
    nav_fit: "Калькулятор",
    nav_workflow: "Первый запуск",
    nav_arch: "Бэкенды",
    nav_gov: "Границы",
    nav_cli: "CLI",
    nav_install: "Установка",
    nav_cta: "Установить",
    pill_1: "Схема",
    pill_2: "Расчёт",
    pill_3: "Кластер",
    pill_4: "Движок",
    pill_5: "Границы",
    hero_title: "Объединяйте обычные GPU.<br><span class=\"accent-phrase\">Запускайте большие модели.</span>",
    hero_subtitle: "Складывайте свободную VRAM и системную RAM нескольких компьютеров в сети, чтобы запускать open-weight модели, не влезающие на одну видеокарту. Совместимость с OpenAI и Anthropic API.",
    hero_cta_primary: "Рассчитать пул VRAM",
    hero_cta_secondary: "Открыть на GitHub",
    beat_0: "01 Поиск",
    beat_1: "02 Шарды",
    beat_2: "03 Поток",
    beat_3: "04 Резерв",
    tl_0_k: "Этап 01",
    tl_0_v: "Сканер и резерв",
    tl_1_k: "Этап 02",
    tl_1_v: "Fit и слои",
    tl_2_k: "Этап 03",
    tl_2_v: "Стрим /v1 API",
    story_title: "Сделайте простаивающее железо осязаемым.<br><span class=\"muted-line\">Пул, который можно измерить.</span><br><span class=\"muted-line\">API, к которому вы привыкли.</span>",
    bento_1_t: "Жёсткие резервы под ОС",
    bento_1_d: "Определяет свободную CUDA VRAM и RAM, резервируя 400 МБ VRAM и 2 ГБ RAM под систему без подвисаний.",
    bento_2_t: "Проверка до скачивания гигабайтов",
    bento_2_d: "Читает точный config.json с GQA-головами, считает KV-кэш на 4K–32K контекста и оценивает каждый квант.",
    bento_3_t: "Компактные активации ~8 КБ по LAN",
    bento_3_d: "Передаёт скрытые состояния по слоям через Ethernet или Wi-Fi и стримит SSE-токены прямо в ваш редактор.",
    about_title: "Узнайте, что поместится на ваших ПК, до запуска",
    about_desc: "Объём памяти — это не скорость. Пул вмещает 32B-модель на обычных картах там, где один ПК падает с OOM, а локальный запуск остаётся быстрее, если веса влезают в одну GPU.",
    fact_2_v: "Сетевые переходы добавляют задержку на каждый токен. Если модель влезает в одну GPU, локальный запуск быстрее.",
    calc_title: "Симулятор распределения памяти кластера",
    calc_sub: "Та же формула резервов, что в shard fit (400 МБ VRAM и 2 ГБ RAM под ОС).",
    calc_model_label: "Open-weight модель (Q4_K_M + KV-кэш 4K)",
    calc_gpu1_label: "Узел 1 (Координатор) VRAM",
    calc_gpu2_label: "Узел 2 (Воркер) VRAM / RAM",
    calc_bar_label: "Распределение памяти по узлам",
    calc_req_label: "Нужно (веса + KV)",
    calc_total_label: "Доступный пул",
    calc_ratio_label: "Пропорция -ts",
    calc_ok_single: "ВЛЕЗАЕТ НА УЗЕЛ 1",
    calc_ok_pool: "ВЛЕЗАЕТ В ПУЛ",
    calc_fail: "МАЛО ПАМЯТИ",
    calc_leg_1: "Узел 1 доступно:",
    calc_leg_2: "Узел 2 доступно:",
    how_title: "Один промпт от старта до ответа",
    how_desc: "Четыре понятные команды превращают пустой терминал в многокомпьютерный кластер для Cursor, Claude Code или любого SDK.",
    shot_catalog: "Железо и каталог",
    shot_fit: "CLI shard fit",
    shot_cluster: "Стадии кластера",
    shot_cap_catalog: "Реальный Dashboard SHARD: телеметрия RTX 2050 VRAM/RAM и каталог моделей.",
    shot_cap_fit: "Реальный CLI shard fit: оценка квантования и слоёв до скачивания весов.",
    shot_cap_cluster: "Реальный Dashboard SHARD v1.9.0: сегменты слоёв и телеметрия 3 воркеров.",
    step1_title: "Установите и запустите движок",
    step1_text: "SHARD определяет GPU NVIDIA, системную RAM, потоки CPU и рантаймы, затем поднимает Web Dashboard и API-шлюз на порту 8000.",
    step2_title: "Проверьте модель до скачивания",
    step2_text: "Узнайте точное число параметров по config.json, размер GQA KV-кэша и вердикт по квантам до загрузки весов.",
    step3_title: "Подключите второй компьютер с явным согласием",
    step3_text: "Создайте LAN-пул на координаторе. Другие ПК видят его по UDP 8001 (или по IP) и отдают память строго по указанной квоте.",
    step4_title: "Направьте инструменты на localhost:8000",
    step4_text: "Cursor, Claude Code, OpenCode и стандартные SDK OpenAI/Anthropic работают без смены рабочего процесса.",
    btn_copy: "Копировать",
    btn_copied: "Скопировано",
    arch_title: "Внутри движка выполнения",
    arch_desc: "Прокручивайте или выбирайте подсистему — справа живая LED-матрица 40×40, реальные Python-модули и CLI-вывод.",
    wb_coord: "Координатор и планировщик",
    wb_gguf: "GGUF RPC разбиение",
    wb_torch: "PyTorch Pipeline Parallel",
    wb_resilience: "Отказоустойчивость воркеров",
    wb_security: "Согласие, доступ и изоляция",
    wb_scroll_hint: "скролльте для смены",
    gov_title: "Движок работает строго в заданных границах",
    gov_sub: "Вы сами выбираете локальный режим или квоты воркера в LAN. Резервы памяти ОС защищены всегда, а скрытого шаринга не бывает.",
    spec_left: "Делает планировщик",
    spec_right: "Только по вашему явному согласию",
    gov_k1: "Автоматически",
    gov_c1_t: "Самостоятельно",
    gov_c1_d: "Локальная диагностика, не покидающая ваш ПК.",
    gov_c1_i1: "Определение NVIDIA VRAM, RAM и CPU",
    gov_c1_i2: "Расчёт GQA KV-кэша и квантования",
    gov_c1_i3: "Обнаружение обрыва воркера и очистка KV",
    gov_k2: "Настраиваете вы",
    gov_c2_t: "На ваш выбор",
    gov_c2_d: "Переключайтесь между локальным режимом и вкладом в LAN.",
    gov_c2_i1: "Выделение квоты VRAM/RAM",
    gov_c2_i2: "Приём кода спаривания LAN",
    gov_solo: "Только мой ПК",
    gov_worker: "Воркер LAN",
    gov_k3: "Ждёт подтверждения",
    gov_c3_t: "Всегда спрашивает",
    gov_c3_d: "Требует явной команды или флага в любом режиме.",
    gov_c3_i1: "Вход в кластер (--share / consent=true)",
    gov_c3_i2: "Скачивание многогигабайтных весов",
    gov_c3_i3: "Экспорт фидбэка для SOUP-обучения",
    gov_k4: "Жёсткий запрет",
    gov_c4_t: "Никогда",
    gov_c4_d: "Заблокировано на уровне кода при любых настройках.",
    gov_c4_i1: "Скрытый фоновый майнинг GPU (HTTP 403)",
    gov_c4_i2: "Доступ удалённых пиров к админ /api/*",
    gov_c4_i3: "Логирование промптов без согласия",
    mat_title: "Что работает сегодня, а что ещё экспериментально",
    mat_desc: "Честные границы зрелости важнее маркетинга. Локальный инференс, shard fit, LAN-пул и отказоустойчивость воркеров реализованы и протестированы; глобальная сеть — активное направление развития.",
    st_impl: "Реализовано",
    st_exp: "Экспериментально",
    st_plan: "Видение / В планах",
    mat_r1_c: "Локальный GGUF и PyTorch инференс + shard fit",
    mat_r1_d: "Запуск llama-server GGUF и SafeTensors локально с проверкой VRAM/RAM и Web Dashboard.",
    mat_r2_c: "LAN-пул памяти и распределённые кластеры",
    mat_r2_d: "Обнаружение UDP 8001, коды спаривания, llama.cpp RPC (-ts) и PyTorch-пайплайн для 8 семейств декодеров.",
    mat_r3_c: "Обнаружение сбоев и восстановление генерации",
    mat_r3_d: "4-состояний FSM воркеров, таймауты и восстановление в PyTorch через context replay (24/24 многопроцессных теста; GGUF корректно завершает поток ошибкой после 1-го токена).",
    mat_r4_c: "Ed25519-идентичность, self-hosted Discovery и Blind Relay",
    mat_r4_d: "Реализовано и проверено на loopback с шифрованием ChaCha20-Poly1305; хаос-тесты между физическими WAN-узлами ещё предстоят.",
    mat_r5_c: "Публичная глобальная вычислительная сеть",
    mat_r5_d: "Публичные серверы discovery и relay пока не развёрнуты. Межсетевая работа сегодня требует свой relay или VPN (WireGuard, Tailscale, Radmin).",
    cli_title: "Один CLI для моделей, пулов и кластеров",
    cli_t_server: "Сервер",
    cli_t_models: "Модели и Fit",
    cli_t_lan: "LAN-пул",
    cli_t_cluster: "Кластер",
    cli_t_net: "Своя сеть",
    cli_t_train: "Обучение (SOUP)",
    inst_title: "Что потребуется",
    inst_sub: "Выберите конфигурацию, чтобы увидеть реальные требования.",
    req_gpu: "Одна GPU NVIDIA",
    req_lan: "LAN-пул из ПК",
    req_cpu: "CPU + ОЗУ",
    req_note_gpu: "Локально запускает квантованные GGUF-модели 1.5B–14B целиком в VRAM или в гибриде VRAM+RAM через llama-server.",
    spec_py: "Windows, Linux и macOS. Опциональное обучение SOUP требует Python 3.11–3.13.",
    spec_fmt: "PyTorch-пайплайн поддерживает llama, qwen2, qwen3, mistral, gemma, gemma2, phi3, gpt2.",
    spec_api: "Работает с Cursor, Claude Code, OpenCode, Continue и стандартными SDK через /v1/*.",
    spec_lic: "Никакого скрытого использования GPU и никакой записи промптов без согласия.",
    faq_title: "Перед установкой",
    faq_sub: "Прямые инженерные ответы на частые вопросы.",
    faq_q1: "SHARD использует протокол BitTorrent?",
    faq_a1: "Нет. BitTorrent — лишь архитектурная аналогия кооперативного объединения ресурсов. SHARD не качает торренты и не склеивает куски текста: он распределяет слои нейросети по воркерам через llama.cpp RPC или сокеты PyTorch и передаёт активации последовательно.",
    faq_q2: "Добавление компьютеров всегда ускоряет генерацию?",
    faq_a2: "Нет. Объём памяти — это не пропускная способность. Пул позволяет запустить модель, не влезающую на одну видеокарту, но каждый токен проходит по сети. Если модель уже помещается в одну GPU, локальный запуск будет быстрее.",
    faq_q3: "Будет ли SHARD тайно использовать мою видеокарту?",
    faq_a3: "Никогда. Участие воркером всегда добровольное (--share или consent=true), без согласия возвращается HTTP 403, соблюдаются резервы памяти ОС, а выключение происходит мгновенно командой shard lan off или shard cluster leave.",
    faq_q4: "Что произойдёт при падении воркера посреди ответа?",
    faq_a4: "Координатор фиксирует обрыв TCP, таймаут или пропуск heartbeat, освобождает KV-кэш и слоты, и перестраивает кластер на живых узлах. PyTorch-пайплайн продолжает генерацию через context replay без дублей; GGUF RPC перезапускается до 1-го токена либо возвращает структурированную ошибку.",
    faq_q5: "Есть ли уже работающая публичная глобальная сеть?",
    faq_a5: "Пока нет. Публичные серверы discovery и relay ещё не развёрнуты. Сейчас SHARD надёжно работает локально, в локальной сети, поверх VPN (Tailscale, WireGuard) или с вашими собственными discovery-serve и relay-serve.",
    cta_title: "Начните на одном ПК.<br>Расширяйте пул, когда нужно больше VRAM.",
    footer_desc: "Открытая кооперативная инфраструктура распределённого AI-инференса. Лицензия MIT.",
    foot_docs: "Документация",
    foot_reports: "Инженерные отчёты",
    foot_proj: "Проект",
    footer_legal: "SHARD Engine v1.9.0 · Открытая распределённая инфраструктура AI · Для обычного железа."
  },
  kz: {
    nav_story: "Қалай жұмыс істейді",
    nav_fit: "Калькулятор",
    nav_workflow: "Алғашқы іске қосу",
    nav_arch: "Бэкендтер",
    nav_gov: "Шектер",
    nav_cli: "CLI",
    nav_install: "Орнату",
    nav_cta: "Орнату",
    pill_1: "Схема",
    pill_2: "Есеп",
    pill_3: "Кластер",
    pill_4: "Қозғалтқыш",
    pill_5: "Шектер",
    hero_title: "Қарапайым GPU-ларды біріктіріңіз.<br><span class=\"accent-phrase\">Үлкен модельдерді іске қосыңыз.</span>",
    hero_subtitle: "Желідегі компьютерлердің бос VRAM және RAM жадын біріктіріп, бір видеокартаға сыймайтын ашық модельдерді іске қосыңыз. OpenAI және Anthropic API үйлесімділігі.",
    hero_cta_primary: "Кластерді есептеу",
    hero_cta_secondary: "GitHub-та ашу",
    beat_0: "01 Іздеу",
    beat_1: "02 Шардтар",
    beat_2: "03 Ағын",
    beat_3: "04 Қалпына",
    tl_0_k: "01-кезең",
    tl_0_v: "Сканер және резерв",
    tl_1_k: "02-кезең",
    tl_1_v: "Fit және қабаттар",
    tl_2_k: "03-кезең",
    tl_2_v: "Стрим /v1 API",
    story_title: "Бос аппаратураны іске қосыңыз.<br><span class=\"muted-line\">Өлшеуге болатын пул.</span><br><span class=\"muted-line\">Үйреншікті API.</span>",
    bento_1_t: "ОЖ үшін қатаң резерв",
    bento_1_d: "CUDA VRAM және RAM көлемін анықтап, жүйе үшін 400 МБ VRAM мен 2 ГБ RAM қорғайды.",
    bento_2_t: "Жүктемес бұрын сыйымдылық есебі",
    bento_2_d: "config.json GQA параметрлерін оқып, 4K–32K контекстке KV-кэш пен кванттау деңгейін есептейді.",
    bento_3_t: "LAN арқылы ~8 КБ активация",
    bento_3_d: "Аралық тензорларды түйіннен түйінге жіберіп, редакторға SSE токендерін ағынмен береді.",
    about_title: "Іске қоспас бұрын не сыятынын көріңіз",
    about_desc: "Жад көлемі жылдамдыққа тең емес. Пул бір картаға сыймайтын 32B модельді іске қосуға мүмкіндік береді.",
    fact_2_v: "Желі өтулері кідіріс қосады. Модель бір GPU-ға сыйса, локалды іске қосу жылдамырақ.",
    calc_title: "Кластер жадын есептеу симуляторы",
    calc_sub: "shard fit формуласымен бірдей (ОЖ үшін 400 МБ VRAM және 2 ГБ RAM резерв).",
    calc_model_label: "Ашық модель (Q4_K_M + 4K KV-кэш)",
    calc_gpu1_label: "1-түйін (Координатор) VRAM",
    calc_gpu2_label: "2-түйін (Воркер) VRAM / RAM",
    calc_bar_label: "Түйіндер арасында жадты бөлу",
    calc_req_label: "Қажетті жад",
    calc_total_label: "Қолжетімді пул",
    calc_ratio_label: "-ts пропорциясы",
    calc_ok_single: "1-ТҮЙІНГЕ СЫЯДЫ",
    calc_ok_pool: "ПУЛҒА СЫЯДЫ",
    calc_fail: "ЖАД ЖЕТПЕЙДІ",
    calc_leg_1: "1-түйін қолжетімді:",
    calc_leg_2: "2-түйін қолжетімді:",
    how_title: "Басынан аяғына дейін бір промпт",
    how_desc: "Төрт команда бос терминалдан Cursor мен Claude Code-қа қызмет ететін кластерге жеткізеді.",
    shot_catalog: "Аппарат және каталог",
    shot_fit: "shard fit CLI",
    shot_cluster: "Кластер кезеңдері",
    shot_cap_catalog: "Нақты SHARD Dashboard: RTX 2050 VRAM/RAM телеметриясы және модельдер каталогы.",
    shot_cap_fit: "Нақты shard fit CLI: жүктемес бұрын кванттау мен қабат есебі.",
    shot_cap_cluster: "Нақты SHARD v1.9.0 Dashboard: модель сегменттері және 3 воркер телеметриясы.",
    step1_title: "Ортаны орнатып іске қосыңыз",
    step1_text: "SHARD GPU, RAM және CPU ресурстарын анықтап, 8000 портында Dashboard пен API шлюзін іске қосады.",
    step2_title: "Жүктемес бұрын сыйымдылықты тексеріңіз",
    step2_text: "config.json арқылы параметр санын, KV-кэш көлемін және кванттау нәтижесін алдын ала көріңіз.",
    step3_title: "Екінші компьютерді келісіммен қосыңыз",
    step3_text: "Координаторда LAN-пул құрыңыз. Басқа ПК UDP 8001 немесе IP арқылы нақты квотамен қосылады.",
    step4_title: "Құралдарыңызды localhost:8000 адресіне бағыттаңыз",
    step4_text: "Cursor, Claude Code, OpenCode және OpenAI/Anthropic SDK тікелей жұмыс істейді.",
    btn_copy: "Көшіру",
    btn_copied: "Көшірілді",
    arch_title: "Орындау қозғалтқышының іші",
    arch_desc: "Ішкі жүйені таңдаңыз — оң жақта тірі 40×40 LED схемасы, Python модульдері және CLI нәтижесі.",
    wb_coord: "Координатор және жоспарлаушы",
    wb_gguf: "GGUF RPC қабат бөлу",
    wb_torch: "PyTorch Pipeline Parallel",
    wb_resilience: "Воркер ақауына төзімділік",
    wb_security: "Келісім және оқшаулау",
    wb_scroll_hint: "ауыстыру үшін жылжытыңыз",
    gov_title: "Қозғалтқыш сіз қойған шектерде жұмыс істейді",
    gov_sub: "Жеке режимді немесе LAN воркер квотасын өзіңіз таңдайсыз. Жасырын GPU қолданысы болмайды.",
    spec_left: "Жоспарлаушы орындайды",
    spec_right: "Тек сіздің келісіміңізбен",
    gov_k1: "Автоматты",
    gov_c1_t: "Өздігінен",
    gov_c1_d: "Компьютерден сыртқа шықпайтын диагностика.",
    gov_c1_i1: "NVIDIA VRAM, RAM және CPU тексеру",
    gov_c1_i2: "GQA KV-кэш және кванттау есебі",
    gov_c1_i3: "Үзілген воркерді анықтап KV тазалау",
    gov_k2: "Сіз реттейсіз",
    gov_c2_t: "Сіздің таңдауыңыз",
    gov_c2_d: "Жеке GPU мен LAN воркер арасында ауысыңыз.",
    gov_c2_i1: "VRAM/RAM квотасын ашу",
    gov_c2_i2: "LAN жұптасу кодын қабылдау",
    gov_solo: "Жеке GPU",
    gov_worker: "LAN воркер",
    gov_k3: "Растауды күтеді",
    gov_c3_t: "Әрдайым сұрайды",
    gov_c3_d: "Кез келген режимде нақты команда қажет.",
    gov_c3_i1: "Кластерге қосылу (--share / consent=true)",
    gov_c3_i2: "Үлкен модель салмақтарын жүктеу",
    gov_c3_i3: "SOUP оқытуына дерек экспорттау",
    gov_k4: "Қатаң тыйым",
    gov_c4_t: "Ешқашан",
    gov_c4_d: "Баптаудан тәуелсіз кодта бұғатталған.",
    gov_c4_i1: "Жасырын GPU қолданысы (HTTP 403)",
    gov_c4_i2: "Сыртқы пирлердің /api/* рұқсаты",
    gov_c4_i3: "Келісімсіз промпт жазу",
    mat_title: "Бүгін не жұмыс істейді, не әлі эксперименттік",
    mat_desc: "Шынайы инженерлік мәртебе: локалды инференс, shard fit, LAN-пул және ақаудан қалпына келтіру тексерілген; ғаламдық желі — ұзақ мерзімді бағыт.",
    st_impl: "Іске асқан",
    st_exp: "Эксперименттік",
    st_plan: "Жоспарда",
    mat_r1_c: "Локалды GGUF және PyTorch инференсі + shard fit",
    mat_r1_d: "llama-server GGUF және SafeTensors локалды іске қосылады, Dashboard пен fit-кеңесші бар.",
    mat_r2_c: "LAN жад пулы және таратылған кластерлер",
    mat_r2_d: "UDP 8001 табу, жұптасу кодтары, llama.cpp RPC (-ts) және 8 декодер үшін PyTorch пайплайны.",
    mat_r3_c: "Ақауды анықтау және қалпына келтіру",
    mat_r3_d: "4 күйлі FSM, таймауттар және PyTorch context replay (24/24 локалды тест).",
    mat_r4_c: "Ed25519 сәйкестік, дербес Discovery және Blind Relay",
    mat_r4_d: "ChaCha20-Poly1305 туннельдері loopback-та тексерілген; бөлек WAN машиналарында сынақ қажет.",
    mat_r5_c: "Қоғамдық ғаламдық есептеу желісі",
    mat_r5_d: "Қоғамдық discovery/relay серверлері әлі орналастырылмаған. Бүгін VPN немесе жеке relay қолданылады.",
    cli_title: "Модельдер, пулдар және кластерлерге арналған бір CLI",
    cli_t_server: "Сервер",
    cli_t_models: "Модельдер және Fit",
    cli_t_lan: "LAN пул",
    cli_t_cluster: "Кластер",
    cli_t_net: "Дербес желі",
    cli_t_train: "Оқыту (SOUP)",
    inst_title: "Не қажет",
    inst_sub: "Конфигурацияңызды таңдаңыз.",
    req_gpu: "Бір NVIDIA GPU",
    req_lan: "Көп ПК LAN пул",
    req_cpu: "CPU + ЖЖҚ",
    req_note_gpu: "1.5B–14B квантталған GGUF модельдерін GPU немесе гибрид VRAM+RAM режимінде іске қосады.",
    spec_py: "Windows, Linux және macOS. Қосымша SOUP оқытуы Python 3.11–3.13 қолданады.",
    spec_fmt: "PyTorch пайплайны llama, qwen2, qwen3, mistral, gemma, gemma2, phi3, gpt2 қолдайды.",
    spec_api: "Cursor, Claude Code, OpenCode және стандартты SDK-лармен /v1/* арқылы жұмыс істейді.",
    spec_lic: "Рұқсатсыз GPU пайдалану және келісімсіз дерек жинау жоқ.",
    faq_title: "Орнату алдында",
    faq_sub: "Ең жиі қойылатын техникалық сұрақтарға нақты жауаптар.",
    faq_q1: "SHARD BitTorrent протоколын қолдана ма?",
    faq_a1: "Жоқ. BitTorrent — тек ресурстарды біріктіру аналогиясы. SHARD нейрожүйе қабаттарын воркерлерге бөліп, аралық активацияларды тізбектей жібереді.",
    faq_q2: "Компьютер қосқан сайын генерация жылдамдай ма?",
    faq_a2: "Жоқ. Жад көлемі жылдамдыққа тең емес. Пул үлкен модельді сыйдыруға мүмкіндік береді, бірақ модель бір GPU-ға сыйса, локалды іске қосу жылдамырақ.",
    faq_q3: "SHARD менің GPU-ымды жасырын пайдалана ма?",
    faq_a3: "Ешқашан. Воркер болып қосылу тек келісіммен (--share немесе consent=true) орындалады және shard lan off арқылы бірден тоқтатылады.",
    faq_q4: "Генерация кезінде воркер өшіп қалса не болады?",
    faq_a4: "Координатор үзілісті анықтап, KV-кэшті тазалайды және кластерді сау түйіндерде қайта құрады. PyTorch context replay арқылы жалғастыра алады; GGUF нақты қате қайтарады.",
    faq_q5: "Қосылуға дайын қоғамдық ғаламдық желі бар ма?",
    faq_a5: "Әзірге жоқ. Қазір SHARD локалды түрде, LAN ішінде, VPN (Tailscale/WireGuard) немесе жеке discovery/relay сервері арқылы жұмыс істейді.",
    cta_title: "Бір компьютерден бастаңыз.<br>VRAM керек болғанда пулды кеңейтіңіз.",
    footer_desc: "Ашық кооперативті таратылған AI инференс инфрақұрылымы. MIT лицензиясы.",
    foot_docs: "Құжаттама",
    foot_reports: "Инженерлік есептер",
    foot_proj: "Жоба",
    footer_legal: "SHARD Engine v1.9.0 · Ашық таратылған AI инференс инфрақұрылымы."
  }
};
const HERO_BEATS = [
  {
    toast: { en: "3 machines pooled · 22.8 GB usable VRAM", ru: "3 ПК в пуле · 22.8 ГБ доступной VRAM", kz: "3 ПК бірікті · 22.8 ГБ бос VRAM" },
    telemetry: "RTX 3060 (11.4G) + RTX 4060 (7.7G) + RTX 2050 (3.7G)"
  },
  {
    toast: { en: "48 transformer layers split proportionally", ru: "48 слоёв распределены пропорционально", kz: "48 қабат пропорционалды бөлінді" },
    telemetry: "Stage 0: L0–20 · Stage 1: L20–36 · Stage 2: L36–48"
  },
  {
    toast: { en: "Streaming ~8 KB hidden states -> 18.4 t/s", ru: "Поток активаций ~8 КБ -> 18.4 т/с", kz: "~8 КБ активация ағыны -> 18.4 т/с" },
    telemetry: "POST /v1/chat/completions · SSE stream active · LAN 0.8 ms"
  },
  {
    toast: { en: "Worker 2 fault isolated · context replayed cleanly", ru: "Сбой Воркера 2 изолирован · context replay", kz: "2-воркер ақауы оқшауланды · қайта жалғасты" },
    telemetry: "FSM: FAILED -> RECOVERING -> READY · 0 duplicate tokens"
  }
];
const GOV_COPY = {
  solo: {
    en: ["Disabled in Solo mode", "Requires --share flag"],
    ru: ["Отключено в локальном режиме", "Требует флаг --share"],
    kz: ["Жеке режимде өшірулі", "--share жалаушасы қажет"]
  },
  worker: {
    en: ["Active inside your VRAM cap", "Accepts paired coordinator only"],
    ru: ["Активно в рамках вашей квоты VRAM", "Только для доверенного координатора"],
    kz: ["Сіздің VRAM квотаңызда белсенді", "Тек жұптасқан координатор үшін"]
  }
};
const ARCH_DATA = {
  coordinator: {
    ledCode: "SHARD-PLAN · 40×40",
    module: "shard_engine/distributed/planner.py · cluster.py · scheduler.py",
    title: { en: "Hardware-aware Coordinator & Planner", ru: "Аппаратно-зависимый Координатор и Планировщик", kz: "Аппараттық Координатор және Жоспарлаушы" },
    desc: {
      en: "Probes usable VRAM and RAM per node after OS reserves, scales KV cache by context and parallel slots, assigns proportional layer ranges with CUDA preference, and warns when single-GPU local execution is faster than crossing the network.",
      ru: "Оценивает свободную VRAM и RAM на каждом узле с учётом резервов ОС, масштабирует KV-кэш под контекст и слоты, распределяет слои пропорционально с приоритетом CUDA и предупреждает, если локальная GPU быстрее сети.",
      kz: "Әр түйіндегі бос VRAM және RAM қорын ОЖ резервін шегеріп есептейді, қабаттарды CUDA басымдығымен бөледі және желі кідірісін ескертеді."
    },
    tags: ["plan_cluster_partition()", "RequestScheduler FIFO", "400 MB / 2 GB reserve", "RTT estimate"],
    codeTitle: "cluster plan · cli",
    copy: "shard cluster create Qwen/Qwen2.5-Coder-14B-Instruct --strategy auto\nshard cluster plan",
    code: `$ shard cluster create \\\n  Qwen/Qwen2.5-Coder-14B-Instruct --strategy auto\n$ shard cluster plan\n<span class="tok-ok">Stage 0  worker-1 (RTX 3060)  L00-20  11.4 GB</span>\n<span class="tok-ok">Stage 1  worker-2 (RTX 4060)  L20-36   7.7 GB</span>\n<span class="tok-ok">Stage 2  worker-3 (RTX 2050)  L36-48   3.7 GB</span>`
  },
  gguf: {
    ledCode: "GGUF-RPC · 40×40",
    module: "shard_engine/gguf_backend.py · lan.py",
    title: { en: "GGUF Distributed RPC Layer Split", ru: "GGUF RPC разбиение по слоям", kz: "GGUF RPC қабат бөлу" },
    desc: {
      en: "Reuses llama.cpp native RPC (ggml-rpc-server + llama-server --rpc + -ts) to split quantized weights and KV cache proportionally across machines with continuous batching. Ideal for mixed VRAM/RAM consumer GPUs.",
      ru: "Использует нативный RPC из llama.cpp (ggml-rpc-server + llama-server --rpc + -ts) для пропорционального распределения весов и KV-кэша с continuous batching. Оптимально для разнородных домашних ПК.",
      kz: "llama.cpp RPC (ggml-rpc-server + --rpc + -ts) арқылы квантталған салмақтар мен KV-кэшті түйіндерге пропорционалды бөледі."
    },
    tags: ["ggml-rpc-server :50052", "--rpc + -ts weights", "--cont-batching", "Q2_K to Q8_0"],
    codeTitle: "llama-server rpc · generated args",
    copy: "llama-server -m qwen2.5-coder-32b-q4_k_m.gguf --rpc 192.168.1.14:50052,192.168.1.22:50052 -ts 7782,11673,3784 -ngl 999 --cont-batching",
    code: `$ llama-server -m qwen2.5-32b-q4_k_m.gguf \\\n  --rpc 192.168.1.14:50052,192.168.1.22:50052 \\\n  -ts 7782,11673,3784 -ngl 999 --cont-batching\n<span class="tok-m"># Master holds GGUF; workers run ggml-rpc-server</span>`
  },
  pytorch: {
    ledCode: "SHPT-PIPE · 40×40",
    module: "shard_engine/distributed/pipeline_coordinator.py · pipeline_worker.py · pipeline_proto.py",
    title: { en: "PyTorch Pipeline Parallelism (SHPT)", ru: "PyTorch Pipeline Parallelism (SHPT)", kz: "PyTorch Pipeline Parallelism (SHPT)" },
    desc: {
      en: "Loads only each stage's assigned SafeTensors layer shards directly on workers, keeps distributed DynamicCache KV state per stage, and streams binary tensor activations over authenticated SHPT TCP frames across 8 decoder architectures.",
      ru: "Загружает на каждом воркере только его шарды SafeTensors, держит локальный DynamicCache KV-кэш и пересылает бинарные тензоры активаций по аутентифицированному протоколу SHPT для 8 семейств декодеров.",
      kz: "Әр воркер тек өз SafeTensors қабаттарын жүктейді және SHPT TCP протоколымен бинарлы активация тензорларын жібереді."
    },
    tags: ["llama / qwen2 / qwen3 / mistral", "gemma / gemma2 / phi3 / gpt2", "SHPT binary framing", "Per-stage KV cache"],
    codeTitle: "torch pipeline · cli",
    copy: "shard cluster create Qwen/Qwen2.5-Coder-7B-Instruct --strategy torch_pipeline",
    code: `$ shard cluster create \\\n  Qwen/Qwen2.5-Coder-7B-Instruct \\\n  --strategy torch_pipeline\n<span class="tok-s">[SHPT]</span> W1 loaded shard-01 (L0..10)\n<span class="tok-s">[SHPT]</span> W2 loaded shard-02 (L10..20)\n<span class="tok-ok">[READY]</span> Streaming -> /v1/chat/completions`
  },
  resilience: {
    ledCode: "FSM-GUARD · 40×40",
    module: "shard_engine/distributed/resilience.py · cluster.py",
    title: { en: "4-State Worker FSM & Context-Replay Recovery", ru: "FSM воркеров и восстановление через Context Replay", kz: "Воркер FSM және Context Replay қалпына келтіру" },
    desc: {
      en: "Tracks HEALTHY, SUSPECT, FAILED, and RECOVERING states with monotonic heartbeats and bounded per-phase timeouts. When a PyTorch worker dies mid-stream, surviving stages release KV caches, reform, and replay [prompt + generated_ids] without duplicating tokens.",
      ru: "Отслеживает состояния HEALTHY, SUSPECT, FAILED и RECOVERING с монотонными heartbeat и фазовыми таймаутами. При падении воркера в PyTorch кластер перестраивается и продолжает генерацию через context replay [prompt + generated_ids] без дубликатов.",
      kz: "HEALTHY, SUSPECT, FAILED, RECOVERING күйлерін бақылайды. Воркер үзілгенде PyTorch пайплайны [prompt + generated_ids] replay арқылы қайта жалғастырады."
    },
    tags: ["24/24 resilience tests", "poll_slice_s blackhole guard", "No fake stop/end_turn", "HTTP 503 + SSE errors"],
    codeTitle: "resilience policy · api",
    copy: "shard cluster resilience",
    code: `$ shard cluster resilience\n<span class="tok-ok">state=READY  healthy=3  stale_hb_rejected=0</span>\n<span class="tok-m">connect=5s  prefill=180s  decode=30s</span>\n<span class="tok-m">poll_slice=0.25s  max_recovery=2</span>\n<span class="tok-s">pytorch=context_replay | gguf=clean_error</span>`
  },
  security: {
    ledCode: "ED25519 · 40×40",
    module: "shard_engine/server.py · net_identity.py · net_tunnel.py · storage.py",
    title: { en: "Explicit Consent, Owner Isolation & AEAD Tunnels", ru: "Явное согласие, изоляция владельца и AEAD-туннели", kz: "Нақты келісім, иесін оқшаулау және AEAD туннельдері" },
    desc: {
      en: "Worker joins without consent=true fail with HTTP 403. Admin /api/* endpoints are restricted to local owner tokens; remote tunnel peers are strictly allowlisted and cannot escalate privileges. Node identities use Ed25519 + X25519 + ChaCha20-Poly1305.",
      ru: "Подключение воркера без consent=true отклоняется с HTTP 403. Админ-маршруты /api/* защищены токеном владельца; удалённые туннельные пиры строго ограничены белым списком путей. Идентичность: Ed25519 + X25519 + ChaCha20-Poly1305.",
      kz: "consent=true жоқ воркер HTTP 403 қатесімен бұғатталады. Әкімшілік /api/* тек иесіне қолжетімді; туннельдер ChaCha20-Poly1305 арқылы шифрланады."
    },
    tags: ["403 CONSENT_REQUIRED", "Owner-only /api/*", "Ed25519 + TOFU", "Zero silent prompt logging"],
    codeTitle: "security check · curl",
    copy: "curl -s http://localhost:8000/api/cluster/resilience",
    code: `$ curl -X POST :8000/api/cluster/worker/join \\\n  -d '{"consent":false}'\n<span class="tok-w">HTTP/1.1 403  {"code":"CONSENT_REQUIRED"}</span>\n<span class="tok-m"># Tunnel peers blocked from /api/keys &amp; /api/cluster/*</span>`
  }
};
const ARCH_ORDER = ["coordinator", "gguf", "pytorch", "resilience", "security"];
const MODELS_FIT = {
  qwen15: { name: "Qwen 2.5 Coder 1.5B", req: 1.25 },
  qwen7: { name: "Qwen 2.5 Coder 7B", req: 4.70 },
  qwen14: { name: "Qwen 2.5 Coder 14B", req: 9.20 },
  qwen32: { name: "Qwen 2.5 Coder 32B", req: 19.80 },
  deepseek32: { name: "DeepSeek R1 Distill 32B", req: 19.80 },
  llama70: { name: "Llama 3.3 70B Instruct", req: 42.50 }
};
const REQ_PRESETS = {
  gpu: {
    big: "4–12 GB",
    unit: "VRAM + 16 GB RAM",
    note: {
      en: "Runs 1.5B to 14B quantized GGUF models locally in pure GPU or hybrid VRAM+RAM mode via llama-server.",
      ru: "Локально запускает квантованные GGUF-модели 1.5B–14B целиком в VRAM или в гибриде VRAM+RAM через llama-server.",
      kz: "1.5B–14B квантталған GGUF модельдерін GPU немесе гибрид VRAM+RAM режимінде іске қосады."
    }
  },
  lan: {
    big: "2+ PCs",
    unit: "1 Gbps Ethernet or Wi-Fi 5/6",
    note: {
      en: "Combine two 8–12 GB GPUs or a GPU + high-RAM desktop on the same subnet to fit 14B–32B models via llama.cpp RPC or PyTorch pipeline.",
      ru: "Объедините две GPU по 8–12 ГБ или GPU + ПК с большим объёмом RAM в одной подсети для запуска моделей 14B–32B через llama.cpp RPC или PyTorch-пайплайн.",
      kz: "Бір желідегі екі 8–12 ГБ GPU немесе GPU + көп RAM бар ПК-лерді біріктіріп, 14B–32B модельдерін іске қосыңыз."
    }
  },
  cpu: {
    big: "16–64 GB",
    unit: "System RAM (no GPU required)",
    note: {
      en: "Any spare laptop or desktop with free RAM can join a cluster as a compute worker or run compact models locally on CPU.",
      ru: "Любой свободный ноутбук или ПК с достаточным объёмом RAM может стать воркером в кластере или локально запускать компактные модели на CPU.",
      kz: "Бос RAM қоры бар кез келген ноутбук не ПК кластерге воркер болып қосыла алады."
    }
  }
};
const SHOTS = {
  catalog: { src: "assets/screenshots/dashboard-catalog.png", capKey: "shot_cap_catalog" },
  fit: { src: "assets/screenshots/cli-fit-advisor.png", capKey: "shot_cap_fit" },
  cluster: { src: "assets/screenshots/dashboard-cluster.png", capKey: "shot_cap_cluster" }
};
const LANG_ORDER = ["en", "ru", "kz"];
let currentLang = "en";
let currentTheme = "daylight";
let currentArch = "coordinator";
let currentReq = "gpu";
let currentShot = "catalog";
let currentGovMode = "solo";
let activeHeroBeat = 0;
let activeStoryStep = 0;
let activeChapterIdx = 0;
let flipTimer = 0;
let archScrollLock = false;
let shotManualLock = false;
function t(key) {
  return (I18N[currentLang] && I18N[currentLang][key]) || I18N.en[key] || key;
}
function setLanguage(lang) {
  if (!I18N[lang]) return;
  currentLang = lang;
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const k = el.getAttribute("data-i18n");
    const val = t(k);
    if (val.includes("<")) el.innerHTML = val;
    else el.textContent = val;
  });
  const btnText = document.getElementById("lang-btn-text");
  if (btnText) btnText.textContent = lang.toUpperCase();
  inspectArchNode(currentArch, false);
  updateCalculator();
  updateReqView(currentReq);
  updateShot(currentShot, false);
  updateGovernanceMode(currentGovMode);
  syncHeroBeatUI(activeHeroBeat, false);
}
function setTheme(theme) {
  currentTheme = theme === "dark" ? "dark" : "daylight";
  document.documentElement.setAttribute("data-theme", currentTheme);
  const txt = document.getElementById("theme-btn-text");
  if (txt) txt.textContent = currentTheme === "dark" ? "Daylight" : "Dark";
}
function syncHeroBeatUI(beatIdx, animateToast) {
  activeHeroBeat = beatIdx;
  const info = HERO_BEATS[beatIdx] || HERO_BEATS[0];
  document.querySelectorAll(".beat-btn").forEach((b) => b.classList.toggle("active", Number(b.getAttribute("data-beat")) === beatIdx));
  const tel = document.getElementById("hero-telemetry-readout");
  if (tel) tel.textContent = info.telemetry;
  const toast = document.getElementById("hero-motion-toast");
  const tTxt = document.getElementById("hero-toast-text");
  const nextText = info.toast[currentLang] || info.toast.en;
  if (toast && tTxt) {
    if (animateToast) {
      toast.classList.add("is-swapping");
      setTimeout(() => {
        tTxt.textContent = nextText;
        toast.classList.remove("is-swapping");
      }, 140);
    } else {
      tTxt.textContent = nextText;
    }
  }
}
function initHeroLedReel() {
  const canvas = document.getElementById("hero-3d-canvas");
  const bezel = document.getElementById("hero-hw-bezel");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const cols = 56, rows = 26;
  const beatDuration = 4600;
  let w = 0, h = 0, dpr = 1, visible = true;
  let beatStart = performance.now();
  let grid = Array.from({ length: rows }, () => Array.from({ length: cols }, () => ({ r: 20, g: 28, b: 42, a: 0 })));
  function resize() {
    const r = canvas.getBoundingClientRect();
    w = Math.max(300, Math.round(r.width));
    h = Math.max(200, Math.round(r.height));
    dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  resize();
  window.addEventListener("resize", resize);
  if ("IntersectionObserver" in window) {
    new IntersectionObserver((en) => { visible = en[0] ? en[0].isIntersecting : true; }, { rootMargin: "120px" }).observe(canvas);
  }
  if (bezel && !reduced) {
    bezel.addEventListener("pointermove", (ev) => {
      if (ev.pointerType !== "mouse") return;
      const r = bezel.getBoundingClientRect();
      const nx = (ev.clientX - r.left) / Math.max(1, r.width) - 0.5;
      const ny = (ev.clientY - r.top) / Math.max(1, r.height) - 0.5;
      bezel.style.transform = `rotateY(${(nx * 5).toFixed(2)}deg) rotateX(${(-ny * 4).toFixed(2)}deg)`;
    });
    bezel.addEventListener("pointerleave", () => { bezel.style.transform = ""; });
  }
  document.querySelectorAll(".beat-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.getAttribute("data-beat")) || 0;
      beatStart = performance.now();
      syncHeroBeatUI(idx, true);
    });
  });
  function clearGrid() {
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        grid[y][x].a = 0;
      }
    }
  }
  function setLed(x, y, r, g, b, a) {
    const ix = Math.round(x), iy = Math.round(y);
    if (ix < 0 || ix >= cols || iy < 0 || iy >= rows) return;
    const cell = grid[iy][ix];
    if (a > cell.a) { cell.r = r; cell.g = g; cell.b = b; cell.a = Math.min(1, a); }
  }
  function drawRect(x0, y0, rw, rh, r, g, b, a, fill) {
    for (let y = y0; y < y0 + rh; y++) {
      for (let x = x0; x < x0 + rw; x++) {
        if (fill || x === x0 || x === x0 + rw - 1 || y === y0 || y === y0 + rh - 1) {
          setLed(x, y, r, g, b, a);
        }
      }
    }
  }
  function drawNodesBase(alphaScale) {
    const xs = [6, 22, 38];
    for (let i = 0; i < 3; i++) {
      drawRect(xs[i], 6, 10, 14, 79, 139, 255, 0.32 * alphaScale, false);
    }
  }
  function renderBeat(beat, u, tSec) {
    clearGrid();
    const xs = [6, 22, 38];
    const colsRgb = [[79, 139, 255], [56, 189, 248], [16, 185, 129]];
    if (beat === 0) {
      const scanX = u * (cols + 6) - 3;
      for (let y = 2; y < rows - 2; y++) {
        for (let dx = -2; dx <= 2; dx++) {
          const a = Math.max(0, 0.7 - Math.abs(dx) * 0.25);
          setLed(scanX + dx, y, 56, 189, 248, a * 0.55);
        }
      }
      const heights = [11, 8, 5];
      for (let i = 0; i < 3; i++) {
        const revealed = Math.max(0, Math.min(1, (scanX - xs[i]) / 8));
        const [r, g, b] = colsRgb[i];
        drawRect(xs[i], 5, 10, 15, r, g, b, 0.35 + revealed * 0.55, false);
        const fillH = Math.round(heights[i] * Math.min(1, u * 1.45));
        for (let fy = 0; fy < fillH; fy++) {
          drawRect(xs[i] + 2, 18 - fy, 6, 1, r, g, b, 0.92, true);
        }
        setLed(xs[i] + 2, 18 - heights[i] - 1, 248, 113, 113, 0.85);
        setLed(xs[i] + 3, 18 - heights[i] - 1, 248, 113, 113, 0.85);
      }
    } else if (beat === 1) {
      const ease = 1 - Math.pow(1 - Math.min(1, u * 1.35), 3);
      drawNodesBase(0.95);
      const targets = [7, 23, 39];
      const widths = [8, 8, 8];
      const heights = [10, 8, 6];
      for (let i = 0; i < 3; i++) {
        const startX = 20 + i * 5;
        const cx = startX + (targets[i] - startX) * ease;
        const [r, g, b] = colsRgb[i];
        const yStart = 18 - heights[i];
        drawRect(Math.round(cx), yStart, widths[i], heights[i], r, g, b, 0.9, true);
        if (ease > 0.72) {
          for (let lx = Math.round(cx) + 1; lx < Math.round(cx) + widths[i] - 1; lx += 2) {
            setLed(lx, yStart + 2, 244, 246, 250, 0.95);
          }
        }
      }
      for (let x = 16; x < 38; x += 2) {
        setLed(x, 12, 79, 139, 255, 0.4 + 0.4 * Math.sin(tSec * 6 - x * 0.4));
      }
    } else if (beat === 2) {
      for (let i = 0; i < 3; i++) {
        const [r, g, b] = colsRgb[i];
        drawRect(xs[i], 6, 10, 14, r, g, b, 0.65, false);
        for (let row = 8; row <= 17; row += 2) {
          const wave = 0.35 + 0.65 * Math.max(0, Math.sin(tSec * 7 - i * 1.4 - row * 0.45));
          drawRect(xs[i] + 2, row, 6, 1, r, g, b, wave, true);
        }
      }
      for (let k = 0; k < 3; k++) {
        const p = (u * 2.2 + k * 0.34) % 1;
        const px = 4 + p * 49;
        const py = 12 + Math.round(Math.sin(p * Math.PI * 6) * 1.3);
        setLed(px, py, 244, 246, 250, 1);
        setLed(px - 1, py, 56, 189, 248, 0.92);
        setLed(px - 2, py, 79, 139, 255, 0.65);
        setLed(px - 3, py, 79, 139, 255, 0.35);
      }
      const outPulse = 0.55 + 0.45 * Math.sin(tSec * 10);
      drawRect(50, 9, 4, 8, 16, 185, 129, outPulse, true);
    } else {
      const faulted = u > 0.16 && u < 0.72;
      const recovered = u >= 0.72;
      drawRect(xs[0], 6, 10, 14, 79, 139, 255, 0.85, false);
      drawRect(xs[0] + 2, 9, 6, 8, 79, 139, 255, 0.82, true);
      drawRect(xs[2], 6, 10, 14, 16, 185, 129, 0.85, false);
      drawRect(xs[2] + 2, 9, 6, 8, 16, 185, 129, 0.82, true);
      if (faulted) {
        const blink = 0.55 + 0.45 * Math.sin(tSec * 16);
        drawRect(xs[1], 6, 10, 14, 248, 113, 113, blink, false);
        for (let d = 0; d < 6; d++) {
          setLed(xs[1] + 2 + d, 10 + d, 248, 113, 113, 1);
          setLed(xs[1] + 7 - d, 10 + d, 248, 113, 113, 1);
        }
        for (let i = 0; i <= 22; i++) {
          const f = i / 22;
          const ax = 15 + f * 24;
          const ay = 10 - Math.sin(f * Math.PI) * 7;
          const glow = 0.45 + 0.55 * Math.max(0, Math.sin(tSec * 9 - i * 0.4));
          setLed(ax, ay, 56, 189, 248, glow);
        }
      } else {
        const col = recovered ? [16, 185, 129] : [56, 189, 248];
        drawRect(xs[1], 6, 10, 14, col[0], col[1], col[2], 0.85, false);
        drawRect(xs[1] + 2, 10, 6, 6, col[0], col[1], col[2], 0.85, true);
        const px = 15 + ((tSec * 22) % 24);
        setLed(px, 12, 244, 246, 250, 1);
        setLed(px - 1, 12, 16, 185, 129, 0.8);
      }
    }
  }
  const pSegs = Array.from(document.querySelectorAll(".hw-pseg"));
  function draw(nowMs) {
    if (visible) {
      const elapsed = nowMs - beatStart;
      if (!reduced && elapsed >= beatDuration) {
        beatStart = nowMs;
        syncHeroBeatUI((activeHeroBeat + 1) % 4, true);
      }
      const u = reduced ? 0.65 : Math.min(1, (nowMs - beatStart) / beatDuration);
      pSegs.forEach((seg, idx) => {
        const bar = seg.querySelector("i");
        seg.classList.toggle("done", idx < activeHeroBeat);
        if (bar) bar.style.width = idx === activeHeroBeat ? `${(u * 100).toFixed(1)}%` : (idx < activeHeroBeat ? "100%" : "0%");
      });
      renderBeat(activeHeroBeat, u, nowMs * 0.001);
      ctx.fillStyle = "#06080D";
      ctx.fillRect(0, 0, w, h);
      const padX = Math.max(12, w * 0.03);
      const padY = Math.max(12, h * 0.06);
      const cellW = (w - padX * 2) / cols;
      const cellH = (h - padY * 2) / rows;
      const rad = Math.min(cellW, cellH) * 0.34;
      for (let y = 0; y < rows; y++) {
        for (let x = 0; x < cols; x++) {
          const cx = padX + (x + 0.5) * cellW;
          const cy = padY + (y + 0.5) * cellH;
          const cell = grid[y][x];
          ctx.fillStyle = "#121825";
          ctx.beginPath();
          ctx.arc(cx, cy, rad * 0.82, 0, Math.PI * 2);
          ctx.fill();
          if (cell.a > 0.04) {
            ctx.fillStyle = `rgba(${cell.r},${cell.g},${cell.b},${(cell.a * 0.28).toFixed(2)})`;
            ctx.beginPath();
            ctx.arc(cx, cy, rad * 2.1, 0, Math.PI * 2);
            ctx.fill();
            ctx.fillStyle = `rgba(${cell.r},${cell.g},${cell.b},${cell.a.toFixed(2)})`;
            ctx.beginPath();
            ctx.arc(cx, cy, rad, 0, Math.PI * 2);
            ctx.fill();
          }
        }
      }
    }
    if (!reduced) requestAnimationFrame(draw);
  }
  syncHeroBeatUI(0, false);
  requestAnimationFrame(draw);
}
function initBentoMotionScenes() {
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const cProbe = document.getElementById("bento-canvas-probe");
  const cFit = document.getElementById("bento-canvas-fit");
  const cStream = document.getElementById("bento-canvas-stream");
  const railFill = document.getElementById("story-rail-fill");
  const nodes = Array.from(document.querySelectorAll(".tl-node"));
  const cards = Array.from(document.querySelectorAll(".bento-card"));
  function setStoryStep(idx) {
    activeStoryStep = idx;
    nodes.forEach((n, i) => n.classList.toggle("active", i <= idx));
    cards.forEach((c, i) => c.classList.toggle("is-focus", i === idx));
    if (railFill) railFill.style.width = `${idx === 0 ? 12 : idx === 1 ? 50 : 88}%`;
  }
  nodes.forEach((n) => n.addEventListener("click", () => setStoryStep(Number(n.getAttribute("data-story-step")) || 0)));
  setStoryStep(1);
  if (!cProbe || !cFit || !cStream) return;
  let visible = true;
  if ("IntersectionObserver" in window) {
    new IntersectionObserver((en) => { visible = en[0] ? en[0].isIntersecting : true; }, { rootMargin: "150px" }).observe(cFit);
  }
  function prep(canvas) {
    const r = canvas.getBoundingClientRect();
    const w = Math.max(240, Math.round(r.width));
    const h = Math.max(180, Math.round(r.height));
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  }
  function drawAll(nowMs) {
    if (visible) {
      const t = reduced ? 1.5 : nowMs * 0.001;
      const isDark = currentTheme === "dark";
      const bg = isDark ? "#111827" : "#F1F4F9";
      const ink = isDark ? "#F4F6FA" : "#0C1018";
      const muted = isDark ? "#94A3B8" : "#4E5A6E";
      const track = isDark ? "#1E293B" : "#DCE3EE";
      const p1 = prep(cProbe);
      p1.ctx.fillStyle = bg;
      p1.ctx.fillRect(0, 0, p1.w, p1.h);
      const rCx = p1.w * 0.34, rCy = p1.h * 0.5, rad = Math.min(p1.w, p1.h) * 0.29;
      const pct = 0.72 + 0.14 * Math.sin(t * 1.2);
      p1.ctx.lineWidth = 10;
      p1.ctx.strokeStyle = track;
      p1.ctx.beginPath(); p1.ctx.arc(rCx, rCy, rad, 0, Math.PI * 2); p1.ctx.stroke();
      p1.ctx.strokeStyle = "#1D4ED8";
      p1.ctx.lineCap = "round";
      p1.ctx.beginPath(); p1.ctx.arc(rCx, rCy, rad, -Math.PI * 0.5, -Math.PI * 0.5 + Math.PI * 2 * pct); p1.ctx.stroke();
      p1.ctx.strokeStyle = "#EF4444";
      p1.ctx.beginPath(); p1.ctx.arc(rCx, rCy, rad, Math.PI * 1.28, Math.PI * 1.46); p1.ctx.stroke();
      p1.ctx.fillStyle = ink;
      p1.ctx.font = '700 18px "JetBrains Mono", monospace';
      p1.ctx.textAlign = "center";
      p1.ctx.fillText(`${(11.6 * (0.85 + 0.15 * pct)).toFixed(1)}G`, rCx, rCy + 4);
      p1.ctx.font = '600 10px "JetBrains Mono", monospace';
      p1.ctx.fillStyle = muted;
      p1.ctx.fillText("USABLE", rCx, rCy + 19);
      const lx = p1.w * 0.62;
      p1.ctx.textAlign = "left";
      p1.ctx.fillStyle = ink;
      p1.ctx.font = '700 11px "JetBrains Mono", monospace';
      p1.ctx.fillText("OS GUARD", lx, p1.h * 0.34);
      p1.ctx.fillStyle = "#065F46";
      p1.ctx.fillText("400 MB VRAM", lx, p1.h * 0.49);
      p1.ctx.fillText("2.0 GB RAM", lx, p1.h * 0.62);
      p1.ctx.fillStyle = muted;
      p1.ctx.fillText("LOCKED SAFE", lx, p1.h * 0.76);
      const p2 = prep(cFit);
      p2.ctx.fillStyle = bg;
      p2.ctx.fillRect(0, 0, p2.w, p2.h);
      const bars = 12;
      const bPad = 24;
      const bw = (p2.w - bPad * 2 - (bars - 1) * 7) / bars;
      const hiIdx = Math.floor((t * 0.55) % 4) + 7;
      for (let i = 0; i < bars; i++) {
        const baseH = 26 + Math.pow(i / (bars - 1), 1.55) * (p2.h - 92);
        const animH = baseH * (0.86 + 0.14 * Math.sin(t * 2.1 - i * 0.35));
        const bx = bPad + i * (bw + 7);
        const by = p2.h - 32 - animH;
        const isHi = i >= 8;
        p2.ctx.fillStyle = i === hiIdx ? "#1D4ED8" : (isHi ? "#38BDF8" : (isDark ? "#23304A" : "#C7D7EE"));
        p2.ctx.beginPath();
        p2.ctx.roundRect(bx, by, bw, animH, [6, 6, 2, 2]);
        p2.ctx.fill();
      }
      p2.ctx.fillStyle = "#0D1321";
      p2.ctx.beginPath();
      p2.ctx.roundRect(p2.w - 190, 16, 168, 56, 10);
      p2.ctx.fill();
      p2.ctx.fillStyle = "#93C5FD";
      p2.ctx.font = '600 10px "JetBrains Mono", monospace';
      p2.ctx.fillText("shard fit · Q4_K_M", p2.w - 176, 35);
      p2.ctx.fillStyle = "#FFFFFF";
      p2.ctx.font = '700 14px "JetBrains Mono", monospace';
      p2.ctx.fillText("28.7 t/s · GPU", p2.w - 176, 55);
      p2.ctx.fillStyle = muted;
      p2.ctx.font = '500 10.5px "JetBrains Mono", monospace';
      p2.ctx.fillText("Q2_K", bPad, p2.h - 12);
      p2.ctx.fillText("Q4_K_M", p2.w * 0.44, p2.h - 12);
      p2.ctx.fillText("Q8_0", p2.w - 52, p2.h - 12);
      const p3 = prep(cStream);
      p3.ctx.fillStyle = "#0A0E17";
      p3.ctx.fillRect(0, 0, p3.w, p3.h);
      p3.ctx.strokeStyle = "rgba(79,139,255,0.22)";
      p3.ctx.lineWidth = 1;
      for (let gy = 36; gy < p3.h; gy += 36) {
        p3.ctx.beginPath(); p3.ctx.moveTo(0, gy); p3.ctx.lineTo(p3.w, gy); p3.ctx.stroke();
      }
      p3.ctx.strokeStyle = "#38BDF8";
      p3.ctx.lineWidth = 2;
      p3.ctx.beginPath();
      for (let x = 14; x < p3.w - 14; x += 3) {
        const nx = (x - 14) / (p3.w - 28);
        const env = Math.sin(nx * Math.PI);
        const y = p3.h * 0.44 + Math.sin(nx * 22 - t * 6.5) * 18 * env + Math.cos(nx * 9 + t * 3) * 6 * env;
        if (x === 14) p3.ctx.moveTo(x, y); else p3.ctx.lineTo(x, y);
      }
      p3.ctx.stroke();
      const blipX = 14 + ((t * 0.45) % 1) * (p3.w - 28);
      p3.ctx.fillStyle = "#34D399";
      p3.ctx.beginPath(); p3.ctx.arc(blipX, p3.h * 0.44, 4.5, 0, Math.PI * 2); p3.ctx.fill();
      p3.ctx.fillStyle = "#94A3B8";
      p3.ctx.font = '600 10.5px "JetBrains Mono", monospace';
      p3.ctx.fillText("POST /v1/chat/completions", 16, 26);
      const fullTxt = "tokens: 18.4 t/s · hop: ~8 KB";
      const showN = Math.max(8, Math.floor(((t * 14) % (fullTxt.length + 14))));
      p3.ctx.fillStyle = "#34D399";
      p3.ctx.fillText(`> ${fullTxt.slice(0, showN)}`, 16, p3.h - 22);
    }
    if (!reduced) requestAnimationFrame(drawAll);
  }
  requestAnimationFrame(drawAll);
}
function initEngineLedDisplay() {
  const canvas = document.getElementById("engine-led-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const N = 32;
  let w = 0, h = 0, dpr = 1, visible = true;
  function resize() {
    const r = canvas.getBoundingClientRect();
    w = Math.max(200, Math.round(r.width));
    h = Math.max(180, Math.round(r.height));
    dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  resize();
  window.addEventListener("resize", resize);
  if ("IntersectionObserver" in window) {
    new IntersectionObserver((en) => { visible = en[0] ? en[0].isIntersecting : true; }, { rootMargin: "120px" }).observe(canvas);
  }
  function evalLed(mode, x, y, t) {
    const cx = x - 15.5, cy = y - 15.5;
    const dist = Math.sqrt(cx * cx + cy * cy);
    if (mode === "coordinator") {
      if (dist < 3.4) return [79, 139, 255, 1];
      const nodes = [[-10, -8], [10, -8], [0, 10]];
      for (const [nx, ny] of nodes) {
        const nd = Math.hypot(cx - nx, cy - ny);
        if (nd < 2.6) return [56, 189, 248, 0.95];
      }
      const ring = Math.abs(dist - ((t * 7) % 14));
      if (ring < 1.1 && dist < 13) return [79, 139, 255, 0.72];
      return null;
    }
    if (mode === "gguf") {
      const col = Math.floor(x / 11);
      const localX = x % 11;
      if (localX < 2 || localX > 8) return null;
      const cap = [24, 19, 14][col] || 14;
      const animCap = cap * (0.72 + 0.28 * Math.sin(t * 2.2 + col));
      if (31 - y < animCap) {
        const rgb = col === 0 ? [79, 139, 255] : col === 1 ? [56, 189, 248] : [16, 185, 129];
        return [rgb[0], rgb[1], rgb[2], (y % 3 === 0) ? 0.55 : 0.96];
      }
      return null;
    }
    if (mode === "pytorch") {
      if (y % 4 === 1 && x >= 3 && x <= 28) {
        const wave = Math.sin(x * 0.35 - t * 5.2 + y * 0.4);
        if (wave > 0.15) return [79, 139, 255, 0.4 + wave * 0.6];
        return [56, 189, 248, 0.22];
      }
      return null;
    }
    if (mode === "resilience") {
      const shift = (x + t * 12) % 32;
      let ty = 16;
      if (shift > 10 && shift < 13) ty = 8;
      else if (shift >= 13 && shift < 16) ty = 23;
      else if (shift >= 16 && shift < 18) ty = 13;
      if (Math.abs(y - ty) < 1.4) {
        const spike = ty !== 16;
        return spike ? [245, 158, 11, 1] : [16, 185, 129, 0.9];
      }
      return null;
    }
    const inShield = Math.abs(cx) < 10 - Math.max(0, cy) * 0.72 && cy > -11 && cy < 12;
    const border = inShield && (Math.abs(Math.abs(cx) - (10 - Math.max(0, cy) * 0.72)) < 1.5 || Math.abs(cy + 10) < 1.3);
    if (border) return [79, 139, 255, 0.95];
    if (inShield && ((x + y + Math.floor(t * 4)) % 5 === 0)) return [56, 189, 248, 0.78];
    return null;
  }
  function draw(nowMs) {
    if (visible) {
      const t = reduced ? 1.2 : nowMs * 0.001;
      ctx.fillStyle = "#05070B";
      ctx.fillRect(0, 0, w, h);
      const pad = 10;
      const stepX = (w - pad * 2) / N;
      const stepY = (h - pad * 2) / N;
      const rad = Math.min(stepX, stepY) * 0.36;
      for (let y = 0; y < N; y++) {
        for (let x = 0; x < N; x++) {
          const px = pad + (x + 0.5) * stepX;
          const py = pad + (y + 0.5) * stepY;
          ctx.fillStyle = "#111826";
          ctx.beginPath(); ctx.arc(px, py, rad * 0.78, 0, Math.PI * 2); ctx.fill();
          const lit = evalLed(currentArch, x, y, t);
          if (lit) {
            ctx.fillStyle = `rgba(${lit[0]},${lit[1]},${lit[2]},${(lit[3] * 0.28).toFixed(2)})`;
            ctx.beginPath(); ctx.arc(px, py, rad * 2.1, 0, Math.PI * 2); ctx.fill();
            ctx.fillStyle = `rgba(${lit[0]},${lit[1]},${lit[2]},${lit[3].toFixed(2)})`;
            ctx.beginPath(); ctx.arc(px, py, rad, 0, Math.PI * 2); ctx.fill();
          }
        }
      }
    }
    if (!reduced) requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);
}
function inspectArchNode(key, animate) {
  const item = ARCH_DATA[key];
  if (!item) return;
  const changed = currentArch !== key;
  currentArch = key;
  const idx = Math.max(0, ARCH_ORDER.indexOf(key));
  const counter = document.getElementById("wb-counter-cur");
  if (counter) counter.textContent = String(idx + 1).padStart(2, "0");
  const fill = document.getElementById("wb-scroll-fill");
  if (fill && !animate) fill.style.setProperty("--wb-p", String((idx + 1) / ARCH_ORDER.length));
  const slabCode = document.getElementById("led-slab-code");
  if (slabCode) slabCode.textContent = item.ledCode;
  document.querySelectorAll(".wb-item").forEach((b) => {
    const on = b.getAttribute("data-arch") === key;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  const stage = document.getElementById("workbench-stage");
  if (animate && changed && stage) {
    stage.classList.add("is-flipping");
    clearTimeout(flipTimer);
    flipTimer = setTimeout(() => stage.classList.remove("is-flipping"), 140);
  }
  const roleEl = document.getElementById("inspector-node-role");
  const titleEl = document.getElementById("inspector-node-title");
  const descEl = document.getElementById("inspector-node-desc");
  const tagsEl = document.getElementById("inspector-tags-list");
  const cTitle = document.getElementById("wb-code-title");
  const cBody = document.getElementById("wb-code-body");
  const cBtn = document.getElementById("wb-copy-btn");
  if (roleEl) roleEl.textContent = item.module;
  if (titleEl) titleEl.textContent = item.title[currentLang] || item.title.en;
  if (descEl) descEl.textContent = item.desc[currentLang] || item.desc.en;
  if (tagsEl) tagsEl.innerHTML = item.tags.map((tg) => `<span class="spec-tag">${tg}</span>`).join("");
  if (cTitle) cTitle.textContent = item.codeTitle;
  if (cBody) cBody.innerHTML = item.code;
  if (cBtn) cBtn.setAttribute("data-copy", item.copy);
}
function updateGovernanceMode(mode) {
  currentGovMode = mode === "worker" ? "worker" : "solo";
  document.querySelectorAll(".gov-mode-btn").forEach((b) => b.classList.toggle("active", b.getAttribute("data-gov-mode") === currentGovMode));
  const pair = (GOV_COPY[currentGovMode] && GOV_COPY[currentGovMode][currentLang]) || GOV_COPY[currentGovMode].en;
  const s1 = document.getElementById("gov-state-1");
  const s2 = document.getElementById("gov-state-2");
  if (s1) s1.textContent = pair[0];
  if (s2) s2.textContent = pair[1];
}
function updateCalculator() {
  const modelSel = document.getElementById("calc-model");
  const g1 = document.getElementById("calc-gpu1");
  const g2 = document.getElementById("calc-gpu2");
  if (!modelSel || !g1 || !g2) return;
  const m = MODELS_FIT[modelSel.value] || MODELS_FIT.qwen32;
  const raw1 = parseFloat(g1.value) || 8;
  const raw2 = parseFloat(g2.value) || 16;
  const usable1 = Math.max(0.2, +(raw1 - 0.4).toFixed(1));
  const usable2 = Math.max(0.5, +(raw2 - 2.0).toFixed(1));
  const total = +(usable1 + usable2).toFixed(1);
  const p1 = Math.round((usable1 / total) * 100);
  const p2 = 100 - p1;
  document.getElementById("calc-gpu1-val").textContent = `${raw1} GB`;
  document.getElementById("calc-gpu2-val").textContent = `${raw2} GB`;
  document.getElementById("calc-req-val").textContent = `${m.req.toFixed(2)} GB`;
  document.getElementById("calc-total-val").textContent = `${total.toFixed(1)} GB`;
  document.getElementById("calc-split-val").textContent = `${p1}% / ${p2}%`;
  document.getElementById("calc-bar-ratio").textContent = `${m.req.toFixed(1)} / ${total.toFixed(1)} GB`;
  document.getElementById("calc-leg-1").textContent = `${t("calc_leg_1")} ${usable1.toFixed(1)} GB`;
  document.getElementById("calc-leg-2").textContent = `${t("calc_leg_2")} ${usable2.toFixed(1)} GB`;
  const scaleMax = Math.max(total, m.req * 1.1, 1);
  document.getElementById("calc-seg-1").style.width = `${Math.min(100, (usable1 / scaleMax) * 100).toFixed(1)}%`;
  document.getElementById("calc-seg-2").style.width = `${Math.min(100, (usable2 / scaleMax) * 100).toFixed(1)}%`;
  document.getElementById("calc-req-marker").style.left = `${Math.min(99, (m.req / scaleMax) * 100).toFixed(1)}%`;
  const badge = document.getElementById("calc-status-badge");
  badge.classList.remove("warn", "err");
  if (usable1 >= m.req) {
    badge.textContent = t("calc_ok_single");
  } else if (total >= m.req) {
    badge.textContent = t("calc_ok_pool");
    badge.classList.add("warn");
  } else {
    badge.textContent = t("calc_fail");
    badge.classList.add("err");
  }
  const mb1 = Math.round(usable1 * 1024);
  const mb2 = Math.round(usable2 * 1024);
  const cmdEl = document.getElementById("calc-live-cmd");
  if (cmdEl) {
    cmdEl.textContent = usable1 >= m.req
      ? `llama-server -m ${modelSel.value}.gguf -ngl 999`
      : `llama-server --rpc 192.168.1.14:50052 -ts ${mb1},${mb2} -ngl 999`;
  }
}
function updateReqView(key) {
  const r = REQ_PRESETS[key];
  if (!r) return;
  currentReq = key;
  document.querySelectorAll(".req-btn").forEach((b) => b.classList.toggle("active", b.getAttribute("data-req") === key));
  document.getElementById("req-big-num").textContent = r.big;
  document.getElementById("req-big-unit").textContent = r.unit;
  document.getElementById("req-big-note").textContent = r.note[currentLang] || r.note.en;
}
function updateShot(key, animate) {
  const s = SHOTS[key];
  if (!s) return;
  const changed = currentShot !== key;
  currentShot = key;
  document.querySelectorAll(".mini-tab-dark").forEach((b) => b.classList.toggle("active", b.getAttribute("data-shot") === key));
  const bezel = document.getElementById("real-shot-bezel");
  const img = document.getElementById("real-ui-img");
  const cap = document.getElementById("real-ui-cap");
  if (animate && changed && bezel) {
    bezel.classList.add("is-swapping");
    setTimeout(() => {
      if (img) img.src = s.src;
      if (cap) cap.textContent = t(s.capKey);
      bezel.classList.remove("is-swapping");
    }, 110);
  } else {
    if (img) img.src = s.src;
    if (cap) cap.textContent = t(s.capKey);
  }
}
function initPillLedMatrix() {
  const canvas = document.getElementById("pill-led-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const patterns = [
    [0,0,1,0,0, 0,1,1,1,0, 1,1,1,1,1, 0,1,1,1,0, 0,0,1,0,0],
    [1,0,0,0,1, 0,1,0,1,0, 0,0,1,0,0, 0,1,0,1,0, 1,0,0,0,1],
    [1,1,1,1,1, 0,0,0,0,0, 1,1,1,1,1, 0,0,0,0,0, 1,1,1,1,1],
    [1,0,1,0,1, 1,0,1,0,1, 1,1,1,1,1, 0,0,1,0,0, 0,0,1,0,0],
    [1,1,0,0,0, 0,1,1,0,0, 0,0,1,1,0, 0,1,1,0,0, 1,1,0,0,0]
  ];
  function draw(ms) {
    const tSec = reduced ? 0 : ms * 0.004;
    const pat = patterns[activeChapterIdx % patterns.length];
    ctx.clearRect(0, 0, 24, 24);
    for (let r = 0; r < 5; r++) {
      for (let c = 0; c < 5; c++) {
        const on = pat[r * 5 + c] === 1;
        const pulse = on ? (0.68 + 0.32 * Math.sin(tSec + r * 0.6 + c * 0.4)) : 0.14;
        ctx.fillStyle = on ? `rgba(79,139,255,${pulse.toFixed(2)})` : "rgba(148,163,184,0.18)";
        ctx.fillRect(2 + c * 4.2, 2 + r * 4.2, 2.8, 2.8);
      }
    }
    if (!reduced) requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);
}
function initScrollFlipWorkbench() {
  const sec = document.getElementById("architecture");
  const fill = document.getElementById("wb-scroll-fill");
  if (!sec || !fill) return;
  let rafId = 0;
  function update() {
    rafId = 0;
    if (window.innerWidth <= 1024) return;
    const rect = sec.getBoundingClientRect();
    const range = Math.max(1, sec.offsetHeight - window.innerHeight);
    const p = Math.max(0, Math.min(1, (-rect.top) / range));
    fill.style.setProperty("--wb-p", Math.max(0.08, p).toFixed(3));
    if (archScrollLock) return;
    const idx = Math.min(ARCH_ORDER.length - 1, Math.floor(p * ARCH_ORDER.length));
    const targetKey = ARCH_ORDER[idx];
    if (targetKey && targetKey !== currentArch && rect.top <= 120 && rect.bottom >= window.innerHeight * 0.45) {
      inspectArchNode(targetKey, true);
    }
  }
  function schedule() { if (!rafId) rafId = requestAnimationFrame(update); }
  window.addEventListener("scroll", schedule, { passive: true });
  window.addEventListener("resize", schedule);
  update();
}
function initParticleWordmark() {
  const canvas = document.getElementById("shard-wordmark-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const word = canvas.getAttribute("data-word") || "SHARD";
  const alwaysDark = canvas.getAttribute("data-surface") === "dark";
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let w = 0, h = 0, dpr = 1, particles = [], visible = true, cell = 6;
  const pointer = { x: -9999, y: -9999, vx: 0, vy: 0, active: false, t: 0 };
  function buildParticles() {
    const rect = canvas.getBoundingClientRect();
    w = Math.max(300, Math.round(rect.width));
    h = Math.max(120, Math.round(rect.height));
    dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const off = document.createElement("canvas");
    off.width = w;
    off.height = h;
    const octx = off.getContext("2d", { willReadFrequently: true });
    const fontFamily = '"Onest", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    octx.font = `800 100px ${fontFamily}`;
    const probe = octx.measureText(word);
    const fontSize = Math.max(48, Math.min((w * 0.88) / Math.max(1, probe.width / 100), h * 0.86));
    octx.clearRect(0, 0, w, h);
    octx.font = `800 ${fontSize}px ${fontFamily}`;
    octx.fillStyle = "#FFFFFF";
    octx.textAlign = "center";
    octx.textBaseline = "middle";
    octx.fillText(word, w * 0.5, h * 0.53);
    const data = octx.getImageData(0, 0, w, h).data;
    cell = Math.max(5, Math.min(8, Math.round(w / 160)));
    let seed = 4219;
    function rand() {
      seed = (1664525 * seed + 1013904223) >>> 0;
      return seed / 4294967295;
    }
    particles = [];
    for (let y = Math.floor(cell * 0.5); y < h; y += cell) {
      for (let x = Math.floor(cell * 0.5); x < w; x += cell) {
        const alpha = data[(y * w + x) * 4 + 3] / 255;
        if (alpha < 0.22) continue;
        const r1 = rand(), r2 = rand();
        const size = (r1 < 0.08) ? cell * (0.65 + 0.22 * r2) : cell * (0.36 + 0.28 * r2);
        const ny = y / h;
        particles.push({
          x, y, nx: x / w, ny, dx: 0, dy: 0, vx: 0, vy: 0, heat: 0, size,
          square: rand() < 0.78, phase: rand() * Math.PI * 2,
          fade: 0.35 + 0.65 * Math.min(1, Math.max(0, (ny - 0.08) / 0.76))
        });
      }
    }
  }
  function updatePointer(ev) {
    const rect = canvas.getBoundingClientRect();
    const nx = ev.clientX - rect.left;
    const ny = ev.clientY - rect.top;
    const now = performance.now();
    if (pointer.active) {
      const dt = Math.max(8, now - pointer.t);
      pointer.vx = ((nx - pointer.x) / dt) * 16;
      pointer.vy = ((ny - pointer.y) / dt) * 16;
    }
    pointer.x = nx; pointer.y = ny; pointer.t = now; pointer.active = true;
  }
  function leavePointer() { pointer.active = false; }
  canvas.addEventListener("pointermove", updatePointer);
  canvas.addEventListener("mousemove", updatePointer);
  canvas.addEventListener("pointerdown", (ev) => {
    updatePointer(ev);
    if (ev.pointerType !== "mouse") setTimeout(leavePointer, 520);
  });
  canvas.addEventListener("pointerleave", leavePointer);
  canvas.addEventListener("mouseleave", leavePointer);
  canvas.addEventListener("pointercancel", leavePointer);
  if ("ResizeObserver" in window) {
    new ResizeObserver(() => buildParticles()).observe(canvas);
  } else {
    window.addEventListener("resize", buildParticles);
  }
  if ("IntersectionObserver" in window) {
    new IntersectionObserver((en) => { visible = en[0] ? en[0].isIntersecting : true; }, { rootMargin: "160px" }).observe(canvas);
  }
  buildParticles();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => buildParticles());
  let lastMs = performance.now();
  function tick(nowMs) {
    const dt = Math.min(34, Math.max(8, nowMs - lastMs)) / 16.67;
    lastMs = nowMs;
    if (visible && particles.length) {
      pointer.vx *= 0.82;
      pointer.vy *= 0.82;
      const radius = Math.max(84, Math.min(144, w * 0.13));
      const rSq = radius * radius;
      const damp = Math.pow(0.84, dt);
      const cool = Math.pow(0.92, dt);
      const tSec = reduced ? 0 : nowMs * 0.0012;
      const useDark = alwaysDark || currentTheme === "dark";
      ctx.clearRect(0, 0, w, h);
      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];
        let fx = 0, fy = 0;
        if (pointer.active) {
          const ex = p.x + p.dx - pointer.x;
          const ey = p.y + p.dy - pointer.y;
          const d2 = ex * ex + ey * ey;
          if (d2 < rSq) {
            const d = Math.sqrt(d2) || 1;
            const u = 1 - d / radius;
            const f = u * u;
            p.heat = Math.max(p.heat, f);
            if (!reduced) {
              const ux = ex / d, uy = ey / d;
              fx += ux * f * 3.35 - uy * f * 1.05 + pointer.vx * f * 0.08;
              fy += uy * f * 3.35 + ux * f * 1.05 + pointer.vy * f * 0.08;
            }
          }
        }
        if (!reduced && (p.dx || p.dy || p.vx || p.vy || fx || fy)) {
          fx -= 0.084 * p.dx;
          fy -= 0.084 * p.dy;
          p.vx = (p.vx + fx * dt) * damp;
          p.vy = (p.vy + fy * dt) * damp;
          p.dx += p.vx * dt;
          p.dy += p.vy * dt;
          if (Math.abs(p.dx) + Math.abs(p.dy) + Math.abs(p.vx) + Math.abs(p.vy) < 0.025) {
            p.dx = 0; p.dy = 0; p.vx = 0; p.vy = 0;
          }
        }
        p.heat = p.heat > 0.004 ? p.heat * cool : 0;
        const wave = 0.5 + 0.5 * Math.sin(p.nx * 5.2 - tSec * 1.45 + p.ny * 1.6 + p.phase * 0.25);
        const pulse = Math.pow(wave, 2.2);
        const heat = p.heat;
        const blend = Math.min(1, pulse * 0.55 + heat * 0.95);
        const alpha = Math.min(1, 0.36 + 0.52 * p.fade + 0.18 * pulse + 0.45 * heat);
        let r, g, b;
        if (useDark) {
          r = Math.round(92 + (120 - 92) * blend + 125 * heat);
          g = Math.round(128 + (182 - 128) * blend + 68 * heat);
          b = Math.round(192 + (255 - 192) * blend);
        } else {
          r = Math.round(18 + (28 - 18) * blend);
          g = Math.round(35 + (78 - 35) * blend + 55 * heat);
          b = Math.round(68 + (216 - 68) * blend + 32 * heat);
        }
        ctx.fillStyle = `rgba(${Math.min(255, r)},${Math.min(255, g)},${Math.min(255, b)},${alpha.toFixed(2)})`;
        const drawSz = p.size * (1 + heat * 0.55);
        const px = p.x + p.dx, py = p.y + p.dy;
        if (p.square) ctx.fillRect(px - drawSz * 0.5, py - drawSz * 0.5, drawSz, drawSz);
        else { ctx.beginPath(); ctx.arc(px, py, drawSz * 0.52, 0, Math.PI * 2); ctx.fill(); }
      }
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}
function initScrollChoreography() {
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const hud = document.querySelector(".corner-hud");
  const darkZones = Array.from(document.querySelectorAll(".surface-dark"));
  const steps = Array.from(document.querySelectorAll(".step-row"));
  const dockProg = document.getElementById("dock-progress");
  const pillLinks = Array.from(document.querySelectorAll(".pill-link"));
  const trackedIds = ["story", "about", "how-it-works", "architecture", "governance"];
  const trackedSecs = trackedIds.map((id) => document.getElementById(id)).filter(Boolean);
  let raf = 0;
  function onScroll() {
    raf = 0;
    const maxScroll = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
    const pct = Math.max(0, Math.min(100, (window.scrollY / maxScroll) * 100));
    if (dockProg) dockProg.style.width = `${pct.toFixed(1)}%`;
    if (hud && darkZones.length) {
      const overDark = darkZones.some((z) => {
        const r = z.getBoundingClientRect();
        return r.top <= 48 && r.bottom >= 24;
      });
      hud.classList.toggle("over-dark", overDark || currentTheme === "dark");
    }
    let activeId = "";
    const probeY = window.innerHeight * 0.38;
    for (let i = 0; i < trackedSecs.length; i++) {
      const r = trackedSecs[i].getBoundingClientRect();
      if (r.top <= probeY && r.bottom >= 80) {
        activeId = trackedSecs[i].id;
        activeChapterIdx = i;
      }
    }
    pillLinks.forEach((a) => a.classList.toggle("active", a.getAttribute("data-section") === activeId));
    if (steps.length) {
      let bestStep = null;
      let bestDist = Infinity;
      const mid = window.innerHeight * 0.42;
      steps.forEach((st) => {
        const r = st.getBoundingClientRect();
        const dist = Math.abs((r.top + r.height * 0.35) - mid);
        if (r.top < window.innerHeight * 0.78 && r.bottom > 80 && dist < bestDist) {
          bestDist = dist;
          bestStep = st;
        }
      });
      steps.forEach((st) => st.classList.toggle("is-active", st === bestStep));
      if (bestStep && !shotManualLock) {
        const shotKey = bestStep.getAttribute("data-step-shot");
        if (shotKey && shotKey !== currentShot) updateShot(shotKey, true);
      }
    }
  }
  function schedule() { if (!raf) raf = requestAnimationFrame(onScroll); }
  window.addEventListener("scroll", schedule, { passive: true });
  window.addEventListener("resize", schedule);
  onScroll();
  const topBtn = document.getElementById("scroll-top-btn");
  if (topBtn) {
    topBtn.addEventListener("click", () => window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" }));
  }
  if (!reduced) {
    document.querySelectorAll(".magnetic-btn").forEach((btn) => {
      btn.addEventListener("pointermove", (ev) => {
        if (ev.pointerType !== "mouse") return;
        const r = btn.getBoundingClientRect();
        const dx = Math.max(-4, Math.min(4, (ev.clientX - (r.left + r.width * 0.5)) * 0.08));
        const dy = Math.max(-3, Math.min(3, (ev.clientY - (r.top + r.height * 0.5)) * 0.14));
        btn.style.transform = `translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px)`;
      });
      btn.addEventListener("pointerleave", () => { btn.style.transform = ""; });
    });
  }
}
function setupTabs() {
  document.querySelectorAll(".cli-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.getAttribute("data-cli-tab");
      document.querySelectorAll(".cli-tab-btn").forEach((b) => b.classList.toggle("active", b === btn));
      document.querySelectorAll(".cli-panel").forEach((p) => p.classList.toggle("active", p.id === target));
    });
  });
  document.querySelectorAll(".install-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.getAttribute("data-install-tab");
      document.querySelectorAll(".install-tab-btn").forEach((b) => b.classList.toggle("active", b === btn));
      document.querySelectorAll(".install-panel").forEach((p) => p.classList.toggle("active", p.id === target));
    });
  });
  document.querySelectorAll(".wb-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.getAttribute("data-arch");
      const idx = ARCH_ORDER.indexOf(key);
      inspectArchNode(key, true);
      const sec = document.getElementById("architecture");
      if (sec && window.innerWidth > 1024 && idx >= 0) {
        const rect = sec.getBoundingClientRect();
        const range = Math.max(1, sec.offsetHeight - window.innerHeight);
        const targetY = window.scrollY + rect.top + ((idx + 0.35) / ARCH_ORDER.length) * range;
        const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        archScrollLock = true;
        window.scrollTo({ top: targetY, behavior: reduced ? "auto" : "smooth" });
        setTimeout(() => { archScrollLock = false; }, 650);
      }
    });
  });
  document.querySelectorAll(".req-btn").forEach((btn) => btn.addEventListener("click", () => updateReqView(btn.getAttribute("data-req"))));
  document.querySelectorAll(".gov-mode-btn").forEach((btn) => btn.addEventListener("click", () => updateGovernanceMode(btn.getAttribute("data-gov-mode"))));
  document.querySelectorAll(".mini-tab-dark").forEach((btn) => {
    btn.addEventListener("click", () => {
      shotManualLock = true;
      updateShot(btn.getAttribute("data-shot"), true);
      setTimeout(() => { shotManualLock = false; }, 2800);
    });
  });
}
function setupCopyButtons() {
  document.querySelectorAll(".copy-code-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const text = btn.getAttribute("data-copy") || "";
      try {
        await navigator.clipboard.writeText(text);
      } catch (e) {
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      }
      if (!btn.classList.contains("chip-copy") && !btn.classList.contains("chip-copy-dark")) {
        const orig = t("btn_copy");
        btn.textContent = t("btn_copied");
        setTimeout(() => { btn.textContent = orig; }, 1400);
      } else {
        btn.style.borderColor = "#10B981";
        btn.style.color = "#10B981";
        setTimeout(() => { btn.style.borderColor = ""; btn.style.color = ""; }, 1200);
      }
    });
  });
}
function setupFaq() {
  document.querySelectorAll(".faq-question-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const item = btn.closest(".faq-item");
      const open = item.classList.toggle("open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  });
}
function setupMobileNav() {
  const btn = document.getElementById("mobile-menu-btn");
  const drawer = document.getElementById("mobile-drawer");
  if (!btn || !drawer) return;
  btn.addEventListener("click", () => {
    const next = drawer.hidden;
    drawer.hidden = !next;
    btn.setAttribute("aria-expanded", next ? "true" : "false");
  });
  drawer.querySelectorAll("a").forEach((a) => {
    a.addEventListener("click", () => {
      drawer.hidden = true;
      btn.setAttribute("aria-expanded", "false");
    });
  });
}
document.addEventListener("DOMContentLoaded", () => {
  const themeBtn = document.getElementById("theme-toggle-btn");
  if (themeBtn) themeBtn.addEventListener("click", () => setTheme(currentTheme === "daylight" ? "dark" : "daylight"));
  const langBtn = document.getElementById("lang-toggle-btn");
  if (langBtn) {
    langBtn.addEventListener("click", () => {
      const next = LANG_ORDER[(LANG_ORDER.indexOf(currentLang) + 1) % LANG_ORDER.length];
      setLanguage(next);
    });
  }
  ["calc-model", "calc-gpu1", "calc-gpu2"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener("input", updateCalculator);
      el.addEventListener("change", updateCalculator);
    }
  });
  setupTabs();
  setupCopyButtons();
  setupFaq();
  setupMobileNav();
  setTheme("daylight");
  if (document.querySelector("[data-i18n]")) setLanguage("en");
  initHeroLedReel();
  initBentoMotionScenes();
  initEngineLedDisplay();
  initPillLedMatrix();
  initScrollFlipWorkbench();
  initParticleWordmark();
  initScrollChoreography();
});