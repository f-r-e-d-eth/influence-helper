# 2026-10-30 — Mainnet archive consolidation and Downloader V1

## Why this was necessary

The main archive previously comprised many symbolic links into three staging directories (`aligned-mainnet-chunks/`, `mainnet-extension-chunks/`, `mainnet-new-chunks/`). Its first block ranges were inconsistent (`218425–218449` and `218425–218999`). Separate metadata files made an independently transferable IPFS chunk incomplete. The downloader targeted the old symlink arrangement and stopped at block 1,699,999.

The goal was a **single physical archive directory** containing self-contained and contiguous 1,000-block chunks, with a safe mechanism to re-check or replace existing data.

## 1. Align the first block range

- Changed the collector's minimum *allowed* start from `ORIGIN` to `218000` while retaining the historical first contract origin.
- Queried local Mainnet Juno (`http://127.0.0.1:6061`) for blocks `218000–218999`; result: **22 dispatcher events**, zero for the six other tracked contracts.
- Independently decompressed and compared the original first 25-block archive: **14/14 events identical** within `218425–218449`; **zero events** before `218425`.
- Initial collector SHA-256 of the new first archive: `d1b1c37015031907f5d9523fc0f42a02696687a94623eda8dec17fb1004d6eca`.
- Original partial chunk directories preserved under `/home/iu/Influence-Mainnet/Archive/original-partial-chunks/`.

## 2. Consolidate physical files

- Preflight verified **1,482 valid symlinks**, pointing to **111 + 341 + 1,030** actual directories across the three source folders; zero broken links.
- Moved all target directories directly into `mainnet-chunks/` without recompression, copying event data, or changing manifests.
- Installed `blocks_218000_218999` in place of two overlapping historical partial ranges.
- Numerically sorted range audit: **1,482** standard 1,000-block chunks, **218000–1699999**, no gaps, overlaps, or symlinks.
- `build_mainnet_chunks.py --verify-only` checked all archive manifests, payloads, counts, hashes and ordering and returned exit code **0**.

## 3. Make every chunk self-contained

Preferred on-disk layout:

```text
blocks_START_END/
  events.jsonl.zst
  manifest.json
  metadata.json
```

- Original metadata was previously in `mainnet-block-metadata/`.
- Tested a populated chunk `219000–219999` (**32 events**) and an empty chunk `220000–220999` (**0 events**).
- Generated missing metadata for the new first chunk: **22 event-bearing blocks, 22 events**.
- Copied **1,479** remaining metadata files; **3** chunks already had bundled metadata.
- Introduced `verify_chunk_metadata.py`; full check passed **1,482/1,482** chunks with exit code **0**.
- Original standalone metadata directory retained for reference as `/home/iu/Influence-Mainnet/Archive/mainnet-block-metadata-old/`.

Metadata includes event-bearing block hashes and timestamps and is linked to the archive SHA-256. This enables offline replay using the bundle, although a downstream replayer may need additional non-Starknet bootstrap data.

## 4. Downloader V1 and safeguards

The original V09 downloader was copied to `event-archive/v09/run_mainnet_download.py` locally before editing. The current downloader targets direct self-contained chunks, accepts an unrestricted future aligned range, and offers:

- Default: download missing chunks; compare existing chunks to Juno and local metadata.
- `--check-only`: independently verify existing chunks against Juno, read-only.
- `--overwrite`: on known corruption/mismatch, download replacement into staging, generate and validate metadata, retain a timestamped original under `replaced-chunk-backups/`, then install. Skip if identical.
- `--dry-run`: print intended block ranges.
- `--out`: safely test outside the production directory.

Comparison exit codes: **0** identical; **2** invalid local archive or metadata; **3** blockchain mismatch; **4** RPC failure/wrong network; **5** unexpected failure. Overwrite refuses RPC or unexpected comparison failures. A separate function `install_chunk()` isolates backup/replace/rollback logic.

### Real-world and automated tests

- End-to-end downloaded 22 events into `v10-download-test/`, generated metadata, and verified it.
- Independent comparison showed **events, blockchain identity, and metadata all identical** to the original first chunk.
- A higher-volume independent check of `999000–999999` matched **3,980/3,980** events.
- Overwrite created and preserved a valid backup.
- Deliberately corrupted the **test** metadata SHA-256; validator failed; overwrite repaired it and preserved corrupted original.
- Offline/unavailable RPC `127.0.0.1:6099` returned code **4**; overwrite stopped without a replacement or additional backup.
- Five Python `unittest` tests passed, covering exit code handling, new install, backup creation, replacement, and rollback on installation failure.
- Post-refactor `--check-only` ran successfully against Juno.

All destructive tests were limited to `v10-download-test/`. The **1,482 production chunks** and the V09 MongoDB reference were not modified by downloader tests.

## 5. Limitations and next steps

- Local checksum/metadata validation does **not** independently re-fetch timestamps from Juno; `--check-only` compares raw events and first/last block hashes with Juno and checks bundled metadata locally.
- Filesystem rename/rollback is not a fully crash-atomic transaction across power failures.
- Manifest creation timestamps mean that independently produced archives are **not yet guaranteed identical IPFS CIDs**.
- Full automated end-to-end fault injection and production release tests remain desirable.
- No IPFS upload, catalog, signed provenance, or fully deterministic epoch packaging has been completed.
- The local downloader, helper scripts, and tests should be migrated from the local `event-archive/` into GitHub `event-archive/1_Starknet-IPFS/`; this report documents observed results, not GitHub publication of those scripts.
- Desired future publication: immutable approximately **100,000-block epochs** with small **<=10 MB** delta units and canonical serialization.

## Reference paths

- Physical event archives: `/home/iu/Influence-Mainnet/mainnet-chunks/`
- Original metadata backup: `/home/iu/Influence-Mainnet/Archive/mainnet-block-metadata-old/`
- Older partial archives: `/home/iu/Influence-Mainnet/Archive/original-partial-chunks/`
- Local active scripts/tests (at time of experiments): `/home/iu/Influence-Mainnet/influence-helper/event-archive/`
- V09 database/checkpoints intentionally preserved.

**Caution:** These facts reflect experiments on 2026-10-30. Retest after changing scripts, RPC version, or publishing the GitHub implementation.
