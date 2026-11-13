# SHARD Real-Time Status API Specifications

## Endpoints
- `GET /api/models/status`: Returns current active model ID, backend type, and memory allocation.
- `GET /api/engine/status`: Returns lifecycle state, device name, and backend readiness.
- `POST /api/models/load`: Idempotent model activation with `already_loaded` short-circuit.
