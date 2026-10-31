#!/usr/bin/env python3
"""Emit verified Mainnet event JSONL enriched with timestamp/status. Read-only."""
import argparse, hashlib, json, subprocess, sys
from pathlib import Path

def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
    return h.hexdigest()

def lines(path,kind):
    if kind=="zstd":
        p=subprocess.Popen(["zstd","-dc",str(path)],stdout=subprocess.PIPE)
        for line in p.stdout:yield line
        p.stdout.close()
        if p.wait()!=0:raise RuntimeError("zstd failed: "+str(path))
    elif kind=="none":
        with path.open("rb") as f:yield from f
    else:raise RuntimeError("Unknown compression "+str(kind))

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--chunks",type=Path,required=True)
    a.add_argument("--metadata",type=Path,required=True)
    args=a.parse_args()
    total=0
    manifests=sorted(args.chunks.glob("blocks_*/manifest.json"))
    if not manifests:raise RuntimeError("No chunk manifests")
    for mp in manifests:
        m=json.loads(mp.read_text())
        if m["chain_id"]!="0x534e5f4d41494e":raise RuntimeError("Not Mainnet")
        archive=mp.parent/m["archive"]["filename"]
        if digest(archive)!=m["archive"]["sha256"]:raise RuntimeError("Archive SHA mismatch")
        side=args.metadata/f"blocks_{m['from_block']}_{m['to_block']}.metadata.json"
        md=json.loads(side.read_text())
        if md["archive_sha256"]!=m["archive"]["sha256"]:raise RuntimeError("Metadata source SHA mismatch")
        if md["chain_id"]!=m["chain_id"] or md["from_block"]!=m["from_block"] or md["to_block"]!=m["to_block"]:raise RuntimeError("Metadata range/chain mismatch")
        blocks={int(b["block_number"]):b for b in md["blocks"]}
        n=0
        for line in lines(archive,m["archive"]["compression"]):
            if not line.strip():continue
            e=json.loads(line);b=blocks[int(e["block_number"])]
            if int(b["block_hash"],16)!=int(e["block_hash"],16):raise RuntimeError("Event/block hash mismatch")
            e["timestamp"]=int(b["timestamp"]);e["status"]=b["status"]
            sys.stdout.write(json.dumps(e,separators=(",",":"))+"\n")
            n+=1;total+=1
        if n!=m["event_count"]:raise RuntimeError("Wrong event count")
    print(f"Verified/enriched {total} Mainnet events.",file=sys.stderr)

if __name__=="__main__":
    try:main()
    except Exception as e:
        print("ERROR:",e,file=sys.stderr)
        sys.exit(1)
