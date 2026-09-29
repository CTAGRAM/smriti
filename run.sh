#!/usr/bin/env bash
# Starts the district Qdrant Server and the Smriti app (two simulated devices).
set -euo pipefail
cd "$(dirname "$0")"
[ -x bin/qdrant ] || ./scripts/get-qdrant.sh
[ -f .env ] && { set -a; source .env; set +a; }
mkdir -p .qdrant-server
if ! curl -s localhost:6333/readyz >/dev/null 2>&1; then
  QDRANT__STORAGE__STORAGE_PATH=./.qdrant-server/storage QDRANT__STORAGE__SNAPSHOTS_PATH=./.qdrant-server/snapshots \
  QDRANT__TELEMETRY_DISABLED=true ./bin/qdrant > .qdrant-server/server.log 2>&1 &
  until curl -s localhost:6333/readyz >/dev/null 2>&1; do sleep 0.3; done
fi
exec uv run uvicorn smriti.app:app --port "${PORT:-8000}"
