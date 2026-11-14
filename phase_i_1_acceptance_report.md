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
