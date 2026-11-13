# Duplicate Download Guard and Author Resolution

## Authorless Normalization
Models requested without author prefix are canonically resolved:
- `Qwen2.5-Coder-1.5B-Instruct-GGUF` -> `Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF`
- Checks local cache before dispatching network requests.
