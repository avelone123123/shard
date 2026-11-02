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
extern "C" {
#ifdef _WIN32
    float shard_asm_dot_f32(const float* a, const float* b, int count);
    float shard_asm_dot_unroll8(const float* a, const float* b, int count);
    void shard_asm_fast_scale(const float* src, float* dst, const float* scale, int count);
    float shard_asm_rmsnorm_sum_sq(const float* src, int count);
#else
    inline float shard_asm_dot_f32(const float* a, const float* b, int count) {
        int i = 0;
        __m256 acc = _mm256_setzero_ps();
        for (; i + 8 <= count; i += 8) {
            __m256 va = _mm256_loadu_ps(a + i);
            __m256 vb = _mm256_loadu_ps(b + i);
            acc = _mm256_fmadd_ps(va, vb, acc);
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
        for (; i + 8 <= count; i += 8) {
            __m256 v = _mm256_loadu_ps(src + i);
            _mm256_storeu_ps(dst + i, _mm256_mul_ps(v, s));
        }
        for (; i < count; ++i) dst[i] = src[i] * (*scale);
    }
    inline float shard_asm_rmsnorm_sum_sq(const float* src, int count) {
        int i = 0;
        __m256 acc = _mm256_setzero_ps();
        for (; i + 8 <= count; i += 8) {
            __m256 v = _mm256_loadu_ps(src + i);
            acc = _mm256_fmadd_ps(v, v, acc);
        }
        alignas(32) float tmp[8];
        _mm256_store_ps(tmp, acc);
        float sum = tmp[0] + tmp[1] + tmp[2] + tmp[3] + tmp[4] + tmp[5] + tmp[6] + tmp[7];
        for (; i < count; ++i) sum += src[i] * src[i];
        return sum;
    }
#endif
    SHARD_EXPORT void* shard_mmap_open(const char* path, unsigned long long* out_size) {
        if (!path || !out_size) return nullptr;
#ifdef _WIN32
        HANDLE hFile = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, NULL);
        if (hFile == INVALID_HANDLE_VALUE) return nullptr;
        LARGE_INTEGER li;
        if (!GetFileSizeEx(hFile, &li)) { CloseHandle(hFile); return nullptr; }
        *out_size = static_cast<unsigned long long>(li.QuadPart);
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
                __m256 v_norm = _mm256_mul_ps(vx, v_rms);
                _mm256_storeu_ps(out + i, _mm256_mul_ps(v_norm, vw));
            }
            for (; i < count; ++i) out[i] = x[i] * rms * w[i];
        } else {
            for (; i + 8 <= count; i += 8) {
                __m256 vx = _mm256_loadu_ps(x + i);
                _mm256_storeu_ps(out + i, _mm256_mul_ps(vx, v_rms));
            }
            for (; i < count; ++i) out[i] = x[i] * rms;
        }
    }
    SHARD_EXPORT void shard_swiglu_f32(const float* gate, const float* up, float* out, int count) {
        if (!gate || !up || !out || count <= 0) return;
        for (int i = 0; i < count; ++i) {
            float g = gate[i];
            float sig = 1.0f / (1.0f + expf(-g));
            out[i] = (g * sig) * up[i];
        }
    }
    SHARD_EXPORT void shard_dequant_q4(const unsigned char* in_q4, float* out_f32, float scale, int count) {
        if (!in_q4 || !out_f32 || count <= 0) return;
        int n_bytes = count / 2;
        int idx = 0;
        for (int i = 0; i < n_bytes; ++i) {
            unsigned char b = in_q4[i];
            int low = (b & 0x0F) - 8;
            int high = ((b >> 4) & 0x0F) - 8;
            out_f32[idx++] = static_cast<float>(low) * scale;
            out_f32[idx++] = static_cast<float>(high) * scale;
        }
    }
    SHARD_EXPORT void shard_dequant_q8_0(const signed char* in_q8, float* out_f32, float scale, int count) {
        if (!in_q8 || !out_f32 || count <= 0) return;
        int i = 0;
        __m256 v_scale = _mm256_set1_ps(scale);
        for (; i + 8 <= count; i += 8) {
            __m128i raw8 = _mm_loadu_si64(in_q8 + i);
            __m256i wide32 = _mm256_cvtepi8_epi32(raw8);
            __m256 f32_vals = _mm256_cvtepi32_ps(wide32);
            _mm256_storeu_ps(out_f32 + i, _mm256_mul_ps(f32_vals, v_scale));
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
        return "Shard Native Core v1.1.0 (C++20 Zero-Copy + MASM AVX2 Dual-Stream SIMD)";
    }
}