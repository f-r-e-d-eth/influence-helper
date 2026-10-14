#!/usr/bin/env bash
set -euo pipefail

# Guided Influence empty-database rebuild.
#
# This script intentionally does NOT delete or move an existing MongoDB database.
# It starts from an already-empty MongoDB database with the rest of the stack
# available.
#
# Recovery flow:
#   1. verify Juno head / requested end block
#   2. stop normal historical-state consumers
#   3. retrieve Starknet history with the parallel crash-resumable helper
#   4. sync Mongo indexes
#   5. restart processor/indexer and pause for catch-up verification
#   6. run legacy asteroid Orbit bootstrap
#   7. reindex asteroids
#   8. report Mongo/Elasticsearch asteroid counts
#   9. optionally remove the six known phantom asteroid docs

PROJECT_DIR="${HOME}/Influence"
COMPOSE_FILE="compose-update.yaml"
FROM_BLOCK=14380
TO_BLOCK=""
EARLY_SPLIT=75000
WORKERS=16
EARLY_CHUNK=100
LATE_CHUNK=1000
JUNO_RPC="http://127.0.0.1:6060/v0_9"
REPAIR_KNOWN_PHANTOMS=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER_REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PARALLEL_HELPER="${HELPER_REPO_DIR}/parallel-starknet-retriever.sh"

usage() {
  cat <<'EOF'
Usage:
  bash database-rebuild/rebuild-database.sh [options]

Options:
  --project-dir PATH          Influence directory (default: ~/Influence)
  --compose-file FILE         Compose file (default: compose-update.yaml)
  --from BLOCK                First historical block (default: 14380)
  --to BLOCK                  Last block. If omitted, read current Juno head.
  --early-split BLOCK         End of dense early-history range (default: 75000)
  --workers N                 Parallel retrievers (default: 16)
  --early-chunk N             Early-history chunk size (default: 100)
  --late-chunk N              Later-history chunk size (default: 1000)
  --juno-rpc URL              Juno RPC endpoint (default: http://127.0.0.1:6060/v0_9)
  --repair-known-phantoms     Remove the six known bad asteroid ES docs after verification
  -h, --help                  Show help

Important:
  This script does NOT delete or create the MongoDB data directory.
  Confirm you are intentionally rebuilding into an empty database before running it.
EOF
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

is_uint() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

while (( $# > 0 )); do
  case "$1" in
    --project-dir) [[ $# -ge 2 ]] || die "--project-dir needs a value"; PROJECT_DIR="$2"; shift 2 ;;
    --compose-file) [[ $# -ge 2 ]] || die "--compose-file needs a value"; COMPOSE_FILE="$2"; shift 2 ;;
    --from) [[ $# -ge 2 ]] || die "--from needs a value"; FROM_BLOCK="$2"; shift 2 ;;
    --to) [[ $# -ge 2 ]] || die "--to needs a value"; TO_BLOCK="$2"; shift 2 ;;
    --early-split) [[ $# -ge 2 ]] || die "--early-split needs a value"; EARLY_SPLIT="$2"; shift 2 ;;
    --workers) [[ $# -ge 2 ]] || die "--workers needs a value"; WORKERS="$2"; shift 2 ;;
    --early-chunk) [[ $# -ge 2 ]] || die "--early-chunk needs a value"; EARLY_CHUNK="$2"; shift 2 ;;
    --late-chunk) [[ $# -ge 2 ]] || die "--late-chunk needs a value"; LATE_CHUNK="$2"; shift 2 ;;
    --juno-rpc) [[ $# -ge 2 ]] || die "--juno-rpc needs a value"; JUNO_RPC="$2"; shift 2 ;;
    --repair-known-phantoms) REPAIR_KNOWN_PHANTOMS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

PROJECT_DIR="${PROJECT_DIR/#\~/$HOME}"

is_uint "$FROM_BLOCK" || die "--from must be an integer"
is_uint "$EARLY_SPLIT" || die "--early-split must be an integer"
is_uint "$WORKERS" && (( WORKERS >= 1 )) || die "--workers must be >= 1"
is_uint "$EARLY_CHUNK" && (( EARLY_CHUNK >= 1 )) || die "--early-chunk must be >= 1"
is_uint "$LATE_CHUNK" && (( LATE_CHUNK >= 1 )) || die "--late-chunk must be >= 1"

[[ -d "$PROJECT_DIR" ]] || die "Project directory not found: $PROJECT_DIR"
[[ -f "$PARALLEL_HELPER" ]] || die "Parallel helper not found: $PARALLEL_HELPER"

if [[ "$COMPOSE_FILE" = /* ]]; then
  COMPOSE_PATH="$COMPOSE_FILE"
else
  COMPOSE_PATH="$PROJECT_DIR/$COMPOSE_FILE"
fi
[[ -f "$COMPOSE_PATH" ]] || die "Compose file not found: $COMPOSE_PATH"

if [[ -z "$TO_BLOCK" ]]; then
  log "Reading current Juno head from $JUNO_RPC ..."
  TO_BLOCK="$(
    curl -fsS "$JUNO_RPC" \
      -H 'Content-Type: application/json' \
      -d '{"jsonrpc":"2.0","method":"starknet_blockNumber","params":[],"id":1}' \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"])'
  )"
fi

is_uint "$TO_BLOCK" || die "Unable to determine a valid --to block"
(( FROM_BLOCK <= TO_BLOCK )) || die "--from must be <= --to"

cat <<EOF

Influence empty-database recovery
---------------------------------
Project:       $PROJECT_DIR
Compose:       $COMPOSE_PATH
History:       $FROM_BLOCK -> $TO_BLOCK
Early split:   $EARLY_SPLIT
Workers:       $WORKERS
Early chunk:   $EARLY_CHUNK
Later chunk:   $LATE_CHUNK
Repair known phantom asteroids: $REPAIR_KNOWN_PHANTOMS

This script assumes MongoDB is intentionally EMPTY.
It will not delete or move an existing database.

EOF

read -r -p "Continue with the rebuild? Type YES: " answer
[[ "$answer" == "YES" ]] || die "Cancelled"

cd "$PROJECT_DIR"

log "Stopping normal retriever / event processor / Elasticsearch indexer ..."
docker compose -f "$COMPOSE_PATH" --profile indexer stop \
  influence-starkneteventretriever \
  influence-eventprocessor \
  influence-indexer || true

run_backfill() {
  local from="$1"
  local to="$2"
  local chunk="$3"

  (( from <= to )) || return 0

  log "Historical retrieval: $from -> $to (chunk=$chunk, workers=$WORKERS)"
  bash "$PARALLEL_HELPER" \
    --from "$from" \
    --to "$to" \
    --workers "$WORKERS" \
    --chunk-size "$chunk" \
    --project-dir "$PROJECT_DIR" \
    --compose-file "$COMPOSE_PATH"
}

if (( FROM_BLOCK <= EARLY_SPLIT )); then
  early_to="$EARLY_SPLIT"
  (( early_to > TO_BLOCK )) && early_to="$TO_BLOCK"
  run_backfill "$FROM_BLOCK" "$early_to" "$EARLY_CHUNK"
  late_from=$(( early_to + 1 ))
else
  late_from="$FROM_BLOCK"
fi

if (( late_from <= TO_BLOCK )); then
  run_backfill "$late_from" "$TO_BLOCK" "$LATE_CHUNK"
fi

log "Historical Starknet retrieval complete."

log "Synchronizing MongoDB indexes ..."
docker compose -f "$COMPOSE_PATH" run --rm influence-server \
  node bin/sync_mongo_indexes.js

log "Starting normal retriever / event processor / Elasticsearch indexer ..."
docker compose -f "$COMPOSE_PATH" --profile indexer up -d \
  influence-starkneteventretriever \
  influence-eventprocessor \
  influence-indexer

cat <<'EOF'

The normal processor/indexer is running now.

Before continuing, verify that:
  - influence-eventprocessor is no longer restart-looping
  - historical event processing has caught up
  - the Elasticsearch indexing queue has caught up sufficiently

Useful commands include:
  docker compose -f compose-update.yaml ps
  docker compose -f compose-update.yaml logs --tail=100 influence-eventprocessor
  docker compose -f compose-update.yaml logs --tail=100 influence-indexer

EOF

read -r -p "Press ENTER after event processing has caught up..." _

log "Running legacy asteroid Orbit bootstrap ..."
docker compose -f "$COMPOSE_PATH" run --rm influence-server \
  node bin/starknet-setup/asteroidsToOrbitComponent.js

log "Queueing asteroid Elasticsearch reindex ..."
docker compose -f "$COMPOSE_PATH" run --rm influence-server \
  node bin/elasticsearch.js reIndex --indices asteroid

cat <<'EOF'

The asteroid reindex has been queued.
Allow the Elasticsearch worker to consume the queue before relying on the
final count. In the successful 2026-10 recovery, this process exposed six
known phantom asteroid documents.

EOF

read -r -p "Press ENTER after the asteroid reindex has finished..." _

log "Checking Mongo asteroid entity count ..."
MONGO_ASTEROIDS="$(
  docker compose -f "$COMPOSE_PATH" exec -T mongo mongosh \
    'mongodb://root:password@localhost:27017/pre-release?authSource=admin' \
    --quiet --eval 'db.entities.countDocuments({label:3})'
)"
echo "Mongo asteroid entities: $MONGO_ASTEROIDS"

log "Checking Elasticsearch asteroid document count ..."
ES_ASTEROIDS="$(
  docker exec elasticsearch curl -fsS 'http://localhost:9200/asteroid_v1/_count' \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["count"])'
)"
echo "Elasticsearch asteroid documents: $ES_ASTEROIDS"

KNOWN_IDS=(
  1894544434003969
  6940920553406465
  6933915461746689
  6881662889623553
  6891760357736449
  6898619420508161
)

KNOWN_UUIDS=(
  0x6bb14000000010003
  0x18a8bb000000010003
  0x18a25c000000010003
  0x1872d6000000010003
  0x187c05000000010003
  0x188242000000010003
)

mongo_id_list="$(IFS=,; echo "${KNOWN_IDS[*]}")"

MONGO_KNOWN_BAD="$(
  docker compose -f "$COMPOSE_PATH" exec -T mongo mongosh \
    'mongodb://root:password@localhost:27017/pre-release?authSource=admin' \
    --quiet --eval "db.entities.countDocuments({label:3,id:{\$in:[$mongo_id_list]}})"
)"

uuid_json="$(
  printf '"%s",' "${KNOWN_UUIDS[@]}" | sed 's/,$//'
)"

ES_KNOWN_BAD="$(
  docker exec elasticsearch curl -fsS -X POST \
    'http://localhost:9200/asteroid_v1/_count' \
    -H 'Content-Type: application/json' \
    -d "{\"query\":{\"terms\":{\"_id\":[$uuid_json]}}}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["count"])'
)"

echo "Known bad IDs present as Mongo asteroids: $MONGO_KNOWN_BAD"
echo "Known phantom UUIDs present in Elasticsearch: $ES_KNOWN_BAD"

if (( ES_KNOWN_BAD > 0 )); then
  echo
  echo "WARNING: known phantom asteroid documents are present in Elasticsearch."
  echo "See: influence-helper/phantom-asteroid-elasticsearch/README.md"
fi

if (( REPAIR_KNOWN_PHANTOMS )); then
  if [[ "$MONGO_KNOWN_BAD" != "0" ]]; then
    die "Refusing phantom repair: at least one known ID exists as a Mongo asteroid entity"
  fi

  if [[ "$ES_KNOWN_BAD" == "0" ]]; then
    log "No known phantom asteroid documents need removal."
  else
    log "Removing only the known phantom asteroid Elasticsearch documents ..."
    docker exec elasticsearch curl -fsS -X POST \
      'http://localhost:9200/asteroid_v1/_delete_by_query?refresh=true' \
      -H 'Content-Type: application/json' \
      -d "{\"query\":{\"terms\":{\"_id\":[$uuid_json]}}}"
    echo

    ES_ASTEROIDS="$(
      docker exec elasticsearch curl -fsS 'http://localhost:9200/asteroid_v1/_count' \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["count"])'
    )"
    echo "Elasticsearch asteroid documents after repair: $ES_ASTEROIDS"
  fi
fi

cat <<EOF

Recovery sequence finished.

Final observations:
  Mongo asteroid entities:         $MONGO_ASTEROIDS
  Elasticsearch asteroid docs:     $ES_ASTEROIDS
  Known bad Mongo asteroid IDs:    $MONGO_KNOWN_BAD
  Known phantom ES documents:      $ES_KNOWN_BAD

Next functional check:
  Open the Influence client and confirm that the world renders correctly.

If anything differs from the known-good 2026-10 recovery, stop and document the
new evidence before applying broader cleanup.
EOF
