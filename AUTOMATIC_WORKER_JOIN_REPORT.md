# Automatic Worker Join & Cluster Onboarding Report

## 1. Executive Summary

The One-Click Cluster Join architecture allows any computer on the network to participate in a distributed AI cluster. Users can join as a pure AI Consumer ("Option A") or contribute their GPU and CPU hardware as a Worker Node ("Option B"). Joining as a worker involves pre-join capability analysis, explicit user consent enforcement, automatic partition scheduling, and encrypted pipeline connection.

```
+--------------------------------------------------------------------------+
|                     1-Click Cluster Onboarding Flow                      |
+--------------------------------------------------------------------------+
  User clicks "Join" in Catalog
            |
            v
  +----------------------+
  | Modal Choice Dialog  |
  +----------+-----------+
             |
             +---> Option A: [Use AI as Client]
             |        |
             |        v
             |     Instant tunnel connection established;
             |     Ready to stream prompts via OpenAI API / UI.
             |
             +---> Option B: [Contribute Computing Resources]
                      |
                      v
             1. Pre-Join Hardware Capability Analysis
                (GET /api/catalog/prejoin/{node_id})
                - Probes GPU VRAM, System RAM, CPU threads, CUDA/GGUF
                - Calculates estimated layer shard size
                - Displays compatibility badge & recommendations
                      |
                      v
             2. Quota Tuning & Consent Enforcement
                - VRAM slider (default 70%)
                - CPU threads allocation slider
