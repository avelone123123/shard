# Contributing to Shard Engine
Thank you for your interest in contributing to Shard. Shard is a high-performance local Hugging Face model runtime engineered with Python, C++20, and MASM x64 AVX2 SIMD.
## Architecture Guidelines
- System performance is prioritized: zero-copy buffers, SIMD vectorization, and minimal memory overhead.
- Primary core codebase is written in C++20 and MASM assembly; Python provides the HTTP/SSE orchestration layer.
- Code hygiene: clean, optimized, production-grade code without dead code or hardcoded dummy values.
## Development Setup
```bash
git clone https://github.com/avelone123123/shard.git
cd shard
pip install -e .
python native/build_native.py
shard test
```
## Issues and Pull Requests
- Please report bugs using the GitHub issue template with reproduction steps and hardware details.
- Pull requests must include validation against the native AVX2 SIMD benchmark (`shard test`).
- Project maintainer: avelone123123.