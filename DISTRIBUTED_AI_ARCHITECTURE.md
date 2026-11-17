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
- `ggml-rpc-server` speaks raw unauthenticated TCP (`transport: TCP`). Binding directly to `0.0.0.0:50052` on untrusted networks exposes raw memory/compute allocation to any peer.
- Empirical test with `llama-server.exe --list-devices` and `--rpc` (`b12110`) proved a critical tensor-split bug in `lan.py`: on CPU-only `llama-server` builds, `device_info` lists **only** `RPC0, RPC1, ...` (no local CPU offload device), whereas `lan.py`'s `rpc_targets()` always emitted `N + 1` weights with the coordinator first, shifting every worker's weight by one slot.

### 2.3 Missing Cluster Rebalancing & Weak-Coordinator Support (`shard_engine/server.py`, `shard_engine/lan.py`)
- `lan.py` declares `REBALANCE_INTERVAL_S = 2.0` and `_rebal_note`, but no rebalancing loop exists. Workers joining after model load or disconnecting mid-run never trigger partition replanning or recovery.
- `_verify_server_model_ready()` rejects server creation (`HTTP 409 MODEL_NOT_READY`) unless the full model is already running on the coordinator alone, preventing a weak coordinator (e.g., 4 GB VRAM / 16 GB RAM) from creating a cluster for a larger model and waiting for workers to pool memory.

### 2.4 Reverse NAT Tunneling Gap for Remote Workers (`shard_engine/net_tunnel.py`)
- `HostTunnelAgent._handle_rpc_open()` currently proxies TCP streams from a connected **client** to `127.0.0.1:port` on the **host**.
- For distributed inference across NAT, the direction is inverted: the **coordinator** (`llama-server` or `PipelineCoordinator`) must connect to the **worker's** local runtime (`127.0.0.1:rpc_port` or `127.0.0.1:pipeline_port`) through the authenticated ChaCha20-Poly1305 tunnel without the worker exposing public ports.

### 2.5 Single-Slot Serial Inference & Lack of PyTorch Distributed Execution (`shard_engine/gguf_backend.py`, `shard_engine/engine.py`)
- `gguf_backend.py` hardcodes `--parallel 1`.
- `server.py` invokes blocking synchronous generators inside `async def` FastAPI handlers with a single global `_cancel_gen` flag, zero request queueing, no per-client fairness/rate limiting, and no TTFT or inter-worker transfer metrics.
- No distributed HuggingFace/PyTorch execution engine exists for safetensors models where each worker loads only its assigned layer shards directly (distributed loading) and maintains its own per-layer KV cache.

---

## 3. Target Role Architecture

```
+-----------------------------------------------------------------------------+
|                           CLIENTS / AI APPLICATIONS                         |
|  Cursor | Claude Code | OpenCode | Web Chat | Custom OpenAI/Anthropic SDK   |
+--------------------------------------+--------------------------------------+
                                       | HTTP/SSE (/v1/chat/completions,
                                       |           /v1/messages, /v1/models)
+--------------------------------------v--------------------------------------+
|                        CLUSTER COORDINATOR (Owner)                          |
|  +------------------+  +-------------------+  +--------------------------+  |
|  | Cluster Lifecycle|  | Partition Planner |  | Multi-User Scheduler     |  |
|  | (cluster.py)     |  | (planner.py)      |  | (scheduler.py)           |  |
|  +--------+---------+  +---------+---------+  +------------+-------------+  |
|           |                      |                         |                |
|  +--------v----------------------v-------------------------v-------------+  |
|  | Unified Distributed Execution Router                                  |  |
|  |  - Path A: GGUF Distributed RPC (llama-server --rpc + -sm layer -ts)  |  |
|  |  - Path B: PyTorch Pipeline Parallelism (safetensors layer sharding)  |  |
|  +--------+----------------------------------------------+---------------+  |
+-----------|----------------------------------------------|------------------+
            | Authenticated LAN / VPN / Encrypted Tunnel   |
     +------v-------------------+               +----------v--------------+
     | WORKER NODE 1 (Consented)|               | WORKER NODE N (Consented)|
     | Quota: 70% VRAM, 4 Thr   |               | Quota: 8 GB RAM, 6 Thr  |
     | - ggml-rpc-server        |   Tensor      | - ggml-rpc-server       |
     | - PipelineWorkerServer   | ------------> | - PipelineWorkerServer  |
     |   (Layers 0..15 + KV)    |  Activations  |   (Layers 16..31 + KV)  |
     +--------------------------+               +-------------------------+
```

### 3.1 Coordinator (AI Server Owner)
- Creates cluster targeting a specific model (`target_model`), backend strategy (`auto`, `gguf_rpc`, `torch_pipeline`), and context/batch parameters.
- Does **not** have to hold the entire model in local memory; can operate as a lightweight coordinator (0 offload layers or minimal embedding/head stages) while workers contribute compute.
- Publishes server metadata (model, requirements, worker count, aggregate capacity, access policy) to LAN UDP (`8001`) and optional Global Discovery.
- Runs continuous health checks, RTT/bandwidth probes, request queueing, and automatic rebalance/degraded handling.

### 3.2 Worker Nodes (Explicitly Consented Compute Contributors)
- Discover or inspect a server (`preview`), review model requirements and owner identity, and explicitly opt in (`Join as Worker` / `consent=True`).
- Enforce local resource limits (`vram_pct`, `max_vram_mb`, `ram_pct`, `max_ram_mb`, `max_threads`) before admitting workload.
- Run authenticated local execution workers (`ggml-rpc-server` for GGUF RPC and/or `PipelineWorkerServer` for PyTorch pipeline stages) bound safely to loopback (when tunneled) or verified mesh/LAN interface.
- Can safely leave or revoke consent at any time (`leave` / `opt_out`), immediately stopping worker processes and freeing VRAM/RAM.

### 3.3 Client / AI Consumer
- Connects via API key, LAN token, session token, or encrypted tunnel.
- Interacts strictly with `/v1/chat/completions`, `/v1/messages`, `/v1/models`, and isolated private chats (`/api/server/chats`) without knowing which worker executes which layers.

---

## 4. Genuine Distributed Model Execution Strategies

