# Autonomous Installation & Zero-Configuration Report

## Overview

A major objective of SHARD is to eliminate manual networking configurations for everyday users. Installing and running SHARD must be as straightforward as launching a modern desktop application, while remaining fully capable of head-less server deployment for high-performance clusters.

This report documents the end-to-end installation workflow, operating system specific behavior, configuration persistence, and headless flags.

---

## The Zero-Configuration User Experience

```
[ Download / Install SHARD ]
             |
             v
[ First Run: `shard start` ]
             |
             +---> Windows: Check Radmin VPN Status
             |              - If Installed: Detect 26.x.x.x
             |              - If Absent: Prompt Optional 1-Click Install
             |              - If Declined: Save Preference, Use P2P/Relay
             |
             +---> Linux / macOS: Initialize P2P / WireGuard / Blind Relay
             |
             v
[ Cryptographic Identity Initialized (Ed25519) ]
             |
             v
[ Autonomous Network Discovery (`https://discovery.shard.network`) ]
             |
             v
[ Dashboard Ready: 1-Click Join as User or Contributor ]
```

---

## Operating System Workflows

### 1. Windows Installation & Radmin VPN Integration
On Windows 10/11:
1. **Initial Startup**:
   Running `shard start` automatically runs system diagnostics.
2. **Radmin VPN Detection**:
   SHARD checks whether `RvControlSvc` and the Radmin VPN adapter are installed.
3. **User Choice**:
   - If missing, the user is offered the option to install Radmin VPN to enable direct mesh networking across difficult firewalls.
   - User consent can be granted via the CLI (`shard network radmin install`) or the Web Dashboard Settings card.
