# First reconstructed Mainnet game state — Asteroid #1 (2026-10-18)

## Result

Reconstructed **Asteroid #1** from Mainnet block **219564** using the original Influence Mongoose model and `ComponentService.updateOrCreateFromEvent()`.

The original service returned `Created: true` and `Updated: true`, and a subsequent query of the isolated lab MongoDB's `Component_Celestial` collection returned:

```json
{
  "entity": {"id":1,"label":3,"uuid":"0x10003"},
  "mass": 1.3297303294068976e+27,
  "radius": 375.14199999999255,
  "scanStatus": 2,
  "event": {
    "id": "6ad47065d5525b4fe60924aa",
    "timestamp": 1694612017
  }
}
```

This is **derived game-state data**, not merely the raw event archive.

## Safety and environment

- All experiments were confined to `influence-mainnet-lab-mongo` (`NetworkMode=none`), database `influence_mainnet_lab`.
- A temporary `influence-server` container used `--network container:influence-mainnet-lab-mongo`; MongoDB was accessed through `127.0.0.1:27017`.
- No connections to, or changes in, the working Testnet database or workers.
- Original `influence-server` code and Mongoose schema/plugins were used unchanged.
- Original archive files were left unchanged.

## Preparation

Previously 30 parsed Mainnet records were imported to `bootstrap_test_events`. They were subsequently copied, **preserving their `_id` values**, to the standard `events` collection of the isolated lab database. The `starknet_unique` index was created on `{event:1,logIndex:1,transactionHash:1}`.

The original app uses `mongoose.plugin(entitiesPlugin,{tags:['useEntitiesPlugin']})` and loads models from `/app/src/common/storage/db/models`. The important models are:

- `Event` → `events`.
- `CelestialComponent` → `Component_Celestial`, unique `entity.uuid` index.
- `ChainComponent` embeds `event.id` as an ObjectId referencing `Event`, plus `event.timestamp`.
- `EntitySchema` derives `uuid` from entity label/id via original Influence code.

## Reconstruction procedure

Executed a temporary Node.js process from `influence-server` using the original Mongoose plugin/model registration, connecting exclusively to the isolated lab database. It fetched the `ComponentUpdated_Celestial` event from block 219564 by the original `Event` model, then called:

```js
await ComponentService.updateOrCreateFromEvent({
  component: 'Celestial',
  event,
  data: { ...event.returnValues },
  replace: true
});
```

Result: `created=true`, `updated=true`. An independent read of the `CelestialComponent` showed the fields above, including an original event ObjectId reference.

## What remains

- This experiment ran **only the component service method** for one event, not the full event processor, Elasticsearch indexing, asteroid asset updater, or socket notifications.
- For a whole playable world, identify the required initialization/seeding state and processing order, set correct Mainnet handler addresses, and test a controlled batch of events in the isolated environment.
- Verify idempotency, partial failures, index creation and relationships for other components.
- Preserve metadata and source compatibility for eventual IPFS package publication.

Related earlier milestone: [First isolated Mainnet event import](MAINNET-MONGODB-IMPORT-2026-10-18.md).
