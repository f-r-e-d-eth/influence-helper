# Influence Mainnet Downloader V1

## Purpose

Download and independently verify Influence Mainnet blockchain events.

- No MongoDB modifications
- No symbolic links
- Self-contained 1,000-block archives

## Archive format

Each chunk contains:

- `events.jsonl.zst` — compressed blockchain events
- `manifest.json` — block range, contracts, hashes, checksums
- `metadata.json` — block timestamps, hashes and status

Default storage:
`/home/iu/Influence-Mainnet/mainnet-chunks`

## Commands

Run from:
`/home/iu/Influence-Mainnet/influence-helper/event-archive`

### Preview

    python3 run_mainnet_download.py --start 1700000 --chunks 10 --dry-run

### Download missing chunks; compare existing ones

    python3 run_mainnet_download.py --start 1700000 --chunks 10

### Check existing chunks against Juno

    python3 run_mainnet_download.py --start 218000 --chunks 1 --check-only

### Repair corrupted or differing chunks

    python3 run_mainnet_download.py --start 218000 --chunks 1 --overwrite

### Run automated tests

    python3 -m unittest discover -s tests -p 'test_downloader.py' -v

Optional arguments:

- `--rpc URL` — alternative Starknet Mainnet RPC
- `--out DIRECTORY` — alternative archive location

Default RPC: `http://127.0.0.1:6061`

## Safety

- New downloads are staged and verified before installation.
- Existing chunks are compared against Juno.
- Identical chunks are not replaced.
- Overwrite requires the explicit `--overwrite` flag.
- Previous versions are preserved in `replaced-chunk-backups/`.
- Installation failures attempt to restore the original.
- RPC failures and unexpected comparison errors prevent replacement.
- No MongoDB operations are performed.

## Verification exit codes

`check_mainnet_chunk.py`:

| Code | Meaning |
|---|---|
| 0 | Archive matches Juno and local metadata is valid |
| 2 | Local archive or metadata error |
| 3 | Blockchain mismatch |
| 4 | RPC failure or wrong network |
| 5 | Unexpected error |

## Historical collection

- 1,482 self-contained chunks
- Blocks 218000–1699999
- All chunks passed local integrity verification
- Selected chunks independently compared against Juno

The original V09 downloader is preserved in `v09/`.

## Known limitations

- Replacement is not fully crash-safe against power loss.
- Metadata timestamps are checked for local consistency, but are not independently compared against Juno during `--check-only`.
- New chunks are not automatically published to IPFS.
- Manifest creation timestamps prevent guaranteed identical IPFS CIDs across independent publishers.
- Complete end-to-end failure injection is not yet covered by automated tests.

## Testing status

Five automated downloader tests passed on 2026-10-30.
