#!/usr/bin/env python3
"""First Mainnet block smoke test: Juno -> JSONL -> isolated raw MongoDB.

This intentionally does NOT build Influence's derived component/game-state collections.
It never connects to the existing 'mongodb' container or 'pre-release' database.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

CONTRACTS = {
    "asteroid": "0x603cf837055c64d026a3c5a9e3a83036cea6c4a3f68a9e19f7a687d726fe817",
    "crew": "0x7280a807c8b79379bec87919433b7b836b93a92e6d71b24ee99f4ffe33dd337",
    "crewmate": "0x241b9c4ce12c06f49fee2ec7c16337386fa5185168f538a7631aacecdf3df74",
    "dispatcher": "0x422d33a3638dcc4c62e72e1d6942cd31eb643ef596ccac2351e0e21f6cd4bf4",
    "ship": "0x04369e47e647ab5fc4d36cee26590276b99a89a83fc3306462d21d366611fde3",
    "sway": "0x004878d1148318a31829523ee9c6a5ee563af6cd87f90a30809e5b0d27db8a9b",
    "escrow": "0x0487a1c4d61c05d79e22ed90d4811b32277961707338c78d563a245749f57618",
}
ORIGIN = 218425
CONTAINER = "influence-mainnet-lab-mongo"
VOLUME = "influence-mainnet-lab-mongo-data"
DATABASE = "influence_mainnet_lab"
COLLECTION = "raw_starknet_events"

def rpc(url, method, params):
    payload = json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=payload,
                                         headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=90) as reply:
                response = json.load(reply)
            if "error" in response:
                raise RuntimeError(str(response["error"]))
            return response["result"]
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)

def docker(*args, input_text=None):
    return subprocess.run(["docker", *args], input=input_text, text=True,
                          check=True, capture_output=True)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rpc", default=os.environ.get("STARKNET_RPC_URL", ""),
                   help="Mainnet Juno JSON-RPC URL, or STARKNET_RPC_URL env")
    p.add_argument("--block", type=int, default=ORIGIN)
    p.add_argument("--output", type=Path, default=Path("mainnet-first-block"))
    p.add_argument("--import-mongo", action="store_true",
                   help="Create isolated lab MongoDB and import these raw events")
    args = p.parse_args()
    if not args.rpc:
        p.error("Provide --rpc or STARKNET_RPC_URL")
    if args.block < ORIGIN:
        p.error(f"Block must be >= Mainnet Influence origin {ORIGIN}")

    chain_id = rpc(args.rpc, "starknet_chainId", [])
    if int(chain_id,16) != int("0x534e5f4d41494e", 16):
        raise SystemExit(f"REFUSING: RPC chain ID {chain_id} is not SN_MAIN")
    head = rpc(args.rpc, "starknet_blockNumber", [])
    if head < args.block:
        raise SystemExit(f"Juno synced to block {head}; requested {args.block}")

    block = rpc(args.rpc, "starknet_getBlockWithTxHashes",
                [{"block_number": args.block}])
    bh = block.get("block_hash")
    if not bh:
        raise SystemExit("Missing finalized block hash. Refusing.")
    print(f"Chain: SN_MAIN | head={head} | block={args.block} | hash={bh}")

    args.output.mkdir(parents=True, exist_ok=True)
    dest = args.output / f"mainnet_{args.block}_raw.jsonl"
    manifest_path = args.output / f"mainnet_{args.block}_manifest.json"
    if dest.exists() or manifest_path.exists():
        raise SystemExit(f"Outputs already exist, refusing to overwrite: {dest}")
    results = []
    for name, address in CONTRACTS.items():
        token = None
        count = 0
        while True:
            filt = {"from_block":{"block_number":args.block},
                    "to_block":{"block_number":args.block},
                    "address":address, "keys":[], "chunk_size":100}
            if token:
                filt["continuation_token"] = token
            page = rpc(args.rpc, "starknet_getEvents", [filt])
            for e in page["events"]:
                if e.get("block_number") != args.block or int(e["from_address"],16) != int(address,16):
                    raise RuntimeError("RPC returned event outside requested block/contract")
                if int(e["block_hash"],16) != int(bh,16):
                    raise RuntimeError("RPC returned inconsistent block hash")
                results.append(dict(e, _contract_name=name, _network="SN_MAIN"))
                count += 1
            new_token = page.get("continuation_token")
            if not new_token:
                break
            if new_token == token:
                raise RuntimeError("Continuation token did not advance")
            token = new_token
        print(f"  {name}: {count} events", flush=True)

    # Deterministic best-effort sort by canonical RPC indices when provided.
    if all(e.get("transaction_index") is not None and e.get("event_index") is not None for e in results):
        results.sort(key=lambda e: (e["transaction_index"], e["event_index"]))
    content = "".join(json.dumps(e, separators=(",",":"))+"\n" for e in results)
    digest = hashlib.sha256(content.encode()).hexdigest()
    manifest = {"network":"SN_MAIN","block":args.block,"block_hash":bh,
                "contracts":CONTRACTS,"event_count":len(results),"sha256":digest}
    dest.write_text(content)
    manifest_path.write_text(json.dumps(manifest,indent=2)+"\n")
    print(f"Archive: {dest} | events={len(results)} | sha256={digest}")

    if not args.import_mongo:
        print("Dry archive completed; MongoDB untouched. Add --import-mongo for an isolated raw-event import.")
        return
    if not results:
        print("No events for these contracts in this block; not creating an empty lab database.")
        return

    # Deliberately refuse if the name exists rather than touch any existing container.
    existing = docker("ps","-a","--format","{{.Names}}").stdout.splitlines()
    if CONTAINER in existing:
        raise SystemExit(f"Refusing to use existing container {CONTAINER}; inspect it manually first.")
    docker("volume","create",VOLUME)
    docker("run","-d","--name",CONTAINER,"--network","none",
           "-v",f"{VOLUME}:/data/db","mongo:7","--bind_ip","127.0.0.1")
    ready = False
    for _ in range(40):
        test = subprocess.run(["docker","exec",CONTAINER,"mongosh","--quiet",
                               "--eval","db.adminCommand({ping:1}).ok"],
                              capture_output=True,text=True)
        if test.returncode == 0 and test.stdout.strip() == "1":
            ready = True
            break
        time.sleep(1)
    if not ready:
        raise SystemExit("Lab MongoDB did not become ready; inspect docker logs. No existing DB touched.")
    # mongoimport reads newline-delimited JSON via stdin; unique container/DB/collection.
    imported = docker("exec","-i",CONTAINER,"mongoimport","--db",DATABASE,
                      "--collection",COLLECTION,"--type","json","--file","/dev/stdin",
                      input_text=content)
    count = docker("exec",CONTAINER,"mongosh","--quiet",DATABASE,
                   "--eval",f"db.getCollection('{COLLECTION}').countDocuments({{}})").stdout.strip()
    print(imported.stderr.strip())
    print(f"Isolated MongoDB: {CONTAINER}/{DATABASE}/{COLLECTION} count={count}")
    if int(count) != len(results):
        raise SystemExit("Import count mismatch!")
    print("PASS: canonical raw-event archive imported into isolated lab MongoDB.")
    print("NOTE: game-state components are NOT constructed by this smoke test.")

if __name__=="__main__":
    main()
