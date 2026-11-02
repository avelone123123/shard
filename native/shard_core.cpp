#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <immintrin.h>
#include <chrono>
#include <cstring>
#include <cstdlib>
extern "C" {
    float shard_asm_dot_f32(const float* a, const float* b, int count);
    void shard_asm_fast_scale(const float* src, float* dst, const float* scale, int count);
    __declspec(dllexport) void* shard_mmap_open(const char* path, unsigned __int64* out_size) {
        if (!path || !out_size) return nullptr;
        HANDLE hFile = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, NULL);
        if (hFile == INVALID_HANDLE_VALUE) return nullptr;
        LARGE_INTEGER li;
        if (!GetFileSizeEx(hFile, &li)) {
            CloseHandle(hFile);
            return nullptr;
        }
        *out_size = static_cast<unsigned __int64>(li.QuadPart);
        HANDLE hMapping = CreateFileMappingA(hFile, NULL, PAGE_READONLY, 0, 0, NULL);
        CloseHandle(hFile);
        if (!hMapping) return nullptr;
        void* view = MapViewOfFile(hMapping, FILE_MAP_READ, 0, 0, 0);
        CloseHandle(hMapping);
        return view;
    }
    __declspec(dllexport) int shard_mmap_close(void* view) {
        if (!view) return 0;
        return UnmapViewOfFile(view) ? 1 : 0;
    }
    __declspec(dllexport) float shard_dot_product(const float* a, const float* b, int count) {
        if (!a || !b || count <= 0) return 0.0f;
        return shard_asm_dot_f32(a, b, count);
    }
    __declspec(dllexport) void shard_scale_vector(const float* src, float* dst, float scale, int count) {
        if (!src || !dst || count <= 0) return;
        shard_asm_fast_scale(src, dst, &scale, count);
    }
    __declspec(dllexport) void shard_dequant_q4(const unsigned char* in_q4, float* out_f32, float scale, int count) {
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
    __declspec(dllexport) int shard_benchmark_avx2(int iterations) {
        if (iterations <= 0) iterations = 1000;
        const int dim = 4096;
        float* v1 = static_cast<float*>(_aligned_malloc(dim * sizeof(float), 32));
        float* v2 = static_cast<float*>(_aligned_malloc(dim * sizeof(float), 32));
        if (!v1 || !v2) {
            if (v1) _aligned_free(v1);
            if (v2) _aligned_free(v2);
            return -1;
        }
        for (int i = 0; i < dim; ++i) {
            v1[i] = 0.05f * (i % 20);
            v2[i] = 0.02f * (i % 15);
        }
        auto t0 = std::chrono::high_resolution_clock::now();
        volatile float acc = 0.0f;
        for (int it = 0; it < iterations; ++it) {
            acc += shard_asm_dot_f32(v1, v2, dim);
        }
        auto t1 = std::chrono::high_resolution_clock::now();
        _aligned_free(v1);
        _aligned_free(v2);
        auto elapsed_us = std::chrono::duration_cast<std::chrono::microseconds>(t1 - t0).count();
        return static_cast<int>(elapsed_us);
    }
    __declspec(dllexport) const char* shard_get_version() {
        return "Shard Native Core v1.0.0 (C++20 + MASM x64 AVX2/FMA Assembly)";
    }
}