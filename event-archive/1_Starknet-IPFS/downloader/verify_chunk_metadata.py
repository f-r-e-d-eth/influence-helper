#!/usr/bin/env python3
"""Verify event archives and their bundled metadata."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def verify(folder):
    manifest = json.loads((folder / "manifest.json").read_text())
    metadata = json.loads((folder / "metadata.json").read_text())

    archive = folder / manifest["archive"]["filename"]

    with archive.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()

    assert checksum == manifest["archive"]["sha256"]
    assert metadata["archive_sha256"] == checksum, "Metadata archive SHA-256 mismatch"
    assert metadata["chain_id"] == manifest["chain_id"]
    assert metadata["from_block"] == manifest["from_block"]
    assert metadata["to_block"] == manifest["to_block"]

    compression = manifest["archive"]["compression"]

    if compression == "zstd":
        raw = subprocess.check_output(["zstd", "-dc", str(archive)], text=True)
    elif compression == "none":
        raw = archive.read_text()
    else:
        raise ValueError(f"Unsupported compression: {compression}")

    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert len(events) == manifest["event_count"]

    event_blocks = {}
    for event in events:
        number = event["block_number"]
        block_hash = int(event["block_hash"], 16)
        assert number not in event_blocks or event_blocks[number] == block_hash
        event_blocks[number] = block_hash

    blocks = metadata["blocks"]
    metadata_blocks = {
        block["block_number"]: int(block["block_hash"], 16)
        for block in blocks
    }

    assert len(metadata_blocks) == len(blocks)
    assert event_blocks == metadata_blocks

    print(f"PASS: {folder.name} ({len(events)} events)", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folders", nargs="+", type=Path)
    args = parser.parse_args()

    for folder in args.folders:
        verify(folder)

    print(f"VERIFIED: {len(args.folders)} chunks")


if __name__ == "__main__":
    main()
