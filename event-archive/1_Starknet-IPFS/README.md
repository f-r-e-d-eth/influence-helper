# Stage 1 — Starknet → IPFS

## Goal

Collect **Influence Starknet Mainnet** contract events, verify the historical blockchain range and publish self-contained, independently reproducible archives to IPFS. This stage must not require MongoDB.

## Current local format

```text
mainnet-chunks/
  blocks_218000_218999/
    events.jsonl.zst
    manifest.json
    metadata.json
  blocks_219000_219999/
    ...
```

Each directory covers **1,000 blocks**. Events are sorted by block, transaction index and event index; the manifest records contract identities, block boundaries and archive SHA-256; metadata covers timestamps, hashes and finality/status for event-bearing blocks.

### Verified milestone — 2026-10-30

- Re-collected first aligned range **218000–218999**, finding **22 events**, zero before the historical first Influence contract block (218425).
- Original **14 events** in blocks 218425–218449 matched the new archive exactly.
- Physically consolidated **1,482** directories (formerly 1,482 symlinks plus one original partial directory), archived two partial legacy chunks and installed the aligned first chunk.
- Verified contiguous coverage **218000–1699999**, **zero symlinks**, 1,482 valid archive checksums.
- Bundled metadata into **all 1,482** chunk directories; generated new first-chunk metadata; full metadata verifier passed.
- Implemented and tested downloader V1: download missing chunks, check existing against Juno (`--check-only`), protected `--overwrite` with backups, rollback, RPC-outage protection and five automated Python tests.
- Compared first chunk (**22 events**) and chunk 999000–999999 (**3,980 events**) directly with Juno; both identical.

Full narrative, commands and caveats: [2026-10-30 historical report](../history/2026-10-30_STARKNET-ARCHIVE-CONSOLIDATION.md).

## Planned structure

```text
1_Starknet-IPFS/
  README.md                 # This overview and current status
  downloader/               # Verified collector, metadata builder, checker and tests (to migrate)
  packaging/                # 100,000-block epoch and smaller delta/IPFS publication tools (future)
  specifications/           # Canonical chunk format, CID rules, provenance and verification (future)
```

Create the subdirectories when their implementation is ready; Git doesn't retain empty directories.

## Status and open work

**Locally implemented but not yet moved into this GitHub folder:** `build_mainnet_chunks.py`, `build_block_metadata.py`, `verify_chunk_metadata.py`, `check_mainnet_chunk.py`, `run_mainnet_download.py`, `DOWNLOADER.md`, and `tests/test_downloader.py`. The older code in `../archive/` is historical and may not reflect these updates.

Next: commit and rerun those local scripts in this stage; document their relative-path dependencies and run all tests. Then define deterministic content serialization, immutable ~100,000-block epochs, <=10 MB delta deliveries, a CID catalog, and independent IPFS verification. No IPFS publication is claimed yet.
