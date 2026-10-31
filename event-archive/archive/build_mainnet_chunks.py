#!/usr/bin/env python3
"""Create immutable, independently verifiable Influence Mainnet event chunks.

Stdlib only to fetch/verify; optional system zstd for compression.
Produces one block-range chunk per run. NOT a game-state MongoDB restore.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from mainnet_first_block import CONTRACTS, ORIGIN, rpc

CHAIN_ID = "0x534e5f4d41494e"
SCHEMA = "influence-starknet-raw-events-v1"

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for b in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()

def block_hash(url, num):
    obj = rpc(url, "starknet_getBlockWithTxHashes", [{"block_number":num}])
    value = obj.get("block_hash")
    if not value: raise RuntimeError(f"No finalized block hash at {num}")
    return value

def gather(url, address, start, end, page_size):
    token, seen = None, set()
    while True:
        q = {"from_block":{"block_number":start}, "to_block":{"block_number":end},
             "address":address, "keys":[], "chunk_size":page_size}
        if token: q["continuation_token"] = token
        response = rpc(url, "starknet_getEvents", [q])
        for e in response["events"]:
            if not start <= e["block_number"] <= end: raise ValueError("Wrong block in response")
            if int(e["from_address"],16) != int(address,16): raise ValueError("Wrong contract in response")
            if e.get("transaction_index") is None or e.get("event_index") is None:
                raise ValueError("Canonical transaction/event indices missing from RPC")
            yield e
        following = response.get("continuation_token")
        if not following: break
        if following == token or following in seen:
            raise ValueError("RPC continuation token did not advance")
        seen.add(following)
        token = following

def verify(folder):
    manifest_path = folder / "manifest.json"
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    if m.get("schema") != SCHEMA or m.get("chain_id") != CHAIN_ID:
        raise ValueError("Wrong manifest version or chain")
    archive = folder / m["archive"]["filename"]
    if not archive.is_file(): raise ValueError("Archive missing")
    if digest(archive) != m["archive"]["sha256"]: raise ValueError("Archive hash mismatch")
    mode = m["archive"]["compression"]
    if mode == "zstd":
        p = subprocess.Popen(["zstd","-dc","--",str(archive)], stdout=subprocess.PIPE)
        source = p.stdout
    elif mode == "none":
        p = None
        source = archive.open("rb")
    else: raise ValueError("Unknown compression")
    count = 0
    previous = None
    try:
        for line in source:
            e = json.loads(line)
            key = (e["block_number"],e["transaction_index"],e["event_index"],
                   int(e["from_address"],16))
            if previous is not None and key < previous:
                raise ValueError("Noncanonical event order")
            if not m["from_block"] <= key[0] <= m["to_block"]:
                raise ValueError("Event outside manifest range")
            if int(e["from_address"],16) not in {int(x,16) for x in m["contracts"].values()}:
                raise ValueError("Event from unknown contract")
            previous = key
            count += 1
    finally:
        source.close()
    if p is not None and p.wait() != 0: raise ValueError("Zstandard decompression failed")
    if count != m["event_count"]: raise ValueError("Event count mismatch")
    print(f"VERIFIED {folder}: {count} events; sha256={m['archive']['sha256']}")
    return m

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc",default=os.environ.get("STARKNET_RPC_URL",""))
    parser.add_argument("--start",type=int)
    parser.add_argument("--end",type=int)
    parser.add_argument("--step",type=int,default=1000,
                        help="Inclusive block chunk width for --start/--end; default 1000")
    parser.add_argument("--page-size",type=int,default=100)
    parser.add_argument("--out",type=Path,default=Path("mainnet-chunks"))
    parser.add_argument("--compress",action="store_true",help="Use system zstd -3")
    parser.add_argument("--verify-only",action="store_true",help="Verify existing chunk folders in --out")
    args=parser.parse_args()
    if args.verify_only:
        paths=sorted(args.out.glob("blocks_*_*/manifest.json"))
        if not paths: parser.error("No chunk manifests found")
        for m in paths: verify(m.parent)
        return
    if not args.rpc or args.start is None or args.end is None:
        parser.error("--rpc, --start and --end required for download")
    if args.start < ORIGIN or args.end < args.start or args.step < 1 or args.page_size < 1:
        parser.error("Invalid block range, step or page-size")
    chain=rpc(args.rpc,"starknet_chainId",[])
    if int(chain,16)!=int(CHAIN_ID,16): raise SystemExit("REFUSING non-Mainnet RPC")
    head=rpc(args.rpc,"starknet_blockNumber",[])
    if head < args.end: raise SystemExit(f"Node head {head} below requested end {args.end}")

    args.out.mkdir(parents=True,exist_ok=True)
    for first in range(args.start,args.end+1,args.step):
        last=min(first+args.step-1,args.end)
        folder=args.out / f"blocks_{first}_{last}"
        if folder.exists():
            if (folder/"manifest.json").exists():
                m=verify(folder)
                if m["from_block"] != first or m["to_block"] != last or m["contracts"] != CONTRACTS:
                    raise SystemExit(f"Existing chunk has different identity: {folder}")
                print("Skipping existing verified chunk")
                continue
            raise SystemExit(f"Incomplete folder {folder}; inspect manually, refusing overwrite")
        # Build fully in staging; never expose incomplete chunk as finished.
        with tempfile.TemporaryDirectory(prefix="influence-chunk-",dir=args.out) as td:
            staging=Path(td)
            print(f"Fetching Mainnet blocks {first}..{last}",flush=True)
            first_hash=block_hash(args.rpc,first)
            last_hash=block_hash(args.rpc,last)
            events=[]
            for name,address in CONTRACTS.items():
                found=list(gather(args.rpc,address,first,last,args.page_size))
                events.extend(found)
                print(f"  {name}: {len(found)}",flush=True)
            events.sort(key=lambda e:(e["block_number"],e["transaction_index"],
                                      e["event_index"],int(e["from_address"],16)))
            # Detect conflicting duplicated canonical event positions (same address).
            keys=[(e["block_number"],e["transaction_hash"].lower(),
                   e["event_index"],e["from_address"].lower()) for e in events]
            if len(keys)!=len(set(keys)):
                raise RuntimeError("Duplicate canonical event identity; investigate provider pagination")
            raw=staging/"events.jsonl"
            with raw.open("w",encoding="utf-8") as f:
                for e in events: f.write(json.dumps(e,sort_keys=True,separators=(",",":"))+"\n")
            archive=raw
            compression="none"
            if args.compress:
                subprocess.run(["zstd","-3","-q","-f",str(raw),"-o",str(staging/"events.jsonl.zst")],check=True)
                raw.unlink()
                archive=staging/"events.jsonl.zst"
                compression="zstd"
            # Check endpoints did not change while downloading (minimal reorg protection).
            if block_hash(args.rpc,first)!=first_hash or block_hash(args.rpc,last)!=last_hash:
                raise RuntimeError("Boundary block hash changed during download")
            m={"schema":SCHEMA,"network":"mainnet","chain_id":CHAIN_ID,
               "from_block":first,"to_block":last,
               "boundary_block_hashes":{"first":first_hash,"last":last_hash},
               "contracts":CONTRACTS,"event_count":len(events),
               "archive":{"filename":archive.name,"compression":compression,
                          "bytes":archive.stat().st_size,"sha256":digest(archive)},
               "created_utc":datetime.now(timezone.utc).isoformat(),
               "source":"local Starknet JSON-RPC; independently verify against own node"}
            (staging/"manifest.json").write_text(json.dumps(m,indent=2,sort_keys=True)+"\n")
            verify(staging)
            staging.rename(folder)
            print(f"COMPLETED {folder} ({len(events)} events)",flush=True)
    print("Done. Chunks are ready for content-addressed publication; not yet uploaded to IPFS.")

if __name__=="__main__": main()
