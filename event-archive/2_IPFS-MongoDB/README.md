# Stage 2 — IPFS → MongoDB

## Goal

Rebuild an independently usable Influence Mainnet MongoDB from the verified event and metadata archives produced by [Stage 1](../1_Starknet-IPFS/README.md), together with the official static asteroid/bootstrap dataset.

**Current development approach:** V12 is first proving reliable game-state reconstruction using **direct retrieval from local Mainnet Juno**. This is a temporary input path; the long-term stage remains archive/IPFS-driven replay.

## Current status — 2026-11-11

- **V09 historical reference:** completed through block **1,699,999**, with **12,049,410** processed events and zero pending, per the historical project record. Preserve original V09 backups and checkpoints.
- **V10 and V11:** earlier isolated reconstruction and benchmarking iterations. Their history and outputs remain useful as references, but they are not the active prototype.
- **V12 active local prototype:** dedicated MongoDB and Docker services, official **250,000-asteroid** bootstrap, 100,000-block checkpoints, original Influence Starknet retriever and event handlers, plus an explicit historical-exception stage.
- **Last confirmed completed V12 checkpoint:** block **299,999**. The next interval (300,000–399,999) is under test; do not assume it has completed.
- **Exception policy:** corrections affect decoded event `returnValues` after retrieval and before processing. Original blockchain event data stays unchanged; mismatches stop the run.

**Detailed current design, commands, verified test results, and caveats:** [V12 prototype and exception workflow](V12-PROTOTYPE.md).

## Pipeline

```text
Official static asteroid seed
    + local Mainnet Juno (current prototype)
       -> retrieve -> MongoDB events
       -> validated historical exceptions (if present)
       -> drain using original Influence handlers
       -> dedicated MongoDB checkpoint and SHA-256 metadata
```

**Target architecture:** replace the direct-Juno input with independently verifiable Stage 1/IPFS event archives, while keeping reconstruction deterministic and recoverable.

The working V12 code and full archive files live on `bahn`; this GitHub directory documents the approach and **does not claim the local prototype code or data has been published here**. Avoid mixing current Stage 2 replay work with Stage 1's downloader and IPFS packaging.

Historical experiments and reports: [../history/](../history/).
