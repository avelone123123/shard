# Экосистема совместного обучения моделей (SHARD x SOUP)

SHARD объединяет распределённый локальный запуск LLM с опциональным конвейером дообучения на базе [SOUP](https://github.com/MakazhanAlpamys/Soup):

```text
CREATE -> SHARE -> CONNECT -> USE -> FEEDBACK -> TRAIN -> IMPROVE -> DEPLOY (и Откат)
```

SHARD остаётся полностью независимой средой инференса и объединения памяти по LAN. SOUP подключается как отдельный адаптер: SHARD обслуживает модели, собирает явные отзывы с согласием, очищает секреты и персональные данные, формирует неизменяемые снимки датасетов, запускает обучение через SOUP, сравнивает метрики и управляет версиями с безопасным откатом.

---

## Архитектура и границы модулей

| Компонент | Модуль | Назначение |
| --- | --- | --- |
| Адаптер SOUP | `shard_engine/soup_adapter.py` | Поиск локального `soup-cli`, проверка совместимости Python `>=3.11,<3.14`, генерация `soup.yaml`, вызов `soup data validate`, `soup train`, `soup eval custom`, `soup merge`, `soup export` |
| Конвейер отзывов и приватности | `shard_engine/feedback.py` | Изолированное хранение вкладов отдельно от чатов, маскирование секретов и ПДн до записи на диск, отзыв согласия, дедупликация, экспорт в `chatml` (SFT) и `dpo` |
| Оркестрация и реестр версий | `shard_engine/training.py` | Управление заданиями владельца, защита VRAM от OOM, отмена и `--resume`, учёт версий, оценка Baseline vs Candidate, подтверждение владельцем, деплой и откат |
| Деплой в движок | `shard_engine/engine.py` | Поддержка локальных merged HF-каталогов, LoRA-адаптеров (при наличии `peft`) и локальных `.gguf` файлов |
| Безопасность и REST API | `shard_engine/server.py` | Изоляция `/api/training/*` только для владельца (localhost или `X-Shard-Owner-Token`); `/api/feedback/*` для вкладов участников |

---

## Честные технические ограничения

- **Обучение выполняется локально на хосте**: Распределённый пул памяти SHARD через `ggml-rpc` предназначен **только для инференса**. Обучение SOUP в версии v1 запускается на одном узле (сервере-владельце).
- **Ограничение версии Python для SOUP**: Пакет `soup-cli` требует `Python >=3.11,<3.14`. При запуске SHARD на Python 3.14 установите SOUP в отдельное окружение Python 3.12 и передайте путь через переменную `SHARD_SOUP_PYTHON`:

```powershell
py -3.12 -m venv "$HOME\.soup-venv"
& "$HOME\.soup-venv\Scripts\pip.exe" install "soup-cli[train]"
$env:SHARD_SOUP_PYTHON = "$HOME\.soup-venv\Scripts\python.exe"
```

Для окружений Python 3.11–3.13 доступна прямая установка опционального пакета:

```bash
pip install "shard-engine[training]"
```

---

## Модель приватности, согласия и доступа

1. **Обычные переписки не сохраняются**: Диалоги в чате и API `/v1/*` никогда не попадают в обучающий датасет автоматически.
2. **Явное согласие**: По умолчанию включён флаг `require_explicit_consent` (`consent_given: true`).
3. **Вклады по LAN отключены по умолчанию**: `allow_remote_feedback = false`. Даже после включения владельцем удалённые клиенты не имеют доступа к чтению чужих отзывов, датасетам, запуску обучения и деплою (`HTTP 403`).
4. **Очистка секретов и ПДн**: API-ключи (`sk-*`), AWS-ключи, токены GitHub, JWT, приватные ключи PEM, email и телефоны заменяются на `[REDACTED_*]` ещё до записи на диск.
5. **Ручная модерация**: Новые вклады попадают в очередь `pending` и требуют одобрения владельцем (`require_manual_review: true`).
6. **Отзыв участия**: Любой участник может посмотреть свои записи (`/api/feedback/my`) и отозвать согласие (`/api/feedback/revoke`) с удалением содержимого с диска.
7. **Строгие форматы SFT и DPO**: Негативная оценка без лучшего варианта не попадает в SFT и не создаёт искусственные DPO-пары. Для DPO обязательны отдельные `prompt`, `chosen` и `rejected`.

---

## Команды CLI (`shard training`)

```bash
shard training doctor
shard training status
shard training privacy --allow-remote on --require-review on
shard training feedback --status pending
shard training feedback approve <entry_id>
shard training dataset build --task sft --name release-1
shard training start --dataset <snapshot_id> --base Qwen/Qwen2.5-Coder-1.5B-Instruct --dry-run
shard training start --dataset <snapshot_id> --base Qwen/Qwen2.5-Coder-1.5B-Instruct --epochs 3
shard training logs <job_id>
shard training eval <version_id>
shard training prepare <version_id> --target gguf --quant q4_k_m
shard training approve <version_id>
shard training deploy <version_id>
shard training rollback
```

