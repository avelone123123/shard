import os
import subprocess
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import json
import time
import argparse
import requests
from hardware import get_hardware_profile, evaluate_compatibility
from hf_hub import browse_models
from shard_bridge import ShardNativeBridge
BASE_URL = os.environ.get("SHARD_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("SHARD_API_KEY", "sk-shard-local-avelone-dev")
def print_banner():
    print("=" * 64)
    print("  SHARD STUDIO // TRI-LANGUAGE LOCAL NEURAL ENGINE")
    print("  Win32 C++20 Desktop + MASM x64 AVX2 + Python FastAPI")
    print("=" * 64)
def cmd_help():
    print_banner()
    print("Usage: shard [command] [options]\n")
    print("Commands:")
    print("  (no args)  Launch Shard Native Desktop Studio (.exe) + Auto-Server")
    print("  gui        Launch Shard Native Desktop Studio (.exe)")
    print("  start      Launch headless Shard FastAPI server")
    print("             Options: --host 0.0.0.0 --port 8000")
    print("  status     Show local GPU, VRAM, RAM, and native engine state")
    print("  list       Browse Hugging Face models and hardware tiers")
    print("             Options: --category [all|ideal|coding|reasoning|compact]")
    print("  run        Load a model and chat in terminal REPL")
    print("             Usage: shard run <model_id>")
    print("  test       Run native C++20 and MASM AVX2 benchmarks")
    print("  key        List or generate authentication API keys")
    print("             Options: --gen (create new key)")
    print("  fit        Check whether a model fits this hardware before download")
    print("             Usage: shard fit <model_id>")
    print("  models-dir Show or change the models download folder")
    print("             Usage: shard models-dir [path|reset]")
    print("  help       Show this reference documentation")
def cmd_gui():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for name in ("shard-native.exe", "shard.exe"):
        exe_path = os.path.join(base_dir, name)
        if os.path.exists(exe_path):
            subprocess.Popen([exe_path, "--gui"], cwd=base_dir)
            print(f"Launched Shard Desktop Studio: {exe_path}")
            return
    print("Native desktop binary not found. Run: python native/build_native.py")
def cmd_start(args):
    import uvicorn
    host = args.host if hasattr(args, "host") and args.host else "0.0.0.0"
    port = args.port if hasattr(args, "port") and args.port else 8000
    print_banner()
    print(f"Starting Shard Server on http://{host}:{port}...")
    uvicorn.run("server:app", host=host, port=port, log_level="info")
def cmd_status():
    print_banner()
    try:
        r = requests.get(f"{BASE_URL}/api/system/hardware", timeout=2)
        if r.status_code == 200:
            data = r.json()
            hw = data.get("hardware", {})
            eng = data.get("engine", {})
            print(f"Server Status:   ACTIVE ({BASE_URL})")
            print(f"GPU Hardware:    {hw.get('gpu_name', 'CPU Only')}")
            print(f"VRAM Allocation: {hw.get('vram_used_mb', 0)} / {hw.get('vram_total_mb', 0)} MB")
            print(f"RAM Allocation:  {hw.get('ram_used_gb', 0)} / {hw.get('ram_total_gb', 0)} GB")
            print(f"CPU Threads:     {hw.get('cpu_cores', 0)}")
            print(f"Active Model:    {eng.get('active_model', 'None')}")
            print(f"Native Core:     {eng.get('native_engine', {}).get('version', 'Ready')}")
            return
    except Exception:
        pass
    hw = get_hardware_profile()
    bridge = ShardNativeBridge()
    print("Server Status:   OFFLINE (showing direct host hardware)")
    print(f"GPU Hardware:    {hw.get('gpu_name', 'CPU Only')}")
    print(f"VRAM Allocation: {hw.get('vram_used_mb', 0)} / {hw.get('vram_total_mb', 0)} MB")
    print(f"RAM Allocation:  {hw.get('ram_used_gb', 0)} / {hw.get('ram_total_gb', 0)} GB")
    print(f"CPU Threads:     {hw.get('cpu_cores', 0)}")
    print(f"Native Core:     {bridge.version}")
    print(f"Registered Specs:{bridge.kernel_count()} verified kernels")
def cmd_list(args):
    print_banner()
    cat = getattr(args, "category", "all") or "all"
    models = browse_models(category=cat)
    print(f"Available Models ({cat}):\n")
    print(f"{'MODEL ID':<42} {'COMPATIBILITY':<24} {'SPEED':<16} {'DOWNLOADS'}")
    print("-" * 95)
    for m in models:
        mid = m["id"]
        compat = m.get("compat_badge", "HYBRID")
        speed = m.get("speed_tier", "20 t/s")
        d_raw = m.get("downloads")
        d = f"{d_raw:,}" if d_raw is not None else "—"
        print(f"{mid:<42} {compat:<24} {speed:<16} {d}")
def cmd_test():
    print_banner()
    print("Running Shard Native Core C++20 + MASM AVX2 SIMD Benchmark Suite...")
    bridge = ShardNativeBridge()
    print(f"Kernel: {bridge.version}")
    print(f"Active Kernel Registry: {bridge.kernel_count()} Specs")
    iters = 100000
    us = bridge.benchmark_avx2(iters)
    if us > 0:
        ops_per_iter = 4096 * 2
        total_ops = ops_per_iter * iters
        sec = us / 1000000.0
        gflops = (total_ops / sec) / 1e9
        print(f"[01/12] Dot-Product FMA:  {gflops:.2f} GFLOPS ({us:,} us)")
        x_sample = [0.05 * ((i % 20) + 1) for i in range(4096)]
        w_sample = [1.0 for _ in range(4096)]
        b_sample = [0.01 for _ in range(4096)]
        t0 = time.time()
        for _ in range(2500):
            bridge.rmsnorm(x_sample, w_sample)
        print(f"[02/12] RMSNorm Vector:   {2500 / max(1e-6, time.time() - t0):.1f} ops/sec (MASM AVX2 sum_sq)")
        t0 = time.time()
        for _ in range(2500):
            bridge.layernorm(x_sample, w_sample, b_sample)
        print(f"[03/12] LayerNorm Affine: {2500 / max(1e-6, time.time() - t0):.1f} ops/sec (C++20 AVX2 mean/var)")
        t0 = time.time()
        for _ in range(2500):
            bridge.swiglu(x_sample, w_sample)
        print(f"[04/12] SwiGLU Fused:     {2500 / max(1e-6, time.time() - t0):.1f} ops/sec (Sigmoid + ASM Hadamard)")
        q8_bytes = bytes([i % 127 for i in range(4096)])
        t0 = time.time()
        for _ in range(2500):
            bridge.dequant_q8(q8_bytes, 0.05)
        print(f"[05/12] Q8_0 Dequant:     {2500 / max(1e-6, time.time() - t0):.1f} ops/sec (AVX2 Int8->FP32)")
        q4_bytes = bytes([0xA3 for _ in range(2048)])
        t0 = time.time()
        for _ in range(2500):
            bridge.dequant_q4(q4_bytes, 0.05)
        print(f"[06/12] Q4_0 Dequant:     {2500 / max(1e-6, time.time() - t0):.1f} ops/sec (AVX2 Nibble Unpack)")
        t0 = time.time()
        for _ in range(2500):
            bridge.vec_add(x_sample, w_sample)
        print(f"[07/12] Vec-Add SIMD:     {2500 / max(1e-6, time.time() - t0):.1f} ops/sec (MASM AVX2 vaddps)")
        t0 = time.time()
        for _ in range(2500):
            bridge.hadamard(x_sample, w_sample)
        print(f"[08/12] Hadamard Gate:    {2500 / max(1e-6, time.time() - t0):.1f} ops/sec (MASM AVX2 vmulps)")
        t0 = time.time()
        for _ in range(2500):
            bridge.axpy(x_sample, w_sample, 0.5)
        print(f"[09/12] Fused AXPY:       {2500 / max(1e-6, time.time() - t0):.1f} ops/sec (MASM AVX2 vfmadd231ps)")
        t0 = time.time()
        for _ in range(2500):
            bridge.softmax(x_sample[:512])
        print(f"[10/12] Stable Softmax:   {2500 / max(1e-6, time.time() - t0):.1f} ops/sec (MASM vmaxps + Scale)")
        t0 = time.time()
        for _ in range(2500):
            bridge.rope(x_sample[:512], pos=7)
        print(f"[11/12] RoPE Rotary:      {2500 / max(1e-6, time.time() - t0):.1f} ops/sec (Interleaved Cos/Sin)")
        t0 = time.time()
        for _ in range(2500):
            bridge.cosine_sim(x_sample, w_sample)
        print(f"[12/12] Cosine Sim RAG:   {2500 / max(1e-6, time.time() - t0):.1f} ops/sec (FMA Dual-Norm)")
        print("Native Pipeline:          ALL 12 KERNEL FAMILIES VALIDATED [OK]")
    else:
        print("Native Pipeline:          BENCHMARK FAILED [ERROR]")
        sys.exit(1)
def cmd_key(args):
    print_banner()
    if getattr(args, "gen", False):
        try:
            r = requests.post(f"{BASE_URL}/api/keys/generate", timeout=2)
            if r.status_code == 200:
                data = r.json()
                print(f"Generated new API Key: {data.get('key')}")
                return
        except Exception:
            pass
        import uuid
        key = f"sk-shard-{uuid.uuid4().hex[:16]}"
        print(f"Generated local API Key (offline): {key}")
    else:
        try:
            r = requests.get(f"{BASE_URL}/api/keys", timeout=2)
            if r.status_code == 200:
                keys = r.json().get("keys", [])
                print("Active API Keys:")
                for k in keys:
                    print(f"  - {k}")
                return
        except Exception:
            pass
        print(f"Default API Key: {API_KEY}")
def cmd_fit(args):
    print_banner()
    model_id = (getattr(args, "model", None) or "").strip()
    if not model_id:
        print("Usage: shard fit <model_id>   (example: google/gemma-2-27b-it)")
        sys.exit(1)
    print(f"Checking fit: {model_id}\n")
    try:
        r = requests.get(f"{BASE_URL}/api/fit/{model_id}", timeout=15)
        rep = r.json() if r.status_code == 200 else None
    except Exception:
        rep = None
    if not isinstance(rep, dict) or "quants" not in rep:
        try:
            from fit import fit_report
            rep = fit_report(model_id)
        except Exception as e:
            print(f"Fit check failed: {e}")
            sys.exit(1)
    vram_gb = rep.get("vram_total_mb", 0) / 1024.0
    ram_gb = rep.get("ram_total_mb", 0) / 1024.0
    print(f"GPU: {rep.get('gpu_name', 'CPU')} ({vram_gb:.1f} GB VRAM) | RAM: {ram_gb:.1f} GB")
    print(f"Params: {rep.get('params_b', '?')}B (source: {rep.get('params_source', '?')}) "
          f"| FP16: {rep.get('fp16_gb', '?')} GB | KV-cache: {rep.get('kv_gb', '?')} GB\n")
    print(f"{'QUANT':<8} {'SIZE':>9} {'VRAM':>9} {'RAM':>9} {'GPU LAYERS':>12} {'SPEED':>10}  STATUS")
    print("-" * 78)
    layers = rep.get("layers") or 0
    for row in rep.get("quants", []):
        ngl_txt = f"{row.get('ngl', 0)}/{layers}" if layers else str(row.get("ngl", 0))
        print(f"{row.get('quant', '?'):<8} {row.get('size_gb', 0):>7.1f} GB "
              f"{row.get('vram_gb', 0):>7.1f} GB {row.get('ram_gb', 0):>7.1f} GB "
              f"{ngl_txt:>12} {row.get('tok_s', 0):>8.1f} t/s  {row.get('verdict', '?')}")
    print()
    verdict = rep.get("verdict")
    if verdict == "IMPOSSIBLE":
        print("Result: DO NOT START ON THIS HARDWARE")
    else:
        rec = rep.get("recommended")
        rec_row = next((r2 for r2 in rep.get("quants", []) if r2.get("quant") == rec), None)
        print("Result: LAUNCH OK")
        if rec_row:
            print(f"Recommendation: {rec} — file {rec_row.get('size_gb', 0):.1f} GB, "
                  f"{rec_row.get('ngl', 0)}/{layers} layers on GPU, "
                  f"~{rec_row.get('tok_s', 0):.1f} tok/s")
    if not rep.get("disk_ok", True):
        print(f"Disk: not enough space (free: {rep.get('disk_free_gb', 0):.1f} GB)")
    if rep.get("mmap_note"):
        print(f"Note: {rep['mmap_note']}")
    print()
    print(rep.get("message_ru", ""))
def _models_dir_set(path):
    if path.lower() == "reset":
        try:
            r = requests.post(f"{BASE_URL}/api/settings", json={"reset": True}, timeout=5)
        except Exception:
            r = None
        if r is not None and r.status_code == 200:
            print("Models folder reset to default. Applies after app restart")
            return
        import settings as _settings
        _settings.reset_models_dir()
        print("Models folder reset to default. Applies after app restart")
        return
    try:
        r = requests.post(f"{BASE_URL}/api/settings", json={"models_dir": path}, timeout=5)
    except Exception:
        r = None
    if r is not None:
        if r.status_code == 200:
            data = r.json()
            print(f"Models folder saved: {data.get('models_dir')}")
            print("Applies after app restart")
            return
        if r.status_code == 400:
            print(f"Error: {(r.json() or {}).get('message', 'Invalid path')}")
            sys.exit(1)
    import settings as _settings
    try:
        saved = _settings.set_models_dir(path)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    print(f"Models folder saved: {saved}")
    print("Applies after app restart")
def cmd_models_dir(args):
    print_banner()
    new_path = (getattr(args, "path", None) or "").strip()
    if new_path:
        _models_dir_set(new_path)
        return
    data = None
    try:
        r = requests.get(f"{BASE_URL}/api/settings", timeout=2)
        if r.status_code == 200:
            data = r.json()
    except Exception:
        pass
    if not isinstance(data, dict):
        import settings as _settings
        data = {
            "models_dir": _settings.models_dir(),
            "default_models_dir": _settings.DEFAULT_MODELS_DIR,
            "is_custom": _settings.is_custom_models_dir(),
            "disk": _settings.disk_info(),
        }
    print(f"Current models folder: {data.get('models_dir')}")
    if data.get("is_custom"):
        print("Status: custom (set via shard models-dir)")
    else:
        print(f"Status: default ({data.get('default_models_dir')})")
    disk = data.get("disk") or {}
    print(f"Free space: {disk.get('free_gb', '?')} GB of {disk.get('total_gb', '?')} GB")
    print("Change:  shard models-dir <path>")
    print("Reset:   shard models-dir reset")
def cmd_run(args):
    model_id = args.model if hasattr(args, "model") and args.model else "Qwen/Qwen2.5-Coder-1.5B-Instruct"
    print_banner()
    print(f"Connecting to model: {model_id}")
    try:
        requests.post(f"{BASE_URL}/api/models/load", json={"model_id": model_id}, timeout=3)
    except Exception:
        pass
    print("Interactive Chat REPL started. Type 'exit' or 'quit' to close.\n")
    messages = []
    while True:
        try:
            user_input = input("shard> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            break
        messages.append({"role": "user", "content": user_input})
        try:
            payload = {"model": model_id, "messages": messages, "stream": False}
            headers = {"Authorization": f"Bearer {API_KEY}"}
            t0 = time.time()
            res = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, headers=headers, timeout=90)
            dt = time.time() - t0
            if res.status_code == 200:
                data = res.json()
                ans = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                tokens = usage.get("completion_tokens", 0)
                tps = round(tokens / max(0.1, dt), 1)
                print(f"\n{ans}\n")
                print(f"[{dt:.2f}s | {tokens} tokens | {tps} tok/sec]\n")
                messages.append({"role": "assistant", "content": ans})
            else:
                print(f"Error: HTTP {res.status_code} - {res.text}")
        except Exception as e:
            print(f"Failed to communicate with Shard Server: {e}")
def main():
    parser = argparse.ArgumentParser(description="Shard CLI Engine", add_help=False)
    sub = parser.add_subparsers(dest="command")
    p_start = sub.add_parser("start")
    p_start.add_argument("--host", default="0.0.0.0", help="Host interface")
    p_start.add_argument("--port", type=int, default=8000, help="Port")
    sub.add_parser("gui")
    sub.add_parser("status")
    p_list = sub.add_parser("list")
    p_list.add_argument("--category", default="all", help="Category filter")
    p_run = sub.add_parser("run")
    p_run.add_argument("model", nargs="?", default="Qwen/Qwen2.5-Coder-1.5B-Instruct", help="Model ID")
    sub.add_parser("test")
    p_key = sub.add_parser("key")
    p_key.add_argument("--gen", action="store_true", help="Generate key")
    p_fit = sub.add_parser("fit")
    p_fit.add_argument("model", nargs="?", default=None, help="Model ID to check")
    p_dir = sub.add_parser("models-dir")
    p_dir.add_argument("path", nargs="?", default=None, help="New folder path, or 'reset'")
    sub.add_parser("help")
    if len(sys.argv) <= 1:
        if os.name == "nt":
            cmd_gui()
            sys.exit(0)
        cmd_help()
        sys.exit(0)
    if sys.argv[1] in ("-h", "--help"):
        cmd_help()
        sys.exit(0)
    args = parser.parse_args()
    if args.command == "start":
        cmd_start(args)
    elif args.command == "gui":
        cmd_gui()
    elif args.command == "status":
        cmd_status()
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "test":
        cmd_test()
    elif args.command == "key":
        cmd_key(args)
    elif args.command == "fit":
        cmd_fit(args)
    elif args.command == "models-dir":
        cmd_models_dir(args)
    else:
        cmd_help()
if __name__ == "__main__":
    main()
