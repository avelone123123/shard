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
