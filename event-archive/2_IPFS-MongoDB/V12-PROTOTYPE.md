# V12 — isolated Mainnet reconstruction prototype

**Status as of 2026-11-18:** local experimental V12 pipeline on `bahn`; replay completed through Starknet block **599,999**. An independent bootstrap-plus-six-delta reconstruction passed full BSON and metadata comparison across all 63 persistent collections. This is experimental validation, not a production release or proof of agreement with the original game backend.

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

The batch adjustments initially passed idempotency checks against an interrupted interval. The later clean replay completed through block 599,999. The delta validation below establishes equivalence to that replay, not independent validation of the historical exception semantics.

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

1. Replay through 599,999 is complete; preserve the checkpoint and investigate historical exception semantics and skipped operations.
2. Add automated tests for exact and batch exception rules, unexpected counts/values, missing files, idempotency, and database safety.
3. Ensure fresh `setup` automatically provisions dedicated Redis, rather than relying on a manually created service.
4. Record provenance and a machine-readable exception audit alongside each checkpoint.
5. Determine whether Ethereum RPC is ever required by handlers; a dummy provider only proves initialization.
6. Investigate nonfatal external marketplace/referral warnings separately.
7. Compare output state against verified historical references, and eventually replace direct Juno retrieval with verified Stage 1 IPFS chunks.
8. Independent delta reconstruction is verified through 599,999; establish longer-run retention, scaling and reproducibility before production.

For prior experiments and the downloader's verified local archive format, see [Stage 1](../1_Starknet-IPFS/README.md) and [dated history](../history/).


## Verified delta reconstruction — 2026-11-18

The producer retained a bootstrap archive and six cumulative snapshots at aligned 100,000-block boundaries. The consumer generated six compressed BSON delta packages offline using the V11 archive parser and delta engine, adapted for V12. Each package carries complete insert, replacement and deletion operations, starting and ending document fingerprints, and metadata. The source archives were checked against the producer's SHA-256 and size records before extraction.

| Interval | Compressed delta (approx.) | Application time |
| --- | ---: | ---: |
| 0–99,999 | 5.5 KiB | 10.84 s |
| 100,000–199,999 | 5.6 KiB | 10.86 s |
| 200,000–299,999 | 1.2 MiB | 12.42 s |
| 300,000–399,999 | 5.4 MiB | 15.09 s |
| 400,000–499,999 | 2.4 MiB | 15.21 s |
| 500,000–599,999 | 2.5 MiB | 16.08 s |
| Total | ~11.5 MiB | 80.50 s |

The bootstrap contains 750,004 documents and occupies approximately 19 MiB. Bootstrap plus six deltas occupies approximately 30.5 MiB versus 278 MiB for all seven cumulative archives (about 89% less storage). These figures are rounded local measurements; the application time excludes initial restore, delta generation and final independent verification.

### Verification procedure

1. Restore the bootstrap into a separate MongoDB 7 container, `influence-v12-delta-mongo`, database `v12_disposable_reconstruction`. Restore result: 750,004 documents, zero failures.
2. Apply the six packages in ascending block order. All six journals returned `applied_checkpoint_verified`.
3. Export the reconstructed database and independently compare the archived BSON documents, collection options and index definitions against the original checkpoint at block 599,999.
4. Exclude only `keyv`: it first appears in the 200,000–299,999 interval and has a TTL index on `expiresAt`. The excluded cache is time-dependent; its contents are not part of the verified equality claim.

Final output:
```text
Collections: 63
Metadata equal: True
Database equal: True
Verification seconds: 13.710712424945086
```

Reference archive SHA-256: `ea2f0e96a905de50f6d3919eb8e0548a6c5900394a9d2f33708f823ba57d85e4`.

The final replay reported 3,686 skipped marketplace updates, 760 skipped referral creations, 208 expected bootstrap warnings, and zero unexpected missing event references. The test proves that the delta consumer exactly reproduces the persistent state of this specific V12 replay; it does not independently validate the replay against on-chain semantics or the live backend.

Local implementation: `event-archive/v12/v12-delta/` (`generate_v12_delta.py`, `verify_v12.py`, adapted `delta.py`, `archive_input.py`, official-parser helper). Local evidence includes the six generation reports, application journals, and `artifacts/v12-final-verification.json`. These local source and evidence files are not assumed to be published. The next planned milestone is extending the experiment through block 999,999.
