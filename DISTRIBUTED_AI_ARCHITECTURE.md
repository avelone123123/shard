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

SHARD explicitly distinguishes **Model Parallelism** (partitioning one model across machines) from **Data Parallelism** (replicating whole models) and supports two real model-parallel execution engines:

### 4.1 Strategy A: GGUF Distributed Layer/Tensor Splitting (`gguf_rpc_layer_split`)
- **Backend**: `llama.cpp` (`llama-server` coordinator + `ggml-rpc-server` workers).
- **Mechanism**:
  - Coordinator inspects local offload devices (`llama-server --list-devices`) and active RPC workers.
  - Planner computes per-device usable memory (respecting worker quotas + `VRAM_RESERVE_MB` / `RAM_RESERVE_MB` + KV cache + compute buffer overhead) and network cost.
  - Coordinator launches `llama-server` with `--rpc <endpoints>`, `-sm layer` (pipeline layer split across RPC devices & local GPUs), exact `--tensor-split` matching the true device order `[RPC0..RPCk, local_GPUs...]`, `--parallel <slots>`, `--cont-batching`, and `--slots`.
  - KV cache for each layer lives directly on the device holding that layer.
- **Supported Models**: Any GGUF model supported by `llama.cpp` (Llama, Qwen2/2.5/3, DeepSeek-R1/V2/V3, Mistral, Gemma, Phi, GPT-2, etc.).
- **Constraints**: Requires `llama-server` and `ggml-rpc-server` binaries; high-latency WAN links degrade token latency proportionally to layer-crossing hops.

### 4.2 Strategy B: PyTorch Pipeline Parallelism with Distributed Loading (`torch_pipeline_parallel`)
- **Backend**: `shard_engine/distributed/pipeline_coordinator.py` + `pipeline_worker.py` + `pipeline_proto.py`.
- **Mechanism**:
  - **Architecture Support Check**: Explicitly validates `config.json` `model_type` against verified stage-partitionable architectures (`llama`, `qwen2`, `qwen3`, `mistral`, `gemma`, `gemma2`, `phi3`, `gpt2`, `deepseek_v2`, `deepseek_v3`). Unsupported architectures fail loudly with a structured error rather than faking partitioning.
  - **Distributed Loading**: Using `model.safetensors.index.json` (or single `model.safetensors`), the planner resolves the exact tensor keys and shard filenames needed for each stage (`embed_tokens` + `layers[s:e]` + optional `norm` & `lm_head`). Each worker loads **only** its assigned tensors onto its configured device/dtype using `safetensors.safe_open(..., framework="pt")` — the coordinator never loads full-model weights into its own RAM/VRAM.
  - **Distributed KV Cache**: Each worker maintains an independent `DynamicCache` / layer-indexed KV store keyed by `request_id` covering only its local layers `[start_layer..end_layer]`, freeing cache immediately on completion, cancellation, or timeout.
  - **Binary Tensor Wire Protocol (`SHPT`)**: Activations (`hidden_states`, `position_ids` / `cache_position`) are transmitted over persistent authenticated TCP sockets as compact binary frames:
    - Header: `4B magic ("SHPT") | 1B version | 1B msg_type | 2B flags | 16B request_id | 4B seq_pos | 1B dtype | 1B ndim | 2B reserved | 4B payload_len` + `ndim * 4B shape` + raw contiguous tensor buffer (`float16`/`bfloat16`/`float32`).
  - **Autoregressive Execution**: Prefill passes the prompt tensor `[1, prompt_len, hidden]` sequentially through Stages `0 -> 1 -> ... -> K`; Stage `K` computes `norm` + `lm_head` logits and returns the sampled `next_token_id` to Stage `0` for decode steps `1..max_tokens` (micro-step latency and transfer bytes recorded per hop).
- **Constraints**: Current implementation targets single-batch/serialized-per-worker pipeline execution per request slot (concurrent requests queue cleanly via `RequestScheduler` or pipeline micro-stages); tensor parallelism (splitting individual attention/MLP matrices within a single layer across machines) is intentionally deferred on commodity internet/LAN links because AllReduce synchronization per transformer layer requires two network barriers per layer (64–160 barriers/token), which is prohibitive over Ethernet/VPN compared to pipeline parallelism (1 hop per stage boundary).

---

## 5. Partition Planner & Resource Economics (`shard_engine/distributed/planner.py`)

Given model metadata (`config.json` or GGUF probe) and cluster node inventories:

1. **Usable Resource Calculation**:
   - Raw VRAM/RAM is never summed naively.
   - Each node reports raw `vram_total_mb`, `vram_free_mb`, `ram_total_mb`, `ram_avail_mb`, `cpu_cores`, `device_type` (`cuda`/`mps`/`cpu`), and user `quota`.
   - `usable_vram_mb = max(0, min(vram_free_mb - reserve, quota_vram_mb))`
   - `usable_ram_mb = max(0, min(ram_avail_mb - reserve, quota_ram_mb))`
   - Memory tiers carry distinct speed weights (`GPU VRAM` >> `Unified/MPS` > `System RAM` >> `Remote WAN`).
2. **Memory Overhead Accounting**:
   - Model weights per layer: `(total_weight_bytes - embed_bytes - head_bytes) / num_layers`
   - Distributed KV cache per layer: `2 * num_kv_heads * head_dim * ctx_tokens * parallel_slots * dtype_bytes`
   - Runtime compute/scratch buffer per stage: `max(128 MB, 0.08 * stage_weight_mb)`
3. **Communication-Aware Placement**:
   - Estimates per-token activation size: `hidden_size * dtype_bytes` (e.g., 4096 * 2B = 8 KB/token in FP16 for 7B/8B; 16 KB/token for 70B).
   - Estimates per-token transfer overhead across `S` stages: `(S - 1) * (2 * rtt_ms + (act_bytes / link_bps) * 1000)`.
   - Compares single-node fit (if coordinator can hold the model alone in VRAM) vs multi-node split, warning when high WAN RTT (`> 30 ms`) makes splitting slower than single-GPU execution, while maximizing fit when the model exceeds any single machine's memory.

---

## 6. Networking, NAT Traversal & Security

### 6.1 Supported Topologies
1. **Local Area Network (LAN)**: UDP beacon discovery (`8001`) + direct TCP.
