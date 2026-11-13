# Hugging Face Hub Storage Layout in SHARD

## Structure
- `models--<author>--<name>/`
  - `snapshots/<commit_hash>/`: Finalized model weights.
  - `blobs/`: Incomplete and committed binary blobs.
  - `refs/`: Branch pointers.

## Validation
SHARD treats snapshots with valid weights as complete even if orphaned `.incomplete` blobs exist.
