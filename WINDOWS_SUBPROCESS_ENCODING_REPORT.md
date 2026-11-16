# SHARD 1.8.1 — Windows Subprocess Encoding & Update Checker Audit Report

## 1. Executive Summary

Two Windows 11 / Python 3.14 runtime issues encountered on `shard start` were investigated, reproduced, fixed, and verified:

1. **Subprocess `_readerthread` `UnicodeDecodeError` (`charmap` byte `0x98` at position `1237`)**:
