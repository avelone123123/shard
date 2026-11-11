# SHARD — локальный AI-движок, который *сначала* говорит, запустится ли модель

[![Tests](https://github.com/avelone123123/shard/actions/workflows/tests.yml/badge.svg)](https://github.com/avelone123123/shard/actions/workflows/tests.yml)
[![Version](https://img.shields.io/badge/Version-v1.5.1-22a06b)](https://github.com/avelone123123/shard)
[![Python](https://img.shields.io/badge/Python-3.8%2B-38bdf8)](https://github.com/avelone123123/shard)
[![License](https://img.shields.io/badge/License-MIT-38bdf8)](LICENSE)

100% локальный AI-рантайм для Windows: FastAPI-сервер + GGUF-бэкенд (llama.cpp) + нативное ядро C++20/MASM AVX2 + веб-панель.

**Проблема, которую он решает:** вы скачали модель на 20 ГБ, запустили — и программа вылетела, зависла или работает в разы медленнее, потому что модель с самого начала не влезала в ваше железо. SHARD проверяет это *до* скачивания: считает по-квантовый план VRAM/RAM/скорость по вашему реальному железу и прямо говорит, запускать или нет.

- **Pre-flight fit-адvisor** — `shard fit <model>` (и карточка в дашборде) показывает для каждого квантования, сколько ГБ уйдёт в VRAM и RAM, сколько слоёв влезет на GPU, ожидаемую скорость и вердикт: `GPU` / `HYBRID` / `IMPOSSIBLE`.
- **GGUF-бэкенд** — квантованные модели гоняются через сабпроцесс llama.cpp `llama-server` (~50 МБ, скачивается один раз), которому fit-advisor передаёт выбранное квантование и число GPU-слоёв.
- **API, совместимый с OpenAI и Anthropic** — `/v1/chat/completions`, `/v1/messages`, SSE-стриминг; настройка Claude Code, Cursor, OpenCode, Continue, Aider, Zed в один клик.
- **Нативное ядро (опционально)** — C++20 zero-copy пейджер safetensors + рукописные MASM x64 AVX2-ядра; бенчмарк — `shard test`.
- **Честный UI** — русский интерфейс, оценки помечены как оценки, ничего не выдумано.

## Быстрый старт

Бинарники больше не лежат в репозитории — запускаем из Python (GPU для просмотра каталога, скачивания и планирования не нужен):

```bash
git clone https://github.com/avelone123123/shard.git
cd shard
pip install -e .
shard start          # откроет дашборд на http://localhost:8000
```

`python main.py` делает то же самое. По умолчанию сервер слушает `0.0.0.0:8000`; задайте `SHARD_HOST=127.0.0.1`, чтобы не светить его в локальной сети.

Перед скачиванием проверяйте fit:

```bash
shard fit google/gemma-2-27b-it
shard fit Qwen/Qwen3-1.7B
```

Модели хранятся в `~/.shard/models` по умолчанию. Перенаправить их куда угодно (внешний SSD подходит) можно в настройках дашборда, командой `shard models-dir D:\models` или через переменную окружения `SHARD_MODELS_DIR`. При первом запуске GGUF один раз скачивается рантайм llama.cpp (~50 МБ) в `~/.shard/runtime`.

### Что говорит advisor (реальный вывод, RTX 2050 / 4 ГБ VRAM / 16 ГБ RAM)

Модель, которая не запустится — все шесть квантов отброшены, с причиной:

```
GPU: NVIDIA GeForce RTX 2050 (4.0 GB VRAM) | RAM: 15.7 GB
Params: 27.0B (source: name-regex) | FP16: 58.29 GB | KV-cache: 4.29 GB

QUANT         SIZE      VRAM       RAM   GPU LAYERS      SPEED  STATUS
------------------------------------------------------------------------------
Q8_0        28.7 GB     0.0 GB    29.9 GB         0/64      0.0 t/s  IMPOSSIBLE
Q6_K        22.3 GB     0.0 GB    23.2 GB         0/64      0.0 t/s  IMPOSSIBLE
Q5_K_M      19.2 GB     0.0 GB    20.2 GB         0/64      0.0 t/s  IMPOSSIBLE
Q4_K_M      16.4 GB     0.0 GB    17.4 GB         0/64      0.0 t/s  IMPOSSIBLE
Q3_K_M      13.2 GB     0.0 GB    14.2 GB         0/64      0.0 t/s  IMPOSSIBLE
Q2_K        10.3 GB     0.0 GB    11.4 GB         0/64      0.0 t/s  IMPOSSIBLE

Result: DO NOT START ON THIS HARDWARE
Note: Теоретически модель можно стримить с SSD через mmap (~0.0 ток/с), но это в 30-50 раз медленнее RAM и практически бесполезно.
```

Модель, которая запустится — рекомендованное квантование, число GPU-слоёв, ожидаемая скорость:

```
GPU: NVIDIA GeForce RTX 2050 (4.0 GB VRAM) | RAM: 15.7 GB
Params: 1.5B (source: name-regex) | FP16: 4.21 GB | KV-cache: 1.21 GB

QUANT         SIZE      VRAM       RAM   GPU LAYERS      SPEED  STATUS
------------------------------------------------------------------------------
Q8_0         1.6 GB     2.8 GB     0.0 GB       999/36     32.1 t/s  GPU
Q6_K         1.2 GB     2.5 GB     0.0 GB       999/36     36.8 t/s  GPU
...

Result: LAUNCH OK
Recommendation: Q8_0 — file 1.6 GB, 999/36 layers on GPU, ~32.1 tok/s
```

## Что потянет ваша видеокарта

Посчитано самим fit-движком (контекст 4096, резерв 2 ГБ RAM под систему). **Это оценки, а не измеренные бенчмарки** — для вашей конкретной карты и модели всегда запускайте `shard fit`.

| VRAM GPU | Самая большая модель целиком в VRAM | Веса | Скорость | Гибрид (VRAM+RAM) | Скорость |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 4 ГБ | ~3.5B (Q5_K_M) | 2,5 ГБ | ~24 ток/с | 70B (Q2_K, mmap) | ~1,3 ток/с |
| 6 ГБ | 8B (Q2_K) | 3,1 ГБ | ~17 ток/с | 70B (Q2_K, mmap) | ~1,4 ток/с |
| 8 ГБ | 8B (Q5_K_M) | 5,7 ГБ | ~11 ток/с | 70B (Q2_K, mmap) | ~1,4 ток/с |
| 12 ГБ | 14B (Q4_K_M) | 8,5 ГБ | ~7,7 ток/с | 70B (Q2_K, mmap) | ~1,6 ток/с |
| 16 ГБ | 32B (Q2_K) | 12,2 ГБ | ~5,5 ток/с | 70B (Q2_K, mmap) | ~1,7 ток/с |
| 24 ГБ | 32B (Q4_K_M) | 19,4 ГБ | ~3,8 ток/с | 70B (Q2_K, mmap) | ~2,2 ток/с |

\* Скорости в гибридном режиме — теоретическая верхняя граница: часть модели стримится через mmap из RAM/SSD, поэтому реальные цифры сильно зависят от пропускной способности памяти и SSD. Advisor помечает такие конфигурации вердиктом `HYBRID` и предупреждает об этом.

## Дашборд

Откройте `http://localhost:8000`:

- просмотр всего каталога Hugging Face Hub с фильтром по вашей VRAM/RAM,
- скачивание с живым прогрессом (включая многокомпонентные GGUF) на любой диск,
- загрузка/выгрузка модели с этапами прогресса, чат со SSE-стримингом,
- карточка fit-advisor по модели *до* скачивания,
- менеджер дисков и хранилища, управление API-ключами (`sk-shard-*`), настройка CLI/IDE в один клик.

## API

| Эндпоинт | Назначение |
| :--- | :--- |
| `POST /v1/chat/completions` | Chat в формате OpenAI (с SSE-стримингом) |
| `POST /v1/messages`, `POST /v1/messages/count_tokens` | Messages в формате Anthropic |
| `GET /v1/models`, `GET /v1/ping` | Список моделей / health |
| `GET /api/fit/{model_id}` | Fit-отчёт в JSON (вердикт, по-квантовый план, KV-кэш) |
| `POST /api/models/load`, `POST /api/models/unload` | Управление движком |
| `GET /api/hf/models`, `GET /api/hf/model/{id}` | Каталог и метаданные HF |
| `GET /api/drives`, `GET/POST /api/settings`, `GET /api/keys` | Диски, настройки, ключи |

## CLI

| Команда | Что делает |
| :--- | :--- |
| `shard start --host --port` | Запуск сервера (дашборд на `/`) |
| `shard gui` | Нативный десктоп-студио (если собран) |
| `shard fit <model_id>` | Pre-flight fit-отчёт по вашему железу |
| `shard models-dir <path>` | Показать/задать каталог моделей |
| `shard list --category` | Ранжирование HF-моделей под эту машину |
| `shard run <model_id>` | REPL в терминале с телеметрией ток/с |
| `shard status` | GPU, VRAM, RAM, потоки CPU, состояние нативного ядра |
| `shard test` | Бенчмарк 12 семейств AVX2 + C++20 ядер |
| `shard key --gen` | Генерация локального API-ключа |

## Нативное ядро (опционально)

Python-путь (сервер, CLI, fit-advisor, GGUF-бэкенд llama.cpp) работает без какой-либо сборки. Нативная часть — отдельный движок safetensors:

- **C++20 ядро (`shard_core.dll`)** — пейджинг safetensors через Win32 `MapViewOfFile`, векторизованные LayerNorm / Softmax / RoPE / SwiGLU / косинусная близость, де-квантование Q4/Q8_0.
- **MASM x64 (`shard_asm.asm`)** — рукописные AVX2/FMA-ядра (ymm0–ymm7).

Соберите сами (нужны MSVC Build Tools с `ml64`):

```bash
python native/build_native.py
```

Пример вывода `shard test` (12 семейств ядер; цифры зависят от машины):

```
Kernel: Shard Native Core (C++20 Zero-Copy + MASM x64 AVX2)
[01/12] Dot-Product FMA:  36.45 GFLOPS (22,480 us)
[05/12] Q8_0 Dequant:     2457.3 ops/sec (AVX2 Int8->FP32)
[06/12] Q4_0 Dequant:     2561.8 ops/sec (AVX2 Nibble Unpack)
[10/12] Stable Softmax:   10565.9 ops/sec (MASM vmaxps + Scale)
Native Pipeline:          ALL 12 KERNEL FAMILIES VALIDATED [OK]
```

## Тесты и CI

- `python -m unittest tests_shard.py` — **50 фокусных unit-тестов** (математика fit-advisor, выбор GGUF-кандидатов, настройки, компиляция модулей). Каждое ожидаемое значение в наборе проверено эмпирически перед записью.
- CI гоняет тот же набор на `windows-latest` для Python 3.12–3.14 (бейдж выше).

## Честные ограничения

- Скорости (ток/с) — оценки из модели «ГБ → пропускная способность», отдельного бенчмарк-набора по моделям пока нет; измеренные бенчмарки запланированы.
- Число параметров при отсутствии `config.json` офлайн восстанавливается по имени модели (name-regex); advisor помечает источник.
- В первую очередь Windows: работа с дисками и нативная сборка нацелены на Windows; Linux/macOS пока нет.
- Нативный движок safetensors не умеет выгружать слои из VRAM — если VRAM мало, используйте GGUF-модели (путь llama.cpp именно про слабое железо).
- Одна GPU; multi-GPU пока нет.
- Интерфейс русский.

## Roadmap

- **LAN-шардинг** — расщепление одной модели на 2+ ПК в вашей сети (мастер владеет планировщиком/токенизатором, воркеры — своими диапазонами слоёв). Эскиз дизайна: [docs/LAN_SHARDING_DESIGN.md](docs/LAN_SHARDING_DESIGN.md).
- Измеренные бенчмарки по моделям — в `benchmarks/`.
- Английский UI, сборки Linux/macOS.

## English version

[README.md](README.md)

## Автор и лицензия

- Автор и мейнтейнер: [avelone123123](https://github.com/avelone123123)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md) | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- Лицензия: MIT
