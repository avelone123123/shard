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
