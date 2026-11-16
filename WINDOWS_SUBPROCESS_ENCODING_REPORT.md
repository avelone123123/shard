# SHARD 1.8.1 — Windows Subprocess Encoding & Update Checker Audit Report

## 1. Executive Summary

Two Windows 11 / Python 3.14 runtime issues encountered on `shard start` were investigated, reproduced, fixed, and verified:

1. **Subprocess `_readerthread` `UnicodeDecodeError` (`charmap` byte `0x98` at position `1237`)**:
   Caused by `shard_engine/soup_adapter.py:_run_soup_cmd()` launching `soup-cli` with `PYTHONIOENCODING=utf-8` while calling `subprocess.run(..., capture_output=True, text=True)` without an explicit `encoding`, defaulting Python 3.14 on Russian Windows to `cp1251` in `subprocess.py:_readerthread`. Rich Unicode box-drawing borders (`┘` = `0xe2 0x94 0x98`) emitted by `soup-cli train --config ... --dry-run --yes` placed byte `0x98` at exact byte index `1237`, crashing `_readerthread`.
2. **False `Latest version: 9.9.9 | Update available!` Banner**:
   Caused by `tests_cli_pkg.py:test_update_check_variants()` mocking PyPI `"9.9.9"` without isolating `SHARD_DATA_DIR`, writing `"latest_version": "9.9.9"` into the user's real `update_cache.json` files (`AppData\Local\SHARD\cache\update_cache.json` and `.shard\update_cache.json`), combined with `shard_engine/updater.py` lacking placeholder version rejection, trusting stale/poisoned cache values within the 4-hour TTL (`CACHE_TTL_SECONDS = 14400`), and probing `GITHUB_RAW_INIT` on `/main/` (404 against the `master` default branch).

---

## 2. Root Cause Analysis

### 2.1 Subprocess `UnicodeDecodeError` (`byte 0x98 in position 1237`)

- **Triggering execution path**: `shard_engine/training.py:start_training_job()` -> `shard_engine/soup_adapter.py:dry_run_train()` -> `shard_engine/soup_adapter.py:_run_soup_cmd()` executing `soup-cli train --config <soup.yaml> --dry-run --yes` (triggered across two training runs `job-20261116-005404-9e091d` and `job-20261116-005732-eb82d3`).
- **Mechanism**:
  - `_run_soup_cmd()` set `PYTHONIOENCODING=utf-8` in the child environment so `soup-cli` (Rich CLI output) emitted UTF-8 box-drawing characters (`┌── Training Setup ──┐ ... └──┘`).
  - The parent called `subprocess.run(..., capture_output=True, text=True)` without specifying `encoding`. On Windows with `cp1251` active code page, Python's `subprocess` background `_readerthread` opened pipes using `encodings\cp1251.py`.
  - Byte `0x98` is undefined in `cp1251` (`0xe2 0x94 0x98` is the UTF-8 encoding of `┘`), occurring at byte offset `1237` of the child output and raising an uncaught `UnicodeDecodeError` inside `_readerthread`, leaving `stdout` as `None`.
- **Repo-wide subprocess audit**: All `subprocess.run`, `subprocess.Popen`, `text=True`, `capture_output=True`, and `.decode(...)` call sites in `shard_engine/` were audited and migrated to centralized binary-safe helpers in `shard_engine/proc_io.py`.

### 2.2 False `Latest version: 9.9.9 | Update available!`

- **Poisoned on-disk cache**: Running `tests_cli_pkg.py:test_update_check_variants()` previously wrote `"latest_version": "9.9.9"` directly into the active user's `update_cache.json` files (`C:\Users\avelo\AppData\Local\SHARD\cache\update_cache.json` and `C:\Users\avelo\.shard\update_cache.json`) because `SHARD_DATA_DIR` was not isolated to a temporary directory.
- **Insufficient validation and fallback in `shard_engine/updater.py`**:
  - Placeholder/sentinel versions such as `9.9.9` were accepted both from remote JSON and from local cache.
  - `GITHUB_RAW_INIT` targeted `/main/` instead of `master` (returning HTTP 404).
  - Unverified/failed remote checks could surface stale/cached `latest_version` values instead of reporting `unknown`.
- **Verified upstream latest release**: GitHub Releases (`v1.8.1`, `prerelease: False`) and PyPI (`1.8.1`).

---

## 3. Changes Implemented

| File | Summary of Changes |
| :--- | :--- |
| `shard_engine/proc_io.py` | New centralized subprocess binary I/O module: `safe_decode_subprocess_output()`, `run_subprocess_safe()`, `stream_subprocess_safe()`, and `terminate_process_tree()`. Reads raw bytes (`text=False`), preserves separate stdout/stderr streams cleanly without `_readerthread` text-mode crashes or dual-pipe deadlocks, decodes per-line/segment cleanly (UTF-8 / Windows `cp1251` / optional `cp866` OEM / `errors="replace"` fallback while avoiding CP1251 mojibake on broken UTF-8 lines with stray `0x98`), supports streaming cancellation and process-tree termination on Windows (`taskkill /F /T /PID`). |
| `shard_engine/soup_adapter.py` | Replaced all 10 `subprocess.run(..., text=True)` / `Popen(text=True)` / `taskkill` calls with `proc_io.run_subprocess_safe`, `proc_io.stream_subprocess_safe` (inside `_run_install_worker`), and `proc_io.terminate_process_tree`. Updated `launch_train_popen` to binary `"ab"` log handles with `PYTHONIOENCODING=utf-8` and `PYTHONUTF8=1`. |
| `shard_engine/updater.py` | Added strict SemVer validation (`_SEMVER_RE`), `is_placeholder_version()`, `is_valid_release_version()` (rejecting `9.9.9`, `0.0.0`, `99.99.99`, `999.999.999`, `123.456.789`, etc.), `_safe_resp_json()`, `GITHUB_REPO` cache validation in `_read_cache()` (auto-ignoring poisoned `9.9.9` caches), removed unreliable `/main/` `GITHUB_RAW_INIT` scraping, returns `latest_version=None` / `update_available=False` / `Latest version: unknown (update check unavailable)` when verification fails, and migrated all 9 `git`/`pip`/verification subprocess calls to `proc_io.run_subprocess_safe`. |
| `shard_engine/cli.py` | Updated `cmd_update` to verify `is_valid_release_version(lat_ver)` before displaying update-available status. |
| `shard_engine/hardware.py` | Migrated `nvidia-smi` invocation to `proc_io.run_subprocess_safe`. |
| `shard_engine/lan.py` | Migrated `tailscale status --json` to `proc_io.run_subprocess_safe` and `ggml-rpc-server` stderr decoding to `proc_io.safe_decode_subprocess_output`. |
| `shard_engine/gguf_backend.py` | Migrated `llama-server` stderr byte decoding to `proc_io.safe_decode_subprocess_output`. |
| `shard_engine/net_platform_win.py` | Migrated `_run_powershell`, `sc.exe`, and `tasklist.exe` subprocess calls to `proc_io.run_subprocess_safe` (using `prefer_utf8=False, try_oem=True` for `sc.exe` and `tasklist.exe`). |
| `shard_engine/net_platform_linux.py` | Migrated all 6 Linux subprocess calls (`ip`, `wg`, `ufw`, `nft`, `systemctl`) to `proc_io.run_subprocess_safe`. |
| `shard_engine/net_wireguard.py` | Updated `_run_readonly_cmd` to use `proc_io.run_subprocess_safe(..., prefer_utf8=True, try_oem=_WIN)`. |
| `tests_cli_pkg.py` | Isolated `test_update_check_variants()` inside `tempfile.TemporaryDirectory()` (`SHARD_DATA_DIR`) to prevent polluting real user caches; uses `2.0.0` for upgrade test and asserts `9.9.9` yields `unknown` without recommending an update. |
| `tests_phase_i_product_hardening.py` | Updated update test fixture from `"9.9.9"` to `"2.0.0"`. |
| `tests_windows_subprocess_and_updater.py` | Added 10 regression tests covering `0x98` at offset `1237` reproduction and safe decode, mixed UTF-8/Cyrillic/emoji/box-drawing + standalone `0x98`, large dual-pipe non-deadlocking reads, mixed stdout/stderr + non-zero exit, streaming cancellation under invalid bytes, SOUP dry-run `0x98` safety, SemVer/placeholder rejection, `9.9.9` API/cache rejection, missing/rate-limited/malformed release responses, and `v`-prefixed release tags. |

---

## 4. Verification Results (Local Windows 11, Python 3.14)

All local checks below ran **after the final code edits**:

1. **AST, Comment, Empty-Line, and Subprocess Audit (`python scratch/audit_style.py`)**:
   - All 13 modified/created Python files (`proc_io.py`, `soup_adapter.py`, `updater.py`, `cli.py`, `hardware.py`, `lan.py`, `gguf_backend.py`, `net_platform_win.py`, `net_platform_linux.py`, `net_wireguard.py`, `tests_cli_pkg.py`, `tests_phase_i_product_hardening.py`, `tests_windows_subprocess_and_updater.py`) parsed with `ast=OK`, `comments=0`, and `empty=[]`.
   - Zero `text=True` or `universal_newlines=True` subprocess calls remain in `shard_engine/`.
2. **Real Live Windows Execution (`shard start` + API + SOUP dry-run + streaming)**:
   - `shard start --host 127.0.0.1 --port 18888 --no-browser` startup banner printed `SHARD 1.8.1 (up to date)` and started cleanly (`Has UnicodeDecodeError: False`, `Has _readerthread: False`, `Has 9.9.9: False`).
   - HTTP `200` on `/api/system/hardware`, `/api/system/update` (`UP_TO_DATE 1.8.1 github_release`), `/api/lan/status`, `/api/lan/diagnose`, `/api/net/status`, `/api/training/soup`, `/api/training/datasets`.
   - Real `soup_adapter.dry_run_train()` on local `job-20261116-005732-eb82d3\soup.yaml`: `ok=True`, `returncode=0`, `stdout len=1027`, contains Unicode box corner `┘`, zero exceptions.
   - Real `proc_io.stream_subprocess_safe()` streaming mixed ASCII, UTF-8 Cyrillic/box-drawing/emoji, CP1251 Cyrillic, and raw `0x98`: `returncode=0`, `cancelled=False`, `lines=4`, decoded as `['line1 ascii', 'Привет └─┘ 🚀', 'Ошибка', 'bad:\ufffdend']`.
   - Live `shard update --check`: `Installed version: shard-engine 1.8.1`, `Update status: UP_TO_DATE`, `Latest version: 1.8.1 (source: github_release)`.
3. **Automated Test Suites**:
   - `python -m unittest tests_windows_subprocess_and_updater.py -v`: **Ran 10 tests in 0.956s — OK**.
   - `python -m unittest tests_cli_pkg.py tests_training_ecosystem.py tests_network_persistence_security.py tests_shard_network.py tests_phase4_network_integration.py tests_phase_iii_unified_hosting.py tests_phase_iii_chat_security.py -v`: **Ran 67 tests in 28.316s — OK**.
   - `python -m unittest discover -s . -p "tests_*.py" -v`: **Ran 200 tests in 46.185s — FAILED (failures=1)**. The single failure is pre-existing `tests_phase_i_product_hardening.py:test_06_model_browser_formats_suggestions_and_security` (`line 171`), caused solely by a real local model directory already present on this host (`D:\shard-models\hub\models--Qwen--Qwen2.5-Coder-1.5B-Instruct\snapshots\2e1fd397ee46e1388853d2af2c993145b0f1098a`).

---

## 5. GitHub Actions CI Matrix Verification (PR #3)

