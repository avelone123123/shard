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
