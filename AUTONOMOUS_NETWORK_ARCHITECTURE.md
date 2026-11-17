# Autonomous SHARD Network Architecture

## Executive Overview

The SHARD Network is an autonomous, decentralized, zero-configuration peer-to-peer and relayed network layer engineered specifically for distributed AI inference and model partitioning across heterogeneous computing devices.

Traditional distributed computing platforms depend on manual infrastructure setup: port forwarding, static public IP assignment, manual Virtual Private Network (VPN) configuration, or centralized cloud orchestrators. SHARD eliminates manual networking through an autonomous multi-tier transport hierarchy, cryptographically signed discovery, blind encrypted relaying, and automated mesh fallbacks.

## Core Architectural Pillars

### 1. Zero-Configuration Identity & Cryptography
- **Trust On First Use (TOFU)**: Every SHARD node generates an Ed25519 signing keypair (`net_identity.py`) and an X25519 key-exchange pair on first execution.
- **Node Identifier**: The public key fingerprint serves as the immutable Node ID (`node_id`). All announcements, registrations, and partition commitments are cryptographically signed with Ed25519.
- **Mutual Authenticated Transport**: Direct P2P channels and relayed payloads are sealed using authenticated encryption (ChaCha20-Poly1305 / AES-256-GCM) with ephemeral keys derived via ECDH.

### 2. Autonomous Multi-Tier Transport Hierarchy
Connections between distributed nodes automatically negotiate the optimal path based on network topology, latency, and throughput:

```
+-------------------------------------------------------------------------+
|                  AUTONOMOUS TRANSPORT SELECTION ENGINE                  |
+-------------------------------------------------------------------------+
                                     |
           +-------------------------+-------------------------+
           |                                                   |
           v                                                   v
   [ Local & Direct ]                                 [ Mesh & Relayed ]
+---------------------+                            +---------------------+
| Tier 1: Localhost   | (IPC / Loopback)           | Tier 3: Mesh VPN    | (Radmin VPN 26.x,
| Tier 2: LAN Direct  | (RFC1918 / RFC4193)        |                      WireGuard 100.64.x)
| Tier 4: Direct P2P  | (STUN / UPnP NAT-T)        | Tier 5: Blind Relay | (WSS WebSocket Relay)
+---------------------+                            +---------------------+
```

### Path Selection Priority Matrix

| Priority | Transport Mode | Latency / Bandwidth | Classification Criteria | Failover Precedence |
| :--- | :--- | :--- | :--- | :--- |
| **1** | `same_machine` | < 0.1 ms / ~25 GB/s | `127.0.0.1`, `::1`, `localhost` | Preferred when local worker slots exist |
| **2** | `direct_lan` | 0.2 - 2 ms / 1-10 Gbps | Private RFC1918 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) | Preferred for local clusters / home labs |
| **3** | `mesh_vpn` | 5 - 35 ms / 100-500 Mbps | Virtual adapters (`26.0.0.0/8` Radmin, `100.64.0.0/10` CGNAT/WireGuard) | Zero-NAT virtual mesh with direct IP |
| **4** | `direct_p2p` | 10 - 60 ms / Line rate | Public IPv4/IPv6 resolved via STUN | Direct hole punching through cone NATs |
| **5** | `relay` | 40 - 120 ms / 50-200 Mbps | End-to-end encrypted Blind WebSocket Relay | Guaranteed traversal for symmetric NATs |

### 3. Production Discovery & Catalog Sync
- **Official Public Infrastructure**:
  - Primary Discovery: `https://discovery.shard.network`
  - Backup Discovery: `https://discovery-backup.shard.network`
