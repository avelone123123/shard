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

### 2. Blind Encrypted Relay Service (`relay.shard.network`)
- **Container**: `deploy/Dockerfile.relay`
- **Internal Port**: `8765`
- **Protocol**: Raw encrypted WebSocket frames (`wss://`)
- **Security**: The relay never has access to encryption keys. Payloads are encrypted with ChaCha20-Poly1305 between endpoints. The relay inspects only the 32-byte destination node fingerprint in the frame header.

### 3. Edge Reverse Proxy & TLS (Caddy)
- **Configuration**: `deploy/Caddyfile`
- **Automatic SSL/TLS**: Managed via Let's Encrypt / ZeroSSL with automatic OCSP stapling.
- **Connection Upgrades**: Native HTTP/1.1 to WebSocket protocol upgrade for relay endpoints.
- **Header Hardening**:
  - `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Referrer-Policy: strict-origin-when-cross-origin`

---

## Deployment Artifacts

All configuration and container definitions are located in the `deploy/` directory:

| File | Purpose |
| :--- | :--- |
| `deploy/docker-compose.prod.yml` | Multi-container production deployment stack |
| `deploy/Dockerfile.discovery` | Minimal hardened Python 3.11 container for Discovery |
| `deploy/Dockerfile.relay` | Minimal hardened Python 3.11 container for Relay |
| `deploy/Caddyfile` | Production reverse proxy configuration |
| `deploy/env.production.example` | Template for environment variables and secrets |
| `deploy/backup_discovery.sh` | Automated snapshot and backup script |
| `deploy/restore_discovery.sh` | Point-in-time state restoration script |

---

## Step-by-Step Production Deployment

### 1. Server Prerequisites
- Ubuntu 22.04 LTS or Debian 12
- Docker 24.0+ and Docker Compose v2
- Ports open: 80 (TCP), 443 (TCP/UDP for HTTP/3)

### 2. Clone Repository and Configure Environment
```bash
git clone https://github.com/avelone123123/shard.git /opt/shard
cd /opt/shard/deploy
cp env.production.example .env
nano .env
```

Ensure domain names and administrator email are configured in `.env`:
```ini
DOMAIN_DISCOVERY=discovery.shard.network
DOMAIN_RELAY=relay.shard.network
ACME_EMAIL=admin@shard.network
```

### 3. Launch the Stack
```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Verify service status:
```bash
docker compose -f docker-compose.prod.yml ps
curl -fsS https://discovery.shard.network/health
```

---

## Backup and Recovery Procedures

### Automated Daily Backup (`deploy/backup_discovery.sh`)
Add the following cron entry (`crontab -e`) to execute automated daily snapshots:
```cron
0 2 * * * /opt/shard/deploy/backup_discovery.sh /opt/shard/deploy /var/backups/shard
```

The backup script:
- Verifies directory integrity.
- Atomically archives `/var/lib/shard/` into `shard_discovery_backup_<timestamp>.tar.gz`.
- Maintains a rolling retention policy, automatically removing snapshots older than 14 days.

### Disaster Recovery (`deploy/restore_discovery.sh`)
To restore from a backup snapshot:
```bash
/opt/shard/deploy/restore_discovery.sh /var/backups/shard/shard_discovery_backup_20261118_020000.tar.gz /opt/shard/deploy
```
The script safely halts the discovery container, restores state files, fixes ownership, and restarts the discovery service with zero data loss.
