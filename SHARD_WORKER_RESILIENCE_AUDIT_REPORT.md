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

1. **Cancelled pipeline emitted `done`:** `PipelineCoordinator.generate_stream()` caught `RequestCancelled` and fell through to yield `{"type": "done", "done": True}`.
2. **Anthropic `/v1/messages` SSE emitted `stop_reason: "end_turn"` on cancellation:** `anthropic_sse()` unconditionally emitted `content_block_stop`, `message_delta(stop_reason="end_turn")`, and `message_stop` whenever `SCHEDULER.stream_async()` exited without an `"error"` item.
3. **Non-streaming `/v1/chat/completions` returned HTTP `200 OK` with `finish_reason: "stop"` on cancellation:** `SCHEDULER.run_sync_async()` did not distinguish cancellation from normal completion.
4. **Pre-header degraded cluster returned HTTP `200 OK` on `stream=True`:** Both `/v1/chat/completions` and `/v1/messages` returned `StreamingResponse` (HTTP 200) before checking whether the cluster was already unavailable, and `/v1/messages` prematurely sent `event: message_start` and `event: content_block_start`.

### Post-Fix SSE Verification Matrix (`test_05_sse_protocol_matrix_openai_and_anthropic`)

Verified with strict SSE frame parsers (`_parse_openai_sse`, `_parse_anthropic_sse`) enforcing OpenAI and Anthropic wire contracts:

| Failure Phase | OpenAI `/v1/chat/completions` (`stream=True`) | Anthropic `/v1/messages` (`stream=True`) | Status |
| :--- | :--- | :--- | :--- |
| Before HTTP headers (cluster already `DEGRADED`) | HTTP `503` + `Retry-After: 5` JSON `{"error": {"code": "CLUSTER_UNAVAILABLE", ...}}` | HTTP `503` + `Retry-After: 5` JSON `{"type": "error", "error": {"type": "overloaded_error", "code": "CLUSTER_UNAVAILABLE", ...}}` | **VERIFIED** |
| After headers, before first token (prefill crash) | HTTP `200` SSE: `data: {"error": {"code": "WORKER_LOST", "tokens_delivered": 0, ...}}`, `data: [DONE]`, zero `finish_reason: "stop"` | HTTP `200` SSE: `event: error` (`WORKER_LOST`, `tokens_delivered: 0`), no `content_block_start`, no `end_turn`, no `message_stop` | **VERIFIED** |
| After 1 token delivered | 1 content chunk + `data: {"error": {"code": "WORKER_LOST", "tokens_delivered": 1}}` + `data: [DONE]`, zero `stop` | `message_start`, `content_block_start`, 1 `content_block_delta`, `event: error` (`tokens_delivered: 1`), zero `end_turn` / `message_stop` | **VERIFIED** |
| After 3 tokens delivered | 3 content chunks + `data: {"error": {"code": "WORKER_LOST", "tokens_delivered": 3}}` + `data: [DONE]`, zero `stop` | `message_start`, `content_block_start`, 3 `content_block_delta`, `event: error` (`tokens_delivered: 3`), zero `end_turn` / `message_stop` | **VERIFIED** |
| Network interruption (blackhole / trickle after 1–2 tokens) | `data: {"error": {"code": "WORKER_TIMEOUT", ...}}` + `data: [DONE]`, zero `stop` | `event: error` (`WORKER_TIMEOUT`), zero `end_turn` / `message_stop` | **VERIFIED** |
| Failure during recovery (`max_recovery_attempts=1`, infeasible) | `data: {"error": {"code": "RECOVERY_FAILED", "recovery_attempted": true, ...}}` + `data: [DONE]` | `event: error` (`RECOVERY_FAILED`, `recovery_attempted: true`), zero `end_turn` / `message_stop` | **VERIFIED** |
| Client cancellation (mid-stream & non-streaming) | Stream exits with zero `finish_reason: "stop"`; non-streaming returns HTTP `499` `REQUEST_CANCELLED` | Stream exits with zero `end_turn` / `message_stop`; non-streaming returns HTTP `499` `REQUEST_CANCELLED` | **VERIFIED** |

*Note:* Official third-party `openai` and `anthropic` Python SDK packages are not installed in this environment, so verification used strict spec-aligned SSE parsers rather than importing those external SDK packages.

---

## 6. Timeout, Heartbeat & Cancellation Audit

### Parameter Table & Enforced Bounds (`shard_engine/distributed/resilience.py`)

| Setting | Default | Enforced Bounds | Interaction / Role |
| :--- | :--- | :--- | :--- |
| `connect_timeout_s` | `5.0s` | `[0.05, 300.0]` | TCP connect + SHPT auth handshake (now clamped by remaining `ctx.deadline`); also passed to GGUF `requests.Session.post()`. |
| `load_timeout_s` | `300.0s` | `[0.1, 3600.0]` | Per-stage `MSG_LOAD_STAGE` timeout; now immediately abortable via `PipelineCoordinator.stop()` / `stop_cluster()`. |
| `prefill_timeout_s` | `180.0s` | `[0.05, 3600.0]` | First `forward()` step per attempt (including resume context replay). |
| `decode_step_timeout_s` | `30.0s` | `[0.05, 3600.0]` | Subsequent single-token `forward()` steps. |
| `stall_timeout_s` | `120.0s` | `[0.05, 3600.0]` | GGUF `chat_stream_guarded()` idle read timeout AND wall-clock data-chunk idle timer (immune to `: ping` keepalive loops). |
| `release_timeout_s` | `2.0s` | `[0.05, 60.0]` | Best-effort `MSG_RELEASE_KV` timeout; dead worker is skipped in `release_request(skip_workers=...)`. |
| `request_deadline_s` | `0.0s` (disabled) | `[0.0, 86400.0]` | Combined via `resolve_deadline(request_timeout, request_deadline_s)` (`min` of finite positive values); enforced in scheduler, `connect()`, `send_frame_poll()`, `recv_frame_poll()`, and `_await_recovery()`. |
| `heartbeat_interval_s` | `2.4s` | `[0.05, 3600.0]` | Worker heartbeat cadence; propagated back in heartbeat response. |
| `suspect_missed` | `2` (`4.8s`) | `[1, 1000]` | Excludes worker from new partition plans without aborting active requests. |
| `dead_missed` | `5` (`12.0s`) | `[1, 2000]` (`> suspect_missed`) | Marks worker `FAILED`, aborts active requests on that worker, triggers `_reform_cluster()`. |
| `poll_slice_s` | `0.25s` | `[0.005, 5.0]` | Socket polling slice for send/recv cancellation & abort wakeups. |
| `monitor_tick_s` | `1.0s` | `[0.01, 60.0]` | Background monitor wake interval (lower-bounded to prevent busy loop). |
| `slow_step_warn_s` | `10.0s` | `[0.01, 3600.0]` | Flags `slow=True` and increments `slow_steps_total` without evicting healthy heartbeating workers. |

### Windows Compatibility & CPU Verification

- **Socket polling on Windows:** `pipeline_proto.py` uses `sock.settimeout(poll_slice_s)` around `sock.send()` / `sock.recv()` (which uses Winsock `select` internally in CPython), and `pipeline_worker.py` uses `select.select([conn], [], [], 1.0)` strictly on `AF_INET` TCP sockets (fully supported on Winsock).
- **No busy loop verified empirically (`test_06`):** During a `0.5s` blackholed wait with `poll_slice_s=0.05s`, measured process CPU time was `< 0.25s` across `>= 0.45s` wall time (blocking kernel wait, zero spin-loop).
- **Cancellation propagation:** Verified cancellation interrupts `connect()`/auth, `send_frame_poll()`, `recv_frame_poll()`, `load_stage()`, `_gen_lock.acquire()`, `_await_recovery()`, GGUF `chat_stream_guarded()` (forcibly closing underlying socket and `requests.Session`), and queued scheduler requests (`acquire_slot()` now raises `RequestCancelled` instead of `RuntimeError`).

---

## 7. Regression Baseline Comparison (`tests_phase_i_product_hardening.py`)

To verify whether the two failures noted in the previous report were pre-existing, `test/tests_phase_i_product_hardening.py` was executed against the actual base commit (`ff1155c`) in a clean isolated `git worktree` and against `feat/worker-failure-resilience` in the exact same Python environment:

| Run Target | Command | Exit Code | Duration | Passed / Failed | Exact Failing Assertions |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Base commit `ff1155c` (isolated worktree `%TEMP%\shard_audit_base_ff1155c`) | `python -m unittest test/tests_phase_i_product_hardening.py -v` | `1` | `7.885s` | `7 passed / 2 failed` | `test_06`: `'D:\\shard-models\\hub\\models--Qwen--Qwen2.5-Coder-1.5B-Instruct\\snapshots\\2e1fd397ee46e1388853d2af2c993145b0f1098a' != 'Qwen/Qwen2.5-Coder-1.5B-Instruct'`<br>`test_09`: `dashboard.html` bytes != `shard_engine/dashboard.html` bytes (`clusterJoinModalBackdrop` present in package copy, absent in root copy). |
| Feature branch `feat/worker-failure-resilience` (`04495bc`) | `python -m unittest test/tests_phase_i_product_hardening.py -v` | `1` | `2.966s` | `7 passed / 2 failed` | Byte-for-byte identical failure in `test_06` and identical divergence in `test_09`. |

Additionally, `git diff ff1155c..04495bc -- dashboard.html shard_engine/dashboard.html shard_engine/model_browser.py` is empty, and `git log` confirms `shard_engine/dashboard.html` was modified at `7f7d18b` (`feat/autonomous-shard-network`) while root `dashboard.html` was last modified at `3288aa1`.

**Verdict:** Both failures are **PROVEN PRE-EXISTING** and unrelated to `feat/worker-failure-resilience`. Neither test was modified or suppressed.

---

## 8. Security & Concurrency Audit Findings

| Area | Pre-Audit Vulnerability / Race on `04495bc` | Fix Applied | Verification |
| :--- | :--- | :--- | :--- |
| Policy endpoint input validation | `NaN`/`Infinity` passed `val < 0` check, crashed FastAPI with `ValueError` (HTTP 500), and permanently poisoned `POLICY` so `GET /api/cluster/resilience` also crashed. Non-dict JSON (`[1,2]`) crashed with `AttributeError`. Invalid JSON silently succeeded (`200`). `monitor_tick_s=0` and `poll_slice_s=1e-9` caused CPU busy loops. `connect_timeout_s=0` put sockets into non-blocking mode. | Added `_BOUNDS`, finite float check, boolean rejection for numeric keys, strict boolean parsing, atomic validate-then-commit `configure()`, HTTP 400 on invalid/non-object JSON in `server.py`. | **VERIFIED** (`test_07`) |
| Policy thread safety | `as_dict()`, `suspect_after_s`, `dead_after_s`, `probation_s()`, `backoff_s()` read fields without `self._lock`, permitting torn reads during concurrent `configure()`. | All policy reads and snapshots acquire `self._lock`. | **VERIFIED** (`test_07`: 8 concurrent reader/writer threads) |
| Stale heartbeat revival | `heartbeat_worker()` lacked sequence/timestamp monotonicity; a single stale heartbeat promoted a dead worker to `HEALTHY` if `probation_s <= suspect_after_s` without checking data-plane TCP reachability; `_worker_hb_loop()` kept heartbeating after local `pipeline_srv` stopped. | Added monotonic `hb_seq` and `sent_at < failed_at` rejection (`stale_heartbeats_rejected_total`), required `recovering_heartbeats >= 2` + TCP data-plane reachability probe before `RECOVERING -> HEALTHY`, and stopped `_worker_hb_loop()` when local data plane exits. | **VERIFIED** (`test_08`) |
| Read-only `/api/cluster/plan` blinding failure detection | `GET /api/cluster/plan` called `recompute_plan()`, overwriting `w["assigned_stage"] = None` on `SUSPECT` workers so subsequent failure left `CLUSTER.state == "READY"`. | `GET /api/cluster/plan` uses `recompute_plan(commit=False)`; added authoritative `self._deployed_workers` tracking and `_is_worker_serving_locked()`. | **VERIFIED** (`test_08`) |
| Stale frame & `request_id` validation | Missing `request_id`, `seq`, or `execution_id` in `MSG_FORWARD_RES` bypassed validation; worker defaulted empty `request_id` to `"default"`. | Required exact non-missing equality on `request_id`, `seq`, and `execution_id`; rejected empty/overlong `request_id` in `PipelineWorkerServer`. | **VERIFIED** (`test_08`) |
| Recovery / shutdown / lock-contention races | (1) `stop_cluster()` did not abort `PipelineCoordinator.start()` during `load_stage()`; (2) `ctx.workers` still contained the dead worker during retry backoff sleep; (3) `_reform_cluster()` `finally` cleared `_reform_active` without epoch guard; (4) `_inflight` keyed only by `request_id`; (5) blocking I/O (`probe_node_capabilities`, `measure_tcp_link`, `slots_status`) held `CLUSTER._lock` and starved heartbeats. | Fixed all 5 races: tracked `_loading_runners` in `PipelineCoordinator`, updated `ctx.workers` before `reset_for_retry()`, epoch-guarded `_reform_active`, keyed `_inflight` by `id(ctx)`, moved all blocking I/O outside `CLUSTER._lock`. | **VERIFIED** (`test_02`, `test_06`, `test_08`) |
| Log hygiene & public endpoints | `METRICS.event()` matched exact key names only, leaking `user_prompt`, `auth_token`, `pairing_code`, nested secrets, and `sk-shard-*`/`Bearer` substrings. GGUF error messages leaked internal `127.0.0.1:<port>`. | Added recursive substring sanitization + secret regex redaction (with safe allowlist for `tokens_delivered`, etc.) and sanitized GGUF error messages. Confirmed `/api/cluster/preview` and `/v1/ping` expose no secrets. | **VERIFIED** (`test_07`) |

---

## 9. Modified & Added Files During Audit

All fixes were implemented directly on `feat/worker-failure-resilience` with minimal surface area:

| File | Summary of Audit Fixes |
| :--- | :--- |
| `shard_engine/distributed/resilience.py` | Bounded finite validation (`_BOUNDS`), atomic `configure()`, thread-safe locked reads (`as_dict`, `suspect_after_s`, `dead_after_s`, `probation_s`, `backoff_s`), finite `resolve_deadline()`, recursive secret/prompt log sanitization + `sk-shard-*`/`Bearer` redaction. |
| `shard_engine/distributed/pipeline_proto.py` | `recv_exact_poll()` calls `poll()` every iteration (blocking slow-trickle bypass); added `send_exact_poll()`, `send_frame_poll()`, `send_json_frame_poll()`, `recv_json_frame_poll()`, and `client_auth_handshake_poll()`. |
| `shard_engine/distributed/pipeline_worker.py` | Tightened pre-auth timeout to 10s, validated non-empty `request_id` (`<= 128` chars), locked metric counter updates, bounded `_conn_threads` join in `stop()`. |
| `shard_engine/distributed/pipeline_coordinator.py` | Fixed `stage_index` vs `stage_idx` lookup, deadline-clamped & abortable `connect()` / `load_stage()` / `_loading_runners`, poll-sliced sends, strict non-missing `request_id`/`seq`/`execution_id` validation, `RequestCancelled` propagation (no fake `done`), post-forward `ctx.poll()`, `top_p`/`top_k`/`repetition_penalty` sampling consistency across recovery, preserved `ResumeState` on lock-wait/unavailable failures, accurate `resumed_after_failure`. |
| `shard_engine/distributed/cluster.py` | Decoupled blocking I/O (`_get_coordinator_caps` 30s cache, `measure_tcp_link`, `slots_status`) from `CLUSTER._lock`; added `_deployed_workers` & `_is_worker_serving_locked()`; `recompute_plan(commit=False)` isolation; monotonic `hb_seq` + `sent_at` + >=2-heartbeat probation + TCP reachability verification; `_worker_hb_loop` stops on dead local data plane; `_inflight` keyed by `id(ctx)`; epoch-guarded `_reform_active`; `ctx.workers` updated before `reset_for_retry()`; GGUF worker probing moved before raise; `_gguf_stream` polls `ctx` before generic `BACKEND_ERROR` and forwards sampling params. |
| `shard_engine/distributed/scheduler.py` | Queued cancellation raises `RequestCancelled` (not `RuntimeError`); locked `state` dict; explicit `"cancelled"` stream item so `run_sync_async()` raises `RequestCancelled` instead of returning HTTP 200 partial text. |
| `shard_engine/gguf_backend.py` | `chat_stream_guarded()` accepts `connect_timeout_s` and sampling params, enforces wall-clock data-chunk idle timer against SSE keepalives, and forcibly closes raw socket + `requests.Session` on cancellation. |
| `shard_engine/server.py` | Pre-header HTTP `503` check for degraded cluster on `stream=True`; lazy Anthropic `message_start`/`content_block_start` and guarded `end_turn` emission (`if completed`); clean cancellation handling (`499` sync, no fake `stop`/`end_turn` SSE); `math.isfinite` timeout extraction; sampling param forwarding; `GET /api/cluster/plan` uses `commit=False`; strict HTTP 400 JSON/dict validation on `/api/cluster/resilience/policy`; `hb_seq`/`sent_at` forwarded on `/api/cluster/workers/heartbeat`; `api_server_chat_send` routes coordinator mode without silent local fallback. |
| `test/tests_worker_resilience_audit.py` *(new)* | 8-test independent audit & regression suite covering context-replay sampling & concurrency, real multi-process `proc.kill()` recovery & error metadata, graceful shutdown + mid-generation blackhole + slow-trickle blackhole, full OpenAI/Anthropic SSE protocol matrix, timeout/keepalive/CPU-busy-loop verification, security/policy/log hygiene, and stale heartbeat/plan/frame/shutdown races. |
| `SHARD_WORKER_RESILIENCE_AUDIT_REPORT.md` *(new)* | This independent audit report. |

---

## 10. Remaining Limitations

1. **Context Replay vs. Binary KV-Cache Transfer:** `torch_pipeline_parallel` recovery reconstructs KV caches by re-prefilling `[prompt + generated_ids]`. For long generations, recovery incurs a full prefill latency spike proportional to `(prompt_len + generated_len)`.
2. **Stochastic RNG Not Checkpointed:** For `temperature > 0.05`, recovered generation continues coherently from the exact delivered prefix but does not reproduce the identical pseudo-random token sequence of an uninterrupted run.
3. **Remote Hugging Face Revision Pinning:** When `model_path_or_repo` is a remote HF repo ID (rather than a local directory), `hf_hub_download()` does not pin an immutable commit SHA across initial deploy and reform.
4. **GGUF Mid-Generation Resume Unsupported After Token 1:** `llama-server` manages KV state internally in C++; failures after `tokens_delivered > 0` terminate explicitly with structured errors rather than resuming.
5. **Synchronous Worker PyTorch Kernel Execution:** While the coordinator bounds step waits and closes sockets on timeout/cancellation, an already-executing synchronous PyTorch forward op inside `PipelineWorkerServer` finishes its current layer pass before observing the closed socket.
6. **Single Coordinator Failure Domain:** `ResumeState` lives in coordinator memory; if the coordinator process itself crashes, client connections drop and cannot resume across coordinator restart.
7. **Verification Scope Boundaries:** Official `openai`/`anthropic` Python SDK packages were not installed locally; remote GitHub Actions CI was not executed; separate physical multi-machine WAN/LAN hosts were not tested (all tests ran on localhost Windows 11 with real OS subprocesses and loopback TCP sockets).

---

## 11. Final Test Results & Capability Classification

### Executed Commands & Summary

| Run | Exact Command | Duration | Exit Code | Total | Passed | Failed | Skipped |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Worker Resilience + Independent Audit Suites | `python -m unittest test/tests_worker_resilience.py test/tests_worker_resilience_audit.py -v` | `47.764s` | `0` | `24` | `24` | `0` | `0` |
| Baseline Comparison — Base Commit (`ff1155c`, isolated worktree) | `python -m unittest test/tests_phase_i_product_hardening.py -v` | `7.885s` | `1` | `9` | `7` | `2` | `0` |
| Baseline Comparison — Feature Branch (`04495bc`) | `python -m unittest test/tests_phase_i_product_hardening.py -v` | `2.966s` | `1` | `9` | `7` | `2` | `0` |
| Full Repository Regression Suite | `python -m unittest discover -s test -p "tests_*.py" -v` | `168.983s` (`178.25s` wall) | `1` | `248` | `245` | `2` (pre-existing) | `1` |

### Full Suite Module-by-Module Breakdown

| Test Module | Passed | Failed | Skipped | Scope / Notes |
| :--- | :--- | :--- | :--- | :--- |
| `tests_worker_resilience.py` | `16` | `0` | `0` | Original resilience scenarios (disconnect, load/prefill crash, decode resume, heartbeat loss, blackhole, slow worker, SSE errors, concurrent requests, reconnect probation, stale frames, cancellation, GGUF stall/truncation, request deadline). |
| `tests_worker_resilience_audit.py` | `8` | `0` | `0` | Independent audit suite (context-replay sampling & concurrency, real multi-process `proc.kill()` recovery & error metadata, graceful leave + mid-gen blackhole + slow trickle, full OpenAI/Anthropic SSE matrix, timeout/keepalive/CPU-busy-loop check, security/policy/log hygiene, stale heartbeat/plan/frame/shutdown races). |
| `tests_distributed_ai.py` | `10` | `0` | `0` | Multi-process PyTorch pipeline, real GGUF RPC cluster + OpenAI/Anthropic APIs, tunnel worker proxy, concurrent multi-user streaming, rebalance recovery, scheduler/queue/cancel. |
| `tests_global_network.py` | `7` | `0` | `0` | Discovery, catalog, consent, distributed pipeline, concurrent scheduler streaming, failover. |
| `tests_autonomous_network.py` | `7` | `0` | `0` | Transport priority, Radmin credentials, doctor endpoints. |
| `tests_shard.py` | `80` | `0` | `0` | Core engine, LAN, hardware, settings, GGUF utilities. |
| `tests_cli_pkg.py` | `14` | `0` | `1` | `test_wheel_contents_when_built` skipped (`Wheel not built in dist/ yet`). |
| `tests_shard_network.py` | `6` | `0` | `0` | CLI network, identity, discovery, relay isolation. |
| `tests_model_lifecycle_progress.py` | `16` | `0` | `0` | Model lifecycle and progress tracking. |
| `tests_network_persistence_security.py` | `10` | `0` | `0` | Auth gates, persistence, rate limits. |
| `tests_phase4_network_integration.py` | `9` | `0` | `0` | End-to-end network integration. |
