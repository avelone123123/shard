# SHARD Multi-Root Cache Detection Architecture

## Multi-Directory Search Path
SHARD detects cached weights across multiple storage locations:
- `HF_HOME/hub`
- `settings.models_dir()/hub`
- `paths.default_models_dir()/hub`
- `~/.shard/models/hub`
- `~/.cache/huggingface/hub`

## Detection Algorithm
- Validates snapshot folder existence.
- Checks that snapshot contains at least one non-empty `.gguf` or `.safetensors` file.
- Ignores stale incomplete temporary blobs older than 120 seconds.
