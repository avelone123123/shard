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
