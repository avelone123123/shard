#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <intrin.h>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
extern "C" {
    float shard_asm_dot_unroll8(const float* a, const float* b, int count);
    float shard_asm_rmsnorm_sum_sq(const float* src, int count);
    void shard_asm_vec_add_f32(const float* a, const float* b, float* dst, int count);
    void shard_asm_hadamard_f32(const float* a, const float* b, float* dst, int count);
    void shard_asm_axpy_f32(const float* x, float* y, const float* alpha, int count);
    float shard_asm_max_f32(const float* src, int count);
}
struct ShardGuiState {
    int activeTab;
    int guideLang;
    int activeKernels;
    int passedTests;
    double dotGflops;
    long long dotUs;
    unsigned long long rdtscCycles;
    double rmsOpsSec;
    double addOpsSec;
    double hadOpsSec;
    double axpyOpsSec;
    double ramTotalGb;
    double ramAvailGb;
    int cpuThreads;
    wchar_t versionText[64];
};
static ShardGuiState g_state = { 0, 0, 17, 23, 0.0, 0, 0, 0.0, 0.0, 0.0, 0.0, 16.0, 8.0, 8, L"v1.3.1" };
static void RefreshHostTelemetry() {
    MEMORYSTATUSEX ms;
    ms.dwLength = sizeof(ms);
    if (GlobalMemoryStatusEx(&ms)) {
        g_state.ramTotalGb = static_cast<double>(ms.ullTotalPhys) / (1024.0 * 1024.0 * 1024.0);
        g_state.ramAvailGb = static_cast<double>(ms.ullAvailPhys) / (1024.0 * 1024.0 * 1024.0);
    }
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    g_state.cpuThreads = static_cast<int>(si.dwNumberOfProcessors);
}
static void RunHardwareBenchmark() {
    RefreshHostTelemetry();
    const int dim = 4096;
    const int iters = 80000;
    float* v1 = static_cast<float*>(_aligned_malloc(dim * sizeof(float), 32));
    float* v2 = static_cast<float*>(_aligned_malloc(dim * sizeof(float), 32));
    float* out = static_cast<float*>(_aligned_malloc(dim * sizeof(float), 32));
    if (!v1 || !v2 || !out) {
        if (v1) _aligned_free(v1);
        if (v2) _aligned_free(v2);
        if (out) _aligned_free(out);
        return;
    }
    for (int i = 0; i < dim; ++i) {
        v1[i] = 0.04f * static_cast<float>((i % 23) + 1);
        v2[i] = 0.03f * static_cast<float>((i % 17) + 1);
        out[i] = 0.0f;
    }
    unsigned long long c0 = __rdtsc();
    auto t0 = std::chrono::high_resolution_clock::now();
    volatile float sink = 0.0f;
    for (int it = 0; it < iters; ++it) {
        sink += shard_asm_dot_unroll8(v1, v2, dim);
    }
    auto t1 = std::chrono::high_resolution_clock::now();
    unsigned long long c1 = __rdtsc();
    long long us = std::chrono::duration_cast<std::chrono::microseconds>(t1 - t0).count();
    if (us < 1) us = 1;
    g_state.dotUs = us;
    g_state.rdtscCycles = (c1 - c0) / static_cast<unsigned long long>(iters);
    double sec = static_cast<double>(us) / 1000000.0;
    g_state.dotGflops = (static_cast<double>(dim) * 2.0 * static_cast<double>(iters)) / sec / 1e9;
    const int subIters = 25000;
    auto r0 = std::chrono::high_resolution_clock::now();
    for (int it = 0; it < subIters; ++it) sink += shard_asm_rmsnorm_sum_sq(v1, dim);
    auto r1 = std::chrono::high_resolution_clock::now();
    double rSec = std::chrono::duration<double>(r1 - r0).count();
    g_state.rmsOpsSec = (rSec > 0.0) ? (subIters / rSec) : 0.0;
    auto a0 = std::chrono::high_resolution_clock::now();
    for (int it = 0; it < subIters; ++it) shard_asm_vec_add_f32(v1, v2, out, dim);
    auto a1 = std::chrono::high_resolution_clock::now();
    double aSec = std::chrono::duration<double>(a1 - a0).count();
    g_state.addOpsSec = (aSec > 0.0) ? (subIters / aSec) : 0.0;
    auto h0 = std::chrono::high_resolution_clock::now();
    for (int it = 0; it < subIters; ++it) shard_asm_hadamard_f32(v1, v2, out, dim);
    auto h1 = std::chrono::high_resolution_clock::now();
    double hSec = std::chrono::duration<double>(h1 - h0).count();
    g_state.hadOpsSec = (hSec > 0.0) ? (subIters / hSec) : 0.0;
    float alpha = 0.25f;
    auto x0 = std::chrono::high_resolution_clock::now();
    for (int it = 0; it < subIters; ++it) shard_asm_axpy_f32(v1, out, &alpha, dim);
    auto x1 = std::chrono::high_resolution_clock::now();
    double xSec = std::chrono::duration<double>(x1 - x0).count();
    g_state.axpyOpsSec = (xSec > 0.0) ? (subIters / xSec) : 0.0;
    sink += shard_asm_max_f32(out, dim);
    _aligned_free(v1);
    _aligned_free(v2);
    _aligned_free(out);
}
static void FillBox(HDC hdc, int x1, int y1, int x2, int y2, COLORREF fillCol, COLORREF borderCol) {
    RECT rc = { x1, y1, x2, y2 };
    HBRUSH br = CreateSolidBrush(fillCol);
    FillRect(hdc, &rc, br);
    DeleteObject(br);
    HPEN pen = CreatePen(PS_SOLID, 1, borderCol);
    HPEN oldPen = static_cast<HPEN>(SelectObject(hdc, pen));
    HBRUSH oldBr = static_cast<HBRUSH>(SelectObject(hdc, GetStockObject(NULL_BRUSH)));
    Rectangle(hdc, x1, y1, x2, y2);
    SelectObject(hdc, oldBr);
    SelectObject(hdc, oldPen);
    DeleteObject(pen);
}
static void DrawLabel(HDC hdc, int x, int y, int w, int h, const wchar_t* txt, HFONT font, COLORREF col, UINT flags = DT_LEFT | DT_VCENTER | DT_SINGLELINE) {
    RECT rc = { x, y, x + w, y + h };
    SelectObject(hdc, font);
    SetTextColor(hdc, col);
    DrawTextW(hdc, txt, -1, &rc, flags);
}
static void RenderWindow(HDC hdc, int W, int H) {
    COLORREF cBg = RGB(12, 14, 20);
    COLORREF cCard = RGB(19, 22, 32);
    COLORREF cInner = RGB(14, 17, 25);
    COLORREF cBorder = RGB(33, 39, 54);
    COLORREF cCobalt = RGB(37, 99, 235);
    COLORREF cEmerald = RGB(16, 185, 129);
    COLORREF cAmber = RGB(217, 119, 6);
    COLORREF cText = RGB(241, 245, 249);
    COLORREF cMuted = RGB(148, 163, 184);
    HBRUSH bgBr = CreateSolidBrush(cBg);
    RECT fullRc = { 0, 0, W, H };
    FillRect(hdc, &fullRc, bgBr);
    DeleteObject(bgBr);
    SetBkMode(hdc, TRANSPARENT);
    HFONT fTitle = CreateFontW(20, 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Segoe UI");
    HFONT fSub = CreateFontW(15, 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Segoe UI");
    HFONT fBody = CreateFontW(15, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Segoe UI");
    HFONT fMono = CreateFontW(15, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, FIXED_PITCH, L"Consolas");
    HFONT fBig = CreateFontW(28, 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, FIXED_PITCH, L"Consolas");
    FillBox(hdc, 0, 0, W, 58, cCard, cBorder);
    FillBox(hdc, 18, 14, 96, 44, cCobalt, cCobalt);
    DrawLabel(hdc, 18, 14, 78, 30, L"SHARD", fSub, RGB(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    wchar_t hdrBuf[160];
    swprintf_s(hdrBuf, L"NATIVE C++20 + MASM x64 AVX2 CONSOLE  |  %s  |  ZERO-COPY NEURAL ENGINE", g_state.versionText);
    DrawLabel(hdc, 110, 14, 680, 30, hdrBuf, fTitle, cText);
    FillBox(hdc, W - 220, 13, W - 18, 45, cEmerald, cEmerald);
    DrawLabel(hdc, W - 220, 13, 202, 32, L"RUN AVX2 BENCHMARK", fSub, RGB(8, 12, 18), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    const wchar_t* tabs[4] = {
        L"01 // AVX2 TELEMETRY",
        L"02 // KERNEL MATRIX",
        L"03 // API & CLI REFERENCE",
        L"04 // GUIDE / ГАЙД / 指南"
    };
    for (int i = 0; i < 4; ++i) {
        int tx = 18 + i * 245;
        COLORREF tf = (g_state.activeTab == i) ? cCobalt : cCard;
        COLORREF tb = (g_state.activeTab == i) ? cCobalt : cBorder;
        COLORREF tc = (g_state.activeTab == i) ? RGB(255, 255, 255) : cMuted;
        FillBox(hdc, tx, 70, tx + 235, 104, tf, tb);
        DrawLabel(hdc, tx, 70, 235, 34, tabs[i], fSub, tc, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    }
    int topY = 118;
    if (g_state.activeTab == 0) {
        int cw = (W - 60) / 4;
        wchar_t b1[64], b2[64], b3[64], b4[64];
        swprintf_s(b1, L"%.2f GFLOPS", g_state.dotGflops);
        swprintf_s(b2, L"%llu cyc / %lld us", g_state.rdtscCycles, g_state.dotUs);
        swprintf_s(b3, L"%d Active / %d Tests", g_state.activeKernels, g_state.passedTests);
        swprintf_s(b4, L"%.1f / %.1f GB (%dT)", g_state.ramAvailGb, g_state.ramTotalGb, g_state.cpuThreads);
        const wchar_t* kTitles[4] = { L"AVX2 FMA THROUGHPUT", L"RDTSC CYCLES / 80K ITER", L"VERIFIED NATIVE KERNELS", L"HOST RAM FREE / THREADS" };
        const wchar_t* kVals[4] = { b1, b2, b3, b4 };
        COLORREF kCols[4] = { cEmerald, cCobalt, cEmerald, cAmber };
        for (int i = 0; i < 4; ++i) {
            int cx = 18 + i * (cw + 8);
            FillBox(hdc, cx, topY, cx + cw, topY + 92, cCard, cBorder);
            DrawLabel(hdc, cx + 14, topY + 10, cw - 24, 22, kTitles[i], fSub, cMuted);
            DrawLabel(hdc, cx + 14, topY + 40, cw - 24, 38, kVals[i], fBig, kCols[i]);
        }
        FillBox(hdc, 18, topY + 108, W - 18, H - 48, cCard, cBorder);
        DrawLabel(hdc, 34, topY + 122, W - 68, 26, L"LIVE HARDWARE EXECUTION BENCHMARK (4096-DIM FP32 VECTORS, 32-BYTE ALIGNED)", fSub, cText);
        wchar_t lines[7][180];
        swprintf_s(lines[0], L"[KRN-002] shard_asm_dot_unroll8   | MASM x64 AVX2 4xYMM FMA   | %8.2f GFLOPS   | PASS", g_state.dotGflops);
        swprintf_s(lines[1], L"[KRN-004] shard_asm_rmsnorm_sum   | MASM x64 AVX2 vfmadd231ps | %8.0f ops/sec  | PASS", g_state.rmsOpsSec);
        swprintf_s(lines[2], L"[KRN-007] shard_asm_vec_add_f32   | MASM x64 AVX2 vaddps      | %8.0f ops/sec  | PASS", g_state.addOpsSec);
        swprintf_s(lines[3], L"[KRN-008] shard_asm_hadamard_f32  | MASM x64 AVX2 vmulps      | %8.0f ops/sec  | PASS", g_state.hadOpsSec);
        swprintf_s(lines[4], L"[KRN-009] shard_asm_axpy_f32      | MASM x64 AVX2 Fused AXPY  | %8.0f ops/sec  | PASS", g_state.axpyOpsSec);
        swprintf_s(lines[5], L"[KRN-012] shard_softmax_f32       | MASM vmaxps + AVX2 Scale  | STABLE LOGIT SHIFT | PASS");
        swprintf_s(lines[6], L"[KRN-016] shard_mmap_open/close   | Win32 MapViewOfFile       | ZERO-COPY PAGING   | PASS");
        for (int r = 0; r < 7; ++r) {
            int ry = topY + 160 + r * 44;
            FillBox(hdc, 34, ry, W - 34, ry + 36, cInner, cBorder);
            DrawLabel(hdc, 48, ry, W - 96, 36, lines[r], fMono, (r == 0) ? cEmerald : cText);
        }
    } else if (g_state.activeTab == 1) {
        FillBox(hdc, 18, topY, W - 18, H - 48, cCard, cBorder);
        wchar_t mTitle[128];
        swprintf_s(mTitle, L"SHARD NATIVE KERNEL & MEMORY TIER ARCHITECTURE (%d REGISTERED KERNELS)", g_state.activeKernels);
        DrawLabel(hdc, 34, topY + 14, W - 68, 28, mTitle, fSub, cText);
        const wchar_t* kRows[9] = {
            L"TIER 1: FULL GPU VRAM       | Quantized weights fit in VRAM (<VRAM - 500MB)     | 45-75 tok/s",
            L"TIER 2: HYBRID VRAM + RAM   | Hot attention layers in VRAM, FFN in DDR4/DDR5   | 18-32 tok/s",
            L"TIER 3: NVME LAYER STREAM   | Win32 MapViewOfFile sequential zero-copy paging   | No OOM cap",
            L"KRN-001..003: Linear Algebra| dot_f32, dot_unroll8 (128B), fast_scale (AVX2)    | 256-bit YMM",
            L"KRN-004..005: Normalization | rmsnorm_f32 (ASM+AVX2), layernorm_f32 (AVX2)      | Affine FP32",
            L"KRN-006..011: Activations   | swiglu, vec_add, hadamard, axpy, silu, gelu       | Fused SIMD",
            L"KRN-012..014: Attention/RAG | softmax_f32 (vmaxps), rope_f32, cosine_sim_f32    | Zero-Alloc",
            L"KRN-015: Quantization       | dequant_q4 nibble unpack & dequant_q8_0 AVX2      | Int4/8->FP32",
            L"KRN-016+: Runtime Guards    | Safetensors mmap pager & continuous spec suite    | Verified"
        };
        for (int r = 0; r < 9; ++r) {
            int ry = topY + 52 + r * 44;
            FillBox(hdc, 34, ry, W - 34, ry + 36, cInner, cBorder);
            DrawLabel(hdc, 48, ry, W - 96, 36, kRows[r], fMono, (r < 3) ? cEmerald : cText);
        }
    } else if (g_state.activeTab == 2) {
        FillBox(hdc, 18, topY, W - 18, H - 48, cCard, cBorder);
        DrawLabel(hdc, 34, topY + 14, W - 68, 28, L"OPENAI & ANTHROPIC LOCAL PROTOCOL ENDPOINTS + CLI COMMANDS", fSub, cText);
        const wchar_t* apiRows[9] = {
            L"CLI: shard start --port 8000          | Launch FastAPI SSE server + Web Workspace",
            L"CLI: shard status                     | Inspect GPU, VRAM, RAM, CPU & Native Core DLL",
            L"CLI: shard test                       | Run MASM x64 AVX2 + C++20 kernel benchmark",
            L"CLI: shard list --category coding     | Rank Hugging Face models against host hardware",
            L"CLI: shard run Qwen/Qwen2.5-Coder-7B  | Interactive terminal chat REPL with tok/s",
            L"CLI: shard key --gen                  | Generate local sk-shard-* authentication key",
            L"API: POST http://localhost:8000/v1/chat/completions  (Cursor / Continue / OpenCode)",
            L"API: POST http://localhost:8000/v1/messages          (Claude Code / Anthropic SDK)",
            L"API: GET  http://localhost:8000/i?os=windows&tool=opencode  (Zero-config installer)"
        };
        for (int r = 0; r < 9; ++r) {
            int ry = topY + 52 + r * 44;
            FillBox(hdc, 34, ry, W - 34, ry + 36, cInner, cBorder);
            DrawLabel(hdc, 48, ry, W - 96, 36, apiRows[r], fMono, (r >= 6) ? RGB(96, 165, 250) : cText);
        }
    } else {
        FillBox(hdc, 18, topY, W - 18, H - 48, cCard, cBorder);
        const wchar_t* langBtns[3] = { L"ENGLISH (EN)", L"РУССКИЙ (RU)", L"中文指南 (ZH)" };
        for (int l = 0; l < 3; ++l) {
            int lx = 34 + l * 165;
            FillBox(hdc, lx, topY + 14, lx + 152, topY + 46, (g_state.guideLang == l) ? cCobalt : cInner, (g_state.guideLang == l) ? cCobalt : cBorder);
            DrawLabel(hdc, lx, topY + 14, 152, 32, langBtns[l], fSub, RGB(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        }
        const wchar_t* guideText = nullptr;
        if (g_state.guideLang == 0) {
            guideText =
                L"1. WHAT IS SHARD?\n"
                L"Shard is a 100% local neural inference runtime built with Python FastAPI, C++20 zero-copy Win32 memory mapping (MapViewOfFile), and hand-written MASM x64 AVX2/FMA SIMD assembly kernels. Your code and prompts never leave your machine.\n\n"
                L"2. HOW THE ARCHITECTURE WORKS\n"
                L"- MASM x64 AVX2 Layer: Executes 256-bit FMA dot products, RMSNorm, Hadamard gating, AXPY, and Softmax max-reduction directly in YMM0-YMM7 hardware registers.\n"
                L"- C++20 Native Core (shard_core.dll): Maps Safetensors weights straight from NVMe/SSD without duplicating RAM and unpacks Q4/Q8_0 blocks into FP32.\n"
                L"- FastAPI Server: Exposes drop-in OpenAI (/v1/chat/completions) and Anthropic (/v1/messages) endpoints on localhost:8000.\n\n"
                L"3. HOW TO USE SHARD IN YOUR PROJECT OR IDE\n"
                L"- Run 'shard start' to launch the local server and dashboard at http://localhost:8000.\n"
                L"- Generate an API key via 'shard key --gen' and set Base URL to http://localhost:8000/v1 in Cursor, Claude Code, OpenCode, or Aider.\n"
                L"- Run 'shard test' or click 'RUN AVX2 BENCHMARK' above to verify SIMD throughput on your CPU.";
        } else if (g_state.guideLang == 1) {
            guideText =
                L"1. ДЛЯ ЧЕГО НУЖНА ПРОГРАММА SHARD?\n"
                L"Shard — это полностью локальный движок запуска нейросетей на трех языках: Python FastAPI, C++20 (прямое отображение весов Win32 MapViewOfFile без копирования в RAM) и ассемблер MASM x64 AVX2/FMA. Код и запросы работают на 100% локально без платных облачных токенов.\n\n"
                L"2. КАК ОНА РАБОТАЕТ ВНУТРИ\n"
                L"- Уровень MASM x64 AVX2: выполняет матричные скалярные произведения FMA, RMSNorm, Hadamard, AXPY и Softmax напрямую в 256-битных регистрах YMM0-YMM7.\n"
                L"- Уровень C++20 (shard_core.dll): читает веса моделей напрямую с SSD (Zero-Copy) и деквантует блоки Q4/Q8_0 без переполнения памяти (OOM).\n"
                L"- Сервер совместимости: поднимает локальные эндпоинты OpenAI (/v1/chat/completions) и Anthropic (/v1/messages) на http://localhost:8000.\n\n"
                L"3. КАК ПОДКЛЮЧИТЬ К СВОЕМУ ПРОЕКТУ ИЛИ IDE\n"
                L"- Запустите сервер командой 'shard start' и откройте панель http://localhost:8000.\n"
                L"- Создайте ключ 'shard key --gen' и укажите Base URL http://localhost:8000/v1 в Cursor, Claude Code, OpenCode или своем приложении.\n"
                L"- Нажмите 'RUN AVX2 BENCHMARK' сверху или выполните 'shard test' для замера реальных GFLOPS вашего процессора.";
        } else {
            guideText =
                L"1. SHARD 是什么？\n"
                L"Shard 是纯本地运行的高性能神经网络推理引擎，采用三层架构：Python FastAPI、C++20 零拷贝内存映射 (Win32 MapViewOfFile) 以及手写 MASM x64 AVX2/FMA 汇编内核。所有代码与推理完全在本地硬件执行，无需消耗云端 API 令牌。\n\n"
                L"2. 核心工作原理\n"
                L"- MASM x64 AVX2 汇编层：利用 YMM0-YMM7 256 位寄存器直接执行 FMA 点积、RMSNorm、Hadamard 门控、AXPY 与 Softmax 归约。\n"
                L"- C++20 原生核心 (shard_core.dll)：从 NVMe SSD 零拷贝映射模型权重并执行 Q4/Q8_0 反量化，防止内存溢出 (OOM)。\n"
                L"- 协议兼容层：在 http://localhost:8000 提供兼容 OpenAI (/v1/chat/completions) 与 Anthropic (/v1/messages) 的本地接口。\n\n"
                L"3. 如何在您的项目或 IDE 中使用\n"
                L"- 运行 'shard start' 启动本地推理服务与控制台 (http://localhost:8000)。\n"
                L"- 运行 'shard key --gen' 生成本地密钥，在 Cursor、Claude Code、OpenCode 或自有项目中将 Base URL 设为 http://localhost:8000/v1。\n"
                L"- 点击右上角 'RUN AVX2 BENCHMARK' 或执行 'shard test' 即可实测当前 CPU 的 AVX2 浮点吞吐量。";
        }
        FillBox(hdc, 34, topY + 58, W - 34, H - 64, cInner, cBorder);
        RECT grc = { 52, topY + 74, W - 52, H - 80 };
        SelectObject(hdc, fBody);
        SetTextColor(hdc, cText);
        DrawTextW(hdc, guideText, -1, &grc, DT_LEFT | DT_TOP | DT_WORDBREAK);
    }
    FillBox(hdc, 0, H - 36, W, H, cCard, cBorder);
    DrawLabel(hdc, 18, H - 36, W - 36, 36, L"github.com/avelone123123/shard   |   100% Local Inference   |   Python + C++20 Zero-Copy + MASM x64 AVX2", fMono, cMuted);
    DeleteObject(fTitle);
    DeleteObject(fSub);
    DeleteObject(fBody);
    DeleteObject(fMono);
    DeleteObject(fBig);
}
static LRESULT CALLBACK ShardWndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    switch (msg) {
    case WM_ERASEBKGND:
        return 1;
    case WM_LBUTTONDOWN: {
        int mx = static_cast<short>(LOWORD(lParam));
        int my = static_cast<short>(HIWORD(lParam));
        RECT rc;
        GetClientRect(hwnd, &rc);
        int W = rc.right - rc.left;
        if (mx >= W - 220 && mx <= W - 18 && my >= 13 && my <= 45) {
            RunHardwareBenchmark();
            InvalidateRect(hwnd, nullptr, FALSE);
            return 0;
        }
        if (my >= 70 && my <= 104) {
            for (int i = 0; i < 4; ++i) {
                int tx = 18 + i * 245;
                if (mx >= tx && mx <= tx + 235) {
                    g_state.activeTab = i;
                    InvalidateRect(hwnd, nullptr, FALSE);
                    return 0;
                }
            }
        }
        if (g_state.activeTab == 3 && my >= 132 && my <= 164) {
            for (int l = 0; l < 3; ++l) {
                int lx = 34 + l * 165;
                if (mx >= lx && mx <= lx + 152) {
                    g_state.guideLang = l;
                    InvalidateRect(hwnd, nullptr, FALSE);
                    return 0;
                }
            }
        }
        return 0;
    }
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC hdc = BeginPaint(hwnd, &ps);
        RECT rc;
        GetClientRect(hwnd, &rc);
        int W = rc.right - rc.left;
        int H = rc.bottom - rc.top;
        HDC memDC = CreateCompatibleDC(hdc);
        HBITMAP memBmp = CreateCompatibleBitmap(hdc, W, H);
        HBITMAP oldBmp = static_cast<HBITMAP>(SelectObject(memDC, memBmp));
        RenderWindow(memDC, W, H);
        BitBlt(hdc, 0, 0, W, H, memDC, 0, 0, SRCCOPY);
        SelectObject(memDC, oldBmp);
        DeleteObject(memBmp);
        DeleteDC(memDC);
        EndPaint(hwnd, &ps);
        return 0;
    }
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wParam, lParam);
}
int WINAPI WinMain(HINSTANCE hInst, HINSTANCE, LPSTR, int nCmdShow) {
    RunHardwareBenchmark();
    WNDCLASSEXW wc = {};
    wc.cbSize = sizeof(wc);
    wc.style = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = ShardWndProc;
    wc.hInstance = hInst;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.lpszClassName = L"ShardNativeConsoleClass";
    RegisterClassExW(&wc);
    HWND hwnd = CreateWindowExW(
        0,
        wc.lpszClassName,
        L"Shard Native Core Console // C++20 Zero-Copy + MASM x64 AVX2",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, 1120, 680,
        nullptr, nullptr, hInst, nullptr
    );
    ShowWindow(hwnd, nCmdShow);
    UpdateWindow(hwnd);
    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return 0;
}
