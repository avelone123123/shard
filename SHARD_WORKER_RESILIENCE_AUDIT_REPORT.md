# SHARD — Independent Worker Failure Resilience Audit Report

**Audit Date:** 2026-11-23  
**Auditor Role:** Independent Senior Distributed Systems Auditor & Reliability Engineer  
**Environment:** Windows 11 x64, Python 3.14.4, PyTorch 2.14.1+cpu, Transformers 5.20.2, FastAPI 0.141.1, httpx 0.28.1, requests 2.34.2, local `llama-server` / `ggml-rpc-server`  
**Execution Scope:** Localhost (`127.0.0.1`) multi-process OS workers, loopback TCP `SHPT` servers, scripted blackhole/trickle/stale peers, local `ThreadingHTTPServer` GGUF SSE mocks, FastAPI `TestClient`, and isolated Git worktree baseline verification. No remote CI workflow and no separate physical machines were executed during this audit.

---

## 1. Actual Git Branch & Commit Information

Independent inspection of the local repository at audit start revealed material discrepancies between the previous report's branch/commit state and actual Git refs:

| Git Attribute | Observed Value at Audit Start | Notes |
| :--- | :--- | :--- |
| Initially checked-out branch | `master` | Not `feat/worker-failure-resilience`. Switched to `feat/worker-failure-resilience` before applying audit fixes so `master` was left untouched. |
| Current `HEAD` commit | `04495bc1efd9c4daf886b98122c056a83312f076` | `docs(resilience): update README.md (3/3) [292/292]` |
| `feat/worker-failure-resilience` | `04495bc1efd9c4daf886b98122c056a83312f076` | Identical to `master`. |
| `origin/feat/worker-failure-resilience` | `04495bc1efd9c4daf886b98122c056a83312f076` | Already pushed to GitHub (`https://github.com/avelone123123/shard.git`). |
| `origin/master` | `04495bc1efd9c4daf886b98122c056a83312f076` | **Important:** The 292-commit series had already been fast-forwarded onto both `master` and `origin/master` before this audit began. |
| Working tree status at start | Clean (`0` uncommitted changes) | No existing work was overwritten or discarded. |
| Reported commit `42d01ed` | Exists locally (`42d01edba33e3300eb8697e1175ffe7e08d603a1`) | **Dangling/orphaned commit** (`git branch -a --contains 42d01ed` returned empty). Its tree SHA (`89def0c17b0a692a337f6981e36adb1de897f69d`) is byte-for-byte identical to `04495bc` (`git diff 42d01ed 04495bc` was empty). Commit `42d01ed` was subsequently split into 292 micro-commits (`f246e23`..`04495bc`) on top of `ff1155c`. |
| Actual base commit | `ff1155cd4d55d57a196db3b23c745d7d9be5ec36` | `feat/autonomous-shard-network` (`docs(network): update README.md (8/8) [251/251]`). `git merge-base 42d01ed 04495bc` = `ff1155c`. |
| Diff (`ff1155c..04495bc`) | 13 files changed, `+2409 / -274` lines | Matches `git show --stat 42d01ed` exactly. |
| Existing worktrees | `swarm-autonet-67ed27f7` at `7fad0df` | Preserved untouched. Temporary worktree created for Section 6 baseline verification was cleanly removed after comparison. |

---

## 2. Independent Assessment of Every Major Implementation Claim

Each major claim in `SHARD_WORKER_FAILURE_RESILIENCE_REPORT.md` was verified against source code and live execution. Multiple hidden defects were reproduced on unmodified commit `04495bc` and subsequently fixed on `feat/worker-failure-resilience`.

| Capability / Claim | Pre-Audit Finding on `04495bc` | Post-Fix Audit Status |
| :--- | :--- | :--- |
| Per-phase timeouts (`connect`, `load`, `prefill`, `decode`, `release`, `stall`, `request_deadline`) | Partially flawed: `connect()` ignored `request_deadline_s`, `send_fn()` used full step timeout without cancellation polling, `recv_exact_poll()` never polled on slow-trickling peers, `stop_cluster()` could not abort `load_stage()` (`self.runners` empty during load), GGUF keepalive comments defeated `stall_timeout_s`. | **VERIFIED** (all fixed and regression-tested) |
| 4-state worker liveness FSM (`HEALTHY`/`SUSPECT`/`FAILED`/`RECOVERING`) & probation | **Defective on `04495bc`:** (1) Single stale heartbeat revived a dead worker to `HEALTHY` if `probation_s <= suspect_after_s` without TCP verification; (2) `GET /api/cluster/plan` wiped `assigned_stage` on `SUSPECT` workers, blinding failure detection; (3) `probe_node_capabilities()` / `measure_tcp_link()` / `slots_status()` ran while holding `CLUSTER._lock`, starving heartbeats for 0.8s–1.9s and falsely failing healthy workers under tight intervals. | **VERIFIED** (all fixed and regression-tested) |
| `torch_pipeline_parallel` mid-generation recovery (`ResumeState`) | Worked for single-retry greedy in-process stop; **defective on `04495bc`** for: (1) multi-retry lock-wait / `not is_running()` failures erasing `ResumeState`; (2) `stage_idx` vs `stage_index` key mismatch leaving `stage=None` on real planned clusters; (3) `top_p`/`top_k`/`repetition_penalty` not forwarded; (4) `resumed_after_failure=True` on Case B restart. | **VERIFIED WITH LIMITATIONS** (context replay works deterministically for greedy/`top_k=1`; stochastic RNG not checkpointed; HF remote revision not SHA-pinned) |
| GGUF RPC mid-generation failure handling | Worked for mock stall/truncation; **defective on `04495bc`** because `_probe_assigned_workers()` ran after `if not can_restart: raise`, so failures after token 1 never probed dead RPC workers, and `_gguf_stream` masked `WORKER_LOST` aborts as generic `BACKEND_ERROR`. | **VERIFIED WITH LIMITATIONS** (fixed probing & abort precedence; mid-generation resume after token 1 remains unsupported by design on `llama-server`) |
| OpenAI & Anthropic streaming error contracts | **Defective on `04495bc`:** (1) `PipelineCoordinator.generate_stream()` yielded `{"type": "done"}` on `RequestCancelled`; (2) `/v1/messages` emitted `stop_reason: "end_turn"` on cancellation; (3) non-streaming sync APIs returned HTTP `200 OK` with `finish_reason: "stop"` on cancellation; (4) pre-header degraded cluster returned HTTP `200` + `message_start` instead of HTTP `503`. | **VERIFIED WITH LIMITATIONS** (all defects fixed and verified with strict SSE parsers; official `openai`/`anthropic` SDK packages are not installed in this environment) |
| Stale frame & execution ID rejection | **Defective on `04495bc`:** `out_meta.get(...) is not None` guards allowed responses omitting `request_id`, `seq`, or `execution_id` to pass unchecked; worker defaulted empty `request_id` to `"default"`. | **VERIFIED** (exact match required; missing fields rejected as `STALE_RESPONSE`) |
| Policy validation, thread-safety & log sanitization | **Defective on `04495bc`:** `{"decode_step_timeout_s": "nan"}` crashed Starlette (HTTP 500) and permanently poisoned `POLICY`; list body crashed with `AttributeError`; `monitor_tick_s=0` created a CPU busy loop; `configure()` partially mutated on error; `as_dict()` lacked lock; `METRICS.event()` leaked `user_prompt`, `auth_token`, `pairing_code`, and bearer/token substrings. | **VERIFIED** (bounded finite validation, atomic updates, locked reads, recursive log redaction) |
| Epoch & shutdown concurrency (`_cluster_epoch`, `_deploy_lock`) | **Defective on `04495bc`:** `stop_cluster()` could not abort `PipelineCoordinator.start()` during `load_stage()`; `ctx.workers` retained dead worker during retry backoff sleep; `_reform_cluster()` `finally` cleared `_reform_active` without epoch guard; `_inflight` keyed only by `request_id`. | **VERIFIED** (all races fixed and stress-tested) |
| Pre-existing classification of `tests_phase_i_product_hardening.py` `test_06` & `test_09` | Claimed pre-existing without baseline proof. | **VERIFIED** (independently proven identical on isolated base worktree `ff1155c`) |
| CI verification on GitHub Actions | Explicitly not run in previous report. | **NOT TESTED** (no remote CI executed) |
| Multi-machine physical WAN/LAN failure testing | Explicitly not run in previous report. | **NOT TESTED** (localhost loopback & local OS subprocesses only) |

---

## 3. Recovery Mechanism Details (`ResumeState` & `torch_pipeline_parallel`)

### Exact Mechanism Classification

SHARD's recovery mechanism in `shard_engine/distributed/pipeline_coordinator.py` and `shard_engine/distributed/cluster.py` is **KV-cache reconstruction through full-context prefill replay** — it is **NOT** real binary KV-cache restoration (no K/V attention tensors are serialized, transferred, or restored across workers).

Specifically, on recovery after `k >= 1` tokens were generated:
1. `PipelineCoordinator.generate_stream()` records `ResumeState(generated_ids, delivered_text)` on the raised `InferenceFailure`, releases KV caches on surviving stages (`release_request(rid, skip_workers=[failed_worker])`), and releases `_gen_lock`.
2. `ClusterManager._reform_cluster()` recomputes a partition plan across surviving `HEALTHY` nodes + Coordinator (when feasible) and deploys a new `PipelineCoordinator` with fresh `PipelineStageModule` instances.
3. `ClusterManager.generate_stream()` calls the new `PipelineCoordinator.generate_stream(..., resume=ResumeState)`.
4. Inside `generate_stream()`, `cur_ids = torch.cat([input_ids, torch.tensor([generated_ids], dtype=torch.long)], dim=-1)` is passed through a single `phase="prefill"` forward pass (`use_cache=True`) across all stages, allocating fresh `DynamicCache` objects populated for positions `0 .. len(prompt_ids) + len(generated_ids) - 1`.
5. `prev_text` is initialized to `resume.delivered_text`, `next_id` is sampled from `logits[0, -1, :]`, and `delta = full_txt[len(prev_text):]` yields only newly generated suffix text for the remaining `max_new - len(generated_ids)` steps.

### 10-Point Verification Checklist

1. **Same model weights and revision (`VERIFIED WITH LIMITATIONS`):** `ClusterManager.generate_stream()` verifies `backend.model_path_or_repo == new_backend.model_path_or_repo`. For local directories (`local_hf_dir`), all stages load the exact same local safetensors files. For remote Hugging Face repository IDs, `pipeline_worker._download_or_locate_files()` calls `hf_hub_download(model_path_or_repo, fn)` without pinning a Git `revision` SHA, so an upstream repo update mid-flight is not pinned unless resolved locally.
2. **Tokenizer and generation configuration compatibility (`VERIFIED`):** Reloaded from identical `model_path_or_repo` (`AutoTokenizer`, `AutoConfig`, `eos_token_id`, `pad_token_id`); loop bound `max(0, max_new - len(generated_ids))` preserves total `max_tokens`.
3. **Previously delivered token IDs preserved exactly (`VERIFIED`):** Preserved in `ResumeState.generated_ids`. Fixed a confirmed defect where a second-attempt failure during `_gen_lock` acquisition or `not self.is_running()` left `f.resume = None` and caused `cluster.py` to overwrite `resume` with an empty `ResumeState()`.
4. **No previously delivered token emitted a second time (`VERIFIED`):** `prev_text` tracks decoded output across recovery; if `ctx.tokens_delivered > 0` without non-empty `resume.generated_ids`, recovery aborts with `RECOVERY_FAILED` instead of restarting from token 0. Also fixed `resumed_after_failure` reporting `True` on empty Case B restarts. (Edge-case limitation: if a BPE tokenizer emits an incomplete multi-byte UTF-8 replacement character `\ufffd` immediately prior to failure and the next token completes the character, `full_txt[len(prev_text):]` string-slicing cannot retroactively edit the already-streamed `\ufffd`.)
5. **Next generated token computed from complete existing context (`VERIFIED`):** Prefill runs `torch.cat([input_ids, generated_ids])` with monotonic `position_ids`, computing logits over the full prefix. Confirmed bit-for-bit identical output vs. uninterrupted baseline under both greedy (`temperature=0.0`) and `repetition_penalty=1.45` (`tests_worker_resilience_audit.py::test_01`, `test_02`).
6. **Sampling parameters (`temperature`, `top_p`, `top_k`, `repetition_penalty`) consistency (`VERIFIED`):** On `04495bc`, only `temperature` was forwarded (`top_p=0.95` hardcoded; `top_k` and `repetition_penalty` absent). Fixed across `server.py`, `cluster.py`, `pipeline_coordinator.py`, and `gguf_backend.py`; `repetition_penalty` is applied over the complete `generated_ids` history including tokens generated prior to recovery.
7. **RNG state handling (`VERIFIED WITH LIMITATIONS`):** PyTorch global RNG state (`torch.get_rng_state()` / per-request `torch.Generator`) is **not** saved or restored across recovery. Greedy decoding (`temperature <= 0.05`) and `top_k=1` produce identical continuations; stochastic sampling (`temperature > 0.05`) produces a valid context-consistent continuation but not the identical pseudo-random trajectory of an uninterrupted run.
8. **Worker repartitioning state integrity (`VERIFIED`):** Repartitioning a 4-layer Qwen2 model from `[worker1(L0-1), worker2(L2-3)]` or `[coord, worker1, worker2]` to `[coord, worker1]` preserves exact greedy and penalized outputs (`test_01`, `test_02`). Note: if a layer migrates between CUDA `float16` and CPU `float32` during repartitioning, minor floating-point associativity differences can alter borderline logits.
9. **KV-cache memory allocation and release (`VERIFIED`):** Fresh `DynamicCache` allocated per unique `rid` (`pipe-<hex10>`); `finally` calls `release_request(rid, skip_workers=[failed_worker])`, and `PipelineWorkerServer._handle_conn()` purges `owned_rids` on disconnect. Verified `active_kv_sessions == 0` on surviving workers after every failure and recovery test.
10. **Concurrent request recovery isolation (`VERIFIED`):** Each `generate_stream()` invocation holds independent `ExecutionContext`, `ResumeState`, and `rid`, and `_inflight` is now keyed by `id(ctx)`. Verified in `test_01` with two concurrent requests using distinct prompts and sampling settings during mid-generation failure.

### Differences Between Uninterrupted Generation and Recovered Generation

- **Latency spike on recovery:** Rebuilding KV caches requires re-prefilling `[prompt + generated_ids]` ($O((P + K)^2)$ attention cost) plus cluster reform and stage weight loading time.
- **Stochastic divergence:** Because RNG state is not checkpointed, `temperature > 0.05` trajectories diverge statistically after the failure point while remaining coherent with the exact delivered prefix.
- **Numerical precision shift on heterogeneous failover:** Moving layers from GPU `float16` to CPU `float32` (or vice versa) can introduce small logit differences.
- **Telemetry accounting:** `ttft_ms` and `compute_ms` in the final `done` event reflect the recovered `PipelineCoordinator` attempt, while `usage.completion_tokens` reflects the complete `len(generated_ids)`.

---

## 4. Real Mid-Generation Failure Test Results

Implemented and executed real end-to-end mid-generation failure tests in `test/tests_worker_resilience_audit.py`:

| Scenario | Test Method | Fault Mechanism | Observed Result | Classification |
| :--- | :--- | :--- | :--- | :--- |
| Multi-process abrupt kill + Case A recovery | `test_02_real_multiprocess_abrupt_kill_mid_generation_recovery` | Two real OS subprocess `PipelineWorkerServer` workers + Coordinator; 2 tokens delivered; `proc2.kill()` (`TerminateProcess`) | Detected `WORKER_LOST` on `proc-w2`; `proc-w1` stayed `HEALTHY` (`fail_count == 0`, process still running); reformed cluster; replayed `[prompt + generated_ids]`; exact baseline text delivered with zero duplicates, `resumed_after_failure == True`, follow-up request returned HTTP `200`, `active_requests == 0`, `inflight == []`. | **VERIFIED** |
| Multi-process abrupt kill (unrecoverable) | `test_03_real_multiprocess_abrupt_kill_unrecoverable_error_fields` | Two real OS subprocess workers (`coordinator_contributes=False`, `enable_resume=False`); 2 tokens delivered; `proc2.kill()` | Terminal `InferenceFailure`: `code="WORKER_LOST"`, `request_id="sreq-audit-kill"`, `worker_id="pw-b"`, `stage=1` (fixed from `None`), `tokens_delivered=2`; healthy `pw-a` remained `HEALTHY`; clean teardown. | **VERIFIED** |
| Graceful worker shutdown mid-generation | `test_04_graceful_shutdown_blackhole_and_slow_trickle_mid_generation` | `CLUSTER.leave_worker("gw2", token)` + `s2.stop()` after 2 tokens | Cleanly detected `WORKER_LOST`, reformed onto `[coordinator, gw1]`, produced exact baseline tokens, `gw1` stayed `HEALTHY`. | **VERIFIED** |
| Mid-generation TCP blackhole (socket stays open) | `test_04` | `_AuditPeerServer(mode="blackhole_after", good_forwards=2)` via `/v1/chat/completions` (`stream=True`) | Delivered 2 tokens, tripped `decode_step_timeout_s=0.35s` in `< 2.0s`, emitted OpenAI SSE error `WORKER_TIMEOUT`, `worker_id="w_bh_mid"`, `stage=0`, `tokens_delivered=2`, zero `finish_reason: "stop"`, marked `w_bh_mid` `FAILED`. | **VERIFIED** |
| Mid-generation slow-trickle blackhole (1 byte / 30ms) | `test_04` | `_AuditPeerServer(mode="trickle_after", good_forwards=1)` via `/v1/messages` (`stream=True`) | Delivered 1 token, tripped `decode_step_timeout_s=0.35s` in `< 2.0s` (proving `recv_exact_poll()` fix), emitted Anthropic `event: error` (`WORKER_TIMEOUT`), zero `end_turn`. | **VERIFIED** |

**Critical Concurrency Defect Discovered During Multi-Process Testing:** Running `test_02` and `test_03` exposed a hidden lock-contention defect in `cluster.py`: `recompute_plan()`, `preview_cluster()`, and `status()` called `_cap.probe_node_capabilities()` (spawning `llama-server --list-devices`, taking 0.8s–1.9s on Windows), `register_worker()` called `_cap.measure_tcp_link(..., timeout=1.5)`, and `status()` called `gb.slots_status()` **while holding `CLUSTER._lock`**. Under tight heartbeat intervals (`heartbeat_interval_s=0.3`), incoming heartbeats blocked on `CLUSTER._lock` and healthy workers were falsely transitioned to `SUSPECT`/`FAILED`. Moving all blocking subprocess/network I/O outside `CLUSTER._lock` and caching coordinator capabilities for 30s eliminated the starvation completely.

---

## 5. Streaming Protocol Audit (OpenAI & Anthropic SSE)

### Confirmed Pre-Audit Protocol Violations (Reproduced on `04495bc`)
