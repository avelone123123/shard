import json
import math
import os
from .. import fit as _fit
from .. import gguf_backend as _gguf
from .. import hardware as _hardware
from . import capabilities as _cap
SUPPORTED_TORCH_PIPELINE_TYPES = frozenset({
    "llama",
    "qwen2",
    "qwen3",
    "mistral",
    "gemma",
    "gemma2",
    "phi3",
    "gpt2",
})
_MB = 1024 * 1024
def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
def _resolve_local_hf_dir(model_id):
    if not model_id:
        return None
    raw = str(model_id).strip()
    if os.path.isdir(raw) and os.path.isfile(os.path.join(raw, "config.json")):
        return raw
    for hub_dir in _gguf.all_hub_dirs():
        entry = f"models--{raw.replace('/', '--')}"
        snap_root = os.path.join(hub_dir, entry, "snapshots")
        if os.path.isdir(snap_root):
            try:
                for sub in sorted(os.listdir(snap_root)):
                    cand = os.path.join(snap_root, sub)
                    if os.path.isfile(os.path.join(cand, "config.json")):
                        return cand
            except Exception:
                pass
    return None
def _read_safetensors_index(local_dir):
    if not local_dir:
        return None
    idx_path = os.path.join(local_dir, "model.safetensors.index.json")
    if os.path.isfile(idx_path):
        data = _read_json(idx_path)
        if isinstance(data, dict) and isinstance(data.get("weight_map"), dict):
            return data["weight_map"]
    single = os.path.join(local_dir, "model.safetensors")
    if os.path.isfile(single):
        try:
            from safetensors import safe_open
            with safe_open(single, framework="pt", device="cpu") as f:
                return {k: "model.safetensors" for k in f.keys()}
        except Exception:
            pass
    return None
def _layer_prefix_for_type(model_type):
    if model_type == "gpt2":
        return "transformer.h."
    return "model.layers."
def _shard_files_for_stage(weight_map, model_type, layer_start, layer_end, has_embed, has_lm_head):
    if not weight_map:
        return []
    prefix = _layer_prefix_for_type(model_type)
    files = set()
    for tensor_name, shard_file in weight_map.items():
        if has_embed and (tensor_name.startswith("model.embed_tokens") or tensor_name.startswith("transformer.wte") or tensor_name.startswith("transformer.wpe")):
            files.add(shard_file)
            continue
        if has_lm_head and (tensor_name.startswith("lm_head") or tensor_name.startswith("model.norm") or tensor_name.startswith("transformer.ln_f")):
            files.add(shard_file)
            continue
        if tensor_name.startswith(prefix):
            rest = tensor_name[len(prefix):]
            dot_idx = rest.find(".")
            if dot_idx > 0:
                try:
                    li = int(rest[:dot_idx])
                    if layer_start <= li < layer_end:
                        files.add(shard_file)
                except ValueError:
                    pass
    return sorted(files)
def inspect_model_architecture(model_id, ctx_tokens=4096, fetch=True, quant=None):
    raw_mid = str(model_id or "").strip()
    local_dir = _resolve_local_hf_dir(raw_mid)
    local_gguf = _gguf.find_local_gguf(raw_mid, wanted_quant=quant)
    cfg = None
    if local_dir:
        cfg = _read_json(os.path.join(local_dir, "config.json"))
    if cfg is None and fetch and not (os.path.isfile(raw_mid) and raw_mid.lower().endswith(".gguf")):
        cfg = _fit.fetch_config(raw_mid)
    params_b = None
    source = "name-regex"
    if isinstance(cfg, dict):
        try:
            params_b, source = _fit.params_from_config(cfg)
        except Exception:
            cfg = None
    if params_b is None:
        if local_gguf:
            sz_gb = local_gguf["size_bytes"] / (1024 ** 3)
            q_name = local_gguf.get("quant") or quant or "Q4_K_M"
            bpw = _gguf.QUANT_BPW.get(q_name, 4.85)
            params_b = round(max(0.1, (local_gguf["size_bytes"] * 8.0) / (bpw * 1e9)), 2)
            source = "gguf-file"
        else:
            params_b = float(_hardware.estimate_model_params(raw_mid))
        cfg, _ = _fit._fallback_cfg(raw_mid, params_b, ctx_tokens)
    model_type = str(cfg.get("model_type") or "").strip().lower()
    if not model_type:
        low = raw_mid.lower()
        if "qwen2" in low:
            model_type = "qwen2"
        elif "qwen3" in low:
            model_type = "qwen3"
        elif "llama" in low:
            model_type = "llama"
        elif "mistral" in low:
            model_type = "mistral"
        elif "gpt2" in low:
            model_type = "gpt2"
        elif "gemma2" in low:
            model_type = "gemma2"
        elif "gemma" in low:
            model_type = "gemma"
        elif "phi" in low:
            model_type = "phi3"
    num_layers = _fit._cfg_int(cfg, "num_hidden_layers", "n_layer") or _fit._layers_from_params(params_b)
    hidden_size = _fit._cfg_int(cfg, "hidden_size", "d_model", "n_embd") or 2048
    heads = _fit._cfg_int(cfg, "num_attention_heads", "n_head") or max(4, hidden_size // 128)
    kv_heads = _fit._cfg_int(cfg, "num_key_value_heads") or heads
    head_dim = _fit._cfg_int(cfg, "head_dim") or max(32, hidden_size // max(1, heads))
    vocab_size = _fit._cfg_int(cfg, "vocab_size") or 32000
    tied = bool(cfg.get("tie_word_embeddings", False))
    weight_map = _read_safetensors_index(local_dir)
    if local_gguf:
        eff_quant = local_gguf.get("quant") or quant or "Q4_K_M"
        bpw = _gguf.QUANT_BPW.get(eff_quant, 4.85)
        gguf_bytes = int(local_gguf["size_bytes"])
    else:
        eff_quant = _gguf.normalize_quant(quant) or "Q4_K_M"
        bpw = _gguf.QUANT_BPW.get(eff_quant, 4.85)
        gguf_bytes = int(_fit.quant_size_bytes(params_b, bpw))
    fp16_bytes = int(_fit.quant_size_bytes(params_b, 16.0))
    kv_bytes_ctx = int(_fit.kv_cache_bytes_from_config(cfg, ctx_tokens))
    kv_per_tok_layer_bytes = 2 * kv_heads * head_dim * 2
    torch_supported = model_type in SUPPORTED_TORCH_PIPELINE_TYPES
    torch_reason = "Supported decoder layer pipeline" if torch_supported else f"Architecture '{model_type or 'unknown'}' requires GGUF RPC backend; PyTorch stage sharding is restricted to standard decoder blocks ({', '.join(sorted(SUPPORTED_TORCH_PIPELINE_TYPES))})."
    return {
        "model_id": raw_mid,
        "model_type": model_type,
        "params_b": params_b,
        "params_source": source,
        "num_layers": int(num_layers),
        "hidden_size": int(hidden_size),
        "num_attention_heads": int(heads),
        "num_key_value_heads": int(kv_heads),
        "head_dim": int(head_dim),
        "vocab_size": int(vocab_size),
        "tie_word_embeddings": tied,
        "quant": eff_quant,
        "bpw": bpw,
        "gguf_weight_mb": round(gguf_bytes / _MB, 2),
        "fp16_weight_mb": round(fp16_bytes / _MB, 2),
        "kv_cache_mb_at_ctx": round(kv_bytes_ctx / _MB, 2),
        "kv_bytes_per_token_per_layer": kv_per_tok_layer_bytes,
        "ctx_tokens": int(ctx_tokens),
        "local_hf_dir": local_dir,
        "local_gguf_path": local_gguf["path"] if local_gguf else None,
        "has_safetensors": bool(weight_map),
        "weight_map": weight_map,
        "supported_for_torch_pipeline": torch_supported,
        "torch_pipeline_reason": torch_reason,
        "config": cfg,
    }
def _choose_strategy(model_spec, nodes, requested_strategy):
    req = str(requested_strategy or "auto").strip().lower()
    if req in ("gguf", "gguf_rpc", "gguf_rpc_layer_split"):
        return "gguf_rpc_layer_split"
    if req in ("torch", "torch_pipeline", "torch_pipeline_parallel", "pipeline"):
        return "torch_pipeline_parallel"
    if model_spec.get("local_gguf_path"):
        return "gguf_rpc_layer_split"
    if model_spec.get("has_safetensors") and model_spec.get("supported_for_torch_pipeline"):
        all_torch = all((n.get("backends") or {}).get("torch_pipeline") for n in nodes)
        if all_torch:
            return "torch_pipeline_parallel"
    return "gguf_rpc_layer_split"
def _node_budget(node):
    hw = node.get("hw") or {}
    quota = node.get("quota") or {}
    rtt_ms = node.get("rtt_ms")
    bw_mbps = node.get("bandwidth_mbps")
    b = _cap.effective_worker_budget(hw, quota=quota, link_rtt_ms=rtt_ms, bandwidth_mbps=bw_mbps)
    backends = node.get("backends") or hw.get("backends") or {}
    has_cuda = bool(backends.get("cuda") and b["usable_vram_mb"] > 0)
    offload_devs = list(node.get("local_offload_devices") or backends.get("llama_offload_devices") or [])
    return {
        "node_id": node.get("node_id") or node.get("id") or "node",
        "name": node.get("name") or node.get("node_id") or "node",
        "role": node.get("role") or "worker",
        "rpc_endpoint": node.get("rpc_endpoint"),
        "pipeline_endpoint": node.get("pipeline_endpoint"),
        "rtt_ms": float(rtt_ms) if rtt_ms is not None else (0.2 if node.get("role") == "coordinator" else 2.0),
        "bandwidth_mbps": float(bw_mbps) if bw_mbps is not None else (10000.0 if node.get("role") == "coordinator" else 1000.0),
        "usable_vram_mb": b["usable_vram_mb"],
        "usable_ram_mb": b["usable_ram_mb"],
        "usable_threads": b["usable_threads"],
        "device_type": "cuda" if has_cuda else "cpu",
        "speed_score": b["speed_score"],
        "backends": backends,
        "local_offload_devices": offload_devs,
        "raw_vram_total_mb": b["raw_vram_total_mb"],
        "raw_ram_total_mb": b["raw_ram_total_mb"],
    }
def plan_cluster_partition(model_spec, nodes, strategy="auto", ctx_tokens=4096, parallel_slots=4):
    if not nodes:
        return {
            "feasible": False,
            "reason": "No cluster nodes available",
            "strategy": strategy,
            "stages": [],
        }
    eff_strategy = _choose_strategy(model_spec, nodes, strategy)
    is_torch = eff_strategy == "torch_pipeline_parallel"
    if is_torch and not model_spec.get("supported_for_torch_pipeline"):
        return {
            "feasible": False,
            "reason": model_spec.get("torch_pipeline_reason") or "Unsupported model architecture for torch pipeline",
            "strategy": eff_strategy,
            "stages": [],
        }
    budgets = [_node_budget(n) for n in nodes]
    total_layers = max(1, int(model_spec.get("num_layers") or 1))
    weight_mb = float(model_spec["fp16_weight_mb"] if is_torch else model_spec["gguf_weight_mb"])
    kv_total_mb = float(model_spec.get("kv_cache_mb_at_ctx") or 0.0)
    if not is_torch:
        kv_total_mb = kv_total_mb * max(1, int(parallel_slots)) * (float(ctx_tokens) / max(256, int(model_spec.get("ctx_tokens") or ctx_tokens)))
    else:
        kv_total_mb = kv_total_mb * (float(ctx_tokens) / max(256, int(model_spec.get("ctx_tokens") or ctx_tokens)))
    embed_share = 0.08 if not model_spec.get("tie_word_embeddings") else 0.04
    head_share = 0.06 if not model_spec.get("tie_word_embeddings") else 0.04
    layer_pool_mb = weight_mb * (1.0 - embed_share - head_share)
    weight_per_layer_mb = layer_pool_mb / total_layers
    kv_per_layer_mb = kv_total_mb / total_layers
    if is_torch:
        exec_nodes = [b for b in budgets if b.get("pipeline_endpoint") or b["role"] == "coordinator"]
        for b in exec_nodes:
            b["alloc_cap_mb"] = b["usable_vram_mb"] if b["device_type"] == "cuda" else b["usable_ram_mb"]
    else:
        rpc_nodes = [b for b in budgets if b.get("rpc_endpoint")]
        coord_nodes = [b for b in budgets if b["role"] == "coordinator" and b.get("local_offload_devices")]
        exec_nodes = rpc_nodes + coord_nodes
        if not exec_nodes:
            exec_nodes = [b for b in budgets if b["role"] == "coordinator"]
        for b in exec_nodes:
            if b["role"] == "worker":
                b["alloc_cap_mb"] = b["usable_vram_mb"] if (b["device_type"] == "cuda" and b["usable_vram_mb"] >= 256) else b["usable_ram_mb"]
            else:
                b["alloc_cap_mb"] = b["usable_vram_mb"] if b["usable_vram_mb"] >= 256 else b["usable_ram_mb"]
    exec_nodes = [b for b in exec_nodes if b["alloc_cap_mb"] > 64]
    if not exec_nodes:
        return {
            "feasible": False,
            "reason": "No nodes with sufficient usable memory above system reserve",
            "strategy": eff_strategy,
            "stages": [],
        }
    total_usable_mb = round(sum(b["alloc_cap_mb"] for b in exec_nodes), 1)
    scratch_per_node = max(48.0, min(256.0, weight_mb * 0.06))
    total_required_mb = round(weight_mb + kv_total_mb + scratch_per_node * len(exec_nodes), 1)
    if total_usable_mb < total_required_mb:
        return {
            "feasible": False,
            "reason": f"Cluster usable memory ({total_usable_mb} MB) is below model + KV + runtime requirement ({total_required_mb} MB).",
            "strategy": eff_strategy,
            "total_layers": total_layers,
            "total_required_mb": total_required_mb,
            "total_usable_cluster_mb": total_usable_mb,
            "stages": [],
        }
    if is_torch:
        exec_nodes.sort(key=lambda b: (0 if b["role"] == "coordinator" else 1, -b["speed_score"], -b["alloc_cap_mb"]))
    weights = [max(1.0, (b["alloc_cap_mb"] - scratch_per_node) * (1.2 if b["device_type"] == "cuda" else 1.0)) for b in exec_nodes]
    w_sum = sum(weights)
    raw_counts = [total_layers * (w / w_sum) for w in weights]
    layer_counts = [max(1, int(math.floor(c))) if total_layers >= len(exec_nodes) else 0 for c in raw_counts]
    if sum(layer_counts) == 0:
        layer_counts[0] = total_layers
    while sum(layer_counts) > total_layers:
        idx = max(range(len(layer_counts)), key=lambda i: layer_counts[i])
        if layer_counts[idx] <= 1:
            break
        layer_counts[idx] -= 1
    while sum(layer_counts) < total_layers:
        remainders = [(raw_counts[i] - layer_counts[i], i) for i in range(len(layer_counts))]
        _, best_i = max(remainders)
        layer_counts[best_i] += 1
    stages = []
    cur_layer = 0
    model_type = model_spec.get("model_type") or ""
    weight_map = model_spec.get("weight_map")
    active_nodes = []
    for idx, (b, cnt) in enumerate(zip(exec_nodes, layer_counts)):
        if cnt <= 0:
            continue
        active_nodes.append((b, cnt))
    for s_idx, (b, cnt) in enumerate(active_nodes):
        l_start = cur_layer
        l_end = cur_layer + cnt
        cur_layer = l_end
        has_embed = s_idx == 0
        has_head = s_idx == (len(active_nodes) - 1)
        w_mb = cnt * weight_per_layer_mb
        if has_embed:
            w_mb += weight_mb * embed_share
        if has_head:
            w_mb += weight_mb * head_share
        kv_mb = cnt * kv_per_layer_mb
        buf_mb = max(32.0, min(256.0, w_mb * 0.08))
        tot_alloc = round(w_mb + kv_mb + buf_mb, 2)
        shard_files = _shard_files_for_stage(weight_map, model_type, l_start, l_end, has_embed, has_head) if is_torch else []
        stages.append({
            "stage_index": s_idx,
            "node_id": b["node_id"],
            "node_name": b["name"],
            "role": b["role"],
            "endpoint": b["pipeline_endpoint"] if is_torch else b["rpc_endpoint"],
            "layer_start": l_start,
            "layer_end": l_end,
            "layer_count": cnt,
            "has_embed": has_embed,
            "has_lm_head": has_head,
            "weight_mb": round(w_mb, 2),
            "kv_cache_mb": round(kv_mb, 2),
            "buffer_mb": round(buf_mb, 2),
            "total_alloc_mb": tot_alloc,
            "capacity_mb": round(b["alloc_cap_mb"], 2),
            "device_type": b["device_type"],
            "rtt_ms": b["rtt_ms"],
            "bandwidth_mbps": b["bandwidth_mbps"],
            "tensor_split_weight": max(1, int(round(tot_alloc))),
            "shard_files": shard_files,
        })
    over_budget = [s for s in stages if s["total_alloc_mb"] > s["capacity_mb"]]
    if over_budget:
        s0 = over_budget[0]
        return {
            "feasible": False,
            "reason": f"Stage {s0['stage_index']} on {s0['node_name']} needs {s0['total_alloc_mb']} MB, exceeding usable budget {s0['capacity_mb']} MB.",
            "strategy": eff_strategy,
            "total_layers": total_layers,
            "total_required_mb": total_required_mb,
            "total_usable_cluster_mb": total_usable_mb,
            "stages": stages,
        }
    hidden = int(model_spec.get("hidden_size") or 2048)
    act_bytes = hidden * 2
    hops = max(0, len(stages) - 1)
    comm_ms = 0.0
    for s in stages[1:]:
        link_bps = max(1_000_000.0, float(s["bandwidth_mbps"]) * 1_000_000.0 / 8.0)
        comm_ms += float(s["rtt_ms"]) + (act_bytes / link_bps) * 1000.0
    if len(stages) > 1:
        comm_ms += float(stages[-1]["rtt_ms"])
    coord_budget = next((b for b in budgets if b["role"] == "coordinator"), None)
    prefer_single = bool(coord_budget and coord_budget["device_type"] == "cuda" and coord_budget["usable_vram_mb"] >= total_required_mb and comm_ms > 8.0)
    comm_warning = None
    if prefer_single:
        comm_warning = f"Model fits in coordinator VRAM ({coord_budget['usable_vram_mb']} MB >= {total_required_mb} MB) and inter-worker transfer adds ~{round(comm_ms, 1)} ms/tok; single-GPU execution will be faster."
    elif comm_ms > 40.0:
        comm_warning = f"High network latency across {hops} stage boundary(ies) (~{round(comm_ms, 1)} ms/tok overhead)."
    rpc_stages = [s for s in stages if s["role"] == "worker" and s["endpoint"]]
    local_offload_stages = [s for s in stages if s["role"] == "coordinator"]
