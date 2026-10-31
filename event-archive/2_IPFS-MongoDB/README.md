# Stage 2 — IPFS → MongoDB

## Goal

Rebuild a usable Influence Mainnet database from the verified event and metadata archives produced by [Stage 1](../1_Starknet-IPFS/README.md), plus the required static IPFS asteroid/bootstrap data.

## Reference and status

- **V09 historical reference:** completed through block **1,699,999** with **12,049,410** events processed and zero pending. The original database volume, checkpoints and final backup are to be preserved.
- **V10:** isolated MongoDB seeded with a verified 250,000-asteroid baseline; replay engine under development and performance experiments pending.
- Key work: deterministic event ordering, original handler correctness, checkpoint/restart safety, MongoDB/Elasticsearch replay performance, and state equivalence to V09.

This GitHub directory is presently a documentation placeholder. Working code and tests on `bahn` have not yet been moved here. Avoid mixing it with Stage 1's downloader and IPFS-format work.

Historical experiments and dated reports: [../history/](../history/).
