# Shard Engine Changelog
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
