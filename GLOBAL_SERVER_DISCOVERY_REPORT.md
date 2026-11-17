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
