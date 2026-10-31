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
  downloader/               # Published V1 collector, metadata builder, checker and instructions
  packaging/                # 100,000-block epoch and smaller delta/IPFS publication tools (future)
  specifications/           # Canonical chunk format, CID rules, provenance and verification (future)
```

Create the subdirectories when their implementation is ready; Git doesn't retain empty directories.

## Published downloader V1 — 2026-10-31

The [working downloader](downloader/) is published on GitHub (commit `9b73535`): six Python scripts and [operating instructions](downloader/DOWNLOADER.md). The relocated code passed syntax, dry-run, and a read-only real Mainnet Juno comparison (22 matching events, valid metadata, exit code 0) before publication.

The standalone local automated tests and earlier experimental scripts were deliberately **not** published in this clean Stage 1 folder. The original source and test files remain on `bahn`; historical variants in `../archive/` are not current V1 implementations.

Next: design deterministic content serialization, immutable ~100,000-block epochs, <=10 MB delta deliveries, a CID catalog, and independent IPFS verification. No IPFS publication is claimed yet.
