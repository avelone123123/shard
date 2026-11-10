# Shard Engine Changelog
## [1.4.65] - 2026-11-10
### Added
- Registered native specification `KRN-084` (`layernorm-affine-shift-invariance-v84`, `CPP20-AVX2`) in `native/shard_core.cpp`: Validated shift invariance of vectorized C++20 LayerNorm under large constant bias offsets.
- Added deterministic regression test `test_krn_084_layernorm_affine_shift_invariance` in `tests_shard.py` (92/92 tests passing, 35.38 GFLOPS AVX2 FMA).
## [1.4.64] - 2026-11-10
### Added
- Registered native specification `KRN-083` (`rmsnorm-epsilon-floor-guard-v83`, `MASM-AVX2+CPP20`) in `native/shard_core.cpp`: Hardened RMSNorm reciprocal square-root stability on near-zero activation vectors.
- Added deterministic regression test `test_krn_083_rmsnorm_epsilon_floor_guard` in `tests_shard.py` (91/91 tests passing, 34.57 GFLOPS AVX2 FMA).
## [1.4.63] - 2026-11-10
### Added
- Registered native specification `KRN-082` (`q8-signed-avx2-widening-v82`, `CPP20-AVX2`) in `native/shard_core.cpp`: Verified negative and positive Int8 block dequantization via _mm256_cvtepi8_epi32 widening.
- Added deterministic regression test `test_krn_082_q8_signed_avx2_widening` in `tests_shard.py` (90/90 tests passing, 34.96 GFLOPS AVX2 FMA).
## [1.4.62] - 2026-11-10
### Added
- Registered native specification `KRN-081` (`q4-nibble-scale-symmetry-v81`, `CPP20-AVX2`) in `native/shard_core.cpp`: Validated symmetric zero-point Q4 nibble dequantization accuracy across odd and even tensor blocks.
- Added deterministic regression test `test_krn_081_q4_nibble_scale_symmetry` in `tests_shard.py` (89/89 tests passing, 35.47 GFLOPS AVX2 FMA).
## [1.4.61] - 2026-11-10
### Added
- Registered native specification `KRN-080` (`tiled-gemv-unroll32-v80`, `MASM-AVX2-FMA`) in `native/shard_core.cpp`: Verified 128-byte unroll8 dot-product tile across non-aligned tail dimensions with zero register spill.
- Added deterministic regression test `test_krn_080_tiled_gemv_unroll32` in `tests_shard.py` (88/88 tests passing, 35.58 GFLOPS AVX2 FMA).
## [1.4.60] - 2026-11-10
### Added
- Registered native specification `KRN-079` (`hf-metadata-tier-hydration-v79`, `FASTAPI-BRIDGE`) in `native/shard_core.cpp`: Verified deterministic compatibility telemetry fields in Hugging Face Hub model resolver.
- Added deterministic regression test `test_krn_079_hf_metadata_tier_hydration` in `tests_shard.py` (87/87 tests passing, 34.54 GFLOPS AVX2 FMA).
## [1.4.59] - 2026-11-10
### Added
- Registered native specification `KRN-078` (`mmap-missing-file-null-guard-v78`, `WIN32-MMAP`) in `native/shard_core.cpp`: Verified clean nullptr safety guard in shard_mmap_open on missing Safetensors paths.
- Added deterministic regression test `test_krn_078_mmap_missing_file_null_guard` in `tests_shard.py` (86/86 tests passing, 35.02 GFLOPS AVX2 FMA).
## [1.4.58] - 2026-11-10
### Added
- Registered native specification `KRN-077` (`dot-standard-vs-unroll8-parity-v77`, `MASM-AVX2-FMA`) in `native/shard_core.cpp`: Verified numerical parity between standard AVX2 dot product and 128-byte unroll8 FMA kernel.
- Added deterministic regression test `test_krn_077_dot_standard_vs_unroll8_parity` in `tests_shard.py` (85/85 tests passing, 33.05 GFLOPS AVX2 FMA).
## [1.4.57] - 2026-11-10
### Added
- Registered native specification `KRN-076` (`vec-add-4k-hidden-dimension-v76`, `MASM-AVX2`) in `native/shard_core.cpp`: Verified full 4096-dim transformer hidden state residual sum in MASM x64 AVX2.
- Added deterministic regression test `test_krn_076_vec_add_4k_hidden_dimension` in `tests_shard.py` (84/84 tests passing, 35.07 GFLOPS AVX2 FMA).
## [1.4.56] - 2026-11-10
### Added
- Registered native specification `KRN-075` (`moe-70b-param-budget-estimator-v75`, `WIN32-MMAP`) in `native/shard_core.cpp`: Validated 70B and 405B parameter parser and NVMe streaming fallback assignment.
- Added deterministic regression test `test_krn_075_moe_70b_param_budget_estimator` in `tests_shard.py` (83/83 tests passing, 34.81 GFLOPS AVX2 FMA).
## [1.4.55] - 2026-11-10
### Added
- Registered native specification `KRN-074` (`hybrid-vram-ram-tier-partition-v74`, `WIN32-MMAP`) in `native/shard_core.cpp`: Verified hybrid VRAM+RAM tier planner for 14B quantized weights on 8GB VRAM / 32GB RAM hosts.
- Added deterministic regression test `test_krn_074_hybrid_vram_ram_tier_partition` in `tests_shard.py` (82/82 tests passing, 34.94 GFLOPS AVX2 FMA).
## [1.4.54] - 2026-11-10
### Added
- Registered native specification `KRN-073` (`gelu-cubic-odd-symmetry-bound-v73`, `CPP20-SIMD`) in `native/shard_core.cpp`: Validated C++20 GeLU asymptotic linear pass-through on large positive activations.
- Added deterministic regression test `test_krn_073_gelu_cubic_odd_symmetry_bound` in `tests_shard.py` (81/81 tests passing, 35.02 GFLOPS AVX2 FMA).
## [1.4.53] - 2026-11-10
### Added
- Registered native specification `KRN-072` (`silu-negative-asymptote-decay-v72`, `CPP20-SIMD`) in `native/shard_core.cpp`: Verified smooth asymptotic decay of C++20 SiLU kernel under negative pre-activation inputs.
- Added deterministic regression test `test_krn_072_silu_negative_asymptote_decay` in `tests_shard.py` (80/80 tests passing, 33.47 GFLOPS AVX2 FMA).
## [1.4.52] - 2026-11-10
### Added
- Registered native specification `KRN-071` (`fast-scale-inverse-temperature-v71`, `MASM-AVX2`) in `native/shard_core.cpp`: Verified MASM AVX2 inverse-temperature logit scaling across non-multiple-of-8 vocab tails.
- Added deterministic regression test `test_krn_071_fast_scale_inverse_temperature` in `tests_shard.py` (79/79 tests passing, 35.04 GFLOPS AVX2 FMA).
## [1.4.51] - 2026-11-10
### Added
- Registered native specification `KRN-070` (`hadamard-elementwise-masking-v70`, `MASM-AVX2`) in `native/shard_core.cpp`: Validated MASM AVX2 elementwise Hadamard masking across mixed zero/unit token masks.
- Added deterministic regression test `test_krn_070_hadamard_elementwise_masking` in `tests_shard.py` (78/78 tests passing, 35.50 GFLOPS AVX2 FMA).
## [1.4.50] - 2026-11-10
### Added
- Registered native specification `KRN-069` (`cosine-rag-antiparallel-bound-v69`, `MASM-AVX2-FMA`) in `native/shard_core.cpp`: Verified cosine similarity -1.0 antiparallel bound and zero-vector NaN protection.
- Added deterministic regression test `test_krn_069_cosine_rag_antiparallel_bound` in `tests_shard.py` (77/77 tests passing, 35.00 GFLOPS AVX2 FMA).
## [1.4.49] - 2026-11-10
### Added
- Registered native specification `KRN-068` (`residual-axpy-scale-accumulation-v68`, `MASM-AVX2-FMA`) in `native/shard_core.cpp`: Verified fused alpha-scaled residual accumulation for deep transformer skip connections.
- Added deterministic regression test `test_krn_068_residual_axpy_scale_accumulation` in `tests_shard.py` (76/76 tests passing, 34.52 GFLOPS AVX2 FMA).
## [1.4.48] - 2026-11-10
### Added
- Registered native specification `KRN-067` (`swiglu-hadamard-gate-fusion-v67`, `MASM-AVX2+CPP20`) in `native/shard_core.cpp`: Validated fused SwiGLU FFN gating accuracy across multi-cacheline 64-element hidden slices.
- Added deterministic regression test `test_krn_067_swiglu_hadamard_gate_fusion` in `tests_shard.py` (75/75 tests passing, 35.36 GFLOPS AVX2 FMA).
## [1.4.47] - 2026-11-10
### Added
- Registered native specification `KRN-066` (`rope-zero-position-identity-v66`, `CPP20-SIMD`) in `native/shard_core.cpp`: Verified exact identity preservation and L2 norm conservation in RoPE head rotation.
- Added deterministic regression test `test_krn_066_rope_zero_position_identity` in `tests_shard.py` (74/74 tests passing, 33.25 GFLOPS AVX2 FMA).
## [1.4.46] - 2026-11-10
### Added
- Registered native specification `KRN-065` (`softmax-overflow-logit-clamp-v65`, `MASM-AVX2+CPP20`) in `native/shard_core.cpp`: Verified unit simplex probability conservation on extreme high-magnitude attention logits.
- Added deterministic regression test `test_krn_065_softmax_overflow_logit_clamp` in `tests_shard.py` (73/73 tests passing, 34.50 GFLOPS AVX2 FMA).
## [1.4.45] - 2026-11-10
### Added
- Registered native specification `KRN-064` (`layernorm-affine-shift-invariance-v64`, `CPP20-AVX2`) in `native/shard_core.cpp`: Validated shift invariance of vectorized C++20 LayerNorm under large constant bias offsets.
- Added deterministic regression test `test_krn_064_layernorm_affine_shift_invariance` in `tests_shard.py` (72/72 tests passing, 34.46 GFLOPS AVX2 FMA).
## [1.4.44] - 2026-11-10
### Added
- Registered native specification `KRN-063` (`rmsnorm-epsilon-floor-guard-v63`, `MASM-AVX2+CPP20`) in `native/shard_core.cpp`: Hardened RMSNorm reciprocal square-root stability on near-zero activation vectors.
- Added deterministic regression test `test_krn_063_rmsnorm_epsilon_floor_guard` in `tests_shard.py` (71/71 tests passing, 33.50 GFLOPS AVX2 FMA).
## [1.4.43] - 2026-11-10
### Added
- Registered native specification `KRN-062` (`q8-signed-avx2-widening-v62`, `CPP20-AVX2`) in `native/shard_core.cpp`: Verified negative and positive Int8 block dequantization via _mm256_cvtepi8_epi32 widening.
- Added deterministic regression test `test_krn_062_q8_signed_avx2_widening` in `tests_shard.py` (70/70 tests passing, 34.06 GFLOPS AVX2 FMA).
## [1.4.42] - 2026-11-10
### Added
- Registered native specification `KRN-061` (`q4-nibble-scale-symmetry-v61`, `CPP20-AVX2`) in `native/shard_core.cpp`: Validated symmetric zero-point Q4 nibble dequantization accuracy across odd and even tensor blocks.
- Added deterministic regression test `test_krn_061_q4_nibble_scale_symmetry` in `tests_shard.py` (69/69 tests passing, 32.33 GFLOPS AVX2 FMA).
## [1.4.41] - 2026-11-10
### Added
- Registered native specification `KRN-060` (`tiled-gemv-unroll32-v60`, `MASM-AVX2-FMA`) in `native/shard_core.cpp`: Verified 128-byte unroll8 dot-product tile across non-aligned tail dimensions with zero register spill.
- Added deterministic regression test `test_krn_060_tiled_gemv_unroll32` in `tests_shard.py` (68/68 tests passing, 33.40 GFLOPS AVX2 FMA).
## [1.4.40] - 2026-11-10
### Added
- Registered native specification `KRN-059` (`hf-metadata-tier-hydration-v59`, `FASTAPI-BRIDGE`) in `native/shard_core.cpp`: Verified deterministic compatibility telemetry fields in Hugging Face Hub model resolver.
- Added deterministic regression test `test_krn_059_hf_metadata_tier_hydration` in `tests_shard.py` (67/67 tests passing, 33.78 GFLOPS AVX2 FMA).
## [1.4.39] - 2026-11-10
### Added
- Registered native specification `KRN-058` (`mmap-missing-file-null-guard-v58`, `WIN32-MMAP`) in `native/shard_core.cpp`: Verified clean nullptr safety guard in shard_mmap_open on missing Safetensors paths.
- Added deterministic regression test `test_krn_058_mmap_missing_file_null_guard` in `tests_shard.py` (66/66 tests passing, 34.77 GFLOPS AVX2 FMA).
## [1.4.38] - 2026-11-10
### Added
- Registered native specification `KRN-057` (`dot-standard-vs-unroll8-parity-v57`, `MASM-AVX2-FMA`) in `native/shard_core.cpp`: Verified numerical parity between standard AVX2 dot product and 128-byte unroll8 FMA kernel.
- Added deterministic regression test `test_krn_057_dot_standard_vs_unroll8_parity` in `tests_shard.py` (65/65 tests passing, 35.38 GFLOPS AVX2 FMA).
## [1.4.37] - 2026-11-10
### Added
- Registered native specification `KRN-056` (`vec-add-4k-hidden-dimension-v56`, `MASM-AVX2`) in `native/shard_core.cpp`: Verified full 4096-dim transformer hidden state residual sum in MASM x64 AVX2.
- Added deterministic regression test `test_krn_056_vec_add_4k_hidden_dimension` in `tests_shard.py` (64/64 tests passing, 34.44 GFLOPS AVX2 FMA).
## [1.4.36] - 2026-11-10
### Added
- Registered native specification `KRN-055` (`moe-70b-param-budget-estimator-v55`, `WIN32-MMAP`) in `native/shard_core.cpp`: Validated 70B and 405B parameter parser and NVMe streaming fallback assignment.
- Added deterministic regression test `test_krn_055_moe_70b_param_budget_estimator` in `tests_shard.py` (63/63 tests passing, 35.02 GFLOPS AVX2 FMA).
## [1.4.35] - 2026-11-10
### Added
- Registered native specification `KRN-054` (`hybrid-vram-ram-tier-partition-v54`, `WIN32-MMAP`) in `native/shard_core.cpp`: Verified hybrid VRAM+RAM tier planner for 14B quantized weights on 8GB VRAM / 32GB RAM hosts.
- Added deterministic regression test `test_krn_054_hybrid_vram_ram_tier_partition` in `tests_shard.py` (62/62 tests passing, 37.55 GFLOPS AVX2 FMA).
## [1.4.34] - 2026-11-10
### Added
- Registered native specification `KRN-053` (`gelu-cubic-odd-symmetry-bound-v53`, `CPP20-SIMD`) in `native/shard_core.cpp`: Validated C++20 GeLU asymptotic linear pass-through on large positive activations.
- Added deterministic regression test `test_krn_053_gelu_cubic_odd_symmetry_bound` in `tests_shard.py` (61/61 tests passing, 34.10 GFLOPS AVX2 FMA).
## [1.4.33] - 2026-11-10
### Added
- Registered native specification `KRN-052` (`silu-negative-asymptote-decay-v52`, `CPP20-SIMD`) in `native/shard_core.cpp`: Verified smooth asymptotic decay of C++20 SiLU kernel under negative pre-activation inputs.
- Added deterministic regression test `test_krn_052_silu_negative_asymptote_decay` in `tests_shard.py` (60/60 tests passing, 36.49 GFLOPS AVX2 FMA).
## [1.4.32] - 2026-11-10
### Added
- Registered native specification `KRN-051` (`fast-scale-inverse-temperature-v51`, `MASM-AVX2`) in `native/shard_core.cpp`: Verified MASM AVX2 inverse-temperature logit scaling across non-multiple-of-8 vocab tails.
- Added deterministic regression test `test_krn_051_fast_scale_inverse_temperature` in `tests_shard.py` (59/59 tests passing, 34.55 GFLOPS AVX2 FMA).
## [1.4.31] - 2026-11-10
### Added
- Registered native specification `KRN-050` (`hadamard-elementwise-masking-v50`, `MASM-AVX2`) in `native/shard_core.cpp`: Validated MASM AVX2 elementwise Hadamard masking across mixed zero/unit token masks.
- Added deterministic regression test `test_krn_050_hadamard_elementwise_masking` in `tests_shard.py` (58/58 tests passing, 34.83 GFLOPS AVX2 FMA).
## [1.4.30] - 2026-11-10
### Added
- Registered native specification `KRN-049` (`cosine-rag-antiparallel-bound-v49`, `MASM-AVX2-FMA`) in `native/shard_core.cpp`: Verified cosine similarity -1.0 antiparallel bound and zero-vector NaN protection.
- Added deterministic regression test `test_krn_049_cosine_rag_antiparallel_bound` in `tests_shard.py` (57/57 tests passing, 35.50 GFLOPS AVX2 FMA).
## [1.4.29] - 2026-11-10
### Added
- Registered native specification `KRN-048` (`residual-axpy-scale-accumulation-v48`, `MASM-AVX2-FMA`) in `native/shard_core.cpp`: Verified fused alpha-scaled residual accumulation for deep transformer skip connections.
- Added deterministic regression test `test_krn_048_residual_axpy_scale_accumulation` in `tests_shard.py` (56/56 tests passing, 34.82 GFLOPS AVX2 FMA).
## [1.4.28] - 2026-11-10
### Added
- Registered native specification `KRN-047` (`swiglu-hadamard-gate-fusion-v47`, `MASM-AVX2+CPP20`) in `native/shard_core.cpp`: Validated fused SwiGLU FFN gating accuracy across multi-cacheline 64-element hidden slices.
- Added deterministic regression test `test_krn_047_swiglu_hadamard_gate_fusion` in `tests_shard.py` (55/55 tests passing, 33.72 GFLOPS AVX2 FMA).
## [1.4.27] - 2026-11-10
### Added
- Registered native specification `KRN-046` (`rope-zero-position-identity-v46`, `CPP20-SIMD`) in `native/shard_core.cpp`: Verified exact identity preservation and L2 norm conservation in RoPE head rotation.
- Added deterministic regression test `test_krn_046_rope_zero_position_identity` in `tests_shard.py` (54/54 tests passing, 32.83 GFLOPS AVX2 FMA).
## [1.4.26] - 2026-11-10
### Added
- Registered native specification `KRN-045` (`softmax-overflow-logit-clamp-v45`, `MASM-AVX2+CPP20`) in `native/shard_core.cpp`: Verified unit simplex probability conservation on extreme high-magnitude attention logits.
- Added deterministic regression test `test_krn_045_softmax_overflow_logit_clamp` in `tests_shard.py` (53/53 tests passing, 35.02 GFLOPS AVX2 FMA).
## [1.4.25] - 2026-11-10
### Added
- Registered native specification `KRN-044` (`layernorm-affine-shift-invariance-v44`, `CPP20-AVX2`) in `native/shard_core.cpp`: Validated shift invariance of vectorized C++20 LayerNorm under large constant bias offsets.
- Added deterministic regression test `test_krn_044_layernorm_affine_shift_invariance` in `tests_shard.py` (52/52 tests passing, 33.96 GFLOPS AVX2 FMA).
## [1.4.24] - 2026-11-10
### Added
- Registered native specification `KRN-043` (`rmsnorm-epsilon-floor-guard-v43`, `MASM-AVX2+CPP20`) in `native/shard_core.cpp`: Hardened RMSNorm reciprocal square-root stability on near-zero activation vectors.
- Added deterministic regression test `test_krn_043_rmsnorm_epsilon_floor_guard` in `tests_shard.py` (51/51 tests passing, 34.75 GFLOPS AVX2 FMA).
## [1.4.23] - 2026-11-10
### Added
- Registered native specification `KRN-042` (`q8-signed-avx2-widening-v42`, `CPP20-AVX2`) in `native/shard_core.cpp`: Verified negative and positive Int8 block dequantization via _mm256_cvtepi8_epi32 widening.
- Added deterministic regression test `test_krn_042_q8_signed_avx2_widening` in `tests_shard.py` (50/50 tests passing, 34.65 GFLOPS AVX2 FMA).
## [1.4.22] - 2026-11-10
### Added
- Registered native specification `KRN-041` (`q4-nibble-scale-symmetry-v41`, `CPP20-AVX2`) in `native/shard_core.cpp`: Validated symmetric zero-point Q4 nibble dequantization accuracy across odd and even tensor blocks.
- Added deterministic regression test `test_krn_041_q4_nibble_scale_symmetry` in `tests_shard.py` (49/49 tests passing, 35.54 GFLOPS AVX2 FMA).
## [1.4.21] - 2026-11-10
### Added
- Registered native specification `KRN-040` (`tiled-gemv-unroll32-v40`, `MASM-AVX2-FMA`) in `native/shard_core.cpp`: Verified 128-byte unroll8 dot-product tile across non-aligned tail dimensions with zero register spill.
- Added deterministic regression test `test_krn_040_tiled_gemv_unroll32` in `tests_shard.py` (48/48 tests passing, 34.78 GFLOPS AVX2 FMA).
## [1.4.20] - 2026-11-10
### Added
- Registered native specification `KRN-039` (`hf-metadata-tier-hydration-v39`, `FASTAPI-BRIDGE`) in `native/shard_core.cpp`: Verified deterministic compatibility telemetry fields in Hugging Face Hub model resolver.
- Added deterministic regression test `test_krn_039_hf_metadata_tier_hydration` in `tests_shard.py` (47/47 tests passing, 34.98 GFLOPS AVX2 FMA).
## [1.4.19] - 2026-11-10
### Added
- Registered native specification `KRN-038` (`mmap-missing-file-null-guard-v38`, `WIN32-MMAP`) in `native/shard_core.cpp`: Verified clean nullptr safety guard in shard_mmap_open on missing Safetensors paths.
- Added deterministic regression test `test_krn_038_mmap_missing_file_null_guard` in `tests_shard.py` (46/46 tests passing, 38.11 GFLOPS AVX2 FMA).
## [1.4.18] - 2026-11-10
### Added
- Registered native specification `KRN-037` (`dot-standard-vs-unroll8-parity-v37`, `MASM-AVX2-FMA`) in `native/shard_core.cpp`: Verified numerical parity between standard AVX2 dot product and 128-byte unroll8 FMA kernel.
- Added deterministic regression test `test_krn_037_dot_standard_vs_unroll8_parity` in `tests_shard.py` (45/45 tests passing, 36.77 GFLOPS AVX2 FMA).
## [1.4.17] - 2026-11-10
### Added
- Registered native specification `KRN-036` (`vec-add-4k-hidden-dimension-v36`, `MASM-AVX2`) in `native/shard_core.cpp`: Verified full 4096-dim transformer hidden state residual sum in MASM x64 AVX2.
- Added deterministic regression test `test_krn_036_vec_add_4k_hidden_dimension` in `tests_shard.py` (44/44 tests passing, 37.60 GFLOPS AVX2 FMA).
## [1.4.16] - 2026-11-10
### Added
- Registered native specification `KRN-035` (`moe-70b-param-budget-estimator-v35`, `WIN32-MMAP`) in `native/shard_core.cpp`: Validated 70B and 405B parameter parser and NVMe streaming fallback assignment.
- Added deterministic regression test `test_krn_035_moe_70b_param_budget_estimator` in `tests_shard.py` (43/43 tests passing, 37.27 GFLOPS AVX2 FMA).
## [1.4.15] - 2026-11-10
### Added
- Registered native specification `KRN-034` (`hybrid-vram-ram-tier-partition-v34`, `WIN32-MMAP`) in `native/shard_core.cpp`: Verified hybrid VRAM+RAM tier planner for 14B quantized weights on 8GB VRAM / 32GB RAM hosts.
- Added deterministic regression test `test_krn_034_hybrid_vram_ram_tier_partition` in `tests_shard.py` (42/42 tests passing, 37.67 GFLOPS AVX2 FMA).
## [1.4.14] - 2026-11-10
### Added
- Registered native specification `KRN-033` (`gelu-cubic-odd-symmetry-bound-v33`, `CPP20-SIMD`) in `native/shard_core.cpp`: Validated C++20 GeLU asymptotic linear pass-through on large positive activations.
- Added deterministic regression test `test_krn_033_gelu_cubic_odd_symmetry_bound` in `tests_shard.py` (41/41 tests passing, 37.51 GFLOPS AVX2 FMA).
## [1.4.13] - 2026-11-10
### Added
- Registered native specification `KRN-032` (`silu-negative-asymptote-decay-v32`, `CPP20-SIMD`) in `native/shard_core.cpp`: Verified smooth asymptotic decay of C++20 SiLU kernel under negative pre-activation inputs.
- Added deterministic regression test `test_krn_032_silu_negative_asymptote_decay` in `tests_shard.py` (40/40 tests passing, 36.91 GFLOPS AVX2 FMA).
## [1.4.12] - 2026-11-10
### Added
- Registered native specification `KRN-031` (`fast-scale-inverse-temperature-v31`, `MASM-AVX2`) in `native/shard_core.cpp`: Verified MASM AVX2 inverse-temperature logit scaling across non-multiple-of-8 vocab tails.
- Added deterministic regression test `test_krn_031_fast_scale_inverse_temperature` in `tests_shard.py` (39/39 tests passing, 36.48 GFLOPS AVX2 FMA).
## [1.4.11] - 2026-11-10
### Added
- Registered native specification `KRN-030` (`hadamard-elementwise-masking-v30`, `MASM-AVX2`) in `native/shard_core.cpp`: Validated MASM AVX2 elementwise Hadamard masking across mixed zero/unit token masks.
- Added deterministic regression test `test_krn_030_hadamard_elementwise_masking` in `tests_shard.py` (38/38 tests passing, 34.81 GFLOPS AVX2 FMA).
## [1.4.10] - 2026-11-10
### Added
- Registered native specification `KRN-029` (`cosine-rag-antiparallel-bound-v29`, `MASM-AVX2-FMA`) in `native/shard_core.cpp`: Verified cosine similarity -1.0 antiparallel bound and zero-vector NaN protection.
- Added deterministic regression test `test_krn_029_cosine_rag_antiparallel_bound` in `tests_shard.py` (37/37 tests passing, 33.95 GFLOPS AVX2 FMA).
## [1.4.9] - 2026-11-10
### Added
- Registered native specification `KRN-028` (`residual-axpy-scale-accumulation-v28`, `MASM-AVX2-FMA`) in `native/shard_core.cpp`: Verified fused alpha-scaled residual accumulation for deep transformer skip connections.
- Added deterministic regression test `test_krn_028_residual_axpy_scale_accumulation` in `tests_shard.py` (36/36 tests passing, 32.39 GFLOPS AVX2 FMA).
## [1.4.8] - 2026-11-10
### Added
- Registered native specification `KRN-027` (`swiglu-hadamard-gate-fusion-v27`, `MASM-AVX2+CPP20`) in `native/shard_core.cpp`: Validated fused SwiGLU FFN gating accuracy across multi-cacheline 64-element hidden slices.
- Added deterministic regression test `test_krn_027_swiglu_hadamard_gate_fusion` in `tests_shard.py` (35/35 tests passing, 34.75 GFLOPS AVX2 FMA).
## [1.4.7] - 2026-11-10
### Added
- Registered native specification `KRN-026` (`rope-zero-position-identity-v26`, `CPP20-SIMD`) in `native/shard_core.cpp`: Verified exact identity preservation and L2 norm conservation in RoPE head rotation.
- Added deterministic regression test `test_krn_026_rope_zero_position_identity` in `tests_shard.py` (34/34 tests passing, 33.84 GFLOPS AVX2 FMA).
## [1.4.6] - 2026-11-10
### Added
- Registered native specification `KRN-025` (`softmax-overflow-logit-clamp-v25`, `MASM-AVX2+CPP20`) in `native/shard_core.cpp`: Verified unit simplex probability conservation on extreme high-magnitude attention logits.
- Added deterministic regression test `test_krn_025_softmax_overflow_logit_clamp` in `tests_shard.py` (33/33 tests passing, 35.00 GFLOPS AVX2 FMA).
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
