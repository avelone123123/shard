# Automatic Worker Join & Cluster Onboarding Report

## 1. Executive Summary

The One-Click Cluster Join architecture allows any computer on the network to participate in a distributed AI cluster. Users can join as a pure AI Consumer ("Option A") or contribute their GPU and CPU hardware as a Worker Node ("Option B"). Joining as a worker involves pre-join capability analysis, explicit user consent enforcement, automatic partition scheduling, and encrypted pipeline connection.

```
+--------------------------------------------------------------------------+
|                     1-Click Cluster Onboarding Flow                      |
+--------------------------------------------------------------------------+
  User clicks "Join" in Catalog
            |
