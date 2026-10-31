# First isolated Mainnet MongoDB event import — 2026-10-18

## Goal and safeguards

Demonstrate that historical Starknet Mainnet events archived in compact, verifiable chunks can be decoded through the **original Influence event handlers** and inserted into MongoDB without modifying the running Testnet database or the original game software.

- Testnet database: `pre-release` — untouched.
- Lab container: `influence-mainnet-lab-mongo`, `NetworkMode=none`.
- Lab database: `influence_mainnet_lab`.
- Lab collection: `bootstrap_test_events` (deliberately not the production `events` collection).
- Existing `raw_starknet_events` collection (one document) preserved.

## Archive inputs

- Block range: **218425–228999**, including zero-event intervals.
- 11 immutable source chunks; seven configured Mainnet contracts.
- 76 raw events in 45 event-bearing blocks.
- Original raw archive contents verified by SHA-256.
- `build_block_metadata.py` retrieved block metadata for the 45 event-bearing blocks into 11 separate sidecars. Block hashes were compared to the event archives.
- Metadata from the original Influence `Block` model: `status || finality_status` and `Number(accepted_time) || Number(timestamp)`.

## Original handler dry run

`dry_run_mainnet_parser.js` uses the original Influence handler implementations and explicit Mainnet contract addresses. It runs without network access and without database writes.

| Category | Count |
|---|---:|
| Raw events | 76 |
| Successfully parsed | **30** |
| Ignored `SystemRegistered` | **41** |
| Unhandled Dispatcher contract-registration events | **5** |
| Parser errors | **0** |

The five unhandled events register Asteroid, Crew, Crewmate, Ether and ReceivablesAccount contracts. The raw archive retains all 76 events. The parser exits cleanly after its report.

The 30 handled events comprise 14 event types, including component updates, transfers, recruited crewmates and construction events. In block **219564**, a 30-event transaction spans three contracts; its archived indices run from 0 to 29. The original RPC provider preserved these indices when queried directly (its returned array is contract-grouped, not sorted by index).

## Preview generation and import

Two scripts were added:

- `enrich_mainnet_events.py`: verifies chunk checksums, sidecar associations and event block hashes; streams the 76 raw events enriched with timestamp/status.
- `prepare_mainnet_import.js`: passes the enriched events through the original Influence handlers; emits 30 preview JSONL records.

The preview is held locally as `~/Influence-Mainnet/mainnet-import-preview.jsonl`. Example record:

- Event: `ComponentUpdated_Celestial`
- Block: **219564**
- Transaction: `0x076d5611db05a3e970286ffd75f0a50eb6544e7bf28d1ab6d9746e3b5d60ae6d`
- `logIndex: 0`
- `status: ACCEPTED_ON_L1`
- `timestamp: 1694612017` = **2023-09-13 13:33:37 UTC**
- `lastProcessed: null`
- Decoded asteroid mass: `1.3297303294068976e+27`

Import command, executed **only** on the isolated lab MongoDB:

```sh
docker exec -i influence-mainnet-lab-mongo mongoimport \
  --db influence_mainnet_lab --collection bootstrap_test_events \
  --type json --file /dev/stdin \
  < ~/Influence-Mainnet/mainnet-import-preview.jsonl
```

Result: **30 imported, zero failed**, 14 distinct event names. BSON checks confirmed `blockNumber`, `timestamp`, `logIndex`, `returnValues.entity.id` as `int`, `returnValues.mass` as `double`, and `lastProcessed` as `null`.

Created the same unique identity index used by the original Influence Testnet database:

```javascript
db.bootstrap_test_events.createIndex(
  { event: 1, logIndex: 1, transactionHash: 1 },
  { unique: true, name: "starknet_unique" }
)
```

MongoDB returned `starknet_unique`; the collection still contained **30 documents**. No duplicate indexed identities were found in this sample.

## What this proves — and does not prove

**Demonstrated:** archived Mainnet raw event payloads + verified block metadata can be converted by original Influence handlers into preview event records and stored in an isolated MongoDB collection with consistent field types and a uniqueness constraint.

**Not yet demonstrated:**
- Exact Mongoose schema/model equivalence (beyond sampled fields/index), including eventual production `events` collection semantics.
- Processing those records through the original Influence event processor into derived entities/components and other game-state collections.
- Historical seeding dependencies, Ethereum events, asteroid/IPFS seed data, or reconstruction of the whole playable world.
- Correctness at scale across every version of contracts/handlers and all Mainnet block ranges.
- Repeatable/resumable bulk import, idempotent checkpoints, complete replay ordering, consistency under crashes, or deployment security.
- IPFS metadata sidecar publication and updated package/catalog provenance. An earlier raw-only package was already uploaded and downloaded successfully; revisions will have different CIDs.

## Proposed next stage

1. Inspect the original Influence Mongoose `Starknet` schema and processing prerequisites, using read-only investigation.
2. Establish an isolated application environment (MongoDB and any required dependencies) for event processing, **without touching live Testnet services**.
3. Run the original event processor on a tiny, controlled Mainnet event subset and inspect actual derived state.
4. After end-to-end validation, finalize versioned event+metadata IPFS packages and automate integrity-checked bootstrap/import.

Do not reset `lastProcessed` on already processed events via `StarknetEventService.updateOrCreateMany`; that method is intended for live upserts and can requeue events. Do not change Testnet checkpoints or databases for a Mainnet experiment.
