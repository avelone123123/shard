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

