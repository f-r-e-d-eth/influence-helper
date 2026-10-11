# influence-helper

Small maintenance tools for running a private [Influence](https://www.influenceth.io/) stack.

The first tool in this repository is a **parallel, crash-resumable Starknet event backfill** helper. It was built while rebuilding an Influence prerelease database from Starknet Sepolia history.

## Why this exists

The Influence Starknet event retriever can replay a block range with:

```bash
npm run starknetEventRetriever -- --run-once --fromBlock=... --toBlock=...
```

That works well, but a full historical rebuild can be extremely slow when run as one sequential process. Early testnet ranges can contain tens of thousands of tracked events per 1,000 blocks, and writing them to MongoDB can take hours.

`parallel-starknet-retriever.sh` splits a large block range into independent chunks and processes several chunks at the same time.

It is designed around one requirement above all others:

> **A machine crash should be annoying, not catastrophic.**

Completed chunks are marked as done. If the machine, SSH session, Docker process, or retriever dies, run the same command again and only unfinished chunks are replayed.

---

# Parallel Starknet Retriever

File:

```text
parallel-starknet-retriever.sh
```

## Features

- configurable start and end block
- configurable number of parallel workers
- configurable chunk size
- one log file per chunk
- timestamped controller output
- `.done` marker only after a chunk exits successfully
- safe resume after interruption or machine crash
- unfinished chunks are replayed automatically
- duplicate controller protection with `flock`
- deterministic Docker container names
- clean SIGINT/SIGTERM shutdown
- `--status` mode
- no modification of Influence source code required

The Influence server already stores Starknet events with MongoDB upserts, so replaying an unfinished chunk is preferable to trying to guess exactly how far a partially completed bulk write got.

---

# Requirements

The helper assumes an Influence server stack with Docker Compose and a service capable of running:

```bash
npm run starknetEventRetriever
```

Defaults used by the script:

```text
Project directory: ~/Influence
Compose file:      compose-update.yaml
Compose profile:   indexer
Retriever service: influence-starkneteventretriever
Chunk size:        1000 blocks
Workers:           2
```

All of these can be overridden on the command line.

The Starknet node used by the Influence server should already be synced to the range you intend to retrieve.

---

# Quick start

Clone the repository:

```bash
git clone https://github.com/f-r-e-d-eth/influence-helper.git
cd influence-helper
```

You can either make the script executable:

```bash
chmod +x parallel-starknet-retriever.sh
```

or simply invoke it with `bash`.

Example:

```bash
bash ./parallel-starknet-retriever.sh \
  --from 75001 \
  --to 16414310 \
  --workers 16 \
  --chunk-size 1000
```

For a long-running SSH session, use `nohup`:

```bash
nohup bash ./parallel-starknet-retriever.sh \
  --from 75001 \
  --to 16414310 \
  --workers 16 \
  --chunk-size 1000 \
  > backfill-full.log 2>&1 &
```

Watch progress:

```bash
tail -f backfill-full.log
```

Typical output:

```text
[2026-10-11 08:42:31] Job:        75001 -> 16414310
[2026-10-11 08:42:31] Workers:    16
[2026-10-11 08:42:31] Chunk size: 1000
[2026-10-11 08:42:31] START 75001 -> 76000
[2026-10-11 08:42:31] START 76001 -> 77000
...
[2026-10-11 08:49:02] DONE  75001 -> 76000
```

---

# Recommended database rebuild procedure

This is the workflow that was successfully used while rebuilding a private Influence prerelease database.

## 1. Make sure Juno is synced

Example for Starknet RPC v0.9:

```bash
curl -s http://127.0.0.1:6060/v0_9 \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"starknet_blockNumber","params":[],"id":1}'
```

Record the returned block number. That can be used as the `--to` value.

## 2. Preserve the old MongoDB data

**Do not delete the previous database until the rebuild has been verified.**

In the tested setup MongoDB used a bind mount similar to:

```text
/home/iu/influencedata/mongodb-data/db
```

The old directory was moved aside before creating a fresh one.

Your paths may be different. Check first:

```bash
docker inspect mongodb --format '{{json .Mounts}}'
```

## 3. Stop historical-state consumers before the backfill

For a clean historical rebuild, stop the normal Starknet retriever, event processor and Elasticsearch indexer before bulk-loading historical events.

Example:

```bash
cd ~/Influence

docker compose -f compose-update.yaml --profile indexer stop \
  influence-starkneteventretriever \
  influence-eventprocessor \
  influence-indexer
```

MongoDB, Redis, Elasticsearch and Juno can remain running.

The helper itself starts temporary retriever containers using `docker compose run --rm`.

## 4. Backfill historical Starknet events

The prerelease configuration used during development had a Starknet origin block of:

```text
14380
```

Verify the correct origin block in your current Influence server configuration before relying on this number.

For dense early testnet history, smaller chunks can make crash recovery less expensive:

```bash
nohup bash ~/influence-helper/parallel-starknet-retriever.sh \
  --from 14380 \
  --to 75000 \
  --workers 16 \
  --chunk-size 100 \
  > ~/influence-helper/backfill-early.log 2>&1 &
```

For the much larger, generally sparser remainder of chain history, larger chunks are more practical:

```bash
nohup bash ~/influence-helper/parallel-starknet-retriever.sh \
  --from 75001 \
  --to CURRENT_JUNO_HEAD \
  --workers 16 \
  --chunk-size 1000 \
  > ~/influence-helper/backfill-full.log 2>&1 &
```

Worker count is hardware-dependent. Start conservatively and increase only after checking system load.

## 5. Check progress

For the exact job:

```bash
bash ~/influence-helper/parallel-starknet-retriever.sh \
  --from 75001 \
  --to CURRENT_JUNO_HEAD \
  --workers 16 \
  --chunk-size 1000 \
  --status
```

The state for each job is stored under:

```text
~/Influence/.starknet-backfill/
```

unless `--state-dir` is supplied.

Each job has:

```text
logs/       one retriever log per chunk
done/       successfully completed chunks
running/    informational running markers
failed/     failed chunk information
```

A complete run ends with:

```text
Finished: N/N chunks complete.
All requested Starknet ranges completed successfully.
```

## 6. Restart processing after retrieval completes

Once every historical chunk is complete, restart the normal workers so retrieved events can be processed into Influence component state and indexed into Elasticsearch.

Example:

```bash
cd ~/Influence

docker compose -f compose-update.yaml --profile indexer up -d \
  influence-starkneteventretriever \
  influence-eventprocessor \
  influence-indexer
```

Allow the event processor and indexer to catch up before judging the reconstructed game state.

---

# Crash recovery

This is the main reason the helper exists.

Suppose the requested range is:

```text
58160 -> 75000
```

with 100-block chunks and several workers.

At the moment of a crash, some chunks might be complete while others are halfway through a MongoDB bulk write.

After reboot, run **the exact same helper command again**.

The script:

1. reads the existing `.done` markers,
2. skips completed chunks,
3. treats chunks without a `.done` marker as unfinished,
4. replays those chunks from their beginning.

Do **not** resume by looking only at the highest block stored in MongoDB when parallel workers are used. Parallel processing intentionally completes block ranges out of order.

For example:

```text
58000-58999  DONE
59000-59999  RUNNING
60000-60999  DONE
61000-61999  DONE
```

The highest stored block does not prove that `59000-59999` is complete.

The helper's per-chunk state is the recovery source of truth.

---

# Stopping a running job

Find the controller PID if it was started with `nohup`, or use the shell job PID shown when it was launched.

Send SIGTERM:

```bash
kill PID
```

The current helper version attempts to stop its active temporary retriever containers before exiting.

A previous early version of the helper allowed worker subprocesses to inherit the controller's lock file descriptor. If using an old copy, update first:

```bash
cd ~/influence-helper
git pull
```

---

# Monitoring the machine

Parallel retrieval can turn MongoDB into the dominant workload.

Useful commands:

```bash
top
```

```bash
docker stats --no-stream mongodb
```

```bash
docker exec mongodb mongostat \
  --uri="mongodb://root:password@localhost:27017/pre-release?authSource=admin" \
  --rowcount 10 1
```

On a 24-thread test machine:

- 1 retriever often used roughly one CPU core effectively
- 10 retrievers drove MongoDB above 1000% CPU at times
- 16 retrievers worked successfully for the tested historical range

Those numbers are observations from one machine, **not recommended defaults**. MongoDB performance, storage speed, RAM and chain activity can change the useful worker count substantially.

---

# Choosing chunk size

A chunk is the smallest unit the helper considers complete.

Smaller chunks:

- recover less work after a crash
- create more Docker containers and log files
- add more orchestration overhead

Larger chunks:

- have lower orchestration overhead
- can be more efficient on sparse history
- redo more work if interrupted

Practical values used during development:

```text
Dense early testnet history: 100 blocks
Later / sparse history:      1000 blocks
```

---

# Why replaying an unfinished chunk is safe

The current Influence Starknet event service writes events with MongoDB `bulkWrite` + `updateOne(..., upsert: true)`.

The event identity filter includes:

```text
event
logIndex
transactionHash
removed: false
```

That means replaying a partially completed chunk updates/reuses already inserted event records instead of intentionally creating a second copy of the same event.

This is why the helper deliberately favors **replay the whole unfinished chunk** over trying to infer where a crash happened inside a bulk write.

---

# Notes from the original rebuild

The helper grew out of a real database recovery.

A stale/private prerelease database had historical gaps after the Starknet node was upgraded and resynced. Symptoms included:

- missing starter-mission campaign state
- stale random-event behavior
- missing newer lease-auction state
- transactions constructed differently from the official game

A full historical replay restored missing state that could not be reconstructed from only recent blocks.

The early testnet period was exceptionally dense. Sequential 1,000-block ranges sometimes took several hours. Parallel chunk retrieval reduced the wall-clock time dramatically and made repeated machine crashes manageable.

---

# Command reference

```text
--from BLOCK          First Starknet block
--to BLOCK            Last Starknet block
--workers N           Parallel retriever count (default: 2)
--chunk-size N        Blocks per resumable chunk (default: 1000)
--project-dir PATH    Influence directory (default: ~/Influence)
--compose-file FILE   Compose file (default: compose-update.yaml)
--profile NAME        Compose profile (default: indexer)
--service NAME        Retriever service
--state-dir PATH      Override state/log directory
--status              Print job progress and exit
-h, --help            Show help
```

---

# License

MIT. See [LICENSE](LICENSE).

Use it, change it, build on it — just don't blame the authors if your database catches fire.
