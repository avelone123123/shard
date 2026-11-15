# SHARD Architecture Audit — Phase III: Unified AI Hosting, Private Sessions & Compute

## Executive Summary

This architecture audit establishes the structural baseline for Phase III of the SHARD project. SHARD is a local-first, distributed Large Language Model (LLM) inference runtime written in Python and C++20 with MASM AVX2 SIMD acceleration. Prior phases (Phases A through II) established the core engine, GGUF/llama.cpp RPC orchestration, local network discovery, SOUP fine-tuning adapter, and the Modern Slate dark workstation interface.

This audit evaluates the reality versus advertising of SHARD subsystems, identifies gaps in server hosting, participant privacy, distributed compute, SOUP fine-tuning workflows, PyPI release infrastructure, and GitHub discoverability.

---

## 1. Subsystem Architecture Map

```
+---------------------------------------------------------------------------------+
|                                 USER INTERFACES                                 |
|  +-----------------------------+               +-----------------------------+  |
|  | Web Dashboard (Modern Slate)|               |   SHARD CLI (`shard ...`)   |  |
|  | 7 Core Workstations         |               |   Doctor, Fit, Server, Run  |  |
|  +--------------+--------------+               +--------------+--------------+  |
+-----------------|---------------------------------------------|-----------------+
                  |                                             |
+-----------------v---------------------------------------------v-----------------+
|                        FASTAPI CORE APPLICATION (`server.py`)                   |
|  +------------------+  +-------------------+  +------------------------------+  |
|  | OpenAI/Anthropic |  | Server Management |  | Training & Feedback (SOUP)   |  |
|  | `/v1/*` APIs     |  | `/api/server/*`   |  | `/api/training/*`            |  |
|  +------------------+  +-------------------+  +------------------------------+  |
|  +------------------+  +-------------------+  +------------------------------+  |
|  | Model Catalog    |  | LAN & WireGuard   |  | P2P & Blind Relay            |  |
|  | `/api/models/*`  |  | `/api/lan/*`      |  | `/api/net/*`                 |  |
|  +------------------+  +-------------------+  +------------------------------+  |
+-----------------|------------------|--------------------------|-----------------+
                  |                  |                          |
+-----------------v-------+  +-------v----------------+  +------v-----------------+
|     INFERENCE ENGINE    |  |    NETWORKING LAYER    |  |    TRAINING (SOUP)     |
| +---------------------+ |  | +--------------------+ |  | +--------------------+ |
| | ShardEngine         | |  | | LAN (UDP & RPC)    | |  | | soup_adapter.py    | |
| | (Zero-Copy Core)    | |  | | Subnet Discovery   | |  | | Isolated Py3.12    | |
| +---------------------+ |  | +--------------------+ |  | +--------------------+ |
| +---------------------+ |  | +--------------------+ |  | +--------------------+ |
| | GGUFBackend         | |  | | NetworkManager     | |  | | training.py        | |
| | (llama.cpp server)  | |  | | Blind Relay / P2P  | |  | | Dataset Manifests  | |
| +---------------------+ |  | +--------------------+ |  | +--------------------+ |
| +---------------------+ |  | +--------------------+ |  | +--------------------+ |
| | Native MASM Core    | |  | | HostTunnelAgent    | |  | | feedback.py        | |
| | AVX2 Kernels        | |  | | Encrypted WS       | |  | | Redaction Pipeline | |
| +---------------------+ |  | +--------------------+ |  | +--------------------+ |
+-------------------------+  +------------------------+  +------------------------+
```

---

## 2. Deep Dive Into Core Subsystems

### 2.1 Model Management & Inference (`engine.py`, `gguf_backend.py`, `hf_hub.py`, `model_browser.py`)
- **Implemented & Verified**:
  - GGUF discovery in local directories and Hugging Face Hub cache.
  - Pre-flight fit calculation (`fit.py`) evaluating VRAM, system RAM, context ceiling, and quantization tiers.
  - Managed `llama.cpp` process lifecycle (`gguf_backend.py`) with stdout/stderr capture, port allocation, and health checks.
  - Native C++20 / MASM AVX2 core bridge (`shard_bridge.py`) for SIMD acceleration.
- **Architectural Gaps**:
  - Prior to Phase III, server creation did not enforce that a model was successfully loaded or that it passed an inference test before publishing.
  - Engine status and server status were decoupled: unloading a model in the engine did not downgrade an active server's state to DEGRADED.

### 2.2 Server Hosting & Lifecycle (`server.py`, `lan.py`, `net_strategy.py`, `net_tunnel.py`)
- **Implemented & Verified**:
  - Local LAN UDP broadcast discovery on port 8001.
  - Dynamic local IP detection filtering virtual interfaces and loopback addresses.
  - Encrypted WebSocket relay tunnel (`net_tunnel.py`) with ChaCha20-Poly1305 end-to-end encryption.
- **Architectural Gaps**:
  - Ambiguity between "Server SHARD" page and "Global Network" page: both allowed configuring server names, regions, and access modes without a unified canonical state machine.
  - When public discovery services were unreachable, the system could enter inconsistent states if error reporting was masked.

### 2.3 Participant Privacy & Conversation Isolation (`server.py`, `storage.py`)
- **CRITICAL DEFECT IDENTIFIED**:
  - Previous implementation of `/api/server/workspace` stored all conversations in a single shared list `shared_chats` inside `server_workspace.json`.
  - When participants connected, all participants could view every other participant's prompt and response history.
  - The server owner could see raw participant chat history in the dashboard.
- **Phase III Required Remediation**:
  - Complete architectural separation of private participant chats.
  - Derivation of participant identities strictly from authenticated credentials (owner token, tunnel peer ID, LAN client IP/token hash, or API key hash).
  - Isolated file per participant (`private_chats/{participant_id}.json`).
  - Strict access controls: participant A cannot read, append, or delete participant B's sessions.
  - Server owner diagnostics expose only aggregate metrics (`private_session_count`), never raw prompts or replies.

### 2.4 Distributed Compute & RPC Pooling (`lan.py`, `gguf_backend.py`, `net_tunnel.py`)
- **Reality vs Marketing Assessment**:
  - **LAN RPC Pooling**: GENUINE. `lan.py` starts `rpc-server` processes on worker nodes, computes tensor-split weights based on available VRAM/RAM, and passes `--rpc` to `llama-server`.
  - **Global WAN Pooling**: EXPERIMENTAL. Transporting raw RPC traffic over high-latency WAN or lossy relays causes severe stalls and timeouts in `llama.cpp`. Encrypted HTTP tunneling allows remote inference consumption, but not transparent WAN layer sharding.
- **Phase III Architectural Decision**:
  - Separate participant roles: `OWNER`, `COMPUTE_WORKER`, and `INFERENCE_CLIENT`.
  - Joining a server does NOT automatically donate GPU or RAM. Compute contribution requires explicit opt-in consent and provides quota controls and immediate revocation.
  - WAN compute pooling is explicitly labeled experimental; LAN pooling is the production-ready distributed path.

### 2.5 SOUP Fine-Tuning Integration (`training.py`, `soup_adapter.py`, `feedback.py`)
- **Implemented & Verified**:
  - Isolated Python 3.12 virtual environment provisioning via `uv` or system Python.
  - SFT and DPO dataset compilation from explicit user feedback with PII and secret redaction.
  - Training orchestration, preflight verification, LoRA fine-tuning execution, and model version registry.
- **Identified Gaps**:
  - Installer progress was previously hardcoded at 55% during package installation, causing users to believe the installation had frozen.
  - Users had to manually input raw Hugging Face model IDs instead of selecting from their running SHARD servers.
  - GGUF quantization files were not automatically mapped back to their Hugging Face source repositories for training base model resolution.

### 2.6 Packaging & PyPI Release Infrastructure (`pyproject.toml`, `.github/workflows/`)
- **Reality Assessment**:
  - `shard-engine` was not published on live PyPI (HTTP 404 on JSON and Simple index).
  - No dedicated GitHub Actions publication workflow existed in `.github/workflows/`.
  - README instructed users to run `pip install shard-engine`, which failed for new users.
- **Phase III Required Remediation**:
  - Create `.github/workflows/publish.yml` using PyPI Trusted Publishing (OIDC).
  - Update README with reliable Git installation instructions (`pip install git+https://github.com/...`) until live PyPI release.
  - Provide complete step-by-step instructions for PyPI Trusted Publisher configuration.

---

## 3. Subsystem Verification & Readiness Matrix

| Subsystem | Component | Implementation Status | Test Coverage | Multi-Process Ready | Multi-Machine Verified | Notes |
|---|---|---|---|---|---|---|
| Inference | GGUF Engine | IMPLEMENTED | UNIT TESTED | YES | YES (LAN) | llama.cpp managed subprocess |
| Inference | Native MASM AVX2 | IMPLEMENTED | UNIT TESTED | YES | N/A | ctypes bridge, fallback to pure Python |
| Hosting | Model-First Guard | IMPLEMENTED (Phase III) | INTEGRATION TESTED | YES | YES | Rejects unready models (HTTP 409) |
| Hosting | Unified Server State | IMPLEMENTED (Phase III) | INTEGRATION TESTED | YES | YES | Shared state between LAN and Network |
| Privacy | Participant Isolation | IMPLEMENTED (Phase III) | INTEGRATION TESTED | YES | YES | Scoped private session storage |
| Network | LAN UDP Discovery | IMPLEMENTED | UNIT TESTED | YES | YES | Port 8001 beacons, dynamic subnet detection |
| Network | P2P & Relay Tunnel | IMPLEMENTED | INTEGRATION TESTED | YES | NO (1 machine test) | ChaCha20-Poly1305 end-to-end encryption |
| Compute | LAN RPC Pooling | IMPLEMENTED | UNIT TESTED | YES | YES (LAN) | llama.cpp `--rpc` tensor splitting |
| Compute | Global WAN Pooling | EXPERIMENTAL | PARTIALLY TESTED | NO | NO | Labeled experimental, roadmap defined |
| SOUP | Environment Provision | IMPLEMENTED | UNIT TESTED | YES | YES | Isolated Python 3.12 venv via uv |
