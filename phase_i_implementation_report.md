# SHARD Phase I — Implementation, Hardening & Verification Report

## 1. Executive Summary
Phase I hardened SHARD's user-facing reliability across README installation instructions, GitHub release/tag update checking and rollback, isolated SOUP training runtime management (especially on Windows Python 3.14 hosts), a secure local model file browser, dataset creation/preview/validation, training preflight and lifecycle orchestration, and dashboard synchronization/JS validity.

**Repository policy note (Rule 9):** No `git commit` or `git push` was executed; all changes remain in the working tree for inspection.

---

## 2. Root Causes Found & Fixed

### 2.1 PyPI Installation Instructions (`README.md`, `README.ru.md`, `README.kz.md`)
- **Cause:** READMEs still instructed users to install via `git+https://github.com/avelone123123/shard.git` only, whereas `shard-engine` (`1.8.0`) was published to PyPI.
- **Fix:** Updated installation sections across English, Russian, and Kazakh READMEs to primary `pip install shard-engine` and `pip install "shard-engine[full]"` (plus `pip install --upgrade shard-engine`), while preserving Git source install as an alternative.

### 2.2 GitHub Updater & `-update` CLI (`shard_engine/updater.py`, `shard_engine/cli.py`, `shard_engine/server.py`)
- **Causes & Hardening Fixes Implemented:**
  - SHARD lacked a native GitHub release/tag update detector, startup non-blocking notification banner, owner-only update API routes, and atomic archive staging with rollback.
  - Hardened `_http_get()` in `updater.py` (`isinstance(getattr(resp, "url", None), str)`) for test/proxy responses lacking `.url`.
  - Guarded `fetch_latest_remote_info()` tag parsing (`isinstance(rt_list, list)`) and added `try/except` inside `check_for_updates()` with `probe_git=False` default so read-only checks (`--check`) do not spawn `git.exe`.
  - Enforced strict URL validation (`validate_remote_url` HTTPS-only GitHub allowlist), `validate_git_ref`, SHA-256 verification and Zip Slip prevention in `verify_archive_artifact()`, plus `rollback_update()` preserving user config/keys/models/datasets.
  - Added `-update` / `--update` normalization in `cli.py:main()` and flags (`--check`, `--force`, `-y`/`--yes`, `--rollback`, `--target`, `--prerelease`, `--json`).

### 2.3 SOUP Training Lifecycle & Python 3.14 Host Compatibility (`shard_engine/soup_adapter.py`, `shard_engine/training.py`)
- **Causes:**
  - Host environment runs Python `3.14.4` (`C:\Python314\python.exe`), while upstream `MakazhanAlpamys/Soup` (`0.77.2`) declares `Requires-Python >=3.11,<3.14`.
  - Training dashboard previously mishandled installer response objects (`data.installer`), leading to misleading `[ok]` / ready state impressions when SOUP was absent.
  - `training.preflight_training_check()` previously failed to align the string return type of `model_browser.resolve_and_validate_training_base()` with inspection metadata, and `training.py` needed explicit uppercase `lifecycle_state` (`QUEUED`, `PREPARING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`).
- **Fix:**
  - Added 10-state SOUP lifecycle (`NOT_INSTALLED`, `CHECKING_ENVIRONMENT`, `FINDING_PYTHON`, `CREATING_VENV`, `INSTALLING`, `VERIFYING`, `INSTALLED`, `UPDATE_AVAILABLE`, `UPDATING`, `FAILED`), `find_compatible_python()`, isolated venv paths (`paths.soup_venv_dir()`), and remote upstream `0.77.2` check.
  - Updated `training.py` with `_LIFECYCLE_MAP`, `_job_lifecycle_state()`, `_enrich_job_fields()`, `preflight_training_check()`, and `.gguf` rejection with HF base resolution via `model_browser`.

### 2.4 Secure Model File Browser (`shard_engine/model_browser.py`, `shard_engine/server.py`)
- **Causes & Fixes Found During Testing:**
  - `model_browser.classify_model_path()` called nonexistent `gguf_backend.extract_quant` -> fixed to `gguf_backend.parse_quant_from_name(p.name)` and attached `suggested_base` on invalid/untrained GGUFs as well.
  - Critical security bypass: `model_browser.list_directory()` previously fell back silently to `models_dir` before calling `validate_browse_path()` when an explicit non-existent/blocked path (e.g., `~/.ssh`, `\\server\share\models`) was requested -> fixed to validate any explicit `raw_path` BEFORE default fallback and raise `PermissionError` (HTTP `403`) on UNC paths and sensitive/system dirs (`C:\Windows`, `.ssh`, browser profiles, SHARD secrets).
  - Added `"soup"` -> `"trainable"` alias and `"dirs"` file filter in directory listings.
  - Dynamic Windows drive enumeration with volume labels and free GB (`list_roots()`), format detection (`gguf_inference`, `hf_model_dir`, `hf_hub_cache`, `safetensors_file`, `lora_adapter`), and owner-only `/api/models/browser/*` routes.

### 2.5 Dataset Preview Modal & Creation Stats (`shard_engine/feedback.py`, `dashboard.html`, `shard_engine/dashboard.html`)
- **Causes:**
  - Dashboard preview JS referenced typo DOM id `modalModelBody` (instead of `modelModalBody`) and dead field `d.sample_records`.
  - Dataset creation returned minimal counts without full structured validation/skip breakdowns.
- **Fix:**
  - Added `valid_records`, `rejected_records`, `source_feedback_count`, `dedup_removed`, `validation_status`, `creation_summary` in `feedback.build_dataset_snapshot()`, plus `_extract_structured_record()`, `preview_dataset_paginated()`, and `validate_dataset_snapshot()`.
  - Fixed `modelModalBody`, `<span id="modalHfLink">`, paginated `/api/training/datasets/{id}/preview?page=...` rendering with `valid`/`invalid` badges, and `trnDsBuildSummary` stats box.

### 2.6 Dashboard Synchronization & JS Syntax Corruption Fixed (`shard_engine/dashboard.html`, `dashboard.html`)
- **Critical Bug Caught During Final Audit:**
  - `shard_engine/dashboard.html` (which `server.py` serves at `GET /` and packages into wheels) had drifted out of sync from root `dashboard.html`, contained 3 blank lines, duplicated blocks of `lanRefresh`, `renderTrnDatasets`, and `trnBackupState`, lost 87 top-level function definitions (`75` vs `162` top-level defs), had 8 broken `onclick` handlers (`lanJoin`, `lanJoinQuick`, `lanOff`, `netRunDiag`, `netSaveServersConfig`, `srvCreateIssue`, `srvSendCommunityMsg`, `srvSendSharedChat`), and failed `node --check` (`SyntaxError: Unexpected token 'catch'` at `<script>` line 1033).
  - Root `dashboard.html` also had a legacy `onclick="switchTab('keys', ...)"` bug on line 2180 instead of `showTab('keys', ...)`.
- **Fix:**
  - Fixed `switchTab` -> `showTab` in `dashboard.html`, synced root `dashboard.html` cleanly to `shard_engine/dashboard.html` (300,374 bytes, byte-for-byte identical), and verified `node --check` exits `0`, zero blank lines, zero comments, zero emojis, and `missing_onclick=[]` in both files.

---

## 3. Files Modified & Created

### Modified Files (13)
1. `README.md` — PyPI install commands (`pip install shard-engine`, `pip install "shard-engine[full]"`).
2. `README.ru.md` — Russian PyPI install commands.
3. `README.kz.md` — Kazakh PyPI install commands.
4. `shard_engine/paths.py` — Added `tools_dir`, `soup_venv_dir`, `update_cache_file`, `legacy_update_cache_file` (173 lines, 0 blanks, 0 comments).
5. `shard_engine/settings.py` — Added `recent_model_paths` and `add_recent_model_path` (142 lines, 0 blanks, 0 comments).
