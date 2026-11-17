# SHARD Network Architecture Specification

## 1. Executive Summary

SHARD Network is an autonomous, decentralized networking and discovery fabric engineered for distributed AI computing. It eliminates the need for manual VPN configuration, third-party room setups (such as Radmin VPN or Hamachi), and manual IP exchange. When a user runs `shard start`, SHARD automatically initializes a cryptographic node identity, establishes peer-to-peer connectivity using STUN/NAT traversal or blind encrypted relays, registers public servers with the global Discovery Catalog, and enables one-click cluster formation.

```
                      +-----------------------------+
                      |   SHARD Discovery Catalog   |
                      |   (Signed Ed25519 Directory)|
                      +--------------+--------------+
                                     ^
         Publish Server / Sync       |  Fetch / Filter / Prejoin
                     +---------------+---------------+
                     |                               |
       +-------------+-------------+   +-------------+-------------+
       |   Coordinator / Host      |   |   Worker / Contributor    |
       |  - Node ID: shn_coord...  |   |  - Node ID: shn_work...   |
       |  - Ed25519 Identity       |   |  - Explicit Consent & Quota|
       |  - Cluster State: READY   |   |  - Partition: Layers 16-32|
       +-------------+-------------+   +-------------+-------------+
                     |                               |
                     +------- P2P Direct Socket -----+
                     |    (ChaCha20-Poly1305 / TLS)  |
                     |                               |
                     +--- Blind WebSocket Relay -----+
                          (Zero-Knowledge Fallback)
```

## 2. Cryptographic Node Identity & TOFU Security

Every SHARD node possesses a sovereign cryptographic identity generated locally at first boot:

1. **Key Generation**: 
   - A cryptographic Ed25519 signing keypair is generated and stored with strict filesystem permissions (`0600`) at `~/.shard/identity.json`.
   - The public key is encoded into a deterministic, human-identifiable Node ID with the `shn_` prefix (e.g. `shn_a1b2c3d4...`).
