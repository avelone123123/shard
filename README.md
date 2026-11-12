<p align="center">
  <strong>English</strong> &nbsp;|&nbsp;
  <a href="README.ru.md">Русская версия</a> &nbsp;|&nbsp;
  <a href="README.kz.md">Қазақша нұсқасы</a>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/branding/shard-logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/branding/shard-logo-light.svg">
    <img src="assets/branding/shard-logo.svg" alt="SHARD — Distributed Local LLM Inference Engine" width="760">
  </picture>
</p>

<h1 align="center">SHARD</h1>

<p align="center">
  <strong>One Network. Many Servers. Shared AI Infrastructure.</strong><br>
  <sub>Distributed local LLM inference engine pooling GPU VRAM and system RAM across LAN machines — powered by PyTorch, FastAPI, <code>llama.cpp</code> RPC, and native C++20 / MASM AVX2 SIMD kernels.</sub>
</p>

<p align="center">
  <a href="https://avelone123123.github.io/shard/"><img src="https://img.shields.io/badge/WEBSITE-Live-00E5FF?style=for-the-badge&labelColor=0A1022" alt="Website"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/LANG-English-00E5FF?style=for-the-badge&labelColor=0A1022" alt="English README"></a>
  <a href="README.ru.md"><img src="https://img.shields.io/badge/ЯЗЫК-Русский-38BDF8?style=for-the-badge&labelColor=0A1022" alt="Русская версия"></a>
  <a href="README.kz.md"><img src="https://img.shields.io/badge/ТІЛ-Қазақша-A855F7?style=for-the-badge&labelColor=0A1022" alt="Қазақша версия"></a>
  <a href="docs/LAN_SHARDING_DESIGN.md"><img src="https://img.shields.io/badge/DOCS-LAN_Sharding-34D399?style=for-the-badge&labelColor=0A1022" alt="Documentation"></a>
  <a href="#installation--quick-start"><img src="https://img.shields.io/badge/START-Quick_Start-00E5FF?style=for-the-badge&labelColor=0A1022" alt="Getting Started"></a>
  <a href="#installation"><img src="https://img.shields.io/badge/SETUP-Installation-38BDF8?style=for-the-badge&labelColor=0A1022" alt="Installation"></a>
  <a href="https://github.com/avelone123123/shard/discussions"><img src="https://img.shields.io/badge/COMMUNITY-Discussions-A855F7?style=for-the-badge&labelColor=0A1022" alt="GitHub Discussions"></a>
  <a href="https://github.com/avelone123123/shard/issues"><img src="https://img.shields.io/badge/TRACKER-Issues-F59E0B?style=for-the-badge&labelColor=0A1022" alt="Issues"></a>
  <a href="https://github.com/avelone123123/shard/releases"><img src="https://img.shields.io/github/downloads/avelone123123/shard/total?style=for-the-badge&labelColor=0A1022&color=34D399&label=DOWNLOADS" alt="Total Downloads"></a>
</p>

<p align="center">
  <a href="https://github.com/avelone123123/shard/releases"><img src="https://img.shields.io/github/v/release/avelone123123/shard?style=flat-square&labelColor=0D1326&color=00E5FF&label=release" alt="Latest Release"></a>
  <a href="https://github.com/avelone123123/shard/releases/latest"><img src="https://img.shields.io/github/downloads/avelone123123/shard/latest/total?style=flat-square&labelColor=0D1326&color=34D399&label=latest%20downloads&logo=github&logoColor=white" alt="Latest Release Downloads"></a>
  <a href="https://github.com/avelone123123/shard/stargazers"><img src="https://img.shields.io/github/stars/avelone123123/shard?style=flat-square&labelColor=0D1326&color=F59E0B&label=stars&logo=github&logoColor=white" alt="GitHub Stars"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.8%2B%20%7C%203.10%2B-38BDF8?style=flat-square&labelColor=0D1326&logo=python&logoColor=white" alt="Python 3.8+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-34D399?style=flat-square&labelColor=0D1326" alt="License: MIT"></a>
  <a href="https://github.com/avelone123123/shard/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/avelone123123/shard/tests.yml?branch=master&style=flat-square&labelColor=0D1326&label=tests" alt="Tests CI"></a>
  <img src="https://img.shields.io/badge/backend-PyTorch%20%7C%20GGUF%20%7C%20AVX2-A855F7?style=flat-square&labelColor=0D1326" alt="Backends">
  <img src="https://img.shields.io/badge/API-OpenAI%20%26%20Anthropic-00E5FF?style=flat-square&labelColor=0D1326" alt="OpenAI and Anthropic API">
</p>

<p align="center">
  <img src="assets/animations/hero-network.gif" alt="SHARD Hero Network Animation — Distributed AI Inference Cluster" width="800">
  <br>
  <sub><b>SHARD Distributed Inference Cluster:</b> Central master coordinator pooling VRAM across connected LAN worker nodes over UDP discovery (<code>:8001</code>) and FastAPI SSE endpoints (<code>:8000</code>).</sub>
</p>

<p align="center">
  <a href="#what-is-shard">Overview</a> &middot;
  <a href="#how-shard-works">How It Works</a> &middot;
  <a href="#features">Features</a> &middot;
  <a href="#installation--quick-start">Quick Start</a> &middot;
  <a href="#cli-reference--configuration">CLI Reference</a> &middot;
  <a href="#architecture">Architecture</a> &middot;
  <a href="#web-dashboard--demonstrations">Dashboard</a> &middot;
  <a href="#project-status--community">Community</a>
</p>

---

## What Is SHARD?

**[SHARD](https://github.com/avelone123123/shard)** (`shard-engine`) is an open-source distributed local LLM inference engine written in Python ([FastAPI](https://fastapi.tiangolo.com/) + [PyTorch](https://pytorch.org/)) with a [GGUF / `llama.cpp` backend](https://github.com/ggml-org/llama.cpp) and native C++20 / MASM AVX2 SIMD acceleration.

### The Problem It Solves
Running capable 14B, 32B, or 70B+ open-weight language models locally typically hits a hard hardware wall: **single-card VRAM limits**. Most developers, labs, and small teams own multiple machines—a desktop gaming GPU, a workstation laptop, an auxiliary mini-PC—whose memory sits fragmented across the local network while no single device can fit the full model weights and KV cache.

### Why SHARD Exists
SHARD unites idle GPUs and system memory across your Local Area Network (LAN) into a single logical inference cluster:
- **Pool Hardware Without Cloud Lock-In:** Split transformer layers across multiple consumer GPUs and host RAM over standard gigabit LAN (`lan.py`, `gguf_backend.py`) with zero external cloud dependencies or recurring API fees.
- **Know Before You Download (`shard fit`):** Inspect whether any Hugging Face model will fit your single GPU, hybrid CPU+GPU setup, or pooled LAN cluster before downloading gigabytes of weights (`fit.py`).
- **Drop-In Agent & IDE Backend:** Expose both OpenAI (`/v1/chat/completions`, `/v1/completions`, `/v1/models`) and Anthropic (`/v1/messages`) compatible endpoints on port `8000` so coding agents and SDKs connect immediately.

### Who Should Use SHARD?
- **Local AI Developers & Researchers** running 7B–70B GGUF or PyTorch SafeTensors models across home lab or office workstations.
- **Software Engineering Teams** hosting private, zero-telemetry coding assistants for [Cursor](https://www.cursor.com/), [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview), [Aider](https://aider.chat/), [Continue](https://www.continue.dev/), [Zed](https://zed.dev/), and [OpenCode](https://github.com/opencode-ai/opencode).
- **AI Agent Builders** orchestrating multi-agent pipelines via [OpenAI Python SDK](https://github.com/openai/openai-python), [LangChain](https://www.langchain.com/), or [LlamaIndex](https://www.llamaindex.ai/) against self-hosted hardware.

---

## How SHARD Works

SHARD turns distributed local AI infrastructure into a four-step workflow: **CREATE → SHARE → CONNECT → USE**.

<table>
  <tr>
    <td width="50%" valign="top">
      <h3 align="center">1. CREATE — Launch Your Server</h3>
      <p align="center">
        <img src="assets/animations/create-server.gif" alt="Step 1: Create and Launch Your SHARD Server" width="100%">
      </p>
      <p>
        Start a local SHARD node with <code>shard start</code> or <code>python main.py</code>. The engine probes your CPU, system RAM, and NVIDIA CUDA / Apple MPS GPU via <code>hardware.py</code>, loads your chosen GGUF or Hugging Face model, and brings up the FastAPI server and Web UI on <code>http://localhost:8000</code>.
      </p>
    </td>
    <td width="50%" valign="top">
      <h3 align="center">2. SHARE — Broadcast to the LAN</h3>
      <p align="center">
        <img src="assets/animations/share-network.gif" alt="Step 2: Share Server With the LAN Network" width="100%">
      </p>
      <p>
        Open a LAN compute pool with <code>shard lan create --open</code> (or generate a pairing code via <code>shard lan create</code>). SHARD broadcasts UDP discovery beacons on port <code>8001</code> and exposes <code>/api/lan/*</code> endpoints so peer machines on your subnet can pool their VRAM and RAM.
      </p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3 align="center">3. CONNECT — Pair Worker Nodes</h3>
      <p align="center">
        <img src="assets/animations/connect-server.gif" alt="Step 3: Connect and Pair to a SHARD Server" width="100%">
      </p>
      <p>
        On any secondary computer on the network, run <code>shard lan join &lt;master-ip&gt; --share</code>. The worker completes an HTTP handshake (<code>POST /api/lan/pair</code>), launches its local RPC tensor worker, sends periodic health heartbeats, and contributes its GPU/RAM to the cluster.
      </p>
    </td>
    <td width="50%" valign="top">
      <h3 align="center">4. USE — Power AI Agents & Apps</h3>
      <p align="center">
        <img src="assets/animations/ai-agents.gif" alt="Step 4: Host and Access AI Agents Through SHARD" width="100%">
      </p>
      <p>
        Connect coding IDEs, autonomous AI agents, or custom Python apps to <code>http://&lt;master-ip&gt;:8000/v1/chat/completions</code> or <code>/v1/messages</code>. Master coordinates layer execution across connected nodes and streams tokens back via Server-Sent Events (SSE).
      </p>
    </td>
  </tr>
</table>

---

## Features

Every feature listed below is verified directly against the [SHARD source tree](https://github.com/avelone123123/shard) (`v1.8.0`).

### Implemented Features

| Category | Capability | Source Module | Description |
| :--- | :--- | :--- | :--- |
| **LAN Cluster & Sharding** | **UDP Auto-Discovery & LAN Pooling** | [`lan.py`](lan.py), [`docs/LAN_SHARDING_DESIGN.md`](docs/LAN_SHARDING_DESIGN.md) | Broadcasts UDP beacons (`:8001`), pairs nodes via code or `--open` mode, monitors node heartbeats (`/api/lan/heartbeat`), and pools VRAM/RAM across machines. |
| **Inference Backends** | **Dual PyTorch & GGUF (`llama.cpp`) Engine** | [`engine.py`](engine.py), [`gguf_backend.py`](gguf_backend.py) | Runs Hugging Face `transformers` / `safetensors` models on CUDA/CPU/MPS and quantized GGUF weights via managed `llama-server` & `ggml-rpc-server` subprocesses with configurable GPU layer offload (`-ngl`). |
| **Hardware Planning** | **Pre-Flight Model Fit Advisor (`shard fit`)** | [`fit.py`](fit.py), [`hardware.py`](hardware.py) | Inspects model architecture parameters (`config.json` or parameter heuristics) against host VRAM and RAM before downloading, returning `GPU`, `HYBRID`, or `IMPOSSIBLE` verdicts, max context length, estimated tok/s, and quantization advice. |
| **Native Acceleration** | **C++20 & MASM AVX2 SIMD Kernels** | [`native/shard_core.cpp`](native/shard_core.cpp), [`native/shard_asm.asm`](native/shard_asm.asm), [`shard_bridge.py`](shard_bridge.py) | Custom C++20 and x86-64 AVX2 assembly routines for `RMSNorm`, `LayerNorm`, Rotary Position Embeddings (`RoPE`), `Softmax`, `GELU`, `SiLU`, and `MatMul` loaded via `ctypes`. |
| **API & Streaming** | **OpenAI & Anthropic Compatible Server** | [`server.py`](server.py) | Serves `/v1/chat/completions`, `/v1/completions`, `/v1/models`, `/v1/messages` (Anthropic protocol), `/api/generate`, and `/api/generate/stream` with real-time Server-Sent Events (SSE) and optional API key auth (`shard key`). |
| **Web Control Panel** | **Zero-Dependency Dashboard & Playground** | [`dashboard.html`](dashboard.html) | Built-in browser UI at `http://localhost:8000` with live GPU/VRAM/RAM telemetry, Hugging Face model search & download, quantization selector, streaming chat playground, LAN node manager, and one-click IDE integration snippets. |
| **Containerization** | **Docker & NVIDIA GPU Compose Stack** | [`Dockerfile`](Dockerfile), [`docker-compose.yml`](docker-compose.yml) | Production-ready container setup with persistent named volumes (`shard_models_volume`, `shard_data_volume`) and NVIDIA GPU reservation. |

### Experimental Features
- **Multi-Host `llama.cpp` RPC Tensor Distribution (`gguf_backend.py`, `lan.py`):** Distributed layer execution over TCP/RPC across heterogeneous LAN GPUs works best on low-latency wired Gigabit/2.5GbE Ethernet subnets; Wi-Fi links may experience higher inter-token latency.
- **Native Win32 Desktop GUI (`native/shard_gui.cpp`):** Native C++ Windows desktop wrapper alongside the cross-platform web dashboard (`dashboard.html`).

### Planned Features
- **Automated Dynamic Re-Sharding on Node Dropout:** Seamless mid-generation layer migration if a worker node disconnects unexpectedly (`docs/LAN_SHARDING_DESIGN.md`).
- **Linux / macOS ARM64 NEON Intrinsics:** Expanding the native SIMD kernel suite (`native/`) beyond x86-64 AVX2/FMA to native Apple Silicon / ARM64 assembly paths.

---

## Installation & Quick Start

### Prerequisites
- **OS:** Windows 10/11 (64-bit), Linux (Ubuntu 20.04+ / Debian 12+), or macOS.
- **Python:** Python `3.8+` (`3.10+` recommended).
- **Hardware:** NVIDIA GPU with CUDA drivers (recommended for GPU acceleration), Apple Silicon (MPS), or x86-64 CPU with AVX2 support.
- **Network (Optional for LAN Sharding):** Two or more machines on the same LAN with TCP port `8000` (HTTP/API) and UDP/TCP port `8001` (LAN discovery & RPC) open.

### Installation

#### Option 1: Install via pip (Recommended)
Run SHARD from any directory without cloning the repository:
```bash
# Core install: CLI, FastAPI server, web dashboard, GGUF (llama.cpp) + LAN clustering
pip install shard-engine

# Full install: adds PyTorch/Transformers safetensors & Hugging Face extras
pip install "shard-engine[full]"
```
Verify your environment right after installation:
```bash
shard doctor
```
> **GPU & Runtime Note:** GGUF inference auto-downloads platform binaries for `llama-server` / `ggml-rpc-server` on first run and needs no extra ML packages. For CUDA PyTorch inference, install a matching CUDA build of `torch` before `pip install "shard-engine[full]"`.

#### Option 2: Editable Install from Source (Developers)
```bash
git clone https://github.com/avelone123123/shard.git
cd shard
pip install -e ".[dev]"
```

#### Option 3: Docker Compose (with NVIDIA GPU Support)
```bash
git clone https://github.com/avelone123123/shard.git
cd shard
docker-compose up --build
```
This builds `shard-engine`, mounts persistent volumes (`/data/models` and `/data/shard`), exposes port `8000:8000`, and enables NVIDIA GPU passthrough.

#### Option 4: Compile Native C++20 / MASM AVX2 Kernels (Optional Speedup)
To compile `shard_core.dll` (Windows MSVC `cl.exe` + `ml64.exe`) or `shard_core.so` (Linux GCC/Clang):
```bash
python native/build_native.py
```

#### Upgrading & Uninstalling
```bash
shard update --check
pip install --upgrade shard-engine
pip uninstall shard-engine
```
Uninstalling the Python package keeps your downloaded models, settings, and API keys intact in your user data directory.

---

### Quick Start Workflow

#### 1. Verify Environment & Launch SHARD
```bash
# Check Python runtime, dependencies, GPU VRAM, ports, and writable paths
shard doctor

# Start FastAPI server in background (--daemon / -d) and inspect logs/status
shard start --daemon
shard status

# Or open the interactive Web Dashboard at http://localhost:8000 immediately
shard gui

# Stop or restart background server cleanly
shard restart
shard stop
```

#### 2. Check Hardware & Model Fit Before Downloading
```bash
# Check if a model fits your local machine or LAN cluster before downloading
shard fit Qwen/Qwen2.5-Coder-7B-Instruct
shard fit meta-llama/Llama-3.1-8B-Instruct
```

#### 3. Download & Run a Model
```bash
# Download and start an interactive terminal chat (default: Qwen/Qwen2.5-Coder-1.5B-Instruct)
shard run Qwen/Qwen2.5-Coder-7B-Instruct

# List cached and catalog models
shard list
```

#### 4. Create or Join a Distributed LAN Cluster
On the **Master Machine** (`shard lan create` prints the live LAN IPv4 addresses detected on this host):
```bash
# Create an open LAN pool (auto-accepts subnet workers and sets target model)
shard lan create --open --share --model Qwen/Qwen2.5-Coder-7B-Instruct

# Or create a code-protected LAN pool
shard lan create --share
```

On each **Worker Machine** on the same subnet, replace `<SERVER_IP>` with the IP printed by `shard lan create` on the Master Machine:
```bash
# Join an open LAN pool and contribute local GPU/RAM
shard lan join <SERVER_IP> --share

# Or join a code-protected pool
shard lan join <SERVER_IP> <PAIR_CODE> --share

# Inspect cluster nodes, host IPv4 addresses, and pooled VRAM/RAM
shard lan status

# Leave or disable LAN pooling
shard lan off
```

---

## CLI Reference & Configuration

### Verified CLI Commands (`shard_engine/cli.py`)

| Command | Arguments & Flags | Description |
| :--- | :--- | :--- |
| `shard start` | `[--host HOST] [--port PORT] [-d\|--daemon] [--no-browser]` | Launches the SHARD FastAPI server and Uvicorn runtime (default `0.0.0.0:8000`). |
| `shard stop` | *(none)* | Safely stops the background SHARD server managed by PID tracking. |
| `shard restart` | `[--host HOST] [--port PORT] [--foreground] [--no-browser]` | Stops and restarts the SHARD server cleanly. |
| `shard gui` | *(none)* | Launches the server (or native Win32 desktop UI if present) and opens the Web Dashboard. |
| `shard status` | *(none)* | Displays detected CPU, RAM, GPU VRAM, active model, and detected LAN IPv4 addresses. |
| `shard doctor` | *(none)* | Audits Python, packaged assets, dependencies, CUDA/GPU, LAN IPs, ports, and user directories. |
| `shard version` | `[-V\|--version]` | Prints installed SHARD version, Python executable path, OS info, and active directories. |
| `shard update` | `[--check]` | Checks PyPI for newer `shard-engine` releases or upgrades non-destructively via `pip`. |
| `shard fit` | `<model_id> [--ctx N] [--no-fetch]` | Calculates VRAM/RAM requirements, quantization fit, and estimated throughput before downloading. |
| `shard run` | `[model_id]` | Downloads (if needed), loads the model, and opens interactive terminal chat REPL. |
| `shard list` | `[--category CAT]` | Lists cached and curated catalog models (`all`, `ideal`, `coding`, `reasoning`, `compact`). |
| `shard lan` | `[status\|create\|join\|model\|off] [extra] [--share] [--open] [--model ID] [--port N]` | Creates, joins, configures, inspects, or stops distributed LAN compute pooling across machines. |
| `shard key` | `[--gen]` | Views the current local API key or generates a new token (`--gen`). |
| `shard models-dir` | `[path \| reset]` | Views, updates, or resets the directory where model weights are stored. |
| `shard test` | `[--strict-native]` | Verifies tensor math kernels and AVX2/pure-Python fallback throughput. |
| `shard help` | `[-h\|--help]` | Prints full CLI command usage and examples. |

### Cross-Platform Directories & Environment Variables (`shard_engine/paths.py`)

SHARD stores user data outside `site-packages` following OS conventions (and reads existing `~/.shard` data cleanly on upgrade):
- **Windows:** `%LOCALAPPDATA%\SHARD` (config, `keys_db.json`, `logs/`, `run/`, `models/`)
- **Linux:** `~/.local/share/shard`, `~/.config/shard`, `~/.cache/shard`, `~/.local/state/shard` (respects `XDG_*` variables)
- **macOS:** `~/Library/Application Support/SHARD`, `~/Library/Caches/SHARD`, `~/Library/Logs/SHARD`

| Variable / File | Default | Purpose |
| :--- | :--- | :--- |
| `SHARD_HOST` | `0.0.0.0` | Network interface IP address bound by `server.py`. |
| `SHARD_PORT` | `8000` | HTTP port for the API server and Web Dashboard. |
| `SHARD_DATA_DIR` | OS data directory (`/data/shard` in Docker) | Overrides root directory for `settings.json`, `keys_db.json`, `lan.json`, logs, and PID metadata. |
| `SHARD_MODELS_DIR` | `<data_dir>/models` (`/data/models` in Docker) | Storage directory for downloaded Hugging Face SafeTensors and GGUF weights. |
| `SHARD_NO_BROWSER` / `SHARD_DOCKER` | *(unset)* | Suppresses auto-opening a browser tab when starting SHARD. |
| `SHARD_NATIVE_LIB` | *(auto-discovered)* | Path override to `shard_core.dll`/`.so`/`.dylib` (set `none` to force Python fallback). |
| `HF_HOME` / `TRANSFORMERS_CACHE` | `~/.cache/huggingface` | Standard Hugging Face cache override respected by `engine.py` and `hf_hub.py`. |
| `engine_optimizations.json` | Packaged inside `shard_engine` | Runtime optimization profile loaded via `importlib.resources`. |

---

## Architecture

<p align="center">
  <img src="assets/animations/network-ecosystem.gif" alt="SHARD Network Ecosystem Overview" width="800">
  <br>
  <sub><b>SHARD Ecosystem Topology:</b> Coding IDEs, agent frameworks, master coordinator, and distributed LAN worker nodes operating as a unified inference fabric.</sub>
</p>

### System Architecture Diagram

```mermaid
flowchart TB
    subgraph Clients["AI Agents, IDEs & SDK Clients"]
        IDE["Cursor / Claude Code / Aider / Continue / Zed"]
        SDK["OpenAI SDK / Anthropic SDK / LangChain / LlamaIndex"]
        Browser["Web Dashboard UI (dashboard.html)"]
    end

    subgraph Master["SHARD Master Node (FastAPI :8000)"]
        Server["server.py\n(/v1/chat/completions, /v1/messages, /api/*)"]
        Fit["fit.py & hardware.py\n(Pre-Flight VRAM/RAM Advisor)"]
        Engine["engine.py\n(PyTorch Transformers & Token Streamer)"]
        GGUF["gguf_backend.py\n(llama-server & Layer Offload -ngl)"]
        Bridge["shard_bridge.py\n(ctypes C++20 / MASM AVX2 SIMD Core)"]
        LANCoord["lan.py\n(UDP Discovery :8001 & Cluster Coordinator)"]
    end

    subgraph Workers["Distributed LAN Worker Nodes (:8001)"]
        W1["Worker Node 1\nggml-rpc-server / GPU VRAM Shard"]
        W2["Worker Node 2\nggml-rpc-server / GPU VRAM Shard"]
        W3["Worker Node N\nggml-rpc-server / System RAM + GPU"]
    end

    IDE -->|HTTP / SSE :8000| Server
    SDK -->|HTTP / SSE :8000| Server
    Browser -->|REST / SSE :8000| Server

    Server --> Fit
    Server --> Engine
    Server --> LANCoord
    Engine --> GGUF
    Engine --> Bridge

    LANCoord <-->|UDP Broadcast & Heartbeat :8001| W1
    LANCoord <-->|UDP Broadcast & Heartbeat :8001| W2
    LANCoord <-->|UDP Broadcast & Heartbeat :8001| W3

    GGUF <-->|TCP Tensor RPC Stream| W1
    GGUF <-->|TCP Tensor RPC Stream| W2
    GGUF <-->|TCP Tensor RPC Stream| W3
```

### Core Repository Modules
```text
shard/
├── assets/
│   ├── branding/              # Official SHARD SVG & PNG logos, icons, and favicon
│   ├── animations/            # Isometric pixel-art / voxel animated GIFs
│   └── screenshots/           # Dashboard and CLI fit advisor visuals
├── docs/
│   └── LAN_SHARDING_DESIGN.md # Distributed LAN sharding protocol & architecture spec
├── native/
│   ├── build_native.py        # Cross-platform compiler for C++20 & MASM AVX2 kernels
│   ├── shard_core.cpp         # C++20 RMSNorm, LayerNorm, RoPE, Softmax, GELU, SiLU, MatMul
│   ├── shard_asm.asm          # x86-64 MASM AVX2 SIMD assembly kernels
│   └── shard_gui.cpp          # Native Win32 desktop GUI wrapper
├── shard_engine/                # Installable Python package (paths, process, CLI, API, inference)
│   ├── paths.py                 # Cross-platform XDG / AppData / macOS paths & packaged assets
│   ├── process.py               # Detached daemon launcher & verified PID lifecycle management
│   ├── cli.py                   # `shard` command-line interface entry point (`shard_engine.cli:main`)
│   ├── main.py                  # Application launcher & Uvicorn bootstrapper
│   ├── server.py                # FastAPI server (OpenAI, Anthropic, Model & LAN routes)
│   ├── engine.py                # Core LLM inference orchestrator & SSE generator
│   ├── gguf_backend.py          # Managed llama.cpp / GGUF server & RPC integration
│   ├── lan.py                   # Dynamic subnet IP discovery, UDP beacons & LAN pooling
│   ├── fit.py                   # Pre-flight model memory & throughput calculator
│   ├── hardware.py              # GPU VRAM (nvidia-smi), CPU & RAM probing
│   ├── hf_hub.py                # Hugging Face Hub search, metadata & downloader
│   ├── shard_bridge.py          # Python ctypes bridge to native shard_core.dll / .so
│   ├── settings.py              # Persistent configuration manager
│   └── dashboard.html           # Packaged web control panel & agent config generator
├── pyproject.toml               # Single-source packaging configuration (version from __init__.py)
├── tests_shard.py               # Unit, API, fit advisor, LAN sharding & kernel verification suite
└── tests_cli_pkg.py             # Packaging, OS path, CLI, PID safety & 2-node LAN cluster suite
```

---

## Web Dashboard & Demonstrations

### Interactive Web Control Panel (`dashboard.html`)
Navigating to `http://localhost:8000` opens the built-in SHARD Control Panel:

<p align="center">
  <img src="assets/screenshots/dashboard-overview.png" alt="SHARD Web Dashboard & LAN Pool Telemetry" width="960">
  <br>
  <sub><b>SHARD Web Dashboard:</b> Real-time per-node VRAM/RAM gauges, Pre-Flight Model Fit Advisor, one-click IDE/agent config snippets, and streaming playground.</sub>
</p>

### CLI Pre-Flight Advisor (`shard fit`) & LAN Cluster Status (`shard lan status`)

<p align="center">
  <img src="assets/screenshots/cli-fit-advisor.png" alt="SHARD CLI Fit Advisor and LAN Status" width="900">
  <br>
  <sub><b>Pre-Flight Fit Verification:</b> Evaluate model quantization, context ceiling, and pooled LAN cluster fit before downloading model weights.</sub>
</p>

### Connecting AI Agents & Coding Tools
Point any OpenAI- or Anthropic-compatible client to your SHARD instance:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="shard-local",  # Or generate a token via `shard key --gen`
)

stream = client.chat.completions.create(
    model="Qwen/Qwen2.5-Coder-7B-Instruct",
    messages=[{"role": "user", "content": "Explain tensor parallelism across LAN nodes."}],
    stream=True,
)

for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

---

## Project Status & Community

### Technology Stack
- **Runtime & API:** Python 3.8+ / 3.10+, [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/), [Pydantic v2](https://docs.pydantic.dev/)
- **ML & Quantization:** [PyTorch](https://pytorch.org/), Hugging Face [`transformers`](https://huggingface.co/docs/transformers), [`accelerate`](https://huggingface.co/docs/accelerate), [`safetensors`](https://huggingface.co/docs/safetensors), [`huggingface_hub`](https://huggingface.co/docs/huggingface_hub), [`llama.cpp` GGUF](https://github.com/ggml-org/llama.cpp)
- **Native Acceleration:** C++20 (`native/shard_core.cpp`) + x86-64 MASM AVX2 Assembly (`native/shard_asm.asm`)
- **System & Networking:** `psutil`, UDP broadcast discovery (`:8001`), HTTP/SSE streaming (`:8000`)

### Community, Contribution & Governance
- **Architecture & LAN Design Spec:** [`docs/LAN_SHARDING_DESIGN.md`](docs/LAN_SHARDING_DESIGN.md)
- **Contribution Guidelines:** [`CONTRIBUTING.md`](CONTRIBUTING.md) — covers zero-copy memory rules, native C++20/AVX2 build steps, and running `shard test`.
- **Code of Conduct:** [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
- **Release History:** [`CHANGELOG.md`](CHANGELOG.md) (current release: `v1.8.0`)
- **Bug Reports & Feature Requests:** [GitHub Issues](https://github.com/avelone123123/shard/issues)
- **Community Q&A & Ideas:** [GitHub Discussions](https://github.com/avelone123123/shard/discussions)
- **Security Policy:** Please report security vulnerabilities privately via [GitHub Security Advisories](https://github.com/avelone123123/shard/security) or by contacting the maintainer before public disclosure.
- **License:** Distributed under the [MIT License](LICENSE).

---

<p align="center">
  <img src="assets/branding/shard-icon.svg" alt="SHARD Icon" width="64"><br>
  <strong>SHARD — One Network. Many Servers. Shared AI Infrastructure.</strong><br>
  <sub>Built for the open-source local AI community &middot; <a href="README.md">English</a> &middot; <a href="README.ru.md">Русский</a> &middot; <a href="README.kz.md">Қазақша</a></sub>
</p>
