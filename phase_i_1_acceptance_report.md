# SHARD — Phase I.1 Acceptance & Real-World Validation Report

Date: 2026-11-14  
Version under test: `shard-engine 1.8.0`  
Repository: `c:\Users\avelo\Desktop\my projects\shard`  

## Executive Summary

| Verification Area | Status | Notes |
| --- | --- | --- |
| Live PyPI Installation (`pip install shard-engine`, `[full]`) | **FAILED** | `shard-engine` is not currently published on live PyPI (`HTTP 404` on JSON & Simple APIs). |
| Local Sdist / Wheel Packaging + `[full]` Dependencies | **VERIFIED** | Fixed PEP 639 classifier bug in `pyproject.toml`; built & verified `shard_engine-1.8.0-py3-none-any.whl` and CLI/daemon outside repo. |
| Real Dashboard Browser Testing (UI & APIs) | **PARTIALLY VERIFIED** | Chrome DevTools MCP `BLOCKED` (`DevToolsActivePort` missing); verified end-to-end in real Chromium via Playwright with `0` page errors and `0` unexpected failed requests. Destructive `Repair` button click skipped to preserve trained `soup-venv`; conditional SOUP Update button absent because `0.77.2 == 0.77.2`. |
| SOUP Isolated Python 3.12 Environment | **PARTIALLY VERIFIED** | `uv` bootstrapped Python `3.12.16` + `soup-cli v0.77.2` cleanly, but default PyPI `soup-cli[train]` pulled CPU-only `torch 2.15.0+cpu` despite host `NVIDIA GeForce RTX 2050`. |
| End-to-End Dataset Lifecycle (Feedback -> Preview -> Validate) | **VERIFIED** | Created from 23 approved entries, paginated preview (`20` + `3`), schema validated, selectable in Training UI. |
| End-to-End Real SOUP SFT Training & Restart Persistence | **VERIFIED** | Fixed 2 real `soup_adapter.py` defects; ran real UI-triggered 20-step LoRA SFT (`distilgpt2`) to completion (`v2-3bd4b4`), persisted across server kill/restart. |
| Controlled SHARD Updater & Rollback Acceptance | **VERIFIED** | `7/7` scenarios passed in isolated wheel venv including real `1.8.0 -> 1.9.0` upgrade, `140`-file rollback, failure guards, and `7/7` user data files preserved. |

---

## 1. PyPI & Packaged Installation Verification

### 1.1 Live PyPI Check (`FAILED`)
Tested inside clean virtual environments outside the repository directory (`%TEMP%\shard_pypi_accept`):
- `venv_core\Scripts\pip.exe install --no-cache-dir shard-engine` -> exited `1`:
  - `ERROR: Could not find a version that satisfies the requirement shard-engine (from versions: none)`
  - `ERROR: No matching distribution found for shard-engine`
- `venv_full\Scripts\pip.exe install --no-cache-dir "shard-engine[full]"` -> exited `1`:
  - `ERROR: No matching distribution found for shard-engine[full]`
- Direct PyPI HTTP probes (`User-Agent: pip/25.0`):
  - `https://pypi.org/pypi/shard-engine/json` -> `HTTP 404 Not Found`
  - `https://pypi.org/simple/shard-engine/` -> `HTTP 404 Not Found`
  - `https://pypi.org/pypi/shard/json` -> `HTTP 200` (unrelated `shard 0.1.0` package by Bruno Amaral)

### 1.2 Packaging Defect Fixed & Local Wheel Verification (`VERIFIED`)
- **Defect found**: [pyproject.toml](file:///c:/Users/avelo/Desktop/my%20projects/shard/pyproject.toml) mixed PEP 639 `license = "MIT"` with deprecated classifier `"License :: OSI Approved :: MIT License"`, causing `python -m build` to fail under `setuptools==84.2.1`.
- **Fix applied**: Removed the deprecated license classifier from [pyproject.toml](file:///c:/Users/avelo/Desktop/my%20projects/shard/pyproject.toml).
- **Verified locally** in `%TEMP%\shard_pypi_accept\venv_wheel` (`Python 3.14.4`, Windows 11 AMD64):
  - `python -m build` produced `shard_engine-1.8.0.tar.gz` and `shard_engine-1.8.0-py3-none-any.whl`.
  - `pip show shard-engine`: `Version: 1.8.0`, `Requires: cryptography, fastapi, httpx, psutil, pydantic, requests, uvicorn, websockets`.
  - `[full]` ML dependencies verified in `venv_wheel`: `torch 2.15.0`, `transformers 5.23.0`, `accelerate 1.15.0`, `safetensors 0.8.0`, `huggingface_hub 2.4.0`.
  - Packaged dashboard asset inside site-packages: `dashboard.html` (`300591` bytes).
  - CLI checks run outside repository (`%TEMP%\shard_pypi_accept`) with isolated `SHARD_*_DIR`:
    - `shard.exe --version` -> `shard-engine 1.8.0` (`Python 3.14.4`)
    - `shard.exe help` -> exit `0`, full CLI command help printed cleanly
    - `shard.exe doctor` -> `Doctor summary: 0 error(s), 2 warning(s)` (`dashboard.html` & `engine_optimizations.json` packaged/readable, `llama-server.exe`, `ggml-rpc-server.exe`, `NVIDIA GeForce RTX 2050`; warnings: optional `Native SIMD Core` fallback and `Port 8000` already in use by pre-existing process)
    - `shard.exe start --daemon --host 127.0.0.1 --port 18089 --no-browser` -> started `PID 24084`; `GET /v1/ping` returned `{"status":"ok","version":"1.8.0","engine":"shard-local-engine",...}`; `GET /` returned `HTTP 200` (`300823` bytes); `shard.exe status` reported `ONLINE`; `shard.exe stop` stopped `PID 24084`.
  - **Observed CLI quirk**: Running foreground `shard start --port 18089` without an isolated `SHARD_DATA_DIR` while another SHARD daemon owns port `8000` exits with `[ERROR] SHARD is already running (PID 15520, port 8000)`.

---

## 2. Real Dashboard Browser Verification

- **Tooling constraint**: Chrome DevTools MCP (`browser` subagent) was `BLOCKED` by `ENOENT: ...\DevToolsActivePort`. All browser checks were executed against a real Chromium instance via Playwright on `http://127.0.0.1:18088` backed by an isolated `SHARD_DATA_DIR`.
- **Console & network telemetry**:
  - `Page errors: 0 []`
  - `Unexpected failed requests: 0 []`
  - Post-restart `HTTP >=400: []`

### 2.1 UI Workflows Verified
- **SHARD Updater UI**: Version pill (`v1.8.0`), update modal (`АКТУАЛЬНО`), check/apply action (`SHARD 1.8.0 уже актуален. | 100%`), rollback control rendered.
- **SOUP Status & Installation UI**:
  - Clicked `Установить SOUP (Python 3.12 venv)` in Chromium -> bootstrapped `uv-0.13.8`, provisioned `Python 3.12.16` + `soup-venv`, installed `soup-cli-0.77.2[train]` (`Verification passed! SOUP v0.77.2 is READY.`).
  - Clicked `"Проверить статус и версию"` -> confirmed `INSTALLED`, `0.77.2`, technical details expanded (`Venv`, `Runtime Python`, `Host Python: 3.12.16`, `Train deps OK: YES`).
  - `"Починить окружение"` button verified present in DOM (destructive repair click intentionally skipped to preserve `soup-venv` for E2E training); conditional SOUP update CTA accurately absent while `0.77.2 == 0.77.2`.
- **Model Browser UI**:
  - Listed drives `C:\` and `D:\ (Ventoy)` and navigated `D:\shard-models`.
  - With `#mbFilterMode=all`, `.gguf` files displayed `НЕ ДЛЯ SOUP` badge, disabled button `"Недоступно для обучения SOUP"`, and suggested HF model `Qwen/Qwen2.5-Coder-1.5B-Instruct`.
  - Selected real local HuggingFace SafeTensors model directory `C:\Users\avelo\.cache\huggingface\hub\models--distilgpt2\snapshots\2290a62682d06624634c1f46a6ad5be0f47f38aa` (`339.2 МБ`, `SOUP OK`).
  - Attempted browse to `C:\Windows\System32` -> blocked by backend (`HTTP 403`: `Access to system or sensitive credential directory is blocked.`).
- **Dataset & Training UI**:
  - Created datasets from 23 approved feedback entries -> opened Preview modal -> paginated Page `1` (`20` rows) to Page `2` (`3` rows) via `Вперёд` and back via `Назад` -> clicked Validate (`Датасет валиден | OK: 23 валидных записей`) -> selected dataset in `#trnJobDataset`.
  - Ran Preflight (`Все проверки пройдены — готово к запуску обучения` — 6 `PASS`, 1 CPU `WARN`), Dry Run (`job-20261114-203513-cb21fc` -> `Data OK: 20 train samples / Val: 3 samples`), live UI training click (`Запустить обучение`), jobs list refresh, and `#trnJobLogBox` log inspection.

### 2.2 Frontend Defects Fixed During Browser Testing
1. [dashboard.html](file:///c:/Users/avelo/Desktop/my%20projects/shard/dashboard.html) & [shard_engine/dashboard.html](file:///c:/Users/avelo/Desktop/my%20projects/shard/shard_engine/dashboard.html): Fixed `Технические детали среды SOUP` field mapping (`s.venv_dir || s.venv_path`, `s.python_executable || s.runtime_python`, `s.host_python_version || s.python_version`) so Venv, Runtime Python, and Host Python render actual values instead of `-`.
2. [dashboard.html](file:///c:/Users/avelo/Desktop/my%20projects/shard/dashboard.html) & [shard_engine/dashboard.html](file:///c:/Users/avelo/Desktop/my%20projects/shard/shard_engine/dashboard.html): Updated `mbLoadRoots()` so custom-labeled Windows volumes preserve drive letters (e.g., `D:\ (Ventoy)`).

---

## 3. SOUP Python 3.12 Environment Validation

- **Location**: `C:\Users\avelo\AppData\Local\Temp\shard_accept_env\data\soup-venv`
- **Interpreter**: `Python 3.12.16` (provisioned automatically by `uv-0.13.8` using `cpython-3.12.16-windows-x86_64-none`)
- **CLI**: `soup.exe --version` -> `soup v0.77.2`
- **API check**: `/api/training/soup?check_remote=1` -> `lifecycle_state: "INSTALLED"`, `soup_version: "0.77.2"`, `latest_version: "0.77.2"`, all `capabilities` flags `true`
- **Verified imports**:
