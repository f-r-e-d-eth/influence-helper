#!/usr/bin/env python3
"""Fetch Influence-compatible block metadata for existing immutable Mainnet chunks.

Reads and verifies existing chunk manifests, queries local Mainnet RPC, and writes
NEW sidecar JSON files in a separate output directory. Never edits chunks.
Requires only Python stdlib and zstd (for compressed archives).
"""
import argparse
import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path

CHAIN_ID = "0x534e5f4d41494e"

def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()

def rpc(url, method, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as response:
        reply = json.load(response)
    if "error" in reply:
        raise RuntimeError(f"{method}: {reply['error']}")
    return reply["result"]

def raw_events(archive, compression):
    if compression == "zstd":
        p = subprocess.Popen(["zstd", "-dc", str(archive)], stdout=subprocess.PIPE)
        try:
            for line in p.stdout:
                if line.strip():
                    yield json.loads(line)
        finally:
            p.stdout.close()
        if p.wait() != 0:
            raise RuntimeError("Failed to decompress " + str(archive))
    elif compression == "none":
        with archive.open("rb") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)
    else:
        raise ValueError("Unsupported archive compression: " + compression)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rpc", default="http://127.0.0.1:6061/v0_10")
    ap.add_argument("--chunks", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if int(rpc(args.rpc, "starknet_chainId", []), 16) != int(CHAIN_ID, 16):
        raise RuntimeError("REFUSING: RPC is not Starknet Mainnet")
    manifests = sorted(args.chunks.glob("blocks_*/manifest.json"))
    if not manifests:
        raise RuntimeError("No chunk manifests found")
    args.out.mkdir(parents=True, exist_ok=True)
    total = 0
    for manifest_path in manifests:
        m = json.loads(manifest_path.read_text())
        if m.get("chain_id") != CHAIN_ID:
            raise RuntimeError("Wrong chain in " + str(manifest_path))
        archive = manifest_path.parent / m["archive"]["filename"]
        if sha256(archive) != m["archive"]["sha256"]:
            raise RuntimeError("Archive checksum mismatch: " + str(archive))
        block_hashes = {}
        events = 0
        for e in raw_events(archive, m["archive"]["compression"]):
            n = int(e["block_number"])
            if not int(m["from_block"]) <= n <= int(m["to_block"]):
                raise RuntimeError("Event outside chunk range")
            h = e["block_hash"]
            if n in block_hashes and int(block_hashes[n], 16) != int(h, 16):
                raise RuntimeError(f"Conflicting block hashes for block {n}")
            block_hashes[n] = h
            events += 1
        if events != m["event_count"]:
            raise RuntimeError("Event count mismatch")
        blocks = []
        for n, archived_hash in sorted(block_hashes.items()):
            b = rpc(args.rpc, "starknet_getBlockWithTxHashes", [{"block_number": n}])
            if int(b["block_hash"], 16) != int(archived_hash, 16):
                raise RuntimeError(f"Block hash mismatch at {n}")
            timestamp = b.get("accepted_time") or b.get("timestamp")
            status = b.get("status") or b.get("finality_status")
            if timestamp is None or status is None:
                raise RuntimeError(f"Block {n} missing timestamp/status")
            blocks.append({"block_number": n, "block_hash": b["block_hash"],
                           "timestamp": int(timestamp), "status": status})
        output = args.out / f"blocks_{m['from_block']}_{m['to_block']}.metadata.json"
        if output.exists():
            raise RuntimeError("Refusing overwrite: " + str(output))
        document = {"schema": "influence-block-metadata-v1", "chain_id": CHAIN_ID,
                    "from_block": m["from_block"], "to_block": m["to_block"],
                    "archive_sha256": m["archive"]["sha256"], "blocks": blocks}
        temporary = output.with_suffix(".tmp")
        try:
            with temporary.open("x") as f:
                json.dump(document, f, indent=2, sort_keys=True)
                f.write("\n")
            temporary.rename(output)
        finally:
            if temporary.exists():
                temporary.unlink()
        total += len(blocks)
        print(f"{output.name}: {len(blocks)} event-bearing blocks, {events} events", flush=True)
    print(f"Completed: {total} event-bearing blocks. Original chunks unchanged.")

if __name__ == "__main__":
    main()
