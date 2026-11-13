import argparse
import json
import math
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import time
from . import __app_name__, __version__, paths, process, storage
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
def _owner_headers():
    try:
        from . import training
        return {"X-Shard-Owner-Token": training.get_owner_token()}
    except Exception:
        return {}
def _default_api_key():
    env_key = os.environ.get("SHARD_API_KEY")
    if env_key:
        return env_key.strip()
    kf = paths.keys_file()
    if os.path.isfile(kf):
        with storage.file_lock(kf):
            data = storage.read_json_safe(kf, default=None, expected_type=list, quarantine=False, use_backup=True)
            if isinstance(data, list) and data:
                return str(data[0])
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
    print("  lan status         Show LAN/VPN mode, host IPs, active members, and peers")
    print("  lan create [name]  Create a distributed LAN/VPN network (--open, --model ID)")
    print("  lan join <ip> [code] [--share] [--port N]")
    print("                     Join an existing LAN/VPN server (--share contributes VRAM/RAM)")
    print("  lan invite         Show copyable join commands and URLs for LAN & VPN peers")
    print("  lan diagnose       Inspect NICs, Tailscale/Radmin VPN, UPnP IGD, and NAT status (--public)")
    print("  lan model <id>     Set target model for the LAN pool")
    print("  lan off            Leave LAN network or stop hosting")
    print("\nCollaborative Training (SOUP):")
    print("  training status    Show SOUP adapter status, feedback counts, jobs, and versions")
    print("  training doctor    Inspect SOUP installation, Python version, GPU, and train deps")
    print("  training feedback  Manage consent-gated feedback (--status|--id|--action)")
    print("  training privacy   Inspect or toggle privacy & remote contribution settings")
    print("  training dataset   List or build immutable SFT/DPO dataset snapshots")
    print("  training start     Launch SOUP training (--dataset ID [--task sft|dpo] [--dry-run])")
    print("  training logs <id> Show progress and log tail for a training job")
    print("  training cancel <id> | resume <id> [--checkpoint auto]")
    print("  training models | eval <ver> | prepare <ver> | approve <ver> | deploy <ver> | rollback")
    print("  training backup | restore <archive.zip> | prune")
    print("\nSHARD Network (Global P2P & Relay):")
    print("  network status     Show Node ID, hosting/client state, NAT profile, and peers")
    print("  network identity   Show public cryptographic node identity and fingerprint")
    print("  network config     Inspect or configure discovery/relay servers and policies")
    print("  network servers    Browse live public AI servers across the SHARD Network")
    print("  network publish    Publish active local server to Discovery / Relays")
    print("  network unpublish  Stop publishing server to SHARD Network")
    print("  network connect <target> [--mode auto|direct|relay]")
    print("                     Connect to remote SHARD server (shard:// URI, Node ID, or host:port)")
    print("  network disconnect Disconnect from current remote SHARD server")
    print("  network invite [create|list|revoke] [--ttl N] [--max-uses N]")
    print("                     Generate or manage revocable cryptographic invite links")
    print("  network peers      List connected, pending, authorized, and revoked peers")
    print("  network approve <peer> | reject <peer> | kick <peer> | block <peer>")
    print("  network discovery-serve [--host IP] [--port N] [--token STR]")
    print("                     Run a standalone self-hosted SHARD Discovery server")
    print("  network relay-serve [--host IP] [--port N] [--token STR]")
    print("                     Run a standalone self-hosted SHARD Blind Relay server")
    print("  network wg [status|plan] Show WireGuard cross-platform status and config plans")
    print("  network doctor     Inspect firewall, platform NAT, STUN, and network readiness")
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
        for iface in lan.local_interfaces():
            if iface.get("kind") == "virtual":
                continue
            tag = iface.get("provider") or iface.get("kind")
            print(f"Network ({tag}): http://{iface['ip']}:{port}")
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
    os.environ["SHARD_PORT"] = str(port)
    os.environ["SHARD_HOST"] = str(host)
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
        r = requests.get(f"{base}/api/system/hardware", headers=_owner_headers(), timeout=2)
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
    for dep in ("fastapi", "uvicorn", "requests", "psutil", "pydantic", "cryptography", "websockets"):
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
    try:
        from .soup_adapter import detect_soup
        st_soup = detect_soup(probe_deps=False)
        if st_soup.get("available"):
            report("PASS", "SOUP Training", f"v{st_soup.get('soup_version') or '?'} ({' '.join(st_soup.get('command') or [])})")
        else:
            report("INFO", "SOUP Training", f"optional ({st_soup['install_hint']})")
    except Exception as e:
        report("INFO", "SOUP Training", f"optional ({e})")
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
    try:
        from . import net_identity, net_wireguard
        ident = net_identity.NodeIdentity()
        report("PASS", "Node ID", ident.node_id)
        wg_st = net_wireguard.detect_wireguard()
        report("PASS" if wg_st.get("installed") else "INFO", "WireGuard", wg_st.get("implementation"))
    except Exception as e:
        report("WARN", "SHARD Network", str(e))
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
    with storage.file_lock(kf):
        data = storage.read_json_safe(kf, default=[], expected_type=list, quarantine=True, use_backup=True)
        return [str(x) for x in (data or []) if str(x).strip()]
def _write_keys_offline(keys):
    kf = paths.keys_file()
    with storage.file_lock(kf):
        storage.atomic_write_json(kf, [str(k) for k in keys if str(k).strip()], indent=2, fsync=True, backup=True, restrict=True)
def cmd_key(args):
    print_banner()
    base = _base_url()
    hdrs = _owner_headers()
    alive = process.ping_server(base)
    if getattr(args, "gen", False):
        if alive:
            try:
                import requests
                r = requests.post(f"{base}/api/keys/generate", headers=hdrs, timeout=2)
                if r.status_code == 200:
                    print(f"Generated new API Key: {r.json().get('key')}")
                    return
            except Exception:
                pass
        keys = _read_keys_offline()
        key = f"sk-shard-{secrets.token_hex(8)}"
        keys.append(key)
        _write_keys_offline(keys)
        print(f"Generated API Key (saved locally): {key}")
    else:
        if alive:
            try:
                import requests
                r = requests.get(f"{base}/api/keys", headers=hdrs, timeout=2)
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
            key = f"sk-shard-{secrets.token_hex(8)}"
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
            r = requests.get(f"{base}/api/fit/{model_id}", headers=_owner_headers(), timeout=15)
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
    alive = process.ping_server(base)
    if path.lower() == "reset":
        r = None
        if alive:
            try:
                import requests
                r = requests.post(f"{base}/api/settings", json={"reset": True}, headers=_owner_headers(), timeout=5)
            except Exception:
                r = None
        if r is not None and r.status_code == 200:
            print("Models folder reset to default. Applies after server restart.")
            return
        from . import settings as _settings
        _settings.reset_models_dir()
        print(f"Models folder reset to default ({_settings.models_dir()}). Applies after server restart.")
        return
    r = None
    if alive:
        try:
            import requests
            r = requests.post(f"{base}/api/settings", json={"models_dir": path}, headers=_owner_headers(), timeout=5)
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
    if process.ping_server(base):
        try:
            import requests
            r = requests.get(f"{base}/api/settings", headers=_owner_headers(), timeout=2)
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
        requests.post(f"{base}/api/models/load", json={"model_id": model_id}, headers=_owner_headers(), timeout=3)
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
    hdrs = _owner_headers()
    action = (getattr(args, "action", None) or "status").lower()
    if action == "diagnose":
        probe_public = bool(getattr(args, "public", False))
        rep = None
        try:
            r = requests.get(
                f"{base}/api/lan/diagnose",
                params={"public": "1" if probe_public else "0"},
                headers=hdrs,
                timeout=8 if probe_public else 3,
            )
            if r.status_code == 200:
                rep = r.json()
        except Exception:
            rep = None
        if not isinstance(rep, dict):
            rep = lan.connectivity_report(probe_public=probe_public)
        print(f"SHARD Port:          TCP {rep.get('port')} (UDP beacon {rep.get('beacon_port')})")
        print(f"Recommended Join IP: {rep.get('recommended_join_ip')}")
        lan_ifs = rep.get("lan_interfaces") or []
        vpn_ifs = rep.get("vpn_interfaces") or []
        virt_ifs = rep.get("virtual_excluded") or []
        print(f"LAN Interfaces:      {', '.join(f'{x['ip']} ({x['interface']})' for x in lan_ifs) or 'None'}")
        print(f"Overlay VPNs:        {', '.join(f'{x['ip']} [{x['provider']}]' for x in vpn_ifs) or 'None'}")
        if virt_ifs:
            print(f"Excluded Virtual:    {', '.join(f'{x['ip']} ({x['interface']})' for x in virt_ifs)}")
        ts = rep.get("tailscale") or {}
        print(f"Tailscale CLI:       {'running ' + str(ts.get('ip')) if ts.get('running') else ('installed/stopped' if ts.get('installed') else 'not installed')}")
        if probe_public:
            stun = rep.get("stun") or {}
            upnp = rep.get("upnp_igd") or {}
            print(f"Public IP (STUN):    {stun.get('public_ip') or 'unreachable'}")
            print(f"Behind NAT:          {rep.get('behind_nat')}")
            print(f"UPnP IGD Gateway:    {upnp.get('gateway_ip') if upnp.get('found') else 'not found'}")
        return
    if action == "invite":
        inv = None
        try:
            r = requests.get(f"{base}/api/lan/invite", headers=hdrs, timeout=3)
            if r.status_code == 200:
                inv = r.json()
        except Exception:
            inv = None
        if not isinstance(inv, dict) or inv.get("status") != "ok":
            print("Create/start a LAN server first: shard lan create")
            sys.exit(1)
        print(f"Invite for '{inv.get('name')}' ({'OPEN' if inv.get('open') else 'CODE: ' + str(inv.get('pairing_code'))}):")
        for t in inv.get("targets") or []:
            print(f"  [{t.get('provider')}] Browser: {t.get('url')}  | CLI: {t.get('cli_command')}")
        return
    if action == "status":
        st = None
        try:
            r = requests.get(f"{base}/api/lan/status", headers=hdrs, timeout=3)
            st = r.json() if r.status_code == 200 else None
        except Exception:
            st = None
        if not isinstance(st, dict):
            ips = lan.local_ips()
            print(f"SHARD Server is not running at {base}.")
            print(f"Host LAN/VPN IP(s): {', '.join(ips) if ips else 'None detected'}")
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
                              json={"name": name, "open": open_net, "model": model},
                              headers=hdrs,
                              timeout=10)
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
            r = requests.post(f"{base}/api/lan/model", json={"model": model_id}, headers=hdrs, timeout=5)
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
                              headers=hdrs,
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
            r = requests.post(f"{base}/api/lan/off", json={}, headers=hdrs, timeout=5)
        except Exception:
            r = None
        if r is None or r.status_code != 200:
            print(f"SHARD server is not running at {base}.")
            sys.exit(1)
        print("LAN network disconnected.")
        return
    print("Usage: shard lan [status|create|join|invite|diagnose|model|off]")
    sys.exit(1)
def _print_training_help():
    print_banner()
    print("Usage: shard training <subcommand> [options]\n")
    print("Subcommands:")
    print("  status                    Show training overview (SOUP, datasets, jobs, models)")
    print("  doctor | soup             Verify SOUP installation and Python/GPU compatibility")
    print("  privacy [--allow-remote on|off] [--require-review on|off]")
    print("                            [--redact-secrets on|off] [--redact-pii on|off]")
    print("  feedback [list|add|approve|reject|delete]")
    print("                            [--status pending|approved|rejected] [--id ID]")
    print("                            [--prompt TEXT --response TEXT --consent]")
    print("  dataset [list|build|inspect]")
    print("                            [--task sft|dpo] [--name NAME] [--base MODEL] [--id SNAP]")
    print("  start --dataset SNAP_ID   [--base MODEL] [--task sft|dpo] [--epochs N] [--lr F]")
    print("                            [--batch N|auto] [--dry-run] [--no-unload]")
    print("  logs <job_id>             Print job progress and log tail")
    print("  cancel <job_id>           Terminate an active training job cleanly")
    print("  resume <job_id>           Resume a paused/failed job [--checkpoint auto|PATH]")
    print("  models                    List registered model versions and active deployment")
    print("  eval <version_id>         Run SOUP custom eval baseline vs candidate")
    print("  prepare <version_id>      [--target merged|gguf] [--quant q4_k_m]")
    print("  approve <version_id>      [--reject] [--notes TEXT]")
    print("  deploy <version_id>       [--prefer auto|gguf|merged|adapter] [--no-load] [--force]")
    print("  rollback [--version ID]   [--no-load]")
    print("  backup [--no-runs] [--no-datasets]")
    print("  restore <archive.zip>")
    print("  prune                     Remove expired feedback records past retention_days")
def _server_owner_call(method, path, payload=None):
    from . import training
    try:
        import requests
    except Exception:
        return None
    base = _base_url()
    hdrs = {"X-Shard-Owner-Token": training.get_owner_token()}
    try:
        if method == "GET":
            r = requests.get(f"{base}{path}", headers=hdrs, timeout=10)
        else:
            r = requests.post(f"{base}{path}", json=payload or {}, headers=hdrs, timeout=30)
        return r
    except Exception:
        return None
def _parse_bool_toggle(val):
    if val is None:
        return None
    v = str(val).strip().lower()
    if v in ("on", "true", "1", "yes"):
        return True
    if v in ("off", "false", "0", "no"):
        return False
    raise ValueError(f"Expected on/off boolean, got '{val}'")
def cmd_training(args):
    from . import feedback, soup_adapter, training
    sub = (getattr(args, "subcommand", None) or "status").strip().lower()
    extra = list(getattr(args, "extra", []) or [])
    if sub in ("help", "-h", "--help"):
        _print_training_help()
        return
    if sub in ("doctor", "soup"):
        st = soup_adapter.detect_soup(probe_deps=True)
        print_banner()
        print("SOUP Training Engine Diagnostic")
        print("-" * 64)
        print(f"Available:         {'YES' if st['available'] else 'NO'}")
        print(f"Python Runtime:    {st['python_version']} (SOUP compatible <3.14: {'YES' if st['python_compatible'] else 'NO'})")
        print(f"SOUP Version:      {st.get('soup_version') or 'not detected'}")
        print(f"Command:           {' '.join(st.get('command') or []) or 'none'}")
        print(f"CUDA GPU Ready:    {'YES' if st.get('cuda_available') else 'NO'}")
        if st.get("missing_train_deps"):
            print(f"Missing Deps:      {', '.join(st['missing_train_deps'])}")
        for w in st.get("warnings") or []:
            print(f"[WARN] {w}")
        if not st["available"]:
            print(f"Install Hint:      {st.get('install_hint')}")
        return
    if sub == "status":
        ov = training.training_overview()
        print_banner()
        st = ov["soup"]
        fb = ov["feedback_stats"]
        reg = ov["registry"]
        print(f"SOUP Engine:       {'READY' if st['available'] else 'NOT INSTALLED'}" + (f" (v{st['soup_version']})" if st.get("soup_version") else ""))
        print(f"Active Model:      {ov['engine'].get('active_model') or 'none'}")
        print(f"Active Version:    {reg.get('active_version_id') or 'base'}")
        if reg.get("previous_version_id") or reg.get("previous_engine_model_id"):
            print(f"Rollback Target:   {reg.get('previous_version_id') or reg.get('previous_engine_model_id')}")
        c = fb["counts"]
        print(f"Feedback Queue:    total={c['total']} pending={c['pending']} approved={c['approved']} rejected={c['rejected']}")
        print(f"Eligible Training: SFT={fb['eligible_approved']['sft']} | DPO={fb['eligible_approved']['dpo']}")
        print(f"Dataset Snapshots: {len(ov['datasets'])}")
        print(f"Training Jobs:     {len(ov['jobs'])}")
        for w in st.get("warnings") or []:
            print(f"[WARN] {w}")
        return
    if sub == "privacy":
        updates = {}
        try:
            for arg_name, cfg_key in (
                ("allow_remote", "allow_remote_feedback"),
                ("allow_feedback", "allow_feedback"),
                ("require_review", "require_manual_review"),
                ("require_consent", "require_explicit_consent"),
                ("redact_secrets", "redact_secrets"),
                ("redact_pii", "redact_pii"),
            ):
                val = _parse_bool_toggle(getattr(args, arg_name, None))
                if val is not None:
                    updates[cfg_key] = val
            cfg = feedback.update_privacy_config(updates) if updates else feedback.get_privacy_config()
            print(json.dumps(cfg, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"[ERROR] {e}")
            sys.exit(1)
        return
    if sub == "feedback":
        action_word = extra[0].lower() if extra else "list"
        if action_word == "add":
            try:
                entry = feedback.submit_feedback({
                    "prompt": getattr(args, "prompt", None),
                    "response": getattr(args, "response", None),
                    "better_response": getattr(args, "better_response", None),
                    "rating": getattr(args, "rating", None) or "up",
                    "consent_given": bool(getattr(args, "consent", False)),
                    "model_id": getattr(args, "base", None) or "",
                }, source="local")
                print(f"Recorded feedback {entry['id']} (status={entry['status']}, redactions={entry['redactions']})")
            except Exception as e:
                print(f"[ERROR] {e}")
                sys.exit(1)
            return
        if action_word in ("approve", "reject", "delete"):
            target_id = extra[1] if len(extra) > 1 else getattr(args, "id", None)
            if not target_id:
                print(f"Usage: shard training feedback {action_word} <entry_id>")
                sys.exit(1)
            try:
                res = feedback.review_feedback(target_id, action_word)
                print(f"{res['id']} -> {res['status']}")
            except Exception as e:
                print(f"[ERROR] {e}")
                sys.exit(1)
            return
        items = feedback.list_feedback(status=getattr(args, "status_filter", None), limit=50)
        if not items:
            print("No feedback entries matching filter.")
            return
        for it in items:
            print(f"{it['id']}  [{it['status']}] [{it['rating']}] ({it['source']}) prompt={repr(it['prompt'][:60])}")
        return
    if sub == "dataset":
        act = extra[0].lower() if extra else "list"
        if act == "build":
            try:
                m = feedback.build_dataset_snapshot(
                    task=getattr(args, "task", None) or "sft",
                    base_model=getattr(args, "base", None),
                    name=getattr(args, "name", None),
                    include_pending=bool(getattr(args, "include_pending", False)),
                )
                print(f"Created dataset snapshot: {m['snapshot_id']}")
                print(f"Task/Format: {m['task']} / {m['format']} | Records: {m['record_count']} | Path: {m['path']}")
            except Exception as e:
                print(f"[ERROR] {e}")
                sys.exit(1)
            return
        if act == "inspect":
            sid = extra[1] if len(extra) > 1 else getattr(args, "id", None)
            if not sid:
                print("Usage: shard training dataset inspect <snapshot_id>")
                sys.exit(1)
            try:
                m = feedback.get_dataset_manifest(sid)
                print(json.dumps(m, indent=2, ensure_ascii=False))
            except Exception as e:
                print(f"[ERROR] {e}")
                sys.exit(1)
            return
        datasets = feedback.list_datasets()
        if not datasets:
            print("No dataset snapshots built yet.")
            return
        for ds in datasets:
            print(f"{ds['snapshot_id']}  [{ds['task']}/{ds['format']}] records={ds['record_count']} sha={ds['sha256'][:12]}")
        return
    if sub == "start":
        ds_id = getattr(args, "dataset", None) or (extra[0] if extra else None)
        if not ds_id:
            print("Usage: shard training start --dataset <snapshot_id> [--base MODEL] [--task sft|dpo] [--dry-run]")
            sys.exit(1)
        params = {}
        if getattr(args, "epochs", None) is not None:
            params["epochs"] = int(args.epochs)
        if getattr(args, "lr", None) is not None:
            params["lr"] = float(args.lr)
        if getattr(args, "batch", None) is not None:
            params["batch_size"] = args.batch
        payload = {
            "base_model": getattr(args, "base", None),
            "dataset_snapshot_id": ds_id,
            "task": getattr(args, "task", None) or "sft",
            "params": params,
            "dry_run": bool(getattr(args, "dry_run", False)),
            "unload_inference": not bool(getattr(args, "no_unload", False)),
            "experiment_name": getattr(args, "name", None),
        }
        r = _server_owner_call("POST", "/api/training/jobs/start", payload)
        if r is not None:
            data = r.json()
            if r.status_code != 200:
                print(f"[ERROR] {data.get('message')}")
                sys.exit(1)
            job = data["job"]
        else:
            try:
                job = training.start_training_job(**payload)
            except Exception as e:
                print(f"[ERROR] {e}")
                sys.exit(1)
        print(f"Training job: {job['job_id']} | status={job['status']} | task={job['task']}")
        print(f"Base model:   {job['base_model']}")
        print(f"Output dir:   {job['output_dir']}")
        return
    if sub == "logs":
        jid = extra[0] if extra else getattr(args, "id", None)
        if not jid:
            jobs = training.list_training_jobs(limit=15)
            for j in jobs:
                print(f"{j['job_id']}  [{j['status']}] task={j['task']} base={j['base_model']}")
            return
        try:
            j = training.get_training_job(jid)
            print(f"Job {j['job_id']} | status={j['status']} | progress={j['progress']}")
            for ln in j.get("log_tail") or []:
                print(ln)
            if j.get("error"):
                print(f"[ERROR] {j['error']}")
        except Exception as e:
            print(f"[ERROR] {e}")
            sys.exit(1)
        return
    if sub == "cancel":
        jid = extra[0] if extra else getattr(args, "id", None)
        if not jid:
            print("Usage: shard training cancel <job_id>")
            sys.exit(1)
        r = _server_owner_call("POST", f"/api/training/jobs/{jid}/cancel", {})
        if r is not None:
            data = r.json()
            if r.status_code != 200:
                print(f"[ERROR] {data.get('message')}")
                sys.exit(1)
            print(f"Cancelled job {jid} (status={data['job']['status']})")
            return
        try:
            job = training.cancel_training_job(jid)
            print(f"Cancelled job {jid} (status={job['status']})")
        except Exception as e:
            print(f"[ERROR] {e}")
            sys.exit(1)
        return
    if sub == "resume":
        jid = extra[0] if extra else getattr(args, "id", None)
        if not jid:
            print("Usage: shard training resume <job_id> [--checkpoint auto]")
            sys.exit(1)
        ckpt = getattr(args, "checkpoint", None) or "auto"
        r = _server_owner_call("POST", f"/api/training/jobs/{jid}/resume", {"checkpoint": ckpt})
        if r is not None:
            data = r.json()
            if r.status_code != 200:
                print(f"[ERROR] {data.get('message')}")
                sys.exit(1)
            print(f"Resumed job {jid} (status={data['job']['status']})")
            return
        try:
            job = training.resume_training_job(jid, checkpoint=ckpt)
            print(f"Resumed job {jid} (status={job['status']})")
        except Exception as e:
            print(f"[ERROR] {e}")
            sys.exit(1)
        return
    if sub == "models":
        reg = training.list_model_versions()
        print(f"Active version:   {reg.get('active_version_id') or 'base'}")
        print(f"Previous version: {reg.get('previous_version_id') or 'none'}")
        for v in reg.get("versions") or []:
            mark = "*DEPLOYED" if v.get("deployed") else v.get("approval_status", "pending")
            comp = v.get("comparison") or {}
            acc = f"acc={comp.get('candidate_accuracy')} (delta={comp.get('delta_accuracy')})" if comp else f"eval={v.get('eval_status')}"
            print(f"  {v['version_id']} [{mark}] {v['artifact_type']} {acc} base={v['base_model']}")
        return
    if sub == "eval":
        vid = extra[0] if extra else getattr(args, "id", None)
        if not vid:
            print("Usage: shard training eval <version_id>")
            sys.exit(1)
        try:
            v = training.evaluate_version(vid)
            print(json.dumps(v.get("comparison"), indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"[ERROR] {e}")
            sys.exit(1)
        return
    if sub == "prepare":
        vid = extra[0] if extra else getattr(args, "id", None)
        if not vid:
            print("Usage: shard training prepare <version_id> [--target merged|gguf] [--quant q4_k_m]")
            sys.exit(1)
        try:
            v = training.prepare_version_artifact(
                vid,
                target=getattr(args, "target", None) or "merged",
                quant=getattr(args, "quant", None) or "q4_k_m",
            )
            print(f"Prepared {v['version_id']}: type={v['artifact_type']} path={v['artifact_path']}")
        except Exception as e:
            print(f"[ERROR] {e}")
            sys.exit(1)
        return
    if sub == "approve":
        vid = extra[0] if extra else getattr(args, "id", None)
        if not vid:
            print("Usage: shard training approve <version_id> [--reject] [--notes TEXT]")
            sys.exit(1)
        try:
            v = training.approve_version(
                vid,
                approved=not bool(getattr(args, "reject", False)),
                notes=getattr(args, "notes", None),
            )
            print(f"Version {v['version_id']} approval_status={v['approval_status']}")
        except Exception as e:
            print(f"[ERROR] {e}")
            sys.exit(1)
        return
    if sub == "deploy":
        vid = extra[0] if extra else getattr(args, "id", None)
        if not vid:
            print("Usage: shard training deploy <version_id> [--prefer auto|gguf|merged|adapter] [--no-load] [--force]")
            sys.exit(1)
        payload = {
            "prefer": getattr(args, "prefer", None) or "auto",
            "load_into_engine": not bool(getattr(args, "no_load", False)),
            "require_approval": not bool(getattr(args, "force", False)),
        }
        r = _server_owner_call("POST", f"/api/training/models/{vid}/deploy", payload)
        if r is not None:
            data = r.json()
            if r.status_code != 200:
                print(f"[ERROR] {data.get('message')}")
                sys.exit(1)
            print(f"Deployed {vid} -> {data.get('artifact_path')}")
            return
        try:
            res = training.deploy_version(vid, **payload)
            print(f"Deployed {vid} -> {res.get('artifact_path')}")
        except Exception as e:
            print(f"[ERROR] {e}")
            sys.exit(1)
        return
    if sub == "rollback":
        vid = getattr(args, "version", None) or (extra[0] if extra else None)
        payload = {
            "version_id": vid,
            "load_into_engine": not bool(getattr(args, "no_load", False)),
        }
        r = _server_owner_call("POST", "/api/training/models/rollback", payload)
        if r is not None:
            data = r.json()
            if r.status_code != 200:
                print(f"[ERROR] {data.get('message')}")
                sys.exit(1)
            print(f"Rolled back to {data.get('restored_version_id') or data.get('restored_model')}")
            return
        try:
            res = training.rollback_deployment(
                target_version_id=vid,
                load_into_engine=payload["load_into_engine"],
            )
            print(f"Rolled back to {res.get('restored_version_id') or res.get('restored_model')}")
        except Exception as e:
            print(f"[ERROR] {e}")
            sys.exit(1)
        return
    if sub == "backup":
        payload = {
            "include_runs": not bool(getattr(args, "no_runs", False)),
            "include_datasets": not bool(getattr(args, "no_datasets", False)),
        }
        r = _server_owner_call("POST", "/api/training/backup", payload)
        if r is not None and r.status_code == 200:
            res = r.json()
        else:
            res = training.create_backup(**payload)
        print(f"Backup created: {res.get('backup_path')} ({res.get('file_count')} files, {res.get('size_bytes')} bytes)")
        return
    if sub == "restore":
        arc = extra[0] if extra else getattr(args, "id", None)
        if not arc:
            print("Usage: shard training restore <archive.zip>")
            sys.exit(1)
        try:
            res = training.restore_backup(arc)
            print(f"Restored {res.get('restored_files')} files (pre-restore snapshot: {res.get('pre_restore_backup')})")
        except Exception as e:
            print(f"[ERROR] {e}")
            sys.exit(1)
        return
    if sub == "prune":
        r = _server_owner_call("POST", "/api/training/feedback/prune", {})
        if r is not None and r.status_code == 200:
            res = r.json()
        else:
