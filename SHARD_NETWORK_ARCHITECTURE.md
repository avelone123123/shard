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
