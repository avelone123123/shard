# SHARD // Tri-Language Local Neural Engine
[![Architecture](https://img.shields.io/badge/Architecture-Python%20%2B%20C%2B%2B20%20%2B%20MASM%20AVX2-06b6d4)](https://github.com/avelone123123/shard)
[![Status](https://img.shields.io/badge/Status-Production%20Ready-10b981)](https://github.com/avelone123123/shard)
[![License](https://img.shields.io/badge/License-MIT-38bdf8)](LICENSE)
Shard is a high-performance local AI runtime engineered with a tri-language architecture:
- Python 3.14 (Async FastAPI, Open weights streaming, SSE token streaming)
- C++20 Core (Direct Win32 memory-mapped tensor paging, zero-copy matrix buffers)
- MASM x64 Assembly (Hand-crafted AVX2/FMA SIMD vector kernels achieving 18+ GFLOPS)
## System Overview
```
+-------------------------------------------------------------------------+
|                          CLIENT ECOSYSTEM                               |
|   Cursor IDE  |  OpenCode  |  Claude Code  |  Aider CLI  |  Continue   |
+-------------------------------------------------------------------------+
                                    | OpenAI & Claude Messages API
                                    v
+-------------------------------------------------------------------------+
|                        FASTAPI PYTHON LAYER                             |
|      SSE Streamer  |  Model Hub Bridge  |  Hardware Profiler (VRAM)     |
+-------------------------------------------------------------------------+
                                    | Zero-Copy C FFI
                                    v
+-------------------------------------------------------------------------+
|                        C++20 NATIVE CORE                                |
|      Win32 VirtualAlloc  |  MapViewOfFile Pager  |  Quantizer (Q4/Q8)   |
+-------------------------------------------------------------------------+
                                    | Direct Register Dispatch
                                    v
+-------------------------------------------------------------------------+
|                     MASM x64 AVX2 ASSEMBLY KERNEL                       |
|        ymm0-ymm7 FMA Accumulators  |  Aligned 256-bit Vector Dot        |
+-------------------------------------------------------------------------+
```
## Installation
```bash
git clone https://github.com/avelone123123/shard.git
cd shard
pip install -e .
```
## CLI Commands
Shard provides a unified command line tool:
```bash
shard start                     # Start local server and web workspace (port 8000)
shard start --port 8080         # Start on custom port
shard status                    # Inspect GPU, VRAM, RAM, and native kernel state
shard list                      # View curated models and tiers
shard list --category coding    # Filter models by category
shard run <model_id>            # Interactive REPL session with live telemetry
shard test                      # Execute native AVX2 SIMD hardware benchmark
shard key                       # List local API authentication keys
shard key --gen                 # Generate new API key
shard help                      # Display command reference
```
## IDE & Tool Integration
Shard exposes full OpenAI and Claude Messages compatible endpoints on `http://localhost:8000/v1`:
### 1. Cursor IDE
- Open **Settings** -> **Models** -> Add Custom Model:
- Model Name: `Qwen/Qwen2.5-Coder-7B-Instruct`
- OpenAI Base URL: `http://localhost:8000/v1`
- API Key: `sk-shard-...` (generate via `shard key --gen` or in Web Dashboard)
### 2. Universal Windows Installer (CMD / PowerShell / Terminal)
```cmd
powershell -ExecutionPolicy Bypass -Command "irm 'http://localhost:8000/i?os=windows&tool=opencode&key=YOUR_KEY' | iex"
```
### 3. Linux & macOS
```bash
bash <(curl -fsSL "http://localhost:8000/i?tool=opencode&key=YOUR_KEY")
```
## Docker & Persistent Model Storage
Run Shard in an isolated container with persistent model caching and automatic volume mounts:
```bash
docker compose up -d
```
All downloaded weights are preserved in `shard_models_volume` (`/data/models`) across container restarts.
## Model Specification & Card Browser
The built-in web dashboard on `http://localhost:8000` provides real-time access to open model repositories, featuring:
- Full Model Card rendering (tables, architecture charts, specs, and resolved CDN images).
- Zero-copy tensor memory mapping straight to C++20 and MASM AVX2 vector registers.
- Multi-session chat sandbox with persistent multi-turn conversational memory.
## Native AVX2 Benchmark
```bash
shard test
```
Result on Intel/AMD x64 with AVX2:
```
Iterations:       100,000
Elapsed Time:     45,179 us (0.0452 s)
Throughput:       18.13 GFLOPS (AVX2 SIMD)
Native Pipeline:  VALIDATED [OK]
```
## Author & Community
- Author & Maintainer: avelone123123
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md) | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
## License
MIT License. Developed by avelone123123.