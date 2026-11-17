# SHARD Distributed AI Implementation Report

## 1. Summary of Changes

This implementation advances SHARD on branch `feat/distributed-ai-platform` from single-node local inference with basic LAN RPC helpers into a unified distributed AI computing platform supporting:

1. **Cluster Coordinator & Worker Lifecycle** (`shard_engine/distributed/cluster.py`)
2. **Rich Hardware & Capability Discovery with Resource Quotas** (`shard_engine/hardware.py`, `shard_engine/distributed/capabilities.py`)
3. **Communication- & Memory-Aware Partition Planner** (`shard_engine/distributed/planner.py`)
4. **PyTorch Distributed Pipeline-Parallel Engine with Distributed Loading & Per-Stage KV Cache** (`shard_engine/distributed/pipeline_proto.py`, `shard_engine/distributed/pipeline_worker.py`, `shard_engine/distributed/pipeline_coordinator.py`)
5. **GGUF Distributed RPC Backend Enhancements & Device-Matched Tensor Split** (`shard_engine/gguf_backend.py`, `shard_engine/lan.py`)
6. **Reverse Encrypted Worker Tunnel for NAT Traversal** (`shard_engine/net_identity.py`, `shard_engine/net_tunnel.py`, `shard_engine/net_strategy.py`)
7. **Asynchronous Multi-User Request Scheduler, Queue & Rate Limiter** (`shard_engine/distributed/scheduler.py`)
8. **Unified API, CLI & Modern Slate Dashboard Integration** (`shard_engine/server.py`, `shard_engine/cli.py`, `shard_engine/dashboard.html`)

---

## 2. Implemented Components & Modified Files

### 2.1 New Package: `shard_engine/distributed/`
- `shard_engine/distributed/capabilities.py`:
  - `detect_llama_offload_devices()` probes `llama-server --list-devices` so offload device count is never miscalculated on CPU-only `llama.cpp` builds.
  - `probe_node_capabilities()` gathers GPU list, CPU model/cores/utilization, RAM, disk, supported backends (`gguf_rpc`, `llama_server`, `torch_pipeline`, `cuda`, `mps`), and non-virtual network interfaces.
  - `measure_tcp_link()` & `measure_bandwidth_mbps()` perform real TCP RTT and payload throughput measurements.
  - `effective_worker_budget()` applies system reserves (`VRAM_RESERVE_MB`, `RAM_RESERVE_MB`), user quotas, and RTT penalties.
- `shard_engine/distributed/planner.py`:
  - `inspect_model_architecture()` resolves `config.json`, `model.safetensors.index.json` / `model.safetensors`, GGUF files, exact layer counts, KV bytes per token per layer, and explicit `torch_pipeline_parallel` support status (`llama`, `qwen2`, `qwen3`, `mistral`, `gemma`, `gemma2`, `phi3`, `gpt2`).
  - `plan_cluster_partition()` computes per-stage weight MB, KV cache MB (scaled by `ctx_tokens` and `parallel_slots`), scratch buffer overhead, exact `shard_files` per worker, `rpc_arg`, `tensor_split_arg`, estimated inter-worker transfer latency (`estimated_comm_overhead_ms_per_tok`), and `prefer_single_node` warnings.
- `shard_engine/distributed/pipeline_proto.py`:
  - Binary TCP framing protocol (`SHPT` v1) with constant-time token auth handshake (`MSG_AUTH` / `MSG_AUTH_OK` / `MSG_AUTH_ERR`), zero-pickle tensor serialization supporting `float32`, `float16`, `bfloat16` (bit-exact via `uint16` view), and `int64`, plus `MSG_LOAD_STAGE`, `MSG_FORWARD`, `MSG_FORWARD_RES`, `MSG_RELEASE_KV`, `MSG_PING`, `MSG_BW_PROBE`.
- `shard_engine/distributed/pipeline_worker.py`:
  - `PipelineStageModule` builds only assigned transformer layers (`layer_start..layer_end`), optional embeddings (`has_embed`), optional norm + `lm_head` (`has_lm_head` with tied-embedding fallback), remaps `layer_idx` to `0..(local_count - 1)` for per-stage `DynamicCache`, and loads only matching keys from assigned safetensors/bin shards.
  - `PipelineWorkerServer` authenticates coordinator connections, enforces `max_threads` and VRAM caps, manages per-request KV caches, measures real stage compute time (`stage_compute_ms`), and cleans up KV caches on `MSG_RELEASE_KV` or `MSG_UNLOAD`.
- `shard_engine/distributed/pipeline_coordinator.py`:
  - `PipelineCoordinator` loads only tokenizer & config on the coordinator (no full model weights required on coordinator), orchestrates parallel stage loading across workers, executes prefill + autoregressive token decoding across stages, and tracks `ttft_ms`, `tokens_per_sec`, `compute_ms`, `inter_worker_transfer_ms`, and `transferred_bytes`.
- `shard_engine/distributed/scheduler.py`:
  - `RequestScheduler` (`SCHEDULER`) provides bounded concurrent execution slots (`max_concurrent`), bounded queue (`max_queue_size`), per-client sliding-window RPM rate limiting (`RateLimitError`), request timeouts, client-disconnect cancellation, non-blocking async streaming bridge (`stream_async` / `run_sync_async`), and real telemetry (`avg_ttft_ms`, `avg_tokens_per_sec`, `avg_inter_worker_transfer_ms`, error log).
- `shard_engine/distributed/cluster.py`:
  - `ClusterManager` (`CLUSTER`) implements the `IDLE -> FORMING -> LOADING -> VERIFYING -> READY / DEGRADED` state machine, explicit-consent worker enrollment (`consented=True` required), cryptographic worker tokens (`sk-shard-wrk-...`) with TTL & revocation, heartbeat monitoring + automatic rebalance/degraded handling on worker loss, local worker runtime (`ggml-rpc-server` + `PipelineWorkerServer`), and unified status/preview/distribution snapshots.

### 2.2 Core Updates
- `shard_engine/hardware.py`: Added multi-GPU `nvidia-smi` probing, CPU model & utilization, OS/arch metadata, backend probing, and `effective_usable_resources()`.
- `shard_engine/gguf_backend.py`: Added `parallel_slots`, `--cont-batching`, `--slots`, `-sm layer`, `slots_status()`, and clean stderr pipe closure on `stop()`.
- `shard_engine/lan.py`: Fixed `worker_cmd()` to enforce `-t <max_threads>` and optional `-c` cache; fixed `rpc_targets()` to emit `tensor-split` weights matching actual `llama-server` offload devices (`RPC0..RPCk` + local offload devices) rather than blindly `N + 1`.
- `shard_engine/net_identity.py`, `shard_engine/net_tunnel.py`, `shard_engine/net_strategy.py`: Added reverse worker tunnel frames (`MSG_WRK_OPEN`, `MSG_WRK_DATA`, `MSG_WRK_CLOSE`), `ClientTunnelAgent.worker_allowed_ports` / `_handle_wrk_open`, `HostTunnelAgent.open_worker_proxy` / `close_worker_proxies`, and added `/api/cluster/*` paths to `ALLOWED_TUNNEL_PATHS`.
- `shard_engine/engine.py`: Added `cancel_check` parameter to `ShardEngine.generate_stream()` and `_generate_gguf()`.
- `shard_engine/server.py`: Routed `/v1/chat/completions`, `/v1/messages`, `/v1/models`, `/v1/ping`, and `/api/server/chats` through `CLUSTER` + `SCHEDULER` asynchronously while preserving all Phase III single-node model-first guards and test contracts; added `/api/cluster/*` control-plane endpoints.
- `shard_engine/cli.py`: Added `shard cluster <create|join|status|plan|deploy|leave|stop>` with `--consent` enforcement.
- `shard_engine/dashboard.html`: Added the `Кластер и Распределение` subtab in `tab-server` with cluster overview cards, coordinator creation form, worker consent + quota join form, layer distribution visualizer, worker roster, and real inference monitoring.

---

## 3. Feature Status Classification Matrix

| Feature | Classification | Notes |
|---|---|---|
| Hardware & Backend Discovery (`hardware.py`, `capabilities.py`) | Implemented, Unit tested | Multi-GPU, CPU model/threads/util, RAM, disk, backend probes |
| Worker Consent & Resource Quotas (`vram_pct`, `max_ram_gb`, `max_threads`) | Implemented, Unit tested, Integration tested | Strictly rejects unconsented join (`HTTP 403`) |
| Model Architecture Inspection & Explicit Support Check (`planner.py`) | Implemented, Unit tested | Flags unsupported architectures honestly |
| Weak-Coordinator Partition Planning & Comm Cost Estimation | Implemented, Unit tested, Integration tested | Separates usable vs raw memory; accounts for KV + buffers + RTT |
| GGUF Distributed RPC Execution (`llama-server --rpc -sm layer -ts`) | Implemented, Integration tested (multi-process) | Verified across 2-3 `ggml-rpc-server` processes with `gpt2-medium.i1-Q6_K.gguf` |
| PyTorch Pipeline Parallelism (`SHPT`, selective safetensors loading, distributed KV cache) | Implemented, Integration tested (multi-process) | Verified exact logits equivalence (`atol=1e-4`) and streaming generation across 2 OS worker processes |
| Reverse Encrypted Tunnel Worker Proxy (`MSG_WRK_*`) | Implemented, Integration tested | Verified ChaCha20-Poly1305 encrypted tunnel loopback proxy to worker |
| Multi-User Async Scheduler, Queue, Rate Limiter & Cancellation | Implemented, Unit tested, Integration tested | Verified concurrent streaming requests and API key auth (`401`/`200`) |
| Worker Disconnect / Degraded & Rebalance Recovery | Implemented, Integration tested | Verified `READY -> DEGRADED -> replan -> READY` across 3->2 workers |
| Unified API (`/v1/chat/completions`, `/v1/messages`, `/v1/models`) | Implemented, Integration tested | OpenAI & Anthropic streaming/sync verified against distributed cluster |
| Cluster CLI (`shard cluster ...`) & Dashboard (`tab-server`) | Implemented | Added subcommands and Modern Slate cluster UI with SVG icons |
| Multi-Machine Physical Cluster Execution | Not tested on multiple physical machines | Verified via multi-process localhost tests on 1 Windows machine; cross-machine physical tests remain on roadmap |
| Tensor Parallelism (intra-layer AllReduce split) | Not implemented (deferred) | Documented as intentionally deferred over commodity WAN/LAN links due to per-layer AllReduce latency |
| Data-Parallel Replica Auto-Scaling | Not implemented (roadmap) | Single-model model-parallelism prioritized first |

---

## 4. Roadmap for Remaining Capabilities

1. **Physical Multi-Machine Validation**: Run cross-machine GGUF RPC and PyTorch pipeline benchmarks over Gigabit LAN, Radmin VPN, Tailscale, and Relay tunnels across Windows/Linux machines with heterogeneous GPUs (4 GB + 8 GB + 12 GB VRAM).
2. **Micro-Batch Pipeline Scheduling (GPipe / 1F1B)**: Upgrade `PipelineCoordinator` from serialized per-request pipeline stepping to interleaved micro-batching across stages so workers stay pipelined under heavy multi-user concurrency.
3. **Resilient Worker Checkpointing & Live KV Migration**: Support mid-generation retry/checkpoint replay if a worker disconnects during an active token stream.
4. **Optional Tensor Parallelism for NVLink / Multi-GPU Single Host**: Support intra-layer row/column sharding within multi-GPU nodes paired with inter-node pipeline parallelism.
5. **Distributed Weights P2P Prefetch**: Allow workers to fetch safetensors shards directly from peer workers or local LAN mirrors when Hugging Face access is throttled.
