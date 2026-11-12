import json
import math
import os
import re
import time
from . import hardware as _hardware
BW_GPU_GB_S = 90.0
BW_RAM_GB_S = 40.0
BW_DISK_GB_S = 2.5
VRAM_RESERVE_MB = 400
RAM_RESERVE_MB = 2048
QUANT_TIERS = [
    ("Q8_0", 8.5),
    ("Q6_K", 6.6),
    ("Q5_K_M", 5.7),
    ("Q4_K_M", 4.85),
    ("Q3_K_M", 3.9),
    ("Q2_K", 3.05),
]
_GB = 1e9
def _cfg_int(cfg, *names, default=0):
    for n in names:
        v = cfg.get(n)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                pass
    return default
def params_from_config(cfg):
    hidden = _cfg_int(cfg, "hidden_size", "d_model", "n_embd")
    layers = _cfg_int(cfg, "num_hidden_layers", "n_layer")
    heads = _cfg_int(cfg, "num_attention_heads", "n_head")
    if not (hidden and layers and heads):
        raise ValueError("config.json без hidden_size/num_hidden_layers")
    head_dim = _cfg_int(cfg, "head_dim") or (hidden // heads)
    kv_heads = _cfg_int(cfg, "num_key_value_heads") or heads
    inter = _cfg_int(cfg, "intermediate_size")
    vocab = _cfg_int(cfg, "vocab_size")
    tied = bool(cfg.get("tie_word_embeddings", False))
    embed = vocab * hidden if vocab else 0
    lm_head = 0 if tied else (vocab * hidden if vocab else 0)
    if not tied and not vocab:
        lm_head = hidden * hidden
    q = hidden * (heads * head_dim)
    k = hidden * (kv_heads * head_dim)
    v = k
    o = hidden * (heads * head_dim)
    attn_per_layer = q + k + v + o
    mlp_per_layer = 0
    if inter:
        mlp_per_layer = 3 * hidden * inter
    n_experts = _cfg_int(cfg, "num_experts", "num_local_experts")
    if n_experts and inter:
        mlp_per_layer = n_experts * 3 * hidden * inter
        shared_inter = _cfg_int(cfg, "shared_expert_intermediate_size")
        if shared_inter:
            mlp_per_layer += 3 * hidden * shared_inter
    per_layer = attn_per_layer + mlp_per_layer
    total = embed + lm_head + per_layer * layers
    if total <= 0:
        raise ValueError("нулевой расчет параметров")
    return round(total / 1e9, 2), "config"
def kv_cache_bytes_from_config(cfg, ctx_tokens=4096):
    layers = _cfg_int(cfg, "num_hidden_layers", "n_layer")
    heads = _cfg_int(cfg, "num_attention_heads", "n_head")
    hidden = _cfg_int(cfg, "hidden_size", "d_model", "n_embd")
    if not (layers and heads and hidden):
        return 0
    head_dim = _cfg_int(cfg, "head_dim") or (hidden // heads)
    kv_heads = _cfg_int(cfg, "num_key_value_heads") or heads
    ctx = max(256, int(ctx_tokens))
    return 2 * layers * kv_heads * head_dim * ctx * 2
def quant_size_bytes(params_b, bpw):
    return params_b * 1e9 * bpw / 8.0
def est_tok_s(vram_bytes, ram_bytes, disk_bytes=0.0):
    t = vram_bytes / (_GB * BW_GPU_GB_S)
    t += ram_bytes / (_GB * BW_RAM_GB_S)
    t += disk_bytes / (_GB * BW_DISK_GB_S)
    if t <= 0:
        return 0.0
    return round(1.0 / t, 1)
_LAYER_GUESS = [(1.0, 24), (4.0, 36), (8.0, 32), (15.0, 48), (35.0, 64), (1e9, 80)]
def _layers_from_params(params_b):
    for cap, n in _LAYER_GUESS:
        if params_b <= cap:
            return n
    return 80
def _fallback_cfg(model_id, params_b, ctx_tokens):
    hidden_by_b = 768 if params_b <= 0.5 else (2048 if params_b <= 4 else 4096)
    layers = _layers_from_params(params_b)
    heads = max(4, hidden_by_b // 128)
    return {
        "hidden_size": hidden_by_b,
        "num_hidden_layers": layers,
        "num_attention_heads": heads,
        "num_key_value_heads": heads,
        "intermediate_size": int(hidden_by_b * 5.3),
        "vocab_size": 32000,
        "tie_word_embeddings": True,
        "_source": "name-regex",
    }, "name-regex"
def fetch_config(model_id, timeout=10):
    try:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(model_id, "config.json")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        pass
    try:
        import urllib.request
        url = f"https://huggingface.co/{model_id}/raw/main/config.json"
        req = urllib.request.Request(url, headers={"User-Agent": "shard-engine"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
_fit_cache = {}
def _budgets(hw):
    vram_budget_bytes = max(0, hw.get("vram_free_mb", 0) - VRAM_RESERVE_MB) * 1024 * 1024
    ram_budget_bytes = max(0, hw.get("ram_avail_mb", 0) - RAM_RESERVE_MB) * 1024 * 1024
    return vram_budget_bytes, ram_budget_bytes
def _plan_quant(params_b, cfg, kv_bytes, bpw, vram_budget, ram_budget):
    w_bytes = quant_size_bytes(params_b, bpw)
    total = w_bytes + kv_bytes
    row = {
        "bpw": bpw,
        "size_gb": round(w_bytes / _GB, 2),
        "total_gb": round(total / _GB, 2),
    }
    if total <= vram_budget and total > 0:
        row.update({
            "vram_gb": round(total / _GB, 2),
            "ram_gb": 0.0,
            "ngl": 999,
            "verdict": "GPU",
            "tok_s": est_tok_s(total, 0),
        })
        return row
    n_layers = _cfg_int(cfg, "num_hidden_layers", "n_layer") or _layers_from_params(params_b)
    bytes_per_layer = (w_bytes + kv_bytes) / max(1, n_layers)
    ngl = int(vram_budget // bytes_per_layer) if bytes_per_layer > 0 else 0
    ngl = max(0, min(n_layers, ngl))
    gpu_bytes = ngl * bytes_per_layer
    ram_bytes = max(0.0, total - gpu_bytes)
    if ngl >= 1 and ram_bytes <= ram_budget:
        row.update({
            "vram_gb": round(gpu_bytes / _GB, 2),
            "ram_gb": round(ram_bytes / _GB, 2),
            "ngl": ngl,
            "verdict": "HYBRID",
            "tok_s": est_tok_s(gpu_bytes, ram_bytes),
        })
        return row
    row.update({
        "vram_gb": 0.0,
        "ram_gb": round(ram_bytes / _GB, 2),
        "ngl": 0,
        "verdict": "IMPOSSIBLE",
        "tok_s": 0.0,
    })
    return row
def _verdict_message_ru(model_id, verdict, quant, tok_s, params_b, hw, kv_gb, total_gb, ngl=None):
    gpu = hw.get("gpu_name", "CPU")
    vram = hw.get("vram_total_mb", 0)
    ram = round(hw.get("ram_total_mb", 0) / 1024, 1)
    short = model_id.split("/")[-1]
    if verdict == "GPU":
        return (f"[OK] {short} ({params_b}B, {quant}) полностью помещается в VRAM "
                f"({gpu}, {vram} MB): {total_gb} ГБ с KV-кэшем. Ожидаемая скорость ~{tok_s} ток/с. Можно запускать.")
    if verdict == "HYBRID":
        return (f"[HYBRID] {short} ({params_b}B, {quant}) не влезает в VRAM целиком — будет гибрид: "
                f"{ngl} слоёв в VRAM ({gpu}, {vram} MB), остальное в RAM ({ram} ГБ). "
                f"Ожидаемая скорость ~{tok_s} ток/с (медленнее GPU-режима). Можно запускать, но наберитесь терпения.")
    return (f"[BLOCKED] {short} ({params_b}B) НЕ ЗАПУСТИТСЯ на этом железе ({gpu}, {vram} MB VRAM, {ram} ГБ RAM) "
            f"— ни один уровень квантования не влезает даже в связку VRAM+RAM. "
            f"Загрузка приведёт к краху или зависанию. Возьмите модель меньше.")
def fit_report(model_id, hw=None, ctx_tokens=4096, fetch=True, include_repo=False):
    if hw is None:
        hw = _hardware.get_hardware_profile()
    cache_key = (model_id, int(ctx_tokens), hw.get("vram_total_mb", 0), hw.get("ram_total_mb", 0))
    now = time.time()
    if cache_key in _fit_cache and (now - _fit_cache[cache_key][0] < 600.0):
        rep = dict(_fit_cache[cache_key][1])
        if include_repo and "gguf_repo" not in rep:
            try:
                from .gguf_backend import find_gguf_repo
                rep["gguf_repo"] = find_gguf_repo(model_id)
            except Exception:
                rep["gguf_repo"] = None
        return rep
    cfg = fetch_config(model_id) if fetch else None
    params_b, source = None, None
    if cfg is not None:
        try:
            params_b, source = params_from_config(cfg)
        except (ValueError, TypeError):
            cfg = None
    if params_b is None:
        params_b = float(_hardware.estimate_model_params(model_id))
        cfg, source = _fallback_cfg(model_id, params_b, ctx_tokens)
        source = "name-regex"
    kv_bytes = kv_cache_bytes_from_config(cfg, ctx_tokens)
    kv_gb = round(kv_bytes / _GB, 2)
    vram_budget, ram_budget = _budgets(hw)
    rows = []
    for name, bpw in QUANT_TIERS:
        row = _plan_quant(params_b, cfg, kv_bytes, bpw, vram_budget, ram_budget)
        row["quant"] = name
        rows.append(row)
    gpu_rows = [r for r in rows if r["verdict"] == "GPU"]
    hyb_rows = [r for r in rows if r["verdict"] == "HYBRID"]
    if gpu_rows:
        best = gpu_rows[0]
        verdict, quant = "GPU", best["quant"]
        tok_s = best["tok_s"]
        ngl = best["ngl"]
    elif hyb_rows:
        best = max(hyb_rows, key=lambda r: (r["tok_s"], r["bpw"]))
        verdict, quant = "HYBRID", best["quant"]
        tok_s = best["tok_s"]
        ngl = best["ngl"]
    else:
        verdict, quant, tok_s, ngl = "IMPOSSIBLE", "Q2_K", 0.0, 0
    fp16_bytes = quant_size_bytes(params_b, 16.0) + kv_bytes
    fp16_gb = round(fp16_bytes / _GB, 2)
    dl_gb = 0.0
    if verdict in ("GPU", "HYBRID"):
        row_b = next((r for r in rows if r["quant"] == quant), None)
        if row_b:
            dl_gb = row_b["size_gb"]
    disk_free_gb = hw.get("disk_free_gb", 0.0)
    disk_ok = disk_free_gb <= 0 or dl_gb == 0 or (disk_free_gb * _GB) >= dl_gb * _GB * 1.05
    if verdict in ("GPU", "HYBRID") and not disk_ok:
        verdict = "IMPOSSIBLE"
        tok_s, ngl = 0.0, 0
    total_gb = next((r["total_gb"] for r in rows if r["quant"] == quant), fp16_gb)
    mmap_note = None
    if verdict == "IMPOSSIBLE":
        disk_tok = est_tok_s(0, 0, fp16_bytes)
        mmap_note = (f"Теоретически модель можно стримить с SSD через mmap (~{disk_tok} ток/с), "
                     f"но это в 30-50 раз медленнее RAM и практически бесполезно.")
    torch_ok = _torch_gpu_ok(hw)
    torch_verdict = None
    if torch_ok:
        torch_verdict = "GPU" if fp16_bytes <= vram_budget else None
    rep = {
        "model_id": model_id,
        "params_b": params_b,
        "params_source": source,
        "layers": _cfg_int(cfg, "num_hidden_layers", "n_layer"),
        "ctx_tokens": int(ctx_tokens),
        "fp16_gb": fp16_gb,
        "kv_gb": kv_gb,
        "quants": rows,
        "verdict": verdict,
        "recommended": quant,
        "ngl": ngl,
        "tok_s_est": tok_s,
        "gguf_size_gb": dl_gb if verdict in ("GPU", "HYBRID") else None,
        "disk_free_gb": disk_free_gb,
        "disk_ok": disk_ok,
        "torch_gpu_ok": torch_verdict == "GPU",
        "mmap_note": mmap_note,
        "gpu_name": hw.get("gpu_name", "CPU"),
        "vram_total_mb": hw.get("vram_total_mb", 0),
        "ram_total_mb": hw.get("ram_total_mb", 0),
        "message_ru": _verdict_message_ru(
            model_id, verdict, quant, tok_s, params_b, hw, kv_gb, total_gb, ngl),
    }
    if verdict != "IMPOSSIBLE":
        rep["message_ru"] += f" KV-кэш при контексте {ctx_tokens}: {kv_gb} ГБ."
    _fit_cache[cache_key] = (now, rep)
    if include_repo:
        try:
            from .gguf_backend import find_gguf_repo
            rep["gguf_repo"] = find_gguf_repo(model_id)
        except Exception:
            rep["gguf_repo"] = None
    return rep
def _torch_gpu_ok(hw):
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False
_ = math

