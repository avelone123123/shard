# Global Server Discovery & Public Catalog Report

## 1. Overview & Objectives

The SHARD Global Server Catalog provides a decentralized directory where cluster coordinators publish available AI servers and workers discover compatible clusters worldwide. It provides search, multi-attribute filtering, sorting, and passport inspection without requiring manual room keys or VPN exchanges.

## 2. Server Passport Schema

Each server registered in the catalog presents a verified passport structure:

```json
{
  "node_id": "shn_9df273a0058b76e1",
  "name": "Alpha-DeepSeek-Cluster",
  "active_model": "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
  "vram_gb": 24.0,
  "aggregate_vram_mb": 24576,
  "aggregate_ram_mb": 65536,
  "worker_count": 3,
  "accepts_workers": true,
  "cluster_state": "READY",
  "cluster_role": "coordinator",
  "cluster_strategy": "torch_pipeline_parallel",
  "backend": "torch",
  "visibility": "public",
  "access_mode": "open",
  "direct_endpoints": ["203.0.113.45:8000"],
  "rendezvous_hint": "relay://hub1.shard.network/shn_9df273a0058b76e1",
  "last_seen": 1731887200
}
```

## 3. Catalog API Specifications

### 1. Catalog Search & Filter (`GET /api/catalog/servers`)
- **Query Parameters**:
  - `q`: Text search over server name, model ID, or node ID.
  - `model`: Exact or partial model filter (e.g. `deepseek`, `qwen`).
  - `min_vram`: Minimum required aggregate cluster VRAM in GB (e.g. `16`).
  - `status`: Filter by cluster state (`READY`, `WAITING_WORKERS`, `FORMING`).
  - `accepts_workers`: Boolean filter (`true` to show clusters accepting new worker nodes).
  - `sort`: Sorting criteria (`activity`, `vram`, `performance`, `newest`, `name`).
  - `limit`: Page limit (default `100`, max `500`).

### 2. Server Passport Details (`GET /api/catalog/servers/{node_id}`)
- Returns the complete public listing, cryptographic verification status, host information, cluster strategy, and connectivity options.

### 3. Server Publication (`POST /api/catalog/publish`)
