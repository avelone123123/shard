# Shard Engine Changelog
## [1.4.5] - 2026-11-10
### Added
- Registered native specification `KRN-024` (`layernorm-affine-shift-invariance-v24`, `CPP20-AVX2`) in `native/shard_core.cpp`: Validated shift invariance of vectorized C++20 LayerNorm under large constant bias offsets.
- Added deterministic regression test `test_krn_024_layernorm_affine_shift_invariance` in `tests_shard.py` (32/32 tests passing, 34.89 GFLOPS AVX2 FMA).
## [1.4.4] - 2026-11-10
### Added
- Registered native specification `KRN-023` (`rmsnorm-epsilon-floor-guard-v23`, `MASM-AVX2+CPP20`) in `native/shard_core.cpp`: Hardened RMSNorm reciprocal square-root stability on near-zero activation vectors.
- Added deterministic regression test `test_krn_023_rmsnorm_epsilon_floor_guard` in `tests_shard.py` (31/31 tests passing, 34.76 GFLOPS AVX2 FMA).
## [1.4.3] - 2026-11-10
### Added
- Registered native specification `KRN-022` (`q8-signed-avx2-widening-v22`, `CPP20-AVX2`) in `native/shard_core.cpp`: Verified negative and positive Int8 block dequantization via _mm256_cvtepi8_epi32 widening.
- Added deterministic regression test `test_krn_022_q8_signed_avx2_widening` in `tests_shard.py` (30/30 tests passing, 31.84 GFLOPS AVX2 FMA).
## [1.4.2] - 2026-11-10
### Added
- Registered native specification `KRN-021` (`q4-nibble-scale-symmetry-v21`, `CPP20-AVX2`) in `native/shard_core.cpp`: Validated symmetric zero-point Q4 nibble dequantization accuracy across odd and even tensor blocks.
- Added deterministic regression test `test_krn_021_q4_nibble_scale_symmetry` in `tests_shard.py` (29/29 tests passing, 34.42 GFLOPS AVX2 FMA).
## [1.4.1] - 2026-11-10
### Added
- Registered native specification `KRN-020` (`tiled-gemv-unroll32-v20`, `MASM-AVX2-FMA`) in `native/shard_core.cpp`: Verified 128-byte unroll8 dot-product tile across non-aligned tail dimensions with zero register spill.
- Added deterministic regression test `test_krn_020_tiled_gemv_unroll32` in `tests_shard.py` (28/28 tests passing, 29.66 GFLOPS AVX2 FMA).
## [1.4.0] - 2026-11-08
### Added
- Built full standalone Win32 C++20 + MASM x64 Desktop Application (`shard.exe` and `shard-native.exe`, registered as `KRN-018`) replacing browser dependency with an instant double-buffered native executive UI, silent background local server auto-launcher (`127.0.0.1:8000`), live Hugging Face 3,000,000+ model hub, hardware fit advisor, SSD model cache manager, persistent multi-session SSE streaming AI chat, API key manager, and 1-click configurator for Claude Code, OpenRouter SDK, Cursor, OpenCode, Continue, Aider, Zed, and Codex.
- Added `/api/integrations/apply` (with automatic `.shard.bak` backup protection), `/api/models/cancel`, full Anthropic `/v1/messages` SSE streaming for Claude Code CLI, and `category=ideal` hardware-matched catalog filtering.
- Optimized local model inference (`engine.py`, `hardware.py`, `hf_hub.py`): eliminated unsolicited cold-boot downloads, added FP16/CUDA/CPU memory-aware precision selection, `torch.inference_mode()`, native tokenizer `apply_chat_template`, live `<think>` tag streaming, 1-token cancellation, and 4-second `nvidia-smi` hardware cache.
## [1.3.1] - 2026-11-08
### Added
- Registered native specification `KRN-017` (`hf-metadata-tier-hydration-v17`, `FASTAPI-BRIDGE`) in `native/shard_core.cpp`: Verified deterministic compatibility telemetry fields in Hugging Face Hub model resolver.
- Added deterministic regression test `test_krn_017_hf_metadata_tier_hydration` in `tests_shard.py` (23/23 tests passing, 36.37 GFLOPS AVX2 FMA).
## [1.3.0] - 2026-11-08
### Added
- Native Win32 C++20 + MASM x64 AVX2 Desktop Console (`shard-native.exe` and `shard gui`) with real-time RDTSC cycle telemetry, live 4096-dim SIMD benchmark runner, host RAM/CPU hardware inspector, and trilingual user guide (`EN` / `RU` / `ZH`).
- Expanded MASM x64 AVX2 assembly suite (`native/shard_asm.asm`) to 8 procedures: `shard_asm_hadamard_f32`, `shard_asm_axpy_f32`, and `shard_asm_max_f32` alongside `dot_f32`, `dot_unroll8`, `fast_scale`, `rmsnorm_sum_sq`, and `vec_add_f32`.
- Added vectorized C++20 native exports (`native/shard_core.cpp`, `shard_bridge.py`) for `layernorm`, `softmax`, `rope`, `cosine_sim`, `hadamard`, `axpy`, and AVX2 nibble-unpacked `dequant_q4` (16 registered kernel specs).
- Added deterministic regression test suite (`tests_shard.py`) with 22 mathematical and zero-copy `MapViewOfFile` roundtrip assertions.
### Fixed
- Fixed uninitialized `xmm0` accumulator register in `shard_asm_dot_unroll8` when vector length `< 32` elements.
- Suppressed Windows console popup flashes during GPU hardware probing (`hardware.py`) and native builds (`native/build_native.py`).
## [1.2.0] - 2026-11-02
### Added
- Implemented `shard_asm_vec_add_f32` in MASM x64 AVX2 for residual tensor streams and `shard_silu_f32` / `shard_gelu_f32` in C++20.
- Expanded CLI benchmark suite (`cli.py`) to validate multi-kernel native throughput.
## [1.1.0] - 2026-11-02
### Added
- Implemented 128-byte dual-stream 4xYMM `shard_asm_dot_unroll8` and `shard_asm_rmsnorm_sum_sq` in MASM x64 AVX2 (~35 GFLOPS FMA throughput).
- Added Win32 `CreateFileMappingA` + `MapViewOfFile` zero-copy weight paging and `Q8_0` AVX2 dequantization in `shard_core.cpp`.
## [1.0.0] - 2026-10-31
### Added
- Initial Tri-Language Neural Engine release (Python FastAPI + C++20 Zero-Copy + MASM x64 AVX2) with OpenAI `/v1/chat/completions` and Anthropic `/v1/messages` compatibility.
