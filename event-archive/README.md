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
- `verify_range.py`: stream the MongoDB cursor and compare all archive events in a block range, one archive file at a time (read-only).
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

## Verify the historical sample (60,000–70,000)

Using the original local downloader filenames (which were named `asteroid_...` although the source was the Sepolia Dispatcher):

```sh
python3 verify_range.py --dir ../../events-testnet --start 60000 --end 70000
```

Run this from the `event-archive` directory inside the cloned repository, or pass the correct absolute path via `--dir`. Files are checked for contiguous coverage. The script compares hexadecimal-normalized transaction, contract, key and data fields, counting duplicate event occurrences. It reports discrepancies and returns a nonzero exit code when events differ. The script assumes the downloaded block files come from the specified contract and MongoDB is synchronized through the range; it does **not** prove independent blockchain completeness.


## Diagnose a specific discrepancy without modifying MongoDB

The 60,000–70,000 Sepolia Dispatcher comparison returned 355,422 archived events and 352,533 MongoDB events, with 3,886 archive occurrences missing from MongoDB and 997 extra MongoDB occurrences. The overlap was 351,536 matching occurrences. A preliminary investigation of block 66,961, transaction `0x00ce3d3ac1868cbfc87e90be3028687a72c8a03b7449863764cbce46c20cdd0a` showed 10 canonical Dispatcher receipt events but 18 MongoDB Dispatcher records, with two insertion timestamps (2026-10-10 and 2026-10-11 UTC). MongoDB block hash matched Juno. Do **not** conclude that all differences are duplicates: the missing archive events also require investigation.

From `~/Influence-Mainnet`:

```sh
python3 influence-helper/event-archive/diagnose_transaction.py \\
  --file events-testnet/asteroid_66000_66999.jsonl \\
  --block 66961 \\
  --tx 0x00ce3d3ac1868cbfc87e90be3028687a72c8a03b7449863764cbce46c20cdd0a
```

The diagnostic uses the normalized transaction and contract addresses, compares multiplicities of keys/data, groups records by creation timestamp, and reports statuses and block hashes. It is read-only. A nonzero exit status means the MongoDB event multiset differs from the archive.

**Decision rule:** event differences alone are not enough to recommend rebuilding the game state. First establish whether differences affect finalized canonical events, whether the event processor is idempotent, and whether derived component/entity state is wrong. Compare regenerated state in an isolated database against the current instance before choosing a destructive rebuild; never rebuild the working database as a diagnostic.

## Roadmap

1. Verify entire blocks/ranges against independent RPC sources, including empty blocks and complete pagination.
2. Create versioned manifests with chain ID, contract list, range, block hash, SHA-256 and optional IPFS CID.
3. Define deterministic event ordering and finalized-range policy (reorgs).
4. Audit Ethereum events and off-chain / seeded state dependencies.
5. Replay into a disposable database; never replay directly into the working Testnet database.

**Important:** Raw RPC events omit explicit transaction/event indices; deterministic reconstruction must recover canonical event ordering before claiming full replay correctness.
