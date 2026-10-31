#!/usr/bin/env python3
"""Compare an archived Influence chunk against fresh Mainnet RPC data."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from build_mainnet_chunks import gather, block_hash, verify
from mainnet_first_block import CONTRACTS, rpc
from verify_chunk_metadata import verify as verify_metadata

CHAIN_ID = "0x534e5f4d41494e"


class LocalError(Exception):
    pass


class MismatchError(Exception):
    pass


class RPCError(Exception):
    pass


def read_events(folder, manifest):
    archive = folder / manifest["archive"]["filename"]
    compression = manifest["archive"]["compression"]

    if compression == "zstd":
        result = subprocess.run(
            ["zstd", "-dc", str(archive)],
            check=True, capture_output=True, text=True
        )
        lines = result.stdout.splitlines()
    elif compression == "none":
        lines = archive.read_text().splitlines()
    else:
        raise ValueError(f"Unknown compression: {compression}")

    return [json.loads(line) for line in lines if line.strip()]


def checked_rpc(url, method, params):
    try:
        return rpc(url, method, params)
    except Exception as exc:
        raise RPCError(f"{method}: {exc}") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--rpc", default="http://127.0.0.1:6061")
    args = parser.parse_args()

    # Check connection and network before inspecting local files.
    chain_id = checked_rpc(args.rpc, "starknet_chainId", [])
    if int(chain_id, 16) != int(CHAIN_ID, 16):
        raise RPCError("RPC is not Starknet Mainnet")

    try:
        manifest = verify(args.folder)
        verify_metadata(args.folder)
        existing = read_events(args.folder, manifest)
    except (
        OSError,
        ValueError,
        KeyError,
        UnicodeError,
        subprocess.CalledProcessError,
        AssertionError,
    ) as exc:
        raise LocalError(
            f"{type(exc).__name__}: {exc or 'Local integrity check failed'}"
        ) from exc

    if manifest["contracts"] != CONTRACTS:
        raise MismatchError("Contract identities differ")

    start = manifest["from_block"]
    end = manifest["to_block"]

    try:
        fresh = []
        for name, address in CONTRACTS.items():
            events = list(gather(args.rpc, address, start, end, 100))
            fresh.extend(events)
            print(f"{name}: {len(events)} events", flush=True)

        first_hash = block_hash(args.rpc, start)
        last_hash = block_hash(args.rpc, end)
    except Exception as exc:
        raise RPCError(str(exc)) from exc

    fresh.sort(key=lambda e: (
        e["block_number"],
        e["transaction_index"],
        e["event_index"],
        int(e["from_address"], 16)
    ))

    if (
        int(first_hash, 16) !=
        int(manifest["boundary_block_hashes"]["first"], 16)
        or int(last_hash, 16) !=
        int(manifest["boundary_block_hashes"]["last"], 16)
        or existing != fresh
    ):
        raise MismatchError("Blockchain events or boundary hashes differ")

    print(
        f"CHECK PASSED: {start}-{end}, "
        f"{len(existing)} identical events",
        flush=True
    )


if __name__ == "__main__":
    try:
        main()
    except LocalError as exc:
        print(f"LOCAL ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except MismatchError as exc:
        print(f"BLOCKCHAIN MISMATCH: {exc}", file=sys.stderr)
        sys.exit(3)
    except RPCError as exc:
        print(f"RPC ERROR: {exc}", file=sys.stderr)
        sys.exit(4)
    except Exception as exc:
        print(
            f"UNEXPECTED ERROR: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        sys.exit(5)
