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
