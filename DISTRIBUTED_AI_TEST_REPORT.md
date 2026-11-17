# SHARD Distributed AI Test Report

## 1. Test Scope & Environment

- **Branch**: `feat/distributed-ai-platform` (branched cleanly from `master`)
- **Host Hardware**: Windows (`win32`), `NVIDIA GeForce RTX 2050` (4096 MB VRAM), `16 GB` RAM (`16105 MB`), `12` logical CPU cores (`11th Gen Intel(R) Core(TM) i5-11400H @ 2.70GHz`)
- **Python & Runtimes**: Python `3.14.4`, `torch 2.14.1+cpu`, `transformers 5.20.2`, `safetensors 0.8.0`, `llama-server.exe` & `ggml-rpc-server.exe` (`llama.cpp` build `b12110`)
- **Important Honesty & Scope Disclosure**:
  All distributed integration tests below were executed across **separate independent OS worker processes** (`subprocess.Popen` + loopback TCP / encrypted tunnel) on **one physical Windows machine**. They genuinely exercise multi-process model partitioning, selective weight loading, binary tensor transmission, RPC layer offloading, and encrypted reverse-proxy tunneling without mocks, **but they do not constitute multi-machine physical network performance verification** across separate physical computers over LAN/WAN.

---

## 2. Distributed AI Test Suite (`tests_distributed_ai.py`)

Command executed:
```bash
python -m unittest tests_distributed_ai -v
```

Observed result:
```
Ran 10 tests in 23.617s

OK
```

### 2.1 Unit Tests
| Test Case | Verification Target | Result |
|---|---|---|
| `test_hardware_discovery_and_quota_enforcement` | Real `get_hardware_profile()`, CPU/backend probing, `effective_usable_resources()` clamping `vram_pct=50%`, `max_ram_gb=10`, `max_threads=6` | **PASSED** |
| `test_planner_weak_coordinator_and_unsupported_arch` | Weak coordinator (0 MB usable VRAM/RAM) + 2 workers pooling memory for 14B 48-layer Qwen2.5; exact 2-device `tensor_split_arg`; rejection of insufficient cluster; `supported_for_torch_pipeline=False` for `mamba` | **PASSED** |
| `test_worker_consent_auth_and_api_lifecycle` | `/api/cluster/create`, `/api/cluster/preview`, HTTP 403 on `consented=False`, HTTP 403 on wrong pairing code, HTTP 401 on forged `worker_token`, valid registration/heartbeat/leave | **PASSED** |
| `test_scheduler_queue_rate_limit_and_cancel` | `RequestScheduler` concurrency slots, `RateLimitError`, `QueueFullError`, and request cancellation propagation | **PASSED** |

### 2.2 Multi-Process Real Distributed Integration Tests (Zero Mocks)
| Test Case | What Actually Executed | Result |
|---|---|---|
| `test_real_multiprocess_pytorch_pipeline_parallelism` | Built a real 4-layer `Qwen2ForCausalLM` sharded across 2 `.safetensors` files (`model-00001-of-00002.safetensors`, `model-00002-of-00002.safetensors`). Spawned **2 separate OS Python worker subprocesses** (`PipelineWorkerServer`), loaded Stage 0 (`embed_tokens` + layers 0..1) in Worker 1 and Stage 1 (layers 2..3 + `norm` + `lm_head`) in Worker 2 with 0 weights on coordinator. Verified distributed forward logits matched single-process `Qwen2ForCausalLM` reference within `atol=1e-4`, ran autoregressive `generate_stream()` across TCP `SHPT` frames, and verified non-zero `transferred_bytes` | **PASSED** |
| `test_real_multiprocess_gguf_rpc_cluster_and_api` | Spawned **2 separate `ggml-rpc-server.exe` worker processes** + `llama-server.exe --rpc` running real `gpt2-medium.i1-Q6_K.gguf` (`323 MB`). Verified `CLUSTER.deploy_cluster(verify=True)` -> `READY`, `/v1/chat/completions`, `/v1/messages`, and real scheduler telemetry (`completed_requests >= 2`, `total_tokens_generated > 0`) | **PASSED** |
| `test_concurrent_multi_user_streaming_and_api_keys` | Spawned 2 `ggml-rpc-server.exe` workers with `--parallel 2` and `--cont-batching`; verified unauthenticated `/v1/chat/completions` returns HTTP `401`, and executed 4 concurrent SSE streaming requests with valid API key (`200 OK`, `data: [DONE]`) | **PASSED** |
| `test_worker_disconnect_and_rebalance_recovery` | Deployed GGUF RPC cluster across **3 `ggml-rpc-server.exe` worker processes** (`3` stages); disconnected Worker 3 -> observed `CLUSTER.state == "DEGRADED"`, re-deployed across remaining **2 workers** -> `READY` (`2` stages) | **PASSED** |
| `test_reverse_encrypted_tunnel_worker_proxy` | Connected `ClientTunnelAgent` to `HostTunnelAgent` over ChaCha20-Poly1305 encrypted TCP tunnel (Ed25519 identity); opened `HostTunnelAgent.open_worker_proxy()` to worker's loopback `PipelineWorkerServer` and exchanged authenticated `PING`/`PONG` frames over the encrypted reverse tunnel | **PASSED** |
| `test_cluster_pytorch_pipeline_via_unified_api` | Full `/v1/ping` and `/v1/chat/completions` end-to-end execution backed by `CLUSTER` running `torch_pipeline_parallel` across two worker servers | **PASSED** |

---

## 3. Empirical `llama.cpp` RPC Verification Snapshot

Prior to implementation, a direct probe of `llama-server.exe --rpc 127.0.0.1:P1,127.0.0.1:P2 -sm layer -ts 1,1` (`b12110`) with `gpt2-medium.i1-Q6_K.gguf` confirmed real memory allocation across both RPC workers:
- `RPC0 (127.0.0.1:50374)`: `186 MiB` (`128` model + `26` KV + `32` compute)
- `RPC1 (127.0.0.1:50375)`: `203 MiB` (`149` model + `22` KV + `32` compute)
- Prompt eval: `375.77 tok/s` (`82-85 ms` for ~30 tokens), token eval: `58.5-62.8 tok/s` across 2 local RPC processes.

---

## 4. Regression Verification Across Full Repository Suite

All existing SHARD test modules were executed alongside `tests_distributed_ai.py` in three clean batches:

1. **Batch 1**: `python -m unittest tests_shard tests_phase_i_product_hardening -v` -> **89 / 89 PASSED** (`13.532s`)
2. **Batch 2**: `python -m unittest tests_model_lifecycle_progress tests_phase_iii_unified_hosting tests_phase_iii_chat_security tests_distributed_ai -v` -> **45 / 45 PASSED** (`33.052s`)
3. **Batch 3**: `python -m unittest tests_cache_index tests_cli_pkg tests_constants tests_formatters tests_network_persistence_security tests_phase4_network_integration tests_shard_network tests_telemetry tests_training_ecosystem tests_validators tests_windows_subprocess_and_updater -v` -> **76 / 76 PASSED** (`25.084s`)

**Combined Verified Total**: **210 / 210 tests passed** across all suites.

*(Note: Running `python -m unittest discover -s . -p "tests_*.py"` in a single interpreter hits 2 pre-existing cross-file environment interactions: `tests_shard.py` mutates `os.environ["SHARD_DATA_DIR"]` at module import time without restoring it, which affects `tests_phase_i_product_hardening.test_06`, and `tests_model_lifecycle_progress.test_find_gguf_repo_rejects_safetensors_and_prefers_gguf_variant` makes an unmocked `_repo_exists` HTTP probe to Hugging Face when `_repo_cache` is cleared.)*

---

## 5. Known Limitations
