# Non-lot UseLot event during prepaid agreement transfer

2026-11-22 — V12 reconstruction investigation. This is a working finding, not an upstream fix or a claim about the deployed production version.

## Observed evidence

Reconstruction and delta verification completed through block 1,899,999. The following interval failed after roughly 5,000 processed events with `Error: Entity not a lot` in the server's Unique handler. No OOM was observed.

- Block: 1,905,718
- Transaction: `0x054ec2469fb30769a89554dd8609f61d1879fcdab894c23f55de72c02166208f`
- Transaction index: 6
- Log index: 3
- Event: `ComponentUpdated_Unique`
- Raw data: `["0x2","0x5573654c6f74","0x2b660005","0x29120001"]`
- Decoded entity: Building 11110 (label 5)
- Decoded tenant: Crew 10514 (label 1)

The complete imported transaction contains a Crew update (log 0), two PrepaidAgreement updates (logs 1 and 2), the Unique update (log 3), and PrepaidAgreementTransferred (log 4). The agreement is for permission 12, EXTRACT_RESOURCES, not permission 1, USE_LOT. The old permitted crew is 10502; the new permitted crew is 10514. The old agreement ends at 1756414341 and the replacement ends at 1757277879. Both refer to the same building. A read-only aggregation over the imported decoded Unique events found 42,290 lot targets (label 4) and one building target (label 5). This count is bounded by the imported dataset, not all mainnet history.

## Contract behavior

The [TransferPrepaidAgreement system](https://github.com/adaliafoundation/influence-starknet/blob/f4d44c1704653fc2b795ca189ce760163d745368/src/systems/agreements/transfer_prepaid.cairo) exposes the run entry point and checks crew delegation, control of the permitted entity, an active agreement and absence of an existing permission for the new permitted entity. It updates both agreement records and then unconditionally writes the Unique key formed from the UseLot namespace and the target UUID. There is no lot-label or USE_LOT permission guard around that write. This explains the observed event sequence without requiring an administrative repair. The SDK exposes the system as a callable action. The contract test covers a lot lease transfer, not a non-lot resource permission transfer.

The [permission constants](https://github.com/adaliafoundation/influence-starknet/blob/f4d44c1704653fc2b795ca189ce760163d745368/src/common/config/permissions.cairo) and [policy validation](https://github.com/adaliafoundation/influence-starknet/blob/f4d44c1704653fc2b795ca189ce760163d745368/src/systems/policies.cairo) distinguish lot tenancy from building permissions. [Extraction](https://github.com/adaliafoundation/influence-starknet/blob/f4d44c1704653fc2b795ca189ce760163d745368/src/systems/production/extract_resource_start.cairo) checks EXTRACT_RESOURCES through assert_can_until; [access checks](https://github.com/adaliafoundation/influence-starknet/blob/f4d44c1704653fc2b795ca189ce760163d745368/src/common/access.cairo) resolve actual agreements and expiry independently of Unique tenancy. [Lot access](https://github.com/adaliafoundation/influence-starknet/blob/f4d44c1704653fc2b795ca189ce760163d745368/src/common/lot_access.cairo) reads the key for an actual lot. Acceptance and expired renewal similarly restrict lot-specific handling to lot targets.

Inference: the transfer of extraction rights is meaningful to gameplay. The extra building-keyed UseLot entry appears redundant for intended on-chain authorization and does not transfer a lot. No evidence of an exploit or deliberate admin intervention was found. The exact historic invocation and currently deployed class hash have not been checked against Juno; current source is not proof of deployment or UI availability.

## Server behavior

The current [Unique handler](https://github.com/adaliafoundation/influence-server/blob/main/src/common/lib/events/handlers/starknet/Dispatcher/components/Unique.js) filters the namespace and path length but not the target entity label. It writes Component_UseLot and queues indexing before calling unpackLot, which rejects the building. The failed replay may therefore contain partial writes. The [server's tenancy documentation](https://github.com/adaliafoundation/influence-server/blob/main/docs/lot-tenancy.md) describes UseLot as a lot projection, and explicitly permits non-crew *tenants*, which must not be confused with non-lot *targets*. [Existing handler tests](https://github.com/adaliafoundation/influence-server/blob/main/test/src/common/lib/events/handlers/starknet/Dispatcher/components/Unique.spec.js) cover assignment, clearing, and unrelated namespaces but not this case.

Both bugs remain in the inspected main source branches. Production impact and how the official backend handled the historical transaction are unknown. A likely upstream improvement is to guard the contract write by target label and permission and to reject or safely ignore non-lot targets in the server projection before any write; preserve raw events. The tenant type should not be restricted to crew.

## V12 recovery proposal (not yet applied)

1. Preserve the verified 1,899,999 archive; verify its SHA-256 against the checkpoint record.
2. Inspect the existing exception loader and its supported schema before editing. Match exact block, transaction hash, log index, event, raw data and original decoded values. Fail closed on mismatch.
3. For log 3 only, substitute an empty decoded returnValues object so the current Unique handler exits before side effects. Keep original raw blockchain data and both agreement events. Record the exception with this rationale. Do not relabel the building as a lot, discard the transaction, or silently ignore arbitrary future non-lot targets.
4. Restore the last verified archive into the isolated reconstruction database using the existing recovery workflow, not the partly modified database. Replay the interval and check the two agreement documents, no building-target UseLot projection, processed event status, and the delta verification report.
5. If similar events recur, evaluate a deterministic general projection guard with upstream agreement; keep count and provenance of skipped events. A database fingerprint proves agreement with the reconstruction's defined projection, not independent correctness of chain semantics.

## Outstanding checks before contacting upstream

- Read the historical transaction invocation and receipt via local archival Juno; establish the dispatcher system and caller path.
- Determine the registered/deployed TransferPrepaidAgreement class at the historical block and current accepted block if feasible.
- Check whether current production frontend offers transfer for building permissions and how production event processing handled this record (requires evidence from maintainers or production logs; not inferred).
- Confirm whether any later event clears or changes the same anomalous key.

No restart, upstream issue, or exception modification was performed as part of this documentation.
