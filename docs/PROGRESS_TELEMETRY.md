# SHARD Download Progress Telemetry Protocol

## Metric Aggregation
- **Downloaded Bytes**: Active `.incomplete` blob size tracking during download.
- **Speed Calculation**: Moving average over sliding timestamp windows.
- **ETA Estimation**: Dynamic calculation based on remaining bytes and smoothed throughput.
- **Byte Counter Preservation**: Non-byte progress updates do not wipe current counters.
