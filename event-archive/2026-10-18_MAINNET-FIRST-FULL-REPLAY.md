# First complete automatic Mainnet transaction replay — 2026-10-18

## Verified result

The original Influence handlers replayed all **30 parsed Mainnet events from block 219564** into fresh isolated database `influence_mainnet_replay_v2`, with **30 processed checkpoints and 0 pending**. Source: terminal output from the isolated lab MongoDB.

Nonempty collections and counts:

| Collection | Count |
|---|---:|
| Component_Building | 1 |
| Component_Celestial | 1 |
| Component_Control | 1 |
| Component_Crew | 1 |
| Component_Crewmate | 5 |
| Component_Inventory | 1 |
| Component_Location | 2 |
| Component_Nft | 6 |
| Component_Orbit | 1 |
| Component_PublicPolicy | 1 |
| Component_Ship | 1 |
| Component_Station | 1 |
| **Component documents total** | **22** |
| entities | 9 |
| activities | 12 |
| indexitems | 24 |
| events | 30 |

Crew #1 (`0x10001`) has roster `[15001,15002,15003,15004,15005]`.

Keyv packed lot cache `keyv:ASTEROID_LOTS_1` has 552,652 array entries, of which one is nonzero. Earlier examination found nonzero index 500706, value 32.

## Procedure and isolation

- Original `influence-server` image, handlers, Mongoose models and services. No original game source modifications.
- A temporary processing container joins `influence-mainnet-lab-mongo`'s isolated network namespace (`NetworkMode=none`). It accesses MongoDB only on `127.0.0.1:27017`.
- Replay code is `event-archive/mainnet_replay_run.js`, with required `--db=influence_mainnet_replay_v2`; both Mongoose and Keyv use the matching validated URI.
- Socket.IO Redis emitter and Ethereum Web3 provider are isolated at module-import time. Live WebSocket notifications are omitted; original handler logic, MongoDB IndexItem queue, and `finalizeEvent()` checkpoints are retained.
- `--seed-only` copied 30 original parsed events to fresh V2, retaining MongoDB ObjectIds. Replay was executed in limited sequential batches: 12, 1, then 17.
- `influence_mainnet_lab` (manual reference), `influence_mainnet_replay_test` (first interrupted replay) and live Testnet `pre-release` remain distinct.

## Discovery: partial replay is NOT necessarily safely retryable

First replay stopped while processing `CrewmateRecruitedV1` (event index 12) because `BaseMongoCache` uses `new Keyv(config.get('MongoDb.uri'))`, not Mongoose's existing connection. The failed handler had **already inserted** a recruitment activity containing an embedded event snapshot before encountering the error; its event checkpoint remained null. The handler returns early if the activity already exists, so a naive retry might permanently skip subsequent packed-lot and InternalSale operations. The first replay database was preserved for investigation.

V2 fixed Keyv config via `NODE_CONFIG` before loading handlers, then replayed successfully from a **fresh** database. It created the packed lot cache update.

## Outstanding limitations

- **22 component documents are not 41 component types**: only types with data for the first transaction have documents. The original app initializes empty component collections.
- `CrewmateRecruitedV1` wraps marketplace update and InternalSale creation in try/catch. Thus completed checkpoints do not guarantee every optional side effect succeeded. **No InternalSale documents** were found for crewmate 15001 during this test.
- `getPurchasePrice()` requires MongoDB constant `ADALIAN_PURCHASE_PRICE` not supplied by the 30-event archive. Existing authenticated Testnet reference stores it from Testnet Dispatcher `ConstantRegistered` block 71003 (2024-06-03), but must **not** copy Testnet values into Mainnet.
- A read-only search of Mainnet Dispatcher `ConstantRegistered` selector between blocks 229000 and 500000 found `ADALIAN_PRICE_ETH`, `ASTEROID_BASE_PRICE_ETH`, `ASTEROID_LOT_PRICE_ETH`, etc., **not** `ADALIAN_PURCHASE_PRICE`. This may be a handler/version mismatch or historically absent optional data. More investigation required.
- Compare actual reconstructed component values and event references to manual reference; investigate any startup seed data and other handler dependencies before extending replay to larger archives.
- Preserve ordering and complete historical provenance for eventual IPFS package generation.

**Next:** verify component equality for manual reference entities; consider automated per-component comparison and restart-safety checks before expanding event ranges.
