# SHARD — Local AI engine that tells you *first* whether a model will run

[![Tests](https://github.com/avelone123123/shard/actions/workflows/tests.yml/badge.svg)](https://github.com/avelone123123/shard/actions/workflows/tests.yml)
[![Version](https://img.shields.io/badge/Version-v1.6.0-22a06b)](https://github.com/avelone123123/shard)
[![Python](https://img.shields.io/badge/Python-3.8%2B-38bdf8)](https://github.com/avelone123123/shard)
[![License](https://img.shields.io/badge/License-MIT-38bdf8)](LICENSE)

A 100% local AI runtime for Windows: FastAPI server + GGUF (llama.cpp) backend + native C++20/MASM AVX2 core + web dashboard.

**The problem it solves:** you download a 20 GB model, load it, and the program crashes, freezes, or crawls — because the model never fit your hardware in the first place. SHARD checks that *before* the download: it computes a per-quantization VRAM/RAM/speed plan from your live hardware and says plainly whether to start or not.

- **Pre-flight fit advisor** — `shard fit <model>` (and a dashboard card) shows, for every quant level, how many GB go to VRAM vs RAM, how many layers fit on GPU, estimated tok/s, and a verdict: `GPU` / `HYBRID` / `IMPOSSIBLE`.
- **GGUF backend** — quantized models run through a llama.cpp `llama-server` subprocess (~50 MB, auto-downloaded once), driven by the fit advisor's quant / GPU-layer recommendation.
- **OpenAI- and Anthropic-compatible API** — `/v1/chat/completions`, `/v1/messages`, SSE streaming; 1-click config for Claude Code, Cursor, OpenCode, Continue, Aider, Zed.
- **Native core (optional)** — C++20 zero-copy safetensors pager + hand-written MASM x64 AVX2 kernels; benchmarked in-app with `shard test`.
- **Honest UI** — Russian-first interface, estimates labeled as estimates, nothing faked.

## Quickstart

The repo is needed either way — the desktop app drives a local Python server. Without a compiler, run from Python (no GPU required to browse, download, or plan):

```bash
git clone https://github.com/avelone123123/shard.git
cd shard
pip install -e .
shard start          # opens the dashboard at http://localhost:8000
```

`python main.py` does the same thing. The server listens on `0.0.0.0:8000` by default; set `SHARD_HOST=127.0.0.1` to keep it off your LAN.

For the native desktop app (auto-starts the server and opens the dashboard), grab `shard-v1.6.0-windows-x64.zip` from [Releases](https://github.com/avelone123123/shard/releases) and unzip `shard.exe` + `shard_core.dll` into the repo root — no MSVC Build Tools needed.

Check fit before downloading anything:

```bash
shard fit google/gemma-2-27b-it
shard fit Qwen/Qwen3-1.7B
```

Models are stored in `~/.shard/models` by default. Point them anywhere (external SSD works) via the dashboard settings, `shard models-dir D:\models`, or the `SHARD_MODELS_DIR` environment variable. The first GGUF launch downloads the llama.cpp runtime (~50 MB) once to `~/.shard/runtime`.

### What the advisor says (real output, RTX 2050 / 4 GB VRAM / 16 GB RAM)

A model that will not run — all six quants rejected, with the reason:

```
GPU: NVIDIA GeForce RTX 2050 (4.0 GB VRAM) | RAM: 15.7 GB
Params: 27.0B (source: name-regex) | FP16: 58.29 GB | KV-cache: 4.29 GB

QUANT         SIZE      VRAM       RAM   GPU LAYERS      SPEED  STATUS
------------------------------------------------------------------------------
Q8_0        28.7 GB     0.0 GB    29.9 GB         0/64      0.0 t/s  IMPOSSIBLE
Q6_K        22.3 GB     0.0 GB    23.2 GB         0/64      0.0 t/s  IMPOSSIBLE
Q5_K_M      19.2 GB     0.0 GB    20.2 GB         0/64      0.0 t/s  IMPOSSIBLE
Q4_K_M      16.4 GB     0.0 GB    17.4 GB         0/64      0.0 t/s  IMPOSSIBLE
Q3_K_M      13.2 GB     0.0 GB    14.2 GB         0/64      0.0 t/s  IMPOSSIBLE
Q2_K        10.3 GB     0.0 GB    11.4 GB         0/64      0.0 t/s  IMPOSSIBLE

Result: DO NOT START ON THIS HARDWARE
Note: Теоретически модель можно стримить с SSD через mmap (~0.0 ток/с), но это в 30-50 раз медленнее RAM и практически бесполезно.
```

A model that will run — recommended quant, GPU layer count, expected speed:

```
GPU: NVIDIA GeForce RTX 2050 (4.0 GB VRAM) | RAM: 15.7 GB
Params: 1.5B (source: name-regex) | FP16: 4.21 GB | KV-cache: 1.21 GB

QUANT         SIZE      VRAM       RAM   GPU LAYERS      SPEED  STATUS
------------------------------------------------------------------------------
Q8_0         1.6 GB     2.8 GB     0.0 GB       999/36     32.1 t/s  GPU
Q6_K         1.2 GB     2.5 GB     0.0 GB       999/36     36.8 t/s  GPU
...

Result: LAUNCH OK
Recommendation: Q8_0 — file 1.6 GB, 999/36 layers on GPU, ~32.1 tok/s
```

(Interface text is currently Russian-first; an English UI is on the roadmap.)

## What your GPU can run

Estimated with the fit engine itself (context 4096, 2 GB RAM reserve for the OS). **These are estimates, not measured benchmarks** — always run `shard fit` for your exact card and model.

| GPU VRAM | Largest model fully in VRAM | Weights | Est. speed | Hybrid upper bound (VRAM+RAM) | Est. speed |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 4 GB | ~3.5B (Q5_K_M) | 2.5 GB | ~24 tok/s | 70B (Q2_K, mmap) | ~1.3 tok/s |
| 6 GB | 8B (Q2_K) | 3.1 GB | ~17 tok/s | 70B (Q2_K, mmap) | ~1.4 tok/s |
| 8 GB | 8B (Q5_K_M) | 5.7 GB | ~11 tok/s | 70B (Q2_K, mmap) | ~1.4 tok/s |
| 12 GB | 14B (Q4_K_M) | 8.5 GB | ~7.7 tok/s | 70B (Q2_K, mmap) | ~1.6 tok/s |
| 16 GB | 32B (Q2_K) | 12.2 GB | ~5.5 tok/s | 70B (Q2_K, mmap) | ~1.7 tok/s |
| 24 GB | 32B (Q4_K_M) | 19.4 GB | ~3.8 tok/s | 70B (Q2_K, mmap) | ~2.2 tok/s |

\* Hybrid speeds are theoretical upper bounds: part of the model streams through mmap from RAM/SSD, so real numbers depend heavily on your RAM bandwidth and SSD. SHARD's advisor marks such setups `HYBRID` and warns about it.

## Dashboard

Open `http://localhost:8000`:

- browse the full Hugging Face Hub catalog, filtered by your GPU VRAM / RAM,
- download with live progress (multi-part GGUF included) to any drive,
- load/unload with progress stages, chat with SSE streaming,
- fit-advisor card per model *before* download,
- drives & storage manager, API-key management (`sk-shard-*`), 1-click CLI/IDE integration.

## API

| Endpoint | Purpose |
| :--- | :--- |
| `POST /v1/chat/completions` | OpenAI-compatible chat (SSE streaming supported) |
| `POST /v1/messages`, `POST /v1/messages/count_tokens` | Anthropic-compatible messages |
| `GET /v1/models`, `GET /v1/ping` | Model list / health |
| `GET /api/fit/{model_id}` | Fit report JSON (verdict, per-quant plan, KV-cache) |
| `POST /api/models/load`, `POST /api/models/unload` | Engine control |
| `GET /api/hf/models`, `GET /api/hf/model/{id}` | HF catalog & metadata |
| `GET /api/drives`, `GET/POST /api/settings`, `GET /api/keys` | Storage, settings, keys |

## CLI

| Command | What it does |
| :--- | :--- |
| `shard start --host --port` | Start the server (dashboard at `/`) |
| `shard gui` | Launch the native desktop studio (if built) |
| `shard fit <model_id>` | Pre-flight fit report for your hardware |
| `shard models-dir <path>` | Show / set the models directory |
| `shard list --category` | Rank HF models for this machine |
| `shard run <model_id>` | Terminal REPL with token/sec telemetry |
| `shard status` | GPU, VRAM, RAM, CPU threads, native core state |
| `shard test` | 12-family AVX2 + C++20 kernel benchmark |
| `shard key --gen` | Generate a local API key |

## Native core (optional)

The Python path (server, CLI, fit advisor, llama.cpp GGUF backend) works without any build. The native part is the separate safetensors engine:

- **C++20 core (`shard_core.dll`)** — Win32 `MapViewOfFile` safetensors paging, vectorized LayerNorm / Softmax / RoPE / SwiGLU / cosine-similarity, Q4/Q8_0 dequantization.
- **MASM x64 (`shard_asm.asm`)** — hand-written AVX2/FMA kernels (ymm0–ymm7).

Build it yourself (MSVC Build Tools with `ml64` required):

```bash
python native/build_native.py
```

Sample `shard test` output (12 kernel families; numbers vary per machine):

```
Kernel: Shard Native Core (C++20 Zero-Copy + MASM x64 AVX2)
[01/12] Dot-Product FMA:  36.45 GFLOPS (22,480 us)
[05/12] Q8_0 Dequant:     2457.3 ops/sec (AVX2 Int8->FP32)
[06/12] Q4_0 Dequant:     2561.8 ops/sec (AVX2 Nibble Unpack)
[10/12] Stable Softmax:   10565.9 ops/sec (MASM vmaxps + Scale)
Native Pipeline:          ALL 12 KERNEL FAMILIES VALIDATED [OK]
```

## Tests & CI

- `python -m unittest tests_shard.py` — **50 focused unit tests** (fit-advisor math, GGUF candidate selection, settings, module compilation). Every expected value in the suite was verified empirically before being written down.
- CI runs the same suite on `windows-latest` for Python 3.12–3.14 (see the badge above).

## Honest limitations

- Speed figures (`tok/s`) are estimates from a per-GB throughput model — no per-model benchmark suite yet; measured benchmarks are planned.
- Parameter counts fall back to name-regex parsing when `config.json` isn't available offline; the advisor labels the source.
- Windows-first: drive handling and the native build target Windows; Linux/macOS support is not there yet.
- The safetensors native engine has no VRAM offload — if your VRAM is small, use GGUF models (the llama.cpp path is the one that handles low-VRAM hardware).
- Single GPU only; no multi-GPU yet.
- UI text is Russian-first.

## Roadmap

- **LAN sharding** — split one model across 2+ PCs on your network (a master owns the scheduler/tokenizer, workers own layer ranges). Design draft: [docs/LAN_SHARDING_DESIGN.md](docs/LAN_SHARDING_DESIGN.md).
- Measured per-model benchmarks published in `benchmarks/`.
- English UI, Linux/macOS builds.

## Русская версия

[README.ru.md](README.ru.md)

## Author & License

- Author & Maintainer: [avelone123123](https://github.com/avelone123123)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md) | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- License: MIT
