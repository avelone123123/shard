# Automatic Worker Join & Cluster Onboarding Report

## 1. Executive Summary

The One-Click Cluster Join architecture allows any computer on the network to participate in a distributed AI cluster. Users can join as a pure AI Consumer ("Option A") or contribute their GPU and CPU hardware as a Worker Node ("Option B"). Joining as a worker involves pre-join capability analysis, explicit user consent enforcement, automatic partition scheduling, and encrypted pipeline connection.

```
+--------------------------------------------------------------------------+
|                     1-Click Cluster Onboarding Flow                      |
+--------------------------------------------------------------------------+
  User clicks "Join" in Catalog
            |
            v
  +----------------------+
  | Modal Choice Dialog  |
  +----------+-----------+
             |
             +---> Option A: [Use AI as Client]
             |        |
             |        v
             |     Instant tunnel connection established;
             |     Ready to stream prompts via OpenAI API / UI.
             |
             +---> Option B: [Contribute Computing Resources]
                      |
                      v
             1. Pre-Join Hardware Capability Analysis
                (GET /api/catalog/prejoin/{node_id})
                - Probes GPU VRAM, System RAM, CPU threads, CUDA/GGUF
                - Calculates estimated layer shard size
                - Displays compatibility badge & recommendations
                      |
                      v
             2. Quota Tuning & Consent Enforcement
                - VRAM slider (default 70%)
                - CPU threads allocation slider
                - [x] Explicit consent checkbox (MANDATORY)
                      |
                      v
             3. Handshake & Layer Partitioning
                - POST /api/catalog/join (mode=worker, consent=true)
                - Coordinator assigns layer stages (e.g. Layers 16..32)
                - Worker downloads or maps safetensors/GGUF shards
                      |
                      v
             4. Active Distributed Execution
                - Enters Cluster State: READY
                - Bidirectional heartbeats every 10 seconds
                - Exchanges intermediate activation tensors over sockets
```

## 2. Pre-Join Hardware Capability Analysis (`/api/catalog/prejoin/{node_id}`)

Before any worker connects, the system performs a non-invasive local hardware audit:

- **GPU & VRAM**: Inspects NVIDIA NVML / DirectML / Metal to detect GPU name and dedicated VRAM.
- **System Memory**: Probes available and total system RAM.
- **CPU Cores & Threads**: Probes logical and physical cores.
- **Backend Compatibility**:
  - `torch_pipeline`: Verifies PyTorch runtime, CUDA/ROCm acceleration, and safetensors support.
  - `gguf_rpc`: Checks availability of llama-box RPC server binary.
- **Recommendation Engine**:
  - Automatically recommends safe allocation limits (e.g. 70% of available VRAM).
  - Emits compatibility warnings if available memory is insufficient for the target model.

## 3. Explicit User Consent Guarantee

Worker resource donation is strictly opt-in:

- Joining with `mode="worker"` and `consent=false` is rejected at the API level with HTTP 403 `CONSENT_REQUIRED`.
- The dashboard UI disables the "Start Contributing" button until the user explicitly checks the confirmation checkbox.
- A worker node may detach at any time via `shard cluster leave` or by clicking "Disconnect Worker" in the dashboard, immediately terminating tensor socket listeners.
