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
