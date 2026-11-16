# SHARD 1.8.1 — Windows Subprocess Encoding & Update Checker Audit Report

## 1. Executive Summary

Two Windows 11 / Python 3.14 runtime issues encountered on `shard start` were investigated, reproduced, fixed, and verified:

1. **Subprocess `_readerthread` `UnicodeDecodeError` (`charmap` byte `0x98` at position `1237`)**:
   Caused by `shard_engine/soup_adapter.py:_run_soup_cmd()` launching `soup-cli` with `PYTHONIOENCODING=utf-8` while calling `subprocess.run(..., capture_output=True, text=True)` without an explicit `encoding`, defaulting Python 3.14 on Russian Windows to `cp1251` in `subprocess.py:_readerthread`. Rich Unicode box-drawing borders (`┘` = `0xe2 0x94 0x98`) emitted by `soup-cli train --config ... --dry-run --yes` placed byte `0x98` at exact byte index `1237`, crashing `_readerthread`.
2. **False `Latest version: 9.9.9 | Update available!` Banner**:
   Caused by `tests_cli_pkg.py:test_update_check_variants()` mocking PyPI `"9.9.9"` without isolating `SHARD_DATA_DIR`, writing `"latest_version": "9.9.9"` into the user's real `update_cache.json` files (`AppData\Local\SHARD\cache\update_cache.json` and `.shard\update_cache.json`), combined with `shard_engine/updater.py` lacking placeholder version rejection, trusting stale/poisoned cache values within the 4-hour TTL (`CACHE_TTL_SECONDS = 14400`), and probing `GITHUB_RAW_INIT` on `/main/` (404 against the `master` default branch).

---

## 2. Root Cause Analysis
