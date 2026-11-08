import os
import psutil
import shutil
import subprocess
def get_hardware_profile():
    gpu_name = "CPU Only"
    vram_total_mb = 0
    vram_used_mb = 0
    try:
        cmd = ["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"]
        kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5, **kw)
        if res.returncode == 0 and res.stdout.strip():
            parts = res.stdout.strip().split("\n")[0].split(",")
            if len(parts) >= 3:
                gpu_name = parts[0].strip()
                vram_total_mb = int(float(parts[1].strip()))
                vram_used_mb = int(float(parts[2].strip()))
    except Exception:
        pass
    ram = psutil.virtual_memory()
    ram_total_mb = int(ram.total / (1024 * 1024))
    ram_avail_mb = int(ram.available / (1024 * 1024))
    ram_used_mb = ram_total_mb - ram_avail_mb
    ram_total_gb = round(ram_total_mb / 1024, 1)
    ram_used_gb = round(ram_used_mb / 1024, 1)
    disk = shutil.disk_usage(os.path.abspath("/"))
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
    for token in ["405b"]:
        if token in mid:
            return 405.0
    for token in ["70b", "72b", "67b"]:
        if token in mid:
            return 70.0
    for token in ["30b", "32b", "34b"]:
        if token in mid:
            return 32.0
    for token in ["27b"]:
        if token in mid:
            return 27.0
    for token in ["22b"]:
        if token in mid:
            return 22.0
    for token in ["14b", "13b", "12b", "11b"]:
        if token in mid:
            return 14.0
    for token in ["9b", "8b", "7b"]:
        if token in mid:
            return 8.0
    for token in ["6b"]:
        if token in mid:
            return 6.0
    for token in ["4b", "3.8b", "3.5b", "3b"]:
        if token in mid:
            return 3.5
    for token in ["2.5b", "2b"]:
        if token in mid:
            return 2.0
    for token in ["1.7b", "1.5b", "1b", "0.6b", "0.5b"]:
        if token in mid:
            return 1.5
    for token in ["360m", "135m"]:
        if token in mid:
            return 0.35
    return 7.0
def evaluate_compatibility(model_id, hw=None):
    if hw is None:
        hw = get_hardware_profile()
    params_b = estimate_model_params(model_id)
    vram_mb = hw.get("vram_total_mb", 4096)
    ram_mb = hw.get("ram_total_mb", 16384)
    q4_size_mb = int(params_b * 650)
    if q4_size_mb <= (vram_mb - 500):
        return {
            "tier": "PERFECT",
            "tier_label": "ИДЕАЛЬНО (VRAM)",
            "color": "#10b981",
            "speed_est": "45-75 ток/сек",
            "mode": "100% GPU VRAM",
            "vram_alloc_mb": q4_size_mb,
            "ram_alloc_mb": 0,
            "nvme_streaming": False,
            "description": f"Полностью помещается в {vram_mb} MB VRAM. Максимальная скорость."
        }
    if q4_size_mb <= (vram_mb + ram_mb - 4000):
        vram_portion = max(1000, vram_mb - 600)
        ram_portion = q4_size_mb - vram_portion
        return {
            "tier": "RECOMMENDED",
            "tier_label": "РЕКОМЕНДУЕТСЯ (VRAM+RAM)",
            "color": "#06b6d4",
            "speed_est": "18-32 ток/сек" if params_b <= 10 else "9-16 ток/сек",
            "mode": "VRAM + RAM Hybrid",
            "vram_alloc_mb": vram_portion,
            "ram_alloc_mb": ram_portion,
            "nvme_streaming": False,
            "description": f"Гибрид: ключевые слои в {vram_mb} MB VRAM, остальные в RAM. Без OOM."
        }
    return {
        "tier": "NVME_STREAM",
        "tier_label": "SSD СТРИМИНГ (NVMe)",
        "color": "#f97316",
        "speed_est": "1.5-4.5 ток/сек",
        "mode": "NVMe Layer Streaming",
        "vram_alloc_mb": max(1000, vram_mb - 800),
        "ram_alloc_mb": min(ram_mb - 3000, 8000),
        "nvme_streaming": True,
        "description": "Послойный стриминг весов с быстрого NVMe SSD без ограничений памяти."
    }
