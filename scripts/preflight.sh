#!/usr/bin/env bash
# Run ~10 minutes before presenting. Starts anything that is down, resets the demo to
# its opening state (Rekha has already rescheduled Meena and synced), and warms every
# model so the first live search, triage and cloud answer are fast.
set -uo pipefail
cd "$(dirname "$0")/.."
OPENJEV_DIR="${OPENJEV_DIR:-../openjev}"
LOG=.qdrant-server
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=1; }
up()   { curl -s -o /dev/null --max-time 2 "$1"; }
FAIL=0
[ -f .env ] && { set -a; source .env; set +a; }

echo "1. Services"
if ! up localhost:6333/readyz; then
  mkdir -p $LOG
  QDRANT__STORAGE__STORAGE_PATH=./$LOG/storage QDRANT__STORAGE__SNAPSHOTS_PATH=./$LOG/snapshots \
    QDRANT__TELEMETRY_DISABLED=true nohup ./bin/qdrant > $LOG/server.log 2>&1 &
  for _ in $(seq 1 30); do up localhost:6333/readyz && break; sleep 0.5; done
fi
up localhost:6333/readyz && ok "Qdrant Server (district) on :6333" || bad "Qdrant Server did not start"

if ! up 127.0.0.1:8093/v1/models && [ -d "$OPENJEV_DIR/.venv" ]; then
  (cd "$OPENJEV_DIR" && OPENJEV_BACKEND=laya OPENJEV_PORT=8093 OPENJEV_DEVICE=cpu nohup .venv/bin/python -m openjev > /tmp/openjev.log 2>&1 &)
  for _ in $(seq 1 90); do up 127.0.0.1:8093/v1/models && break; sleep 2; done
fi
up 127.0.0.1:8093/v1/models && ok "OpenJev (Laya, CPU) on :8093" || bad "OpenJev not running — triage card will be missing (demo still works)"

if ! up localhost:8000/api/state; then
  OPENJEV_URL=http://127.0.0.1:8093/v1/systemone nohup uv run uvicorn smriti.app:app --port 8000 > /tmp/smriti.log 2>&1 &
  for _ in $(seq 1 60); do up localhost:8000/api/state && break; sleep 1; done
fi
up localhost:8000/api/state && ok "Smriti app on http://localhost:8000" || bad "Smriti app did not start (see /tmp/smriti.log)"

echo "2. Demo state"
curl -s -X POST localhost:8000/api/demo/reset -o /dev/null && ok "Demo reset (both devices synced)" || bad "Reset failed"
curl -s -X POST localhost:8000/api/devices/anm-rekha/households/HH-014 -H 'content-type: application/json' \
  -d '{"changes":{"next_visit":"7 Oct at PHC"}}' -o /dev/null
curl -s -X POST localhost:8000/api/devices/anm-rekha/sync -o /dev/null && ok "Rekha rescheduled Meena to “7 Oct at PHC” and synced" || bad "ANM sync failed"

echo "3. Warm-up"
for q in "which pregnant women have danger signs" "newborn not feeding" "heat illness advice"; do
  for d in asha-sunita anm-rekha; do curl -s -o /dev/null "localhost:8000/api/devices/$d/search?q=${q// /%20}"; done
done
MS=$(curl -s "localhost:8000/api/devices/asha-sunita/search?q=urgent%20notes%20about%20Meena%20this%20week" | python3 -c "import sys,json;print(json.load(sys.stdin)['search_ms'])")
ok "On-device search warm (${MS} ms)"
if up 127.0.0.1:8093/v1/models; then
  curl -s -o /dev/null -X POST 127.0.0.1:8093/v1/systemone -H 'content-type: application/json' \
    -d '{"model":"jev-latest","state":"warm up","questions":{"q":{"type":"noul","instructions":"Is this a test?"}}}' && ok "OpenJev warm"
fi
ANS=$(curl -s -X POST localhost:8000/api/devices/asha-sunita/ask -H 'content-type: application/json' -d '{"question":"what is the antenatal check-up schedule?"}' | python3 -c "import sys,json;d=json.load(sys.stdin);print('ok' if d.get('answer') else 'none')" 2>/dev/null)
[ "$ANS" = ok ] && ok "Gemini reachable (cloud answers work)" || bad "Gemini not answering — skip the 'Ask with cloud AI' step"
for u in / /compare?before=pramaan%2Fgreen-highlands%2Fhighlands-before\&after=pramaan%2Fgreen-highlands%2Fhighlands-after; do curl -s -o /dev/null --max-time 30 "https://pramaan-coral.vercel.app$u"; done
curl -s -o /dev/null --max-time 60 -X POST https://pramaan-coral.vercel.app/api/compare -H 'content-type: application/json' \
  -d '{"before":"pramaan/green-highlands/highlands-before","after":"pramaan/green-highlands/highlands-after"}' && ok "Pramaan (live) warm, in case judges ask"

echo
[ $FAIL = 0 ] && printf "\033[32mReady.\033[0m Open http://localhost:8000 — Sunita online, Rekha online, no conflicts yet.\n" \
  || printf "\033[31mSomething above needs attention.\033[0m Plan B: play the demo video.\n"
