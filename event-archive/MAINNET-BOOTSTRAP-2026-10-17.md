# Mainnet bootstrap field log — 2026-10-17

This is an **observed, date-stamped experiment**, not a production sizing estimate or a claim that the game-state database has been rebuilt. Host: Ubuntu `iu@bahn`. Values copied from terminal output, in the units printed by the corresponding tools. Preserve this as a reproducibility record; do not commit credentials.

## Snapshot and storage (2026-10-17)

- Snapshot source: Nethermind Juno Mainnet archive, file `juno_mainnet_v0.16.8_16283169.tar.zst`.
- Local compressed path: `/home/iu/Influence-Mainnet/snapshots/juno_mainnet_v0.16.8_16283169.tar.zst`.
- Download completed successfully with aria2c; reported average speed **6.2 MiB/s**.
- `ls -lh` reported **512G** for the archive; `zstd -lv` reported **511 GiB (548,936,523,079 bytes)** compressed. An earlier `zstd -t` success message displayed `571,215,278,080 bytes`, inconsistent with `zstd -lv`. Preserve both measurements; exact compressed-byte size should be rechecked with `stat -c %s FILE` if needed.
- `zstd -t` completed successfully (archive checksum verification). `zstd -lv` reported **1 frame**, **XXH64 checksum 59dd9f0b**, decompressed size unspecified.
- TAR payload: starts `./` and `./*.sst`; no enclosing database directory. Streaming `zstdcat | tar -tvf | awk` reported **531.9 GiB** extracted regular-file sizes.
- Extraction target: `/home/iu/Influence-Mainnet/juno-data` using `set -o pipefail; zstd -dc ... | tar -xvf - -C ...`.
- After extraction, `du -sh` reported **533G** for the database directory; `CURRENT` was not found, while `LOCK`, `MANIFEST-*`, `OPTIONS-*` and `marker.manifest.*` were present. Juno subsequently opened the database and synchronized blocks, confirming the directory was usable.
- Storage on `/dev/mapper/ubuntu--vg-ubuntu--lv`: **1.8T** total. Before extraction: **799G used / 957G available / 46%**; after extraction: **1.3T used / 425G available / 76%** (from `df -h`). Both the compressed archive and extracted database remained on this filesystem. **425G remaining is a point-in-time figure, not a guaranteed runway** for chain growth, compaction, MongoDB, Elasticsearch or other services.
- An external ~1 TB USB disk with ~800 GB free was available as a possible backup location but was **not needed for this extraction**. Do not assume that such a drive is safe for a live Juno DB.

## Juno deployments

Working Sepolia container (preserve unchanged):

```text
Container: influence-juno-1
Image: nethermind/juno:latest (observed)
Command: --db-path /var/lib/juno --network sepolia --disable-l1-verification
         --http --http-host 0.0.0.0 --http-port 6060
Bind mount: /home/iu/starknet-juno-data:/var/lib/juno
Host RPC: 6060
```

New independent Mainnet instance, started successfully:

```sh
docker run -d \
  --name influence-juno-mainnet \
  --restart unless-stopped \
  -p 127.0.0.1:6061:6060 \
  -v /home/iu/Influence-Mainnet/juno-data:/var/lib/juno \
  nethermind/juno:v0.16.8 \
  --db-path /var/lib/juno --network mainnet \
  --disable-l1-verification \
  --http --http-host 0.0.0.0 --http-port 6060
```

The pinned image `nethermind/juno:v0.16.8` was pulled and ran. Logs showed successive **Stored Block** entries at **16,283,480–16,283,519** after startup; a later RPC request reported head **16,291,650**. Do not mistake those observed heights for current synchronized tip. L1 verification is disabled for initial experimentation and must be assessed before production use.

RPC success, from `http://127.0.0.1:6061/v0_10`:

```json
{"jsonrpc":"2.0","result":"0x534e5f4d41494e","id":1}
```

This is **SN_MAIN**. Verified historical block queries and `starknet_getEvents` from the local Juno archive.

## First Mainnet Influence events

Official production config specifies `Starknet.originBlock=218425`, with Dispatcher contract `0x422d33a3638dcc4c62e72e1d6942cd31eb643ef596ccac2351e0e21f6cd4bf4`.

- `mainnet_first_block.py --block 218425`: valid block hash `0x3adf3a31ab806742294407c6667e70e2cfb1c1af37a64cf66a3983a134b543e`; zero events across seven queried Influence contracts (not an error).
- A Dispatcher range query **218425–218524**, page size 10, returned first events at **218428**, **218430**, **218432**, then events in **218433–218443**; continuation token `218444-565`. Names represented in the `data[0]` felt include `Asteroid`, `Crew`, `Crewmate`, `ArrangeCrew`, `AssignPublicPolicy`, `ChangeName`, `ClaimEarlyAdopterReward`, `DelegateCrew`, `ExchangeCrew`, `InitializeArvadian`. The events appear to be **registration/configuration activity**, not evidence of actual gameplay; don't classify by names alone.
- `mainnet_first_block.py --block 218500`: zero events across same seven contracts.
- **218428**: one Dispatcher event, block hash `0x6f6de05045faceb944d9711b9104042cd0afcb8f1e50ae69797740a19126818`, transaction hash `0x6b3823ec01c4516b3930355231acc6101b4572fd7d487d58373b58d94c150cf`; RPC `transaction_index=120`, `event_index=0`. The `data[0]` felt decodes as `Asteroid`, the following felt is the asteroid-contract address.
- The local Juno RPC **does return** `transaction_index` and `event_index`; preserve these canonical fields in archives. Earlier general statements that these fields are absent were incorrect for this provider/version.

Working raw-event lab import:

```sh
cd ~/Influence-Mainnet
python3 influence-helper/event-archive/mainnet_first_block.py \
  --rpc http://127.0.0.1:6061/v0_10 \
  --block 218428 --output mainnet-first-import --import-mongo
```

Result: **1 event retrieved, 1 imported, 0 rejected**; SHA-256 `70eeb6f4cd30379aa8944194328b711082695d65aa3cfd7f7aaafacff36c3867`. Isolated Docker container `influence-mainnet-lab-mongo`, DB `influence_mainnet_lab`, collection `raw_starknet_events`, Docker volume `influence-mainnet-lab-mongo-data`, network `none`. Existing Testnet `mongodb` container and `pre-release` database were not changed. **This is only raw JSONL event import, not decoded event processing or constructed game state.**

## Influence server worker facts

Read-only `docker inspect` captured existing Testnet workers:

| Worker | Image | Entrypoint | Command |
| --- | --- | --- | --- |
| `influence-eventprocessor` | `influence-server` | `docker-entrypoint.sh` | `npm run eventProcessor` |
| `influence-starkneteventretriever` | `influence-server` | `docker-entrypoint.sh` | `npm run starknetEventRetriever` |

The retriever has a **read/write bind mount** from `/home/iu/Influence/influence-server-update` to `/app`, plus a Docker volume for `/app/node_modules`. The processor inspect output listed **no mounts**, so the workers may run **different code revisions** despite using the same image name. Verify this before any Mainnet rollout.

The inspected processor uses `NODE_CONFIG_ENV=prerelease`, `NODE_ENV=development`, MongoDB `pre-release`, and Sepolia-facing provider settings. These must **never** be reused to start a Mainnet processor. Secrets and full environment-variable dumps intentionally omitted from this public document. The existing Redis and Elasticsearch are also Testnet services: use separated Mainnet equivalents.

## Related Testnet research and unresolved dependencies

- Sepolia historical Dispatcher sample: blocks **60,000–70,000**, **355,422** RPC events, compared to **352,533** MongoDB events, with **351,536** common occurrences, **3,886** archive-only, **997** Mongo-only. Some missing archive-only events may be ignored/unhandled handlers, and some Mongo-only events appear to be duplicated with differing log indices; this is **not** a final root-cause finding.
- An early Testnet inventory event at block **34,728** initialized an **empty** inventory. At block **37,744**, an `ArrivalRewardClaimed` transaction created/initialized a ship, filled inventory slots with explicit product quantities and assigned control. This confirms at least **some starter goods were on-chain**, **not** that all Mainnet marketplace stock was sourced on-chain.
- Static asteroid-related components may use published IPFS snapshots, which are a separate dependency from dynamic on-chain inventory state. Exact bootstrap and required checkpoint order remain to be validated.

## Next engineering milestones

1. Audit Mainnet production config and retriever + event processor startup; identify image/source revision, MongoDB schema/index initialization, Redis queues and environment separation. Do not launch the current Testnet workers against Mainnet.
2. Decide on durable archive formats, pagination, canonical ordering, checkpoint/restart rules and safe historical range batching. Reconcile handler filtering and duplicate-index findings.
3. Build a **disposable, separate Mainnet application database** (not just raw-event lab) and process one real decoded event end-to-end through upstream handlers.
4. Trace IPFS static asteroid data, Ethereum L1 events, game initialization, and resource seeding. Do not assume raw Starknet events alone cover all state.
5. Gradually replay history, monitor CPU/RAM/disk and synchronization, index into isolated Elasticsearch, then connect Mainnet API/client.
6. Validate representative asteroid/ship/warehouse/crew state against official game data via a private API key; do not commit the key.

## Recheck commands (read-only)

```sh
df -h ~/Influence-Mainnet
du -sh ~/Influence-Mainnet/juno-data
stat -c '%s %n' ~/Influence-Mainnet/snapshots/juno_mainnet_v0.16.8_16283169.tar.zst
docker ps --filter name=influence-juno-mainnet
docker logs --tail 15 influence-juno-mainnet
```

These observations are a **baseline dated 2026-10-17**, not a guarantee of future storage consumption or reproducibility across versions.

## IPFS package milestone (2026-10-17)

- First aligned raw event chunk `218425–218999`: **22 events**, compressed **3,033 bytes**, verified locally.
- Eleven contiguous chunks `218425–228999`: **76 events**, **8,034 bytes** of compressed payload. The first range is partial, followed by ten 1,000-block ranges. Packager output: `package_218425_228999` with `manifest.json`, and top-level `catalog.json`. Catalog schema `influence-event-catalog-v1`, initial package CID field null.
- Kubo Docker image `ipfs/kubo:v0.43.1` started as `influence-ipfs`, using separate persistent volume `influence-ipfs-data`; package directory bind-mounted read-only to `/export`. Peer ports 4001 TCP/UDP exposed; administrative API (5001) and gateway (8080) **not published to the host**. Daemon reported ready.
- Command: `docker exec influence-ipfs ipfs add -r --cid-version=1 /export/package_218425_228999`
- Resulting UnixFS directory CID (root): **`bafybeidqd2ckciwnimm4nhdsyogliybaq4ezv5wnw6hsiku7no5x5wuys4`**.
- Some empty 1,000-block archives had identical CIDs, demonstrating content reuse for identical files.
- A local `ipfs get`/bytewise comparison and external-peer retrieval are **next verification steps**, not yet observed as successful. IPFS content availability requires ongoing pinning and network reachability.
- The master `catalog.json` is not yet updated with the CID, published, versioned, or authenticated.

### External storage verification: Filebase (2026-10-17)

- Created an IPFS bucket on Filebase. Exported the locally added folder using `ipfs dag export <rootCID>` to a **14 KB CAR** file.
- Imported the CAR to Filebase and confirmed that the object shown in the bucket bears the **same UnixFS root CID**: `bafybeidqd2ckciwnimm4nhdsyogliybaq4ezv5wnw6hsiku7no5x5wuys4`.
- Successfully opened `https://ipfs.filebase.io/ipfs/bafybeidqd2ckciwnimm4nhdsyogliybaq4ezv5wnw6hsiku7no5x5wuys4/manifest.json` in a browser. The JSON reports 76 events, mainnet chain ID `0x534e5f4d41494e`, and the first archive range 218425–218999 with digest `d1b1c37015031907f5d9523fc0f42a02696687a94623eda8dec17fb1004d6eca` matching the local manifest.
- Direct public retrieval from the home Kubo node had stalled; diagnostics via `ipfs swarm addrs autonat` reported `Reachability: Private` (UDP 4001 unavailable directly). User prefers **not to open home-router ports**. Filebase CAR upload provides an outbound-only path.
- Verified **remote manifest retrieval**, not yet independent download and SHA-256 validation of all 11 compressed event files. Catalog currently has `cid: null` on disk; next step is to update that entry, verify the catalog, and consider publishing/versioning the catalog itself.


### Remote compressed-chunk integrity check

- Downloaded `218425_218999.jsonl.zst` from `https://ipfs.filebase.io/ipfs/bafybeidqd2ckciwnimm4nhdsyogliybaq4ezv5wnw6hsiku7no5x5wuys4/218425_218999.jsonl.zst` using `curl -fL`. Transfer succeeded, **3033 bytes**.
- `sha256sum /tmp/influence-ipfs-test.zst` returned **`d1b1c37015031907f5d9523fc0f42a02696687a94623eda8dec17fb1004d6eca`**, exactly matching the local package manifest. This confirms remote byte-for-byte integrity for this one chunk.
- User updated `~/Influence-Mainnet/mainnet-packages-v2/catalog.json` with the folder CID. `package_mainnet_chunks.py --verify-only --out ~/Influence-Mainnet/mainnet-packages-v2` reported `VERIFIED 1 packages, blocks 218425..228999`.
- Future steps: verify every remotely retrieved chunk, automate CAR creation/upload, publish and version the updated master catalog, test retrieval from a distinct provider, and eventually rebuild the full game database.
