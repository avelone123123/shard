# SHARD // Tri-Language Local Neural Engine
[![Version](https://img.shields.io/badge/Version-v1.3.1-2563eb)](https://github.com/avelone123123/shard)
[![Architecture](https://img.shields.io/badge/Architecture-Python%20%2B%20C%2B%2B20%20%2B%20MASM%20AVX2-06b6d4)](https://github.com/avelone123123/shard)
[![Kernels](https://img.shields.io/badge/Kernels-17%20Active-10b981)](https://github.com/avelone123123/shard)
[![Tests](https://img.shields.io/badge/Tests-23%20Passed-10b981)](https://github.com/avelone123123/shard)
[![License](https://img.shields.io/badge/License-MIT-38bdf8)](LICENSE)
Shard is a high-performance 100% local AI inference runtime engineered with a tri-language architecture:
- **Python 3.14 Layer**: Async FastAPI server, OpenAI `/v1/chat/completions` & Anthropic `/v1/messages` SSE streaming, Hugging Face Hub discovery, and VRAM/RAM/NVMe tier profiling.
- **C++20 Native Core (`shard_core.dll`)**: Direct Win32 `MapViewOfFile` zero-copy tensor paging, vectorized `LayerNorm`, `Softmax`, `RoPE`, `SwiGLU`, `CosineSim`, and `Q4`/`Q8_0` dequantization.
- **MASM x64 Assembly (`shard_asm.asm` + `shard-native.exe`)**: Hand-crafted 256-bit AVX2/FMA SIMD kernels (`ymm0-ymm7`) achieving 35+ GFLOPS plus an instant native Win32 desktop telemetry console with trilingual (`EN` / `RU` / `ZH`) user guide.
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
|                   C++20 NATIVE CORE + WIN32 CONSOLE                     |
|      Win32 MapViewOfFile Pager  |  Q4/Q8 Unpacker  |  shard-native.exe  |
+-------------------------------------------------------------------------+
                                    | Direct Register Dispatch
                                    v
+-------------------------------------------------------------------------+
|                     MASM x64 AVX2 ASSEMBLY KERNELS                      |
|    Dot Unroll8 FMA | RMSNorm | Hadamard | AXPY | VecAdd | MaxSoft       |
+-------------------------------------------------------------------------+
```
## Installation
```bash
git clone https://github.com/avelone123123/shard.git
cd shard
pip install -e .
```
## CLI & Native Desktop Console
| Command | Responsibility |
| :--- | :--- |
| `shard start --port 8000` | Start local FastAPI server and Web Workspace |
| `shard gui` | Launch native Win32 C++20 + MASM x64 AVX2 desktop console (`shard-native.exe`) |
| `shard status` | Inspect GPU, VRAM, RAM, CPU threads, and active native DLL state |
| `shard list --category coding` | Rank open-weight models against host hardware compatibility tiers |
| `shard run <model_id>` | Interactive terminal REPL with live token/sec telemetry |
| `shard test` | Execute 12-family MASM x64 AVX2 + C++20 hardware benchmark |
| `shard key --gen` | Generate local `sk-shard-*` authentication key |
| `python -m unittest tests_shard.py` | Execute 23 deterministic native & runtime unit tests |
## IDE & Tool Integration
Shard exposes OpenAI and Anthropic Messages compatible endpoints on `http://localhost:8000/v1`:
### 1. Cursor IDE / Continue / Aider
- Model Name: `Qwen/Qwen2.5-Coder-7B-Instruct`
- OpenAI Base URL: `http://localhost:8000/v1`
- API Key: `sk-shard-...` (`shard key --gen`)
### 2. Universal Windows One-Liner (Claude Code / OpenCode)
```powershell
powershell -ExecutionPolicy Bypass -Command "irm 'http://localhost:8000/i?os=windows&tool=opencode' | iex"
```
### 3. Linux & macOS One-Liner
```bash
bash <(curl -fsSL "http://localhost:8000/i?tool=opencode")
```
## Docker & Persistent Model Storage
```bash
docker compose up -d
```
All downloaded weights persist in `shard_models_volume` (`/data/models`) across container restarts.
## Native AVX2 Benchmark (`shard test`)
```
Kernel: Shard Native Core v1.3.1 (C++20 Zero-Copy + MASM x64 AVX2 17-Kernel Engine)
[01/12] Dot-Product FMA:  35.15 GFLOPS (23,307 us)
[05/12] Q8_0 Dequant:     2457.3 ops/sec (AVX2 Int8->FP32)
[06/12] Q4_0 Dequant:     2561.8 ops/sec (AVX2 Nibble Unpack)
[10/12] Stable Softmax:   10565.9 ops/sec (MASM vmaxps + Scale)
Native Pipeline:          ALL 12 KERNEL FAMILIES VALIDATED [OK]
```
## Author & License
- Author & Maintainer: [avelone123123](https://github.com/avelone123123)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md) | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- License: MIT
