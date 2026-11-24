# SHARD — Master Project Context, Architecture & Implementation Truth

**Document Role:** Canonical engineering reference and architectural north star for SHARD (`shard-engine`).  
**Repository:** `https://github.com/avelone123123/shard`  
**Package Version:** `1.9.0` (`shard_engine/__init__.py`, git tag `v1.9.0`)  
**Audited Branch & Commit:** `feat/worker-failure-resilience` @ `1a98ff72c80847c9d203a6634d3c9f63904ba7e0` (base `master` @ `10313ed2e6b8018c081789d9cff08bdbcf027391`; original resilience fork point `ff1155cd4d55d57a196db3b23c745d7d9be5ec36`)  
**Last Verified Against Source:** 2026-11-23

> **Engineering Rule of Thumb:** Treat source code, tests, and empirical checks as the source of truth. Never treat an architecture specification, hardcoded domain URL, or localhost test as proof of Internet-wide production readiness.

---

## 1. Core Mission & Product Identity

**SHARD is an open, cooperative, distributed AI computing infrastructure** designed to turn the fragmented GPU, VRAM, system RAM, and CPU capacity of ordinary independent machines into shared AI inference clusters.

Its core proposition is **not** "another local LLM launcher" or proprietary chat UI:
- A single mid-range computer that cannot fit a large open-weight model alone can contribute its available VRAM/RAM/compute to a multi-machine cluster.
- Multiple heterogeneous machines pool memory and compute to execute models exceeding any single participant's capacity.
- The resulting cluster exposes standard developer APIs (`/v1/chat/completions`, `/v1/messages`, `/v1/models`) so existing tools (Cursor, Claude Code, OpenCode, SDKs, custom apps) can use distributed inference transparently.

### What SHARD Is NOT
- **Not a torrent client** for downloading model weight files.
- **Not a cloud GPU rental marketplace** or centralized model hosting provider.
- **Not a blockchain or cryptocurrency project** — there is no crypto token economy; "token" strictly means a unit of generated text.
- **Not primarily a distributed training framework** — optional single-node local fine-tuning via [SOUP](docs/COLLABORATIVE_TRAINING.md) (`shard_engine/training.py`, `shard_engine/soup_adapter.py`) exists as a secondary workflow, not the core identity.
- **Not a proprietary chatbot UI** or passive GPU monitor.

---

## 2. BitTorrent-Inspired Architectural Philosophy

SHARD takes **conceptual inspiration** from BitTorrent without copying its protocol or file-chunk mechanics:
- **BitTorrent** demonstrated that ordinary computers can pool bandwidth and storage to distribute files cooperatively without centralized distribution servers.
- **SHARD** explores a cooperative model for **AI inference compute and memory**: sovereign peer identities (`shn_<40hex>`), dynamic consent-based resource contribution, peer discovery, availability tracking, fault tolerance, and hardware/topology-aware scheduling.

### Crucial Technical Difference From BitTorrent
File chunks in BitTorrent can be fetched out of order from interchangeable peers. **Autoregressive Transformer inference has strict sequential dependencies**:
- Every generated token traverses model layers sequentially across stages (`Stage 0 -> Stage 1 -> ... -> Stage N-1 -> Logits`).
- Each stage holds stateful **KV-cache** tensors for active sequences.
- Workers are **not interchangeable**: GPU architecture, VRAM, memory bandwidth, RAM, network RTT/bandwidth, stage assignment, and reliability directly constrain latency and feasibility.
- Losing one assigned Worker interrupts the active execution pipeline until failure detection, KV cleanup, and cluster re-formation complete.
- **Capacity is not throughput:** pooling memory enables larger models to fit, but inter-node activation hops and stage imbalance mean distributed execution does not deliver linear speedup over a single GPU capable of holding the model.

---

## 3. Compute Sharing vs. Token Sharing (Critical Distinction)

SHARD **does not** split generated text tokens across users. Participants contribute **hardware compute and memory** to execute model partitions:

1. **Model partitioning (`shard_engine/distributed/planner.py`, `shard_engine/lan.py`):** Layers and KV cache are partitioned across participating nodes according to usable VRAM/RAM budgets, user-configured quotas, and device speed/RTT heuristics.
2. **Stage loading (`pipeline_worker.py`, `gguf_backend.py`):**
   - In `torch_pipeline_parallel`, each Worker loads only its assigned safetensors layer shards and maintains per-request `DynamicCache` KV state locally.
   - In `gguf_rpc_layer_split`, the Coordinator runs `llama-server --rpc <workers> -ts <weights>` while Workers run `ggml-rpc-server` to host offloaded weight tensors, KV cache, and layer ops.
3. **Activation pipeline:** Input token IDs enter Stage 0; **intermediate hidden-state tensor activations** cross stage boundaries over TCP (`SHPT` binary framing or llama.cpp RPC); the final stage projects logits and samples the next token.
4. **Autoregressive loop:** Repeats step-by-step until EOS, stop sequence, `max_tokens`, client disconnect, or structured failure.

Never conflate:
- **Compute/memory sharing** (GPU/VRAM/RAM/CPU contributed by Workers)
- **Model weight distribution** (layer shards loaded per stage)
- **Intermediate tensor activations** (hidden states passed between stages)
- **Generated text tokens** (output streamed to Inference Clients)
- **Training/feedback data** (optional, strictly opt-in local SOUP workflow)

---

## 4. Current System Architecture

### High-Level Component Map

```text
+-----------------------------------------------------------------------------+
|  Interfaces: CLI (shard_engine/cli.py) | Dashboard (shard_engine/dashboard) |
|              OpenAI/Anthropic HTTP/SSE API (shard_engine/server.py)         |
+--------------------------------------+--------------------------------------+
                                       |
      +--------------------------------+--------------------------------+
      |                                |                                |
      v                                v                                v
+------------------+       +-------------------------+      +---------------------+
| Local Inference  |       | Distributed Execution   |      | Network & Identity  |
| - engine.py      |       | 1) Cluster (Modern):    |      | - net_identity.py   |
| - gguf_backend.py|       |    distributed/         |      | - net_discovery.py  |
| - fit.py         |       |    - cluster.py         |      | - net_tunnel.py     |
| - hardware.py    |       |    - planner.py         |      | - net_strategy.py   |
| - model_browser  |       |    - scheduler.py       |      | - net_wireguard.py  |
| - shard_bridge   |       |    - pipeline_coord.py  |      | - radmin_mgr.py     |
|   (C++/ASM DLL)  |       |    - pipeline_worker.py |      | - net_platform_*.py |
+------------------+       |    - pipeline_proto.py  |      +---------------------+
                           |    - resilience.py      |
                           |    - capabilities.py    |
                           | 2) LAN Pool (Phase 1):  |
                           |    - lan.py (UDP+RPC)   |
                           +-------------------------+
```

### Important Architectural Reality: Two Coexisting Distributed Subsystems
The repository currently contains **two distinct distributed orchestration layers** exposed side-by-side in `shard_engine/server.py`:
1. **LAN Pool (`shard_engine/lan.py`, `/api/lan/*`, `shard lan`):** Shipped in v1.7.0–v1.8.0. Uses UDP port `8001` beacons, `XXXX-XXXX` pairing codes / open-LAN flag, `sk-shard-lan-*` tokens, 1s heartbeats (`3` misses -> drop), and feeds `--rpc` / `-ts` into `ShardEngine` (`engine.py`) for GGUF pooling.
2. **Distributed AI Cluster (`shard_engine/distributed/*`, `/api/cluster/*`, `shard cluster`):** Newer unified orchestrator supporting both `gguf_rpc_layer_split` and `torch_pipeline_parallel`, hardware-aware partition planning (`planner.py`), multi-user `RequestScheduler`, reverse encrypted tunnel worker proxies (`open_worker_proxy`), global catalog publishing (`/api/catalog/*`), 4-state worker liveness FSM, and mid-generation failure recovery (`resilience.py`).

### Key Source Modules & Responsibilities

| Area | Modules | Purpose |
| :--- | :--- | :--- |
| Core Server & CLI | `shard_engine/server.py` (3357L), `shard_engine/cli.py` (2213L), `shard_engine/main.py` | FastAPI control plane, OpenAI/Anthropic APIs, auth/CSRF middleware, CLI entrypoint (`shard`) |
| Local Inference | `shard_engine/engine.py` (1519L), `shard_engine/gguf_backend.py` (926L), `shard_engine/model_browser.py` (687L), `shard_engine/hf_hub.py` | Transformers/SafeTensors and `llama-server` GGUF lifecycle, download/progress, stream sanitizer |
| Hardware & Fit | `shard_engine/hardware.py` (334L), `shard_engine/fit.py` (289L), `shard_engine/distributed/capabilities.py` (89L) | GPU/RAM/CPU/disk probing, quota math, quant memory/KV estimation, `llama-server --list-devices` |
| Distributed Cluster | `shard_engine/distributed/cluster.py` (1300+L), `planner.py` (401L), `scheduler.py`, `pipeline_coordinator.py`, `pipeline_worker.py`, `pipeline_proto.py`, `resilience.py` | Partition planning, multi-user queuing, PyTorch `SHPT` pipeline, GGUF RPC orchestration, 4-state FSM & failure recovery |
| Phase-1 LAN Pool | `shard_engine/lan.py` (1622L) | UDP `:8001` LAN discovery, pairing, heartbeats, `ggml-rpc-server` management, `-ts` split weights |
| Global Networking | `shard_engine/net_identity.py` (1079L), `net_discovery.py` (1169L), `net_tunnel.py` (2402L), `net_strategy.py` (2230L), `net_wireguard.py` (780L), `radmin_mgr.py` (253L), `net_platform_win.py`, `net_platform_linux.py` | Ed25519/X25519 identity, TOFU pinning, Discovery store/client, ChaCha20-Poly1305 WebSocket blind relay & reverse tunnels, STUN/NAT probing, WireGuard/Radmin detection |
| Native Core | `native/shard_core.cpp`, `native/shard_asm.asm`, `native/shard_gui.cpp`, `shard_engine/shard_bridge.py` (289L) | Optional C++20/MASM AVX2 SIMD kernels (`shard_core.dll`) used in `shard test` benchmarks & Win32 GUI launcher |
| Optional Training (SOUP) | `shard_engine/training.py` (1137L), `soup_adapter.py` (1175L), `feedback.py` (942L) | Optional single-node consent-gated feedback & local fine-tuning via `soup-cli` |
| Persistence & Utilities | `shard_engine/storage.py`, `paths.py`, `settings.py`, `proc_io.py`, `process.py`, `updater.py`, `session_registry.py`, `telemetry.py`, `cache_index.py` | Atomic JSON writes with file locking & restricted permissions, Windows UTF-8 subprocess handling, updater |
| Web UI | `shard_engine/dashboard.html` (authoritative package copy), `dashboard.html` (drifted root copy) | 7-tab control plane (`tab-chat`, `tab-market`, `tab-network`, `tab-server`, `tab-connect`, `tab-keys`, `tab-training`) |

---

## 5. Participant Roles & Boundaries

SHARD enforces a strict separation between three participant roles across `lan.py`, `distributed/cluster.py`, `server.py`, and `net_tunnel.py`:

1. **Coordinator / Master (`role="coordinator"` in cluster, `role="server"` in LAN):**
   - Owns model selection, hardware capacity assessment, partition planning (`plan_cluster_partition`), worker registration, request scheduling (`RequestScheduler`), activation coordination, heartbeat/data-plane health monitoring, failure cleanup/re-formation, and public `/v1/*` API serving.
   - Can participate in compute (`coordinator_contributes=True`) or act purely as orchestrator (`coordinator_contributes=False`) when Workers carry all model layers.
2. **Worker / Compute Provider (`role="worker"`):**
   - Explicitly opts in (`consent=True` required; missing consent returns HTTP `403 CONSENT_REQUIRED`) with user-configurable resource quotas (`vram_pct`, `max_vram_mb`/`gb`, `ram_pct`, `max_ram_mb`/`gb`, `max_threads`).
   - Executes either `PipelineWorkerServer` (PyTorch safetensors shard + local KV cache over authenticated `SHPT` TCP) or `ggml-rpc-server` (llama.cpp RPC offload).
   - Can inspect status and detach safely at any time (`shard cluster leave`, `shard lan off`, `/api/cluster/worker/leave`, `/api/lan/compute/opt_out`).
3. **Inference Client / Consumer (`role="member"` in LAN, client mode in catalog/tunnel):**
   - Consumes `/v1/chat/completions`, `/v1/messages`, `/v1/models`, or hosted web chat (`/client`) without contributing compute resources.
   - Isolated from host administration: cannot access `/api/cluster/*` admin routes, `/api/training/*`, or `/api/keys/*`.

---

## 6. Local Inference Capabilities

Implemented in `shard_engine/engine.py`, `shard_engine/gguf_backend.py`, and `shard_engine/model_browser.py`:
- **GGUF via `llama.cpp` (`LlamaBackend`):** Auto-discovers or downloads official `llama-server` / `ggml-rpc-server` binaries into `~/.shard/runtime`, resolves GGUF repositories and quant tiers on Hugging Face, runs `llama-server` locally with GPU layer offload (`-ngl`) or hybrid VRAM+RAM split, streams tokens via guarded SSE (`chat_stream_guarded`), and monitors process health/slots.
- **PyTorch / SafeTensors (`ShardEngine`):** Loads Hugging Face Transformers models (`AutoModelForCausalLM`, `AutoTokenizer`, PEFT LoRA adapters, local merged checkpoints) with streaming via `TextIteratorStreamer`, cancellation via `_ShardCancelCriteria`, and `<think>` / special-token sanitization (`_StreamSanitizer`).
- **Model Lifecycle State Machine:** Tracks `DISCOVERED -> COMPATIBILITY_CHECK -> QUEUED -> DOWNLOADING -> VERIFYING -> DOWNLOADED -> LOADING -> READY` (plus failure/cancel states), duplicate download guards, and live progress telemetry (`tests_model_lifecycle_progress.py`: 16 tests).
- **Native C++20 / AVX2 Module (`shard_bridge.py`):** Exposes 12+ float32/dequant SIMD kernels and Win32 mmap helpers with pure-Python fallback, verified by `shard test`. **Important caveat:** `engine.py` attaches `self.native_core = shard_native` for status/diagnostics, but actual LLM forward execution runs inside PyTorch or `llama-server`, not via `shard_core.dll` kernels.

---

## 7. LAN Distributed Inference

SHARD provides two working LAN mechanisms (verified on localhost multi-process suites and documented in `docs/LAN_SHARDING_DESIGN.md` and `SHARD_WORKER_FAILURE_RESILIENCE_REPORT.md`):

### A. Phase-1 LAN Memory Pool (`shard_engine/lan.py`)
- **Discovery & Pairing:** Permanent daemon UDP beacon on port `8001` (2s interval, 8s TTL, `SO_REUSEADDR`). Supports closed networks (`XXXX-XXXX` pairing code) and opt-in open networks (`open: true`), issuing `sk-shard-lan-<16hex>` member tokens bound to IP + token.
- **Pooled Execution:** Workers spawn `ggml-rpc-server` (default port `50052`) and send 1s heartbeats (`3` misses -> dropped). `rpc_targets()` samples master free VRAM/RAM live, combines alive workers' budgets (`VRAM_RESERVE_MB=400`, `RAM_RESERVE_MB=2048`), optionally weights by `_GPU_SCORE`, and passes `--rpc` + `-ts` to `llama-server`.
- **Fit Bypass:** `engine.py` bypasses single-machine `IMPOSSIBLE` verdicts only when `LAN.pool_active()` is true, `disk_ok` is not false, and `pool_capacity_mb()` covers the recommended quant total GB.

### B. Distributed AI Cluster (`shard_engine/distributed/*`)
- **Dual Execution Strategies:**
  1. `gguf_rpc_layer_split`: Coordinates `llama-server --rpc` + `-ts` across workers (including reverse-tunneled workers via local TCP proxy) with continuous batching (`--parallel`, `--cont-batching`).
  2. `torch_pipeline_parallel`: Partitions safetensors layers for 8 decoder families (`llama`, `qwen2`, `qwen3`, `mistral`, `gemma`, `gemma2`, `phi3`, `gpt2`) across Coordinator and `PipelineWorkerServer` nodes using authenticated binary `SHPT` TCP framing and per-worker KV caches.
- **Worker Failure Resilience (`resilience.py`, `cluster.py`, `pipeline_coordinator.py`):**
  - 4-state liveness FSM (`HEALTHY`, `SUSPECT`, `FAILED`, `RECOVERING`) with monotonic `hb_seq`, `sent_at < failed_at` stale-heartbeat rejection, >=2-heartbeat + TCP-probe probation, and background epoch-guarded cluster re-formation.
  - Bounded per-phase timeouts (`connect`, `load`, `prefill`, `decode_step`, `stall`, `release`, `request_deadline`) with `poll_slice_s` cancellation polling on both send and recv (resisting TCP blackholes, 1-byte/30ms slow trickles, and GGUF `: ping` keepalive loops).
  - **Mid-generation recovery (`torch_pipeline_parallel`):** Reconstructs KV caches via **full-context prefill replay** (`[prompt + generated_ids]`) onto freshly reformed stages (not binary KV-cache tensor transfer), preserving `temperature`, `top_p`, `top_k`, and `repetition_penalty` without duplicating delivered tokens.
  - Verified by `test/tests_worker_resilience.py` (16 tests) + `test/tests_worker_resilience_audit.py` (8 tests) = **24/24 passed** on localhost Windows (including real OS `proc.kill()` subprocess workers).

---

## 8. Networking & Global Peer-to-Peer Vision

### What Exists in Source (`shard_engine/net_*.py`, `radmin_mgr.py`)
- **Cryptographic Identity (`net_identity.py`):** Persistent Ed25519 signing keypair (`shn_<40hex>` Node ID), domain-separated signed envelopes (`SHARD-DISC-REG-V1`, `SHARD-DISC-HB-V1`, `SHARD-DISC-UNREG-V1`, `SHARD-RELAY-REG-V1`), revocable invite tokens (`shinv1_*`), SSRF/private-IP validation on external service URLs, and TOFU key pinning (`tofu_pins.json`).
- **Discovery Service & Client (`net_discovery.py`):** Self-hostable FastAPI registry (`shard network discovery-serve`) with Ed25519 signature verification, replay/sequence/clock-skew checks, per-IP rate limiting, TTL expiration, search/filtering (`q`, `model`, `region`, `access`, `min_vram`, `status`, `accepts_workers`), and private commitment lookups (`/v1/discovery/*`, `/healthz`).
- **Encrypted Tunnels & Blind WebSocket Relay (`net_tunnel.py`):** 3-step ephemeral X25519 + Ed25519 authenticated handshake, `ChaCha20-Poly1305` AEAD framing with 16-byte AAD and monotonic anti-replay sequences, self-hostable `RelayHub` (`shard network relay-serve`, `/v1/relay/host`, `/v1/relay/connect`, `/healthz`), HTTP allowlist tunneling (`ALLOWED_TUNNEL_PATHS`), and reverse worker data-plane TCP proxying (`MSG_WRK_OPEN`/`DATA`/`CLOSE`).
- **Transport Strategy & Diagnostics (`net_strategy.py`, `net_wireguard.py`, `net_platform_*.py`, `radmin_mgr.py`):** Priority ordering (`same_machine -> direct_lan -> mesh_vpn -> direct_p2p -> relay`), RFC 5389 STUN NAT profiling (`stun.l.google.com`, `stun.cloudflare.com`), UDP punch helpers, WireGuard interface probing, OS firewall diagnostics, and Windows Radmin VPN detection/GUI launcher.

### Empirical Infrastructure Reality (Crucial Distinction)
- **Public `*.shard.network` services are NOT deployed:** Live verification on 2026-11-23 confirmed `shard.network` resolves to AWS Global Accelerator domain-parking IPs (`13.248.169.48`, `76.223.54.146`) redirecting to `/lander`, while `https://discovery.shard.network/health` and `https://relay.shard.network` fail TLS (`TLSV1_UNRECOGNIZED_NAME`). Global operation today requires self-hosting Discovery/Relay (`SHARD_DISCOVERY_URL`, `SHARD_RELAY_URL`) or using a VPN mesh.
- **No Internet-wide multi-machine verification yet:** All global-network and tunnel tests (`tests_shard_network.py`, `tests_global_network.py`, `tests_autonomous_network.py`, `tests_phase4_network_integration.py`, `tests_distributed_ai.py`) run against local loopback servers on a single Windows host.

---

## 9. APIs & Integrations

### Implemented Client-Facing Endpoints (`shard_engine/server.py`)
- `GET /v1/models` — lists active local or distributed cluster model(s).
- `GET /v1/ping` — unauthenticated reachability/RTT probe (exempt from bearer auth).
- `POST /v1/chat/completions` — OpenAI-compatible streaming (`SSE`) and non-streaming chat completions; routes to `CLUSTER` when Coordinator is active, otherwise `ShardEngine`.
- `POST /v1/messages` and `POST /v1/messages/count_tokens` — Anthropic-compatible streaming (`message_start`, `content_block_start`, `content_block_delta`, `event: error`, guarded `end_turn`) and non-streaming Messages API.
- `GET /client` and `/api/server/*` — hosted multi-chat client surface for connected peers.
- `POST /api/integrations/apply` — writes local client configs for tools such as Cursor, Claude Code, and OpenCode.

### Notable API Gap
- **`POST /v1/completions` is NOT implemented** in `shard_engine/server.py` (only `/v1/chat/completions` and `/v1/messages` exist).

### Control-Plane Route Groups (`shard_engine/server.py`)
- `/api/cluster/*` (18 routes): create, stop, deploy, rebalance, preview, status, plan, telemetry, resilience, resilience/policy, workers/register|heartbeat|leave|kick, worker/join|leave, my-servers, my-workers.
- `/api/lan/*` (17 routes): Phase-1 LAN pool lifecycle, pairing, heartbeats, compute consent/opt-out, diagnostics, kick/unblock.
- `/api/net/*` (29 routes): identity, config, status, doctor, diagnostics, servers, lookup, publish/unpublish, invites, connect/disconnect, peer ACL/quotas, Radmin endpoints.
- `/api/catalog/*` (6 routes): global catalog listing, server passport, pre-join capability audit, publish/unpublish, 1-click join.
- `/api/models/*`, `/api/fit/{model_id}`, `/api/hf/*`, `/api/system/*`, `/api/settings`, `/api/keys/*`, `/api/feedback/*`, `/api/training/*`.

---

## 10. Hardware-Aware Planning (`shard fit` & `planner.py`)

Hardware-aware placement is a foundational pillar of SHARD, implemented across three cooperating layers:

1. **Local Hardware & Quota Probing (`shard_engine/hardware.py`, `distributed/capabilities.py`):**
   - Queries `nvidia-smi` for multi-GPU index, name, total/used/free VRAM, and utilization; `psutil` for system RAM and logical/physical CPU cores; `shutil.disk_usage` for model storage; `llama-server --list-devices` for offload backends.
   - `effective_usable_resources()` enforces hard OS safety reserves (`VRAM_RESERVE_MB=400`, `RAM_RESERVE_MB=2048`) and caps usable resources by user quotas (`vram_pct`, `max_vram_mb`/`gb`, `ram_pct`, `max_ram_mb`/`gb`, `max_threads`).
2. **Single-Machine Fit Advisor (`shard_engine/fit.py`, `shard fit <model>`):**
   - Reads Hugging Face `config.json` (with name-regex fallback) to compute exact parameter counts (including GQA `num_key_value_heads` and MoE `num_experts`) and KV-cache footprint at `ctx_tokens`.
   - Evaluates 6 quant tiers (`Q8_0`, `Q6_K`, `Q5_K_M`, `Q4_K_M`, `Q3_K_M`, `Q2_K`), disk free space (`1.05x` margin), GPU layer count (`ngl`), and verdict (`GPU`, `HYBRID`, `IMPOSSIBLE`).
3. **Cluster Partition Planner (`shard_engine/distributed/planner.py`):**
   - `inspect_model_architecture()` inspects local HF directories, GGUF files, safetensors weight maps, and model type compatibility.
   - `plan_cluster_partition()` computes per-node usable memory, accounts for KV cache scaled by `parallel_slots` (GGUF) and scratch buffers, assigns layer ranges proportionally with a `1.2x` CUDA preference, verifies every stage fits its node budget, estimates per-token inter-stage network latency (`estimated_comm_overhead_ms_per_tok`), emits `prefer_single_node` / `communication_warning` when single-GPU local execution is faster, and builds `--rpc`/`-ts` or per-stage safetensors `shard_files`.

---

## 11. Security & Consent Principles

### Enforced Controls (Verified in Source & Tests)
- **Explicit Worker Consent:** `/api/cluster/worker/join` and `/api/catalog/join` (`mode="worker"`) reject `consent != True` with HTTP `403 CONSENT_REQUIRED`. Workers can set quotas and opt out immediately (`/api/cluster/worker/leave`, `/api/lan/compute/opt_out`).
- **Owner Isolation (`server.py`):** `lan_gate` validates `Host`, `Origin`, and `Sec-Fetch-Site` (blocking cross-site mutations). Administrative endpoints (`/api/cluster/*` admin ops, `/api/training/*`, `/api/keys/*`, `/api/net/*` mutations) require local owner identity (loopback without tunnel/proxy markers, or `X-Shard-Owner-Token` stored with restricted permissions).
- **Zero-Trust Tunnel Isolation (`net_tunnel.py`):** Remote tunnel peers are strictly limited to `ALLOWED_TUNNEL_PATHS`, cannot reach admin `/api/*` endpoints, and have spoofable headers (`X-Shard-Owner-Token`, `X-Shard-Bridge-Token`, `X-Shard-Tunnel-Peer`, `X-Forwarded-*`) stripped before local forwarding. `X-Shard-Bridge-Token` uses a per-process memory-only 256-bit secret.
- **Data-Plane & Identity Cryptography:** Ed25519 signatures + TOFU pinning (`net_identity.py`), X25519 + ChaCha20-Poly1305 AEAD tunnels (`net_tunnel.py`), authenticated `SHPT` pipeline handshake (`pipeline_proto.py`), and recursive prompt/token/secret redaction in resilience logs (`resilience.py`).
- **Storage Hygiene (`storage.py`):** Atomic write-rename with file locks and restricted filesystem permissions (`0600` on POSIX / ACLs on Windows) for identity keys, API keys, and tokens.
- **Feedback Privacy (`feedback.py`):** Regular inference never silently records prompts/responses into training sets; feedback requires explicit consent, redacts secrets/PII before writing to disk, and supports participant revocation.

### Security Gaps Identified During Repository Audit (Must Be Fixed Before Production Claims)
1. **`radmin_mgr.py` Authenticode check fails open:** Lines 116–122 return `(True, "Sanity verified", details)` even when Windows Authenticode validation fails or throws an error — contradicting `RADMIN_VPN_INTEGRATION_REPORT.md`.
2. **Predictable Radmin room passwords:** `radmin_mgr.py:232-238` derives room passwords solely from `sha256("radmin-cluster-seed:" + cluster_id)` with no secret entropy.
3. **Unencrypted LAN `ggml-rpc-server` transport:** llama.cpp's native RPC socket (`:50052`) has no built-in TLS/auth; security on direct LAN depends on network isolation or routing through SHARD's encrypted tunnel (`open_worker_proxy`).
4. **Open LAN mode risk:** Enabling `open_net=True` on `/api/lan/*` intentionally removes pairing-code checks and must only be used on trusted private LANs.

---

## 12. Current Implementation Status & Doc-vs-Code Reconciliation

### Implementation-Status Matrix

Classifications: **Implemented** (verified in source + tests), **Experimental** (code exists, reliability/portability/deployment unproven), **Planned** (not implemented), **Unverified** (present in code/docs without live verification).

| Capability | Classification | Verification Scope | Key Modules / Tests | Notes & Caveats |
| :--- | :--- | :--- | :--- | :--- |
| Local GGUF inference (`llama-server`) | **Implemented** | Automated tests + single Windows machine | `gguf_backend.py`, `engine.py`, `tests_shard.py` | Auto-downloads official runtime binaries; supports hybrid VRAM+RAM offload |
| Local PyTorch / SafeTensors inference | **Implemented** | Automated tests + single Windows machine | `engine.py`, `tests_model_lifecycle_progress.py` | Supports HF models, LoRA adapters, cancellation, `<think>` sanitizer |
| Hardware detection & `shard fit` | **Implemented** *(with limits)* | Automated tests + single Windows machine | `hardware.py`, `fit.py`, `tests_shard.py` | NVIDIA via `nvidia-smi` only; no native ROCm/Apple VRAM telemetry; fixed bandwidth constants; CPU-only marks `IMPOSSIBLE` in `fit.py` |
| OpenAI (`/v1/chat/completions`, `/v1/models`) & Anthropic (`/v1/messages`) APIs | **Implemented** *(with limits)* | Automated tests (strict SSE parsers) | `server.py`, `tests_worker_resilience_audit.py` | `/v1/completions` not implemented; external `openai`/`anthropic` SDK packages not tested locally |
| Web Dashboard & CLI (`shard`) | **Implemented** *(with drift)* | Automated tests + local UI | `cli.py`, `shard_engine/dashboard.html`, `tests_cli_pkg.py` | Root `dashboard.html` drifted behind `shard_engine/dashboard.html` (`test_09` pre-existing failure) |
| Phase-1 LAN Pool (`shard lan`, UDP `:8001`, GGUF RPC) | **Implemented** *(single-host tested)* | 30 tests in `tests_shard.py` + local tests | `lan.py`, `engine.py`, `docs/LAN_SHARDING_DESIGN.md` | Multi-machine live `-ts` placement order not yet benchmarked across physical hosts |
| Cluster Planner & Scheduler (`planner.py`, `scheduler.py`) | **Implemented** | Automated tests | `distributed/planner.py`, `scheduler.py`, `tests_distributed_ai.py` | Supports GGUF RPC and 8 PyTorch decoder families; FIFO + rate-limit + cancellation |
| PyTorch Pipeline Parallelism (`torch_pipeline_parallel`) | **Implemented** *(single-machine multi-proc)* | Real OS subprocess + loopback TCP tests | `pipeline_coordinator.py`, `pipeline_worker.py`, `pipeline_proto.py`, `tests_distributed_ai.py`, `tests_worker_resilience_audit.py` | Verified on small synthetic Qwen2 models locally; large multi-GB multi-machine benchmarks not yet run |
| GGUF Distributed Cluster (`gguf_rpc_layer_split`) | **Implemented** *(single-machine multi-proc)* | Local `llama-server` + `ggml-rpc-server` tests | `cluster.py`, `gguf_backend.py`, `tests_distributed_ai.py` | No mid-generation resume after token 1 (`llama-server` internal KV state) |
| Worker Failure Resilience & Context-Replay Recovery | **Implemented** *(with limits)* | 24/24 focused tests (incl. `proc.kill()`, blackhole, trickle) | `resilience.py`, `cluster.py`, `pipeline_coordinator.py`, `tests_worker_resilience*.py` | Context replay (`[prompt+generated_ids]`), not binary KV restore; RNG not checkpointed; HF revision not SHA-pinned; Windows localhost only |
| Cryptographic Identity, TOFU, Self-Hosted Discovery & Blind Relay | **Implemented** *(localhost only)* | Automated loopback tests (`tests_shard_network.py`, `tests_network_persistence_security.py`, `tests_global_network.py`) | `net_identity.py`, `net_discovery.py`, `net_tunnel.py`, `net_strategy.py` | Crypto & protocol tests pass locally; never verified across separate physical WAN hosts |
| Reverse Encrypted Tunnel Worker Proxy (`MSG_WRK_OPEN`) | **Experimental** | Single loopback test (`tests_distributed_ai.py::test_reverse_encrypted_tunnel_worker_proxy`) | `net_tunnel.py`, `cluster.py` | Functional in unit/integration test; WAN throughput/latency stability unverified |
| Public Global Infrastructure (`discovery.shard.network`, `relay.shard.network`) | **Planned / Not Deployed** | Empirically checked 2026-11-23 | `net_strategy.py` defaults, `deploy/*` | Domains point to parked `/lander` IPs and fail TLS; `deploy/Dockerfile.*` fail build (`requirements.txt` missing, wrong health paths) |
| STUN Direct P2P Hole-Punching over WAN | **Experimental / Unverified** | Unit/mocked STUN parsing only | `net_strategy.py`, `tests_phase4_network_integration.py` | STUN binding packet parser works; real cross-NAT hole punching not verified across two WAN machines |
| WireGuard Mesh Integration (`net_wireguard.py`) | **Experimental / Unverified** | Status/config generation tests only | `net_wireguard.py`, `tests_global_network.py` | Interface detection & config planning exist; automated cross-machine mesh setup not verified |
| Windows Radmin VPN Fallback (`radmin_mgr.py`) | **Experimental** *(security defect)* | Mocked/status API tests (`tests_autonomous_network.py`) | `radmin_mgr.py` | Windows-only; manual GUI room join required; Authenticode check fails open (`return True`); deterministic password |
| Optional Local Training Integration (SHARD x SOUP) | **Experimental** | Mocked/lifecycle tests (`tests_training_ecosystem.py`: 8 tests) | `training.py`, `soup_adapter.py`, `feedback.py` | Single-node only; upstream `soup-cli` requires Python `>=3.11,<3.14` (incompatible with Python 3.14 without separate venv) |
| Native C++20 / MASM AVX2 Library (`shard_core.dll`) | **Implemented** *(benchmark-only)* | `shard test` / `tests_shard.py` | `native/shard_core.cpp`, `shard_asm.asm`, `shard_bridge.py` | Prebuilt Windows DLL exists; not wired into PyTorch or `llama.cpp` inference hot path |
| Cross-Machine Physical LAN/WAN Multi-Node Clusters | **Unverified** | None executed in repo | — | All 248 tests run on localhost; physical multi-host testing is required |
| GitHub Actions Remote CI Execution | **Unverified** | Workflows present (`.github/workflows/tests.yml`) | `.github/workflows/*.yml` | Workflows run `windows-latest` unit tests + `ubuntu-latest` packaging audit; current branch not verified on remote CI yet |

### Current Automated Test Baseline (Verified 2026-11-23 on Windows 11, Python 3.14.4)
- **Focused Resilience & Audit Suites:** `24/24 passed` (`0` failed, `0` skipped, `49.088s`).
- **Full Repository Suite (`python -m unittest discover -s test -p "tests_*.py" -v`):** `248` tests (`187.494s`): **`245` passed**, **`1` skipped** (`tests_cli_pkg.py::test_wheel_contents_when_built`), **`2` proven pre-existing baseline failures** in `test/tests_phase_i_product_hardening.py` (`test_06` local snapshot on `D:\shard-models`; `test_09` root `dashboard.html` vs `shard_engine/dashboard.html` mismatch).

### Reconciled Contradictions Between Reports and Actual Code

| Report / Doc Claim | Actual Repository Truth |
| :--- | :--- |
| `AUTONOMOUS_NETWORK_ARCHITECTURE.md`, `SHARD_PRODUCTION_INFRASTRUCTURE_REPORT.md`, `REAL_WORLD_NETWORK_TEST_REPORT.md`: `discovery.shard.network` & `relay.shard.network` are "Official Public Infrastructure" ready for production. | **Not deployed.** Domains resolve to parking IPs (`13.248.169.48` / `76.223.54.146` -> `/lander`) and fail TLS. Only self-hosted execution is functional today. |
| `SHARD_PRODUCTION_INFRASTRUCTURE_REPORT.md` & `deploy/Dockerfile.*`: Production Docker build & Caddyfile for Discovery (`8080`, `/v1/discovery/health`, `/api/net/announce`) and Relay (`8765`/`8081`, `/health`). | **Broken out of the box.** `deploy/Dockerfile.discovery` & `deploy/Dockerfile.relay` run `COPY requirements.txt .` (missing from repo), and healthcheck paths mismatch the actual FastAPI `/healthz` route in `net_discovery.py` and `net_tunnel.py`. Actual discovery routes are `/v1/discovery/*`. Root `docker-compose.network.yml` + `Dockerfile.network` provide a separate layout on `8090`/`8091`. |
| `RADMIN_VPN_INTEGRATION_REPORT.md`: Execution immediately halts if Authenticode `Status != Valid` or signer is not `Famatech Corp.` | **False (fail-open bug).** `shard_engine/radmin_mgr.py:122` falls through to `return True, "Sanity verified", details` even if signature validation fails or errors. |
| `AUTONOMOUS_INSTALLATION_REPORT.md`: CLI supports `--no-radmin`, `--discovery-url`, `--relay-url`, `--headless`, and `SHARD_NO_RADMIN`. | **Not implemented in `cli.py`.** None of those CLI flags or `SHARD_NO_RADMIN` exist in `shard_engine/cli.py`. Only `SHARD_DISCOVERY_URL` and `SHARD_RELAY_URL` env vars exist (in `net_strategy.py`). |
| `REAL_WORLD_NETWORK_TEST_REPORT.md`: References `dist_pipeline.py`, `dist_engine.py`, `cluster_mgr.py` and declares "all production criteria met". | **Stale/inaccurate filenames and overstated readiness.** Actual modules are under `shard_engine/distributed/`; tests ran on localhost only. |
| `docs/LAN_SHARDING_DESIGN.md`: States `/api/*` has no auth ("trusted-LAN"). | **Partially superseded.** `server.py` now enforces Host/Origin/Sec-Fetch-Site CSRF guards, owner-only guards (`_is_owner_request` / `X-Shard-Owner-Token`) on admin endpoints, and strict tunnel allowlisting (`ALLOWED_TUNNEL_PATHS`). |
| `README.md` footer cites `v1.8.1`. | Package `__version__` (`shard_engine/__init__.py`) and latest git tag are `v1.9.0`. |

---

## 13. Experimental & Planned Capabilities

### Currently Experimental
- Reverse-tunneled distributed workers over encrypted WebSocket relay (`open_worker_proxy` / `MSG_WRK_OPEN`).
- STUN NAT traversal and UDP hole punching across real consumer/CGNAT routers (`net_strategy.py`).
- WireGuard interface orchestration (`net_wireguard.py`).
- Windows Radmin VPN fallback workflow (`radmin_mgr.py`).
- Optional local fine-tuning via SOUP (`training.py`, `soup_adapter.py`).
- Automated self-hosted Docker deployment stacks (`deploy/*`, `docker-compose.network.yml`).

### Planned / Long-Term Vision (Not Yet Built or Deployed)
- **Hosted Public Discovery & Blind Relay Infrastructure** (deployed, TLS-terminated, monitored, rate-limited, DDoS-hardened).
- **True Multi-Machine Cross-Network Distributed Clusters** verified across heterogeneous physical hosts on LAN, VPN, and WAN.
- **Cross-Platform GPU Telemetry & Planning:** Native AMD ROCm, Intel Arc, and Apple Silicon Metal/unified-memory VRAM & memory-bandwidth detection in `hardware.py` and `fit.py`.
- **Unified Distributed Control Plane:** Consolidating Phase-1 `lan.py` and modern `distributed/cluster.py` into a single coherent orchestration model without breaking existing `shard lan` users.
- **Dynamic Performance-Aware Scheduling & Topology Awareness:** Measured inter-node bandwidth/RTT profiling, real per-GPU throughput benchmarking, latency-aware stage rebalancing, and peer reliability scoring.
- **Legacy `/v1/completions` Compatibility** (if needed by older IDE/tool integrations) and official `openai`/`anthropic` SDK integration tests.
- **Binary KV-Cache Checkpointing / Speculative or Pipeline Micro-Batching** (long-term research beyond current single-sequence context-replay recovery).

---

## 14. Known Technical Limitations

1. **Context Replay vs. Binary KV-Cache Restoration:** `torch_pipeline_parallel` recovery re-prefills `[prompt + generated_ids]` onto fresh stage caches rather than migrating binary K/V tensors, creating a prefill latency spike proportional to `(prompt_len + generated_len)`.
2. **Stochastic RNG Not Checkpointed:** PyTorch RNG state is not saved across recovery; greedy (`temperature <= 0.05`) and `top_k=1` match uninterrupted baselines, while stochastic runs continue coherently on a different random trajectory.
3. **Remote Hugging Face Revision Not SHA-Pinned:** Remote HF repo IDs in `pipeline_worker.py` do not pin an immutable commit SHA across deploy and cluster re-form.
4. **GGUF Mid-Generation Resume Unsupported After Token 1:** `llama-server` holds KV state internally in C++; failures after `tokens_delivered > 0` terminate explicitly with structured errors rather than resuming.
5. **Synchronous Worker PyTorch Kernel Execution:** Coordinator aborts sockets promptly on timeout/cancel, but an already-running synchronous layer forward pass inside `PipelineWorkerServer` finishes before observing socket closure.
6. **Single Coordinator Failure Domain:** `ResumeState`, `RequestScheduler`, and client HTTP streams live in one Coordinator process; Coordinator crashes terminate active requests.
7. **Sequential Single-Stream Pipeline Execution:** `PipelineCoordinator` serializes generations through `_gen_lock` (one active sequence per pipeline instance, no pipeline micro-batching yet); GGUF uses `llama-server --cont-batching`.
8. **Hardware Detection & Fit Gaps:** `hardware.py` only probes NVIDIA GPUs via `nvidia-smi`; `fit.py` uses fixed bandwidth constants and returns `IMPOSSIBLE` for CPU-only profiles when `ngl == 0`.
9. **Dual Orchestration Surface:** Maintaining both `lan.py` (`/api/lan/*`) and `distributed/cluster.py` (`/api/cluster/*`) creates duplication and user/UX ambiguity if not carefully delineated.
10. **Packaging & Asset Drift:** Root `dashboard.html` is out of sync with `shard_engine/dashboard.html`; `deploy/Dockerfile.*` cannot build without fixing `requirements.txt` and healthcheck endpoints; `tests_phase_i_product_hardening.py::test_06` is coupled to local Hugging Face cache contents on `D:\shard-models`.
11. **Single-Host Windows Verification Boundary:** No physical multi-machine LAN/WAN chaos testing or Linux/macOS CI runtime test execution has been performed yet.

---

## 15. Architecture Decisions That Must Be Preserved

Future engineering work MUST preserve these load-bearing design decisions unless explicitly superseded by an approved RFC:

1. **Explicit Consent & Resource Quotas:** Workers never contribute GPU/RAM/CPU silently; `consent=True` and hard OS memory reserves (`VRAM_RESERVE_MB=400`, `RAM_RESERVE_MB=2048`) must remain strictly enforced.
2. **Strict Role & Owner Isolation:** Remote clients and workers connecting over LAN or encrypted tunnels must never gain Owner privileges or access administrative `/api/*` endpoints.
3. **OpenAI & Anthropic API Compatibility:** Keep `/v1/chat/completions`, `/v1/messages`, and `/v1/models` faithful to wire contracts — especially pre-header HTTP `503`/`504`, structured SSE error frames, clean cancellation (`499` sync, zero fake `stop` or `end_turn`), and zero silent degradation.
4. **Local-First & Offline Resilience:** Local inference, `shard fit`, and LAN pooling must continue working with zero errors when Internet/Discovery/Relay endpoints are unreachable or `offline_only=True`.
5. **Zero-Knowledge Discovery & Blind Relay Invariants:** Discovery servers must only store signed metadata (never inference payloads or secrets); relays must only forward AEAD-encrypted envelopes (never plaintext prompts, tokens, or activations).
6. **Lock Hygiene on Coordinator Hot Paths:** Never perform blocking subprocess (`nvidia-smi`, `llama-server --list-devices`) or network I/O while holding `CLUSTER._lock` (which starves worker heartbeats).
7. **Honest Failure Semantics:** Never fabricate successful completions (`finish_reason: "stop"`, `stop_reason: "end_turn"`, HTTP `200` on cancelled sync) or silently drop from coordinator mode to unrelated local fallback.
8. **Hardware-Aware Partitioning Before Execution:** Always validate memory budgets, architecture support, and communication overhead before deploying a cluster or launching inference.
9. **Atomic Persistence & Secret Redaction:** Keep atomic file writes with restricted permissions (`storage.py`) and recursive secret/prompt sanitization in telemetry/logs (`resilience.py`).
10. **No Speculative Tokenomics:** Do not introduce cryptocurrency, wallets, or blockchain layers into SHARD's core architecture.

---

## 16. Suggested Long-Term Engineering Roadmap

Ordered by reliability, real-world impact, and architectural risk reduction (incremental, testable phases — do not attempt everything simultaneously):

### Phase A — Hygiene, Security & Baseline Integrity (Immediate)
1. Fix fail-open Authenticode check and predictable password derivation in `shard_engine/radmin_mgr.py` (or gate Radmin behind explicit secure checks).
2. Fix the two pre-existing baseline failures cleanly: eliminate root vs. package `dashboard.html` duplication (`test_09`) and make `test_06` hermetically isolated from machine-local `D:\shard-models` cache.
3. Repair `deploy/Dockerfile.discovery`, `deploy/Dockerfile.relay`, and `deploy/Caddyfile` (`pyproject.toml` install instead of missing `requirements.txt`, `/healthz` healthchecks, aligned ports) and verify container builds in CI.
4. Sync CLI version/README version references (`v1.9.0`) and reconcile stale docs (`AUTONOMOUS_INSTALLATION_REPORT.md`, `REAL_WORLD_NETWORK_TEST_REPORT.md`).

### Phase B — Physical Multi-Machine LAN Verification & Hardware Portability
1. Execute real 2-to-4 machine physical LAN tests (Ethernet and Wi-Fi) for both `gguf_rpc_layer_split` and `torch_pipeline_parallel`, benchmarking actual tok/s, stage latency, and mid-generation `SIGKILL`/NIC-disconnect recovery.
2. Add native AMD ROCm (`rocm-smi`), Apple Silicon Metal unified-memory reporting, and CPU-only `fit.py` support (removing the `ngl >= 1` false `IMPOSSIBLE` on CPU-only machines that have ample RAM).
3. Add per-GPU memory bandwidth lookup + empirical link bandwidth probing (`measure_bandwidth_mbps`) into `fit.py` and `planner.py` for more accurate heterogeneous stage splits.
4. Pin Hugging Face commit revision SHAs across cluster deploy and re-form in `pipeline_coordinator.py` / `pipeline_worker.py`.
5. Expand CI in `.github/workflows/tests.yml` to run Linux and macOS runners alongside Windows, plus official `openai`/`anthropic` SDK streaming contract tests.

### Phase C — Control Plane Unification & Pipeline Throughput
1. Define a clean migration path unifying `lan.py` (`/api/lan/*`) and `distributed/cluster.py` (`/api/cluster/*`) behind one underlying worker registry and health FSM while keeping legacy `shard lan` CLI compatibility.
2. Add pipeline concurrency improvements (`torch_pipeline_parallel` batched/pipelined scheduling or async multi-request stage execution) and quantized activation compression (`fp16`/`bf16`/`int8`) for slower links.
3. Add `/v1/completions` if demanded by external toolchains, and harden per-client token/rate quotas across cluster APIs.

### Phase D — Verified Cross-Network & Cooperative Global Network
1. Deploy real, monitored, TLS-secured reference instances of `shard-discovery` and `shard-relay` (only updating public docs and defaults once externally reachable and load-tested).
2. Verify direct P2P STUN/NAT hole-punching and blind-relay fallback across real separate residential/cloud networks behind Cone and Symmetric NATs.
3. Introduce peer reliability/latency scoring into `planner.py` (preferring low-RTT, low-`fail_count` workers for latency-critical early/late stages) and transparent Dashboard observability for network path, stage load, and real-time contribution metrics.
4. Design optional fairness / cooperative contribution policies separately only after cross-network distributed inference is proven reliable in the wild.
