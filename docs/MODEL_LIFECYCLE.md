# SHARD Model Lifecycle State Machine

## Overview
The model lifecycle in SHARD Engine transitions deterministically across five operational phases:

1. **Unloaded**: No weights reside in VRAM or RAM.
2. **Downloading**: Active layer/blob fetch from Hugging Face Hub.
3. **Loading**: Model weight instantiation into llama.cpp server runtime.
4. **Ready**: Model ready for sub-millisecond inference and streaming.
5. **Error**: Fatal execution exception with trace capture.

## State Transition Invariants
- A model already loaded into memory skips re-downloading and initialization.
- Inference calls are strictly gated by backend verification.
