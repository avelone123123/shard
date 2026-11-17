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
