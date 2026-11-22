# V12 historical reconstruction findings

Updated 2026-11-22.

The archive producer replays historical Influence events using current server handlers. Some old events were encoded under different schemas; others expose bugs in the current processing logic. This catalog distinguishes those cases. It is not a list of changes to raw blockchain data.

## Exception catalog

| Block interval | Event / issue | Count | Replay adjustment | Status |
|---|---|---:|---|---|
| 200,000–299,999 | Crew #1 initialization, missing fields | 2 | Missing actionRound and actionWeight become 0 | Applied and replay verified |
| 200,000–299,999 | Ship #1 initialization, missing fields | 1 | Missing emergency/transit numeric fields become 0; missing transit entities become null | Applied and replay verified |
| 300,000–399,999 | Crew v0 short-format payload | 3,857 | Missing actionRound and actionWeight become 0 | Applied and replay verified |
| 300,000–399,999 | Ship v0 six-value payload | 1,205 | emergencyAt, transitDeparture and transitArrival become 0; transitOrigin and transitDestination become null | Applied and replay verified |
| 300,000–399,999 | Ship v0 twelve-value payload | 122 | Missing transitArrival becomes 0 | Applied and replay verified |
| 400,000–499,999 | Mixed historical Ship formats | 72 short, 488 complete (560 total) | Missing transitArrival becomes 0 for validated short events; complete events unchanged | Implemented; interval replay verified; local rule file still to cross-check |
| 1,900,000–1,999,999 | Non-lot UseLot update during building prepaid agreement transfer | 1 observed | Proposed suppression of only the invalid decoded Unique event, preserving raw data and agreement transfer | Investigated; not yet applied |

Earlier counts are recorded in [V12 prototype documentation](../../2_IPFS-MongoDB/V12-PROTOTYPE.md). The mixed Ship rule counts and checks come from the locally supplied exceptions/apply.js inspected on 2026-11-22. The exact interval rule JSON files remain on Bahn; their identities, counts and any additional rules should be reconciled against this catalog. In particular the Crew batch engine supports mixed short/extended payloads; the precise interval-specific counts for that mode have not yet been established here.

## Detailed investigations

- [2026-11-22 — Prepaid agreement transfer produces invalid UseLot event](2026-11-22-prepaid-transfer-unique/README.md): normal resource extraction agreement transfer exposes an unconditional contract write and a non-lot server projection failure.

## How exceptions are handled

The isolated V12 producer has a separate exception stage after retrieval and before drain. Rules live in the local prototype's exceptions/blocks_<from>_<to>.json files, with exceptions/apply.js as the executor.

- Preserve raw blockchain data and event keys.
- Adjust only the decoded returnValues, under an explicitly documented rule.
- Validate exact event identity and expected values or strict batch structure/counts.
- Refuse unexpected values and counts rather than silently guessing.
- Repeat safely without a second mutation.
- After partial processing failure, resume from the last independently verified recovery archive, not a potentially inconsistent working database.

Existing rule types include individual field corrections, batch (Crew), shipBatch and shipMixedBatch. A proposed suppressEvent type would handle the precisely identified non-lot Unique update. It has not been implemented yet.

## Scope of verification

The early intervals were successfully replayed and independently reconstructed from BSON deltas through block 599,999. This proves equivalence to the defined V12 reconstruction, not independent proof of every historical correction's semantic correctness. The latest verified producer checkpoint reported on 2026-11-22 is 1,899,999.

Nonfatal skipped marketplace updates (3,686), skipped referral creations (760), and expected bootstrap warnings (208) were reported in the earlier run through 599,999, alongside zero unexpected missing event references. These are separate from the explicit exception rules above and should not be treated as fully investigated or automatically correct.

Other operational failures (MongoDB OOM, delta identity continuity thresholds) are not historical event exceptions.
