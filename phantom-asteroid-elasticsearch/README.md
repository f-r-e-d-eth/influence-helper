# Phantom asteroid documents in Elasticsearch

Status: **workaround applied successfully; root cause still unresolved**

Date first documented: **2026-10-14**

## Summary

After rebuilding the Influence testnet data stack, the client crashed while loading the world with an error around:

```
Cannot read properties of null (reading 'a')
[topic: updateAsteroidPositions]
```

The immediate cause was six invalid asteroid documents in Elasticsearch. Their numeric IDs were not real asteroid IDs at all: they were valid packed **lot IDs** that had somehow been indexed with entity label **3 (ASTEROID)** instead of label **4 (LOT)**.

Deleting only those six stale Elasticsearch documents restored the asteroid index to the expected 250,000 entries and the world rendered again.

The bug that originally created those six records has **not** been identified yet.

---

## Affected IDs

The six bogus asteroid documents were:

| Packed lot ID incorrectly used as asteroid ID | Bad Elasticsearch entity UUID |
|---:|---|
| 1894544434003969 | `0x6bb14000000010003` |
| 6940920553406465 | `0x18a8bb000000010003` |
| 6933915461746689 | `0x18a25c000000010003` |
| 6881662889623553 | `0x1872d6000000010003` |
| 6891760357736449 | `0x187c05000000010003` |
| 6898619420508161 | `0x188242000000010003` |

All six packed lot IDs decode to lots on **asteroid 1**:

```
packedLotId % 2**32 == 1
```

The correct lot UUIDs end in label `0004`; the bogus asteroid UUIDs end in `0003`.

---

## What was observed

Elasticsearch contained six extra asteroid-shell documents:

- index: `asteroid_v1`
- entity label: asteroid / label 3
- IDs were huge packed lot IDs
- component data was effectively empty / null
- the six documents had consecutive Elasticsearch sequence numbers immediately after the initial 250,000 asteroid documents:
  - `_seq_no: 250000 .. 250005`

MongoDB, however, contained exactly:

```
db.entities.countDocuments({label:3}) == 250000
```

and none of the six bad IDs existed in Mongo as label-3 asteroid entities.

After deleting the six Elasticsearch records:

```
GET asteroid_v1/_count
=> 250000
```

and the Influence world rendered successfully again.

---

## Investigation results

### Mongo entity state

The six IDs exist as legitimate **LOT** entities (`label:4`), not asteroid entities.

No current component document was found with a root entity matching:

```
{ label: 3, id: <one of the six packed lot IDs> }
```

Nested component entity fields were also checked.

One initial match in `Component_Location.locations` was a MongoDB array-query false positive: one array element contained the lot and another contained asteroid 1. Rechecking with `$elemMatch` returned no malformed entries.

### Stored event data

A broad scan of `returnValues` showed the six IDs repeatedly as normal lot values.

Examples include:

- `ConstructionPlanned.lot`
- `ComponentUpdated_Location.location`
- `ComponentUpdated_Unique.entity`
- `ComponentUpdated_PrepaidAgreement.entity`
- `PrepaidAgreementAccepted.target`
- `SamplingDepositStartedV1.lot`

Where `ConstructionPlanned` also carried an asteroid entity, that asteroid was correctly:

```
{ label: 3, id: 1 }
```

No matching records were found for the six IDs in:

- `returnValues.tokenId`
- `returnValues.asteroidId`
- a stored event entity with `label:3`

So the stored event data does not appear to be the origin of the bad asteroid IDs.

### Entity packing / SDK

The current SDK entity packing is:

```
uuid = id * 65536 + label
```

An older SDK version tested (`2.2.0-beta.1`) used the same entity IDs and the same packing scheme.

This makes an SDK UUID-format migration unlikely to be the cause.

### Elasticsearch queue path

`ElasticSearchService.queueEntityForIndexing(entity)` preserves the entity UUID it is given.

The low-level queue service itself does not relabel lots as asteroids.

The Elasticsearch Indexer also does not create follow-up queue items; it only deletes consumed `IndexItem` records after a successful indexing batch.

Therefore some upstream code must at some point have supplied an entity equivalent to:

```
{ id: <packed lot id>, label: 3 }
```

or the corresponding UUID.

### Mongo entities plugin

The components use an `entitiesPlugin` which upserts all embedded `EntitySchema` values into the Mongo `Entity` collection.

No obvious code path was found that deletes Entity documents.

Because the six bogus asteroid entities are absent from the current Mongo Entity collection, the theory that they were created there temporarily and later automatically removed is weak.

### Constructor searches

Current code was searched for:

```
Entity.Asteroid(...)
```

The obvious sources are asteroid NFT transfer / bridge / legacy Ethereum handlers and normal lot-unpacking helpers.

No event data matched the six bad IDs in the corresponding `tokenId` or `asteroidId` fields.

A comparison of `Entity.Asteroid(...)` usage before and after server merge commit `30849bb` showed no difference.

A git-history search for obvious historical calls such as `Entity.Asteroid(lot...)` also returned nothing.

### ConstructionPlanned correlation

Each of the six lots has a historical `ConstructionPlanned` event, but this is not specific enough to identify the bug.

There are thousands of `ConstructionPlanned` events on asteroid 1, so this correlation is currently considered a red herring.

---

## Workaround used

First verify that the bogus entities are absent from Mongo as asteroids and that the Elasticsearch documents are clearly invalid.

Then delete only the exact six Elasticsearch document IDs:

```bash
docker exec elasticsearch curl -s -X POST \
'http://localhost:9200/asteroid_v1/_delete_by_query?refresh=true' \
-H 'Content-Type: application/json' \
-d '{
  "query": {
    "terms": {
      "_id": [
        "0x6bb14000000010003",
        "0x18a8bb000000010003",
        "0x18a25c000000010003",
        "0x1872d6000000010003",
        "0x187c05000000010003",
        "0x188242000000010003"
      ]
    }
  }
}'
```

Observed result:

```json
{
  "total": 6,
  "deleted": 6,
  "version_conflicts": 0,
  "failures": []
}
```

Verify the final asteroid count:

```bash
docker exec elasticsearch curl -s \
'http://localhost:9200/asteroid_v1/_count'
```

Expected result for this dataset:

```json
{"count":250000}
```

After this repair, the client successfully rendered the world again.

---

## Important caution

Do **not** blindly delete every asteroid with an ID above 250000.

The safe approach is:

1. identify the suspicious Elasticsearch document,
2. verify that the same UUID / ID does not exist as a real asteroid entity in Mongo,
3. inspect its component data,
4. confirm that the numeric ID is actually a valid packed lot ID or otherwise clearly invalid,
5. then delete only the exact stale Elasticsearch document.

The current dataset happens to contain 250,000 real asteroids, but that count should not be treated as a permanent protocol rule.

---

## Possible next steps

If the problem returns, the most useful next step is instrumentation rather than more static grep searches.

### 1. Add a temporary guard in `queueEntityForIndexing`

Log a stack trace whenever an asteroid entity is queued with an implausibly large ID, for example:

```js
if (_entity.isAsteroid() && _entity.id > 250000) {
  console.error('Suspicious asteroid queued', _entity, new Error().stack);
}
```

Do not make the numeric threshold a permanent rule; use it only as temporary diagnostics for this dataset.

This would identify the exact caller if the bug occurs again.

### 2. Inspect the pre-repair queue timing

The six bad Elasticsearch records had sequence numbers `250000..250005`.

If old logs or backups still exist, correlate:

- when the 250,000-asteroid reindex ran,
- when the event processor was active,
- when the six additional `IndexItem` records were created.

That may reveal whether the phantoms were created during replay, legacy bootstrap, or immediately after reindexing.

### 3. Search historical server revisions more broadly

The current and immediately pre-merge code did not expose an obvious constructor bug.

If necessary, search older commits for any code that:

- takes a lot ID,
- constructs an asteroid entity,
- manually sets `label: Entity.IDS.ASTEROID`,
- builds an Entity without using `Entity.Asteroid(...)`,
- or passes a numeric location/target into an asteroid constructor.

### 4. Add a consistency checker

A small helper script could compare:

- Mongo asteroid entities,
- Elasticsearch asteroid IDs,
- component presence,

and report Elasticsearch-only asteroid shells.

That would make future recovery much faster and safer.

---

## Current conclusion

The direct corruption is repaired.

Known facts:

- Mongo currently has the correct 250,000 asteroid entities.
- Stored events represent the six affected IDs as lots, not asteroids.
- Elasticsearch had six extra asteroid-shell documents using packed lot IDs with asteroid label 3.
- Deleting those six Elasticsearch records restored the client world.
- The exact producer of those six incorrect indexing requests remains unknown.

This folder should be updated if new evidence appears.
