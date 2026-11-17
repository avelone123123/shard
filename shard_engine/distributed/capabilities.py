import socket
import time
from .. import hardware as _hardware
from .. import lan as _lan
def detect_llama_offload_devices():
    try:
        from .. import gguf_backend, proc_io
        exe = gguf_backend.find_llama_server()
        if not exe:
            return []
        res = proc_io.run_subprocess_safe([exe, "--list-devices"], timeout=3.0)
        if res.returncode != 0:
            return []
        devices = []
        in_section = False
        for line in (res.stdout or "").splitlines():
            s = line.strip()
            if s.lower().startswith("available devices:"):
                in_section = True
                continue
            if not in_section or not s:
                continue
            if s.lower() == "(none)":
                return []
            devices.append(s)
        return devices
    except Exception:
        return []
def probe_node_capabilities(force_refresh=False):
    hw = _hardware.get_hardware_profile(force_refresh=force_refresh)
    local_devices = detect_llama_offload_devices()
    ifaces = _lan.local_interfaces()
    usable_ifaces = [x for x in ifaces if x.get("kind") != "virtual"]
    backends = dict(hw.get("backends") or {})
    backends["llama_offload_devices"] = local_devices
    return {
        "hw": hw,
        "backends": backends,
        "local_offload_devices": local_devices,
        "interfaces": usable_ifaces,
        "primary_ip": usable_ifaces[0]["ip"] if usable_ifaces else _lan.local_ip(),
        "probed_at": time.time(),
    }
def measure_tcp_link(host, port, timeout=2.0, probe_bytes=65536):
    t0 = time.perf_counter()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(float(timeout))
    try:
        s.connect((str(host), int(port)))
        rtt_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {"reachable": True, "rtt_ms": rtt_ms, "host": str(host), "port": int(port)}
    except Exception as e:
        return {"reachable": False, "rtt_ms": None, "host": str(host), "port": int(port), "error": str(e)[:120]}
    finally:
        try:
            s.close()
        except Exception:
            pass
def measure_bandwidth_mbps(send_fn, recv_fn, payload_bytes=262144):
    buf = b"\x5a" * int(payload_bytes)
    t0 = time.perf_counter()
    send_fn(buf)
    ack = recv_fn()
    dt = max(1e-5, time.perf_counter() - t0)
    mbps = round((len(buf) * 8.0) / (dt * 1_000_000.0), 2)
    return {"bytes": len(buf), "elapsed_ms": round(dt * 1000.0, 2), "mbps": mbps, "ok": bool(ack is not None)}
def effective_worker_budget(hw, quota=None, link_rtt_ms=None, bandwidth_mbps=None):
    eff = _hardware.effective_usable_resources(hw, quota=quota)
    vram_mb = eff["usable_vram_mb"]
    ram_mb = eff["usable_ram_mb"]
    has_gpu = bool((hw or {}).get("vram_total_mb", 0) > 0 and vram_mb > 0)
    tier = "gpu" if has_gpu else "cpu"
    primary_mb = vram_mb if has_gpu else ram_mb
    score = _lan.speed_score(hw) or (0.45 if has_gpu else 0.12)
    if link_rtt_ms is not None and link_rtt_ms > 0:
        rtt_penalty = 1.0 / (1.0 + (float(link_rtt_ms) / 40.0))
        score = round(max(0.02, score * rtt_penalty), 4)
    return {
        "usable_vram_mb": vram_mb,
        "usable_ram_mb": ram_mb,
        "usable_threads": eff["usable_threads"],
        "primary_mb": primary_mb,
        "device_type": tier,
        "speed_score": score,
        "link_rtt_ms": link_rtt_ms,
        "bandwidth_mbps": bandwidth_mbps,
        "raw_vram_total_mb": eff["raw_vram_total_mb"],
        "raw_ram_total_mb": eff["raw_ram_total_mb"],
    }
