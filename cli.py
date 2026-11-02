import os
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
    print("  SHARD // TRI-LANGUAGE LOCAL HUGGING FACE RUNNER")
    print("  Python FastAPI + C++20 Zero-Copy Paging + MASM x64 AVX2")
    print("=" * 64)
def cmd_help():
    print_banner()
    print("Usage: shard <command> [options]\n")
    print("Commands:")
    print("  start      Launch Shard server and web workspace")
    print("             Options: --host 0.0.0.0 --port 8000")
    print("  status     Show local GPU, VRAM, RAM, and native engine state")
    print("  list       Browse Hugging Face models and hardware tiers")
    print("             Options: --category [all|coding|reasoning|compact]")
    print("  run        Load a model and chat in terminal REPL")
    print("             Usage: shard run <model_id>")
    print("  test       Run native C++20 and MASM AVX2 benchmarks")
    print("  key        List or generate authentication API keys")
    print("             Options: --gen (create new key)")
    print("  help       Show this reference documentation")
    print("\nExamples:")
    print("  shard start --port 8000")
    print("  shard run Qwen/Qwen2.5-Coder-7B-Instruct")
    print("  shard status")
    print("  shard test")
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
def cmd_list(args):
    print_banner()
    cat = getattr(args, "category", "all") or "all"
    models = browse_models(category=cat)
    print(f"Available Hugging Face Models ({cat}):\n")
    print(f"{'MODEL ID':<42} {'COMPATIBILITY':<24} {'SPEED':<16} {'DOWNLOADS'}")
    print("-" * 95)
    for m in models:
        mid = m["id"]
        compat = m.get("compat_badge", "HYBRID")
        speed = m.get("speed_tier", "20 t/s")
        d = f"{m.get('downloads', 0):,}"
        print(f"{mid:<42} {compat:<24} {speed:<16} {d}")
def cmd_test():
    print_banner()
    print("Running Shard Native Core C++20 + MASM AVX2 SIMD Benchmark...")
    bridge = ShardNativeBridge()
    print(f"Kernel: {bridge.version}")
    iters = 100000
    us = bridge.benchmark_avx2(iters)
    if us > 0:
        ops_per_iter = 4096 * 2
        total_ops = ops_per_iter * iters
        sec = us / 1000000.0
        gflops = (total_ops / sec) / 1e9
        print(f"Iterations:       {iters:,}")
        print(f"Elapsed Time:     {us:,} us ({sec:.4f} s)")
        print(f"Throughput:       {gflops:.2f} GFLOPS (AVX2 SIMD)")
        print("Native Pipeline:  VALIDATED [OK]")
    else:
        print("Native Pipeline:  BENCHMARK FAILED [ERROR]")
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
def cmd_run(args):
    model_id = args.model if hasattr(args, "model") and args.model else "Qwen/Qwen2.5-Coder-7B-Instruct"
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
            res = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, headers=headers, timeout=60)
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
    sub.add_parser("status")
    p_list = sub.add_parser("list")
    p_list.add_argument("--category", default="all", help="Category filter")
    p_run = sub.add_parser("run")
    p_run.add_argument("model", nargs="?", default="Qwen/Qwen2.5-Coder-7B-Instruct", help="Model ID")
    sub.add_parser("test")
    p_key = sub.add_parser("key")
    p_key.add_argument("--gen", action="store_true", help="Generate key")
    sub.add_parser("help")
    if len(sys.argv) <= 1:
        cmd_help()
        sys.exit(0)
    args = parser.parse_args()
    if args.command == "start":
        cmd_start(args)
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
    else:
        cmd_help()
if __name__ == "__main__":
    main()