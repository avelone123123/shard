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
6. `shard_engine/soup_adapter.py` — 10-state SOUP lifecycle, Python `>=3.11,<3.14` discovery, isolated venv management, upstream version probe (`0.77.2`) (1089 lines, 0 blanks, 0 comments).
7. `shard_engine/feedback.py` — Dataset creation stats, paginated structured preview, schema validation (942 lines, 0 blanks, 0 comments).
8. `shard_engine/training.py` — `preflight_training_check`, `_LIFECYCLE_MAP`, uppercase `lifecycle_state`, GGUF base rejection + HF suggestion, training hyperparameters (`batch_size`, `gradient_accumulation_steps`, `max_length`) (1137 lines, 0 blanks, 0 comments).
9. `shard_engine/cli.py` — `-update`/`--update` normalization, `cmd_update`, startup update check banner in `cmd_start` (1994 lines, 0 blanks, 0 comments).
10. `shard_engine/server.py` — Owner-only `/api/system/update*`, `/api/models/browser/*`, `/api/training/datasets/{id}/preview`, `/api/training/datasets/{id}/validate`, `/api/training/preflight`, SOUP installer status & background checks (2385 lines, 0 blanks, 0 comments).
11. `dashboard.html` — Header `#shardVersionPill`, Update Center modal, Model File Browser modal, dataset summary + paginated preview, preflight/hyperparameters, bugfixes (`modelModalBody`, `modalHfLink`, `data.installer`, `showTab`) (4474 lines, 0 blanks, 0 comments, 0 emojis, `node --check` ok).
12. `shard_engine/dashboard.html` — Restored valid JS syntax and synced byte-for-byte with `dashboard.html` (300,374 bytes).
13. `tests_cli_pkg.py` — Updated `test_update_check_variants` error expectation (`GITHUB_UNAVAILABLE`) and removed trailing blank line (254 lines, 0 blanks, 0 comments).

### New Files (3 Code/Test Modules + This Report)
1. `shard_engine/updater.py` — GitHub release/tag checker, SHA-256 + Zip Slip verification, backup & rollback preserving user data (861 lines, 0 blanks, 0 comments).
2. `shard_engine/model_browser.py` — Cross-platform drive/shortcut listing, model format/compatibility classifier, security path guard (687 lines, 0 blanks, 0 comments).
3. `tests_phase_i_product_hardening.py` — 9 dedicated Phase I integration & security tests (283 lines, 0 blanks, 0 comments).

---

## 4. Direct Verification Results

### 4.1 Style, Syntax & Synchronization Checks (Post-Final-Edit)
- **AST & Tokenizer Audit across all 11 touched/new Python files:** `ast.parse` passed on all 11 files; `0` blank lines, `0` `#` comments, `0` emojis across every file.
- **HTML/CSS/JS Audit across `dashboard.html` and `shard_engine/dashboard.html`:** Byte-for-byte identical (`300,374` bytes), `node --check` exit code `0`, `0` blank lines, `0` HTML comments, `0` CSS comments, `0` emojis, `missing_onclick=[]`.
- **Dashboard HTTP Serve (`TestClient(app).get("/")`):** HTTP `200`, length `283971`, contains `#shardVersionPill` and `#browserModalBackdrop`.

### 4.2 Full Unittest Suite (Post-Final-Edit)
Command executed:
```powershell
python -m unittest discover -s . -p "tests_*.py" -v
```
Result:
- **`Ran 171 tests in 39.616s` -> `OK`** (0 failures, 0 errors, 0 skipped), including all 9 tests in `tests_phase_i_product_hardening.py`:
  - `test_01_updater_version_comparison_and_security`
  - `test_02_updater_zip_slip_and_sha256_verification`
  - `test_03_updater_check_states_and_rollback_preserves_user_data`
  - `test_04_updater_api_endpoints_and_cli_dash_update_normalization`
  - `test_05_soup_lifecycle_and_python_compat`
  - `test_06_model_browser_formats_suggestions_and_security`
  - `test_07_dataset_creation_metrics_pagination_and_validation`
  - `test_08_training_preflight_blocks_gguf_and_runs_lifecycle`
  - `test_09_dashboard_regression_bugs_fixed`

### 4.3 Non-Mocked Live Windows Verification (Observed Directly)
- **CLI Commands:**
  - `python -m shard_engine.cli --version` -> `shard-engine 1.8.0` (Python `3.14.4`, Windows 11 AMD64).
  - `python -m shard_engine.cli -update --check --json` -> exit `0`, live GitHub check returned `"state": "UP_TO_DATE"`, `"current_version": "1.8.0"`, `"latest_version": "1.8.0"`, `"tag_name": "v1.8.0"`, `"source": "github_release"`, `"install.method": "git_clone"`.
  - `python -m shard_engine.cli doctor` -> `Doctor summary: 0 error(s), 0 warning(s).` (`tools_dir`, `soup_venv_dir`, `llama-server.exe`, `ggml-rpc-server.exe`, `RTX 2050 4096 MB`, SIMD native core all `[PASS]`).
  - `python -m shard_engine.cli training soup` -> cleanly reported `Available: NO`, `Python Runtime: 3.14.4 (SOUP compatible <3.14: NO)`, `SOUP Engine` incompatible warning + install hint.
- **Host Python / SOUP Environment Discovery:**
  - `py -0p` / `soup_adapter.find_compatible_python()`: only `Python 3.14.4` installed on host (`found: False`, `uv_available: False`).
  - `soup_adapter.detect_soup(probe_deps=True, check_remote=True)`: returned `available=False`, `lifecycle_state="NOT_INSTALLED"`, live upstream `latest_version="0.77.2"`, `python_compatible=False`, missing deps `["peft", "trl", "datasets"]`.
- **Live Non-Mocked API & Filesystem Checks (isolated temp `SHARD_*_DIR` + real host drives):**
  - `GET /api/system/update?force=1` (with `X-Shard-Owner-Token`): HTTP `200`, `UP_TO_DATE`, `1.8.0`.
  - `GET /api/models/browser/roots`: HTTP `200`, 2 real drives (`C:\` 78.8 GB free, `D:\` labeled `Ventoy` 905.0 GB free) and 5 shortcuts (`D:\shard-models`, `~\.cache\huggingface\hub`, etc.).
  - Real `model_browser.list_directory()` on `~\.cache\huggingface\hub` (8 items, classified `hf_hub_cache` incl. `models--Qwen--Qwen2.5-Coder-1.5B-Instruct-GGUF`) and `D:\shard-models\hub` (`models--Qwen--Qwen2.5-Coder-1.5B-Instruct`, `models--Qwen--Qwen2.5-Coder-1.5B-Instruct-GGUF` valid=`True`, `3B`, `7B`).
  - Sensitive/UNC path blocking (`PermissionError` -> HTTP `403`):
    - `C:\Windows\System32` -> `403 Access to system or sensitive credential directory is blocked.`
    - `\\server\share\models` -> `403 Network UNC paths are not permitted.`
    - `C:\Users\avelo\.ssh` -> `403 Access to system or sensitive credential directory is blocked.`
  - Format inspection (`POST /api/models/browser/inspect`):
    - `.gguf` file -> `format="gguf_inference"`, `quant="Q4_K_M"`, `gguf_inference.supported=True`, `soup_training.supported=False`, `suggested_base_model="Qwen/qwen-test"`.
    - Local HF dir (`config.json` + `tokenizer_config.json` + `model.safetensors`) -> `format="hf_model_dir"`, `soup_training.supported=True`.
  - Feedback -> Approve -> Dataset Build -> Paginated Preview -> Validate -> Preflight:
    - Submitted 5 entries (`POST /api/feedback/submit`), approved via `POST /api/training/feedback/{id}/review`, built snapshot (`POST /api/training/datasets/build`) -> HTTP `200`, `valid_records=5`, `rejected_records=0`, `source_feedback_count=5`, `validation_status="schema_valid"`.
    - Paginated preview `page=1&page_size=3` & `page=2&page_size=3` -> HTTP `200`, `total_pages=2`, `records_len=2` on page 2.
    - Validation `POST /api/training/datasets/{id}/validate` -> HTTP `200`, `ok=True`, `valid_records=5`, `invalid_records=0`.
