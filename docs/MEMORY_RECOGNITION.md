# Local Memory Recognition and Deduplication

## Duplicate Load Guard
When a user selects a model that is already running, SHARD returns an instant success response:
```json
{"status": "already_loaded", "model": "..."}
```
This prevents redundant initialization overhead.
