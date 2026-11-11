# V12 — isolated Mainnet reconstruction prototype

**Status as of 2026-11-11:** local experimental V12 pipeline on `bahn`; completed checkpoint through Starknet block **299,999**. The 300,000–399,999 interval is being tested and is **not yet claimed complete** here. This document records the tested approach, not a published production release.

## Why V12 exists

The long-term goal remains to rebuild Influence from independently verifiable, IPFS-published Starknet event archives plus static asteroid/bootstrap data. **V12 currently retrieves directly from local Mainnet Juno** to validate event processing, data compatibility and recovery before replacing that input with Stage 1 archived data. Direct Juno retrieval is an interim implementation, not a change of vision.

## Current pipeline

```text
Official asteroid seed (250,000)
          |
          v
Dedicated V12 MongoDB bootstrap
          |
          v
Mainnet Juno -- worker.js retrieve -- events in MongoDB
          |
          v
exceptions/apply.js -- interval-specific, documented adjustments to decoded returnValues
          |
          v
worker.js drain -- original Influence handlers -- reconstructed MongoDB state
          |
          v
Controller checkpoint -- MongoDB archive + SHA-256 metadata + progress.json
```

- **Controller:** `controller.py`, orchestrated by `v12.sh`.
- **Worker:** `worker.js` runs inside an Influence server Docker image and reuses existing Starknet retriever and event handlers. No permanent edits to the upstream Influence server are required for exceptions.
- **Isolation:** `influence-v12-net`, `influence-v12-mongo`, database `v12_reconstruction`, worker `influence-v12-worker`, and dedicated `influence-v12-redis`.
- **Ethereum:** a dummy provider URL satisfies module initialization; V12 has not established that no future handler requires real Ethereum RPC.
- **Redis:** the original socket emitter initializes a Redis connection even during offline work. A dedicated local Redis container is used.
- **Checkpoints:** aligned intervals of 100,000 Starknet blocks, with full compressed MongoDB snapshots and recorded archive checksums. `unverified_candidate` metadata is verified on recovery; do not describe it as independently verified at creation time.
- **Recovery:** controller validates the prior archive, restores the dedicated V12 DB, skips completed intervals, and retries an interrupted interval. Restoration overwrites the **dedicated V12 database**, not other databases.
- **Local location:** `/home/iu/Influence-Mainnet/influence-helper/event-archive/v12/v12-prototype-v0.4/`; the running prototype files and large checkpoint archives are **not assumed published to GitHub**.

## Historical event exceptions

Certain early Influence events have shorter payloads than current handlers expect. Their decoded `returnValues` contain `NaN`, which Mongoose rejects during component construction. V12 therefore uses a separate exceptions stage **after retrieval and before drain**.

- Rules are documented in `exceptions/blocks_<start>_<end>.json`, one file per 100,000-block interval when needed.
- `exceptions/apply.js` validates event identity, expected values and/or strict batch structure and count before changing decoded fields in MongoDB.
- Raw blockchain `data` and `keys` remain unchanged.
- Rules must be repeatable: a second application should report zero additional changes.
- Unknown cases stop processing and require investigation; never silently skip an event or invent values.
- Corrections are provisional compatibility policy, **not proof of original on-chain field semantics**.

### Tested examples

| Block interval | Rule | Confirmed events | Adjustment |
| --- | --- | ---: | --- |
| 200,000–299,999 | Crew #1 initialization | 2 | Missing `actionRound` and `actionWeight` → 0 |
| 200,000–299,999 | Ship #1 initialization | 1 | Missing emergency/transit numbers → 0; transit entities → null |
| 300,000–399,999 | Crew v0 short-format batch | 3,857 | Missing `actionRound` and `actionWeight` → 0 |
| 300,000–399,999 | Ship v0 six-value events | 1,205 | Missing emergency/transit numbers → 0; transit entities → null |
| 300,000–399,999 | Ship v0 twelve-value events | 122 | Missing `transitArrival` → 0 |

The batch adjustments were tested against an **interrupted** interval and passed idempotency checks. That is not yet evidence that the final interval drain and snapshot succeeded.

## Local operating commands

Run only on `bahn`, from the local prototype path:

```bash
cd /home/iu/Influence-Mainnet/influence-helper/event-archive/v12/v12-prototype-v0.4
./v12.sh setup
V12_END=399999 ./v12.sh resume
cat artifacts/progress.json
```

`resume` **restores the last completed checkpoint into the dedicated V12 database** before attempting the next interval. Do not run a second controller concurrently. `./v12.sh rebuild` is destructive to dedicated V12 data and requires an explicit reset phrase; do not use it for normal recovery.

## Findings and remaining work

1. Confirm completion and audit results of interval 300,000–399,999. Do not claim its checkpoint until `progress.json` and the archive prove it.
2. Add automated tests for exact and batch exception rules, unexpected counts/values, missing files, idempotency, and database safety.
3. Ensure fresh `setup` automatically provisions dedicated Redis, rather than relying on a manually created service.
4. Record provenance and a machine-readable exception audit alongside each checkpoint.
5. Determine whether Ethereum RPC is ever required by handlers; a dummy provider only proves initialization.
6. Investigate nonfatal external marketplace/referral warnings separately.
7. Compare output state against verified historical references, and eventually replace direct Juno retrieval with verified Stage 1 IPFS chunks.
8. Establish archive retention and independent restore verification before scaling to Mainnet.

For prior experiments and the downloader's verified local archive format, see [Stage 1](../1_Starknet-IPFS/README.md) and [dated history](../history/).
