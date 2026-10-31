#!/usr/bin/env python3
"""Diagnose an Influence transaction's canonical event multiplicity versus MongoDB (read-only)."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import subprocess
import sys

def normalize(value):
    return format(int(value, 16), "x")

def signature(event, mongo=False):
    return (
        normalize(event["address"] if mongo else event["from_address"]),
        normalize(event["transactionHash"] if mongo else event["transaction_hash"]),
        tuple(map(normalize, event["keys"])),
        tuple(map(normalize, event["data"])),
    )

def describe(sig):
    return f"tx=0x{sig[1]} keys={len(sig[2])} data={len(sig[3])} selector=0x{sig[2][0] if sig[2] else '?'}"

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", type=Path, required=True, help="JSONL file covering the block")
    ap.add_argument("--block", type=int, required=True)
    ap.add_argument("--tx", required=True, help="Transaction hash (any leading-zero representation)")
    ap.add_argument("--contract", default="0x517567ac7026ce129c950e6e113e437aa3c83716cd61481c6bb8c5057e6923e")
    ap.add_argument("--mongo-container", default="mongodb")
    ap.add_argument("--database", default="pre-release")
    args = ap.parse_args()
    tx, contract = normalize(args.tx), normalize(args.contract)

    canonical = Counter()
    with args.file.open(encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            if (e["block_number"] == args.block and
                normalize(e["transaction_hash"]) == tx and
                normalize(e["from_address"]) == contract):
                canonical[signature(e)] += 1
    if not canonical:
        ap.error("No matching archive events. Check the block, transaction and contract.")

    # Read-only query; filter by normalized transaction hash inside mongosh to
    # avoid MongoDB's variable-length leading-zero hex strings.
    js = """
const target = BigInt(TX_JSON);
const address = BigInt(CONTRACT_JSON);
db.events.find({__t:"Starknet", blockNumber: BLOCK_NUMBER},
  {_id:0, address:1, transactionHash:1, keys:1, data:1,
   event:1, logIndex:1, createdAt:1, blockHash:1,
   removed:1, ignore:1, status:1}).forEach(e => {
  if (BigInt(e.transactionHash) === target && BigInt(e.address) === address) {
    print(JSON.stringify(e));
  }
});
"""
    js = (js.replace("TX_JSON", json.dumps(args.tx))
            .replace("CONTRACT_JSON", json.dumps(args.contract))
            .replace("BLOCK_NUMBER", str(args.block)))
    shell = ('mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" '
             '-p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin "$1" --eval "$2"')
    result = subprocess.run(
        ["docker", "exec", args.mongo_container, "sh", "-c", shell,
         "sh", args.database, js],
        capture_output=True, text=True, check=True)
    documents = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    mongo = Counter(signature(e, mongo=True) for e in documents)

    print(f"Block {args.block} | transaction 0x{tx} | contract 0x{contract}")
    print(f"Archive occurrences: {sum(canonical.values())}; unique contents: {len(canonical)}")
    print(f"MongoDB occurrences: {sum(mongo.values())}; unique contents: {len(mongo)}")
    print(f"Archive absent from DB: {sum((canonical - mongo).values())}")
    print(f"DB in excess of archive: {sum((mongo - canonical).values())}")
    print()

    batches = defaultdict(list)
    for doc in documents:
        created = str(doc.get("createdAt", "unknown"))
        batches[created].append(doc)
    print("MongoDB insertion batches (createdAt):")
    for created, docs in sorted(batches.items()):
        group = Counter(signature(e, mongo=True) for e in docs)
        excess = sum((group - canonical).values())
        absent = sum((canonical - group).values())
        print(f"  {created}: {len(docs)} events, "
              f"missing vs archive={absent}, extra vs archive={excess}, "
              f"exact_multiset_match={group == canonical}")
        print(f"    logIndex: {sorted(e.get('logIndex') for e in docs)}")

    print()
    print("Canonical event multiplicities vs MongoDB:")
    for sig in sorted(set(canonical) | set(mongo)):
        a, b = canonical[sig], mongo[sig]
        if a != b:
            print(f"  archive={a} mongo={b} delta={b-a:+d} {describe(sig)}")
    if canonical == mongo:
        print("  No differences.")
    else:
        print("  Caution: matching event content alone does not establish canonical ordering.")
    print()
    block_hashes = sorted({str(e.get("blockHash")) for e in documents})
    statuses = Counter((str(e.get("status")), str(e.get("removed")), str(e.get("ignore")))
                       for e in documents)
    print("MongoDB block hashes:", ", ".join(block_hashes) if block_hashes else "(none)")
    print("MongoDB status/removed/ignore:")
    for fields, count in sorted(statuses.items()):
        print(f"  {fields}: {count}")
    if canonical == mongo:
        print("RESULT: exact multiset match for this transaction")
    else:
        print("RESULT: discrepancy confirmed; do not use the MongoDB events as an exact canonical archive")
        sys.exit(1)

if __name__ == "__main__":
    main()
