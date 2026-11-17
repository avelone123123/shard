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
   - If the user declines (`shard network radmin decline` or `--no-radmin`), SHARD remembers this preference in `radmin_state.json` and never prompts again.
4. **Authenticode Verification**:
   When approved, the official installer (`Radmin_VPN_2.1.4952.2.exe`) is downloaded from Famatech Corp., verified against its DigiCert signature (`CN=Famatech Corp.`), and executed silently with UAC elevation.
5. **Immediate Activation**:
   Once installed, the adapter IPv4 (`26.x.x.x`) is bound and announced as a viable transport.

### 2. Linux & macOS Zero-Configuration Workflow
On POSIX platforms (Ubuntu, Debian, Fedora, Arch, macOS):
1. **Zero External Dependencies**:
   Radmin VPN is neither supported nor required on Linux/macOS. SHARD automatically skips Windows-specific adapters.
2. **Native Transports**:
   - **LAN Discovery**: Multicast and subnet scanning for local cluster nodes.
   - **WireGuard**: Automatic detection of active `wg*` interfaces and `100.64.0.0/10` CGNAT / Tailscale / Netbird interfaces.
   - **Direct P2P**: STUN-based UDP hole punching.
   - **Encrypted Blind Relay**: Seamless fallback over `wss://relay.shard.network`.
3. **No Root Requirement**:
   SHARD runs completely in user space without requiring `sudo` privileges.

---

## Configuration & State Persistence

All node configuration is stored in the user profile directory under `~/.shard/`:

| File | Purpose | Sample Contents |
| :--- | :--- | :--- |
| `~/.shard/identity.json` | Ed25519 / X25519 node keys | `{"node_id": "...", "public_key": "...", "private_key": "..."}` |
| `~/.shard/network_config.json` | Network preferences | `{"discovery_urls": [...], "relay_urls": [...], "p2p_enabled": true}` |
| `~/.shard/radmin_state.json` | Windows Radmin preferences | `{"consent": true, "declined": false, "installed_at": 1731889200}` |

---

## Non-Interactive & Headless Environment Flags

For CI/CD pipelines, Docker containers, and automated server deployments:

