# SHARD Network — Architecture, Self-Hosting & Security Specification
## 1. Executive Summary
SHARD Network provides a decentralized, community-powered networking ecosystem enabling users worldwide to host, discover, and collaboratively use local AI inference engines across arbitrary network boundaries without vendor lock-in, proprietary cloud servers, or subscription fees.
## 2. Core Architectural Principles
- Peer Sovereignty: Every node owns an immutable Ed25519 identity (`shn_<40hex>`).
- Zero AI Traffic on Discovery: Discovery servers store only signed metadata and TTL-expiring commitments; inference traffic never passes through Discovery.
- Blind Relay Forwarding: Relays only forward authenticated AEAD ciphertexts (`ChaCha20-Poly1305` with 16-byte AAD); relays cannot decrypt prompts, responses, or bearer tokens.
- Strict Owner Isolation: Remote peers connecting via encrypted tunnels are never granted Owner privileges and are blocked from all administrative `/api/*` endpoints.
- Deterministic Path Priority: Handshakes resolve paths in strict preference: `same_machine -> direct_lan -> direct_p2p (STUN/UDP) -> encrypted relay`. No node is marked `connected` or `relay_connected` without a verified `/v1/ping` round-trip.
- Full Offline Independence: When `offline_only=True` or when outside networks are unreachable, local inference and local LAN compute pools continue operating without failure.
## 3. Cryptographic Identity & Wire Protocol
- Node ID: `shn_` prefix followed by 40 hex characters derived from SHA-256 of the Ed25519 public key.
- Handshake: 3-step Noise-inspired ephemeral X25519 Diffie-Hellman key exchange authenticated by Ed25519 signatures.
- Frame Format: 16-byte plaintext AAD containing `ver` (1B), `msg_type` (1B), `flags` (2B), `stream_id` (4B), `seq` (8B), followed by encrypted payload and 16-byte Poly1305 authentication tag.
- Monotonic Anti-Replay: Inbound frames must match strictly increasing sequence numbers; tampered or out-of-order frames terminate the session immediately.
## 4. Self-Hosting Guide
### Standalone Discovery Server
```bash
shard network discovery-serve --host 0.0.0.0 --port 8090 --rate-limit 120
```
### Standalone Blind Relay Server
```bash
shard network relay-serve --host 0.0.0.0 --port 8091 --max-tunnels-per-host 32 --max-kbps 10240
```
### Docker Compose Deployment
```bash
docker compose -f docker-compose.network.yml up -d
```
### Systemd Units
Production systemd unit templates are provided under `deploy/systemd/`:
- `deploy/systemd/shard-discovery.service`: Dedicated Discovery daemon with sandboxed non-root execution.
- `deploy/systemd/shard-relay.service`: High-throughput Blind Relay daemon.
- `deploy/systemd/shard-user.service`: Local SHARD engine instance with automatic network recovery.
## 5. Security & Isolation Matrix
- `/v1/ping`: Public endpoint for reachability and RTT measurement.
- `/v1/models`, `/v1/chat/completions`: Allowed for cryptographically authorized tunnel peers and local bearer token holders.
- `/api/*`: Strictly restricted to local Owner requests (`127.0.0.1` without proxy/tunnel markers or with verified Owner token). Remote peers receive HTTP 403.
- Anti-Spoofing: `X-Shard-Bridge-Token` is verified against memory-only random secrets. Presence of `X-Shard-Tunnel-Peer` or `X-Shard-Bridge-Token` unconditionally disallows Owner privilege escalation.
## 6. CLI Command Reference
- `shard network status`: Display local Node ID, connection state, active peer count, and discovery/relay addresses.
- `shard network identity`: Output public cryptographic identity, Ed25519 key, and SHA-256 fingerprint.
- `shard network config`: Read or mutate network configuration (`--discovery`, `--relay`, `--offline-only`, `--mode`, `--visibility`, `--access`).
- `shard network servers`: Query Discovery servers for available public AI hosts with model, region, and access filtering.
- `shard network publish`: Publish local engine to Discovery and configure tunnel endpoints.
- `shard network unpublish`: Remove server from Discovery and terminate inbound tunnel endpoints.
- `shard network connect <target>`: Establish an end-to-end verified tunnel to a remote server.
- `shard network disconnect`: Tear down the active tunnel.
- `shard network invite create|list|revoke`: Manage revocable cryptographic invite links (`shinv1_*`).
- `shard network peers`: List connected, pending, authorized, and blocked peers.
- `shard network approve|reject|kick|block|unblock`: Administer access control for connecting nodes.
- `shard network quota <peer> --rpm <n> --kbps <n>`: Set per-peer request and bandwidth caps.
- `shard network wg`: Inspect WireGuard subsystem status and interface configuration plans.
- `shard network doctor`: Audit platform firewall, STUN NAT mapping, and network connectivity.

