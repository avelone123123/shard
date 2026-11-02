# Журнал изменений Shard Engine
## [2026-11-02 14:25] Запуск в Docker Desktop, очистка документации и 100% локальный инференс
### 1. Зона ответственности
- **Компоненты**: `Dockerfile`, `docker-compose.yml`, `main.py`, `engine.py`, `hf_hub.py`, `dashboard.html`, `README.md`, `.gitignore`.
- **Стек**: Docker Desktop + Containerization + 100% Local On-Device Runtime.
### 2. Причина изменений
- Контейнер Shard не отображался в Docker Desktop из-за отсутствия команды сборки и запуска, а также привязки Uvicorn к `127.0.0.1` вместо `0.0.0.0`.
- Очистка публичной документации репозитория: удалены все внутренние рабочие файлы и служебные директивы, репозиторий представляет собой чистый авторский open-source проект разработчика avelone123123.
- Полная изоляция от облачных API: в рантайме Shard исключены любые внешние запросы, весь инференс локализован на хосте и в контейнере.
### 3. Список изменений
- `main.py`: хост переведен на переменную окружения `SHARD_HOST` по умолчанию `0.0.0.0` для прозрачной маршрутизации портов из контейнера Docker на хост.
- `Dockerfile`: добавлены переменные окружения `SHARD_HOST=0.0.0.0`, `SHARD_PORT=8000`, `SHARD_DOCKER=1`.
- `docker-compose.yml`: развернут контейнер `shard-engine` с монтированием томов `shard_models_volume` и `shard_data_volume`, статус `Up` на порту `8000:8000`.
- `README.md`: очищены внутренние ссылки на директивы, секция переименована в `Author & Community`.
- `.gitignore`: добавлены служебные файлы для предотвращения их попадания в публичный репозиторий.
- `engine.py`: полностью локализован генератор ответов без обращений к сети.
- `hf_hub.py`: расширен поиск до 3 000 000+ моделей с прямым резолвингом ID репозиториев.
### 4. Статус и валидация
- `docker ps`: контейнер `shard-engine` активен и виден в Docker Desktop со статусом healthy.
- `http://localhost:8000/v1/ping`: успешно отвечает из запущенного Docker-контейнера.
- Все файлы проверены: 0 комментариев, 0 пустых строк, 0 эмодзи.
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
- Скрипт установки выполнен успешно, `opencode models` выполнена со статусом 0, все 5 моделей Shard распознаны без ошибок валидации.
## [2026-11-02 13:40] Карточки моделей с Hugging Face, .gitattributes для языков GitHub и Docker
### 1. Зона ответственности
- **Компоненты**: `hf_hub.py`, `server.py`, `dashboard.html`, `.gitattributes`, `Dockerfile`, `docker-compose.yml`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `.github/`, `README.md`.
- **Стек**: Python + FastAPI + C++20 + MASM x64 + Docker Compose + Hugging Face Hub API.
### 2. Причина изменений
- Отображение полного описания и Model Card из Hugging Face Hub со всеми изображениями, таблицами и архитектурой по клику на модель.
- Исправление языковой статистики репозитория на GitHub (скрытие HTML через linguist-vendored, чтобы отображались C++, Assembly и Python).
- Персистентное хранение весов моделей через Docker volumes (`shard_models_volume`).
- Автор проекта: avelone123123.
### 3. Список изменений
- `hf_hub.py`: добавлена функция `fetch_model_card()` с парсингом raw README.md и авторезолвингом относительных URL изображений в CDN Hugging Face (`resolve/main/`).
- `server.py`: зарегистрирован эндпоинт `GET /api/hf/model/{model_id:path}`.
- `dashboard.html`: встроен модальный просмотрщик Model Card с парсером Markdown, кнопкой быстрого запуска и ссылкой на HF.
- `.gitattributes`: HTML файлы помечены как `linguist-vendored`, принудительно включено обнаружение C++ (`*.cpp`) и Assembly (`*.asm`).
- `Dockerfile` и `docker-compose.yml`: развернута контейнеризация с томами `/data/models` и `/data/shard`.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `.github/ISSUE_TEMPLATE/`: подготовлены стандарты сообщества с автором `avelone123123`.
### 4. Статус и валидация
- Тест эндпоинта модели `nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B`: README 130KB успешно получен, изображения отрезолвлены.
- Все файлы проверены: 0 пустых строк, 0 комментариев, 0 эмодзи.
## [2026-11-02 13:25] Локальная база API ключей, удаление ключей, фикс OpenCode и CMD
### 1. Зона ответственности
- **Компоненты**: `server.py`, `dashboard.html`, `keys_db.json`, `.gitignore`.
- **Стек**: FastAPI + JSON Storage + Vanilla JS + OpenCode SDK Schema.
### 2. Причина изменений
- Пользователь должен создавать и удалять ключи самостоятельно.
- Локальная база данных `keys_db.json` для сохранения ключей между перезапусками.
- Универсальная команда запуска через PowerShell Bypass без синтаксических ошибок в CMD.
### 3. Список изменений
- `server.py`: реализованы `load_keys()`, `save_keys()`, добавлен эндпоинт `POST /api/keys/delete`.
- `dashboard.html`: панель управления ключами и подстановка ключа в инструкции.
### 4. Статус и валидация
- Тест генерации и удаления ключей в `keys_db.json`: подтвержден.
## [2026-11-02 13:12] Многосессионный чат, история диалогов и боковая панель
### 1. Зона ответственности
- **Компонент**: Веб-интерфейс чат-песочницы Shard (`dashboard.html`).
- **Стек**: Vanilla JS + LocalStorage API + SSE Token Streamer.
### 2. Причина изменений
- Создание новых диалогов, переключение на сохраненные сессии, сохранение блоков размышлений и аппаратной телеметрии.
### 3. Список изменений
- `dashboard.html`: боковая панель сессий, сохранение контекста переписки в `localStorage`, экранирование HTML от XSS.
### 4. Статус и валидация
- Тест многосессионного чата подтвержден, статус HTTP 200 OK.