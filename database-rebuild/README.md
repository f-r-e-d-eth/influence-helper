# Empty database recovery

Status: **documented from a successful 2026-10 rebuild**

This folder contains a repeatable recovery procedure for rebuilding the Influence prerelease database from an empty MongoDB database.

The main helper is:

```
rebuild-database.sh
```

It deliberately does **not** delete or move an existing MongoDB database. Create/confirm the empty database yourself first. The script starts from the point where MongoDB, Juno, Redis and Elasticsearch are available and the Influence source tree / Compose stack already exists.

The procedure is based on the rebuild that successfully restored the private Influence stack in October 2026.

---

## Why this was needed

After the local Starknet/Juno stack had been upgraded and resynced, the existing prerelease database was no longer a reliable complete representation of chain history.

Symptoms included missing or stale game state. Replaying only recent blocks was not enough; the database had to be reconstructed from the historical Starknet events beginning at the Influence origin block.

For the tested prerelease configuration the historical origin was:

```
14380
```

Always verify this against the current server configuration before a future rebuild.

---

## Where the first rebuild got stuck

The obvious first attempt was to run the normal Influence Starknet event retriever sequentially across the whole historical range.

That turned out to be impractical.

The early testnet history is very dense. Some 1,000-block ranges took hours, and a long sequential replay eventually hard-froze the machine. A crash also made it difficult to know exactly which part of the historical range had completed safely.

That problem led to the repository's existing:

```
parallel-starknet-retriever.sh
```

The helper divides history into independent chunks, runs several retrievers in parallel and creates a `.done` marker only after a chunk exits successfully. Rerunning the same command resumes only unfinished chunks.

The successful rebuild used:

- **100-block chunks** for the dense early history
- **1,000-block chunks** for the later history
- up to **16 workers** on the tested machine

Those worker counts are hardware-specific, not universal recommendations.

---

## Successful recovery sequence

The recovery eventually required more than simply retrieving all events.

### 1. Start with an empty MongoDB database

The previous MongoDB data was preserved rather than deleted.

In the tested installation the bind-mounted database lived under:

```
/home/iu/influencedata/mongodb-data/db
```

The old database was moved aside before the new empty database was created.

This recovery script intentionally does **not** automate that destructive step.

### 2. Make sure Juno is fully synced

The successful rebuild used the current Sepolia head from the local Juno node.

RPC endpoint used by the stack:

```
http://127.0.0.1:6060/v0_9
```

The rebuild script can read the current block number automatically, or a fixed `--to` block can be supplied for a reproducible recovery.

The successful historical replay ended at:

```
16414310
```

That number is historical and should not be reused as the current head in a future rebuild unless intentionally reproducing that exact snapshot.

### 3. Stop normal historical-state consumers

Before bulk retrieval, stop the normal:

- Starknet event retriever
- event processor
- Elasticsearch indexer

MongoDB, Redis, Elasticsearch and Juno can remain running.

This prevents the normal consumers from racing against the historical import.

### 4. Retrieve the complete Starknet history in parallel

For the original rebuild:

```
14380 -> 75000
```

was treated as dense early history using 100-block chunks.

The remainder:

```
75001 -> Juno head
```

used 1,000-block chunks.

The parallel retriever is crash-resumable. If the PC or Docker dies, rerun the same command.

**Do not infer completion from the highest MongoDB block when using parallel workers.** Chunks finish out of order; the `.done` markers are the source of truth.

### 5. Synchronize MongoDB indexes before processing events

This was the next major recovery trap.

After all events had been retrieved, the normal event processor entered a restart loop because a fresh database did not contain every required MongoDB index.

One observed missing index involved:

```
DeliveryPackaged_unresolved
```

The fix was:

```bash
docker compose -f compose-update.yaml run --rm influence-server \
  node bin/sync_mongo_indexes.js
```

After synchronizing indexes, the processor could run and catch up.

This step belongs in every future empty-database recovery.

An upstream issue was filed because a fresh database bootstrap can miss required MongoDB indexes.

### 6. Start the processor/indexer and let them catch up

Restart the normal retriever, event processor and Elasticsearch indexer.

Do not judge the reconstructed game state until processing and indexing have caught up.

The recovery script pauses here deliberately because the exact catch-up indicator may change with upstream server versions.

### 7. Restore the legacy asteroid Orbit bootstrap

After the event replay had caught up, the client still crashed while rendering the world:

```
Cannot read properties of null (reading 'a')
[topic: updateAsteroidPositions]
```

The reason was that some old asteroids had never received an Orbit component through the modern event stream.

The repository still contains a legacy setup script:

```
bin/starknet-setup/asteroidsToOrbitComponent.js
```

It loads the original asteroid snapshot from IPFS. During the successful recovery the snapshot contained exactly **250,000 asteroids**.

The server environment required:

```
IPFS_GATEWAY_URL=http://ipfs:8080
```

The bootstrap completed with:

```
Completed: 250000
```

This restored the missing Orbit components.

### 8. Reindex asteroids in Elasticsearch

After the Orbit bootstrap the asteroid index was rebuilt:

```bash
docker compose -f compose-update.yaml run --rm influence-server \
  node bin/elasticsearch.js reIndex --indices asteroid
```

The normal Elasticsearch worker then consumes the queued index work.

This restored the world data expected by the client.

---

## Known post-rebuild anomaly: six extra asteroid documents

The successful rebuild exposed one additional bug.

After the asteroid reindex, Elasticsearch contained:

```
250006
```

asteroid documents instead of the expected:

```
250000
```

The six extra Elasticsearch records were phantom asteroid shells. Their numeric IDs were actually packed **LOT** IDs, but their UUIDs used entity label **3 (ASTEROID)**.

MongoDB itself remained correct with exactly 250,000 asteroid entities.

The six bogus UUIDs were:

```
0x6bb14000000010003
0x18a8bb000000010003
0x18a25c000000010003
0x1872d6000000010003
0x187c05000000010003
0x188242000000010003
```

Deleting those six exact Elasticsearch documents restored the count to 250,000 and the world rendered normally.

The exact producer of these six bad indexing requests is still unknown.

The full investigation is documented separately:

[../phantom-asteroid-elasticsearch/README.md](../phantom-asteroid-elasticsearch/README.md)

The rebuild script detects these known phantom documents. It does **not** remove them unless `--repair-known-phantoms` is explicitly supplied, and even then it first verifies that the corresponding IDs do not exist as asteroid entities in MongoDB.

---

## The recovery helper

Run:

```bash
bash database-rebuild/rebuild-database.sh
```

Defaults:

```
Project:          ~/Influence
Compose file:     compose-update.yaml
Origin block:     14380
Early split:      75000
Workers:          16
Early chunk:      100
Later chunk:      1000
Juno RPC:         http://127.0.0.1:6060/v0_9
```

For a fixed historical endpoint:

```bash
bash database-rebuild/rebuild-database.sh --to 16414310
```

To also repair the six known phantom asteroid records if they reappear:

```bash
bash database-rebuild/rebuild-database.sh \
  --repair-known-phantoms
```

For a more conservative worker count:

```bash
bash database-rebuild/rebuild-database.sh --workers 8
```

The script is intentionally guided rather than completely unattended. It pauses after starting the event processor/indexer so the operator can confirm that processing has caught up before running the legacy Orbit bootstrap and asteroid reindex.

---

## What the script does not do

It does **not**:

- delete an existing MongoDB database
- move old database files
- resync Juno
- modify Influence source code
- guess whether event processing has caught up
- blindly delete every asteroid above ID 250000
- treat 250000 as a permanent protocol rule

Those choices are deliberate safety boundaries.

---

## Verification after recovery

The final checks that mattered in the successful rebuild were:

### Mongo asteroid entities

```javascript
db.entities.countDocuments({label:3})
```

For the recovered historical dataset this was:

```
250000
```

### Elasticsearch asteroid documents

```bash
docker exec elasticsearch curl -s \
  'http://localhost:9200/asteroid_v1/_count'
```

After repairing the six phantom documents:

```json
{"count":250000}
```

### Client

The decisive functional test was simple:

> Open the Influence world.

Before the Orbit/bootstrap/index repair the world crashed. After the recovery and six-document cleanup it rendered again successfully.

A wallet transaction that had previously failed also succeeded after the broader server/database recovery.

---

## If recovery fails in the future

The most useful checkpoints are:

1. Is Juno synced to the requested `--to` block?
2. Did every parallel retriever chunk get a `.done` marker?
3. Were Mongo indexes synchronized **before** starting the event processor?
4. Is the event processor still restart-looping?
5. Has event processing caught up?
6. Did the Orbit bootstrap report 250,000?
7. Did the asteroid reindex finish?
8. How many asteroid entities exist in Mongo?
9. How many asteroid documents exist in Elasticsearch?
10. Did the six known phantom asteroid UUIDs return?

Add new evidence to this folder as future recoveries teach us more.
