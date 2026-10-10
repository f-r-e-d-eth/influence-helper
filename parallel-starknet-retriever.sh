#!/usr/bin/env bash
set -uo pipefail

# Parallel, crash-resumable Starknet event backfill for an Influence server.
#
# Each chunk is replayed independently. A chunk gets a .done marker only after
# the retriever exits successfully, so rerunning the same command after a crash
# safely resumes unfinished chunks.

FROM=""
TO=""
WORKERS=2
CHUNK_SIZE=1000
PROJECT_DIR="${HOME}/Influence"
COMPOSE_FILE="compose-update.yaml"
PROFILE="indexer"
SERVICE="influence-starkneteventretriever"
STATE_DIR=""
STATUS_ONLY=0

usage() {
  cat <<'EOF'
Usage:
  parallel-starknet-retriever.sh --from BLOCK --to BLOCK [options]

Required:
  --from BLOCK          First Starknet block to retrieve
  --to BLOCK            Last Starknet block to retrieve

Options:
  --workers N           Number of parallel retrievers (default: 2)
  --chunk-size N        Blocks per resumable chunk (default: 1000)
  --project-dir PATH    Influence compose directory (default: ~/Influence)
  --compose-file FILE   Compose file name/path (default: compose-update.yaml)
  --profile NAME        Docker Compose profile (default: indexer)
  --service NAME        Retriever service (default: influence-starkneteventretriever)
  --state-dir PATH      Logs/state root (default: <project>/.starknet-backfill)
  --status              Show progress for this exact job and exit
  -h, --help            Show this help

Example:
  ./parallel-starknet-retriever.sh \
    --from 58160 --to 75000 --workers 4 --chunk-size 1000

Crash recovery:
  Run the exact same command again. Completed chunks are skipped and unfinished
  chunks are replayed.

Important:
  For historical backfills, stop the Influence event processor/indexer first.
  Restart them after all retrieval chunks complete.
EOF
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
    --from)          [[ $# -ge 2 ]] || die "--from needs a value"; FROM="$2"; shift 2 ;;
    --to)            [[ $# -ge 2 ]] || die "--to needs a value"; TO="$2"; shift 2 ;;
    --workers)       [[ $# -ge 2 ]] || die "--workers needs a value"; WORKERS="$2"; shift 2 ;;
    --chunk-size)    [[ $# -ge 2 ]] || die "--chunk-size needs a value"; CHUNK_SIZE="$2"; shift 2 ;;
    --project-dir)   [[ $# -ge 2 ]] || die "--project-dir needs a value"; PROJECT_DIR="$2"; shift 2 ;;
    --compose-file)  [[ $# -ge 2 ]] || die "--compose-file needs a value"; COMPOSE_FILE="$2"; shift 2 ;;
    --profile)       [[ $# -ge 2 ]] || die "--profile needs a value"; PROFILE="$2"; shift 2 ;;
    --service)       [[ $# -ge 2 ]] || die "--service needs a value"; SERVICE="$2"; shift 2 ;;
    --state-dir)     [[ $# -ge 2 ]] || die "--state-dir needs a value"; STATE_DIR="$2"; shift 2 ;;
    --status)        STATUS_ONLY=1; shift ;;
    -h|--help)       usage; exit 0 ;;
    *)               die "Unknown argument: $1" ;;
  esac
done

[[ -n "$FROM" ]] || die "--from is required"
[[ -n "$TO" ]] || die "--to is required"
is_uint "$FROM" || die "--from must be a non-negative integer"
is_uint "$TO" || die "--to must be a non-negative integer"
is_uint "$WORKERS" && (( WORKERS >= 1 )) || die "--workers must be >= 1"
is_uint "$CHUNK_SIZE" && (( CHUNK_SIZE >= 1 )) || die "--chunk-size must be >= 1"
(( FROM <= TO )) || die "--from must be <= --to"

PROJECT_DIR="${PROJECT_DIR/#\~/$HOME}"
[[ -d "$PROJECT_DIR" ]] || die "Project directory not found: $PROJECT_DIR"

if [[ "$COMPOSE_FILE" != /* ]]; then
  COMPOSE_PATH="$PROJECT_DIR/$COMPOSE_FILE"
else
  COMPOSE_PATH="$COMPOSE_FILE"
fi
[[ -f "$COMPOSE_PATH" ]] || die "Compose file not found: $COMPOSE_PATH"

if [[ -z "$STATE_DIR" ]]; then
  STATE_DIR="$PROJECT_DIR/.starknet-backfill"
fi
STATE_DIR="${STATE_DIR/#\~/$HOME}"

JOB_ID="${FROM}-${TO}-chunk${CHUNK_SIZE}"
RUN_DIR="$STATE_DIR/$JOB_ID"
LOG_DIR="$RUN_DIR/logs"
DONE_DIR="$RUN_DIR/done"
RUNNING_DIR="$RUN_DIR/running"
FAILED_DIR="$RUN_DIR/failed"

mkdir -p "$LOG_DIR" "$DONE_DIR" "$RUNNING_DIR" "$FAILED_DIR"

# Prevent accidentally starting the exact same controller twice.
exec 9>"$RUN_DIR/controller.lock"
if ! flock -n 9; then
  die "Another controller for this job is already running: $JOB_ID"
fi

chunk_name() {
  printf "%012d-%012d" "$1" "$2"
}

total_chunks=0
done_chunks=0
pending_chunks=0
for (( start=FROM; start<=TO; start+=CHUNK_SIZE )); do
  end=$(( start + CHUNK_SIZE - 1 ))
  (( end > TO )) && end=$TO
  name="$(chunk_name "$start" "$end")"
  ((total_chunks+=1))
  if [[ -f "$DONE_DIR/$name.done" ]]; then
    ((done_chunks+=1))
  else
    ((pending_chunks+=1))
  fi
done

echo "Job:        $FROM -> $TO"
echo "Workers:    $WORKERS"
echo "Chunk size: $CHUNK_SIZE"
echo "Progress:   $done_chunks/$total_chunks chunks complete"
echo "State:      $RUN_DIR"

if (( STATUS_ONLY )); then
  exit 0
fi

# Stale .running markers are informational only. If there is no .done marker,
# the chunk will be replayed.
rm -f "$RUNNING_DIR"/*.running 2>/dev/null || true

interrupted=0
on_interrupt() {
  interrupted=1
  echo
  echo "Stopping active retrievers..."
  jobs -pr | xargs -r kill 2>/dev/null || true
  wait 2>/dev/null || true
  echo "Interrupted. Run the same command again to resume."
  exit 130
}
trap on_interrupt INT TERM

run_chunk() {
  local start="$1"
  local end="$2"
  local name
  local log
  local running
  local failed
  local done_tmp

  name="$(chunk_name "$start" "$end")"
  log="$LOG_DIR/$name.log"
  running="$RUNNING_DIR/$name.running"
  failed="$FAILED_DIR/$name.failed"
  done_tmp="$DONE_DIR/$name.done.tmp"

  if [[ -f "$DONE_DIR/$name.done" ]]; then
    echo "SKIP  $start -> $end"
    return 0
  fi

  rm -f "$failed" "$done_tmp"
  printf 'pid=%s\nstarted=%s\nrange=%s-%s\n'     "$BASHPID" "$(date --iso-8601=seconds)" "$start" "$end" > "$running"

  echo "START $start -> $end"

  (
    cd "$PROJECT_DIR" || exit 1
    docker compose -f "$COMPOSE_PATH" --profile "$PROFILE" run --rm "$SERVICE"       npm run starknetEventRetriever --       --run-once       "--fromBlock=$start"       "--toBlock=$end"
  ) > "$log" 2>&1

  rc=$?
  rm -f "$running"

  if (( rc == 0 )); then
    {
      echo "range=$start-$end"
      echo "finished=$(date --iso-8601=seconds)"
      echo "log=$log"
    } > "$done_tmp"
    mv "$done_tmp" "$DONE_DIR/$name.done"
    rm -f "$failed"
    echo "DONE  $start -> $end"
    return 0
  fi

  {
    echo "range=$start-$end"
    echo "failed=$(date --iso-8601=seconds)"
    echo "exit_code=$rc"
    echo "log=$log"
  } > "$failed"
  echo "FAIL  $start -> $end (exit $rc; log: $log)" >&2
  return "$rc"
}

failed_any=0
active=0

for (( start=FROM; start<=TO; start+=CHUNK_SIZE )); do
  end=$(( start + CHUNK_SIZE - 1 ))
  (( end > TO )) && end=$TO
  name="$(chunk_name "$start" "$end")"

  [[ -f "$DONE_DIR/$name.done" ]] && continue

  while (( active >= WORKERS )); do
    if wait -n; then
      :
    else
      failed_any=1
    fi
    ((active-=1))
  done

  run_chunk "$start" "$end" &
  ((active+=1))
done

while (( active > 0 )); do
  if wait -n; then
    :
  else
    failed_any=1
  fi
  ((active-=1))
done

done_chunks=0
missing_chunks=0
for (( start=FROM; start<=TO; start+=CHUNK_SIZE )); do
  end=$(( start + CHUNK_SIZE - 1 ))
  (( end > TO )) && end=$TO
  name="$(chunk_name "$start" "$end")"
  if [[ -f "$DONE_DIR/$name.done" ]]; then
    ((done_chunks+=1))
  else
    ((missing_chunks+=1))
  fi
done

echo
echo "Finished: $done_chunks/$total_chunks chunks complete."

if (( failed_any != 0 || missing_chunks != 0 )); then
  echo "$missing_chunks chunk(s) remain unfinished."
  echo "Run the same command again to retry only unfinished chunks."
  exit 1
fi

echo "All requested Starknet ranges completed successfully."
echo "You can now restart the Influence event processor/indexer and let them catch up."
