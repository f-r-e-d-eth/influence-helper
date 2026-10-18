# Influence Mainnet V5 — verified replay (2026-10-18)

## Result

Historical archive of **111 chunks**, covering Starknet Mainnet blocks **218425–328999** from the seven tracked Influence contracts. Total **30,487 raw events**. All 111 immutable chunks have sidecar block metadata; the existing 31 V4 metadata sidecars were independently regenerated and found identical.

The original Influence parser produced **30,312 parsed**, **77 ignored**, **98 unhandled** and **0 failed** records. The 30,312-record JSONL was validated for valid JSON and unique transaction-hash/log-index identities. It was staged as `influence_mainnet_lab.mainnet_v5_staging_events` (30,312 records, 30,312 identities, no preprocessed or removed flags). **All 14,365 V4 parsed event identities are present in V5.**

`mainnet_replay_run.js` was extended with an explicit V5 source, expected count, first/last parsed event blocks (219564–328971), plus V3 and V4 ancestry validation. Original Influence handlers were not changed. Replay target: separate `influence_mainnet_replay_v5`, using the MongoDB lab container's isolated network namespace. The original V4 database and live Testnet `pre-release` database were not modified.

The replay completed **30,312/30,312 events processed, 0 pending**. At the first 1,081 events, V5 matched all 546 V3 component documents and had no differing component collections. At the V4 14,365-event checkpoint, V5 matched 12,921 V4 component documents with no differing component collections, and ownership of all 11,469 previously minted asteroids matched historical events.

## Final V5 verification

- **11,487 distinct asteroid IDs** in processed Transfer events; **11,487 correct current owners**, **0 missing Component_Nft**, **0 ownership mismatches**. Comparison used latest canonical transfer and numeric address normalization.
- **257 Component_Celestial** and **257 Component_Orbit** records; **257 matched asteroid IDs**, **0 Celestial without Orbit**, **0 Orbit without Celestial**.
- Other final document counts:

| Collection | Count |
|---|---:|
| Component_Crew | 432 |
| Component_Crewmate | 943 |
| Component_Ship | 421 |
| Component_Inventory | 421 |
| Component_Station | 1 |
| Component_Name | 2,396 |
| Component_Nft | 13,325 |
| entities | 13,812 |
| activities | 18,209 |
| indexitems | 26,673 |
| constants | 6 |

V5 added real orbital elements (`a`, `ecc`, `inc`, `raan`, `argp`, `m`) and physical asteroid properties such as `celestialType`, `mass`, `radius`, `bonuses`, and `scanStatus`. Asteroid type codes observed: 1 through 11. A Celestial/Orbit pair indicates matching physical and orbital records, **not** that full resource maps, scans or every gameplay dependency are proven complete.

Constants present: `ADALIAN_PRICE_ETH` (updated value), `ASTEROID_MERKLE_ROOT`, `RANDOM_STRATEGY`, `ASTEROID_SALE_LIMIT`, `ASTEROID_BASE_PRICE_ETH`, `ASTEROID_LOT_PRICE_ETH`. Earlier `ADALIAN_PURCHASE_PRICE` handler dependency remains unresolved and must not be assumed interchangeable with `ADALIAN_PRICE_ETH`.

## Limits and next work

- A completed replay checkpoint does not guarantee all handler side effects; original handlers can catch errors internally. Validate derived state and game API behavior independently.
- The 98 parser-unhandled raw events need classification.
- Only seven tracked contracts are archived; possible additional Starknet, Ethereum L1, or off-chain/IPFS dependencies remain unverified.
- Next priority: **read-only assessment of how to attach a separately configured original Influence server/client to the isolated V5 database**, with strict safeguards against changes to Testnet; then test representative real entities/components and missing resource/asteroid data.
- Longer-term goal: deterministic, safe, restart-aware, documented bootstrap from an empty database, with useful live progress output. Keep V4 and V5 verification checkpoints intact.
