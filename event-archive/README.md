# Influence Event Archive (experimental)

Tools for rebuilding and independently verifying Influence blockchain event archives, starting with Starknet Sepolia and Mainnet.

## Verified so far (2026-10-16)

- A local Juno v0.16.7 node serves Sepolia JSON-RPC at `http://127.0.0.1:6060/v0_10`.
- Historical `starknet_getEvents` queries work, including continuation-token pagination.
- The Sepolia Dispatcher range 60,000–70,000 returned 355,422 events in the user's test.
- The resulting test dataset (~214 MiB JSONL) compressed to ~14.5 MiB with zstd -3 and ~9.07 MiB with zstd -19.
- In block 70,000, the raw event associated with transaction `0x2fc7fb03c906e358b11a891527270306c41353a1891853b29b5ad5b2c70066a` matched the `ComponentUpdated_Inventory` record in MongoDB (keys and data).
- The working Testnet MongoDB database is named `pre-release`; **do not modify it**.

These tests do not establish complete event coverage or prove that all game state can be rebuilt.

## Files

- `download_events.py`: standalone Python 3 downloader. Resumes by completed block-range file; supports pagination and retry. No dependencies beyond the standard library.
- `verify_block.py`: compare *all* archive events for a specified block/contract with records in the existing MongoDB database (read-only).
- `.gitignore`: excludes archives, credentials, snapshots and local outputs.

## Download sample (Sepolia)

```sh
export STARKNET_RPC_URL=http://127.0.0.1:6060/v0_10
python3 download_events.py --network sepolia --contract dispatcher --start 70000 --end 70000 --out events-testnet
```

For Mainnet set `--network mainnet`, and set `STARKNET_RPC_URL` to your Mainnet node's endpoint. **Do not store private RPC API URLs or secrets in the repository.** The tool refuses to silently reuse an existing output file with a different recorded query identity. Event files are written atomically with a sidecar metadata file.

## Verify a block against the existing Testnet MongoDB

```sh
python3 verify_block.py --file events-testnet/sepolia_dispatcher_70000_70000.jsonl --block 70000
```

Defaults: Mongo container `mongodb`, database `pre-release`. Requires `mongosh` inside the container and the container's `MONGO_INITDB_ROOT_USERNAME` / `MONGO_INITDB_ROOT_PASSWORD` environment variables. This is strictly read-only.

The verification compares event occurrence counts after normalizing numeric hexadecimal fields. MongoDB may retain a different subset of events or processing metadata; mismatches require investigation. Chain RPC is the primary source for completeness verification, not MongoDB alone.

## Roadmap

1. Verify entire blocks/ranges against independent RPC sources, including empty blocks and complete pagination.
2. Create versioned manifests with chain ID, contract list, range, block hash, SHA-256 and optional IPFS CID.
3. Define deterministic event ordering and finalized-range policy (reorgs).
4. Audit Ethereum events and off-chain / seeded state dependencies.
5. Replay into a disposable database; never replay directly into the working Testnet database.

**Important:** Raw RPC events omit explicit transaction/event indices; deterministic reconstruction must recover canonical event ordering before claiming full replay correctness.
