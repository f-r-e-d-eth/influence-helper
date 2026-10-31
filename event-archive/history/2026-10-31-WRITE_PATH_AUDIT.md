# V9 replay MongoDB write-path audit

Source-only audit, 2026-10-31. No application modules were executed; no Docker,
MongoDB, replay, archive/backup inspection or network operations were performed.
No replay implementation was changed.

## Conclusion and scope

A complete manifest cannot be obtained by selecting documents whose `event.id`
points at one of the 1,024 replay events, or by installing only `save` middleware.
Confirmed source paths include component deletion, updates preserving an old
event reference, older activity updates, entity upserts from apparently read-only
helpers, batched index-queue insertion, and writes through a separate Keyv client.
Capture must start before import/initialization, include all clients, and retain
identities for deleted documents. Final-state verification needs the union of
both runs' changed keys and a demonstrably equal baseline.

The successful event comparison is user-supplied context. It establishes neither
which game-state branches ran nor which documents were written. This audit
identifies possible paths in source, not a measured write set for those events.

### Source provenance and citation convention

Repository paths below are relative to `influence-helper`. Application citations
use these explicit roots; `S/services/Components/Component.js:64` means line 64 in the corresponding
local file, not a verified container file:

- **S** = `/home/iu/Influence/influence-server/src/common/`.
- **U** = `/home/iu/Influence/influence-server-update/src/common/`.

The replay loads `/app/src/...` from the `influence-server` image. The Python
controller names the image but does not pin its source tree or image digest
(`event-archive/v09/run_mainnet_v9.py:345–356`). Neither checkout is proven to
match that image. Both were inspected; most detailed traces below use S, with
important U additions called out explicitly. Do not combine them into an asserted
runtime schema.

There is material version drift:

- S `package.json:74` pins Mongoose **8.5.1**; U `package.json:73` pins **8.24.4**.
  These package paths are immediately above each `src/` directory.
- Both declare `@keyv/mongo` `^2.1.8` (S package line 41, U line 40).
- U `services/Components/Component.js:86–89` adds a snapshot-block guard absent
  in S. Subsequent save/upsert line numbers shift by five.
- U adds StarterPack components and offchain purchase-confirmation handlers.
  The current comparator expects 41 component collection names
  (`event-archive/v10/compare_correctness.py:21–22,125–126`), not a schema proven
  from S. Counting source files is not counting registered collections.
- Mongoose and Keyv adapter implementation files were not present at the inspected
  `node_modules/mongoose/lib/model.js` and `node_modules/@keyv/mongo` locations
  in S. Exact middleware and driver behavior must be tested against the pinned
  replay dependencies; package declarations alone do not establish coverage.

## Architecture and replay boundary

1. `run_mainnet_v9.py:212–236` imports prepared JSON into **events** with external
   `mongoimport --mode insert --stopOnError`. This bypasses application middleware.
   `event-archive/prepare_mainnet_import.js:5–9,37–67` converts input through
   Starknet parser handlers and emits JSON; it does not itself call a Mongo write.
2. `run_mainnet_v9.py:316–363` launches the replay entry point in the application
   image. Marker/checkpoint files are filesystem state, not MongoDB writes.
   Backup handling uses `mongodump` and archive-validation
   `mongorestore --dryRun` (`run_mainnet_v9.py:420,440–449`); it is outside handler
   state mutation and is not a restore performed by this audit.
3. `mainnet_replay_v9.js:14–44` validates the database, overrides the URI used by
   Mongoose **and caches**, blocks socket/Web3 clients, registers the tagged entity
   plugin, disables auto-indexing and imports all models. It loads model files
   directly, rather than calling `S/storage/db/index.js:5–16`, the normal bootstrap
   that also connects Mongoose. `S/storage/db/models/index.js:1–21` eagerly loads
   auxiliary models, notification discriminators and components.
4. `mainnet_replay_v9.js:46–55` installs six Starknet contract mappings: Asteroid,
   Crew, Crewmate, Dispatcher, Ship and Sway. `S/lib/events/processor/config.js:62–76`
   maps standardized address plus event name to a class. Dispatcher merges system
   and component handlers (`S/lib/events/handlers/starknet/Dispatcher/index.js:1–12`).
5. `mainnet_replay_v9.js:69–108` connects, checks pending counts, and streams
   events in block/transaction/log order, awaiting `processEvent()` and then
   `finalizeEvent()` for each event. It does **not** invoke the ordinary processor
   loop or socket emission. `S/lib/events/processor/EventProcessor.js:30–33,80–89`
   does additionally emit sockets/cache chain height/schedule another iteration;
   those paths must not be attributed to this replay loop.
6. Handlers call services (often through the eager barrel
   `S/services/index.js:1–34`), which call models and other services. Entity plugin
   hooks and schema validation/save hooks add derived fields and secondary writes.
   The only handler `finalizeEvent` definition found in S is
   `S/lib/events/handlers/BaseHandler.js:55–57`: set `lastProcessed`, then save
   the event. Event schema timestamps also affect `updatedAt`
   (`S/storage/db/models/Event.js:15–21`).
7. Success/error paths disconnect Mongoose and explicitly exit
   (`mainnet_replay_v9.js:127–133`). This is not evidence that an independently
   owned Keyv connection, detached promises, or background workers have drained.

The isolated reference uses the same process/finalize structure
(`event-archive/v10/reference_replay_v9.js:98–108`). The V10 benchmark also has
**raw writes**: guard claim through `db.collection(...).updateOne` and imported
`Event.collection.insertMany`
(`event-archive/v10/benchmark_replay.js:164–181`). These are harness operations,
not V9 handler writes; capture them with phase labels and a separately reviewed
policy for the operational guard. Neither should disappear silently from scope.

## Write paths and capture strategies

“Confirmed path” means present and callable in the cited source, conditional on
handler/input/baseline state. It does not assert execution in the 1,024-event run.
Collection names without explicit schema overrides are conventional Mongoose
names; resolve the actual namespace from registered models/driver commands in a
future instrumented environment. Never guess namespaces from class names alone.

| Path / evidence | Affected collection or model | Mechanism and indirect effect | Suggested capture |
|---|---|---|---|
| V9 input import (`run_mainnet_v9.py:228–236`) | `events` | External `mongoimport` inserts generated IDs | Capture import as its own phase; server-side observation or pre/post event manifest. Mongoose hooks cannot see it. |
| Component handler → generic helper (`S/services/Components/Component.js:64–81,118–130`) | Explicit `Component_*` collections | `save()` existing overwritten/patched doc or new doc; keeps old doc in return value; validates first; ordering checks can return without writing | Document save hooks for attribution plus final acknowledged driver write/preimage capture; record old/new natural keys. U uses lines 125–131 after snapshot guard. |
| Create-only helper (same file `133–156`; U `138–161`) | `Component_*`, e.g. InternalSale | `updateOne` with `$setOnInsert`, upsert; existing match can be a no-op | Query middleware can log request/result. Determine actual inserts from acknowledged results/server changes, not attempted upserts. |
| Component removal (`S/services/Components/Component.js:32–47`) | `Component_Name`, agreement/policy components | `deleteOne` by explicit filter or `doc.uniquePath()` | Preimage and tombstone required; query hook alone supplies filter/count, not deleted identity. |
| Location component handler (`S/lib/events/handlers/starknet/Dispatcher/components/Location.js:20–31`) → location refresh (`S/services/Components/Location.js:14–40`) | `Component_Location`, `entities`, `indexitems` | Saves other entities' location docs, queues them and crew roster members; does not assign the new event to refreshed docs | Capture all resulting saves/upserts/queue batches, not just the primary component. |
| Location schema (`S/storage/db/models/Components/Location.js:60–66,87–88`) | `Component_Location` | Validate/save hooks recompute `locations` through database reads | Capture persisted postimage, not the object supplied before hooks. |
| Global tagged entity plugin (`S/storage/db/plugins/entities.js:23–66`) | `entities`; parent component's `entities` field | Validation derives entity list; `pre(save/updateOne/replaceOne)` runs entity `updateOne` upserts in awaited `Promise.all` | Capture nested writes separately. Register instrumentation before models; verify query-vs-document `this` behavior and hook order with actual dependencies. |
| “Read” entity aggregation helper (`S/services/Entity.js:46–61,170–174`) | `entities` | `getEntity/getEntities` upserts UUIDs before aggregation | Instrument the model/driver, not only functions named update/create. Called from delivery etc. |
| NFT service (`S/services/Components/Nft.js:84–111`) and transfer/bridge handlers (`S/lib/events/handlers/starknet/common/BridgedToL1.js:15–29`) | `Component_Nft` | `updateOne` upsert for ownership/card flags; can flag related crew; may retain old `event` while other fields change | Query hook + acknowledged before/after capture, including related keys. Card rendering/reset at Nft service line 57 is a worker path, not this replay loop. |
| Order service (`S/services/Components/Order.js:15–29`) | `Component_Order` | `updateOne` initial caller using seven-part key, without refreshing event reference | Full natural-key capture; not an event-ID selector. Buy/SellOrderCreated handlers call this at line 28 in their respective system files. |
| Prepaid agreement methods (`S/storage/db/models/Components/Agreements/Prepaid.js:7–19`) | `Component_PrepaidAgreement` | `resetStatus/updateToCancelled/updateToTransferred` call `save()` | Save capture must include custom methods; see handler `Dispatcher/components/agreements/Prepaid.js:40–43` and system agreement cancellation/transfer handlers line 31. |
| Activity creation (`S/services/Activity.js:9–36`) | `activities` | Find by transaction/log, `create` if absent; copied event data, no ObjectId event pointer | `create` uses document-save capture; preserve stored embedded event/data and derived fields, key by transaction/log. |
| Older activity resolution and reassignment (`S/services/Activity.js:133–158,243–275`) | `activities` | Save a prior start activity; `updateMany` changes unresolved ownership on older rows | Enumerate **all** matched keys/preimages, not only returned modified count. `DeliverySent.js:67` and `BuildingRepossessed.js:28` demonstrate callers. |
| Elasticsearch enqueue (`S/services/ElasticSearch.js:10–89,98–136`) | `indexitems` | `create` single row; `bulkWrite` of `insertOne` batches; related entities discovered by cursors | Explicit bulk interception or common driver/server capture; compare normalized row multisets including duplicate multiplicities. |
| Packed lot data (`S/services/PackedData/LotData.js:31–34,328,381,427–440`) → `S/lib/cache/LotData.js:4–13` | Keyv Mongo collection, exact namespace unresolved | `cacheInstance.set/delete`, including get-on-miss initialization; lease/building/crew status affects asteroid-wide packed data | Explicit Keyv/adapter capture or server capture across this separate client. Mongoose middleware is insufficient. |
| Notification services (`S/services/Notifications/Resolvable.js:10–43`, `CrewReady.js:15–47`, `LeaseExpiration.js:15–58`) | `notifications` discriminators | `updateOne` upserts; resolvable record points to Activity ObjectId; wall-clock and user-recipient predicates can suppress entire writes | Capture discriminator, stable identity, activity reference map, full postimage and branch outcomes; compare presence, not just timestamps. |
| Constants / entropy / asteroid-sale helpers (`S/services/Constant.js:12–37`, `Entropy.js:8–18`, `AsteroidSale.js:12–37`) | `constants`, `entropies`, `asteroidsales` models | `updateOne` upserts; Constant/AsteroidSale use update pipelines and event-time conditions | Driver/server postimages needed; do not interpret pipelines as ordinary `$set` patches. Keys: name / round / period (model indexes at Constant:15, Entropy:13, AsteroidSale:15). |
| Purchase referral (`S/services/Referral.js:8–25`) | `referrals` | `updateOne` upsert depending on pre-existing User/referredBy; called by AsteroidPurchased/CrewmatePurchased | Query capture and key `(buyer, referrer, entity.uuid)`; baseline user state and caught errors matter. |
| Sway claim (`S/services/SwayClaim.js:17–21`) | `swayclaims` | Update claimed flag by address/phase; no event reference | Capture query and matched key. TestnetSwayClaimed handler line 14 is registered source; applicability to Mainnet input unproven. |
| Sway withdrawal (`S/lib/events/handlers/starknet/Sway/WithdrawInitiated.js:15–22`) → `S/services/SwayCrossing.js:76–107` | `swaycrossings` | `create` or `save`, counters and embedded event array | Capture images and occurrence identity; no declared unique natural index in model (`S/storage/db/models/SwayCrossing.js:42–72`). Do not assume one row per amount/address. |
| Newer offchain grant handlers (`U/lib/events/handlers/starknet/Dispatcher/systems/OffchainCrewmateGranted.js:30–40`, `OffchainStarterPackGranted.js:30–56`) | `crewmatepurchases`, `starterpackpurchases`, StarterPack components, activities, queues | Direct **model** `updateOne` by `externalRef` (no upsert specified), component saves, queue batches | Capture at model/driver boundary; service-only wrappers miss the purchase updates. Present in U, not established for V9 image. |
| Model/adapter initialization | Potential collections/index metadata | V9 disables `autoIndex`, but does not explicitly disable `autoCreate`; Keyv may have separate initialization behavior | Observe initialization and namespace/DDL changes; actual implicit creates/TTL setup remain unverified without dependencies. Do not assume disabling Mongoose indexing covers the adapter. |

### Other mechanisms found, but not established as selected-replay branches

These must remain in the capture mechanism coverage matrix; their existence is
not evidence that the isolated Starknet replay invokes them:

| Mechanism | Source evidence and reachability boundary | Capture implication |
|---|---|---|
| `findOneAndUpdate` | `S/services/EventAnnotation.js:68`, `DirectMessage.js:78`; U purchase service also uses it (`U/services/CrewmatePurchase.js:271`). S EventAnnotated handler `.../Dispatcher/systems/EventAnnotated.js:13–22` creates an Activity, **not** an EventAnnotation | Separate query hook, not save middleware; use images/results for upsert ID and actual matched row. No confirmed S replay caller of these writes. |
| `findOneAndReplace` | `S/services/Event/Ethereum.js:23` retrieval path | Replacement query hook/driver capture; not the fixed Starknet replay input importer. |
| Event `bulkWrite/updateMany` | `S/services/Event/Starknet.js:43,54,83`; Ethereum service bulk at line 101 | Retrieval/status/reorg operations, not called by the replay loop; capture explicitly if scope expands. Unordered partial success must be recorded. |
| Additional `deleteOne` | `S/services/Event.js:27`; `S/services/Crossing.js:41` | Event administration / bridge-crossing helpers. S Starknet common bridge handlers update NFT/Activity/queue, not these Crossing helpers. Do not conflate Ethereum bridge paths with this run. |
| `deleteMany` | `S/services/Activity.js:223–230` purges removed events; `S/lib/notifications/Processor.js:169` purges notifications | No invocation by the fixed loop found. A running purge worker invalidates a handler-only capture assumption. Need per-record tombstones if enabled. |
| Consumer `bulkWrite(deleteOne)` | `S/lib/elasticsearch/Indexer.js:118,157` removes queue rows | Normal indexer consumer, not launched by V9 entry point. Quiescence must exclude it or capture its deletions. |
| Other `save/create/updateOne` | `S/services/User.js:21,29,36,42,52,83`; `Faucet.js:90`; `DirectMessage.js:103`; `Notifications/DirectMessage.js:16`; SwayCrossing increment/decrement helpers at 28,54,73 | API/worker or other-chain paths, not proven selected-loop writes. User saves at 42/52 are not awaited/returned, an explicit async risk if later made reachable. |
| `insertMany` / raw driver | No direct application `insertMany` call found in the searched S handler/service/model files; V9 imports through external tool; V10 raw insert at `benchmark_replay.js:181` | Model insertMany has its own model middleware if supported; raw collection/driver calls bypass schema hooks. Capture both independently. |
| `insertOne`, `replaceOne`, `deleteMany`, `updateMany` inside bulk | S queue uses nested insertOne; retriever and consumer bulk paths use other operations. Entity plugin registers replaceOne hook even though generic component replacement uses overwrite+save | Expanding one bulk request into operation types must not fabricate nested save/query hook execution. Support all bulk op kinds and partial failures. |

Search covered literal write-method calls in S `src/`, detailed handler/service/
model/cache reads, and U additions. No direct driver mutation, aggregation `$out`
or `$merge` stage was found in S common replay paths searched. `$mergeObjects`
in an **update pipeline** (`S/services/Crossing.js:83`) is not an aggregation
`$merge` output stage. Computed method names, external packages, imported code
and runtime model extensions cannot be ruled out by text search. This is a
mechanism inventory with explicit scope gaps, not a static proof of every
runtime call edge.

## Mongoose middleware versus explicit instrumentation

Expected coverage must be established by offline tests with the exact pinned
Mongoose version before using it as evidence:

| API | Suitable middleware layer | What hooks alone do not establish |
|---|---|---|
| Document `save`, including `create` and custom methods returning save | Document pre/post save; validation hooks derive fields first | Secondary writes can occur before the primary save fails. Hook snapshots may precede later hooks. Post-save is not a manifest durability guarantee. |
| `updateOne/updateMany`, `replaceOne`, `findOneAndUpdate/findOneAndReplace` | Explicit query middleware for each method; document updateOne needs explicit document/query options if used | Query `this` is not a hydrated target. Filter/update is not a list of affected keys or final documents; many/pipeline writes need images or controlled enumeration. |
| `deleteOne/deleteMany/findOneAndDelete` | Explicit query/document middleware as applicable | Deleted images are gone afterward. A count and filter cannot reconstruct tombstones; failed/partial operations must not be reported as successes. |
| Model `insertMany` | Separate model middleware | It is not equivalent to calling save for each row. Need generated IDs, ordered/unordered result and validation/partial failure behavior. |
| `bulkWrite` | Explicit interception plus driver/server observation; do not depend on individual operation hooks | Per-operation save/update hooks cannot be assumed. Model-level bulk-hook availability differs by version; verify 8.5.1 and 8.24.4 independently. |
| `Model.collection.*`, `connection.db.collection.*`, native MongoClient, Keyv | Common driver instrumentation for **every client**, explicit adapter interception, or server capture | No Mongoose schema middleware coverage. External mongoimport and server TTL deletes need observation outside the Node process. |

Install any plugins before model compilation and discriminator registration.
The existing entity plugin is restricted by `tags: ['useEntitiesPlugin']`; it is
not an audit plugin for every collection. Its `pre(updateOne/replaceOne)` calls
`instance.get(key)` (`S/storage/db/plugins/entities.js:11–14,29–45`), so query
context semantics and actual entity creation must be tested rather than assumed.
Do not change application hooks while adding observation.

A Mongoose debug logger is insufficient: it can describe attempted operations
without acknowledged results, preimages, generated server values, multi-match
membership or independent-client writes. Driver command monitoring is useful
for detecting uninstrumented mechanisms, but command requests/results still do
not generally provide updateMany/deleteMany per-document images.

## Identities, deletions, time and asynchronous completeness

- **Stable keys:** use declared, validated natural keys, not just `entity.uuid`.
  Inventory uses UUID+slot (`S/storage/db/models/Components/Inventory.js:22–23`);
  Order uses seven fields (`S/services/Components/Order.js:16–23`); PrepaidAgreement
  uses UUID+permission+permitted UUID (model lines 39–40). `uniquePathPlugin`
  (`S/storage/db/plugins/uniquePath.js:3–12`) only builds filters; it does not
  itself guarantee uniqueness or immutability. Validate actual indexes/schema
  later and preserve both old/new keys on changes.
- **Generated IDs:** imported events, new documents and array subdocuments can
  receive different ObjectIds in each run. Normalize references, not every
  field named `id`. S SwayCrossing embeds an events array with no `_id:false`
  (`S/storage/db/models/SwayCrossing.js:46–51`); U StarterPack building allowances
  similarly omit that suppression (model lines 11–14). Exact generation needs
  pinned-library fixtures. Preserve array order and gameplay IDs.
- **Event references:** `S/storage/db/schemas/ChainComponent.js:7–18` defines
  ObjectId `event.id`. Resolve to the full blockchain identity in the **same
  side's** event map. Historical references may predate the 1,024 events.
  NFT bridge updates can keep the prior reference while updating owners/bridge
  state; location refresh and order updates also need not replace it. Neither
  “reference in range” nor `updatedAt` provides a complete selector.
- **Reference closure:** notifications can point at Activity ObjectIds
  (`S/services/Notifications/Resolvable.js:28`); queue identifiers can be
  component ObjectIds (`S/services/ElasticSearch.js:12,25`) or entity UUIDs.
  Capture mappings before deletion; generated references may be serialized as
  strings (`doc.id || doc._id`). Do not silently accept string IDs as natural
  values. Current checker support is narrower: it resolves only `event.id` and
  recognized queue component references (`compare_correctness.py:150–194`),
  and unknown generated-ID collections fail. Notification, auxiliary identities
  and additional embedded IDs require reviewed schema policies before comparison.
- **Deletion:** tombstones must include namespace, old stable key and old ID
  mapping. Final absence is a comparison result; deleted/recreated keys, keys
  moved to new values, and insert-then-delete operations must remain traceable.
  Queue duplicates require multiset counts, not arbitrary row pairing.
- **Time:** event-only opt-in timestamp exclusion is not permission to ignore
  game-state timestamps. Activities and IndexItems use `timestamps:true`
  (S models Activity:47, IndexItem:7). Notification branches compare against
  `moment()` before writing (Resolvable:19, CrewReady:23, LeaseExpiration:28),
  and recipients depend on Crew delegation and baseline User/email preferences
  (`S/lib/notifications/utils.js:15–24`). Freeze/record the clock in a future
  controlled experiment, or report these as real presence/content differences.
- **Async and failures:** top-level events are sequential, internal calls can run
  concurrently. `Promise.allSettled` in DeliverySent (lines 27–37,60–64) continues
  with fulfilled values even after lookup/upsert failure. AsteroidPurchased
  catches referral and create-only component errors (lines 28–47). Finalization
  can succeed despite those failures; matched event records are not a write
  success audit. Capture acknowledged successes, partial successes and errors.
  Do not sort operation order across independent parallel tasks and assume
  identical final values follow.
- **Drain:** track in-flight operations and asynchronous event context through
  hooks, adapter calls and driver completion; stop only after all producers are
  closed and writes acknowledged. A zero in-flight count at one instant does not
  rule out future timer callbacks. Unexpected clients/timers/workers or writes
  after the closing barrier invalidate the completion marker. Exiting the
  process is not a replacement for flushing an external manifest.

## Recommended complete-manifest design

Use **snapshot diff as the initial correctness authority**, with instrumentation
for attribution and bounded follow-up verification. This is slower once, but is
available even if standalone MongoDB cannot support change streams, and avoids
claiming unproven hook coverage. Do not run it on the completed benchmark or V9.

1. In a later separately authorized experiment, use fresh disposable restores
   of one independently verified common snapshot and the pinned application
   image. Record snapshot checksum, schema/index inventory, source/dependency
   digests, exact ordered event identities/input hash, and clock policy. Confirm
   both restored baselines canonically match; shared filename/checksum is not
   sufficient to prove effective namespaces and restore options were identical.
2. Before any client initialization/import, obtain an external baseline manifest
   of every collection's canonical stable key, content digest, ObjectId mapping
   and reference closure. Unknown schemas fail closed. Stream to disk with
   bounded resources; do not load whole collections into memory. Preserve raw
   BSON/EJSON types and field-presence distinctions.
3. Record writes externally with sequence, side/run/phase, event identity via
   async context when available (initialization/import can have no event), actual
   namespace, mechanism, operation/batch index, filter/options, acknowledgement,
   failure/partial results, and available pre/postimages. Redact secrets in
   human-facing logs, but protect complete canonical artifacts needed for
   verification. Use separate files per side; do not write audit rows into Mongo.
4. Instrument the driver boundary for every Mongoose/Keyv client and explicit
   import phases; add service/middleware context only as attribution. If the
   disposable topology supports a database-wide change stream with retained
   preimages, use that as an independent committed-change feed. Establish it
   before writes and consume through a verified end barrier/resume token.
   Missing preimages, history loss, invalidation, dropped/new namespaces, DDL,
   capture errors or TTL/background writes must be accounted for or invalidate
   capture. `fullDocument: updateLookup` alone is not an exact intermediate
   postimage under later updates. Change-stream setup is future provisioning,
   not something the read-only checker should enable.
5. After producers drain and databases are quiescent, stream **all** final keys
   and digests and diff against baseline. Include additions, changes, deletions,
   metadata and unrecognized namespaces. This independent diff catches missing
   clients/hooks and same-count changes. It proves net state changes, not every
   transient write; complete operation-history claims still require gap-free
   server capture. Capture no-op attempts separately; do not require one change
   event for each attempted update.
6. Build the union of changed old/new natural keys and tombstones from **both**
   sides, including reference mapping dependencies. For queue/ambiguous-identity
   collections use normalized multiset deltas plus baseline multiplicities.
   Query both final databases for the union with bounded indexed reads. Rows
   needed only for reference resolution must not inflate compared-record counts.
   Compare full retained content, not just hashes of a partial projection.
7. Persist and flush a terminal marker only after artifacts and final manifest
   validation succeed; include counts, capture bounds, hashes, and coverage
   status. Missing marker/unsupported schema means incomplete, never equal.
   Baseline equality plus complete net-change coverage permits an explicitly
   scoped final-state equivalence claim; selected records or event-only results
   must retain `equivalent=null`. Strict wall-clock differences and any optional
   normalization must be reported separately and never broadened implicitly.

**Why not middleware only?** Multi-document matching, pipelines, external import,
Keyv, transient/deleted identity mappings, model compilation order and dependency
version drift all prevent a complete proof from the current hooks alone. A
future optimized capture-only run can replace repeated full scans only after
independent snapshot diffs validate its coverage and the runtime/schema boundary
is pinned. Existing final databases cannot retroactively supply missing
preimages or an operation manifest; a source audit cannot reconstruct them.

## Unresolved questions and evidence required

| Question | Current evidence / next evidence needed |
|---|---|
| Which image/source/dependencies actually ran? | Controller and JS paths confirmed; image filesystem/digest not inspected under today's rules. Need an independently supplied, hashed source/dependency export and run provenance. |
| Which handlers executed among 1,024 events? | Event equality supplied by user, but payload/handler distribution not audited. Need an offline, hashed identity/handler list; do not infer distribution from block range. |
| Exact Keyv collection, driver, implicit indexes/TTL behavior? | Separate Keyv URI/client confirmed; adapter source unavailable at inspected S paths. Need pinned adapter source and offline tests, later disposable validation. Do not assume a `keyv` collection or absence of TTL deletes. |
| Do Mongoose bulk hooks exist and cover this deployed version? | Two declared versions differ. Need version-specific local dependency source and offline hook matrix, including discriminators/document query options. |
| Can parent-hook side effects occur if parent save fails? | Entity upserts run in pre-save; no enclosing transaction in reviewed replay loop. Treat partial state as possible and test fault injection. |
| Are workers/implicit initialization writes present? | Not explicitly launched by entry point; eager imports and library initialization exist. Absence of other runtime processes/clients is not established by source alone. |
| Is a complete baseline manifest already available? | No such manifest supplied/read for this audit. Shared snapshot provenance must be verified separately; do not inspect or modify preserved backup to fill this gap today. |
| Are change streams/preimages supported? | Topology/server version/config not inspected. Prefer snapshot diff until separately verified; no provisioning proposed against current databases. |

## Small implementation plan for the next session

1. **Offline scope contract and fixtures:** obtain/pin source provenance; define
   manifest schema and immutable per-collection identities/reference policies.
   Build fixtures for compound keys, generated IDs, stringified references,
   old/new keys, tombstones, queue duplicates and unknown namespaces. Acceptance:
   unsupported identity/schema fails rather than omits records.
2. **Pure manifest diff:** implement disk-backed before/after EJSON digest/key
   diff with reference maps, cancellation and terminal marker validation, using
   fixture streams only. Acceptance: additions, deletions, same-count changes,
   delete/recreate, indirect updates and missing capture all produce expected
   results without MongoDB or application initialization.
3. **Offline capture prototype:** use fake models/drivers/Keyv/import adapters to
   exercise save/create, insertMany, every query operation, pipeline update and
   mixed ordered/unordered bulk partial failure. With pinned dependencies locally
   available, test middleware dispatch using stubbed collection methods only.
   Acceptance: awaited nested writes, detached-operation detection, failure after
   secondary writes and allSettled errors cannot produce a complete marker.
4. **Reconcile capture with snapshot fixtures:** require complete net diff to be
   covered by captured key/mapping union; deliberate bypassed Keyv/raw writes
   invalidate capture. Test end barriers and flush failure, interruption and
   reference closure without live databases.
5. **Stop at an externally reviewable experiment specification:** list resources,
   pinning, clock policy, capture topology and bounded read/write phases for
   fresh disposables. Obtain separate authorization before any restore/replay,
   Docker, MongoDB or network execution. Preserve all existing completed runs.

No implementation or runtime tests were required or performed for this audit.