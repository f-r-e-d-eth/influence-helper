#!/usr/bin/env python3
"""Read-only cross-check of one archive block against a MongoDB event collection."""
import argparse
import collections
import json
import subprocess
from pathlib import Path

def norm_hex(x):
    return format(int(x, 16), "x")

def signature(e, mongo=False):
    return (norm_hex(e["address"] if mongo else e["from_address"]),
            norm_hex(e["transactionHash"] if mongo else e["transaction_hash"]),
            tuple(map(norm_hex, e["keys"])),
            tuple(map(norm_hex, e["data"])))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--block", type=int, required=True)
    parser.add_argument("--mongo-container", default="mongodb")
    parser.add_argument("--database", default="pre-release")
    args = parser.parse_args()
    rpc_events = []
    with args.file.open(encoding="utf-8") as stream:
        for line in stream:
            e = json.loads(line)
            if e["block_number"] == args.block:
                rpc_events.append(e)
    if not rpc_events:
        print("No events in archive for this block; checking MongoDB anyway.")
    addresses = sorted({norm_hex(e["from_address"]) for e in rpc_events})
    # If the archive block is empty, determine the contract using its companion metadata.
    meta_path = args.file.with_suffix(".meta.json")
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        addresses = [norm_hex(meta["contract"])]
        if not (meta["from_block"] <= args.block <= meta["to_block"]):
            parser.error("Requested block not covered by archive metadata")
    elif not addresses:
        parser.error("For an empty block, companion .meta.json is required")
    # Pass only a fixed JS program; numeric block and addresses are JSON-encoded.
    js = """
const block = BLOCK_PLACEHOLDER;
const addresses = ADDRESS_PLACEHOLDER;
const docs = db.events.find({__t: "Starknet", blockNumber: block},
  { _id: 0, address: 1, transactionHash: 1, keys: 1, data: 1 }).toArray();
print(JSON.stringify(docs.filter(e => addresses.includes(BigInt(e.address).toString(16)))));
""".replace("BLOCK_PLACEHOLDER", str(args.block)).replace("ADDRESS_PLACEHOLDER", json.dumps(addresses))
    shell = 'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin "$1" --eval "$2"'
    proc = subprocess.run(["docker", "exec", args.mongo_container, "sh", "-c", shell, "sh", args.database, js],
                          check=True, capture_output=True, text=True)
    mongo_events = json.loads(proc.stdout.strip())
    actual = collections.Counter(signature(e) for e in rpc_events)
    expected = collections.Counter(signature(e, mongo=True) for e in mongo_events)
    missing = actual - expected
    extra = expected - actual
    print(f"Block: {args.block}; Archive: {len(rpc_events)}, MongoDB: {len(mongo_events)}")
    print(f"Missing from MongoDB: {sum(missing.values())}; extra in MongoDB: {sum(extra.values())}")
    print("MATCH" if actual == expected else "DIFFERENCE (investigate record coverage and indexing)")
    if actual != expected:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
