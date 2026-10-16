#!/usr/bin/env python3
"""Read-only, bounded-memory comparison of archived dispatcher events with MongoDB."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys

def hx(s):
    return format(int(s, 16), "x")

def signature(e, mongo=False):
    return (hx(e["address"] if mongo else e["from_address"]),
            hx(e["transactionHash"] if mongo else e["transaction_hash"]),
            tuple(hx(v) for v in e["keys"]),
            tuple(hx(v) for v in e["data"]))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dir", type=Path, default=Path("events-testnet"))
    p.add_argument("--start", type=int, default=60000)
    p.add_argument("--end", type=int, default=70000)
    p.add_argument("--mongo-container", default="mongodb")
    p.add_argument("--database", default="pre-release")
    p.add_argument("--contract", default="0x517567ac7026ce129c950e6e113e437aa3c83716cd61481c6bb8c5057e6923e",
                   help="Contract to verify; defaults to Sepolia dispatcher")
    p.add_argument("--prefix", default="asteroid_", help="Existing archive filename prefix")
    args = p.parse_args()
    if args.end < args.start or args.start < 0:
        p.error("Invalid block range")
    address = hx(args.contract)
    pattern = re.compile(r"^" + re.escape(args.prefix) + r"(\\d+)_(\\d+)\\.jsonl$")
    selected = []
    for path in args.dir.glob("*.jsonl"):
        m = pattern.fullmatch(path.name)
        if m:
            first, last = map(int, m.groups())
            if first <= args.end and last >= args.start:
                selected.append((first, last, path))
    selected.sort()
    current = args.start
    for first, last, path in selected:
        if first != current:
            p.error(f"Archive coverage gap/overlap: expected block {current}, got {first} ({path})")
        if last > args.end:
            p.error(f"Archive file extends past --end: {path}")
        current = last + 1
    if current != args.end + 1:
        p.error(f"Missing archive coverage starting at block {current}")
    if not selected:
        p.error("No archive files found")

    # The Mongo shell only reads db.events. It prints one JSON record per line and
    # streams its cursor; Python compares at most one archive file at a time.
    shell = (
        'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" '
        '-p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin '
        '"$1" --eval "$2"'
    )
    total_raw = total_mongo = total_missing = total_extra = 0
    for first, last, path in selected:
        raw = Counter()
        read = 0
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                e = json.loads(line)
                n = e["block_number"]
                if not first <= n <= last:
                    raise ValueError(f"Unexpected block {n} in {path}")
                if hx(e["from_address"]) != address:
                    raise ValueError(f"Unexpected contract in {path}: {e['from_address']}")
                raw[signature(e)] += 1
                read += 1
        js = (
            'db.events.find({__t:"Starknet", blockNumber:{$gte:' + str(first) +
            ',$lte:' + str(last) +
            '}},{_id:0,address:1,transactionHash:1,keys:1,data:1})'
            '.batchSize(1000).forEach(e=>print(JSON.stringify(e)))'
        )
        mongo = Counter()
        count_mongo = 0
        cmd = ["docker", "exec", args.mongo_container, "sh", "-c", shell,
               "sh", args.database, js]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, bufsize=1) as process:
            for line in process.stdout:
                if not line.strip():
                    continue
                e = json.loads(line)
                if hx(e["address"]) == address:
                    mongo[signature(e, mongo=True)] += 1
                    count_mongo += 1
            stderr = process.stderr.read()
            status = process.wait()
            if status:
                raise RuntimeError(f"MongoDB query failed ({status}): {stderr.strip()}")
        missing = raw - mongo
        extra = mongo - raw
        a = sum(missing.values())
        b = sum(extra.values())
        total_raw += read
        total_mongo += count_mongo
        total_missing += a
        total_extra += b
        verdict = "MATCH" if not a and not b else "DIFFERENCE"
        print(f"{first}-{last}: archive={read:,} mongo={count_mongo:,} "
              f"missing={a:,} extra={b:,} {verdict}", flush=True)
        if a:
            print(f"  Example absent from MongoDB (contract, tx): {next(iter(missing))[:2]}")
        if b:
            print(f"  Example extra in MongoDB (contract, tx): {next(iter(extra))[:2]}")
    print(f"TOTAL: archive={total_raw:,} mongo={total_mongo:,} "
          f"missing={total_missing:,} extra={total_extra:,}")
    if total_missing or total_extra:
        print("DIFFERENCES FOUND; investigate before claiming complete verification")
        sys.exit(1)
    print("ALL MATCH — archive and MongoDB have identical event multisets in this range")

if __name__ == "__main__":
    main()
