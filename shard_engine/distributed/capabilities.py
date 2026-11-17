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
