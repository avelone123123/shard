import os
import re
import shutil
import subprocess
import time
from . import paths
_gpu_cache = {"ts": 0.0, "gpu_name": "CPU Only", "vram_total_mb": 0, "vram_used_mb": 0, "probed": False}
def _existing_ancestor(path):
    cur = path
    while cur and not os.path.exists(cur):
        parent = os.path.dirname(cur)
        if parent == cur:
            return os.path.expanduser("~")
        cur = parent
    return cur or os.path.expanduser("~")
def get_hardware_profile(force_refresh=False):
    import psutil
    now = time.time()
    if force_refresh or not _gpu_cache["probed"] or (now - _gpu_cache["ts"] >= 4.0):
        _gpu_cache["ts"] = now
        _gpu_cache["probed"] = True
        try:
            cmd = ["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"]
            kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            res = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=3, **kw)
            if res.returncode == 0 and res.stdout.strip():
                parts = res.stdout.strip().split("\n")[0].split(",")
                if len(parts) >= 3:
                    _gpu_cache["gpu_name"] = parts[0].strip()
                    _gpu_cache["vram_total_mb"] = int(float(parts[1].strip()))
                    _gpu_cache["vram_used_mb"] = int(float(parts[2].strip()))
        except Exception:
            pass
    gpu_name = _gpu_cache["gpu_name"]
    vram_total_mb = _gpu_cache["vram_total_mb"]
    vram_used_mb = _gpu_cache["vram_used_mb"]
    ram = psutil.virtual_memory()
    ram_total_mb = int(ram.total / (1024 * 1024))
    ram_avail_mb = int(ram.available / (1024 * 1024))
    ram_used_mb = ram_total_mb - ram_avail_mb
    ram_total_gb = round(ram_total_mb / 1024, 1)
    ram_used_gb = round(ram_used_mb / 1024, 1)
    target_disk = _existing_ancestor(os.environ.get("HF_HOME") or paths.default_models_dir())
    disk = shutil.disk_usage(target_disk)
    disk_free_gb = round(disk.free / (1024 ** 3), 1)
    return {
        "gpu_name": gpu_name,
        "vram_total_mb": vram_total_mb,
        "vram_used_mb": vram_used_mb,
        "vram_free_mb": max(0, vram_total_mb - vram_used_mb),
        "ram_total_mb": ram_total_mb,
        "ram_avail_mb": ram_avail_mb,
        "ram_used_mb": ram_used_mb,
        "ram_total_gb": ram_total_gb,
        "ram_used_gb": ram_used_gb,
        "disk_free_gb": disk_free_gb,
        "cpu_cores": psutil.cpu_count(logical=True)
    }
def estimate_model_params(model_id):
    mid = model_id.lower()
    if "360m" in mid or "135m" in mid:
        return 0.35
    if "gpt2-xl" in mid:
        return 1.5
    if "gpt2-large" in mid:
        return 0.8
    if "gpt2-medium" in mid:
        return 0.35
    if mid.endswith("/gpt2") or mid == "gpt2" or "distilgpt2" in mid:
        return 0.15
    if "phi-3.5-mini" in mid or "phi-3-mini" in mid:
        return 3.8
    if "phi-4-mini" in mid:
        return 3.8
    if mid.endswith("phi-4") or "/phi-4" in mid:
        return 14.0
    if "mistral-nemo" in mid:
        return 12.0
    if "deepseek-v3" in mid or "deepseek-r1" == mid.split("/")[-1]:
        return 67.0
    if "command-r" in mid:
        return 35.0
    m_b = re.findall(r"(?<![0-9.])(\d+(?:\.\d+)?)b(?![a-z])", mid)
    if m_b:
        val = float(m_b[-1])
        if val >= 300:
            return 405.0
        if val >= 65:
            return 70.0
        if val >= 30:
            return 32.0
        if val >= 26:
            return 27.0
        if val >= 20:
            return 22.0
        if val >= 11:
            return 14.0
        if val >= 7:
            return 8.0
        if val >= 5.5:
            return 6.0
        if val >= 3.0:
            return 3.5
        if val >= 1.9:
            return 2.0
        if val >= 1.0:
            return 1.5
        return max(0.35, round(val, 2))
    m_m = re.findall(r"(?<![0-9.])(\d+)m(?![a-z])", mid)
    if m_m:
        return max(0.1, round(float(m_m[-1]) / 1000.0, 2))
    if "embed" in mid or "mini" in mid or "small" in mid or "tiny" in mid:
        return 0.6
    return 7.0
def evaluate_compatibility(model_id, hw=None):
    if hw is None:
        hw = get_hardware_profile()
    params_b = estimate_model_params(model_id)
    vram_mb = hw.get("vram_total_mb", 4096)
    ram_mb = hw.get("ram_total_mb", 16384)
    q4_size_mb = max(120, int(params_b * 650))
    if q4_size_mb <= max(900, vram_mb - 500):
        return {
            "tier": "PERFECT",
            "tier_label": "ИДЕАЛЬНО (VRAM)",
            "color": "#22a06b",
            "speed_est": "45-75 ток/сек" if params_b <= 2.5 else "30-50 ток/сек",
            "mode": "100% GPU VRAM",
            "size_est_mb": q4_size_mb,
            "params_b": params_b,
            "vram_alloc_mb": q4_size_mb,
            "ram_alloc_mb": 0,
            "nvme_streaming": False,
            "description": f"Полностью помещается в {vram_mb} MB VRAM. Максимальная скорость без свопа."
        }
    if q4_size_mb <= max(2200, vram_mb + ram_mb - 4000):
        vram_portion = max(1000, vram_mb - 600) if vram_mb > 0 else 0
        ram_portion = max(256, q4_size_mb - vram_portion)
        return {
            "tier": "RECOMMENDED",
            "tier_label": "РЕКОМЕНДУЕТСЯ (VRAM+RAM)",
            "color": "#d4943a",
            "speed_est": "18-32 ток/сек" if params_b <= 10 else "9-16 ток/сек",
            "mode": "VRAM + RAM Hybrid",
            "size_est_mb": q4_size_mb,
            "params_b": params_b,
            "vram_alloc_mb": vram_portion,
            "ram_alloc_mb": ram_portion,
            "nvme_streaming": False,
            "description": f"Гибридный режим: горячие слои в VRAM, остальные в RAM без переполнения памяти."
        }
    return {
        "tier": "NVME_STREAM",
        "tier_label": "SSD СТРИМИНГ (NVMe)",
        "color": "#c96a4b",
        "speed_est": "1.5-4.5 ток/сек",
        "mode": "NVMe Layer Streaming",
        "size_est_mb": q4_size_mb,
        "params_b": params_b,
        "vram_alloc_mb": max(1000, vram_mb - 800) if vram_mb > 0 else 0,
        "ram_alloc_mb": max(1024, min(ram_mb - 3000, 8000)),
        "nvme_streaming": True,
        "description": "Послойный Zero-Copy стриминг весов с NVMe SSD без ограничений ОЗУ."
    }

