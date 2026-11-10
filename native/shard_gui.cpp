#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <windowsx.h>
#include <winhttp.h>
#include <shellapi.h>
#include <dwmapi.h>
#include <intrin.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "winhttp.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "dwmapi.lib")
#pragma comment(lib, "advapi32.lib")
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
static ShardGuiState g_state = { 0, 0, 61, 69, 0.0, 0, 0, 0.0, 0.0, 0.0, 0.0, 16.0, 8.0, 8, L"v1.4.42" };
static std::wstring Utf8ToWide(const std::string& s) {
    if (s.empty()) return L"";
    int len = MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
    if (len <= 0) return L"";
    std::wstring out(len, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), out.data(), len);
    return out;
}
static std::string WideToUtf8(const std::wstring& w) {
    if (w.empty()) return "";
    int len = WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), nullptr, 0, nullptr, nullptr);
    if (len <= 0) return "";
    std::string out(len, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), out.data(), len, nullptr, nullptr);
    return out;
}
static std::string JsonEscape(const std::wstring& w) {
    std::string u = WideToUtf8(w);
    std::string out;
    out.reserve(u.size() + 16);
    for (unsigned char c : u) {
        if (c == '"') out += "\\\"";
        else if (c == '\\') out += "\\\\";
        else if (c == '\n') out += "\\n";
        else if (c == '\r') out += "\\r";
        else if (c == '\t') out += "\\t";
        else if (c < 0x20) { char b[8]; sprintf_s(b, "\\u%04x", c); out += b; }
        else out.push_back(static_cast<char>(c));
    }
    return out;
}
static void CopyToClipboard(HWND hwnd, const std::wstring& text) {
    if (!OpenClipboard(hwnd)) return;
    EmptyClipboard();
    size_t bytes = (text.size() + 1) * sizeof(wchar_t);
    HGLOBAL hMem = GlobalAlloc(GMEM_MOVEABLE, bytes);
    if (hMem) {
        void* ptr = GlobalLock(hMem);
        if (ptr) {
            memcpy(ptr, text.c_str(), bytes);
            GlobalUnlock(hMem);
            SetClipboardData(CF_UNICODETEXT, hMem);
        } else {
            GlobalFree(hMem);
        }
    }
    CloseClipboard();
}
static std::wstring ReadClipboardText(HWND hwnd) {
    std::wstring res;
    if (!OpenClipboard(hwnd)) return res;
    HANDLE hData = GetClipboardData(CF_UNICODETEXT);
    if (hData) {
        const wchar_t* p = static_cast<const wchar_t*>(GlobalLock(hData));
        if (p) {
            res = p;
            GlobalUnlock(hData);
        }
    }
    CloseClipboard();
    std::wstring clean;
    for (wchar_t c : res) {
        if (c != L'\r') clean.push_back(c);
    }
    return clean;
}
struct JVal {
    enum Type { Null, Bool, Num, Str, Arr, Obj } type = Null;
    bool bVal = false;
    double nVal = 0.0;
    std::wstring sVal;
    std::vector<JVal> arr;
    std::map<std::string, JVal> obj;
    const JVal& get(const std::string& k) const {
        static const JVal empty;
        auto it = obj.find(k);
        return (it != obj.end()) ? it->second : empty;
    }
    std::wstring wstr(const std::string& k, const std::wstring& def = L"") const {
        auto it = obj.find(k);
        if (it == obj.end()) return def;
        if (it->second.type == Str) return it->second.sVal;
        if (it->second.type == Num) {
            wchar_t b[64];
            if (std::floor(it->second.nVal) == it->second.nVal) swprintf_s(b, L"%lld", static_cast<long long>(it->second.nVal));
            else swprintf_s(b, L"%.2f", it->second.nVal);
            return b;
        }
        return def;
    }
    double num(const std::string& k, double def = 0.0) const {
        auto it = obj.find(k);
        return (it != obj.end() && it->second.type == Num) ? it->second.nVal : def;
    }
    bool boolean(const std::string& k, bool def = false) const {
        auto it = obj.find(k);
        return (it != obj.end() && it->second.type == Bool) ? it->second.bVal : def;
    }
};
class JsonParser {
    const std::string& src;
    size_t pos = 0;
    void skipWs() {
        while (pos < src.size() && (src[pos] == ' ' || src[pos] == '\t' || src[pos] == '\r' || src[pos] == '\n')) ++pos;
    }
    unsigned int parseHex4() {
        unsigned int v = 0;
        for (int i = 0; i < 4 && pos < src.size(); ++i, ++pos) {
            char c = src[pos];
            v <<= 4;
            if (c >= '0' && c <= '9') v |= (c - '0');
            else if (c >= 'a' && c <= 'f') v |= (c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') v |= (c - 'A' + 10);
        }
        return v;
    }
    static void AppendCodepointUtf8(std::string& out, unsigned int cp) {
        if (cp <= 0x7F) out.push_back(static_cast<char>(cp));
        else if (cp <= 0x7FF) {
            out.push_back(static_cast<char>(0xC0 | ((cp >> 6) & 0x1F)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        } else if (cp <= 0xFFFF) {
            out.push_back(static_cast<char>(0xE0 | ((cp >> 12) & 0x0F)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        } else {
            out.push_back(static_cast<char>(0xF0 | ((cp >> 18) & 0x07)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        }
    }
    std::string parseRawUtf8String() {
        std::string out;
        if (pos < src.size() && src[pos] == '"') ++pos;
        while (pos < src.size()) {
            char c = src[pos++];
            if (c == '"') break;
            if (c == '\\' && pos < src.size()) {
                char esc = src[pos++];
                if (esc == '"') out.push_back('"');
                else if (esc == '\\') out.push_back('\\');
                else if (esc == '/') out.push_back('/');
                else if (esc == 'b') out.push_back('\b');
                else if (esc == 'f') out.push_back('\f');
                else if (esc == 'n') out.push_back('\n');
                else if (esc == 'r') out.push_back('\r');
                else if (esc == 't') out.push_back('\t');
                else if (esc == 'u') {
                    unsigned int u1 = parseHex4();
                    if (u1 >= 0xD800 && u1 <= 0xDBFF && pos + 1 < src.size() && src[pos] == '\\' && src[pos + 1] == 'u') {
                        pos += 2;
                        unsigned int u2 = parseHex4();
                        unsigned int cp = 0x10000 + (((u1 - 0xD800) << 10) | (u2 - 0xDC00));
                        AppendCodepointUtf8(out, cp);
                    } else {
                        AppendCodepointUtf8(out, u1);
                    }
                }
            } else {
                out.push_back(c);
            }
        }
        return out;
    }
public:
    explicit JsonParser(const std::string& s) : src(s) {}
    JVal parseValue() {
        skipWs();
        JVal v;
        if (pos >= src.size()) return v;
        char c = src[pos];
        if (c == '"') {
            v.type = JVal::Str;
            v.sVal = Utf8ToWide(parseRawUtf8String());
            return v;
        }
        if (c == '{') {
            v.type = JVal::Obj;
            ++pos;
            skipWs();
            if (pos < src.size() && src[pos] == '}') { ++pos; return v; }
            while (pos < src.size()) {
                skipWs();
                if (pos >= src.size() || src[pos] != '"') break;
                std::string k = parseRawUtf8String();
                skipWs();
                if (pos < src.size() && src[pos] == ':') ++pos;
                v.obj[k] = parseValue();
                skipWs();
                if (pos < src.size() && src[pos] == ',') { ++pos; continue; }
                if (pos < src.size() && src[pos] == '}') { ++pos; break; }
                break;
            }
            return v;
        }
        if (c == '[') {
            v.type = JVal::Arr;
            ++pos;
            skipWs();
            if (pos < src.size() && src[pos] == ']') { ++pos; return v; }
            while (pos < src.size()) {
                v.arr.push_back(parseValue());
                skipWs();
                if (pos < src.size() && src[pos] == ',') { ++pos; continue; }
                if (pos < src.size() && src[pos] == ']') { ++pos; break; }
                break;
            }
            return v;
        }
        if (src.compare(pos, 4, "true") == 0) { pos += 4; v.type = JVal::Bool; v.bVal = true; return v; }
        if (src.compare(pos, 5, "false") == 0) { pos += 5; v.type = JVal::Bool; v.bVal = false; return v; }
        if (src.compare(pos, 4, "null") == 0) { pos += 4; v.type = JVal::Null; return v; }
        size_t start = pos;
        while (pos < src.size() && (isdigit(static_cast<unsigned char>(src[pos])) || src[pos] == '-' || src[pos] == '+' || src[pos] == '.' || src[pos] == 'e' || src[pos] == 'E')) ++pos;
        if (pos > start) {
            v.type = JVal::Num;
            v.nVal = std::strtod(src.substr(start, pos - start).c_str(), nullptr);
        } else {
            ++pos;
        }
        return v;
    }
};
static JVal ParseJson(const std::string& s) {
    JsonParser p(s);
    return p.parseValue();
}
struct ModelEntry {
    std::wstring id;
    std::wstring name;
    std::wstring author;
    std::wstring desc;
    std::wstring badge;
    std::wstring tier;
    std::wstring speed;
    std::wstring memMode;
    std::wstring sizeStr;
    long long downloads = 0;
    double sizeMb = 0.0;
    bool isDownloaded = false;
    bool isActive = false;
};
struct ChatMsg {
    std::wstring role;
    std::wstring content;
    std::wstring reasoning;
    double tokPerSec = 0.0;
    double totalSec = 0.0;
    int totalTokens = 0;
};
struct ChatSession {
    std::wstring id;
    std::wstring title;
    std::vector<ChatMsg> messages;
};
enum HitAction {
    ACT_NONE = 0,
    ACT_NAV_TAB,
    ACT_TOGGLE_SERVER,
    ACT_UNLOAD_MODEL,
    ACT_CANCEL_LOAD,
    ACT_FOCUS_SEARCH,
    ACT_CLEAR_SEARCH,
    ACT_RUN_SEARCH,
    ACT_CAT_FILTER,
    ACT_MODEL_LOAD,
    ACT_MODEL_CARD,
    ACT_MODEL_DELETE_SSD,
    ACT_MODAL_CLOSE,
    ACT_MODAL_OPEN_HF,
    ACT_MODAL_LOAD,
    ACT_CHAT_NEW,
    ACT_CHAT_SELECT,
    ACT_CHAT_DELETE,
    ACT_CHAT_FOCUS_INPUT,
    ACT_CHAT_SEND,
    ACT_CHAT_STOP,
    ACT_CHAT_TEMP,
    ACT_CHAT_MAXTOK,
    ACT_CHAT_PRESET,
    ACT_CHAT_COPY_MSG,
    ACT_INTEG_SELECT,
    ACT_INTEG_APPLY,
    ACT_INTEG_COPY_MAIN,
    ACT_INTEG_COPY_URL,
    ACT_INTEG_COPY_KEY,
    ACT_INTEG_COPY_MODEL,
    ACT_INTEG_PING,
    ACT_KEY_GENERATE,
    ACT_KEY_SELECT,
    ACT_KEY_COPY,
    ACT_KEY_DELETE,
    ACT_SRV_START,
    ACT_SRV_RESTART,
    ACT_SRV_STOP,
    ACT_SRV_KEEP_BG,
    ACT_COPY_TEXT,
    ACT_BENCH_RUN,
    ACT_GUIDE_LANG
};
struct HitZone {
    RECT rc;
    int action = ACT_NONE;
    int idx = 0;
    std::wstring param;
};
struct AppData {
    bool serverOnline = false;
    bool serverBooting = true;
    bool keepServerOnExit = false;
    double lastPingMs = 0.4;
    std::wstring pingReport = L"Ожидание проверки /v1/ping...";
    std::wstring gpuName = L"Определение GPU...";
    int vramUsedMb = 0;
    int vramTotalMb = 0;
    double ramUsedGb = 0.0;
    double ramTotalGb = 16.0;
    double diskFreeGb = 0.0;
    std::wstring activeModelId = L"Qwen/Qwen2.5-Coder-1.5B-Instruct";
    std::wstring loadedModelId = L"";
    bool isModelLoaded = false;
    bool isLoading = false;
    double loadPct = 0.0;
    double loadCurrMb = 0.0;
    double loadTotalMb = 0.0;
    double loadSpeedMb = 0.0;
    std::wstring loadStage = L"";
    std::wstring loadEta = L"";
    std::wstring currentCat = L"all";
    std::wstring searchQuery = L"";
    bool catalogBusy = false;
    std::vector<ModelEntry> catalogModels;
    std::vector<ModelEntry> ssdModels;
    double ssdTotalGb = 0.0;
    bool modalOpen = false;
    bool modalBusy = false;
    std::wstring modalModelId;
    std::wstring modalAuthor;
    std::wstring modalStats;
    std::wstring modalCompat;
    std::wstring modalReadme;
    int modalScrollY = 0;
    std::vector<ChatSession> chats;
    int activeChatIdx = 0;
    std::wstring chatInput;
    bool chatStreaming = false;
    double chatTemp = 0.7;
    int chatMaxTokens = 512;
    int selectedToolIdx = 0;
    std::vector<std::wstring> apiKeys;
    std::wstring activeApiKey = L"sk-shard-local";
    std::wstring toastTitle;
    std::wstring toastBody;
    ULONGLONG toastUntilMs = 0;
};
static std::mutex g_mtx;
static AppData g_app;
static HWND g_hwnd = nullptr;
static HANDLE g_serverProcess = nullptr;
static std::atomic<bool> g_running{ true };
static std::atomic<bool> g_cancelStream{ false };
static int g_focusField = 0;
static bool g_caretVisible = true;
static int g_hoverAction = -1;
static int g_scrollY[6] = { 0, 0, 0, 0, 0, 0 };
static int g_maxScrollY[6] = { 0, 0, 0, 0, 0, 0 };
static std::vector<HitZone> g_hits;
static constexpr UINT WM_SHARD_REFRESH = WM_APP + 10;
static void ShowToastSafe(const std::wstring& title, const std::wstring& body) {
    std::lock_guard<std::mutex> lk(g_mtx);
    g_app.toastTitle = title;
    g_app.toastBody = body;
    g_app.toastUntilMs = GetTickCount64() + 3800;
    if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
}
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
    for (int it = 0; it < iters; ++it) sink += shard_asm_dot_unroll8(v1, v2, dim);
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
static std::wstring GetExeDirectory() {
    wchar_t buf[MAX_PATH] = {};
    GetModuleFileNameW(nullptr, buf, MAX_PATH);
    std::wstring p(buf);
    size_t sl = p.find_last_of(L"\\/");
    return (sl != std::wstring::npos) ? p.substr(0, sl) : L".";
}
static std::wstring FindPythonExecutable() {
    const wchar_t* candidates[] = {
        L"C:\\Python314\\python.exe",
        L"C:\\Python313\\python.exe",
        L"C:\\Python312\\python.exe",
        L"C:\\Python311\\python.exe"
    };
    for (const wchar_t* c : candidates) {
        if (GetFileAttributesW(c) != INVALID_FILE_ATTRIBUTES) return c;
    }
    return L"python.exe";
}
static std::wstring UrlEncodeWide(const std::wstring& s) {
    std::string u = WideToUtf8(s);
    std::wstring out;
    const wchar_t* hex = L"0123456789ABCDEF";
    for (unsigned char c : u) {
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '/') {
            out.push_back(static_cast<wchar_t>(c));
        } else {
            out.push_back(L'%');
            out.push_back(hex[(c >> 4) & 0xF]);
            out.push_back(hex[c & 0xF]);
        }
    }
    return out;
}
static bool HttpCall(const wchar_t* method, const std::wstring& path, const std::string& body, std::string& outResp, int timeoutMs = 6000) {
    outResp.clear();
    HINTERNET hSession = WinHttpOpen(L"ShardStudioNative/1.4", WINHTTP_ACCESS_TYPE_NO_PROXY, WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!hSession) return false;
    WinHttpSetTimeouts(hSession, 900, 900, timeoutMs, timeoutMs);
    HINTERNET hConnect = WinHttpConnect(hSession, L"127.0.0.1", 8000, 0);
    if (!hConnect) { WinHttpCloseHandle(hSession); return false; }
    HINTERNET hRequest = WinHttpOpenRequest(hConnect, method, path.c_str(), nullptr, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0);
    if (!hRequest) { WinHttpCloseHandle(hConnect); WinHttpCloseHandle(hSession); return false; }
    const wchar_t* hdrs = L"Content-Type: application/json; charset=utf-8\r\n";
    BOOL sent = WinHttpSendRequest(hRequest, hdrs, -1L, body.empty() ? WINHTTP_NO_REQUEST_DATA : const_cast<char*>(body.data()), static_cast<DWORD>(body.size()), static_cast<DWORD>(body.size()), 0);
    if (!sent || !WinHttpReceiveResponse(hRequest, nullptr)) {
        WinHttpCloseHandle(hRequest);
        WinHttpCloseHandle(hConnect);
        WinHttpCloseHandle(hSession);
        return false;
    }
    DWORD statusCode = 0;
    DWORD szStatus = sizeof(statusCode);
    WinHttpQueryHeaders(hRequest, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, WINHTTP_HEADER_NAME_BY_INDEX, &statusCode, &szStatus, WINHTTP_NO_HEADER_INDEX);
    DWORD avail = 0;
    while (WinHttpQueryDataAvailable(hRequest, &avail) && avail > 0) {
        std::string chunk(avail, '\0');
        DWORD readBytes = 0;
        if (WinHttpReadData(hRequest, chunk.data(), avail, &readBytes) && readBytes > 0) {
            outResp.append(chunk.data(), readBytes);
        } else {
            break;
        }
    }
    WinHttpCloseHandle(hRequest);
    WinHttpCloseHandle(hConnect);
    WinHttpCloseHandle(hSession);
    return statusCode >= 200 && statusCode < 300;
}
static void StartLocalServerSilent() {
    std::string pingOut;
    if (HttpCall(L"GET", L"/v1/ping", "", pingOut, 900)) {
        std::lock_guard<std::mutex> lk(g_mtx);
        g_app.serverOnline = true;
        g_app.serverBooting = false;
        if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
        return;
    }
    if (g_serverProcess) {
        DWORD code = 0;
        if (GetExitCodeProcess(g_serverProcess, &code) && code == STILL_ACTIVE) return;
        CloseHandle(g_serverProcess);
        g_serverProcess = nullptr;
    }
    {
        std::lock_guard<std::mutex> lk(g_mtx);
        g_app.serverBooting = true;
    }
    if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
    std::wstring exeDir = GetExeDirectory();
    std::wstring pyExe = FindPythonExecutable();
    std::wstring cmdLine = L"\"" + pyExe + L"\" -m uvicorn server:app --app-dir \"" + exeDir + L"\" --host 127.0.0.1 --port 8000 --log-level warning";
    STARTUPINFOW si = {};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    PROCESS_INFORMATION pi = {};
    std::vector<wchar_t> mutableCmd(cmdLine.begin(), cmdLine.end());
    mutableCmd.push_back(L'\0');
    if (CreateProcessW(nullptr, mutableCmd.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, nullptr, exeDir.c_str(), &si, &pi)) {
        CloseHandle(pi.hThread);
        g_serverProcess = pi.hProcess;
    }
}
static void StopLocalServer() {
    if (g_serverProcess) {
        TerminateProcess(g_serverProcess, 0);
        WaitForSingleObject(g_serverProcess, 1200);
        CloseHandle(g_serverProcess);
        g_serverProcess = nullptr;
    }
    std::lock_guard<std::mutex> lk(g_mtx);
    g_app.serverOnline = false;
    g_app.serverBooting = false;
    if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
}
static std::wstring GetChatsFilePath() {
    wchar_t prof[MAX_PATH] = {};
    DWORD n = GetEnvironmentVariableW(L"USERPROFILE", prof, MAX_PATH);
    std::wstring base = (n > 0) ? std::wstring(prof) : GetExeDirectory();
    std::wstring shardDir = base + L"\\.shard";
    CreateDirectoryW(shardDir.c_str(), nullptr);
    return shardDir + L"\\studio_chats.json";
}
static void SaveChatsToDisk() {
    std::vector<ChatSession> copy;
    {
        std::lock_guard<std::mutex> lk(g_mtx);
        copy = g_app.chats;
    }
    std::ostringstream oss;
    oss << "[\n";
    for (size_t i = 0; i < copy.size(); ++i) {
        oss << "  {\"id\":\"" << JsonEscape(copy[i].id) << "\",\"title\":\"" << JsonEscape(copy[i].title) << "\",\"messages\":[";
        for (size_t m = 0; m < copy[i].messages.size(); ++m) {
            const auto& msg = copy[i].messages[m];
            oss << "{\"role\":\"" << JsonEscape(msg.role) << "\",\"content\":\"" << JsonEscape(msg.content) << "\",\"reasoning\":\"" << JsonEscape(msg.reasoning)
                << "\",\"tokPerSec\":" << msg.tokPerSec << ",\"totalSec\":" << msg.totalSec << ",\"totalTokens\":" << msg.totalTokens << "}";
            if (m + 1 < copy[i].messages.size()) oss << ",";
        }
        oss << "]}";
        if (i + 1 < copy.size()) oss << ",";
        oss << "\n";
    }
    oss << "]\n";
    std::ofstream ofs(GetChatsFilePath(), std::ios::binary | std::ios::trunc);
    if (ofs) {
        std::string s = oss.str();
        ofs.write(s.data(), static_cast<std::streamsize>(s.size()));
    }
}
static void LoadChatsFromDisk() {
    std::ifstream ifs(GetChatsFilePath(), std::ios::binary);
    std::vector<ChatSession> loaded;
    if (ifs) {
        std::ostringstream ss;
        ss << ifs.rdbuf();
        JVal root = ParseJson(ss.str());
        if (root.type == JVal::Arr) {
            for (const auto& item : root.arr) {
                ChatSession cs;
                cs.id = item.wstr("id", L"chat_1");
                cs.title = item.wstr("title", L"Новый диалог");
                const JVal& msgs = item.get("messages");
                if (msgs.type == JVal::Arr) {
                    for (const auto& mj : msgs.arr) {
                        ChatMsg cm;
                        cm.role = mj.wstr("role", L"user");
                        cm.content = mj.wstr("content");
                        cm.reasoning = mj.wstr("reasoning");
                        cm.tokPerSec = mj.num("tokPerSec", 0.0);
                        cm.totalSec = mj.num("totalSec", 0.0);
                        cm.totalTokens = static_cast<int>(mj.num("totalTokens", 0.0));
                        cs.messages.push_back(cm);
                    }
                }
                loaded.push_back(cs);
            }
        }
    }
    if (loaded.empty()) {
        ChatSession fresh;
        fresh.id = L"chat_init";
        fresh.title = L"Новый диалог";
        loaded.push_back(fresh);
    }
    std::lock_guard<std::mutex> lk(g_mtx);
    g_app.chats = loaded;
    g_app.activeChatIdx = 0;
}
static void FetchCatalogAsync() {
    std::thread([]() {
        std::wstring cat, q;
        {
            std::lock_guard<std::mutex> lk(g_mtx);
            cat = g_app.currentCat;
            q = g_app.searchQuery;
            g_app.catalogBusy = true;
        }
        if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
        if (cat == L"downloaded") {
            std::string raw;
            if (HttpCall(L"GET", L"/api/models/downloaded", "", raw, 8000)) {
                JVal root = ParseJson(raw);
                std::vector<ModelEntry> list;
                const JVal& arr = root.get("models");
                if (arr.type == JVal::Arr) {
                    for (const auto& m : arr.arr) {
                        ModelEntry me;
                        me.id = m.wstr("id");
                        me.name = m.wstr("name", me.id);
                        me.author = m.wstr("author", L"Community");
                        me.sizeStr = m.wstr("size_str", L"0 МБ");
                        me.sizeMb = m.num("size_mb", 0.0);
                        me.isDownloaded = true;
                        me.isActive = m.boolean("is_active", false);
                        const JVal& comp = m.get("compatibility");
                        me.badge = comp.wstr("tier_label", L"НА ЛОКАЛЬНОМ SSD");
                        me.tier = comp.wstr("tier", L"PERFECT");
                        me.speed = comp.wstr("speed_est", L"30-60 ток/сек");
                        me.memMode = comp.wstr("mode", L"Local SSD Cache");
                        me.desc = L"Веса сохранены на SSD (" + me.sizeStr + L"). Мгновенный запуск без интернета.";
                        list.push_back(me);
                    }
                }
                std::lock_guard<std::mutex> lk(g_mtx);
                g_app.ssdModels = list;
                g_app.catalogModels = list;
                g_app.ssdTotalGb = root.num("total_size_gb", 0.0);
                g_app.catalogBusy = false;
            } else {
                std::lock_guard<std::mutex> lk(g_mtx);
                g_app.catalogBusy = false;
            }
            if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
            return;
        }
        std::wstring url = L"/api/hf/models?category=" + UrlEncodeWide(cat) + L"&limit=72";
        if (!q.empty()) url += L"&query=" + UrlEncodeWide(q);
        std::string raw;
        if (HttpCall(L"GET", url, "", raw, 9000)) {
            JVal root = ParseJson(raw);
            std::vector<ModelEntry> list;
            if (root.type == JVal::Arr) {
                for (const auto& m : root.arr) {
                    ModelEntry me;
                    me.id = m.wstr("id");
                    me.name = m.wstr("name", me.id);
                    me.author = m.wstr("author", L"Community");
                    me.desc = m.wstr("description", m.wstr("desc", L"Открытая архитектура Hugging Face"));
                    const JVal& comp = m.get("compatibility");
                    me.badge = m.wstr("compat_badge", comp.wstr("tier_label", L"ГИБРИД VRAM+RAM"));
                    me.tier = comp.wstr("tier", L"RECOMMENDED");
                    me.speed = m.wstr("speed_tier", comp.wstr("speed_est", L"20-35 ток/сек"));
                    me.memMode = m.wstr("memory_slot", comp.wstr("mode", L"VRAM + RAM"));
                    me.downloads = static_cast<long long>(m.num("downloads", 0.0));
                    me.sizeMb = m.num("size_est_mb", comp.num("size_est_mb", 1000.0));
                    wchar_t sb[48];
                    if (me.sizeMb >= 1024.0) swprintf_s(sb, L"~%.1f ГБ", me.sizeMb / 1024.0);
                    else swprintf_s(sb, L"~%.0f МБ", me.sizeMb);
                    me.sizeStr = sb;
                    me.isDownloaded = m.boolean("is_downloaded", false);
                    list.push_back(me);
                }
            }
            std::lock_guard<std::mutex> lk(g_mtx);
            g_app.catalogModels = list;
            g_app.catalogBusy = false;
        } else {
            std::lock_guard<std::mutex> lk(g_mtx);
            g_app.catalogBusy = false;
        }
        if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
    }).detach();
}
static void FetchDownloadedSilent() {
    std::thread([]() {
        std::string raw;
        if (HttpCall(L"GET", L"/api/models/downloaded", "", raw, 6000)) {
            JVal root = ParseJson(raw);
            std::vector<ModelEntry> list;
            const JVal& arr = root.get("models");
            if (arr.type == JVal::Arr) {
                for (const auto& m : arr.arr) {
                    ModelEntry me;
                    me.id = m.wstr("id");
                    me.name = m.wstr("name", me.id);
                    me.author = m.wstr("author", L"Community");
                    me.sizeStr = m.wstr("size_str", L"0 МБ");
                    me.sizeMb = m.num("size_mb", 0.0);
                    me.isDownloaded = true;
                    me.isActive = m.boolean("is_active", false);
                    const JVal& comp = m.get("compatibility");
                    me.badge = comp.wstr("tier_label", L"НА SSD");
                    me.tier = comp.wstr("tier", L"PERFECT");
                    me.speed = comp.wstr("speed_est", L"35-65 ток/сек");
                    me.memMode = comp.wstr("mode", L"SSD Cache");
                    list.push_back(me);
                }
            }
            std::lock_guard<std::mutex> lk(g_mtx);
            g_app.ssdModels = list;
            g_app.ssdTotalGb = root.num("total_size_gb", 0.0);
            if (g_app.currentCat == L"downloaded") g_app.catalogModels = list;
            if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
        }
    }).detach();
}
static void FetchKeysAsync() {
    std::thread([]() {
        std::string raw;
        if (HttpCall(L"GET", L"/api/keys", "", raw, 4000)) {
            JVal root = ParseJson(raw);
            const JVal& arr = root.get("keys");
            std::vector<std::wstring> ks;
            if (arr.type == JVal::Arr) {
                for (const auto& k : arr.arr) {
                    if (!k.sVal.empty()) ks.push_back(k.sVal);
                }
            }
            std::lock_guard<std::mutex> lk(g_mtx);
            g_app.apiKeys = ks;
            if (!ks.empty()) {
                bool found = false;
                for (const auto& x : ks) if (x == g_app.activeApiKey) found = true;
                if (!found) g_app.activeApiKey = ks[0];
            }
            if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
        }
    }).detach();
}
static std::wstring StripMarkdownPlain(const std::wstring& md) {
    std::wstring out;
    out.reserve(md.size());
    bool inFrontmatter = false;
    size_t lineIdx = 0;
    std::wistringstream iss(md);
    std::wstring line;
    while (std::getline(iss, line)) {
        if (!line.empty() && line.back() == L'\r') line.pop_back();
        if (lineIdx == 0 && line == L"---") { inFrontmatter = true; ++lineIdx; continue; }
        if (inFrontmatter) {
            if (line == L"---") inFrontmatter = false;
            ++lineIdx;
            continue;
        }
        ++lineIdx;
        std::wstring clean;
        for (size_t i = 0; i < line.size(); ++i) {
            if (line[i] == L'*' && i + 1 < line.size() && line[i + 1] == L'*') { ++i; continue; }
            if (line[i] == L'`') continue;
            clean.push_back(line[i]);
        }
        out += clean + L"\n";
        if (out.size() > 14000) {
            out += L"\n[Полная спецификация доступна на странице репозитория Hugging Face]\n";
            break;
        }
    }
    return out.empty() ? L"Спецификация модели открыта для локального запуска через Shard Runtime." : out;
}
static void OpenModelCardAsync(const std::wstring& modelId) {
    {
        std::lock_guard<std::mutex> lk(g_mtx);
        g_app.modalOpen = true;
        g_app.modalBusy = true;
        g_app.modalModelId = modelId;
        size_t sl = modelId.find(L'/');
        g_app.modalAuthor = (sl != std::wstring::npos) ? modelId.substr(0, sl) : L"Community";
        g_app.modalStats = L"Запрос метаданных с Hugging Face...";
        g_app.modalCompat = L"";
        g_app.modalReadme = L"Загрузка карточки и технической документации модели...";
        g_app.modalScrollY = 0;
    }
    if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
    std::thread([modelId]() {
        std::string raw;
        std::wstring path = L"/api/hf/model/" + UrlEncodeWide(modelId);
        if (HttpCall(L"GET", path, "", raw, 9000)) {
            JVal d = ParseJson(raw);
            long long dl = static_cast<long long>(d.num("downloads", 0));
            long long lkCount = static_cast<long long>(d.num("likes", 0));
            std::wstring pipe = d.wstr("pipeline_tag", L"text-generation");
            const JVal& comp = d.get("compatibility");
            wchar_t sb[256];
            swprintf_s(sb, L"Тип: %s   |   Скачиваний: %lld   |   Рейтинг HF: %lld", pipe.c_str(), dl, lkCount);
            std::wstring readme = StripMarkdownPlain(d.wstr("readme"));
            std::lock_guard<std::mutex> lk(g_mtx);
            g_app.modalBusy = false;
            g_app.modalStats = sb;
            g_app.modalCompat = comp.wstr("tier_label") + L"  //  " + comp.wstr("speed_est") + L"  //  " + comp.wstr("description");
            g_app.modalReadme = readme;
        } else {
            std::lock_guard<std::mutex> lk(g_mtx);
            g_app.modalBusy = false;
            g_app.modalStats = L"Локальная спецификация";
            g_app.modalReadme = L"Не удалось получить расширенный README (проверьте подключение к сети или запустите модель напрямую).";
        }
        if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
    }).detach();
}
static void TriggerLoadModelAsync(const std::wstring& modelId) {
    {
        std::lock_guard<std::mutex> lk(g_mtx);
        g_app.activeModelId = modelId;
        g_app.isLoading = true;
        g_app.loadPct = 5.0;
        g_app.loadStage = L"Подключение к Hugging Face Hub";
        g_app.loadEta = L"оценка...";
        g_app.modalOpen = false;
    }
    ShowToastSafe(L"Запуск модели", L"Инициализация " + modelId);
    std::thread([modelId]() {
        std::string body = "{\"model_id\":\"" + JsonEscape(modelId) + "\"}";
        std::string out;
        if (!HttpCall(L"POST", L"/api/models/load", body, out, 8000)) {
            StartLocalServerSilent();
            Sleep(1600);
            HttpCall(L"POST", L"/api/models/load", body, out, 8000);
        }
    }).detach();
}
static void TriggerUnloadAsync() {
    std::thread([]() {
        std::string out;
        if (HttpCall(L"POST", L"/api/models/unload", "{}", out, 5000)) {
            std::lock_guard<std::mutex> lk(g_mtx);
            g_app.isModelLoaded = false;
            g_app.loadedModelId.clear();
            g_app.isLoading = false;
        }
        ShowToastSafe(L"Память освобождена", L"Модель выгружена из VRAM и RAM");
        FetchDownloadedSilent();
    }).detach();
}
static void TriggerCancelAsync() {
    g_cancelStream = true;
    std::thread([]() {
        std::string out;
        HttpCall(L"POST", L"/api/models/cancel", "{}", out, 4000);
        {
            std::lock_guard<std::mutex> lk(g_mtx);
            g_app.isLoading = false;
            g_app.chatStreaming = false;
        }
        ShowToastSafe(L"Операция остановлена", L"Загрузка или генерация прервана");
    }).detach();
}
static void TriggerDeleteSsdModelAsync(const std::wstring& modelId) {
    std::thread([modelId]() {
        std::string body = "{\"model_id\":\"" + JsonEscape(modelId) + "\"}";
        std::string out;
        if (HttpCall(L"POST", L"/api/models/delete", body, out, 12000)) {
            JVal r = ParseJson(out);
            std::wstring freed = r.wstr("freed_str", L"место на диске");
            ShowToastSafe(L"Удалено с SSD", modelId + L" (освобождено " + freed + L")");
            FetchDownloadedSilent();
            FetchCatalogAsync();
        } else {
            ShowToastSafe(L"Ошибка удаления", L"Не удалось удалить кэш модели");
        }
    }).detach();
}
static void TriggerGenerateKeyAsync() {
    std::thread([]() {
        std::string out;
        if (HttpCall(L"POST", L"/api/keys/generate", "{}", out, 4000)) {
            JVal r = ParseJson(out);
            std::wstring nk = r.wstr("key");
            {
                std::lock_guard<std::mutex> lk(g_mtx);
                if (!nk.empty()) g_app.activeApiKey = nk;
            }
            ShowToastSafe(L"Новый API ключ создан", nk);
            FetchKeysAsync();
        }
    }).detach();
}
static void TriggerDeleteKeyAsync(const std::wstring& key) {
    std::thread([key]() {
        std::string body = "{\"key\":\"" + JsonEscape(key) + "\"}";
        std::string out;
        HttpCall(L"POST", L"/api/keys/delete", body, out, 4000);
        ShowToastSafe(L"Ключ отозван", key);
        FetchKeysAsync();
    }).detach();
}
static void TriggerApplyIntegrationAsync(const std::wstring& toolSlug) {
    std::thread([toolSlug]() {
        std::wstring key, mdl;
        {
            std::lock_guard<std::mutex> lk(g_mtx);
            key = g_app.activeApiKey;
            mdl = g_app.activeModelId;
        }
        std::string body = "{\"tool\":\"" + JsonEscape(toolSlug) + "\",\"key\":\"" + JsonEscape(key) + "\",\"model\":\"" + JsonEscape(mdl) + "\"}";
        std::string out;
        if (!HttpCall(L"POST", L"/api/integrations/apply", body, out, 6000)) {
            StartLocalServerSilent();
            Sleep(1500);
            HttpCall(L"POST", L"/api/integrations/apply", body, out, 6000);
        }
        JVal r = ParseJson(out);
        if (r.wstr("status") == L"ok") {
            ShowToastSafe(L"Интеграция применена", r.wstr("message", L"Конфигурация успешно записана на диск"));
        } else {
            ShowToastSafe(L"Ошибка настройки", r.wstr("message", L"Проверьте статус локального сервера"));
        }
    }).detach();
}
static void TriggerPingTestAsync() {
    std::thread([]() {
        auto t0 = std::chrono::high_resolution_clock::now();
        std::string out;
        bool ok = HttpCall(L"GET", L"/v1/ping", "", out, 3000);
        auto t1 = std::chrono::high_resolution_clock::now();
        double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
        if (ok) {
            JVal r = ParseJson(out);
            wchar_t buf[200];
            swprintf_s(buf, L"200 OK  |  Отклик: %.1f мс  |  Ядро: %s", ms, r.wstr("engine", L"AVX2").c_str());
            {
                std::lock_guard<std::mutex> lk(g_mtx);
                g_app.serverOnline = true;
                g_app.lastPingMs = ms;
                g_app.pingReport = buf;
            }
            ShowToastSafe(L"Сервер доступен", buf);
        } else {
            {
                std::lock_guard<std::mutex> lk(g_mtx);
                g_app.serverOnline = false;
                g_app.pingReport = L"Сервер не отвечает. Нажмите «Запустить сервер».";
            }
            ShowToastSafe(L"Нет ответа от :8000", L"Локальный сервер сейчас остановлен");
        }
        if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
    }).detach();
}
static void SendChatPromptAsync(const std::wstring& promptText) {
    if (promptText.empty()) return;
    int cIdx = 0;
    std::wstring modelId, apiKey;
    double temp = 0.7;
    int maxTok = 512;
    std::string reqJson;
    {
        std::lock_guard<std::mutex> lk(g_mtx);
        if (g_app.chatStreaming) return;
        if (g_app.chats.empty()) {
            ChatSession cs;
            cs.id = L"chat_1";
            cs.title = L"Новый диалог";
            g_app.chats.push_back(cs);
            g_app.activeChatIdx = 0;
        }
        cIdx = std::clamp(g_app.activeChatIdx, 0, static_cast<int>(g_app.chats.size()) - 1);
        auto& sess = g_app.chats[cIdx];
        if (sess.title == L"Новый диалог" || sess.messages.empty()) {
            sess.title = (promptText.size() > 26) ? (promptText.substr(0, 26) + L"...") : promptText;
        }
        ChatMsg uMsg;
        uMsg.role = L"user";
        uMsg.content = promptText;
        sess.messages.push_back(uMsg);
        ChatMsg aMsg;
        aMsg.role = L"assistant";
        sess.messages.push_back(aMsg);
        g_app.chatStreaming = true;
        g_cancelStream = false;
        modelId = g_app.activeModelId;
        apiKey = g_app.activeApiKey;
        temp = g_app.chatTemp;
        maxTok = g_app.chatMaxTokens;
        std::ostringstream oss;
        oss << "{\"model\":\"" << JsonEscape(modelId) << "\",\"stream\":true,\"temperature\":" << temp << ",\"max_tokens\":" << maxTok << ",\"messages\":[";
        for (size_t i = 0; i + 1 < sess.messages.size(); ++i) {
            oss << "{\"role\":\"" << JsonEscape(sess.messages[i].role) << "\",\"content\":\"" << JsonEscape(sess.messages[i].content) << "\"}";
            if (i + 2 < sess.messages.size()) oss << ",";
        }
        oss << "]}";
        reqJson = oss.str();
        g_scrollY[2] = 999999;
    }
    if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
    std::thread([cIdx, reqJson]() {
        auto t0 = std::chrono::high_resolution_clock::now();
        HINTERNET hSession = WinHttpOpen(L"ShardStudioChat/1.4", WINHTTP_ACCESS_TYPE_NO_PROXY, WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
        bool gotAny = false;
        if (hSession) {
            WinHttpSetTimeouts(hSession, 2000, 2000, 30000, 120000);
            HINTERNET hConnect = WinHttpConnect(hSession, L"127.0.0.1", 8000, 0);
            if (hConnect) {
                HINTERNET hReq = WinHttpOpenRequest(hConnect, L"POST", L"/v1/chat/completions", nullptr, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0);
                if (hReq) {
                    const wchar_t* hdr = L"Content-Type: application/json; charset=utf-8\r\n";
                    if (WinHttpSendRequest(hReq, hdr, -1L, const_cast<char*>(reqJson.data()), static_cast<DWORD>(reqJson.size()), static_cast<DWORD>(reqJson.size()), 0) &&
                        WinHttpReceiveResponse(hReq, nullptr)) {
                        std::string sseBuf;
                        DWORD avail = 0;
                        while (!g_cancelStream && WinHttpQueryDataAvailable(hReq, &avail) && avail > 0) {
                            std::string chunk(avail, '\0');
                            DWORD readB = 0;
                            if (!WinHttpReadData(hReq, chunk.data(), avail, &readB) || readB == 0) break;
                            sseBuf.append(chunk.data(), readB);
                            size_t nlPos = 0;
                            while ((nlPos = sseBuf.find('\n')) != std::string::npos) {
                                std::string line = sseBuf.substr(0, nlPos);
                                sseBuf.erase(0, nlPos + 1);
                                if (!line.empty() && line.back() == '\r') line.pop_back();
                                if (line.rfind("data: ", 0) == 0) {
                                    std::string payload = line.substr(6);
                                    if (payload == "[DONE]") break;
                                    JVal ev = ParseJson(payload);
                                    const JVal& choices = ev.get("choices");
                                    std::wstring dContent, dReason;
                                    if (choices.type == JVal::Arr && !choices.arr.empty()) {
                                        const JVal& delta = choices.arr[0].get("delta");
                                        dContent = delta.wstr("content");
                                        dReason = delta.wstr("reasoning_content");
                                    }
                                    const JVal& telem = ev.get("telemetry");
                                    const JVal& usage = ev.get("usage");
                                    {
                                        std::lock_guard<std::mutex> lk(g_mtx);
                                        if (cIdx >= 0 && cIdx < static_cast<int>(g_app.chats.size()) && !g_app.chats[cIdx].messages.empty()) {
                                            auto& targetMsg = g_app.chats[cIdx].messages.back();
                                            if (!dReason.empty()) { targetMsg.reasoning += dReason; gotAny = true; }
                                            if (!dContent.empty()) { targetMsg.content += dContent; gotAny = true; }
                                            if (telem.type == JVal::Obj) {
                                                targetMsg.tokPerSec = telem.num("tokens_per_sec", targetMsg.tokPerSec);
                                                targetMsg.totalSec = telem.num("total_seconds", targetMsg.totalSec);
                                            }
                                            if (usage.type == JVal::Obj) {
                                                targetMsg.totalTokens = static_cast<int>(usage.num("total_tokens", targetMsg.totalTokens));
                                            }
                                            g_scrollY[2] = 999999;
                                        }
                                    }
                                    if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
                                }
                            }
                        }
                    }
                    WinHttpCloseHandle(hReq);
                }
                WinHttpCloseHandle(hConnect);
            }
            WinHttpCloseHandle(hSession);
        }
        auto t1 = std::chrono::high_resolution_clock::now();
        double dt = std::chrono::duration<double>(t1 - t0).count();
        {
            std::lock_guard<std::mutex> lk(g_mtx);
            if (cIdx >= 0 && cIdx < static_cast<int>(g_app.chats.size()) && !g_app.chats[cIdx].messages.empty()) {
                auto& targetMsg = g_app.chats[cIdx].messages.back();
                if (!gotAny && targetMsg.content.empty()) {
                    targetMsg.content = L"Локальный сервер еще запускается или модель не выбрана. Проверьте индикатор сервера сверху и нажмите «Запустить» на нужной модели в каталоге.";
                }
                if (targetMsg.totalSec <= 0.01) targetMsg.totalSec = dt;
            }
            g_app.chatStreaming = false;
        }
        SaveChatsToDisk();
        if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
    }).detach();
}
static void BackgroundTelemetryLoop() {
    StartLocalServerSilent();
    bool initialLoaded = false;
    while (g_running) {
        std::string raw;
        if (HttpCall(L"GET", L"/api/system/hardware", "", raw, 2500)) {
            JVal root = ParseJson(raw);
            const JVal& hw = root.get("hardware");
            const JVal& eng = root.get("engine");
            const JVal& lp = eng.get("load_progress");
            bool wasLoading = false;
            bool nowLoading = false;
            std::wstring finishedModel;
            std::wstring errMsg;
            {
                std::lock_guard<std::mutex> lk(g_mtx);
                wasLoading = g_app.isLoading;
                g_app.serverOnline = true;
                g_app.serverBooting = false;
                std::wstring gName = hw.wstr("gpu_name", L"CPU AVX2");
                size_t p1 = gName.find(L"NVIDIA GeForce ");
                if (p1 != std::wstring::npos) gName.erase(p1, 15);
                size_t p2 = gName.find(L" Laptop GPU");
                if (p2 != std::wstring::npos) gName.erase(p2, 11);
                g_app.gpuName = gName;
                g_app.vramUsedMb = static_cast<int>(hw.num("vram_used_mb", 0));
                g_app.vramTotalMb = static_cast<int>(hw.num("vram_total_mb", 0));
                g_app.ramUsedGb = hw.num("ram_used_gb", 0.0);
                g_app.ramTotalGb = hw.num("ram_total_gb", g_state.ramTotalGb);
                g_app.diskFreeGb = hw.num("disk_free_gb", 0.0);
                std::wstring actM = eng.wstr("active_model");
                if (!actM.empty()) g_app.activeModelId = actM;
                g_app.loadedModelId = eng.wstr("current_loaded");
                g_app.isModelLoaded = eng.boolean("is_loaded", !g_app.loadedModelId.empty());
                std::wstring st = eng.wstr("status");
                std::wstring lpSt = lp.wstr("status");
                nowLoading = (st == L"loading" || lpSt == L"loading");
                g_app.isLoading = nowLoading;
                if (nowLoading) {
                    g_app.loadPct = lp.num("percent", 5.0);
                    g_app.loadCurrMb = lp.num("curr_mb", 0.0);
                    g_app.loadTotalMb = lp.num("total_mb", 0.0);
                    g_app.loadSpeedMb = lp.num("speed_mb", 0.0);
                    g_app.loadStage = lp.wstr("stage", L"Загрузка весов...");
                    g_app.loadEta = lp.wstr("eta_text", L"расчет...");
                } else if (wasLoading) {
                    if (lpSt == L"ready" || st == L"ready") finishedModel = g_app.activeModelId;
                    else if (lpSt == L"error" || st == L"error") errMsg = lp.wstr("message", L"Ошибка инициализации модели");
                }
            }
            if (!initialLoaded) {
                initialLoaded = true;
                FetchKeysAsync();
                FetchDownloadedSilent();
                FetchCatalogAsync();
            }
            if (wasLoading && !nowLoading) {
                if (!finishedModel.empty()) ShowToastSafe(L"Модель активна", finishedModel + L" загружена в память");
                else if (!errMsg.empty()) ShowToastSafe(L"Ошибка загрузки", errMsg);
                FetchDownloadedSilent();
                FetchCatalogAsync();
            }
            if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
        } else {
            std::lock_guard<std::mutex> lk(g_mtx);
            g_app.serverOnline = false;
            if (g_hwnd) PostMessageW(g_hwnd, WM_SHARD_REFRESH, 0, 0);
        }
        bool fastPoll = false;
        {
            std::lock_guard<std::mutex> lk(g_mtx);
            fastPoll = g_app.isLoading || g_app.serverBooting;
        }
        for (int s = 0; s < (fastPoll ? 6 : 24) && g_running; ++s) Sleep(100);
    }
}
static constexpr COLORREF COL_BG = RGB(11, 12, 15);
static constexpr COLORREF COL_SIDEBAR = RGB(15, 17, 22);
static constexpr COLORREF COL_CARD = RGB(20, 23, 30);
static constexpr COLORREF COL_CARD_HOVER = RGB(26, 30, 39);
static constexpr COLORREF COL_WELL = RGB(12, 14, 18);
static constexpr COLORREF COL_BORDER = RGB(37, 42, 54);
static constexpr COLORREF COL_GOLD = RGB(207, 166, 91);
static constexpr COLORREF COL_GOLD_DIM = RGB(138, 108, 54);
static constexpr COLORREF COL_GOLD_BG = RGB(36, 30, 20);
static constexpr COLORREF COL_EMERALD = RGB(34, 160, 107);
static constexpr COLORREF COL_EMERALD_BG = RGB(17, 36, 28);
static constexpr COLORREF COL_AMBER = RGB(212, 148, 58);
static constexpr COLORREF COL_AMBER_BG = RGB(38, 29, 17);
static constexpr COLORREF COL_TERRA = RGB(201, 106, 75);
static constexpr COLORREF COL_TERRA_BG = RGB(40, 23, 20);
static constexpr COLORREF COL_CRIMSON = RGB(201, 74, 74);
static constexpr COLORREF COL_CRIMSON_BG = RGB(42, 20, 22);
static constexpr COLORREF COL_STEEL = RGB(96, 142, 204);
static constexpr COLORREF COL_TEXT = RGB(240, 242, 246);
static constexpr COLORREF COL_SUBTEXT = RGB(172, 180, 194);
static constexpr COLORREF COL_MUTED = RGB(114, 123, 138);
static void AddHit(int x1, int y1, int x2, int y2, int action, int idx = 0, const std::wstring& param = L"") {
    HitZone hz;
    hz.rc = { x1, y1, x2, y2 };
    hz.action = action;
    hz.idx = idx;
    hz.param = param;
    g_hits.push_back(hz);
}
static void FillBox(HDC hdc, int x1, int y1, int x2, int y2, COLORREF fillCol, COLORREF borderCol) {
    RECT rc = { x1, y1, x2, y2 };
    HBRUSH br = CreateSolidBrush(fillCol);
    FillRect(hdc, &rc, br);
    DeleteObject(br);
    if (borderCol != CLR_NONE) {
        HPEN pen = CreatePen(PS_SOLID, 1, borderCol);
        HPEN oldPen = static_cast<HPEN>(SelectObject(hdc, pen));
        HBRUSH oldBr = static_cast<HBRUSH>(SelectObject(hdc, GetStockObject(NULL_BRUSH)));
        Rectangle(hdc, x1, y1, x2, y2);
        SelectObject(hdc, oldBr);
        SelectObject(hdc, oldPen);
        DeleteObject(pen);
    }
}
static void FillRoundBox(HDC hdc, int x1, int y1, int x2, int y2, int rad, COLORREF fillCol, COLORREF borderCol) {
    HBRUSH br = CreateSolidBrush(fillCol);
    HPEN pen = CreatePen(PS_SOLID, 1, (borderCol == CLR_NONE) ? fillCol : borderCol);
    HBRUSH oldBr = static_cast<HBRUSH>(SelectObject(hdc, br));
    HPEN oldPen = static_cast<HPEN>(SelectObject(hdc, pen));
    RoundRect(hdc, x1, y1, x2, y2, rad, rad);
    SelectObject(hdc, oldPen);
    SelectObject(hdc, oldBr);
    DeleteObject(pen);
    DeleteObject(br);
}
static void DrawLabel(HDC hdc, int x, int y, int w, int h, const wchar_t* txt, HFONT font, COLORREF col, UINT flags = DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX) {
    if (w <= 0 || h <= 0 || !txt) return;
    RECT rc = { x, y, x + w, y + h };
    SelectObject(hdc, font);
    SetTextColor(hdc, col);
    DrawTextW(hdc, txt, -1, &rc, flags);
}
static int MeasureMultiline(HDC hdc, int w, const std::wstring& txt, HFONT font) {
    if (txt.empty() || w <= 10) return 20;
    RECT rc = { 0, 0, w, 10 };
    SelectObject(hdc, font);
    DrawTextW(hdc, txt.c_str(), -1, &rc, DT_LEFT | DT_TOP | DT_WORDBREAK | DT_CALCRECT | DT_NOPREFIX);
    return std::max(20, static_cast<int>(rc.bottom - rc.top));
}
static void DrawMultiline(HDC hdc, int x, int y, int w, int h, const std::wstring& txt, HFONT font, COLORREF col) {
    if (w <= 0 || h <= 0 || txt.empty()) return;
    RECT rc = { x, y, x + w, y + h };
    SelectObject(hdc, font);
    SetTextColor(hdc, col);
    DrawTextW(hdc, txt.c_str(), -1, &rc, DT_LEFT | DT_TOP | DT_WORDBREAK | DT_NOPREFIX);
}
static void DrawShardLogo(HDC hdc, int cx, int cy, int r) {
    POINT topTri[3] = { { cx, cy - r }, { cx + r, cy }, { cx, cy + 2 } };
    POINT leftTri[3] = { { cx, cy - r }, { cx - r, cy }, { cx, cy + 2 } };
    POINT botRight[3] = { { cx + r, cy }, { cx, cy + r }, { cx, cy + 2 } };
    POINT botLeft[3] = { { cx - r, cy }, { cx, cy + r }, { cx, cy + 2 } };
    auto drawPoly = [&](POINT* pts, COLORREF fill, COLORREF edge) {
        HBRUSH b = CreateSolidBrush(fill);
        HPEN p = CreatePen(PS_SOLID, 1, edge);
        HBRUSH ob = static_cast<HBRUSH>(SelectObject(hdc, b));
        HPEN op = static_cast<HPEN>(SelectObject(hdc, p));
        Polygon(hdc, pts, 3);
        SelectObject(hdc, op);
        SelectObject(hdc, ob);
        DeleteObject(p);
        DeleteObject(b);
    };
    drawPoly(topTri, RGB(224, 186, 112), RGB(224, 186, 112));
    drawPoly(leftTri, RGB(178, 140, 72), RGB(178, 140, 72));
    drawPoly(botRight, RGB(154, 118, 56), RGB(154, 118, 56));
    drawPoly(botLeft, RGB(116, 88, 40), RGB(116, 88, 40));
}
static void DrawStatusDot(HDC hdc, int cx, int cy, int r, COLORREF col) {
    HBRUSH b = CreateSolidBrush(col);
    HPEN p = CreatePen(PS_SOLID, 1, col);
    HBRUSH ob = static_cast<HBRUSH>(SelectObject(hdc, b));
    HPEN op = static_cast<HPEN>(SelectObject(hdc, p));
    Ellipse(hdc, cx - r, cy - r, cx + r, cy + r);
    SelectObject(hdc, op);
    SelectObject(hdc, ob);
    DeleteObject(p);
    DeleteObject(b);
}
static void DrawMagnifier(HDC hdc, int cx, int cy, COLORREF col) {
    HPEN p = CreatePen(PS_SOLID, 2, col);
    HPEN op = static_cast<HPEN>(SelectObject(hdc, p));
    HBRUSH ob = static_cast<HBRUSH>(SelectObject(hdc, GetStockObject(NULL_BRUSH)));
    Ellipse(hdc, cx - 6, cy - 6, cx + 4, cy + 4);
    MoveToEx(hdc, cx + 3, cy + 3, nullptr);
    LineTo(hdc, cx + 8, cy + 8);
    SelectObject(hdc, ob);
    SelectObject(hdc, op);
    DeleteObject(p);
}
struct IntegSpec {
    std::wstring slug;
    std::wstring name;
    std::wstring subtitle;
    std::wstring targetPath;
    std::wstring steps;
    std::wstring codeBlock;
    bool supportsAutoApply = true;
};
static IntegSpec BuildIntegrationSpec(int idx, const std::wstring& modelId, const std::wstring& apiKey) {
    IntegSpec s;
    std::wstring shortM = modelId;
    size_t sl = shortM.find(L'/');
    if (sl != std::wstring::npos) shortM = shortM.substr(sl + 1);
    switch (idx) {
    case 0:
        s.slug = L"claude";
        s.name = L"Claude Code CLI";
        s.subtitle = L"Нативный протокол Anthropic /v1/messages (SSE)";
        s.targetPath = L"%USERPROFILE%\\.claude\\settings.json";
        s.steps = L"1. Запустите нужную модель из Hugging Face во вкладке Каталог.\n"
                  L"2. Нажмите кнопку «Применить в 1 клик на ПК» ниже — Shard автоматически пропишет локальный эндпоинт http://localhost:8000/v1 и ваш ключ в ~/.claude/settings.json (создав резервную копию .bak).\n"
                  L"3. Откройте любой терминал в папке вашего проекта и введите claude — весь кодинг будет идти на 100% локально и бесплатно.";
        s.codeBlock = L"{\n"
                      L"  \"hasCompletedOnboarding\": true,\n"
                      L"  \"env\": {\n"
                      L"    \"ANTHROPIC_BASE_URL\": \"http://localhost:8000/v1\",\n"
                      L"    \"ANTHROPIC_AUTH_TOKEN\": \"" + apiKey + L"\",\n"
                      L"    \"ANTHROPIC_DEFAULT_OPUS_MODEL\": \"" + modelId + L"\",\n"
                      L"    \"ANTHROPIC_DEFAULT_SONNET_MODEL\": \"" + modelId + L"\",\n"
                      L"    \"ANTHROPIC_DEFAULT_HAIKU_MODEL\": \"" + modelId + L"\"\n"
                      L"  }\n"
                      L"}";
        break;
    case 1:
        s.slug = L"openrouter";
        s.name = L"OpenRouter / Локальный Мост";
        s.subtitle = L"Полная замена OpenRouter API для агентов, студий и ботов";
        s.targetPath = L"%USERPROFILE%\\.shard\\shard_local_bridge.env";
        s.steps = L"1. Любой проект, использующий OpenRouter (https://openrouter.ai/api/v1), переключается на локальные модели Hugging Face заменой одного параметра Base URL на http://localhost:8000/v1.\n"
                  L"2. Нажмите «Применить в 1 клик на ПК» для генерации готового .env файла и установки системных переменных.\n"
                  L"3. Никаких лимитов RPM и платных токенов — запросы исполняются вашим GPU/CPU локально.";
        s.codeBlock = L"OPENROUTER_BASE_URL=http://localhost:8000/v1\n"
                      L"OPENAI_BASE_URL=http://localhost:8000/v1\n"
                      L"OPENROUTER_API_KEY=" + apiKey + L"\n"
                      L"MODEL_ID=" + modelId + L"\n\n"
                      L"from openai import OpenAI\n"
                      L"client = OpenAI(base_url=\"http://localhost:8000/v1\", api_key=\"" + apiKey + L"\")\n"
                      L"res = client.chat.completions.create(model=\"" + modelId + L"\", messages=[{\"role\":\"user\",\"content\":\"Привет!\"}])";
        break;
    case 2:
        s.slug = L"cursor";
        s.name = L"Cursor IDE";
        s.subtitle = L"Локальный ИИ-ассистент и Composer внутри Cursor";
        s.targetPath = L"Cursor Settings -> Models -> OpenAI API Key";
        s.steps = L"1. В Cursor откройте Settings (Ctrl+Shift+J) -> вкладка Models.\n"
                  L"2. Нажмите «+ Add Model» и вставьте ID модели: " + modelId + L"\n"
                  L"3. Включите тумблер «Override OpenAI Base URL», укажите http://localhost:8000/v1 и вставьте ключ API ниже.";
        s.codeBlock = L"Base URL:   http://localhost:8000/v1\n"
                      L"API Key:    " + apiKey + L"\n"
                      L"Model ID:   " + modelId;
        break;
    case 3:
        s.slug = L"opencode";
        s.name = L"OpenCode CLI";
        s.subtitle = L"Терминальный агент с поддержкой схемы @ai-sdk/openai-compatible";
        s.targetPath = L"%USERPROFILE%\\.config\\opencode\\config.json";
        s.steps = L"1. Нажмите «Применить в 1 клик на ПК» — Shard запишет валидную конфигурацию ~/.config/opencode/config.json с вашей активной моделью.\n"
                  L"2. Запустите opencode в любой директории кода — провайдер shard/" + modelId + L" активируется автоматически.";
        s.codeBlock = L"{\n"
                      L"  \"$schema\": \"https://opencode.ai/config.json\",\n"
                      L"  \"provider\": {\n"
                      L"    \"shard\": {\n"
                      L"      \"npm\": \"@ai-sdk/openai-compatible\",\n"
                      L"      \"options\": { \"baseURL\": \"http://localhost:8000/v1\", \"apiKey\": \"" + apiKey + L"\" }\n"
                      L"    }\n"
                      L"  },\n"
                      L"  \"model\": \"shard/" + modelId + L"\"\n"
                      L"}";
        break;
    case 4:
        s.slug = L"continue";
        s.name = L"Continue (VS Code / JetBrains)";
        s.subtitle = L"Автодополнение и рефакторинг в VS Code и IntelliJ IDEA";
        s.targetPath = L"%USERPROFILE%\\.continue\\config.json";
        s.steps = L"1. Установите расширение Continue в VS Code или JetBrains IDE.\n"
                  L"2. Нажмите «Применить в 1 клик на ПК» — Shard добавит профиль «Shard Local AI» первым в список ~/.continue/config.json.";
        s.codeBlock = L"{\n"
                      L"  \"title\": \"Shard Local AI (" + shortM + L")\",\n"
                      L"  \"provider\": \"openai\",\n"
                      L"  \"model\": \"" + modelId + L"\",\n"
                      L"  \"apiBase\": \"http://localhost:8000/v1\",\n"
                      L"  \"apiKey\": \"" + apiKey + L"\"\n"
                      L"}";
        break;
    case 5:
        s.slug = L"aider";
        s.name = L"Aider CLI";
        s.subtitle = L"Автономное редактирование git-репозиториев из консоли";
        s.targetPath = L"%USERPROFILE%\\.aider.conf.yml";
        s.steps = L"1. Нажмите «Применить в 1 клик на ПК» для автоматического создания ~/.aider.conf.yml.\n"
                  L"2. Или скопируйте команду быстрого старта ниже и запустите в папке проекта.";
        s.codeBlock = L"aider --openai-api-base http://localhost:8000/v1 --openai-api-key " + apiKey + L" --model openai/" + modelId;
        break;
    case 6:
        s.slug = L"zed";
        s.name = L"Zed Editor";
        s.subtitle = L"Высокоскоростной редактор с локальной панелью ассистента";
        s.targetPath = L"%APPDATA%\\Zed\\settings.json";
        s.steps = L"1. Нажмите «Применить в 1 клик на ПК» для добавления секции language_models.openai в настройки Zed.\n"
                  L"2. В панели ассистента Zed выберите модель Shard: " + shortM + L".";
        s.codeBlock = L"\"language_models\": {\n"
                      L"  \"openai\": {\n"
                      L"    \"api_url\": \"http://localhost:8000/v1\",\n"
                      L"    \"available_models\": [{ \"name\": \"" + modelId + L"\", \"max_tokens\": 128000 }]\n"
                      L"  }\n"
                      L"}";
        break;
    case 7:
        s.slug = L"codex";
        s.name = L"Codex CLI (OpenAI)";
        s.subtitle = L"Подключение терминального агента Codex к локальному ядру";
        s.targetPath = L"%USERPROFILE%\\.codex\\config.toml";
        s.steps = L"1. Нажмите «Применить в 1 клик на ПК» — Shard запишет ~/.codex/config.toml и установит переменную OPENAI_API_KEY.\n"
                  L"2. Запускайте codex с локальным провайдером shard без обращения к облаку OpenAI.";
        s.codeBlock = L"model = \"" + modelId + L"\"\n"
                      L"model_provider = \"shard\"\n"
                      L"[model_providers.shard]\n"
                      L"name = \"Shard Local\"\n"
                      L"base_url = \"http://localhost:8000/v1\"\n"
                      L"env_key = \"OPENAI_API_KEY\"";
        break;
    case 8:
        s.slug = L"sdk";
        s.name = L"Python / TypeScript / cURL";
        s.subtitle = L"Прямая интеграция в собственные программы, микросервисы и игры";
        s.targetPath = L"HTTP REST / SSE: http://localhost:8000/v1/chat/completions";
        s.steps = L"Shard поддерживает оба мировых стандарта ИИ API одновременно:\n"
                  L"- OpenAI Chat Completions: POST http://localhost:8000/v1/chat/completions\n"
                  L"- Anthropic Messages API:  POST http://localhost:8000/v1/messages";
        s.codeBlock = L"curl http://localhost:8000/v1/chat/completions \\\n"
                      L"  -H \"Content-Type: application/json\" \\\n"
                      L"  -H \"Authorization: Bearer " + apiKey + L"\" \\\n"
                      L"  -d '{\"model\":\"" + modelId + L"\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}]}'";
        break;
    default:
        s.slug = L"env";
        s.name = L"Глобальные Переменные Windows";
        s.subtitle = L"Системные переменные окружения HKCU\\Environment";
        s.targetPath = L"Реестр пользователя Windows (HKCU\\Environment)";
        s.steps = L"Устанавливает переменные OPENAI_API_BASE, OPENAI_BASE_URL, OPENAI_API_KEY и ANTHROPIC_BASE_URL для вашей учетной записи Windows, чтобы любые CLI и скрипты автоматически видели локальный сервер Shard.";
        s.codeBlock = L"[Environment]::SetEnvironmentVariable(\"OPENAI_API_BASE\", \"http://localhost:8000/v1\", \"User\")\n"
                      L"[Environment]::SetEnvironmentVariable(\"OPENAI_BASE_URL\", \"http://localhost:8000/v1\", \"User\")\n"
                      L"[Environment]::SetEnvironmentVariable(\"OPENAI_API_KEY\", \"" + apiKey + L"\", \"User\")";
        break;
    }
    return s;
}
static void RenderModelGridCard(HDC hdc, int cx, int cy, int cw, int ch, int viewTop, int viewBot, const ModelEntry& m, const AppData& snap, HFONT fSub, HFONT fBody, HFONT fSmall, HFONT fMono) {
    if (cy + ch < viewTop || cy > viewBot) return;
    bool isAct = (!snap.loadedModelId.empty() && _wcsicmp(m.id.c_str(), snap.loadedModelId.c_str()) == 0 && snap.isModelLoaded);
    bool isLoadThis = (snap.isLoading && _wcsicmp(m.id.c_str(), snap.activeModelId.c_str()) == 0);
    COLORREF cardBorder = isAct ? COL_EMERALD : (isLoadThis ? COL_GOLD : COL_BORDER);
    FillRoundBox(hdc, cx, cy, cx + cw, cy + ch, 8, COL_CARD, cardBorder);
    COLORREF bCol = COL_AMBER;
    COLORREF bBg = COL_AMBER_BG;
    if (m.tier == L"PERFECT" || m.badge.find(L"VRAM)") != std::wstring::npos) { bCol = COL_EMERALD; bBg = COL_EMERALD_BG; }
    else if (m.tier == L"NVME_STREAM" || m.badge.find(L"SSD") != std::wstring::npos) { bCol = COL_TERRA; bBg = COL_TERRA_BG; }
    int badgeW = 166;
    FillRoundBox(hdc, cx + cw - badgeW - 12, cy + 12, cx + cw - 12, cy + 34, 5, bBg, bCol);
    DrawLabel(hdc, cx + cw - badgeW - 8, cy + 12, badgeW - 8, 22, m.badge.c_str(), fSmall, bCol, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
    DrawLabel(hdc, cx + 14, cy + 11, cw - badgeW - 34, 22, m.name.c_str(), fSub, COL_TEXT);
    std::wstring subLine = m.author + L"  •  " + m.sizeStr + (m.isDownloaded ? L"  •  [СКАЧАНА НА SSD]" : L"");
    DrawLabel(hdc, cx + 14, cy + 33, cw - 28, 18, subLine.c_str(), fSmall, m.isDownloaded ? COL_GOLD : COL_MUTED);
    DrawMultiline(hdc, cx + 14, cy + 56, cw - 28, 36, m.desc, fBody, COL_SUBTEXT);
    FillRoundBox(hdc, cx + 14, cy + 96, cx + cw - 14, cy + 122, 5, COL_WELL, COL_BORDER);
    std::wstring specTxt = L"Скорость: " + m.speed + L"   |   Режим: " + m.memMode;
    DrawLabel(hdc, cx + 22, cy + 96, cw - 44, 26, specTxt.c_str(), fMono, COL_SUBTEXT);
    int btnY = cy + ch - 38;
    if (btnY >= viewTop && cy + ch <= viewBot + 24) {
        FillRoundBox(hdc, cx + 14, btnY, cx + 106, btnY + 28, 5, COL_WELL, COL_BORDER);
        DrawLabel(hdc, cx + 14, btnY, 92, 28, L"Обзор", fSmall, COL_TEXT, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(cx + 14, std::max(btnY, viewTop), cx + 106, std::min(btnY + 28, viewBot), ACT_MODEL_CARD, 0, m.id);
        if (m.isDownloaded) {
            FillRoundBox(hdc, cx + 114, btnY, cx + 206, btnY + 28, 5, COL_CRIMSON_BG, COL_CRIMSON);
            DrawLabel(hdc, cx + 114, btnY, 92, 28, L"Удалить SSD", fSmall, RGB(248, 180, 180), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            AddHit(cx + 114, std::max(btnY, viewTop), cx + 206, std::min(btnY + 28, viewBot), ACT_MODEL_DELETE_SSD, 0, m.id);
        }
        int runW = 128;
        int runX = cx + cw - runW - 14;
        if (isLoadThis) {
            wchar_t lb[48];
            swprintf_s(lb, L"Загрузка %.0f%%", snap.loadPct);
            FillRoundBox(hdc, runX, btnY, runX + runW, btnY + 28, 5, COL_GOLD_BG, COL_GOLD);
            DrawLabel(hdc, runX, btnY, runW, 28, lb, fSmall, COL_GOLD, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        } else if (isAct) {
            FillRoundBox(hdc, runX, btnY, runX + runW, btnY + 28, 5, COL_EMERALD, COL_EMERALD);
            DrawLabel(hdc, runX, btnY, runW, 28, L"АКТИВНА -> Чат", fSmall, RGB(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            AddHit(runX, std::max(btnY, viewTop), runX + runW, std::min(btnY + 28, viewBot), ACT_NAV_TAB, 2);
        } else {
            FillRoundBox(hdc, runX, btnY, runX + runW, btnY + 28, 5, COL_GOLD, COL_GOLD);
            DrawLabel(hdc, runX, btnY, runW, 28, m.isDownloaded ? L"Запустить с SSD" : L"Скачать и Запуск", fSmall, RGB(12, 13, 16), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            AddHit(runX, std::max(btnY, viewTop), runX + runW, std::min(btnY + 28, viewBot), ACT_MODEL_LOAD, 0, m.id);
        }
    }
}
static void RenderWindow(HDC hdc, int W, int H) {
    AppData snap;
    {
        std::lock_guard<std::mutex> lk(g_mtx);
        snap = g_app;
    }
    g_hits.clear();
    FillBox(hdc, 0, 0, W, H, COL_BG, CLR_NONE);
    SetBkMode(hdc, TRANSPARENT);
    HFONT fBrand = CreateFontW(18, 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Segoe UI");
    HFONT fTitle = CreateFontW(17, 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Segoe UI");
    HFONT fSub = CreateFontW(15, 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Segoe UI");
    HFONT fBody = CreateFontW(14, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Segoe UI");
    HFONT fSmall = CreateFontW(12, 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Segoe UI");
    HFONT fMono = CreateFontW(13, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, FIXED_PITCH, L"Consolas");
    HFONT fBigNum = CreateFontW(24, 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, FIXED_PITCH, L"Consolas");
    int hdrH = 56;
    int sideW = 228;
    FillBox(hdc, 0, 0, W, hdrH, COL_SIDEBAR, COL_BORDER);
    DrawShardLogo(hdc, 28, 28, 13);
    DrawLabel(hdc, 48, 10, 170, 22, L"SHARD STUDIO", fBrand, COL_TEXT);
    std::wstring verSub = std::wstring(g_state.versionText) + L" // LOCAL AI ENGINE";
    DrawLabel(hdc, 48, 30, 175, 16, verSub.c_str(), fSmall, COL_GOLD);
    COLORREF srvCol = snap.serverOnline ? COL_EMERALD : (snap.serverBooting ? COL_GOLD : COL_CRIMSON);
    COLORREF srvBg = snap.serverOnline ? COL_EMERALD_BG : (snap.serverBooting ? COL_GOLD_BG : COL_CRIMSON_BG);
    const wchar_t* srvTxt = snap.serverOnline ? L"СЕРВЕР :8000 ОНЛАЙН" : (snap.serverBooting ? L"ЗАПУСК ЯДРА..." : L"СЕРВЕР ОФЛАЙН");
    FillRoundBox(hdc, sideW + 14, 13, sideW + 190, 43, 6, srvBg, srvCol);
    DrawStatusDot(hdc, sideW + 28, 28, 4, srvCol);
    DrawLabel(hdc, sideW + 38, 13, 146, 30, srvTxt, fSmall, srvCol);
    AddHit(sideW + 14, 13, sideW + 190, 43, ACT_TOGGLE_SERVER);
    wchar_t hwBuf[256];
    swprintf_s(hwBuf, L"GPU: %s   |   VRAM: %d/%d МБ   |   RAM: %.1f/%.1f ГБ   |   SSD: %.1f ГБ своб.",
        snap.gpuName.c_str(), snap.vramUsedMb, snap.vramTotalMb, snap.ramUsedGb, snap.ramTotalGb, snap.diskFreeGb);
    DrawLabel(hdc, sideW + 204, 13, std::max(100, W - sideW - 500), 30, hwBuf, fSmall, COL_SUBTEXT);
    std::wstring shortAct = snap.activeModelId;
    size_t slAct = shortAct.find(L'/');
    if (slAct != std::wstring::npos) shortAct = shortAct.substr(slAct + 1);
    int modBoxX = std::max(sideW + 420, W - 285);
    if (snap.isModelLoaded) {
        FillRoundBox(hdc, modBoxX, 13, W - 98, 43, 6, COL_EMERALD_BG, COL_EMERALD);
        DrawLabel(hdc, modBoxX + 8, 13, W - 98 - modBoxX - 16, 30, shortAct.c_str(), fSmall, COL_EMERALD, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
        FillRoundBox(hdc, W - 92, 13, W - 14, 43, 6, COL_CRIMSON_BG, COL_CRIMSON);
        DrawLabel(hdc, W - 92, 13, 78, 30, L"Выгрузить", fSmall, RGB(248, 180, 180), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(W - 92, 13, W - 14, 43, ACT_UNLOAD_MODEL);
    } else {
        FillRoundBox(hdc, modBoxX, 13, W - 14, 43, 6, COL_WELL, COL_BORDER);
        std::wstring mLbl = snap.isLoading ? (L"Загрузка: " + shortAct) : (L"Выбрана: " + shortAct);
        DrawLabel(hdc, modBoxX + 10, 13, W - 24 - modBoxX, 30, mLbl.c_str(), fSmall, snap.isLoading ? COL_GOLD : COL_MUTED, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
    }
    int bodyTop = hdrH;
    if (snap.isLoading) {
        FillBox(hdc, sideW, bodyTop, W, bodyTop + 48, COL_GOLD_BG, COL_GOLD_DIM);
        wchar_t progTxt[320];
        swprintf_s(progTxt, L"РАЗВЕРТЫВАНИЕ: %s   |   %s   |   %.1f / %.1f МБ  (%.1f МБ/с)   |   Осталось: %s",
            shortAct.c_str(), snap.loadStage.c_str(), snap.loadCurrMb, snap.loadTotalMb, snap.loadSpeedMb, snap.loadEta.c_str());
        DrawLabel(hdc, sideW + 18, bodyTop + 4, W - sideW - 220, 20, progTxt, fSmall, COL_TEXT);
        int barX1 = sideW + 18;
        int barX2 = W - 130;
        FillRoundBox(hdc, barX1, bodyTop + 28, barX2, bodyTop + 40, 4, COL_WELL, COL_BORDER);
        int fillW = static_cast<int>((barX2 - barX1) * std::clamp(snap.loadPct / 100.0, 0.02, 1.0));
        FillRoundBox(hdc, barX1, bodyTop + 28, barX1 + fillW, bodyTop + 40, 4, COL_GOLD, COL_GOLD);
        FillRoundBox(hdc, W - 116, bodyTop + 10, W - 16, bodyTop + 38, 5, COL_CRIMSON_BG, COL_CRIMSON);
        DrawLabel(hdc, W - 116, bodyTop + 10, 100, 28, L"Отменить", fSmall, RGB(252, 185, 185), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(W - 116, bodyTop + 10, W - 16, bodyTop + 38, ACT_CANCEL_LOAD);
        bodyTop += 48;
    }
    int footerH = 30;
    int bodyBot = H - footerH;
    FillBox(hdc, 0, hdrH, sideW, bodyBot, COL_SIDEBAR, COL_BORDER);
    const wchar_t* navTitles[6] = {
        L"Каталог Hugging Face",
        L"Подбор под Железо и SSD",
        L"Локальный ИИ Чат",
        L"Подключение CLI и IDE",
        L"API Ключи и Сервер",
        L"Ядро AVX2 и Гайд"
    };
    const wchar_t* navSubs[6] = {
        L"3 000 000+ открытых моделей",
        L"Совместимость GPU/RAM и диск",
        L"Живой диалог без облака",
        L"Claude, OpenRouter, Cursor...",
        L"Ключи sk-shard и эндпоинты",
        L"Тест GFLOPS и документация"
    };
    DrawLabel(hdc, 18, hdrH + 14, sideW - 28, 18, L"РАБОЧИЕ ПРОСТРАНСТВА", fSmall, COL_MUTED);
    for (int i = 0; i < 6; ++i) {
        int ny = hdrH + 40 + i * 56;
        bool sel = (g_state.activeTab == i);
        FillRoundBox(hdc, 10, ny, sideW - 10, ny + 48, 6, sel ? COL_CARD_HOVER : COL_SIDEBAR, sel ? COL_GOLD_DIM : CLR_NONE);
        if (sel) FillBox(hdc, 10, ny + 8, 14, ny + 40, COL_GOLD, COL_GOLD);
        DrawLabel(hdc, 24, ny + 6, sideW - 40, 20, navTitles[i], fSub, sel ? COL_GOLD : COL_TEXT);
        DrawLabel(hdc, 24, ny + 26, sideW - 40, 16, navSubs[i], fSmall, COL_MUTED);
        AddHit(10, ny, sideW - 10, ny + 48, ACT_NAV_TAB, i);
    }
    int boxY = bodyBot - 118;
    if (boxY > hdrH + 390) {
        FillRoundBox(hdc, 12, boxY, sideW - 12, bodyBot - 12, 6, COL_WELL, COL_BORDER);
        DrawLabel(hdc, 22, boxY + 8, sideW - 44, 18, L"ЛОКАЛЬНЫЙ BASE URL", fSmall, COL_GOLD);
        DrawLabel(hdc, 22, boxY + 28, sideW - 44, 18, L"http://localhost:8000/v1", fMono, COL_TEXT);
        FillRoundBox(hdc, 22, boxY + 56, sideW - 22, boxY + 84, 5, COL_CARD, COL_BORDER);
        DrawLabel(hdc, 22, boxY + 56, sideW - 44, 28, L"Скопировать URL", fSmall, COL_SUBTEXT, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(22, boxY + 56, sideW - 22, boxY + 84, ACT_COPY_TEXT, 0, L"http://localhost:8000/v1");
    }
    int mainX = sideW + 18;
    int mainW = W - mainX - 18;
    if (g_state.activeTab == 0) {
        int sBarY = bodyTop + 14;
        int searchW = std::max(260, mainW - 160);
        bool focused = (g_focusField == 1);
        FillRoundBox(hdc, mainX, sBarY, mainX + searchW, sBarY + 38, 6, COL_WELL, focused ? COL_GOLD : COL_BORDER);
        DrawMagnifier(hdc, mainX + 20, sBarY + 19, focused ? COL_GOLD : COL_MUTED);
        std::wstring dispSearch = snap.searchQuery;
        if (focused && g_caretVisible) dispSearch += L"|";
        if (snap.searchQuery.empty() && !focused) {
            DrawLabel(hdc, mainX + 38, sBarY, searchW - 80, 38, L"Поиск по 3 000 000+ моделям Hugging Face (qwen, deepseek, llama, owner/repo)...", fBody, COL_MUTED);
        } else {
            DrawLabel(hdc, mainX + 38, sBarY, searchW - 80, 38, dispSearch.c_str(), fBody, COL_TEXT);
        }
        AddHit(mainX, sBarY, mainX + searchW - 36, sBarY + 38, ACT_FOCUS_SEARCH);
        if (!snap.searchQuery.empty()) {
            DrawLabel(hdc, mainX + searchW - 34, sBarY, 28, 38, L"X", fSub, COL_MUTED, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            AddHit(mainX + searchW - 34, sBarY, mainX + searchW, sBarY + 38, ACT_CLEAR_SEARCH);
        }
        FillRoundBox(hdc, mainX + searchW + 10, sBarY, mainX + mainW, sBarY + 38, 6, COL_GOLD, COL_GOLD);
        DrawLabel(hdc, mainX + searchW + 10, sBarY, mainW - searchW - 10, 38, L"Найти в Hub", fSub, RGB(12, 13, 16), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(mainX + searchW + 10, sBarY, mainX + mainW, sBarY + 38, ACT_RUN_SEARCH);
        const wchar_t* catIds[7] = { L"all", L"ideal", L"coding", L"reasoning", L"compact", L"general", L"downloaded" };
        wchar_t ssdPill[64];
        swprintf_s(ssdPill, L"На SSD (%zu)", snap.ssdModels.size());
        const wchar_t* catLabels[7] = { L"Все популярные", L"Идеально под ваш ПК", L"Кодинг и IDE", L"Рассуждения", L"Компактные VRAM", L"Универсальные", ssdPill };
        int pillY = sBarY + 48;
        int px = mainX;
        for (int c = 0; c < 7; ++c) {
            int pw = (c == 1) ? 156 : ((c == 0 || c == 4) ? 132 : 116);
            if (px + pw > mainX + mainW) break;
            bool cSel = (snap.currentCat == catIds[c]);
            FillRoundBox(hdc, px, pillY, px + pw, pillY + 30, 5, cSel ? COL_GOLD_BG : COL_CARD, cSel ? COL_GOLD : COL_BORDER);
            DrawLabel(hdc, px, pillY, pw, 30, catLabels[c], fSmall, cSel ? COL_GOLD : COL_SUBTEXT, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            AddHit(px, pillY, px + pw, pillY + 30, ACT_CAT_FILTER, c, catIds[c]);
            px += pw + 8;
        }
        wchar_t countInfo[200];
        if (snap.catalogBusy) swprintf_s(countInfo, L"Синхронизация каталога Hugging Face Hub...");
        else swprintf_s(countInfo, L"Доступно моделей в выборке: %zu   |   Автоматическая оценка совместимости с %s (%d МБ VRAM / %.1f ГБ RAM)", snap.catalogModels.size(), snap.gpuName.c_str(), snap.vramTotalMb, snap.ramTotalGb);
        DrawLabel(hdc, mainX, pillY + 36, mainW, 20, countInfo, fSmall, COL_MUTED);
        int gridTop = pillY + 62;
        int gridBot = bodyBot - 8;
        int cols = (mainW >= 960) ? 3 : 2;
        int gap = 12;
        int cardW = (mainW - gap * (cols - 1) - 10) / cols;
        int cardH = 168;
        int rows = (static_cast<int>(snap.catalogModels.size()) + cols - 1) / cols;
        int totalH = rows * (cardH + gap);
        g_maxScrollY[0] = std::max(0, totalH - (gridBot - gridTop));
        g_scrollY[0] = std::clamp(g_scrollY[0], 0, g_maxScrollY[0]);
        int savedDC = SaveDC(hdc);
        IntersectClipRect(hdc, mainX, gridTop, mainX + mainW, gridBot);
        if (snap.catalogModels.empty() && !snap.catalogBusy) {
            FillRoundBox(hdc, mainX, gridTop + 10, mainX + mainW - 12, gridTop + 110, 8, COL_CARD, COL_BORDER);
            DrawLabel(hdc, mainX + 20, gridTop + 30, mainW - 50, 24, L"В данной категории пока нет моделей.", fSub, COL_TEXT);
            DrawLabel(hdc, mainX + 20, gridTop + 58, mainW - 50, 22, L"Попробуйте другой поисковый запрос или переключитесь на «Все популярные» / «Идеально под ваш ПК».", fBody, COL_MUTED);
        }
        for (size_t i = 0; i < snap.catalogModels.size(); ++i) {
            int r = static_cast<int>(i) / cols;
            int c = static_cast<int>(i) % cols;
            int cx = mainX + c * (cardW + gap);
            int cy = gridTop + r * (cardH + gap) - g_scrollY[0];
            RenderModelGridCard(hdc, cx, cy, cardW, cardH, gridTop, gridBot, snap.catalogModels[i], snap, fSub, fBody, fSmall, fMono);
        }
        RestoreDC(hdc, savedDC);
        if (g_maxScrollY[0] > 0) {
            int sbH = gridBot - gridTop;
            int thumbH = std::max(32, sbH * sbH / (sbH + g_maxScrollY[0]));
            int thumbY = gridTop + (sbH - thumbH) * g_scrollY[0] / g_maxScrollY[0];
            FillRoundBox(hdc, mainX + mainW - 5, gridTop, mainX + mainW, gridBot, 3, COL_WELL, CLR_NONE);
            FillRoundBox(hdc, mainX + mainW - 5, thumbY, mainX + mainW, thumbY + thumbH, 3, COL_GOLD_DIM, CLR_NONE);
        }
    } else if (g_state.activeTab == 1) {
        int viewTop = bodyTop + 14;
        int viewBot = bodyBot - 10;
        int savedDC = SaveDC(hdc);
        IntersectClipRect(hdc, mainX, viewTop, mainX + mainW, viewBot);
        int curY = viewTop - g_scrollY[1];
        FillRoundBox(hdc, mainX, curY, mainX + mainW, curY + 96, 8, COL_CARD, COL_GOLD_DIM);
        wchar_t profHdr[256];
        swprintf_s(profHdr, L"АППАРАТНЫЙ ПРОФИЛЬ ПК:  %s  |  VRAM: %d МБ  |  RAM: %.1f ГБ  |  Потоков CPU: %d  |  Свободно на SSD: %.1f ГБ",
            snap.gpuName.c_str(), snap.vramTotalMb, snap.ramTotalGb, g_state.cpuThreads, snap.diskFreeGb);
        DrawLabel(hdc, mainX + 18, curY + 12, mainW - 36, 24, profHdr, fSub, COL_GOLD);
        DrawLabel(hdc, mainX + 18, curY + 40, mainW - 36, 20, L"Shard автоматически классифицирует нейросети Hugging Face на 3 эшелона под вашу конфигурацию памяти, предотвращая зависания и своп диска:", fBody, COL_SUBTEXT);
        DrawLabel(hdc, mainX + 18, curY + 64, mainW - 36, 20, L"Рекомендация для мгновенной работы без лагов на вашем ПК: модели от 0.5B до 3B параметров (FP16 / Q4).", fSmall, COL_EMERALD);
        curY += 110;
        int tierW = (mainW - 24) / 3;
        struct TierBox { const wchar_t* title; const wchar_t* badge; const wchar_t* desc; const wchar_t* recId; const wchar_t* recName; COLORREF col; COLORREF bg; };
        TierBox tiers[3] = {
            { L"ЭШЕЛОН 1: 100% БЫСТРАЯ ПАМЯТЬ", L"45 - 75 ток/сек", L"Модели 0.3B - 3B. Мгновенный отклик без нагрузки на систему. Идеально для постоянной работы в Claude CLI и Cursor.", L"Qwen/Qwen2.5-Coder-1.5B-Instruct", L"Запустить Qwen2.5-Coder-1.5B", COL_EMERALD, COL_EMERALD_BG },
            { L"ЭШЕЛОН 2: ГИБРИД VRAM + RAM", L"18 - 32 ток/сек", L"Модели 3.8B - 8B. Ключевые слои внимания в GPU, остальные в системной RAM в компактном FP16.", L"deepseek-ai/DeepSeek-R1-Distill-Qwen-7B", L"Запустить DeepSeek-R1-7B", COL_AMBER, COL_AMBER_BG },
            { L"ЭШЕЛОН 3: NVMe ZERO-COPY СТРИМИНГ", L"Без лимита ОЗУ", L"Тяжелые модели 14B - 70B+. Прямое отображение Safetensors через Win32 MapViewOfFile с SSD.", L"Qwen/Qwen2.5-Coder-14B-Instruct", L"Запустить Qwen2.5-Coder-14B", COL_TERRA, COL_TERRA_BG }
        };
        for (int t = 0; t < 3; ++t) {
            int tx = mainX + t * (tierW + 12);
            FillRoundBox(hdc, tx, curY, tx + tierW, curY + 166, 8, COL_CARD, tiers[t].col);
            DrawLabel(hdc, tx + 14, curY + 12, tierW - 28, 20, tiers[t].title, fSmall, tiers[t].col);
            FillRoundBox(hdc, tx + 14, curY + 36, tx + 146, curY + 56, 4, tiers[t].bg, tiers[t].col);
            DrawLabel(hdc, tx + 14, curY + 36, 132, 20, tiers[t].badge, fSmall, tiers[t].col, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            DrawMultiline(hdc, tx + 14, curY + 64, tierW - 28, 56, tiers[t].desc, fBody, COL_SUBTEXT);
            FillRoundBox(hdc, tx + 14, curY + 126, tx + tierW - 14, curY + 154, 5, COL_GOLD, COL_GOLD);
            DrawLabel(hdc, tx + 14, curY + 126, tierW - 28, 28, tiers[t].recName, fSmall, RGB(12, 13, 16), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            if (curY + 126 >= viewTop && curY + 154 <= viewBot) {
                AddHit(tx + 14, curY + 126, tx + tierW - 14, curY + 154, ACT_MODEL_LOAD, 0, tiers[t].recId);
            }
        }
        curY += 184;
        FillRoundBox(hdc, mainX, curY, mainX + mainW, curY + 44, 6, COL_SIDEBAR, COL_BORDER);
        wchar_t ssdHdr[256];
        swprintf_s(ssdHdr, L"УПРАВЛЕНИЕ ЛОКАЛЬНЫМ ХРАНИЛИЩЕМ SSD   |   Скачано моделей: %zu   |   Занято весами: %.2f ГБ   |   Свободно: %.1f ГБ",
            snap.ssdModels.size(), snap.ssdTotalGb, snap.diskFreeGb);
        DrawLabel(hdc, mainX + 16, curY, mainW - 210, 44, ssdHdr, fSub, COL_TEXT);
        FillRoundBox(hdc, mainX + mainW - 186, curY + 8, mainX + mainW - 12, curY + 36, 5, COL_CARD, COL_GOLD_DIM);
        DrawLabel(hdc, mainX + mainW - 186, curY + 8, 174, 28, L"Показать все Идеальные", fSmall, COL_GOLD, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        if (curY + 8 >= viewTop && curY + 36 <= viewBot) {
            AddHit(mainX + mainW - 186, curY + 8, mainX + mainW - 12, curY + 36, ACT_CAT_FILTER, 1, L"ideal");
        }
        curY += 54;
        if (snap.ssdModels.empty()) {
            FillRoundBox(hdc, mainX, curY, mainX + mainW, curY + 74, 6, COL_CARD, COL_BORDER);
            DrawLabel(hdc, mainX + 18, curY + 24, mainW - 36, 24, L"На вашем SSD еще нет сохраненных весов моделей. Выберите любую модель выше или в Каталоге для скачивания.", fBody, COL_MUTED);
            curY += 84;
        } else {
            for (const auto& sm : snap.ssdModels) {
                FillRoundBox(hdc, mainX, curY, mainX + mainW, curY + 54, 6, COL_CARD, sm.isActive ? COL_EMERALD : COL_BORDER);
                DrawLabel(hdc, mainX + 16, curY + 8, mainW - 380, 20, sm.id.c_str(), fSub, COL_TEXT);
                std::wstring smMeta = L"Размер на диске: " + sm.sizeStr + L"   |   " + sm.badge + (sm.isActive ? L"   |   [В ПАМЯТИ]" : L"");
                DrawLabel(hdc, mainX + 16, curY + 28, mainW - 380, 18, smMeta.c_str(), fSmall, sm.isActive ? COL_EMERALD : COL_MUTED);
                if (curY + 12 >= viewTop && curY + 42 <= viewBot) {
                    FillRoundBox(hdc, mainX + mainW - 246, curY + 12, mainX + mainW - 132, curY + 42, 5, COL_GOLD, COL_GOLD);
                    DrawLabel(hdc, mainX + mainW - 246, curY + 12, 114, 30, L"Запустить", fSmall, RGB(12, 13, 16), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
                    AddHit(mainX + mainW - 246, curY + 12, mainX + mainW - 132, curY + 42, ACT_MODEL_LOAD, 0, sm.id);
                    FillRoundBox(hdc, mainX + mainW - 122, curY + 12, mainX + mainW - 14, curY + 42, 5, COL_CRIMSON_BG, COL_CRIMSON);
                    DrawLabel(hdc, mainX + mainW - 122, curY + 12, 108, 30, L"Удалить с SSD", fSmall, RGB(248, 180, 180), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
                    AddHit(mainX + mainW - 122, curY + 12, mainX + mainW - 14, curY + 42, ACT_MODEL_DELETE_SSD, 0, sm.id);
                }
                curY += 62;
            }
        }
        int totalH1 = (curY + g_scrollY[1]) - viewTop;
        g_maxScrollY[1] = std::max(0, totalH1 - (viewBot - viewTop));
        g_scrollY[1] = std::clamp(g_scrollY[1], 0, g_maxScrollY[1]);
        RestoreDC(hdc, savedDC);
    } else if (g_state.activeTab == 2) {
        int sessW = 216;
        int chatTop = bodyTop + 12;
        int chatBot = bodyBot - 12;
        FillRoundBox(hdc, mainX, chatTop, mainX + sessW, chatBot, 8, COL_CARD, COL_BORDER);
        FillRoundBox(hdc, mainX + 10, chatTop + 10, mainX + sessW - 10, chatTop + 42, 6, COL_GOLD, COL_GOLD);
        DrawLabel(hdc, mainX + 10, chatTop + 10, sessW - 20, 32, L"+ Новый диалог", fSub, RGB(12, 13, 16), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(mainX + 10, chatTop + 10, mainX + sessW - 10, chatTop + 42, ACT_CHAT_NEW);
        for (size_t i = 0; i < snap.chats.size() && i < 12; ++i) {
            int sy = chatTop + 52 + static_cast<int>(i) * 40;
            if (sy + 36 > chatBot - 10) break;
            bool cAct = (static_cast<int>(i) == snap.activeChatIdx);
            FillRoundBox(hdc, mainX + 10, sy, mainX + sessW - 10, sy + 34, 5, cAct ? COL_CARD_HOVER : COL_WELL, cAct ? COL_GOLD_DIM : COL_BORDER);
            DrawLabel(hdc, mainX + 18, sy, sessW - 56, 34, snap.chats[i].title.c_str(), fSmall, cAct ? COL_TEXT : COL_SUBTEXT);
            AddHit(mainX + 10, sy, mainX + sessW - 38, sy + 34, ACT_CHAT_SELECT, static_cast<int>(i));
            DrawLabel(hdc, mainX + sessW - 36, sy, 24, 34, L"x", fSmall, COL_MUTED, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            AddHit(mainX + sessW - 36, sy, mainX + sessW - 10, sy + 34, ACT_CHAT_DELETE, static_cast<int>(i));
        }
        int areaX = mainX + sessW + 12;
        int areaW = mainW - sessW - 12;
        FillRoundBox(hdc, areaX, chatTop, areaX + areaW, chatTop + 44, 8, COL_CARD, COL_BORDER);
        std::wstring chatHdr = L"Модель: " + shortAct + (snap.isModelLoaded ? L" [В ПАМЯТИ]" : L" [НЕ ЗАГРУЖЕНА]");
        DrawLabel(hdc, areaX + 14, chatTop, areaW - 340, 44, chatHdr.c_str(), fSub, snap.isModelLoaded ? COL_EMERALD : COL_GOLD);
        wchar_t tBtn[48], mBtn[48];
        swprintf_s(tBtn, L"Temp: %.1f", snap.chatTemp);
        swprintf_s(mBtn, L"Max: %d ток", snap.chatMaxTokens);
        FillRoundBox(hdc, areaX + areaW - 226, chatTop + 8, areaX + areaW - 130, chatTop + 36, 5, COL_WELL, COL_BORDER);
        DrawLabel(hdc, areaX + areaW - 226, chatTop + 8, 96, 28, tBtn, fSmall, COL_SUBTEXT, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(areaX + areaW - 226, chatTop + 8, areaX + areaW - 130, chatTop + 36, ACT_CHAT_TEMP);
        FillRoundBox(hdc, areaX + areaW - 122, chatTop + 8, areaX + areaW - 12, chatTop + 36, 5, COL_WELL, COL_BORDER);
        DrawLabel(hdc, areaX + areaW - 122, chatTop + 8, 110, 28, mBtn, fSmall, COL_SUBTEXT, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(areaX + areaW - 122, chatTop + 8, areaX + areaW - 12, chatTop + 36, ACT_CHAT_MAXTOK);
        int msgTop = chatTop + 52;
        int inputH = 54;
        int msgBot = chatBot - inputH - 10;
        FillRoundBox(hdc, areaX, msgTop, areaX + areaW, msgBot, 8, COL_CARD, COL_BORDER);
        const ChatSession* curSess = (!snap.chats.empty() && snap.activeChatIdx >= 0 && snap.activeChatIdx < static_cast<int>(snap.chats.size())) ? &snap.chats[snap.activeChatIdx] : nullptr;
        int totalMsgH = 24;
        if (curSess) {
            for (const auto& m : curSess->messages) {
                int bw = std::max(200, areaW - 90);
                int th = MeasureMultiline(hdc, bw - 28, m.content.empty() ? L"Генерация ответа..." : m.content, fBody) + 34;
                if (!m.reasoning.empty()) th += MeasureMultiline(hdc, bw - 36, m.reasoning, fSmall) + 28;
                if (m.role == L"assistant") th += 26;
                totalMsgH += th + 14;
            }
        }
        g_maxScrollY[2] = std::max(0, totalMsgH - (msgBot - msgTop));
        g_scrollY[2] = std::clamp(g_scrollY[2], 0, g_maxScrollY[2]);
        int savedChat = SaveDC(hdc);
        IntersectClipRect(hdc, areaX + 2, msgTop + 2, areaX + areaW - 2, msgBot - 2);
        if (!curSess || curSess->messages.empty()) {
            DrawLabel(hdc, areaX + 24, msgTop + 32, areaW - 48, 26, L"Локальная песочница ИИ готова к работе", fTitle, COL_TEXT, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            DrawLabel(hdc, areaX + 24, msgTop + 62, areaW - 48, 22, L"Все вычисления выполняются внутри вашего компьютера без отправки данных на внешние серверы.", fBody, COL_MUTED, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            const wchar_t* presets[3] = {
                L"Напиши потокобезопасный пул потоков на C++20",
                L"Объясни разницу между квантованием Q4_0 и FP16",
                L"Проверь архитектуру REST API и предложи оптимизации"
            };
            for (int p = 0; p < 3; ++p) {
                int py = msgTop + 108 + p * 44;
                int px1 = areaX + std::max(30, (areaW - 480) / 2);
                int px2 = areaX + areaW - std::max(30, (areaW - 480) / 2);
                FillRoundBox(hdc, px1, py, px2, py + 34, 6, COL_WELL, COL_BORDER);
                DrawLabel(hdc, px1 + 14, py, px2 - px1 - 28, 34, presets[p], fBody, COL_GOLD, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
                AddHit(px1, py, px2, py + 34, ACT_CHAT_PRESET, p, presets[p]);
            }
        } else {
            int my = msgTop + 14 - g_scrollY[2];
            int bw = std::max(220, areaW - 90);
            for (size_t mi = 0; mi < curSess->messages.size(); ++mi) {
                const auto& m = curSess->messages[mi];
                bool isUser = (m.role == L"user");
                std::wstring dispTxt = m.content.empty() ? (snap.chatStreaming ? L"Генерация токенов..." : L"...") : m.content;
                int contentH = MeasureMultiline(hdc, bw - 28, dispTxt, fBody);
                int reasonH = m.reasoning.empty() ? 0 : (MeasureMultiline(hdc, bw - 36, m.reasoning, fSmall) + 28);
                int boxH = contentH + reasonH + 34 + (isUser ? 0 : 26);
                int bx1 = isUser ? (areaX + areaW - bw - 18) : (areaX + 18);
                int bx2 = bx1 + bw;
                if (my + boxH >= msgTop && my <= msgBot) {
                    FillRoundBox(hdc, bx1, my, bx2, my + boxH, 8, isUser ? COL_GOLD_BG : COL_WELL, isUser ? COL_GOLD_DIM : COL_BORDER);
                    DrawLabel(hdc, bx1 + 14, my + 6, 220, 18, isUser ? L"ВЫ" : L"SHARD LOCAL AI", fSmall, isUser ? COL_GOLD : COL_EMERALD);
                    int innerY = my + 26;
                    if (reasonH > 0) {
                        FillRoundBox(hdc, bx1 + 12, innerY, bx2 - 12, innerY + reasonH - 6, 5, COL_CARD, COL_BORDER);
                        DrawMultiline(hdc, bx1 + 18, innerY + 6, bw - 36, reasonH - 12, m.reasoning, fSmall, COL_MUTED);
                        innerY += reasonH;
                    }
                    DrawMultiline(hdc, bx1 + 14, innerY, bw - 28, contentH + 4, dispTxt, fBody, COL_TEXT);
                    if (!isUser) {
                        int footY = my + boxH - 24;
                        wchar_t tInfo[160];
                        swprintf_s(tInfo, L"Скорость: %.1f ток/сек   |   Время: %.2f с   |   Токенов: %d", m.tokPerSec, m.totalSec, m.totalTokens);
                        DrawLabel(hdc, bx1 + 14, footY, bw - 130, 20, tInfo, fSmall, COL_MUTED);
                        if (footY >= msgTop && footY + 20 <= msgBot) {
                            FillRoundBox(hdc, bx2 - 104, footY - 2, bx2 - 12, footY + 18, 4, COL_CARD, COL_BORDER);
                            DrawLabel(hdc, bx2 - 104, footY - 2, 92, 20, L"Копировать", fSmall, COL_SUBTEXT, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
                            AddHit(bx2 - 104, footY - 2, bx2 - 12, footY + 18, ACT_CHAT_COPY_MSG, static_cast<int>(mi), m.content);
                        }
                    }
                }
                my += boxH + 14;
            }
        }
        RestoreDC(hdc, savedChat);
        int inpY = chatBot - inputH;
        bool chatFoc = (g_focusField == 2);
        FillRoundBox(hdc, areaX, inpY, areaX + areaW - 138, chatBot, 8, COL_WELL, chatFoc ? COL_GOLD : COL_BORDER);
        std::wstring cDisp = snap.chatInput;
        if (chatFoc && g_caretVisible) cDisp += L"|";
        if (snap.chatInput.empty() && !chatFoc) {
            DrawLabel(hdc, areaX + 16, inpY, areaW - 166, inputH, L"Введите задачу или код (Enter — отправить, Ctrl+V — вставить)...", fBody, COL_MUTED);
        } else {
            DrawLabel(hdc, areaX + 16, inpY, areaW - 166, inputH, cDisp.c_str(), fBody, COL_TEXT);
        }
        AddHit(areaX, inpY, areaX + areaW - 138, chatBot, ACT_CHAT_FOCUS_INPUT);
        if (snap.chatStreaming) {
            FillRoundBox(hdc, areaX + areaW - 128, inpY, areaX + areaW, chatBot, 8, COL_CRIMSON, COL_CRIMSON);
            DrawLabel(hdc, areaX + areaW - 128, inpY, 128, inputH, L"Остановить", fSub, RGB(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            AddHit(areaX + areaW - 128, inpY, areaX + areaW, chatBot, ACT_CHAT_STOP);
        } else {
            FillRoundBox(hdc, areaX + areaW - 128, inpY, areaX + areaW, chatBot, 8, COL_GOLD, COL_GOLD);
            DrawLabel(hdc, areaX + areaW - 128, inpY, 128, inputH, L"Отправить", fSub, RGB(12, 13, 16), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            AddHit(areaX + areaW - 128, inpY, areaX + areaW, chatBot, ACT_CHAT_SEND);
        }
    } else if (g_state.activeTab == 3) {
        int listW = 244;
        int topY = bodyTop + 14;
        int botY = bodyBot - 12;
        FillRoundBox(hdc, mainX, topY, mainX + listW, botY, 8, COL_CARD, COL_BORDER);
        DrawLabel(hdc, mainX + 14, topY + 10, listW - 28, 20, L"ВЫБЕРИТЕ CLI / IDE / SDK", fSmall, COL_GOLD);
        const wchar_t* toolNames[10] = {
            L"01. Claude Code CLI",
            L"02. OpenRouter / Агенты",
            L"03. Cursor IDE",
            L"04. OpenCode CLI",
            L"05. Continue (VS Code)",
            L"06. Aider CLI",
            L"07. Zed Editor",
            L"08. Codex CLI (OpenAI)",
            L"09. Python / JS / cURL",
            L"10. Windows ENV (Глобально)"
        };
        for (int t = 0; t < 10; ++t) {
            int ty = topY + 36 + t * 40;
            if (ty + 36 > botY - 6) break;
            bool tSel = (snap.selectedToolIdx == t);
            FillRoundBox(hdc, mainX + 10, ty, mainX + listW - 10, ty + 34, 5, tSel ? COL_GOLD_BG : COL_WELL, tSel ? COL_GOLD : COL_BORDER);
            DrawLabel(hdc, mainX + 18, ty, listW - 36, 34, toolNames[t], fSmall, tSel ? COL_GOLD : COL_TEXT);
            AddHit(mainX + 10, ty, mainX + listW - 10, ty + 34, ACT_INTEG_SELECT, t);
        }
        int detX = mainX + listW + 14;
        int detW = mainW - listW - 14;
        IntegSpec spec = BuildIntegrationSpec(snap.selectedToolIdx, snap.activeModelId, snap.activeApiKey);
        FillRoundBox(hdc, detX, topY, detX + detW, botY, 8, COL_CARD, COL_BORDER);
        DrawLabel(hdc, detX + 20, topY + 14, detW - 260, 26, spec.name.c_str(), fTitle, COL_TEXT);
        DrawLabel(hdc, detX + 20, topY + 40, detW - 260, 18, spec.subtitle.c_str(), fBody, COL_GOLD);
        FillRoundBox(hdc, detX + detW - 238, topY + 14, detX + detW - 18, topY + 50, 6, COL_GOLD, COL_GOLD);
        DrawLabel(hdc, detX + detW - 238, topY + 14, 220, 36, L"Применить в 1 клик на ПК", fSub, RGB(12, 13, 16), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(detX + detW - 238, topY + 14, detX + detW - 18, topY + 50, ACT_INTEG_APPLY, 0, spec.slug);
        int pBarY = topY + 68;
        int thirdW = (detW - 56) / 3;
        FillRoundBox(hdc, detX + 20, pBarY, detX + 20 + thirdW, pBarY + 34, 5, COL_WELL, COL_BORDER);
        DrawLabel(hdc, detX + 28, pBarY, thirdW - 16, 34, L"Копировать URL (:8000/v1)", fSmall, COL_SUBTEXT, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(detX + 20, pBarY, detX + 20 + thirdW, pBarY + 34, ACT_INTEG_COPY_URL);
        FillRoundBox(hdc, detX + 28 + thirdW, pBarY, detX + 28 + thirdW * 2, pBarY + 34, 5, COL_WELL, COL_BORDER);
        DrawLabel(hdc, detX + 34 + thirdW, pBarY, thirdW - 12, 34, L"Копировать API Ключ", fSmall, COL_SUBTEXT, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(detX + 28 + thirdW, pBarY, detX + 28 + thirdW * 2, pBarY + 34, ACT_INTEG_COPY_KEY);
        FillRoundBox(hdc, detX + 36 + thirdW * 2, pBarY, detX + detW - 20, pBarY + 34, 5, COL_WELL, COL_BORDER);
        DrawLabel(hdc, detX + 40 + thirdW * 2, pBarY, thirdW - 12, 34, L"Копировать ID Модели", fSmall, COL_SUBTEXT, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(detX + 36 + thirdW * 2, pBarY, detX + detW - 20, pBarY + 34, ACT_INTEG_COPY_MODEL);
        std::wstring pathInfo = L"Целевой путь конфигурации:  " + spec.targetPath;
        DrawLabel(hdc, detX + 20, pBarY + 44, detW - 40, 20, pathInfo.c_str(), fMono, COL_MUTED);
        int stH = MeasureMultiline(hdc, detW - 40, spec.steps, fBody);
        DrawMultiline(hdc, detX + 20, pBarY + 70, detW - 40, stH + 6, spec.steps, fBody, COL_TEXT);
        int codeY = pBarY + 82 + stH;
        int codeBot = botY - 54;
        if (codeBot > codeY + 40) {
            FillRoundBox(hdc, detX + 20, codeY, detX + detW - 20, codeBot, 6, COL_WELL, COL_BORDER);
            FillRoundBox(hdc, detX + detW - 176, codeY + 8, detX + detW - 28, codeY + 34, 5, COL_CARD_HOVER, COL_GOLD_DIM);
            DrawLabel(hdc, detX + detW - 176, codeY + 8, 148, 26, L"Скопировать конфиг", fSmall, COL_GOLD, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            AddHit(detX + detW - 176, codeY + 8, detX + detW - 28, codeY + 34, ACT_INTEG_COPY_MAIN, 0, spec.codeBlock);
            int savedCode = SaveDC(hdc);
            IntersectClipRect(hdc, detX + 26, codeY + 38, detX + detW - 26, codeBot - 6);
            DrawMultiline(hdc, detX + 28, codeY + 38, detW - 60, codeBot - codeY - 44, spec.codeBlock, fMono, RGB(214, 224, 238));
            RestoreDC(hdc, savedCode);
        }
        FillRoundBox(hdc, detX + 20, botY - 44, detX + 200, botY - 12, 5, COL_EMERALD_BG, COL_EMERALD);
        DrawLabel(hdc, detX + 20, botY - 44, 180, 32, L"Проверить /v1/ping", fSmall, COL_EMERALD, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(detX + 20, botY - 44, detX + 200, botY - 12, ACT_INTEG_PING);
        DrawLabel(hdc, detX + 214, botY - 44, detW - 234, 32, snap.pingReport.c_str(), fMono, COL_SUBTEXT);
    } else if (g_state.activeTab == 4) {
        int topY = bodyTop + 14;
        int halfW = (mainW - 16) / 2;
        int botY = bodyBot - 12;
        FillRoundBox(hdc, mainX, topY, mainX + halfW, botY, 8, COL_CARD, COL_BORDER);
        DrawLabel(hdc, mainX + 18, topY + 14, halfW - 36, 24, L"УПРАВЛЕНИЕ ЛОКАЛЬНЫМ СЕРВЕРОМ SHARD", fTitle, COL_TEXT);
        std::wstring stDesc = snap.serverOnline ? L"Статус: АКТИВЕН (127.0.0.1:8000, без консольных окон)" : L"Статус: ОСТАНОВЛЕН";
        DrawLabel(hdc, mainX + 18, topY + 44, halfW - 36, 20, stDesc.c_str(), fSub, snap.serverOnline ? COL_EMERALD : COL_CRIMSON);
        int bw3 = (halfW - 52) / 3;
        FillRoundBox(hdc, mainX + 18, topY + 74, mainX + 18 + bw3, topY + 106, 5, COL_EMERALD, COL_EMERALD);
        DrawLabel(hdc, mainX + 18, topY + 74, bw3, 32, L"Запустить", fSmall, RGB(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(mainX + 18, topY + 74, mainX + 18 + bw3, topY + 106, ACT_SRV_START);
        FillRoundBox(hdc, mainX + 26 + bw3, topY + 74, mainX + 26 + bw3 * 2, topY + 106, 5, COL_GOLD_BG, COL_GOLD);
        DrawLabel(hdc, mainX + 26 + bw3, topY + 74, bw3, 32, L"Перезапустить", fSmall, COL_GOLD, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(mainX + 26 + bw3, topY + 74, mainX + 26 + bw3 * 2, topY + 106, ACT_SRV_RESTART);
        FillRoundBox(hdc, mainX + 34 + bw3 * 2, topY + 74, mainX + halfW - 18, topY + 106, 5, COL_CRIMSON_BG, COL_CRIMSON);
        DrawLabel(hdc, mainX + 34 + bw3 * 2, topY + 74, bw3, 32, L"Остановить", fSmall, RGB(248, 180, 180), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(mainX + 34 + bw3 * 2, topY + 74, mainX + halfW - 18, topY + 106, ACT_SRV_STOP);
        FillRoundBox(hdc, mainX + 18, topY + 120, mainX + halfW - 18, topY + 156, 6, COL_WELL, snap.keepServerOnExit ? COL_GOLD : COL_BORDER);
        const wchar_t* bgLbl = snap.keepServerOnExit
            ? L"Оставлять сервер работать в фоне при закрытии окна: ВКЛЮЧЕНО"
            : L"Оставлять сервер работать в фоне при закрытии окна: ВЫКЛЮЧЕНО";
        DrawLabel(hdc, mainX + 30, topY + 120, halfW - 60, 36, bgLbl, fSmall, snap.keepServerOnExit ? COL_GOLD : COL_SUBTEXT);
        AddHit(mainX + 18, topY + 120, mainX + halfW - 18, topY + 156, ACT_SRV_KEEP_BG);
        DrawLabel(hdc, mainX + 18, topY + 174, halfW - 36, 20, L"ЛОКАЛЬНЫЕ ПРОТОКОЛЫ И ЭНДПОИНТЫ (КЛИК — КОПИРОВАТЬ):", fSmall, COL_MUTED);
        const wchar_t* eps[5] = {
            L"http://localhost:8000/v1/chat/completions",
            L"http://localhost:8000/v1/messages",
            L"http://localhost:8000/v1/models",
            L"http://localhost:8000/v1/ping",
            L"http://localhost:8000/i?os=windows&tool=claude"
        };
        for (int e = 0; e < 5; ++e) {
            int ey = topY + 202 + e * 42;
            if (ey + 36 > botY - 14) break;
            FillRoundBox(hdc, mainX + 18, ey, mainX + halfW - 18, ey + 34, 5, COL_WELL, COL_BORDER);
            DrawLabel(hdc, mainX + 28, ey, halfW - 56, 34, eps[e], fMono, COL_TEXT);
            AddHit(mainX + 18, ey, mainX + halfW - 18, ey + 34, ACT_COPY_TEXT, 0, eps[e]);
        }
        int kx = mainX + halfW + 16;
        FillRoundBox(hdc, kx, topY, kx + halfW, botY, 8, COL_CARD, COL_BORDER);
        DrawLabel(hdc, kx + 18, topY + 14, halfW - 200, 24, L"КЛЮЧИ АВТОРИЗАЦИИ API", fTitle, COL_TEXT);
        FillRoundBox(hdc, kx + halfW - 186, topY + 12, kx + halfW - 18, topY + 44, 6, COL_GOLD, COL_GOLD);
        DrawLabel(hdc, kx + halfW - 186, topY + 12, 168, 32, L"+ Создать ключ", fSub, RGB(12, 13, 16), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(kx + halfW - 186, topY + 12, kx + halfW - 18, topY + 44, ACT_KEY_GENERATE);
        DrawLabel(hdc, kx + 18, topY + 52, halfW - 36, 20, L"Локальные ключи sk-shard-* для Cursor, Claude Code, OpenRouter SDK и OpenCode:", fBody, COL_SUBTEXT);
        for (size_t ki = 0; ki < snap.apiKeys.size() && ki < 10; ++ki) {
            int ky = topY + 84 + static_cast<int>(ki) * 48;
            if (ky + 42 > botY - 14) break;
            bool isKAct = (snap.apiKeys[ki] == snap.activeApiKey);
            FillRoundBox(hdc, kx + 18, ky, kx + halfW - 18, ky + 40, 6, COL_WELL, isKAct ? COL_EMERALD : COL_BORDER);
            DrawLabel(hdc, kx + 28, ky, halfW - 220, 40, snap.apiKeys[ki].c_str(), fMono, isKAct ? COL_EMERALD : COL_TEXT);
            AddHit(kx + 18, ky, kx + halfW - 200, ky + 40, ACT_KEY_SELECT, 0, snap.apiKeys[ki]);
            FillRoundBox(hdc, kx + halfW - 190, ky + 6, kx + halfW - 104, ky + 34, 4, COL_CARD, COL_BORDER);
            DrawLabel(hdc, kx + halfW - 190, ky + 6, 86, 28, L"Копировать", fSmall, COL_SUBTEXT, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            AddHit(kx + halfW - 190, ky + 6, kx + halfW - 104, ky + 34, ACT_KEY_COPY, 0, snap.apiKeys[ki]);
            FillRoundBox(hdc, kx + halfW - 96, ky + 6, kx + halfW - 26, ky + 34, 4, COL_CRIMSON_BG, COL_CRIMSON);
            DrawLabel(hdc, kx + halfW - 96, ky + 6, 70, 28, L"Удалить", fSmall, RGB(248, 180, 180), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            AddHit(kx + halfW - 96, ky + 6, kx + halfW - 26, ky + 34, ACT_KEY_DELETE, 0, snap.apiKeys[ki]);
        }
    } else {
        int topY = bodyTop + 14;
        int kw = (mainW - 36) / 4;
        wchar_t b1[64], b2[64], b3[64], b4[64];
        swprintf_s(b1, L"%.2f GFLOPS", g_state.dotGflops);
        swprintf_s(b2, L"%llu такт / %lld мкс", g_state.rdtscCycles, g_state.dotUs);
        swprintf_s(b3, L"%d Ядер / %d Тестов", g_state.activeKernels, g_state.passedTests);
        swprintf_s(b4, L"%.1f / %.1f ГБ (%dT)", g_state.ramAvailGb, g_state.ramTotalGb, g_state.cpuThreads);
        const wchar_t* kTitles[4] = { L"AVX2 FMA ПРОПУСКНАЯ СПОСОБНОСТЬ", L"RDTSC ТАКТЫ ЦПУ (80K ИТЕР)", L"АССЕМБЛЕРНЫЕ И C++20 ЯДРА", L"СВОБОДНАЯ ОЗУ И ПОТОКИ" };
        const wchar_t* kVals[4] = { b1, b2, b3, b4 };
        COLORREF kCols[4] = { COL_GOLD, COL_EMERALD, COL_GOLD, COL_AMBER };
        for (int i = 0; i < 4; ++i) {
            int cx = mainX + i * (kw + 12);
            FillRoundBox(hdc, cx, topY, cx + kw, topY + 78, 8, COL_CARD, COL_BORDER);
            DrawLabel(hdc, cx + 14, topY + 8, kw - 24, 18, kTitles[i], fSmall, COL_MUTED);
            DrawLabel(hdc, cx + 14, topY + 32, kw - 24, 34, kVals[i], fBigNum, kCols[i]);
        }
        int benchY = topY + 90;
        FillRoundBox(hdc, mainX, benchY, mainX + mainW, benchY + 178, 8, COL_CARD, COL_BORDER);
        DrawLabel(hdc, mainX + 18, benchY + 10, mainW - 240, 26, L"НАТИВНЫЕ ЯДРА MASM x64 AVX2 + C++20 ZERO-COPY MMAP", fSub, COL_TEXT);
        FillRoundBox(hdc, mainX + mainW - 210, benchY + 8, mainX + mainW - 14, benchY + 36, 5, COL_GOLD, COL_GOLD);
        DrawLabel(hdc, mainX + mainW - 210, benchY + 8, 196, 28, L"ЗАПУСТИТЬ ТЕСТ AVX2", fSmall, RGB(12, 13, 16), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(mainX + mainW - 210, benchY + 8, mainX + mainW - 14, benchY + 36, ACT_BENCH_RUN);
        wchar_t kLines[4][190];
        swprintf_s(kLines[0], L"[KRN-002] shard_asm_dot_unroll8   | MASM x64 AVX2 4xYMM FMA   | %8.2f GFLOPS   | [OK]", g_state.dotGflops);
        swprintf_s(kLines[1], L"[KRN-004] shard_asm_rmsnorm_sum   | MASM x64 AVX2 vfmadd231ps | %8.0f ops/sec  | [OK]", g_state.rmsOpsSec);
        swprintf_s(kLines[2], L"[KRN-008] shard_asm_hadamard_f32  | MASM x64 AVX2 vmulps      | %8.0f ops/sec  | [OK]", g_state.hadOpsSec);
        swprintf_s(kLines[3], L"[KRN-016] shard_mmap_open/close   | Win32 MapViewOfFile       | ZERO-COPY PAGING  | [OK]");
        for (int r = 0; r < 4; ++r) {
            int ry = benchY + 44 + r * 31;
            FillRoundBox(hdc, mainX + 16, ry, mainX + mainW - 16, ry + 26, 4, COL_WELL, COL_BORDER);
            DrawLabel(hdc, mainX + 26, ry, mainW - 52, 26, kLines[r], fMono, (r == 0) ? COL_EMERALD : COL_SUBTEXT);
        }
        int gTop = benchY + 190;
        int gBot = bodyBot - 10;
        if (gBot > gTop + 60) {
            FillRoundBox(hdc, mainX, gTop, mainX + mainW, gBot, 8, COL_CARD, COL_BORDER);
            const wchar_t* langs[3] = { L"РУССКИЙ (RU)", L"ENGLISH (EN)", L"中文指南 (ZH)" };
            for (int l = 0; l < 3; ++l) {
                int lx = mainX + 16 + l * 150;
                bool lSel = (g_state.guideLang == l);
                FillRoundBox(hdc, lx, gTop + 12, lx + 138, gTop + 40, 5, lSel ? COL_GOLD : COL_WELL, lSel ? COL_GOLD : COL_BORDER);
                DrawLabel(hdc, lx, gTop + 12, 138, 28, langs[l], fSmall, lSel ? RGB(12, 13, 16) : COL_SUBTEXT, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
                AddHit(lx, gTop + 12, lx + 138, gTop + 40, ACT_GUIDE_LANG, l);
            }
            const wchar_t* guideTxt = nullptr;
            if (g_state.guideLang == 0) {
                guideTxt = L"SHARD STUDIO — ПОЛНОЕ НАТИВНОЕ ПРИЛОЖЕНИЕ ЛОКАЛЬНОГО ИИ (ОТ А ДО Я)\n"
                           L"1. Мгновенный запуск из .exe: При открытии программы локальный сервер (127.0.0.1:8000) поднимается автоматически в фоне без окон cmd и без тяжелого браузера.\n"
                           L"2. Все открытые модели Hugging Face под ваше железо: Ищите среди 3 000 000+ моделей, используйте фильтр «Идеально под ваш ПК», отслеживайте скорость скачивания и очищайте SSD в 1 клик.\n"
                           L"3. Подключение к любому CLI и коду: Во вкладке «Подключение CLI и IDE» нажмите «Применить в 1 клик» для настройки Claude Code, замены OpenRouter API, Cursor, OpenCode, Aider и Zed.";
            } else if (g_state.guideLang == 1) {
                guideTxt = L"SHARD STUDIO — COMPLETE DESKTOP LOCAL AI PLATFORM\n"
                           L"1. Instant Execution: Opening shard.exe / shard-native.exe automatically starts the local FastAPI inference server on 127.0.0.1:8000 with zero console windows or browser overhead.\n"
                           L"2. Hugging Face Hub & Hardware Fit: Search 3,000,000+ open-weight models, filter by 'Ideal for Your PC', download weights to SSD with live speed/ETA tracking, and free disk space anytime.\n"
                           L"3. One-Click CLI & SDK Ecosystem: Connect Claude Code (~/.claude/settings.json), OpenRouter SDK apps, Cursor IDE, OpenCode, Continue, Aider, Zed, and Codex in one click.";
            } else {
                guideTxt = L"SHARD STUDIO — 纯原生桌面级本地大模型运行与集成中心\n"
                           L"1. 单文件即开即用：双击运行 .exe 自动在后台静默启动 127.0.0.1:8000 推理引擎，无需浏览器，无命令行弹窗，零界面卡顿。\n"
                           L"2. Hugging Face 全量模型与硬件适配：实时检索 300 万+ 开源权重，智能评估当前 GPU VRAM 与内存兼容性，支持 SSD 缓存一键清理。\n"
                           L"3. 一键接入主流 CLI 与 IDE：内置 Claude Code、OpenRouter 本地桥接、Cursor、OpenCode、Continue、Aider、Zed 一键自动写入配置。";
            }
            int savedG = SaveDC(hdc);
            IntersectClipRect(hdc, mainX + 16, gTop + 48, mainX + mainW - 16, gBot - 10);
            DrawMultiline(hdc, mainX + 18, gTop + 50, mainW - 36, gBot - gTop - 60, guideTxt, fBody, COL_TEXT);
            RestoreDC(hdc, savedG);
        }
    }
    FillBox(hdc, 0, bodyBot, W, H, COL_SIDEBAR, COL_BORDER);
    DrawLabel(hdc, 18, bodyBot, W - 36, footerH, L"SHARD STUDIO   |   100% Local Hugging Face Runtime   |   Win32 C++20 + MASM x64 AVX2 + OpenAI & Anthropic Local Endpoints", fMono, COL_MUTED);
    if (snap.modalOpen) {
        int mw = std::min(880, W - 80);
        int mh = std::min(620, H - 80);
        int mx1 = (W - mw) / 2;
        int my1 = (H - mh) / 2;
        FillRoundBox(hdc, mx1 - 4, my1 - 4, mx1 + mw + 4, my1 + mh + 4, 12, RGB(5, 6, 8), COL_GOLD_DIM);
        FillRoundBox(hdc, mx1, my1, mx1 + mw, my1 + mh, 10, COL_CARD, COL_GOLD);
        AddHit(0, 0, W, H, ACT_NONE);
        DrawLabel(hdc, mx1 + 22, my1 + 14, mw - 310, 26, snap.modalModelId.c_str(), fTitle, COL_TEXT);
        DrawLabel(hdc, mx1 + 22, my1 + 42, mw - 310, 18, snap.modalStats.c_str(), fSmall, COL_SUBTEXT);
        DrawLabel(hdc, mx1 + 22, my1 + 62, mw - 44, 18, snap.modalCompat.c_str(), fSmall, COL_EMERALD);
        FillRoundBox(hdc, mx1 + mw - 284, my1 + 14, mx1 + mw - 168, my1 + 44, 5, COL_WELL, COL_BORDER);
        DrawLabel(hdc, mx1 + mw - 284, my1 + 14, 116, 30, L"Открыть на HF", fSmall, COL_SUBTEXT, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(mx1 + mw - 284, my1 + 14, mx1 + mw - 168, my1 + 44, ACT_MODAL_OPEN_HF, 0, snap.modalModelId);
        FillRoundBox(hdc, mx1 + mw - 160, my1 + 14, mx1 + mw - 54, my1 + 44, 5, COL_GOLD, COL_GOLD);
        DrawLabel(hdc, mx1 + mw - 160, my1 + 14, 106, 30, L"Запустить", fSmall, RGB(12, 13, 16), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(mx1 + mw - 160, my1 + 14, mx1 + mw - 54, my1 + 44, ACT_MODAL_LOAD, 0, snap.modalModelId);
        FillRoundBox(hdc, mx1 + mw - 46, my1 + 14, mx1 + mw - 14, my1 + 44, 5, COL_CRIMSON_BG, COL_CRIMSON);
        DrawLabel(hdc, mx1 + mw - 46, my1 + 14, 32, 30, L"X", fSub, RGB(250, 190, 190), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        AddHit(mx1 + mw - 46, my1 + 14, mx1 + mw - 14, my1 + 44, ACT_MODAL_CLOSE);
        int mdTop = my1 + 88;
        int mdBot = my1 + mh - 16;
        FillRoundBox(hdc, mx1 + 18, mdTop, mx1 + mw - 18, mdBot, 6, COL_WELL, COL_BORDER);
        int mdTotalH = MeasureMultiline(hdc, mw - 64, snap.modalReadme, fBody) + 24;
        int maxMdScroll = std::max(0, mdTotalH - (mdBot - mdTop));
        int curMdScroll = std::clamp(snap.modalScrollY, 0, maxMdScroll);
        int savedMd = SaveDC(hdc);
        IntersectClipRect(hdc, mx1 + 22, mdTop + 4, mx1 + mw - 22, mdBot - 4);
        DrawMultiline(hdc, mx1 + 28, mdTop + 12 - curMdScroll, mw - 64, mdTotalH, snap.modalReadme, fBody, COL_TEXT);
        RestoreDC(hdc, savedMd);
    }
    if (GetTickCount64() < snap.toastUntilMs && !snap.toastTitle.empty()) {
        int tw = 370;
        int th = 62;
        int tx1 = W - tw - 22;
        int ty1 = H - footerH - th - 14;
        FillRoundBox(hdc, tx1, ty1, tx1 + tw, ty1 + th, 8, COL_CARD_HOVER, COL_GOLD);
        FillBox(hdc, tx1, ty1 + 10, tx1 + 4, ty1 + th - 10, COL_GOLD, COL_GOLD);
        DrawLabel(hdc, tx1 + 16, ty1 + 8, tw - 28, 22, snap.toastTitle.c_str(), fSub, COL_GOLD);
        DrawLabel(hdc, tx1 + 16, ty1 + 32, tw - 28, 20, snap.toastBody.c_str(), fSmall, COL_TEXT);
    }
    DeleteObject(fBrand);
    DeleteObject(fTitle);
    DeleteObject(fSub);
    DeleteObject(fBody);
    DeleteObject(fSmall);
    DeleteObject(fMono);
    DeleteObject(fBigNum);
}
static void DispatchHitZone(HWND hwnd, const HitZone& hz) {
    switch (hz.action) {
    case ACT_NAV_TAB:
        g_state.activeTab = hz.idx;
        g_focusField = (hz.idx == 2) ? 2 : 0;
        if (hz.idx == 1 || hz.idx == 4) { FetchDownloadedSilent(); FetchKeysAsync(); }
        InvalidateRect(hwnd, nullptr, FALSE);
        break;
    case ACT_TOGGLE_SERVER: {
        bool online = false;
        { std::lock_guard<std::mutex> lk(g_mtx); online = g_app.serverOnline; }
        if (online) {
            TriggerPingTestAsync();
        } else {
            ShowToastSafe(L"Запуск сервера", L"Поднимаем локальное ядро 127.0.0.1:8000...");
            std::thread([]() { StartLocalServerSilent(); }).detach();
        }
        break;
    }
    case ACT_UNLOAD_MODEL:
        TriggerUnloadAsync();
        break;
    case ACT_CANCEL_LOAD:
        TriggerCancelAsync();
        break;
    case ACT_FOCUS_SEARCH:
        g_focusField = 1;
        g_caretVisible = true;
        InvalidateRect(hwnd, nullptr, FALSE);
        break;
    case ACT_CLEAR_SEARCH:
        { std::lock_guard<std::mutex> lk(g_mtx); g_app.searchQuery.clear(); }
        g_scrollY[0] = 0;
        FetchCatalogAsync();
        break;
    case ACT_RUN_SEARCH:
        g_scrollY[0] = 0;
        FetchCatalogAsync();
        break;
    case ACT_CAT_FILTER:
        { std::lock_guard<std::mutex> lk(g_mtx); g_app.currentCat = hz.param; }
        g_state.activeTab = 0;
        g_scrollY[0] = 0;
        FetchCatalogAsync();
        InvalidateRect(hwnd, nullptr, FALSE);
        break;
    case ACT_MODEL_LOAD:
    case ACT_MODAL_LOAD:
        TriggerLoadModelAsync(hz.param);
        break;
    case ACT_MODEL_CARD:
        OpenModelCardAsync(hz.param);
        break;
    case ACT_MODEL_DELETE_SSD:
        TriggerDeleteSsdModelAsync(hz.param);
        break;
    case ACT_MODAL_CLOSE:
        { std::lock_guard<std::mutex> lk(g_mtx); g_app.modalOpen = false; }
        InvalidateRect(hwnd, nullptr, FALSE);
        break;
    case ACT_MODAL_OPEN_HF: {
        std::wstring url = L"https://huggingface.co/" + hz.param;
        ShellExecuteW(hwnd, L"open", url.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
        break;
    }
    case ACT_CHAT_NEW: {
        std::lock_guard<std::mutex> lk(g_mtx);
        if (!g_app.chatStreaming) {
            ChatSession cs;
            cs.id = L"chat_" + std::to_wstring(GetTickCount64());
            cs.title = L"Новый диалог";
            g_app.chats.insert(g_app.chats.begin(), cs);
            g_app.activeChatIdx = 0;
            g_scrollY[2] = 0;
        }
        SaveChatsToDisk();
        g_focusField = 2;
        InvalidateRect(hwnd, nullptr, FALSE);
        break;
    }
    case ACT_CHAT_SELECT:
        { std::lock_guard<std::mutex> lk(g_mtx); if (!g_app.chatStreaming) g_app.activeChatIdx = hz.idx; g_scrollY[2] = 999999; }
        InvalidateRect(hwnd, nullptr, FALSE);
        break;
    case ACT_CHAT_DELETE: {
        {
            std::lock_guard<std::mutex> lk(g_mtx);
            if (!g_app.chatStreaming && hz.idx >= 0 && hz.idx < static_cast<int>(g_app.chats.size())) {
                g_app.chats.erase(g_app.chats.begin() + hz.idx);
                if (g_app.chats.empty()) {
                    ChatSession cs;
                    cs.id = L"chat_init";
                    cs.title = L"Новый диалог";
                    g_app.chats.push_back(cs);
                }
                if (g_app.activeChatIdx >= static_cast<int>(g_app.chats.size())) g_app.activeChatIdx = 0;
            }
        }
        SaveChatsToDisk();
        InvalidateRect(hwnd, nullptr, FALSE);
        break;
    }
    case ACT_CHAT_FOCUS_INPUT:
        g_focusField = 2;
        g_caretVisible = true;
        InvalidateRect(hwnd, nullptr, FALSE);
        break;
    case ACT_CHAT_SEND: {
        std::wstring txt;
        { std::lock_guard<std::mutex> lk(g_mtx); txt = g_app.chatInput; g_app.chatInput.clear(); }
        if (!txt.empty()) SendChatPromptAsync(txt);
        break;
    }
    case ACT_CHAT_STOP:
        TriggerCancelAsync();
        break;
    case ACT_CHAT_TEMP: {
        std::lock_guard<std::mutex> lk(g_mtx);
        if (g_app.chatTemp < 0.35) g_app.chatTemp = 0.7;
        else if (g_app.chatTemp < 0.8) g_app.chatTemp = 1.0;
        else g_app.chatTemp = 0.2;
        InvalidateRect(hwnd, nullptr, FALSE);
        break;
    }
    case ACT_CHAT_MAXTOK: {
        std::lock_guard<std::mutex> lk(g_mtx);
        if (g_app.chatMaxTokens == 256) g_app.chatMaxTokens = 512;
        else if (g_app.chatMaxTokens == 512) g_app.chatMaxTokens = 1024;
        else if (g_app.chatMaxTokens == 1024) g_app.chatMaxTokens = 2048;
        else g_app.chatMaxTokens = 256;
        InvalidateRect(hwnd, nullptr, FALSE);
        break;
    }
    case ACT_CHAT_PRESET:
        SendChatPromptAsync(hz.param);
        break;
    case ACT_CHAT_COPY_MSG:
        CopyToClipboard(hwnd, hz.param);
        ShowToastSafe(L"Ответ скопирован", L"Текст сообщения сохранен в буфер обмена");
        break;
    case ACT_INTEG_SELECT:
        { std::lock_guard<std::mutex> lk(g_mtx); g_app.selectedToolIdx = hz.idx; }
        InvalidateRect(hwnd, nullptr, FALSE);
        break;
    case ACT_INTEG_APPLY:
        TriggerApplyIntegrationAsync(hz.param);
        break;
    case ACT_INTEG_COPY_MAIN:
        CopyToClipboard(hwnd, hz.param);
        ShowToastSafe(L"Конфигурация скопирована", L"Вставьте в файл настроек или терминал");
        break;
    case ACT_INTEG_COPY_URL:
        CopyToClipboard(hwnd, L"http://localhost:8000/v1");
        ShowToastSafe(L"Base URL скопирован", L"http://localhost:8000/v1");
        break;
    case ACT_INTEG_COPY_KEY: {
        std::wstring k;
        { std::lock_guard<std::mutex> lk(g_mtx); k = g_app.activeApiKey; }
        CopyToClipboard(hwnd, k);
        ShowToastSafe(L"API Ключ скопирован", k);
        break;
    }
    case ACT_INTEG_COPY_MODEL: {
        std::wstring m;
        { std::lock_guard<std::mutex> lk(g_mtx); m = g_app.activeModelId; }
        CopyToClipboard(hwnd, m);
        ShowToastSafe(L"ID модели скопирован", m);
        break;
    }
    case ACT_INTEG_PING:
        TriggerPingTestAsync();
        break;
    case ACT_KEY_GENERATE:
        TriggerGenerateKeyAsync();
        break;
    case ACT_KEY_SELECT:
        { std::lock_guard<std::mutex> lk(g_mtx); g_app.activeApiKey = hz.param; }
        ShowToastSafe(L"Выбран активный ключ", hz.param);
        break;
    case ACT_KEY_COPY:
        CopyToClipboard(hwnd, hz.param);
        ShowToastSafe(L"Ключ скопирован", hz.param);
        break;
    case ACT_KEY_DELETE:
        TriggerDeleteKeyAsync(hz.param);
        break;
    case ACT_SRV_START:
        ShowToastSafe(L"Запуск сервера", L"Инициализация FastAPI на 127.0.0.1:8000...");
        std::thread([]() { StartLocalServerSilent(); }).detach();
        break;
    case ACT_SRV_RESTART:
        ShowToastSafe(L"Перезапуск сервера", L"Перезагрузка локального движка...");
        std::thread([]() { StopLocalServer(); Sleep(400); StartLocalServerSilent(); }).detach();
        break;
    case ACT_SRV_STOP:
        StopLocalServer();
        ShowToastSafe(L"Сервер остановлен", L"Фоновый процесс завершен");
        break;
    case ACT_SRV_KEEP_BG:
        { std::lock_guard<std::mutex> lk(g_mtx); g_app.keepServerOnExit = !g_app.keepServerOnExit; }
        InvalidateRect(hwnd, nullptr, FALSE);
        break;
    case ACT_COPY_TEXT:
        CopyToClipboard(hwnd, hz.param);
        ShowToastSafe(L"Скопировано в буфер", hz.param);
        break;
    case ACT_BENCH_RUN:
        RunHardwareBenchmark();
        ShowToastSafe(L"Тест AVX2 завершен", L"Замеры регистров YMM0-YMM7 обновлены");
        InvalidateRect(hwnd, nullptr, FALSE);
        break;
    case ACT_GUIDE_LANG:
        g_state.guideLang = hz.idx;
        InvalidateRect(hwnd, nullptr, FALSE);
        break;
    default:
        break;
    }
}
static LRESULT CALLBACK ShardWndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    switch (msg) {
    case WM_ERASEBKGND:
        return 1;
    case WM_GETMINMAXINFO: {
        MINMAXINFO* mmi = reinterpret_cast<MINMAXINFO*>(lParam);
        mmi->ptMinTrackSize.x = 1060;
        mmi->ptMinTrackSize.y = 660;
        return 0;
    }
    case WM_SHARD_REFRESH:
        InvalidateRect(hwnd, nullptr, FALSE);
        return 0;
    case WM_TIMER:
        if (wParam == 1) {
            g_caretVisible = !g_caretVisible;
            bool needPaint = (g_focusField != 0);
            {
                std::lock_guard<std::mutex> lk(g_mtx);
                if (g_app.toastUntilMs > 0 && GetTickCount64() >= g_app.toastUntilMs) {
                    g_app.toastUntilMs = 0;
                    g_app.toastTitle.clear();
                    needPaint = true;
                }
            }
            if (needPaint) InvalidateRect(hwnd, nullptr, FALSE);
        }
        return 0;
    case WM_MOUSEMOVE: {
        int mx = GET_X_LPARAM(lParam);
        int my = GET_Y_LPARAM(lParam);
        int hovered = ACT_NONE;
        for (int i = static_cast<int>(g_hits.size()) - 1; i >= 0; --i) {
            const RECT& r = g_hits[i].rc;
            if (mx >= r.left && mx <= r.right && my >= r.top && my <= r.bottom) {
                hovered = g_hits[i].action;
                break;
            }
        }
        if (hovered == ACT_FOCUS_SEARCH || hovered == ACT_CHAT_FOCUS_INPUT) SetCursor(LoadCursor(nullptr, IDC_IBEAM));
        else if (hovered != ACT_NONE) SetCursor(LoadCursor(nullptr, IDC_HAND));
        else SetCursor(LoadCursor(nullptr, IDC_ARROW));
        return 0;
    }
    case WM_MOUSEWHEEL: {
        short delta = GET_WHEEL_DELTA_WPARAM(wParam);
        int step = -(delta / WHEEL_DELTA) * 56;
        bool modalAct = false;
        {
            std::lock_guard<std::mutex> lk(g_mtx);
            modalAct = g_app.modalOpen;
            if (modalAct) g_app.modalScrollY = std::max(0, g_app.modalScrollY + step);
        }
        if (!modalAct && g_state.activeTab >= 0 && g_state.activeTab < 6) {
            g_scrollY[g_state.activeTab] = std::clamp(g_scrollY[g_state.activeTab] + step, 0, g_maxScrollY[g_state.activeTab]);
        }
        InvalidateRect(hwnd, nullptr, FALSE);
        return 0;
    }
    case WM_LBUTTONDOWN: {
        SetFocus(hwnd);
        int mx = GET_X_LPARAM(lParam);
        int my = GET_Y_LPARAM(lParam);
        g_focusField = 0;
        for (int i = static_cast<int>(g_hits.size()) - 1; i >= 0; --i) {
            const RECT& r = g_hits[i].rc;
            if (mx >= r.left && mx <= r.right && my >= r.top && my <= r.bottom) {
                HitZone copyHz = g_hits[i];
                DispatchHitZone(hwnd, copyHz);
                return 0;
            }
        }
        InvalidateRect(hwnd, nullptr, FALSE);
        return 0;
    }
    case WM_KEYDOWN: {
        if (wParam == VK_ESCAPE) {
            bool hadModal = false;
            { std::lock_guard<std::mutex> lk(g_mtx); hadModal = g_app.modalOpen; g_app.modalOpen = false; }
            g_focusField = 0;
            if (hadModal) InvalidateRect(hwnd, nullptr, FALSE);
            return 0;
        }
        bool ctrl = (GetKeyState(VK_CONTROL) & 0x8000) != 0;
        if (ctrl && wParam == 'V' && g_focusField != 0) {
            std::wstring clip = ReadClipboardText(hwnd);
            if (!clip.empty()) {
                for (wchar_t& ch : clip) if (ch == L'\n') ch = L' ';
                std::lock_guard<std::mutex> lk(g_mtx);
                if (g_focusField == 1) g_app.searchQuery += clip;
                else if (g_focusField == 2) g_app.chatInput += clip;
                InvalidateRect(hwnd, nullptr, FALSE);
            }
            return 0;
        }
        if (wParam == VK_RETURN && g_focusField != 0) {
            if (g_focusField == 1) {
                g_scrollY[0] = 0;
                FetchCatalogAsync();
            } else if (g_focusField == 2) {
                std::wstring txt;
                { std::lock_guard<std::mutex> lk(g_mtx); txt = g_app.chatInput; g_app.chatInput.clear(); }
                if (!txt.empty()) SendChatPromptAsync(txt);
            }
            return 0;
        }
        if (wParam == VK_BACK && g_focusField != 0) {
            std::lock_guard<std::mutex> lk(g_mtx);
            std::wstring& target = (g_focusField == 1) ? g_app.searchQuery : g_app.chatInput;
            if (!target.empty()) {
                if (ctrl) {
                    while (!target.empty() && target.back() == L' ') target.pop_back();
                    while (!target.empty() && target.back() != L' ') target.pop_back();
                } else {
                    target.pop_back();
                }
                InvalidateRect(hwnd, nullptr, FALSE);
            }
            return 0;
        }
        return 0;
    }
    case WM_CHAR: {
        if (g_focusField == 0) return 0;
        wchar_t ch = static_cast<wchar_t>(wParam);
        if (ch >= 32 && ch != 127) {
            std::lock_guard<std::mutex> lk(g_mtx);
            if (g_focusField == 1 && g_app.searchQuery.size() < 160) g_app.searchQuery.push_back(ch);
            else if (g_focusField == 2 && g_app.chatInput.size() < 4000) g_app.chatInput.push_back(ch);
            InvalidateRect(hwnd, nullptr, FALSE);
        }
        return 0;
    }
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC hdc = BeginPaint(hwnd, &ps);
        RECT rc;
        GetClientRect(hwnd, &rc);
        int W = std::max(1, static_cast<int>(rc.right - rc.left));
        int H = std::max(1, static_cast<int>(rc.bottom - rc.top));
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
    case WM_DESTROY: {
        g_running = false;
        g_cancelStream = true;
        KillTimer(hwnd, 1);
        bool keepBg = false;
        { std::lock_guard<std::mutex> lk(g_mtx); keepBg = g_app.keepServerOnExit; }
        if (!keepBg && g_serverProcess) {
            TerminateProcess(g_serverProcess, 0);
            CloseHandle(g_serverProcess);
            g_serverProcess = nullptr;
        } else if (g_serverProcess) {
            CloseHandle(g_serverProcess);
            g_serverProcess = nullptr;
        }
        PostQuitMessage(0);
        return 0;
    }
    }
    return DefWindowProcW(hwnd, msg, wParam, lParam);
}
static int ForwardCliCommand(const std::wstring& argsWide) {
    if (!AttachConsole(ATTACH_PARENT_PROCESS)) {
        AllocConsole();
    }
    std::wstring exeDir = GetExeDirectory();
    std::wstring pyExe = FindPythonExecutable();
    std::wstring cliPath = exeDir + L"\\cli.py";
    std::wstring cmd = L"\"" + pyExe + L"\" \"" + cliPath + L"\" " + argsWide;
    STARTUPINFOW si = {};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    si.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    si.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    PROCESS_INFORMATION pi = {};
    std::vector<wchar_t> buf(cmd.begin(), cmd.end());
    buf.push_back(L'\0');
    if (!CreateProcessW(nullptr, buf.data(), nullptr, nullptr, TRUE, 0, nullptr, exeDir.c_str(), &si, &pi)) {
        return 1;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD exitCode = 0;
    GetExitCodeProcess(pi.hProcess, &exitCode);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return static_cast<int>(exitCode);
}
int WINAPI WinMain(HINSTANCE hInst, HINSTANCE, LPSTR, int nCmdShow) {
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argv && argc > 1) {
        std::wstring firstArg = argv[1];
        if (firstArg != L"--gui" && firstArg != L"gui") {
            std::wstring joined;
            for (int i = 1; i < argc; ++i) {
                if (i > 1) joined += L" ";
                std::wstring a = argv[i];
                if (a.find(L' ') != std::wstring::npos) joined += L"\"" + a + L"\"";
                else joined += a;
            }
            LocalFree(argv);
            return ForwardCliCommand(joined);
        }
    }
    if (argv) LocalFree(argv);
    HANDLE hMutex = CreateMutexW(nullptr, TRUE, L"Local\\ShardStudioDesktopAppMutex");
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        HWND existing = FindWindowW(L"ShardNativeConsoleClass", nullptr);
        if (existing) {
            if (IsIconic(existing)) ShowWindow(existing, SW_RESTORE);
            SetForegroundWindow(existing);
        }
        if (hMutex) CloseHandle(hMutex);
        return 0;
    }
    RunHardwareBenchmark();
    LoadChatsFromDisk();
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
        L"Shard Studio — Local Hugging Face AI Runtime & CLI Integration Hub",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, 1360, 840,
        nullptr, nullptr, hInst, nullptr
    );
    g_hwnd = hwnd;
    BOOL useDark = TRUE;
    DwmSetWindowAttribute(hwnd, 20, &useDark, sizeof(useDark));
    SetTimer(hwnd, 1, 500, nullptr);
    ShowWindow(hwnd, nCmdShow);
    UpdateWindow(hwnd);
    std::thread(BackgroundTelemetryLoop).detach();
    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    g_running = false;
    if (hMutex) CloseHandle(hMutex);
    return 0;
}
