# GGUF Backend Execution Architecture

## Runtime Integration
- Subprocess execution of `llama-server` binary.
- Context length dynamic scaling based on system VRAM.
- Quantization BPW preference ranking.
- Automated port allocation with loopback binding.
