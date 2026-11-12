import argparse
import json
import math
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import uuid
from . import __app_name__, __version__, paths, process
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
_BANNER_LINES = (
    r"  ____  _   _    _    ____  ____",
    r" / ___|| | | |  / \  |  _ \|  _ \\",
    r" \___ \| |_| | / _ \ | |_) | | | |",
    r"  ___) |  _  |/ ___ \|  _ <| |_| |",
    r" |____/|_| |_/_/   \_\_| \_\____/",
)
def _base_url():
    env_url = os.environ.get("SHARD_BASE_URL")
    if env_url:
        return env_url.rstrip("/")
    info = process.running_status()
    if info and info.get("port"):
        host = process.probe_host_for_bind(info.get("host"))
        return f"http://{host}:{info['port']}"
    port = int(os.environ.get("SHARD_PORT", "8000"))
    return f"http://127.0.0.1:{port}"
def _default_api_key():
    env_key = os.environ.get("SHARD_API_KEY")
    if env_key:
        return env_key.strip()
    kf = paths.keys_file()
    if os.path.isfile(kf):
        try:
            with open(kf, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return str(data[0])
        except Exception:
            pass
    return "sk-shard-local"
def print_banner():
    for idx, line in enumerate(_BANNER_LINES):
        clean_line = line.replace("\\\\", "\\")
        if idx == len(_BANNER_LINES) - 1:
            print(f"{clean_line}  v{__version__}")
        else:
            print(clean_line)
    print("  Distributed Local LLM Inference Engine")
    print("=" * 64)
def cmd_help():
    print_banner()
    print("Usage: shard <command> [options]\n")
    print("Server & Lifecycle:")
    print("  start              Start SHARD server and web dashboard")
    print("                     Options: --host <ip> --port <n> --daemon (-d) --no-browser")
    print("  stop               Stop background SHARD server safely")
    print("  restart            Restart SHARD server (--foreground for attached mode)")
    print("  status             Show live server, hardware, model, and LAN status")
    print("  gui                Launch native desktop UI if installed (Windows)")
    print("\nModels & Inference:")
    print("  list               Browse models and hardware compatibility")
    print("                     Options: --category [all|ideal|coding|reasoning|compact]")
    print("  fit <model_id>     Estimate VRAM/RAM fit and GGUF quant plan before download")
    print("                     Options: --ctx <tokens> --no-fetch")
    print("  run [model_id]     Load model and open interactive terminal chat REPL")
    print("  models-dir [path]  Show, set, or reset the models download directory")
    print("  key                List or generate local API keys (--gen)")
    print("\nLAN Compute Pool:")
    print("  lan status         Show LAN mode, host IPs, active members, and peers")
    print("  lan create [name]  Create a distributed LAN compute network (--open, --model ID)")
    print("  lan join <ip> [code] [--share] [--port N]")
    print("                     Join an existing LAN server (--share contributes VRAM/RAM)")
    print("  lan model <id>     Set target model for the LAN pool")
    print("  lan off            Leave LAN network or stop hosting")
    print("\nDiagnostics & Maintenance:")
    print("  doctor             Run full environment, dependency, GPU, network, and disk checks")
    print("  test               Verify tensor math kernels and SIMD/fallback benchmarks")
    print("                     Options: --strict-native")
    print("  update             Check PyPI for newer SHARD releases")
    print("  version            Show SHARD version, Python runtime, OS, and directories")
    print("  help               Show this help reference (--help, -h)")
    print("\nExamples:")
    print("  shard doctor")
    print("  shard start --daemon")
    print("  shard fit Qwen/Qwen2.5-Coder-7B-Instruct")
    print("  shard lan create --open")
    print("  shard lan join <SERVER_IP> --share")
def cmd_version():
    print(f"shard-engine {__version__}")
    print(f"Python:   {platform.python_version()} ({sys.executable})")
    print(f"Platform: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Package:  {os.path.dirname(os.path.abspath(__file__))}")
    print(f"Data dir: {paths.data_dir()}")
    print(f"Config:   {paths.config_dir()}")
    print(f"Models:   {paths.default_models_dir()}")
def cmd_gui():
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        pkg_dir,
        os.path.dirname(pkg_dir),
        paths.runtime_dir(),
    ]
    for base in candidates:
        for name in ("shard-native.exe", "shard.exe"):
            exe_path = os.path.join(base, name)
            if os.path.isfile(exe_path):
                subprocess.Popen([exe_path, "--gui"], cwd=base)
                print(f"Launched Shard Desktop Studio: {exe_path}")
                return
    print("Native desktop binary (shard-native.exe) is not present in this installation.")
    print("Start the Web Dashboard instead with: shard start")
def _print_start_urls(host, port):
    from . import lan
    view_host = process.probe_host_for_bind(host)
    print(f"Dashboard: http://{view_host}:{port}")
    if host in ("0.0.0.0", "::", "*"):
        ips = lan.local_ips()
        for ip in ips:
            print(f"LAN URL:   http://{ip}:{port}")
def cmd_start(args):
    host = getattr(args, "host", None) or os.environ.get("SHARD_HOST", "0.0.0.0")
    port = getattr(args, "port", None) or int(os.environ.get("SHARD_PORT", "8000"))
    daemon = bool(getattr(args, "daemon", False))
    no_browser = bool(getattr(args, "no_browser", False))
    print_banner()
    if daemon:
        ok, res = process.start_background(host=host, port=port, no_browser=True)
        if not ok:
            print(f"[ERROR] {res.get('error', 'Failed to start SHARD.')}")
            if res.get("log_tail"):
                print(f"Log tail ({res.get('log_file')}):\n{res['log_tail']}")
            sys.exit(1)
        print(f"SHARD started in background (PID {res['pid']}).")
        _print_start_urls(host, port)
        print(f"Log file:  {res['log_file']}")
        print("Stop with: shard stop")
        return
    existing = process.running_status()
    if existing:
        print(f"[ERROR] SHARD is already running (PID {existing['pid']}, port {existing['port']}).")
        print("Use 'shard stop' or specify a different '--port'.")
        sys.exit(1)
    if process.is_port_in_use(host, port):
        print(f"[ERROR] Port {port} is already in use. Stop the existing service or use '--port {int(port) + 1}'.")
        sys.exit(1)
    print(f"Starting SHARD server on http://{host}:{port}...")
    _print_start_urls(host, port)
    try:
        from . import main as shard_main
        shard_main.main(host=host, port=port, no_browser=no_browser)
    except KeyboardInterrupt:
        print("\nSHARD server stopped.")
    except OSError as e:
        print(f"[ERROR] Failed to bind {host}:{port}: {e}")
        sys.exit(1)
def cmd_stop():
    print_banner()
    ok, msg = process.stop_server()
    print(msg)
    if not ok:
        info_url = _base_url()
        try:
            import requests
            r = requests.get(f"{info_url}/v1/ping", timeout=1)
            if r.status_code == 200:
                print(f"Note: a SHARD server is still responding on {info_url} (likely started manually in foreground).")
        except Exception:
            pass
        sys.exit(1)
def cmd_restart(args):
    print_banner()
    prev = process.read_pid_info()
    host = getattr(args, "host", None) or (prev.get("host") if prev else None) or os.environ.get("SHARD_HOST", "0.0.0.0")
    port = getattr(args, "port", None) or (prev.get("port") if prev else None) or int(os.environ.get("SHARD_PORT", "8000"))
    foreground = bool(getattr(args, "foreground", False))
    no_browser = bool(getattr(args, "no_browser", False))
    ok, msg = process.stop_server()
    if ok:
        print(msg)
        time.sleep(0.4)
    if foreground:
        if process.is_port_in_use(host, port):
            print(f"[ERROR] Port {port} is still busy.")
            sys.exit(1)
        from . import main as shard_main
        shard_main.main(host=host, port=port, no_browser=no_browser)
        return
    started, res = process.start_background(host=host, port=port, no_browser=True)
    if not started:
        print(f"[ERROR] {res.get('error', 'Failed to restart SHARD.')}")
        if res.get("log_tail"):
            print(res["log_tail"])
        sys.exit(1)
    print(f"SHARD restarted in background (PID {res['pid']}).")
    _print_start_urls(host, port)
    print(f"Log file:  {res['log_file']}")
def cmd_status():
    print_banner()
    base = _base_url()
    pid_info = process.running_status()
    online = False
    try:
        import requests
        r = requests.get(f"{base}/api/system/hardware", timeout=2)
        if r.status_code == 200:
            data = r.json()
            hw = data.get("hardware", {})
            eng = data.get("engine", {})
            print(f"Server Status:   ONLINE ({base})")
            if pid_info:
                print(f"Managed PID:     {pid_info['pid']}")
            print(f"GPU Hardware:    {hw.get('gpu_name', 'CPU Only')}")
            print(f"VRAM Allocation: {hw.get('vram_used_mb', 0)} / {hw.get('vram_total_mb', 0)} MB")
            print(f"RAM Allocation:  {hw.get('ram_used_gb', 0)} / {hw.get('ram_total_gb', 0)} GB")
            print(f"CPU Threads:     {hw.get('cpu_cores', 0)}")
            print(f"Active Model:    {eng.get('active_model', 'None')}")
            print(f"Native Core:     {eng.get('native_engine', {}).get('version', 'Ready')}")
            online = True
    except Exception:
        pass
    if not online:
        from .hardware import get_hardware_profile
        from .shard_bridge import ShardNativeBridge
        try:
            hw = get_hardware_profile()
        except Exception as e:
            hw = {"gpu_name": f"Unavailable ({e})", "vram_used_mb": 0, "vram_total_mb": 0, "ram_used_gb": 0, "ram_total_gb": 0, "cpu_cores": os.cpu_count() or 0}
        bridge = ShardNativeBridge()
        print(f"Server Status:   OFFLINE ({base})")
        print(f"GPU Hardware:    {hw.get('gpu_name', 'CPU Only')}")
        print(f"VRAM Allocation: {hw.get('vram_used_mb', 0)} / {hw.get('vram_total_mb', 0)} MB")
        print(f"RAM Allocation:  {hw.get('ram_used_gb', 0)} / {hw.get('ram_total_gb', 0)} GB")
        print(f"CPU Threads:     {hw.get('cpu_cores', 0)}")
        print(f"Native Core:     {bridge.version}")
    try:
        from . import lan
        ips = lan.local_ips()
        print(f"LAN IPv4(s):     {', '.join(ips) if ips else 'None detected'}")
    except Exception:
        pass
    if not online:
        sys.exit(1)
def _dep_version(mod_name):
    import importlib.metadata
    import importlib.util
    if importlib.util.find_spec(mod_name) is None:
        return None
    try:
        pkg_map = {"huggingface_hub": "huggingface-hub"}
        dist = pkg_map.get(mod_name, mod_name)
        return importlib.metadata.version(dist)
    except Exception:
        return "installed"
def cmd_doctor():
    print_banner()
    print("SHARD Environment Diagnostics\n")
    fails = 0
    warns = 0
    def report(level, label, detail):
        nonlocal fails, warns
        if level == "FAIL":
            fails += 1
        elif level == "WARN":
            warns += 1
        print(f"  [{level:<4}] {label:<24} {detail}")
    py_ver = sys.version_info
    if py_ver >= (3, 8):
        report("PASS", "Python", f"{platform.python_version()} ({platform.machine()})")
    else:
        report("FAIL", "Python", f"{platform.python_version()} (requires >= 3.8)")
    report("PASS", "SHARD Package", f"v{__version__} at {os.path.dirname(os.path.abspath(__file__))}")
    for asset in ("dashboard.html", "engine_optimizations.json"):
        if paths.package_resource_exists(asset):
            report("PASS", f"Asset {asset}", "packaged and readable")
        else:
            report("FAIL", f"Asset {asset}", "missing from package directory")
    print("\nCore Dependencies:")
    for dep in ("fastapi", "uvicorn", "requests", "psutil", "pydantic"):
        ver = _dep_version(dep)
        if ver:
            report("PASS", dep, ver)
        else:
            report("FAIL", dep, "missing (run: pip install shard-engine)")
    print("\nOptional ML & Runtime Acceleration:")
    missing_ml = []
    for dep in ("torch", "transformers", "accelerate", "safetensors", "huggingface_hub", "tqdm"):
        ver = _dep_version(dep)
        if ver:
            extra = ""
            if dep == "torch":
                try:
                    import torch
                    extra = " (CUDA ready)" if torch.cuda.is_available() else " (CPU only)"
                except Exception:
                    pass
            report("PASS", dep, f"{ver}{extra}")
        else:
            missing_ml.append(dep)
            report("WARN", dep, "not installed (optional, GGUF still works)")
    if missing_ml:
        print('         Tip: install PyTorch/HF extras with: pip install "shard-engine[full]"')
    from .shard_bridge import ShardNativeBridge
    bridge = ShardNativeBridge()
    if bridge.is_native_active:
        report("PASS", "Native SIMD Core", f"{bridge.version} ({bridge.kernel_count()} kernels, {bridge.dll_path})")
    else:
        report("WARN", "Native SIMD Core", "not built; pure-Python fallback active (optional)")
    from . import gguf_backend, lan
    llama_srv = gguf_backend.find_llama_server()
    if llama_srv:
        report("PASS", "llama-server", llama_srv)
    else:
        report("WARN", "llama-server", "not cached yet (auto-downloads on first GGUF run)")
    rpc_srv = lan.find_rpc_server()
    if rpc_srv:
        report("PASS", "ggml-rpc-server", rpc_srv)
    else:
        report("INFO", "ggml-rpc-server", "not cached yet (needed only for LAN --share workers)")
    print("\nHardware & Network:")
    try:
        from .hardware import get_hardware_profile
        hw = get_hardware_profile(force_refresh=True)
        report("PASS", "CPU / RAM", f"{hw.get('cpu_cores')} threads, {hw.get('ram_total_gb')} GB RAM")
        vram_t = hw.get("vram_total_mb", 0)
        if vram_t > 0:
            report("PASS", "GPU / VRAM", f"{hw.get('gpu_name')} ({vram_t} MB total, {hw.get('vram_free_mb')} MB free)")
        else:
            report("WARN", "GPU / VRAM", f"{hw.get('gpu_name', 'CPU Only')} (no NVIDIA CUDA GPU detected)")
    except Exception as e:
        report("WARN", "Hardware Probe", f"partial ({e})")
    ips = lan.local_ips()
    if ips:
        report("PASS", "LAN IPv4", ", ".join(ips))
    else:
        report("WARN", "LAN IPv4", "no non-loopback IPv4 interface active")
    default_port = int(os.environ.get("SHARD_PORT", "8000"))
    managed = process.running_status()
    if managed:
        report("PASS", f"Port {default_port}", f"SHARD running (PID {managed['pid']} on port {managed['port']})")
    elif process.is_port_in_use("127.0.0.1", default_port):
        report("WARN", f"Port {default_port}", "already in use by another process")
    else:
        report("PASS", f"Port {default_port}", "available")
    print("\nUser Directories:")
    for name, dpath in paths.all_dirs().items():
        if name in ("data_dir", "config_dir", "logs_dir", "run_dir", "cache_dir"):
            try:
                os.makedirs(dpath, exist_ok=True)
                probe = os.path.join(dpath, ".shard_doctor_probe")
                with open(probe, "w", encoding="utf-8") as pf:
                    pf.write("ok")
                os.remove(probe)
                report("PASS", name, f"{dpath} (writable)")
            except Exception as e:
                report("FAIL", name, f"{dpath} (not writable: {e})")
        else:
            report("INFO", name, dpath)
    from . import settings
    dinfo = settings.disk_info()
    if dinfo:
        lvl = "WARN" if dinfo["free_gb"] < 5 else "PASS"
        report(lvl, "Models Disk", f"{dinfo['free_gb']} GB free of {dinfo['total_gb']} GB")
    print("\n" + "-" * 64)
    print(f"Doctor summary: {fails} error(s), {warns} warning(s).")
    if fails > 0:
        sys.exit(1)
def _parse_ver_tuple(ver):
    parts = []
    for tok in str(ver or "0").split("."):
        num = ""
        for ch in tok:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts)
def cmd_update(args):
    print_banner()
    print(f"Installed version: {__app_name__} {__version__}")
    check_only = bool(getattr(args, "check", False))
    try:
        import requests
        r = requests.get(f"https://pypi.org/pypi/{__app_name__}/json", timeout=6)
    except Exception as e:
        print(f"Unable to reach PyPI: {e}")
        print("Upgrade manually when online with: pip install --upgrade shard-engine")
        sys.exit(1)
    if r.status_code == 404:
        print(f"{__app_name__} is not published on PyPI yet (HTTP 404).")
        print("Local or editable installations can be updated from Git:")
        print("  pip install --upgrade git+https://github.com/avelone123123/shard.git")
        return
    if r.status_code != 200:
        print(f"PyPI returned HTTP {r.status_code}.")
        sys.exit(1)
    latest = (r.json().get("info") or {}).get("version") or ""
    if not latest:
        print("Could not determine latest version from PyPI metadata.")
        return
    if _parse_ver_tuple(latest) > _parse_ver_tuple(__version__):
        print(f"Update available: {__version__} -> {latest}")
        if check_only:
            print("Run: pip install --upgrade shard-engine")
            return
        print("Upgrading via pip (user config, keys, and models will remain untouched)...")
        proc = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", __app_name__])
        sys.exit(proc.returncode)
    print(f"SHARD is up to date ({__version__}).")
def cmd_list(args):
    from .hf_hub import browse_models
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
        d = f"{d_raw:,}" if d_raw is not None else "-"
        print(f"{mid:<42} {compat:<24} {speed:<16} {d}")
def cmd_test(args=None):
    from .shard_bridge import ShardNativeBridge
    print_banner()
    strict_native = bool(getattr(args, "strict_native", False))
    bridge = ShardNativeBridge()
    mode_tag = "NATIVE C++20 / AVX2" if bridge.is_native_active else "PYTHON FALLBACK"
    print(f"Backend Mode:   {mode_tag}")
    print(f"Kernel Version: {bridge.version}")
    if bridge.is_native_active:
        print(f"Loaded Binary:  {bridge.dll_path} ({bridge.kernel_count()} kernels)")
    x_sample = [0.05 * ((i % 20) + 1) for i in range(1024)]
    w_sample = [1.0 for _ in range(1024)]
    b_sample = [0.01 for _ in range(1024)]
    dot = bridge.dot_product(x_sample, w_sample)
    rms = bridge.rmsnorm(x_sample, w_sample)
    ln = bridge.layernorm(x_sample, w_sample, b_sample)
    sg = bridge.swiglu(x_sample, w_sample)
    va = bridge.vec_add(x_sample, w_sample)
    hm = bridge.hadamard(x_sample, w_sample)
    ax = bridge.axpy(x_sample, w_sample, 0.5)
    sm = bridge.softmax(x_sample[:128])
    rp = bridge.rope(x_sample[:128], pos=7)
    cs = bridge.cosine_sim(x_sample, w_sample)
    dq8 = bridge.dequant_q8(bytes([i % 127 for i in range(128)]), 0.05)
    dq4 = bridge.dequant_q4(bytes([0xA3 for _ in range(64)]), 0.05)
    assert len(rms) == 1024 and len(ln) == 1024 and len(sg) == 1024
    assert len(va) == 1024 and len(hm) == 1024 and len(ax) == 1024
    assert abs(sum(sm) - 1.0) < 1e-4 and len(rp) == 128
    assert -1.01 <= cs <= 1.01 and len(dq8) == 128 and len(dq4) == 128 and math.isfinite(dot)
    print("[PASS] Verified 12 math kernel APIs (accuracy & shape assertions passed)")
    if bridge.is_native_active:
        iters = 50000
        us = bridge.benchmark_avx2(iters)
        if us > 0:
            sec = us / 1000000.0
            gflops = ((4096 * 2 * iters) / sec) / 1e9
            print(f"[PASS] AVX2 Benchmark: {gflops:.2f} GFLOPS ({us:,} us for {iters:,} iters)")
        else:
            print("[FAIL] Native benchmark failed")
            sys.exit(1)
    else:
        t0 = time.time()
        for _ in range(250):
            bridge.dot_product(x_sample, w_sample)
            bridge.softmax(x_sample[:128])
        dt = max(1e-6, time.time() - t0)
        print(f"[INFO] Pure-Python fallback throughput: {250 / dt:.1f} batches/sec")
        print("       Optional native acceleration can be compiled via: python native/build_native.py")
        if strict_native:
            sys.exit(1)
def _read_keys_offline():
    kf = paths.keys_file()
    if os.path.isfile(kf):
        try:
            with open(kf, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    return []
def _write_keys_offline(keys):
    kf = paths.keys_file()
    os.makedirs(os.path.dirname(kf), exist_ok=True)
    with open(kf, "w", encoding="utf-8") as f:
        json.dump(keys, f, indent=2, ensure_ascii=False)
def cmd_key(args):
    print_banner()
    base = _base_url()
    if getattr(args, "gen", False):
        try:
            import requests
            r = requests.post(f"{base}/api/keys/generate", timeout=2)
            if r.status_code == 200:
                print(f"Generated new API Key: {r.json().get('key')}")
                return
        except Exception:
            pass
        keys = _read_keys_offline()
        key = f"sk-shard-{uuid.uuid4().hex[:16]}"
        keys.append(key)
        _write_keys_offline(keys)
        print(f"Generated API Key (saved locally): {key}")
    else:
        try:
            import requests
            r = requests.get(f"{base}/api/keys", timeout=2)
            if r.status_code == 200:
                keys = r.json().get("keys", [])
                print("Active API Keys:")
                for k in keys:
                    print(f"  - {k}")
                return
        except Exception:
            pass
        keys = _read_keys_offline()
        if keys:
            print(f"Local API Keys ({paths.keys_file()}):")
            for k in keys:
                print(f"  - {k}")
        else:
            key = f"sk-shard-{uuid.uuid4().hex[:16]}"
            _write_keys_offline([key])
            print(f"Initialized local API Key ({paths.keys_file()}):\n  - {key}")
def cmd_fit(args):
    print_banner()
    model_id = (getattr(args, "model", None) or "").strip()
    if not model_id:
        print("Usage: shard fit <model_id>   (example: Qwen/Qwen2.5-Coder-7B-Instruct)")
        sys.exit(1)
    ctx_tokens = int(getattr(args, "ctx", 4096) or 4096)
    no_fetch = bool(getattr(args, "no_fetch", False))
    base = _base_url()
    print(f"Checking fit: {model_id} (ctx={ctx_tokens})\n")
    rep = None
    if ctx_tokens == 4096 and not no_fetch:
        try:
            import requests
            r = requests.get(f"{base}/api/fit/{model_id}", timeout=15)
            rep = r.json() if r.status_code == 200 else None
        except Exception:
            rep = None
    if not isinstance(rep, dict) or "quants" not in rep:
        try:
            from .fit import fit_report
            rep = fit_report(model_id, ctx_tokens=ctx_tokens, fetch=not no_fetch)
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
            print(f"Recommendation: {rec} -- file {rec_row.get('size_gb', 0):.1f} GB, "
                  f"{rec_row.get('ngl', 0)}/{layers} layers on GPU, "
                  f"~{rec_row.get('tok_s', 0):.1f} tok/s")
    if not rep.get("disk_ok", True):
        print(f"Disk: not enough space (free: {rep.get('disk_free_gb', 0):.1f} GB)")
    if rep.get("mmap_note"):
        print(f"Note: {rep['mmap_note']}")
    print()
    print(rep.get("message_ru", ""))
def _models_dir_set(path):
    base = _base_url()
    if path.lower() == "reset":
        try:
            import requests
            r = requests.post(f"{base}/api/settings", json={"reset": True}, timeout=5)
        except Exception:
            r = None
        if r is not None and r.status_code == 200:
            print("Models folder reset to default. Applies after server restart.")
            return
        from . import settings as _settings
        _settings.reset_models_dir()
        print(f"Models folder reset to default ({_settings.models_dir()}). Applies after server restart.")
        return
    try:
        import requests
        r = requests.post(f"{base}/api/settings", json={"models_dir": path}, timeout=5)
    except Exception:
        r = None
    if r is not None:
        if r.status_code == 200:
            data = r.json()
            print(f"Models folder saved: {data.get('models_dir')}")
            print("Applies after server restart.")
            return
        if r.status_code == 400:
            print(f"Error: {(r.json() or {}).get('message', 'Invalid path')}")
            sys.exit(1)
    from . import settings as _settings
    try:
        saved = _settings.set_models_dir(path)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    print(f"Models folder saved: {saved}")
    print("Applies after server restart.")
def cmd_models_dir(args):
    print_banner()
    new_path = (getattr(args, "path", None) or "").strip()
    if new_path:
        _models_dir_set(new_path)
        return
    base = _base_url()
    data = None
    try:
        import requests
        r = requests.get(f"{base}/api/settings", timeout=2)
        if r.status_code == 200:
            data = r.json()
    except Exception:
        pass
    if not isinstance(data, dict):
        from . import settings as _settings
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
    import requests
    model_id = args.model if hasattr(args, "model") and args.model else "Qwen/Qwen2.5-Coder-1.5B-Instruct"
    base = _base_url()
    api_key = _default_api_key()
    print_banner()
    print(f"Connecting to {base} | model: {model_id}")
    try:
        requests.post(f"{base}/api/models/load", json={"model_id": model_id}, timeout=3)
    except Exception as e:
        print(f"[ERROR] Cannot reach SHARD server at {base}: {e}")
        print("Start SHARD first with: shard start --daemon")
        sys.exit(1)
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
            headers = {"Authorization": f"Bearer {api_key}"}
            t0 = time.time()
            res = requests.post(f"{base}/v1/chat/completions", json=payload, headers=headers, timeout=90)
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
            print(f"Failed to communicate with SHARD server: {e}")
def _print_lan_join_instructions(st, open_net):
    ips = [ip for ip in (st.get("ips") or []) if ip]
    if not ips and st.get("ip") and st.get("ip") != "127.0.0.1":
        ips = [st["ip"]]
    code = st.get("pairing_code")
    if ips:
        print(f"Reachable Host IP(s): {', '.join(ips)}")
        for ip in ips:
            if open_net or not code:
                print(f"  Join command: shard lan join {ip} --share")
            else:
                print(f"  Join command: shard lan join {ip} {code} --share")
    else:
        print("Warning: no non-loopback LAN IPv4 address detected on this host.")
        if open_net or not code:
            print("  Join command: shard lan join <SERVER_IP> --share")
        else:
            print(f"  Join command: shard lan join <SERVER_IP> {code} --share")
    print("Note: '--share' enables contributing VRAM/RAM as a compute worker.")
def cmd_lan(args):
    import requests
    from . import lan
    print_banner()
    base = _base_url()
    action = (getattr(args, "action", None) or "status").lower()
    if action == "status":
        st = None
        try:
            r = requests.get(f"{base}/api/lan/status", timeout=3)
            st = r.json() if r.status_code == 200 else None
        except Exception:
            st = None
        if not isinstance(st, dict):
            ips = lan.local_ips()
            print(f"SHARD Server is not running at {base}.")
            print(f"Host LAN IP(s): {', '.join(ips) if ips else 'None detected'}")
            print("Start server first: shard start --daemon")
            sys.exit(1)
        print(f"Mode:        {st.get('mode') or 'off'} | Network: {st.get('name')}"
              + (" [OPEN]" if st.get("open") else ""))
        ips = st.get("ips") or ([st.get("ip")] if st.get("ip") else [])
        print(f"Host IP(s):  {', '.join(ips) if ips else 'None detected'}")
        if st.get("pairing_code"):
            print(f"Pairing Code:{st.get('pairing_code')}")
        if st.get("target_model"):
            print(f"Target Model:{st.get('target_model')}")
        members = st.get("members") or []
        if members:
            print("\nMembers:")
            for m in members:
                mark = "online" if m.get("alive") else "unreachable"
                print(f"  {m.get('ip')}  {m.get('name')}  [{m.get('role')}] -- {mark}")
        peers = [p for p in (st.get("peers") or []) if p.get("role") == "server"]
        if peers:
            print("\nDiscovered LAN Servers (UDP):")
            for p in peers:
                tag = " [OPEN]" if p.get("open") else ""
                model = f" model: {p.get('model')}" if p.get("model") else ""
                print(f"  {p.get('ip')}  {p.get('name')}{tag}{model}  port {p.get('port')}"
                      f"  -> shard lan join {p.get('ip')}")
        rpc = st.get("rpc")
        if rpc:
            print(f"\nRPC Pool: --rpc {rpc.get('servers')} -ts {rpc.get('split')}")
        return
    if action == "create":
        name = " ".join(getattr(args, "extra", []) or []).strip() or None
        open_net = bool(getattr(args, "open", False))
        model = getattr(args, "model", None)
        try:
            r = requests.post(f"{base}/api/lan/create",
                              json={"name": name, "open": open_net, "model": model}, timeout=10)
        except Exception:
            r = None
        if r is None or r.status_code != 200:
            print(f"Cannot create LAN network: SHARD server is not running at {base}.")
            print("Start SHARD first: shard start --daemon")
            sys.exit(1)
        st = r.json()
        if open_net:
            print(f"Open LAN network '{st.get('name')}' created (no pairing code required).")
        else:
            print(f"LAN network '{st.get('name')}' created. Pairing code: {st.get('pairing_code')}")
        _print_lan_join_instructions(st, open_net)
        return
    if action == "model":
        extra = getattr(args, "extra", []) or []
        model_id = " ".join(extra).strip() or (getattr(args, "model", None) or "").strip()
        if not model_id:
            print("Usage: shard lan model <model_id>")
            sys.exit(1)
        try:
            r = requests.post(f"{base}/api/lan/model", json={"model": model_id}, timeout=5)
            data = r.json()
            if data.get("status") == "ok":
                print(data.get("message"))
                return
            print(f"Error: {data.get('message')}")
            sys.exit(1)
        except Exception:
            print(f"SHARD server is not running at {base}. Start with: shard start --daemon")
            sys.exit(1)
    if action == "join":
        extra = getattr(args, "extra", []) or []
        if len(extra) < 1:
            print("Usage: shard lan join <SERVER_IP> [CODE] [--share] [--port PORT]")
            print("Note: CODE can be omitted for open networks.")
            sys.exit(1)
        raw_ip, code = extra[0], (extra[1] if len(extra) > 1 else "")
        clean_ip, ip_err = lan.validate_master_host(raw_ip)
        if ip_err:
            print(f"Error: {ip_err}")
            sys.exit(1)
        port = int(getattr(args, "port", None) or lan.DEFAULT_MASTER_PORT)
        try:
            r = requests.post(f"{base}/api/lan/join",
                              json={"ip": clean_ip, "code": code,
                                    "share": bool(getattr(args, "share", False)),
                                    "port": port},
                              timeout=15)
        except Exception:
            r = None
        if r is None:
            print(f"Local SHARD server is not running at {base}.")
            print("Start local SHARD first: shard start --daemon")
            sys.exit(1)
        data = r.json()
        if data.get("status") == "ok":
            print(f"Joined '{data.get('master')}' ({data.get('master_ip', clean_ip)}). Mode: {data.get('mode')}")
            if data.get("warning"):
                print(f"Warning: compute sharing disabled -- {data.get('warning')}")
        else:
            print(f"Error: {data.get('message')}")
            sys.exit(1)
        return
    if action == "off":
        try:
            r = requests.post(f"{base}/api/lan/off", json={}, timeout=5)
        except Exception:
            r = None
        if r is None or r.status_code != 200:
            print(f"SHARD server is not running at {base}.")
            sys.exit(1)
        print("LAN network disconnected.")
        return
    print("Usage: shard lan [status|create|join|model|off]")
    sys.exit(1)
def _build_parser():
    parser = argparse.ArgumentParser(prog="shard", description="SHARD CLI Engine", add_help=False)
    parser.add_argument("-V", "--version", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    sub = parser.add_subparsers(dest="command")
    p_start = sub.add_parser("start", add_help=False)
    p_start.add_argument("--host", default=None)
    p_start.add_argument("--port", type=int, default=None)
    p_start.add_argument("-d", "--daemon", action="store_true")
    p_start.add_argument("--no-browser", action="store_true")
    sub.add_parser("stop", add_help=False)
    p_restart = sub.add_parser("restart", add_help=False)
    p_restart.add_argument("--host", default=None)
    p_restart.add_argument("--port", type=int, default=None)
    p_restart.add_argument("-d", "--daemon", action="store_true")
    p_restart.add_argument("--foreground", action="store_true")
    p_restart.add_argument("--no-browser", action="store_true")
    sub.add_parser("gui", add_help=False)
    sub.add_parser("status", add_help=False)
    sub.add_parser("doctor", add_help=False)
    sub.add_parser("version", add_help=False)
    p_upd = sub.add_parser("update", add_help=False)
    p_upd.add_argument("--check", action="store_true")
    p_list = sub.add_parser("list", add_help=False)
    p_list.add_argument("--category", default="all")
    p_run = sub.add_parser("run", add_help=False)
    p_run.add_argument("model", nargs="?", default="Qwen/Qwen2.5-Coder-1.5B-Instruct")
    p_test = sub.add_parser("test", add_help=False)
    p_test.add_argument("--strict-native", action="store_true")
    p_key = sub.add_parser("key", add_help=False)
    p_key.add_argument("--gen", action="store_true")
    p_fit = sub.add_parser("fit", add_help=False)
    p_fit.add_argument("model", nargs="?", default=None)
    p_fit.add_argument("--ctx", type=int, default=4096)
    p_fit.add_argument("--no-fetch", action="store_true")
    p_dir = sub.add_parser("models-dir", add_help=False)
    p_dir.add_argument("path", nargs="?", default=None)
    p_lan = sub.add_parser("lan", add_help=False)
    p_lan.add_argument("action", nargs="?", default="status", choices=["status", "create", "join", "model", "off"])
    p_lan.add_argument("extra", nargs="*")
    p_lan.add_argument("--share", action="store_true")
    p_lan.add_argument("--open", action="store_true", dest="open")
    p_lan.add_argument("--model", default=None)
    p_lan.add_argument("--port", type=int, default=None)
    sub.add_parser("help", add_help=False)
    return parser
def main(argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if not raw_argv:
        cmd_help()
        return
    if raw_argv[0] in ("-h", "--help", "help"):
        cmd_help()
        return
    if raw_argv[0] in ("-V", "--version", "version"):
        cmd_version()
        return
    if "-h" in raw_argv or "--help" in raw_argv:
        cmd_help()
        return
    parser = _build_parser()
    args = parser.parse_args(raw_argv)
    if args.version:
        cmd_version()
    elif args.command == "start":
        cmd_start(args)
    elif args.command == "stop":
        cmd_stop()
    elif args.command == "restart":
        cmd_restart(args)
    elif args.command == "gui":
        cmd_gui()
    elif args.command == "status":
        cmd_status()
    elif args.command == "doctor":
        cmd_doctor()
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "test":
        cmd_test(args)
    elif args.command == "key":
        cmd_key(args)
    elif args.command == "fit":
        cmd_fit(args)
    elif args.command == "models-dir":
        cmd_models_dir(args)
    elif args.command == "lan":
        cmd_lan(args)
    else:
        cmd_help()
if __name__ == "__main__":
    main()
