# SHARD STUDIO // Native Desktop Local AI Runtime & CLI Bridge
[![Version](https://img.shields.io/badge/Version-v1.4.79-cfa65b)](https://github.com/avelone123123/shard)
[![Architecture](https://img.shields.io/badge/Architecture-Win32%20C%2B%2B20%20%2B%20MASM%20AVX2%20%2B%20FastAPI-22a06b)](https://github.com/avelone123123/shard)
[![Kernels](https://img.shields.io/badge/Kernels-98%20Active-10b981)](https://github.com/avelone123123/shard)
[![Tests](https://img.shields.io/badge/Tests-106%20Passed-10b981)](https://github.com/avelone123123/shard)
[![License](https://img.shields.io/badge/License-MIT-38bdf8)](LICENSE)
Shard Studio is a standalone 100% local AI desktop platform (`shard.exe` / `shard-native.exe`) engineered with a tri-language native architecture:
- **Native Win32 C++20 Desktop Studio (`shard.exe` / `shard-native.exe`)**: Double-buffered 60fps desktop application with asynchronous `WinHTTP` worker threads, auto-managed background server startup (`CREATE_NO_WINDOW`), live Hugging Face 3,000,000+ model catalog, hardware fit advisor, SSD storage manager, multi-session SSE streaming AI chat, and 1-click CLI/IDE auto-configurator.
- **C++20 Zero-Copy Core (`shard_core.dll`)**: Direct Win32 `MapViewOfFile` Safetensors paging, vectorized `LayerNorm`, `Softmax`, `RoPE`, `SwiGLU`, `CosineSim`, and `Q4`/`Q8_0` dequantization.
- **MASM x64 Assembly (`shard_asm.asm`) & Python FastAPI Runtime**: Hand-crafted 256-bit AVX2/FMA SIMD kernels (`ymm0-ymm7`) achieving 35+ GFLOPS plus full OpenAI `/v1/chat/completions` and Anthropic `/v1/messages` SSE streaming endpoints on `http://localhost:8000/v1`.
## System Overview
```
+-------------------------------------------------------------------------+
|                    ECOSYSTEM & CLI AUTO-INTEGRATION                     |
|  Claude Code | OpenRouter SDK | Cursor | OpenCode | Continue | Aider    |
+-------------------------------------------------------------------------+
                                    | OpenAI & Anthropic Messages SSE API
                                    v
+-------------------------------------------------------------------------+
|               SHARD STUDIO NATIVE DESKTOP (.EXE) + SERVER               |
|  HF Hub Search | Hardware Fit Advisor | Live AI Chat | SSD Disk Manager |
+-------------------------------------------------------------------------+
                                    | Zero-Copy C FFI & WinHTTP
                                    v
+-------------------------------------------------------------------------+
|                   C++20 NATIVE CORE + MASM x64 AVX2                     |
|   MapViewOfFile Pager | Dot Unroll8 FMA | RMSNorm | Q4/Q8 Unpacker      |
+-------------------------------------------------------------------------+
```
## Instant Desktop Quickstart
1. Download or clone the repository and launch **`shard.exe`** (or **`shard-native.exe`**):
```bash
git clone https://github.com/avelone123123/shard.git
cd shard
shard.exe
```
2. `shard.exe` opens immediately and starts the local inference server (`127.0.0.1:8000`) silently in the background.
3. Browse open-weight Hugging Face models filtered for your exact GPU VRAM & RAM, download & launch in 1 click, chat locally, or click **Apply in 1 Click** to connect **Claude Code**, **OpenRouter SDK**, **Cursor**, **OpenCode**, **Continue**, **Aider**, **Zed**, or **Codex**.
## CLI & Desktop Commands
| Command | Responsibility |
| :--- | :--- |
| `shard` / `shard.exe` | Open Native Win32 C++20 Desktop Studio + Auto-Start Local Server |
| `shard start --port 8000` | Start headless FastAPI server |
| `shard status` | Inspect GPU, VRAM, RAM, CPU threads, and active native DLL state |
| `shard list --category ideal` | Rank open-weight Hugging Face models ideal for host GPU/RAM |
| `shard run <model_id>` | Interactive terminal REPL with live token/sec telemetry |
| `shard test` | Execute 12-family MASM x64 AVX2 + C++20 hardware benchmark |
| `shard key --gen` | Generate local `sk-shard-*` authentication key |
| `python -m unittest tests_shard.py` | Execute 106 deterministic native & runtime unit tests |
## Native AVX2 Benchmark (`shard test`)
```
Kernel: Shard Native Core v1.4.79 (C++20 Zero-Copy + MASM x64 AVX2 98-Kernel Engine)
[01/12] Dot-Product FMA:  36.45 GFLOPS (22,480 us)
[05/12] Q8_0 Dequant:     2457.3 ops/sec (AVX2 Int8->FP32)
[06/12] Q4_0 Dequant:     2561.8 ops/sec (AVX2 Nibble Unpack)
[10/12] Stable Softmax:   10565.9 ops/sec (MASM vmaxps + Scale)
Native Pipeline:          ALL 12 KERNEL FAMILIES VALIDATED [OK]
```
## Author & License
- Author & Maintainer: [avelone123123](https://github.com/avelone123123)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md) | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- License: MIT
