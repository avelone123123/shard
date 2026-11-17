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
