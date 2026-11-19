# SHARD — Worker Failure Resilience & Fault-Tolerant Inference Report

## 1. Root-Cause Analysis of Original Failure-Handling Behavior

An inspection of the original distributed runtime (`cluster.py`, `pipeline_coordinator.py`, `pipeline_worker.py`, `pipeline_proto.py`, `scheduler.py`, `gguf_backend.py`, and `server.py`) revealed nine concrete failure-handling defects when a Worker disconnected, crashed, stalled, or became unreachable mid-generation:

1. **Single Fixed Socket Timeout Without Cancellation Polling (`pipeline_coordinator.py`, `pipeline_proto.py`):** `_RemoteStageClient` created sockets with a hardcoded `timeout=60.0` shared across connect, load, prefill, and decode phases. `recv_exact()` blocked indefinitely up to 60s without checking `cancel_check` or heartbeat failure transitions; a client disconnect or heartbeat death could not wake an active `forward()` call.
2. **Heartbeat Liveness Was Binary and Unbounded During In-Flight Calls (`cluster.py`):** `_alive_worker_records()` and `_monitor_loop()` checked only `(now - last_heartbeat) <= 12.0` (`HEARTBEAT_TIMEOUT_S`). There was no `SUSPECT` state, no configurable intervals/thresholds, no probation on reconnect, and no mechanism to abort active `generate_stream()` calls when a worker stopped sending heartbeats.
3. **Silent Local Fallback on Degraded Coordinator (`server.py`):** `_inference_stream_factory()` checked `if CLUSTER.is_ready():` and silently fell through to standalone `engine_instance` whenever the cluster was `DEGRADED` or `LOADING`. Clients received generic HTTP 500 errors (`"Модель не загружена"`) instead of structured `503 CLUSTER_UNAVAILABLE` errors.
4. **Raw Error Text Injected Into Token Stream (`cluster.py`, `server.py`):**
   - For GGUF (`gguf_backend.py`), `chat_stream()` yielded `("error", str(e))`, and `ClusterManager.generate_stream()` yielded `{"type": "content", "token": "[Cluster inference error: ...]", "done": False}` followed by a normal `{"type": "done"}` event (HTTP 200 / `finish_reason: "stop"`).
   - For Anthropic `/v1/messages` SSE streaming, errors after headers were missing structured handling and emitted a fake `stop_reason: "end_turn"` frame after `event: error`.
   - For non-streaming `SCHEDULER.run_sync_async()`, partial tokens suppressed errors (`if err_msg and not content_chunks:`), returning truncated completions as HTTP 200 OK.
5. **GGUF Read Timeout & Stream Truncation Masked (`gguf_backend.py`):** `chat_stream()` passed a fixed `timeout=(5, 600)` to `requests.post()`, holding requests open for 10 minutes on a stalled RPC backend, and treated unexpected stream EOF without `[DONE]` identically to clean completion. Furthermore, `requests.iter_lines()` raises `requests.exceptions.ConnectionError` wrapping `urllib3.exceptions.ReadTimeoutError` during mid-stream stalls rather than `requests.exceptions.ReadTimeout`.
6. **In-Flight Recovery Was Not Implemented During Generation (`cluster.py`):** `_monitor_loop()` only rebuilt the cluster after polling detected dead heartbeats (`2.0s` wake loop + `12.0s` heartbeat window) and blindly tore down backends without saving checkpoints (`generated_ids`, `delivered_text`) or coordinating with active generation streams.
7. **Concurrent Reform / Shutdown Race (`cluster.py`):** Manual `deploy_cluster()` and background `_reform_cluster()` lacked mutual exclusion (`_deploy_lock`) and generation epochs (`_cluster_epoch`), and `stop_cluster()` did not join `_reform_thread` or abort in-flight startup backends. A late `_reform_cluster()` thread could overwrite `IDLE` with `DEGRADED` or spawn orphan processes after `stop_cluster()`.
8. **Worker Connection & KV-Cache Resource Leaks (`pipeline_worker.py`, `scheduler.py`):**
   - `PipelineWorkerServer._handle_conn()` retained KV cache entries (`self._kv_caches[rid]`) indefinitely if a coordinator crashed before sending `MSG_RELEASE_KV`, and `_conn_threads` grew unbounded without pruning finished threads.
   - `RequestScheduler.cancel_request()` ignored queued requests in `_waiting`, did not notify `_cond`, and `_worker()` could block up to 10s in `q.put().result(timeout=10.0)` or raise `RuntimeError: Event loop is closed` with unawaited coroutine warnings when clients disconnected cleanly.
9. **No Stale Frame / Execution ID Validation (`pipeline_coordinator.py`, `pipeline_worker.py`):** `forward()` checked `mtype == MSG_FORWARD_RES` without validating sequence numbers or execution IDs, risking frame mixups across retries or reconnections.

---

## 2. Implemented Architecture

### A. Configurable Policy, Structured Failures & Observability (`shard_engine/distributed/resilience.py`)

- **`ResiliencePolicy` (`POLICY` singleton):** Configurable in-process via `POLICY.configure(...)`, API `POST /api/cluster/resilience/policy`, or `SHARD_RESILIENCE_*` environment variables:
  - Liveness control plane: `heartbeat_interval_s` (`2.4s`), `suspect_missed` (`2` -> `4.8s`), `dead_missed` (`5` -> `12.0s`), `monitor_tick_s` (`1.0s`), `recovery_probation_s` (`5.0s`, exponential backoff on repeated failures up to `600s`).
  - Data plane timeouts (distinct per phase): `connect_timeout_s` (`5.0s`), `load_timeout_s` (`300.0s`), `prefill_timeout_s` (`180.0s`), `decode_step_timeout_s` (`30.0s`), `release_timeout_s` (`2.0s`), `poll_slice_s` (`0.25s`), `slow_step_warn_s` (`10.0s`), `stall_timeout_s` (`120.0s`), and optional end-to-end `request_deadline_s` (`0.0` = disabled/use scheduler timeout; when positive, `resolve_deadline()` enforces `min(request_timeout, request_deadline_s)`).
  - Recovery budget: `max_recovery_attempts` (`1`), `recovery_wait_s` (`60.0s`), `retry_backoff_s` (`0.5s`, exponential up to `retry_backoff_max_s=30.0s`), `enable_resume` (`True`).
- **`InferenceFailure` & `RequestCancelled`:** Structured exception carrying `code`, `message`, `request_id`, `worker_id`, `stage`, `phase`, `retryable`, `recovery_attempted`, `cluster_available`, `tokens_delivered`, HTTP status mapping (`503`, `504`, `502`, `499`, `500`), and internal `ResumeState`.
- **Execution Tracking (`ExecutionContext`):** Tracks `request_id`, per-attempt `execution_id`, `attempt`, participating `workers` set, `stage_map`, `tokens_delivered`, `phase`, `deadline`, and thread-safe first-writer-wins `abort(failure)`.
- **`Metrics` (`METRICS` singleton):** Thread-safe counters (`worker_failures_total`, `heartbeat_missed_total`, `heartbeat_loss_total`, `worker_reconnects_total`, `slow_steps_total`, `generations_aborted_total`, `generations_cancelled_total`, `requests_failed_total`, `recovery_attempts_total`, `recovery_success_total`, `recovery_failed_total`, `retries_total`, `stale_responses_rejected_total`, `reform_*`), timing distributions (`failure_detection_ms`, `request_cleanup_ms`, `reform_ms`), and ring-buffered JSON logs (`shard.resilience`) that strip sensitive keys (`prompt`, `messages`, `content`, `token`, `worker_token`, `api_key`, `authorization`, `key`, `secret`).

### B. Worker Liveness State Machine (`cluster.py`)

```
                         missed >= suspect_missed
   +-------------------------------------------------------------------+
   |                                                                   v
[ HEALTHY ] <----- heartbeat arrives --------- [ SUSPECT ] (excluded from NEW plans)
   ^                                                |
   |  heartbeat stays healthy                       | missed >= dead_missed
   |  for probation_s(fail_count)                   | OR data-plane lost/timeout
   |                                                v
[ RECOVERING ] <--- heartbeat resumes --------- [ FAILED ] (aborts active requests)
```

- **Slow vs. Dead Worker Distinction:** A slow worker whose heartbeats arrive on schedule (and whose steps finish inside `prefill_timeout_s` / `decode_step_timeout_s`) remains `HEALTHY`; steps slower than `slow_step_warn_s` increment `slow_steps_total`, emit `slow_worker_step` warnings, and set `slow=True` + `last_step_ms` in worker status without evicting the worker.
- **Data-Plane Open-Socket Hang Detection:** `_RemoteStageClient._roundtrip()` sets `socket.settimeout(poll_slice_s)` and calls `proto.recv_frame_poll(sock, poll)`. Every `poll_slice_s`, `poll()` checks `ctx.poll()` (client cancellation, coordinator abort, and `request_deadline_s`) AND step deadline (`prefill_timeout_s` / `decode_step_timeout_s`). Even if the TCP connection remains open (half-open blackhole), the request terminates cleanly via either step timeout (`WORKER_TIMEOUT`) or heartbeat expiration (`WORKER_LOST`), whichever occurs first.
- **Worker Restart Re-Registration Guard:** If an already-assigned worker crashes and restarts quickly (calling `register_worker` with the same `worker_id` before heartbeats expire), `register_worker()` invokes `_handle_assigned_worker_lost()` to abort in-flight requests using its old stage state before accepting the new endpoints.

### C. Recovery Policy (`cluster.py`, `pipeline_coordinator.py`)

1. **Case A — State-Preserving Resume (`torch_pipeline_parallel` with `enable_resume=True`):**
   - When a worker fails after `k >= 1` tokens were streamed, `PipelineCoordinator.generate_stream()` captures `ResumeState(generated_ids, delivered_text)` in the raised `InferenceFailure`, releases KV caches on surviving runners (skipping the failed `worker_id` so cleanup never stalls), and releases `_gen_lock`.
   - `ClusterManager.generate_stream()` checks `attempt < max_recovery_attempts` and waits up to `recovery_wait_s` for `_reform_cluster()` to rebuild a healthy `PipelineCoordinator` onto the surviving nodes (with `same_model == True`).
   - Generation resumes via `PipelineCoordinator.generate_stream(..., resume=ResumeState)` by prefilling `torch.cat([prompt_ids, generated_ids])` to reconstruct KV caches on the healthy stages, setting `prev_text = delivered_text`, and yielding only newly sampled suffix tokens. Already-delivered tokens are never re-emitted.
2. **Case B — Safe Restart Before First Token (`tokens_delivered == 0`):**
   - If failure occurs during model prefill (or GGUF startup/first token before any token is yielded to the client), restarting from the prompt on the re-formed cluster produces zero duplicate tokens. Bounded by `max_recovery_attempts` and exponential `backoff_s`.
3. **Case C — Explicit Structured Error When Unrecoverable:**
   - Triggered when `_reform_infeasible` (insufficient surviving cluster memory), `recovery_wait_s` expires, `attempt >= max_recovery_attempts`, `enable_resume=False` with `tokens_delivered > 0`, or on `gguf_rpc_layer_split` after partial tokens have already been streamed (`tokens_delivered > 0`).
   - `_await_recovery()` exits immediately when `_reform_infeasible` is set or no reform is pending/active after `0.35s`, preventing multi-second hangs.
   - Emits `InferenceFailure` with structured JSON payload (`code`, `message`, `request_id`, `worker_id`, `stage`, `phase`, `retryable`, `recovery_attempted`, `cluster_available`, `tokens_delivered`).

### D. Cascade Prevention, Concurrency, and Resource Cleanup

- **Epoch & Deploy Serialization (`_cluster_epoch`, `_deploy_lock`):** `stop_cluster()` increments `_cluster_epoch`, aborts `_starting_backend`, aborts all `_inflight` executions with `CLUSTER_UNAVAILABLE`, closes execution backends, and joins `_monitor_thread` + `_reform_thread`. Any stale `_reform_cluster()` or `deploy_cluster()` checks `_cluster_epoch` before mutating state or committing backends.
- **Broadcast Abort Across Active Requests:** `_abort_executions(worker_id, failure)` aborts every `ExecutionContext` in `_inflight` whose `workers` set includes `worker_id` (both the request currently holding `_gen_lock` and queued requests waiting on `_gen_lock` or recovery).
- **Healthy Worker Isolation:** `_alive_worker_records()` filters out `SUSPECT`, `FAILED`, and `RECOVERING` workers for new partition plans while leaving healthy workers registered and `HEALTHY` (`fail_count == 0`). `_reform_cluster()` re-plans onto remaining `HEALTHY` nodes + Coordinator when memory suffices.
- **Connection & Memory Cleanup:**
  - `PipelineWorkerServer._handle_conn()` tracks `owned_rids` per TCP socket and purges their KV caches in `finally:` upon socket disconnect/crash; `stop()` closes all `_active_conns` immediately and prunes `_conn_threads`.
  - `_RemoteStageClient._roundtrip()` drops/closes `self.sock` on every timeout, error, cancellation, or stale response so delayed bytes from abandoned requests cannot leak into later reads.
  - `RequestScheduler` (`scheduler.py`) tracks queued requests in `self._queued` so `cancel_request`/`cancel_all` wake queued callers too, closes generator instances via `gen.close()` in `_worker()`'s `finally:`, releases scheduler slots in `finally:`, and guards `asyncio.run_coroutine_threadsafe(coro, loop)` with `consumer_done` and `loop.is_closed()` checks + `coro.close()` on failure.

---

## 3. Modified & Added Files

| File | Summary of Changes |
| :--- | :--- |
| `shard_engine/distributed/resilience.py` *(new)* | `ResiliencePolicy` (`POLICY`), worker states, `InferenceFailure` / `RequestCancelled`, `ExecutionContext`, `ResumeState`, `CancelEvent`, `Metrics` (`METRICS`), `resolve_deadline()`. |
| `shard_engine/distributed/__init__.py` | Export `resilience` module in `__all__`. |
| `shard_engine/distributed/pipeline_proto.py` | Added `recv_exact_poll()` and `recv_frame_poll()` supporting non-blocking `poll_slice_s` deadline and cancellation checks on socket timeouts. |
| `shard_engine/distributed/pipeline_coordinator.py` | Per-phase timeouts (`load`, `prefill`, `decode`, `release`), slow-step callback, stale `request_id`/`seq`/`execution_id` rejection, `ResumeState` continuation, `on_worker_failure` propagation, and selective `release_request(skip_workers=...)`. |
| `shard_engine/distributed/pipeline_worker.py` | `select`-paced frame loop for fast stop, per-connection `owned_rids` KV-cache cleanup on disconnect, `_active_conns`/`_conn_threads` lifecycle cleanup, `seq`/`execution_id` echoing. |
| `shard_engine/distributed/cluster.py` | 4-state worker health FSM (`HEALTHY`/`SUSPECT`/`FAILED`/`RECOVERING`), probation logic, `_inflight` tracking & broadcast abort, Case A/B/C recovery loop, epoch + deploy-lock guarded `_reform_cluster()` & `stop_cluster()`, GGUF guarded streaming + TCP worker probe, `resilience_status()`. |
| `shard_engine/distributed/scheduler.py` | Queue cancellation (`_queued`), `resolve_deadline()` integration, cancel-aware `_put_queue()` with safe coroutine/event-loop teardown, explicit `gen.close()`, and structured `error_detail` propagation in `stream_async()` / `run_sync_async()`. |
| `shard_engine/gguf_backend.py` | Added `chat_stream_guarded()` detecting streaming stalls (`WORKER_STALLED`, including `ConnectionError`-wrapped `ReadTimeoutError`), truncated SSE streams without `[DONE]` (`BACKEND_ERROR`), and cancellation polling. |
| `shard_engine/server.py` | Routed degraded/loading coordinator requests through cluster error handling (no silent local fallback), `_failure_response()` (`503`/`504` + `Retry-After`), compliant OpenAI & Anthropic SSE error termination, per-request timeout support, `/api/cluster/resilience`, and `POST /api/cluster/resilience/policy`. |
| `shard_engine/cli.py` | Enhanced `shard cluster status` with per-worker liveness state/fail/slow info and added `shard cluster resilience`. |
| `README.md` | Documented worker failure resilience architecture, recovery rules, `shard cluster resilience`, and known limitations. |
| `test/tests_worker_resilience.py` *(new)* | Deterministic 16-test suite (covering scenarios 1–17) using real TCP SHPT servers, scripted blackhole/stale/crash peers, `ThreadingHTTPServer` GGUF SSE mock, and FastAPI `TestClient`. |

---

## 4. Failure Scenario Behavior Matrix

| # | Scenario | Observed Behavior & Error Code | Bounded Recovery / Termination |
| :--- | :--- | :--- | :--- |
| **1** | Worker disconnects before generation starts | Worker removed / marked `FAILED`; cluster transitions `READY -> DEGRADED` or re-forms if feasible. If infeasible, API returns `503 CLUSTER_UNAVAILABLE` (`cluster_available: false`, `retryable: true`). | Verified `< 0.5s` API rejection |
| **2** | Worker crashes during model load (`deploy_cluster`) | `PipelineCoordinator.start()` aborts peer stages, cleans partial runners, transitions cluster out of `LOADING` cleanly, and marks bad worker `FAILED`. | Bounded by `load_timeout_s` |
| **3** | Worker crashes during prefill (`tokens_delivered == 0`) | Data plane detects TCP disconnect -> `WORKER_LOST` -> aborts execution -> Case B safe restart on healthy surviving workers (zero duplicates) or `RECOVERY_FAILED` if infeasible. | Verified `< 1.5s` in local test |
| **4** | Worker disconnects during token decode (`tokens_delivered > 0`) | **Pipeline (`enable_resume=True`):** Case A re-forms cluster on healthy nodes, rebuilds KV cache with `[prompt + generated_ids]`, yields only suffix tokens. **Pipeline (`enable_resume=False`) / GGUF:** Case C fails with `WORKER_LOST` (`tokens_delivered > 0`). | Verified `< 2.5s` in local test |
| **5** | Worker stops sending heartbeats | Monitor moves worker `HEALTHY -> SUSPECT` (`heartbeat_interval_s * suspect_missed`) -> `FAILED` (`heartbeat_interval_s * dead_missed`), excluding from plans and aborting active requests on that worker. | Bounded by `dead_after_s + monitor_tick_s` |
| **6** | Worker stays TCP-connected (blackhole) & stops responding | `_roundtrip()` `poll_slice_s` trips `prefill_timeout_s` / `decode_step_timeout_s` (`WORKER_TIMEOUT`) or `request_deadline_s` (`REQUEST_DEADLINE_EXCEEDED`), closes socket, marks worker `FAILED`, releases slots & KV caches. | Verified `< 1.5s` with `0.35s` policy |
| **7** | Worker becomes slow but keeps responding & heartbeating | Steps slower than `slow_step_warn_s` log `slow_worker_step` and set `slow=True`; worker stays `HEALTHY` and generation completes cleanly. | Generation succeeds cleanly |
| **8** | Worker fails during HTTP/SSE stream (unrecoverable) | OpenAI `/v1/chat/completions` emits `data: {"error": ...}` then `data: [DONE]` without `finish_reason: "stop"`. Anthropic `/v1/messages` emits `event: error` and exits without `end_turn`. | Clean stream termination |
| **9** | One worker fails in a multi-worker pipeline | Only failed worker is marked `FAILED`; healthy worker remains `HEALTHY` (`fail_count == 0`), receives `MSG_RELEASE_KV` to free partial KV cache, and executes rebalanced stages. | Verified in 2-worker + coordinator test |
| **10** | Multiple concurrent requests active when worker fails | `_abort_executions()` aborts all in-flight and lock-waiting requests referencing that worker; all HTTP callers receive `503`/`504`; `SCHEDULER` active requests return to `0`. | All 4 concurrent clients terminate `< 3s` |
| **11** | Failed worker reconnects later | Resumed heartbeats move `FAILED -> RECOVERING` probation (exponential backoff by `fail_count`), then `HEALTHY`, automatically re-forming `DEGRADED -> READY` so new requests return `200 OK`. | Verified end-to-end |
| **12** | Stale/out-of-order worker frame (`seq` / `execution_id` mismatch) | Rejected with `STALE_RESPONSE`, socket closed immediately, `stale_responses_rejected_total` incremented. | Verified for both `seq` and `execution_id` |
| **13** | Client cancels / disconnects during hang or recovery wait | `RequestCancelled` aborts `_roundtrip()` (`poll_slice_s`), `_await_recovery()`, and `SCHEDULER.stream_async()`, running `finally:` cleanup with `0` leaked slots. | Terminates `< 1.0s` |
| **14** | GGUF `llama-server` stalls silently or closes stream before `[DONE]` | `chat_stream_guarded()` yields `"stalled"` (`504 WORKER_STALLED`) on idle read timeout or `"truncated"` (`502 BACKEND_ERROR`) on premature EOF. | Verified via `ThreadingHTTPServer` mock |

---

## 5. Automated Verification Results & Execution Scope

### New Resilience Suite (`test/tests_worker_resilience.py`)

Command executed after final code edit:
```powershell
python -m unittest test/tests_worker_resilience.py -v
```
Result: **16 test methods (`test_01` through `test_17`, combining 13 & 14) ran in 17.537s — `OK` (16/16 passed, 0 failures, 0 errors, 0 thread warnings).**

