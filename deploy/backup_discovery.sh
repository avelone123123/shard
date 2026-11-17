#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR="${BACKUP_DIR:-/var/backups/shard}"
mkdir -p "${BACKUP_DIR}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
ARCHIVE="${BACKUP_DIR}/shard_discovery_${TIMESTAMP}.tar.gz"
docker run --rm -v shard_discovery_data:/data -v "${BACKUP_DIR}:/backup" alpine tar -czf "/backup/shard_discovery_${TIMESTAMP}.tar.gz" -C /data .
echo "Discovery backup created: ${ARCHIVE}"