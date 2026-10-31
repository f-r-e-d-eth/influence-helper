#!/usr/bin/env python3
"""Download Starknet contract events into resumable per-range JSONL archives."""
import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

CONTRACTS = {
    "sepolia": {
        "dispatcher": "0x517567ac7026ce129c950e6e113e437aa3c83716cd61481c6bb8c5057e6923e",
        "asteroid": "0x680710b95255a852ed9ead04d4c1ffcf4f0695e29fb5c327abe2b8cb305ba25",
    },
    "mainnet": {
        "dispatcher": "0x422d33a3638dcc4c62e72e1d6942cd31eb643ef596ccac2351e0e21f6cd4bf4",
        "asteroid": "0x603cf837055c64d026a3c5a9e3a83036cea6c4a3f68a9e19f7a687d726fe817",
    },
}

def rpc_call(url, method, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for attempt in range(7):
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                obj = json.load(response)
            if "error" in obj:
                raise RuntimeError(str(obj["error"]))
            return obj["result"]
        except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as exc:
            if attempt == 6:
                raise
            wait = min(2 ** attempt, 30)
            print(f"RPC attempt {attempt + 1} failed: {exc}; retry in {wait}s", flush=True)
            time.sleep(wait)

def download(url, address, first, last, page_size):
    events, token, pages = [], None, 0
    while True:
        query = {"from_block": {"block_number": first},
                 "to_block": {"block_number": last},
                 "address": address, "keys": [], "chunk_size": page_size}
        if token is not None:
            query["continuation_token"] = token
        result = rpc_call(url, "starknet_getEvents", [query])
        events.extend(result["events"])
        pages += 1
        next_token = result.get("continuation_token")
        if not next_token:
            break
        if next_token == token:
            raise RuntimeError("RPC returned unchanged continuation token")
        token = next_token
    return events, pages

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", choices=CONTRACTS, required=True)
    parser.add_argument("--contract", choices=("asteroid", "dispatcher"), required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--step", type=int, default=1000)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--out", type=Path, default=Path("events"))
    args = parser.parse_args()
    if args.start < 0 or args.end < args.start or args.step < 1 or args.page_size < 1:
        parser.error("Invalid range, step, or page size")
    url = os.environ.get("STARKNET_RPC_URL")
    if not url:
        parser.error("STARKNET_RPC_URL environment variable is required")
    address = CONTRACTS[args.network][args.contract]
    args.out.mkdir(parents=True, exist_ok=True)
    new_count = 0
    for first in range(args.start, args.end + 1, args.step):
        last = min(first + args.step - 1, args.end)
        base = args.out / f"{args.network}_{args.contract}_{first}_{last}"
        target = base.with_suffix(".jsonl")
        meta = base.with_suffix(".meta.json")
        identity = {"network": args.network, "contract": address, "from_block": first, "to_block": last}
        if target.exists() or meta.exists():
            if not target.exists() or not meta.exists():
                raise SystemExit(f"Incomplete archive or metadata: {base}. Inspect before retrying.")
            saved = json.loads(meta.read_text())
            if any(saved.get(k) != v for k, v in identity.items()):
                raise SystemExit(f"Existing archive identity mismatch: {base}")
            print(f"Skipping verified metadata range {first}-{last}; see README for stronger validation")
            continue
        print(f"Downloading {first}-{last} ...", flush=True)
        events, pages = download(url, address, first, last, args.page_size)
        tmp = target.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as stream:
            for event in events:
                stream.write(json.dumps(event, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(target)
        import hashlib
        sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
        metadata = dict(identity, event_count=len(events), pages=pages, sha256=sha256)
        tmpmeta = meta.with_suffix(".json.tmp")
        tmpmeta.write_text(json.dumps(metadata, indent=2) + "\n")
        tmpmeta.replace(meta)
        new_count += len(events)
        print(f"  {len(events)} events; {pages} page(s)")
    print(f"Complete; {new_count} newly downloaded events")

if __name__ == "__main__":
    main()
