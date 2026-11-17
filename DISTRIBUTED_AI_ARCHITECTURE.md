# SHARD Distributed AI Computing Platform — Technical Architecture

## 1. Executive Summary

This document defines the technical architecture for transforming **SHARD** (`shard-engine`) from a single-node local inference server with experimental LAN RPC helpers into a real, production-oriented **distributed AI computing platform**.

In this architecture, independent computers across local networks, mesh VPNs (Radmin VPN, Tailscale, WireGuard, ZeroTier), or NAT-traversed encrypted tunnels explicitly contribute bounded hardware resources (GPU VRAM, system RAM, CPU threads) to collaboratively load and execute **one large AI model** behind a **unified OpenAI- and Anthropic-compatible API endpoint**.

---

## 2. Repository Audit & Architectural Blockers Identified

Inspection of the existing codebase (`shard_engine/`), runtime binaries (`llama-server.exe` / `ggml-rpc-server.exe` build `b12110`), and tests (`190` baseline tests) identified the following critical blockers:

### 2.1 Incomplete Hardware & Capability Discovery (`shard_engine/hardware.py`)
- Only probes GPU 0 via `nvidia-smi`; ignores multi-GPU rigs, CPU model strings, OS/arch metadata, live CPU/GPU utilization, and supported inference runtimes (`gguf_rpc`, `llama_server`, `torch_pipeline`).
- Workers do not report network link metrics (RTT, measured bandwidth) or enforced contribution quotas.

### 2.2 Disconnected Compute Quotas & Unauthenticated RPC Exposure (`shard_engine/lan.py`)
- `LanManager.set_compute_consent()` persists `compute_quota` (`max_vram_gb`, `max_threads`), but `_start_worker()` launches `ggml-rpc-server` without `-t max_threads`, without device filtering, and without propagating quota limits to the coordinator's `_pool_snapshot()`.
