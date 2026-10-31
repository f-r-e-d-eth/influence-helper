# Influence Mainnet reconstruction

An independent, reproducible pipeline from **Starknet Mainnet events** to a locally reconstructed **Influence MongoDB**. Work is divided into two stages.

| Stage | Goal | Status |
|---|---|---|
| [1. Starknet → IPFS](1_Starknet-IPFS/README.md) | Collect, verify and publish self-contained event chunks | Local collector and downloader V1 tested; IPFS publication pending |
| [2. IPFS → MongoDB](2_IPFS-MongoDB/README.md) | Restore game state with deterministic historical replay | V09 reference complete through block 1,699,999; V10 under development |

**History:** [dated research and test notes](history/) — including [2026-10-30 archive consolidation](history/2026-10-30_STARKNET-ARCHIVE-CONSOLIDATION.md). Early code experiments are retained in [archive/](archive/).

## Current local data (not stored in Git)

On host `bahn`, the working archive is under `/home/iu/Influence-Mainnet/mainnet-chunks/`. At the 2026-10-30 checkpoint, all **1,482** standard 1,000-block chunks from **218000 to 1699999** were physically consolidated, with no symlinks. Every chunk contains `events.jsonl.zst`, `manifest.json`, and `metadata.json`; all 1,482 passed local verification. Two representative chunks (22 events and 3,980 events) were independently compared with Mainnet Juno.

**GitHub synchronization note:** The working downloader, metadata utilities, and automated tests were built and tested on `bahn` in `influence-helper/event-archive/` but are **not yet committed to their new Stage 1 paths on GitHub**. Do not assume the archived scripts in `archive/` implement the new V1 behavior.

## Principles

- Preserve canonical blockchain event order and contract identities.
- Keep event chunks self-contained and verifiable; no RPC connection should be required for downstream replay.
- Never modify the verified V09 backup or the working Testnet MongoDB to test new reconstruction code.
- Publish deterministic IPFS packages only after defining a canonical format and testing independent reproduction.

Older main overview: [2026-10-16 legacy notes](history/2026-10-16_LEGACY-OVERVIEW.md).
