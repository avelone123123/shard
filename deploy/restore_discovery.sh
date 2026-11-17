#!/usr/bin/env bash
set -euo pipefail
ARCHIVE="${1:-}"
if [[ -z "${ARCHIVE}" || ! -f "${ARCHIVE}" ]]; then
echo "Usage: $0 <path-to-archive.tar.gz>" >&2
exit 1
fi
docker run --rm -v shard_discovery_data:/data -v "$(dirname "${ARCHIVE}"):/backup" alpine sh -c "rm -rf /data/* && tar -xzf /backup/$(basename "${ARCHIVE}") -C /data"
echo "Discovery data restored from: ${ARCHIVE}"