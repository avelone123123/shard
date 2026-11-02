# Журнал изменений Shard Engine
## [2026-11-02 14:10] Полное удаление OpenRouter из Shard, 100% локальный инференс и поиск среди 3 000 000+ моделей Hugging Face
### 1. Зона ответственности
- **Компоненты**: `engine.py`, `hf_hub.py`, `server.py`, `dashboard.html`, `agent.md`, `skill.md`.
- **Стек**: 100% Local On-Device Inference + AirLLM Layer Streaming + Hugging Face Hub Global Search.
### 2. Причина изменений
- Категорический запрет на использование облачных токенов OpenRouter в локальном рантайме Shard: Shard создан как независимый локальный движок запуска моделей на ноутбуке (NVIDIA RTX 2050 4GB VRAM + 16GB RAM) по принципам AirLLM без внешних серверов.
- Токены OpenRouter и подписка Gemini Flash 3.8 закреплены исключительно за студией 50 AI-агентов Shard Triad (`shard-triad`) для автономной разработки 24/7.
- Расширение каталога: в Hugging Face Hub доступно свыше 3 000 000 моделей; сняты ограничения выборки, добавлен прямой резолвинг любого `repo_id` (owner/model) и глобальный поиск без жесткого фильтра text-generation.
### 3. Список изменений
- `engine.py`: полностью удалены модуль `requests`, функции `_load_api_key`, `_map_model` и вызовы `openrouter.ai`. Реализован 100% локальный движок инференса `_local_reasoning_and_response` с аппаратной привязкой к RTX 2050 и системной памяти.
- `hf_hub.py`: снят ограничивающий фильтр `text-generation` при поиске, добавлен прямой резолвинг моделей по точному идентификатору с `/`, лимит увеличен до 100.
- `server.py`: параметр `limit` в `/api/hf/models` увеличен до 100 по умолчанию.
- `dashboard.html`: обновлен бейдж каталога («Каталог Hugging Face Hub (3 000 000+ открытых моделей)»), обновлен плейсхолдер поиска для прямого ввода `owner/model`.
- `agent.md` и `skill.md`: закреплена директива 9 и скилл 7 о разделении назначения токенов (Shard — 100% локально, Shard Triad — 24/7 работа агентов через OpenRouter/Gemini).
### 4. Статус и валидация
- Все файлы проверены: 0 комментариев, 0 пустых строк, 0 эмодзи.
- Сервер Shard работает на http://localhost:8000.
## [2026-11-02 13:50] Фикс схемы OpenCode (modalities object schema)
### 1. Зона ответственности
- **Компоненты**: `server.py`, `dashboard.html`.
- **Стек**: PowerShell Automation + OpenCode Zod Schema Validation.
### 2. Причина изменений
- OpenCode v1.17.18 валидирует поле `modalities` моделей строго как объект `{ input: string[], output: string[] }`.
- В предыдущей версии PowerShell-скрипт передавал массив строк `@("text")`, что приводило к ошибке валидации: `Expected object | undefined, got ["text"] provider.shard.models...modalities`.
### 3. Список изменений
- `server.py`: в генераторе скрипта PowerShell для OpenCode заменено `@("text")` на `@{ input = @("text"); output = @("text") }`. При сериализации `ConvertTo-Json -Depth 6` генерируется корректный JSON-объект.
- `dashboard.html`: в блоке ручной конфигурации OpenCode обновлена структура `modalities` на `{"input": ["text"], "output": ["text"]}`.
### 4. Статус и валидация
- Скрипт `powershell -ExecutionPolicy Bypass -Command "irm 'http://localhost:8000/i?os=windows&tool=opencode&key=sk-shard-e7b54014d2fd4ad0' | iex"` выполнен успешно.
- Команда `opencode models` выполнена со статусом 0, все 5 моделей Shard (`shard/Qwen/...`, `shard/deepseek-ai/...`, `shard/google/...`, `shard/meta-llama/...`, `shard/mistralai/...`) распознаны без ошибок валидации.
- Все файлы соответствуют правилам: 0 комментариев, 0 пустых строк, 0 эмодзи.
## [2026-11-02 13:40] Карточки моделей с Hugging Face, .gitattributes для языков GitHub, Docker и agent.md
### 1. Зона ответственности
- **Компоненты**: `hf_hub.py`, `server.py`, `dashboard.html`, `.gitattributes`, `Dockerfile`, `docker-compose.yml`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `.github/`, `agent.md`, `skill.md`, `README.md`.
- **Стек**: Python + FastAPI + C++20 + MASM x64 + Docker Compose + Hugging Face Hub API.
### 2. Причина изменений
- Отображение полного описания и Model Card из Hugging Face Hub со всеми изображениями, таблицами и архитектурой по клику на модель.
- Исправление языковой статистики репозитория на GitHub (скрытие HTML через linguist-vendored, чтобы отображались C++, Assembly и Python).
- Персистентное хранение весов моделей через Docker volumes (`shard_models_volume`).
- Формирование строгих директив для 50 AI-агентов студии `shard-triad` (`agent.md` и `skill.md`): полный запрет на хардкод, приоритет системного кода над версткой, поддержка issues/PRs, автор `avelone123123`.
### 3. Список изменений
- `hf_hub.py`: добавлена функция `fetch_model_card()` с парсингом raw README.md и авторезолвингом относительных URL изображений в CDN Hugging Face (`resolve/main/`).
- `server.py`: зарегистрирован эндпоинт `GET /api/hf/model/{model_id:path}`.
- `dashboard.html`: встроен модальный просмотрщик Model Card с парсером Markdown (таблицы, код, изображения, списки, бейджи), кнопкой быстрого запуска и ссылкой на HF.
- `.gitattributes`: HTML файлы помечены как `linguist-vendored`, принудительно включено обнаружение C++ (`*.cpp`) и Assembly (`*.asm`).
- `Dockerfile` и `docker-compose.yml`: развернута контейнеризация с томами `/data/models` и `/data/shard`.
- `agent.md` и `skill.md`: созданы в `shard-triad` и продублированы в `shard`.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `.github/ISSUE_TEMPLATE/`: подготовлены стандарты сообщества с автором `avelone123123`.
### 4. Статус и валидация
- Тест эндпоинта модели `nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B`: README 130KB успешно получен, изображения отрезолвлены.
- Все файлы проверены: 0 пустых строк, 0 комментариев, 0 эмодзи.
## [2026-11-02 13:25] Локальная база API ключей, удаление ключей, фикс OpenCode и CMD
### 1. Зона ответственности
- **Компоненты**: `server.py`, `dashboard.html`, `keys_db.json`, `.gitignore`.
- **Стек**: FastAPI + JSON Storage + Vanilla JS + OpenCode SDK Schema.
### 2. Причина изменений
- Удаление хардкодных тестовых ключей: пользователь должен создавать ключи самостоятельно.
- Необходимость локальной базы данных (`keys_db.json`) для сохранения ключей между перезапусками.
- Добавление возможности удаления любого ключа через UI.
- Ошибка синтаксиса в CMD при выполнении скрипта подключения: исправлена на универсальную команду через обертку powershell bypass.
- Исправление формата конфигурации OpenCode на официальную спецификацию (@ai-sdk/openai-compatible).
- Автоматическая подстановка созданного пользователем ключа во все инструкции и скрипты подключения.
### 3. Список изменений
- `server.py`: реализованы `load_keys()` и `save_keys()` с хранилищем в `keys_db.json`.
- `server.py`: удален хардкод `sk-shard-local-avelone-dev`. Добавлен эндпоинт `POST /api/keys/delete`.
- `server.py`: в `/i?tool=opencode` встроен генератор официальной спецификации OpenCode (`provider.shard`, `@ai-sdk/openai-compatible`, `models`, `model`).
- `dashboard.html`: реализованы `loadKeys()`, `generateKey()`, `deleteKey()`. Добавлено отображение активного ключа, пустой список и кнопка удаления с SVG-иконкой.
- `dashboard.html`: обновлен `updateGuide()` — генерирует универсальную команду `powershell -ExecutionPolicy Bypass -Command "irm '...' | iex"` без синтаксических ошибок в CMD/PowerShell.
- `dashboard.html`: ручная инструкция OpenCode переведена на официальную схему JSON.
- `dashboard.html`: боковая панель и все команды автоматически отображают реальный созданный ключ пользователя.
### 4. Статус и валидация
- Проверка синтаксиса Python и JS: 0 ошибок.
- Тест выполнения в CMD: команда отработала успешно со статусом 0, `%USERPROFILE%\.config\opencode\config.json` сгенерирован по официальной схеме.
- Тест генерации и удаления ключей в `keys_db.json`: подтвержден.
- Проверка форматирования: 0 пустых строк, 0 комментариев, 0 эмодзи.
## [2026-11-02 13:12] Многосессионный чат, история диалогов и боковая панель
### 1. Зона ответственности
- **Компонент**: Веб-интерфейс чат-песочницы Shard (`dashboard.html`).
- **Стек**: Vanilla JS + LocalStorage API + SSE Token Streamer.
### 2. Причина изменений
- Пользователю требовалась возможность создавать новые диалоги («+ Новый диалог»), переключаться на старые сохраненные сессии, просматривать историю сообщений и удалять ненужные чаты без потери контекста.
### 3. Список изменений в `dashboard.html`
- Добавлена боковая панель управления сессиями `.chat-sidebar` с кнопкой `+ Новый диалог` и списком `#chatSessionsList`.
- Реализованы функции `initChatSessions()`, `saveChatSessions()`, `createNewChat(switchNow)`, `selectChat(chatId)`, `deleteChat(chatId, e)`.
- Реализован рендеринг истории сообщений `renderActiveChatMessages()` с сохранением блоков размышлений модели (thinking panel) и аппаратной телеметрии (скорость, время, токены).
- Обновлен `sendChat()`: отправляет полный контекст переписки `historyPayload` в `/v1/chat/completions`, автоматически задает заголовок диалога по первому сообщению и синхронизирует результат с `localStorage`.
- Добавлена функция безопасного экранирования HTML `escapeHtml()` во избежание XSS.
### 4. Статус и валидация
- Проверка синтаксиса JS через Node.js: 0 ошибок.
- Проверка форматирования: 0 пустых строк, 0 комментариев, 0 эмодзи (все иконки — SVG).
- Git коммит: `7a747c4` отправлен в `origin master` (`https://github.com/avelone123123/shard.git`).
- Сервер: `http://localhost:8000/` HTTP 200 OK.