# Radmin VPN Integration & Fallback Report

## Executive Summary

Radmin VPN (by Famatech Corp.) is a widely adopted virtual networking technology that creates a secure virtual local network over the internet. Computers running Radmin VPN are assigned static, routable IP addresses within the `26.0.0.0/8` subnet.

In distributed AI computing environments, Radmin VPN serves as a resilient, zero-configuration fallback transport when direct peer-to-peer connections (STUN/UPnP) are blocked by aggressive Carrier-Grade NAT (CGNAT) or restrictive corporate firewalls.

This report documents the deep integration of Radmin VPN into SHARD, covering automated installation, binary Authenticode signature validation, adapter detection, cluster network provisioning, vendor limitations, and runtime health management.

---

## Technical Architecture & Lifecycle

```
+-------------------------------------------------------------------------------+
|                       SHARD RADMIN VPN LIFECYCLE                              |
+-------------------------------------------------------------------------------+
                                        |
       +--------------------------------+-------------------------------+
       |                                                                |
       v                                                                v
[ State Inspection ]                                           [ Installation / Repair ]
- Check Registry & Filesystem                                  - Official Executable Download
- Query RvControlSvc Service Status                            - Authenticode Signature Verification
- Query Radmin VPN Virtual Adapter                             - Elevated UAC Execution
- Read 26.x.x.x IPv4 Allocation                                - Silent Inno Setup Installation
                                                                - Service / Adapter Initialization
```

---

## Detailed Component Breakdown

### 1. Vendor Binary & Authenticode Signature Verification
- **Official Download URL**: `https://download.radmin-vpn.com/download/files/Radmin_VPN_2.1.4952.2.exe`
- **Installer Technology**: Inno Setup with silent arguments `/VERYSILENT /NORESTART /SP-`
- **Authenticode Signature Verification**:
  To protect users from supply chain attacks or MITM binary injection, SHARD verifies the digital signature of the downloaded installer before execution:
  - Subject: `CN=Famatech Corp.`
  - Certification Authority: Trusted Windows Root Certification Authorities
  - PowerShell validation:
    ```powershell
    Get-AuthenticodeSignature -FilePath '<installer_path>' | Select-Object Status, SignerCertificate
    ```
  - Execution is immediately halted if `Status` does not equal `Valid` or if the Subject does not contain `Famatech Corp.`.

### 2. UAC Elevated Silent Installation
Installing network drivers (NDIS virtual adapter) and Windows background services requires administrative privileges:
- SHARD detects current privilege level via Windows API (`shell32.IsUserAnAdmin`).
- If running unprivileged, SHARD invokes Windows User Account Control (UAC) elevation:
  ```powershell
  Start-Process -FilePath '<installer_path>' -ArgumentList '/VERYSILENT /NORESTART' -Verb RunAs -Wait
  ```
- After installation completes, SHARD polls for the registration of the `RvControlSvc` service and the creation of the virtual network adapter.

### 3. Service & Network Adapter Detection
SHARD monitors three independent indicators of Radmin VPN operational readiness:
1. **Windows Service (`RvControlSvc`)**:
   - Monitored via WMI / PowerShell `Get-Service RvControlSvc`.
   - Verified state: `Running`, start type: `Automatic`.
2. **Virtual Network Interface**:
   - Interface name pattern: `*Radmin VPN*`.
   - IP address allocation: Assigned an IPv4 in `26.0.0.0/8` (e.g., `26.84.23.251`).
   - Interface status: `Up` / `Operational`.
3. **GUI Management Process**:
   - Process name: `RvRvpnGui.exe`.
   - File locations checked:
     - `C:\Program Files (x86)\Radmin VPN\RvRvpnGui.exe`
     - `C:\Program Files\Radmin VPN\RvRvpnGui.exe`

### 4. Vendor Limitations & Cluster Network Workflow

#### The Vendor CLI Limitation
Famatech Corp. does not provide a public command-line interface (CLI) or programmatic C/C++ API for Radmin VPN to create, join, or leave virtual networks programmatically.
The client GUI (`RvRvpnGui.exe`) relies on an internal proprietary IPC protocol with `RvControlSvc`.

#### SHARD's Deterministic Provisioning Solution
To provide a zero-configuration user experience despite this vendor limitation, SHARD implements **deterministic cluster network provisioning**:
1. When a coordinator creates a cluster (e.g., `cluster-9876543210`), SHARD deterministically computes an isolated, collision-resistant network credential pair:
   - **Network Name**: `SHARD-<sha256(cluster_id)[:8]>` (e.g., `SHARD-4a7b21f9`)
   - **Network Password**: `ShardNet_<sha256(seed + cluster_id)[:10]>`
2. When a worker joins the cluster:
   - SHARD queries `/api/net/radmin/cluster-network/{cluster_id}`.
   - The dashboard presents one-click buttons to copy the network name and password to the clipboard.
   - SHARD provides a direct trigger (`POST /api/net/radmin/gui` or `shard network radmin gui`) that launches `RvRvpnGui.exe` with zero navigation required.
   - Once joined, SHARD automatically detects the new `26.x.x.x` peer routes without requiring manual IP address entry.

### 5. Self-Healing & Service Repair
If the Radmin service stalls or fails to respond after a Windows sleep cycle or system update:
- SHARD exposes a self-healing API: `POST /api/net/radmin/repair` (CLI: `shard network radmin repair`).
- Execution performs a clean restart of the driver subsystem:
  ```powershell
  Restart-Service RvControlSvc -Force -ErrorAction SilentlyContinue; Start-Service RvControlSvc -ErrorAction SilentlyContinue
  ```
- The virtual adapter is polled until the `26.0.0.0/8` IP rebinds.

---

## State Transition Diagram

```
+----------------+      Download & Verify      +--------------------+
|  Not Installed | --------------------------> | Verified Installer |
+----------------+                             +--------------------+
                                                         |
                                                         | RunAs /VERYSILENT
                                                         v
