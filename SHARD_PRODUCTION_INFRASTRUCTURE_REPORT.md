# SHARD Production Infrastructure Deployment Report

## Overview

The SHARD global network relies on two core production infrastructure components:
1. **SHARD Global Discovery Service**: Fast, stateless/persistent catalog registry providing node discovery, health monitoring, and server listings.
2. **SHARD Blind Encrypted Relay Service**: High-throughput, zero-knowledge WebSocket relay server enabling end-to-end encrypted packet forwarding across restrictive NATs and symmetric firewalls.

This report provides the full production deployment specification, containerization details, TLS termination, backup and restore procedures, and security hardening guidelines.

---

## Architecture Diagram

```
                             [ Internet Traffic ]
                                      |
                       +--------------+--------------+
                       |   Caddy Reverse Proxy       |
                       |   Automatic Let's Encrypt   |
                       |   Rate Limiting & Gzip      |
                       +--------------+--------------+
                                      |
              +-----------------------+-----------------------+
              | (HTTP /api/net/...)                           | (WebSocket wss://...)
              v                                               v
+-----------------------------+               +-----------------------------+
|   shard-discovery Service   |               |     shard-relay Service     |
|   FastAPI + Uvicorn         |               |     Python WebSocket Async  |
|   Persistent SQLite Volume  |               |     Zero-Knowledge Relay    |
+-----------------------------+               +-----------------------------+
```

---

## Service Specifications

### 1. Discovery Service (`discovery.shard.network`)
- **Container**: `deploy/Dockerfile.discovery`
- **Internal Port**: `8080`
- **Endpoints**:
  - `GET /health`: Health probe returning uptime, registered nodes, and memory usage.
  - `POST /api/net/announce`: Node registration, heartbeat renewal, and capability advertisement.
  - `GET /api/net/catalog`: Active AI server listing with latency annotations and load status.
  - `POST /api/net/leave`: Explicit graceful departure notification.
- **Data Persistence**:
  Mounted volume at `/var/lib/shard` storing `discovery_state.json` and sqlite catalogs.

