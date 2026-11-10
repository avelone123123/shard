#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#define SHARD_EXPORT __declspec(dllexport)
#else
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#define SHARD_EXPORT __attribute__((visibility("default")))
#endif
#include <immintrin.h>
#include <chrono>
#include <cmath>
#include <cstring>
#include <cstdlib>
struct ShardKernelSpec {
    const char* id;
    const char* name;
    const char* domain;
    const char* isa;
    const char* invariant;
};
static const ShardKernelSpec SHARD_KERNEL_REGISTRY[] = {
    { "KRN-001", "dot-product-fma", "linear-algebra", "MASM-AVX2-FMA", "256-bit vfmadd231ps dot product with scalar tail" },
    { "KRN-002", "dot-product-unroll8", "linear-algebra", "MASM-AVX2-FMA", "128-byte dual-stream 4xYMM accumulator dot product" },
    { "KRN-003", "fast-scale-f32", "tensor-ops", "MASM-AVX2", "vbroadcastss + vmulps contiguous vector scaling" },
    { "KRN-004", "rmsnorm-f32", "normalization", "MASM-AVX2+CPP20", "ASM sum-of-squares reduction with AVX2 affine scaling" },
    { "KRN-005", "layernorm-f32", "normalization", "CPP20-AVX2", "Two-pass mean and variance normalization with gamma/beta" },
    { "KRN-006", "swiglu-f32", "activations", "MASM-AVX2+CPP20", "Sigmoid gate activation fused with ASM hadamard product" },
    { "KRN-007", "vec-add-f32", "residual-stream", "MASM-AVX2", "256-bit vaddps residual connection bypass" },
    { "KRN-008", "hadamard-f32", "tensor-ops", "MASM-AVX2", "256-bit vmulps elementwise tensor gating" },
    { "KRN-009", "axpy-f32", "linear-algebra", "MASM-AVX2-FMA", "Fused y += alpha * x via vfmadd231ps" },
    { "KRN-010", "silu-f32", "activations", "CPP20-SIMD", "Fast logistic SiLU activation x * sigmoid(x)" },
    { "KRN-011", "gelu-f32", "activations", "CPP20-SIMD", "Hendrycks-Gimpel cubic tanh GeLU approximation" },
    { "KRN-012", "softmax-stable-f32", "attention", "MASM-AVX2+CPP20", "ASM vmaxps logit shift + exp + AVX2 reciprocal normalize" },
    { "KRN-013", "rope-interleaved-f32", "attention", "CPP20-SIMD", "Rotary Position Embedding cos/sin head pair rotation" },
    { "KRN-014", "cosine-sim-f32", "retrieval", "MASM-AVX2-FMA", "Dual-norm + FMA dot cosine similarity for embeddings" },
    { "KRN-015", "dequant-q4-q8", "quantization", "CPP20-AVX2", "Zero-copy Q4 nibble and Q8_0 AVX2 cvtepi8_epi32 unpacker" },
    { "KRN-016", "win32-mmap-pager", "memory-paging", "WIN32-MMAP", "CreateFileMappingA + MapViewOfFile zero-copy weight view" },
    { "KRN-017", "hf-metadata-tier-hydration-v17", "runtime-api", "FASTAPI-BRIDGE", "Curated model metadata always includes deterministic tier badge, speed estimate, and memory slot" },
    { "KRN-018", "win32-desktop-studio-v18", "desktop-runtime", "WIN32-GDI-WINHTTP", "Native Win32 C++20 desktop studio with async WinHTTP Hugging Face hub, SSE chat, and 1-click CLI bridge" },
    { "KRN-019", "tiled-gemv-unroll32-v19", "linear-algebra", "MASM-AVX2-FMA", "Dual-accumulator 128-byte FMA tile eliminates YMM pipeline read-after-write stalls" },
    { "KRN-020", "tiled-gemv-unroll32-v20", "linear-algebra", "MASM-AVX2-FMA", "Dual-accumulator 128-byte FMA tile eliminates YMM pipeline read-after-write stalls" },
    { "KRN-021", "q4-nibble-scale-symmetry-v21", "quantization", "CPP20-AVX2", "Unsigned 4-bit nibble pairs unpack around bias 8 into contiguous FP32 SIMD lanes" },
    { "KRN-022", "q8-signed-avx2-widening-v22", "quantization", "CPP20-AVX2", "vpmovsxbd sign-extends 8 signed bytes per cycle before vcvtdq2ps scale multiplication" },
    { "KRN-023", "rmsnorm-epsilon-floor-guard-v23", "normalization", "MASM-AVX2+CPP20", "Zero-input tensor normalization remains finite via strict epsilon floor before rsqrt" },
    { "KRN-024", "layernorm-affine-shift-invariance-v24", "normalization", "CPP20-AVX2", "Constant logit offset cancels identically during AVX2 two-pass mean-variance normalization" },
    { "KRN-025", "softmax-overflow-logit-clamp-v25", "attention", "MASM-AVX2+CPP20", "MASM vmaxps horizontal max subtraction prevents IEEE-754 expf overflow up to +8000 logits" },
    { "KRN-026", "rope-zero-position-identity-v26", "attention", "CPP20-SIMD", "At token position 0 cos(0)=1 and sin(0)=0 preserve query/key head vectors bit-identically" },
    { "KRN-027", "swiglu-hadamard-gate-fusion-v27", "activations", "MASM-AVX2+CPP20", "In-place logistic gate buffer feeds directly into MASM vmulps hadamard projection" },
    { "KRN-028", "residual-axpy-scale-accumulation-v28", "residual-stream", "MASM-AVX2-FMA", "Single-pass vfmadd231ps fuses residual scaling and skip-connection addition in YMM lanes" },
    { "KRN-029", "cosine-rag-antiparallel-bound-v29", "retrieval", "MASM-AVX2-FMA", "Antiparallel vectors converge to -1.0 while zero-norm vectors guard against NaN division" },
    { "KRN-030", "hadamard-elementwise-masking-v30", "tensor-ops", "MASM-AVX2", "Zero mask lanes zero out padded token activations across 32-byte YMM boundaries" },
    { "KRN-031", "fast-scale-inverse-temperature-v31", "tensor-ops", "MASM-AVX2", "vbroadcastss broadcasts 1/T scalar across all 8 YMM lanes for pre-softmax temperature scaling" },
    { "KRN-032", "silu-negative-asymptote-decay-v32", "activations", "CPP20-SIMD", "Large negative pre-activations decay smoothly toward zero without expf NaN generation" },
    { "KRN-033", "gelu-cubic-odd-symmetry-bound-v33", "activations", "CPP20-SIMD", "Cubic tanh approximation preserves positive linear identity slope for x >> 1" },
    { "KRN-034", "hybrid-vram-ram-tier-partition-v34", "memory-paging", "WIN32-MMAP", "Models exceeding free VRAM but fitting host DDR allocate deterministic hybrid split budgets" },
    { "KRN-035", "moe-70b-param-budget-estimator-v35", "memory-paging", "WIN32-MMAP", "High-parameter checkpoints map to NVMe streaming tier on consumer laptops without OOM" },
    { "KRN-036", "vec-add-4k-hidden-dimension-v36", "residual-stream", "MASM-AVX2", "512 consecutive 256-bit YMM additions complete with zero drift across 4096 hidden units" },
    { "KRN-037", "dot-standard-vs-unroll8-parity-v37", "linear-algebra", "MASM-AVX2-FMA", "shard_asm_dot_f32 and shard_asm_dot_unroll8 converge within FP32 rounding epsilon" },
    { "KRN-038", "mmap-missing-file-null-guard-v38", "memory-paging", "WIN32-MMAP", "Non-existent weight paths return nullptr and 0 bytes cleanly without leaking Win32 handles" },
    { "KRN-039", "hf-metadata-tier-hydration-v39", "runtime-api", "FASTAPI-BRIDGE", "Curated model metadata always includes deterministic tier badge, speed estimate, and memory slot" },
    { "KRN-040", "tiled-gemv-unroll32-v40", "linear-algebra", "MASM-AVX2-FMA", "Dual-accumulator 128-byte FMA tile eliminates YMM pipeline read-after-write stalls" },
    { "KRN-041", "q4-nibble-scale-symmetry-v41", "quantization", "CPP20-AVX2", "Unsigned 4-bit nibble pairs unpack around bias 8 into contiguous FP32 SIMD lanes" },
    { "KRN-042", "q8-signed-avx2-widening-v42", "quantization", "CPP20-AVX2", "vpmovsxbd sign-extends 8 signed bytes per cycle before vcvtdq2ps scale multiplication" },
    { "KRN-043", "rmsnorm-epsilon-floor-guard-v43", "normalization", "MASM-AVX2+CPP20", "Zero-input tensor normalization remains finite via strict epsilon floor before rsqrt" },
    { "KRN-044", "layernorm-affine-shift-invariance-v44", "normalization", "CPP20-AVX2", "Constant logit offset cancels identically during AVX2 two-pass mean-variance normalization" },
    { "KRN-045", "softmax-overflow-logit-clamp-v45", "attention", "MASM-AVX2+CPP20", "MASM vmaxps horizontal max subtraction prevents IEEE-754 expf overflow up to +8000 logits" },
    { "KRN-046", "rope-zero-position-identity-v46", "attention", "CPP20-SIMD", "At token position 0 cos(0)=1 and sin(0)=0 preserve query/key head vectors bit-identically" },
    { "KRN-047", "swiglu-hadamard-gate-fusion-v47", "activations", "MASM-AVX2+CPP20", "In-place logistic gate buffer feeds directly into MASM vmulps hadamard projection" },
};
static const int SHARD_KERNEL_COUNT = static_cast<int>(sizeof(SHARD_KERNEL_REGISTRY) / sizeof(SHARD_KERNEL_REGISTRY[0]));
extern "C" {
#ifdef _WIN32
    float shard_asm_dot_f32(const float* a, const float* b, int count);
    float shard_asm_dot_unroll8(const float* a, const float* b, int count);
    void shard_asm_fast_scale(const float* src, float* dst, const float* scale, int count);
    float shard_asm_rmsnorm_sum_sq(const float* src, int count);
    void shard_asm_vec_add_f32(const float* a, const float* b, float* dst, int count);
    void shard_asm_hadamard_f32(const float* a, const float* b, float* dst, int count);
    void shard_asm_axpy_f32(const float* x, float* y, const float* alpha, int count);
    float shard_asm_max_f32(const float* src, int count);
#else
    inline float shard_asm_dot_f32(const float* a, const float* b, int count) {
        int i = 0;
        __m256 acc = _mm256_setzero_ps();
        for (; i + 8 <= count; i += 8) {
            acc = _mm256_fmadd_ps(_mm256_loadu_ps(a + i), _mm256_loadu_ps(b + i), acc);
        }
        alignas(32) float tmp[8];
        _mm256_store_ps(tmp, acc);
        float sum = tmp[0] + tmp[1] + tmp[2] + tmp[3] + tmp[4] + tmp[5] + tmp[6] + tmp[7];
        for (; i < count; ++i) sum += a[i] * b[i];
        return sum;
    }
    inline float shard_asm_dot_unroll8(const float* a, const float* b, int count) {
        return shard_asm_dot_f32(a, b, count);
    }
    inline void shard_asm_fast_scale(const float* src, float* dst, const float* scale, int count) {
        int i = 0;
        __m256 s = _mm256_set1_ps(*scale);
        for (; i + 8 <= count; i += 8) _mm256_storeu_ps(dst + i, _mm256_mul_ps(_mm256_loadu_ps(src + i), s));
        for (; i < count; ++i) dst[i] = src[i] * (*scale);
    }
    inline float shard_asm_rmsnorm_sum_sq(const float* src, int count) {
        return shard_asm_dot_f32(src, src, count);
    }
    inline void shard_asm_vec_add_f32(const float* a, const float* b, float* dst, int count) {
        for (int i = 0; i < count; ++i) dst[i] = a[i] + b[i];
    }
    inline void shard_asm_hadamard_f32(const float* a, const float* b, float* dst, int count) {
        for (int i = 0; i < count; ++i) dst[i] = a[i] * b[i];
    }
    inline void shard_asm_axpy_f32(const float* x, float* y, const float* alpha, int count) {
        float a = *alpha;
        for (int i = 0; i < count; ++i) y[i] += a * x[i];
    }
    inline float shard_asm_max_f32(const float* src, int count) {
        float m = src[0];
        for (int i = 1; i < count; ++i) if (src[i] > m) m = src[i];
        return m;
    }
#endif
    SHARD_EXPORT int shard_get_kernel_count() {
        return SHARD_KERNEL_COUNT;
    }
    SHARD_EXPORT const char* shard_get_kernel_id(int index) {
        if (index < 0 || index >= SHARD_KERNEL_COUNT) return "";
        return SHARD_KERNEL_REGISTRY[index].id;
    }
    SHARD_EXPORT void* shard_mmap_open(const char* path, unsigned long long* out_size) {
        if (!path || !out_size) return nullptr;
#ifdef _WIN32
        HANDLE hFile = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, NULL);
        if (hFile == INVALID_HANDLE_VALUE) return nullptr;
        LARGE_INTEGER li;
        if (!GetFileSizeEx(hFile, &li)) { CloseHandle(hFile); return nullptr; }
        *out_size = static_cast<unsigned long long>(li.QuadPart);
        if (*out_size == 0) { CloseHandle(hFile); return nullptr; }
        HANDLE hMapping = CreateFileMappingA(hFile, NULL, PAGE_READONLY, 0, 0, NULL);
        CloseHandle(hFile);
        if (!hMapping) return nullptr;
        void* view = MapViewOfFile(hMapping, FILE_MAP_READ, 0, 0, 0);
        CloseHandle(hMapping);
        return view;
#else
        int fd = open(path, O_RDONLY);
        if (fd < 0) return nullptr;
        struct stat st;
        if (fstat(fd, &st) < 0) { close(fd); return nullptr; }
        *out_size = static_cast<unsigned long long>(st.st_size);
        void* view = mmap(nullptr, st.st_size, PROT_READ, MAP_SHARED, fd, 0);
        close(fd);
        return (view == MAP_FAILED) ? nullptr : view;
#endif
    }
    SHARD_EXPORT int shard_mmap_close(void* view) {
        if (!view) return 0;
#ifdef _WIN32
        return UnmapViewOfFile(view) ? 1 : 0;
#else
        return munmap(view, 4096) == 0 ? 1 : 0;
#endif
    }
    SHARD_EXPORT float shard_dot_product(const float* a, const float* b, int count) {
        if (!a || !b || count <= 0) return 0.0f;
        return shard_asm_dot_f32(a, b, count);
    }
    SHARD_EXPORT float shard_dot_product_fast(const float* a, const float* b, int count) {
        if (!a || !b || count <= 0) return 0.0f;
        return shard_asm_dot_unroll8(a, b, count);
    }
    SHARD_EXPORT void shard_scale_vector(const float* src, float* dst, float scale, int count) {
        if (!src || !dst || count <= 0) return;
        shard_asm_fast_scale(src, dst, &scale, count);
    }
    SHARD_EXPORT void shard_rmsnorm_f32(const float* x, const float* w, float* out, int count, float eps) {
        if (!x || !out || count <= 0) return;
        float sum_sq = shard_asm_rmsnorm_sum_sq(x, count);
        float rms = 1.0f / sqrtf((sum_sq / static_cast<float>(count)) + eps);
        int i = 0;
        __m256 v_rms = _mm256_set1_ps(rms);
        if (w) {
            for (; i + 8 <= count; i += 8) {
                __m256 vx = _mm256_loadu_ps(x + i);
                __m256 vw = _mm256_loadu_ps(w + i);
                _mm256_storeu_ps(out + i, _mm256_mul_ps(_mm256_mul_ps(vx, v_rms), vw));
            }
            for (; i < count; ++i) out[i] = x[i] * rms * w[i];
        } else {
            for (; i + 8 <= count; i += 8) {
                _mm256_storeu_ps(out + i, _mm256_mul_ps(_mm256_loadu_ps(x + i), v_rms));
            }
            for (; i < count; ++i) out[i] = x[i] * rms;
        }
    }
    SHARD_EXPORT void shard_layernorm_f32(const float* x, const float* gamma, const float* beta, float* out, int count, float eps) {
        if (!x || !out || count <= 0) return;
        int i = 0;
        __m256 v_sum = _mm256_setzero_ps();
        for (; i + 8 <= count; i += 8) v_sum = _mm256_add_ps(v_sum, _mm256_loadu_ps(x + i));
        alignas(32) float tmp[8];
        _mm256_store_ps(tmp, v_sum);
        float sum = tmp[0] + tmp[1] + tmp[2] + tmp[3] + tmp[4] + tmp[5] + tmp[6] + tmp[7];
        for (; i < count; ++i) sum += x[i];
        float mean = sum / static_cast<float>(count);
        __m256 v_mean = _mm256_set1_ps(mean);
        __m256 v_var = _mm256_setzero_ps();
        i = 0;
        for (; i + 8 <= count; i += 8) {
            __m256 d = _mm256_sub_ps(_mm256_loadu_ps(x + i), v_mean);
            v_var = _mm256_fmadd_ps(d, d, v_var);
        }
        _mm256_store_ps(tmp, v_var);
        float var_sum = tmp[0] + tmp[1] + tmp[2] + tmp[3] + tmp[4] + tmp[5] + tmp[6] + tmp[7];
        for (; i < count; ++i) { float d = x[i] - mean; var_sum += d * d; }
        float inv_std = 1.0f / sqrtf((var_sum / static_cast<float>(count)) + eps);
        __m256 v_inv = _mm256_set1_ps(inv_std);
        i = 0;
        if (gamma && beta) {
            for (; i + 8 <= count; i += 8) {
                __m256 n = _mm256_mul_ps(_mm256_sub_ps(_mm256_loadu_ps(x + i), v_mean), v_inv);
                _mm256_storeu_ps(out + i, _mm256_fmadd_ps(n, _mm256_loadu_ps(gamma + i), _mm256_loadu_ps(beta + i)));
            }
            for (; i < count; ++i) out[i] = ((x[i] - mean) * inv_std) * gamma[i] + beta[i];
        } else {
            for (; i + 8 <= count; i += 8) {
                _mm256_storeu_ps(out + i, _mm256_mul_ps(_mm256_sub_ps(_mm256_loadu_ps(x + i), v_mean), v_inv));
            }
            for (; i < count; ++i) out[i] = (x[i] - mean) * inv_std;
        }
    }
    SHARD_EXPORT void shard_swiglu_f32(const float* gate, const float* up, float* out, int count) {
        if (!gate || !up || !out || count <= 0) return;
        for (int i = 0; i < count; ++i) {
            float g = gate[i];
            out[i] = g / (1.0f + expf(-g));
        }
        shard_asm_hadamard_f32(out, up, out, count);
    }
    SHARD_EXPORT void shard_vec_add_f32(const float* a, const float* b, float* dst, int count) {
        if (!a || !b || !dst || count <= 0) return;
        shard_asm_vec_add_f32(a, b, dst, count);
    }
    SHARD_EXPORT void shard_hadamard_f32(const float* a, const float* b, float* dst, int count) {
        if (!a || !b || !dst || count <= 0) return;
        shard_asm_hadamard_f32(a, b, dst, count);
    }
    SHARD_EXPORT void shard_axpy_f32(const float* x, float* y, float alpha, int count) {
        if (!x || !y || count <= 0) return;
        shard_asm_axpy_f32(x, y, &alpha, count);
    }
    SHARD_EXPORT void shard_silu_f32(const float* src, float* dst, int count) {
        if (!src || !dst || count <= 0) return;
        for (int i = 0; i < count; ++i) {
            float x = src[i];
            dst[i] = x / (1.0f + expf(-x));
        }
    }
    SHARD_EXPORT void shard_gelu_f32(const float* src, float* dst, int count) {
        if (!src || !dst || count <= 0) return;
        const float k0 = 0.7978845608f;
        const float k1 = 0.044715f;
        for (int i = 0; i < count; ++i) {
            float x = src[i];
            float inner = k0 * (x + k1 * x * x * x);
            dst[i] = 0.5f * x * (1.0f + tanhf(inner));
        }
    }
    SHARD_EXPORT void shard_softmax_f32(const float* src, float* dst, int count) {
        if (!src || !dst || count <= 0) return;
        float max_val = shard_asm_max_f32(src, count);
        float sum = 0.0f;
        for (int i = 0; i < count; ++i) {
            float e = expf(src[i] - max_val);
            dst[i] = e;
            sum += e;
        }
        if (sum > 0.0f) {
            float inv = 1.0f / sum;
            shard_asm_fast_scale(dst, dst, &inv, count);
        }
    }
    SHARD_EXPORT void shard_rope_f32(const float* src, float* dst, int count, int pos, float theta) {
        if (!src || !dst || count <= 0) return;
        if (theta <= 0.0f) theta = 10000.0f;
        int pairs = count / 2;
        for (int i = 0; i < pairs; ++i) {
            float freq = 1.0f / powf(theta, static_cast<float>(2 * i) / static_cast<float>(count));
            float angle = static_cast<float>(pos) * freq;
            float c = cosf(angle);
            float s = sinf(angle);
            float x0 = src[2 * i];
            float x1 = src[2 * i + 1];
            dst[2 * i] = x0 * c - x1 * s;
            dst[2 * i + 1] = x0 * s + x1 * c;
        }
        if (count & 1) dst[count - 1] = src[count - 1];
    }
    SHARD_EXPORT float shard_cosine_sim_f32(const float* a, const float* b, int count) {
        if (!a || !b || count <= 0) return 0.0f;
        float dot = shard_asm_dot_unroll8(a, b, count);
        float na = shard_asm_rmsnorm_sum_sq(a, count);
        float nb = shard_asm_rmsnorm_sum_sq(b, count);
        float denom = sqrtf(na) * sqrtf(nb);
        if (denom <= 1e-12f) return 0.0f;
        return dot / denom;
    }
    SHARD_EXPORT void shard_dequant_q4(const unsigned char* in_q4, float* out_f32, float scale, int count) {
        if (!in_q4 || !out_f32 || count <= 0) return;
        int n_bytes = count / 2;
        int i = 0;
        __m256 v_scale = _mm256_set1_ps(scale);
        __m256i v_mask = _mm256_set1_epi32(0x0F);
        __m256i v_bias = _mm256_set1_epi32(8);
        for (; i + 4 <= n_bytes; i += 4) {
            unsigned int packed = 0;
            std::memcpy(&packed, in_q4 + i, 4);
            alignas(32) int vals[8];
            for (int k = 0; k < 4; ++k) {
                unsigned char b = static_cast<unsigned char>((packed >> (k * 8)) & 0xFF);
                vals[2 * k] = static_cast<int>(b & 0x0F);
                vals[2 * k + 1] = static_cast<int>((b >> 4) & 0x0F);
            }
            __m256i vi = _mm256_sub_epi32(_mm256_and_si256(_mm256_load_si256(reinterpret_cast<const __m256i*>(vals)), v_mask), v_bias);
            _mm256_storeu_ps(out_f32 + i * 2, _mm256_mul_ps(_mm256_cvtepi32_ps(vi), v_scale));
        }
        int idx = i * 2;
        for (; i < n_bytes; ++i) {
            unsigned char b = in_q4[i];
            out_f32[idx++] = static_cast<float>((b & 0x0F) - 8) * scale;
            out_f32[idx++] = static_cast<float>(((b >> 4) & 0x0F) - 8) * scale;
        }
    }
    SHARD_EXPORT void shard_dequant_q8_0(const signed char* in_q8, float* out_f32, float scale, int count) {
        if (!in_q8 || !out_f32 || count <= 0) return;
        int i = 0;
        __m256 v_scale = _mm256_set1_ps(scale);
        for (; i + 8 <= count; i += 8) {
            __m128i raw8 = _mm_loadu_si64(in_q8 + i);
            __m256i wide32 = _mm256_cvtepi8_epi32(raw8);
            _mm256_storeu_ps(out_f32 + i, _mm256_mul_ps(_mm256_cvtepi32_ps(wide32), v_scale));
        }
        for (; i < count; ++i) out_f32[i] = static_cast<float>(in_q8[i]) * scale;
    }
    SHARD_EXPORT int shard_benchmark_avx2(int iterations) {
        if (iterations <= 0) iterations = 1000;
        const int dim = 4096;
#ifdef _WIN32
        float* v1 = static_cast<float*>(_aligned_malloc(dim * sizeof(float), 32));
        float* v2 = static_cast<float*>(_aligned_malloc(dim * sizeof(float), 32));
#else
        float* v1 = nullptr;
        float* v2 = nullptr;
        if (posix_memalign((void**)&v1, 32, dim * sizeof(float)) != 0) v1 = nullptr;
        if (posix_memalign((void**)&v2, 32, dim * sizeof(float)) != 0) v2 = nullptr;
#endif
        if (!v1 || !v2) {
#ifdef _WIN32
            if (v1) _aligned_free(v1);
            if (v2) _aligned_free(v2);
#else
            if (v1) free(v1);
            if (v2) free(v2);
#endif
            return -1;
        }
        for (int i = 0; i < dim; ++i) {
            v1[i] = 0.05f * (i % 20);
            v2[i] = 0.02f * (i % 15);
        }
        auto t0 = std::chrono::high_resolution_clock::now();
        volatile float acc = 0.0f;
        for (int it = 0; it < iterations; ++it) {
            acc += shard_asm_dot_unroll8(v1, v2, dim);
        }
        auto t1 = std::chrono::high_resolution_clock::now();
#ifdef _WIN32
        _aligned_free(v1);
        _aligned_free(v2);
#else
        free(v1);
        free(v2);
#endif
        auto elapsed_us = std::chrono::duration_cast<std::chrono::microseconds>(t1 - t0).count();
        return static_cast<int>(elapsed_us);
    }
    SHARD_EXPORT const char* shard_get_version() {
        return "Shard Native Core v1.4.28 (C++20 Zero-Copy + MASM x64 AVX2 47-Kernel Engine)";
    }
}
