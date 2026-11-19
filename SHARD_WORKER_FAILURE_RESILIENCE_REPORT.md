# SHARD — Worker Failure Resilience & Fault-Tolerant Inference Report

## 1. Root-Cause Analysis of Original Failure-Handling Behavior

An inspection of the original distributed runtime (`cluster.py`, `pipeline_coordinator.py`, `pipeline_worker.py`, `pipeline_proto.py`, `scheduler.py`, `gguf_backend.py`, and `server.py`) revealed nine concrete failure-handling defects when a Worker disconnected, crashed, stalled, or became unreachable mid-generation:

1. **Single Fixed Socket Timeout Without Cancellation Polling (`pipeline_coordinator.py`, `pipeline_proto.py`):** `_RemoteStageClient` created sockets with a hardcoded `timeout=60.0` shared across connect, load, prefill, and decode phases. `recv_exact()` blocked indefinitely up to 60s without checking `cancel_check` or heartbeat failure transitions; a client disconnect or heartbeat death could not wake an active `forward()` call.
2. **Heartbeat Liveness Was Binary and Unbounded During In-Flight Calls (`cluster.py`):** `_alive_worker_records()` and `_monitor_loop()` checked only `(now - last_heartbeat) <= 12.0` (`HEARTBEAT_TIMEOUT_S`). There was no `SUSPECT` state, no configurable intervals/thresholds, no probation on reconnect, and no mechanism to abort active `generate_stream()` calls when a worker stopped sending heartbeats.
