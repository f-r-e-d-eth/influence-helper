#!/usr/bin/env python3
"""Build and verify IPFS-ready directories of consecutive 1000-block Influence chunks.

A package is a UnixFS-ready folder, not a single IPFS raw block. Does not upload.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCHEMA="influence-event-package-v1"
CATALOG="influence-event-catalog-v1"
CHUNK_SCHEMA="influence-starknet-raw-events-v1"
WIDTH=1000

def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()

def write_json(path,obj):
    path.write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n",encoding="utf-8")

def load_chunks(src, origin):
    found=[]
    for path in sorted(src.glob("blocks_*_*/manifest.json")):
        m=json.loads(path.read_text())
        lo,hi=m["from_block"],m["to_block"]
        if m.get("schema")!=CHUNK_SCHEMA or m.get("network")!="mainnet":
            raise ValueError(f"Unsupported chunk: {path}")
        # First chunk may begin at chain's origin, but must close on 999.
        if hi != (lo//WIDTH+1)*WIDTH-1:
            raise ValueError(f"Not 1000-aligned: {path}; rebuild aligned chunks")
        if lo%WIDTH and lo!=origin:
            raise ValueError(f"Unexpected partial first chunk: {path}")
        if not (lo==origin or lo%WIDTH==0):
            raise ValueError(f"Invalid start boundary: {path}")
        filename=m["archive"]["filename"]
        if Path(filename).name!=filename: raise ValueError("Unsafe archive filename")
        data=path.parent/filename
        if not data.is_file() or sha(data)!=m["archive"]["sha256"]:
            raise ValueError(f"Missing/corrupted archive: {path}")
        found.append((lo,hi,m,path.parent,data))
    found.sort(key=lambda x:x[0])
    if not found: raise ValueError("No aligned chunks. First create ranges ending in ...999")
    if found[0][0]!=origin: raise ValueError(f"Expected first chunk at origin {origin}")
    for a,b in zip(found,found[1:]):
        if b[0]!=a[1]+1: raise ValueError(f"Gap or overlap: {a[:2]} -> {b[:2]}")
    return found

def verify_package(folder):
    m=json.loads((folder/"manifest.json").read_text())
    if m.get("schema")!=SCHEMA: raise ValueError("Wrong package schema")
    last=None
    for entry in m["files"]:
        name=entry["name"]
        if Path(name).name!=name: raise ValueError("Unsafe name")
        file=folder/name
        if not file.exists() or file.stat().st_size!=entry["bytes"] or sha(file)!=entry["sha256"]:
            raise ValueError(f"Corrupted {file}")
        if last is not None and entry["from_block"]!=last+1:
            raise ValueError("Noncontiguous package files")
        last=entry["to_block"]
    if m["files"][0]["from_block"]!=m["from_block"] or last!=m["to_block"]:
        raise ValueError("Package coverage mismatch")
    if sum(e["bytes"] for e in m["files"])!=m["payload_bytes"]:
        raise ValueError("Package byte count mismatch")
    if sum(e["events"] for e in m["files"])!=m["event_count"]:
        raise ValueError("Package event count mismatch")
    return m

def pack(args):
    chunks=load_chunks(args.chunks,args.origin)
    args.out.mkdir(parents=True,exist_ok=True)
    if list(args.out.iterdir()):
        raise SystemExit("Output must be an EMPTY directory; refusing to overwrite a published package")
    grouping=[]; current=[]; used=0
    for item in chunks:
        size=item[4].stat().st_size
        if current and used+size>args.target_bytes:
            grouping.append(current);current=[];used=0
        current.append(item);used+=size
    if current:grouping.append(current)
    packages=[]
    for group in grouping:
        start,end=group[0][0],group[-1][1]
        name=f"package_{start}_{end}"
        with tempfile.TemporaryDirectory(prefix=".building-",dir=args.out) as tmp:
            tmp=Path(tmp)
            entries=[]
            for lo,hi,m,folder,source in group:
                dest_name=f"{lo}_{hi}{''.join(source.suffixes)}"
                dest=tmp/dest_name
                shutil.copyfile(source,dest)
                entries.append({"name":dest_name,"from_block":lo,"to_block":hi,
                                "bytes":dest.stat().st_size,"sha256":sha(dest),
                                "events":m["event_count"],"boundary_block_hashes":m["boundary_block_hashes"]})
            manifest={"schema":SCHEMA,"network":"mainnet","chain_id":group[0][2]["chain_id"],
                      "from_block":start,"to_block":end,"target_payload_bytes":args.target_bytes,
                      "payload_bytes":sum(e["bytes"] for e in entries),
                      "event_count":sum(e["events"] for e in entries),"files":entries}
            write_json(tmp/"manifest.json",manifest)
            verify_package(tmp)
            target=args.out/name
            tmp.rename(target)
            packages.append({"directory":name,"from_block":start,"to_block":end,
                             "manifest_sha256":sha(target/"manifest.json"),
                             "payload_bytes":manifest["payload_bytes"],"event_count":manifest["event_count"],
                             "cid":None})
            print(f"{name}: {len(entries)} chunks, {manifest['event_count']} events, {manifest['payload_bytes']} bytes")
    catalog={"schema":CATALOG,"network":"mainnet","chain_id":chunks[0][2]["chain_id"],
             "origin_block":args.origin,"through_block":chunks[-1][1],
             "package_target_bytes":args.target_bytes,"packages":packages}
    write_json(args.out/"catalog.json",catalog)
    print(f"Created {len(packages)} IPFS-ready packages and catalog; CID fields await publication.")

def verify(args):
    catalog=json.loads((args.out/"catalog.json").read_text())
    if catalog["schema"]!=CATALOG:raise ValueError("Unexpected catalog")
    last=catalog["origin_block"]-1
    for e in catalog["packages"]:
        if Path(e["directory"]).name!=e["directory"]:raise ValueError("Unsafe path")
        folder=args.out/e["directory"]
        if sha(folder/"manifest.json")!=e["manifest_sha256"]:
            raise ValueError("Manifest digest mismatch")
        m=verify_package(folder)
        if m["from_block"]!=last+1 or m["from_block"]!=e["from_block"] or m["to_block"]!=e["to_block"]:
            raise ValueError("Catalog gap/overlap")
        if m["event_count"]!=e["event_count"] or m["payload_bytes"]!=e["payload_bytes"]:
            raise ValueError("Catalog totals mismatch")
        last=m["to_block"]
    if last!=catalog["through_block"]:raise ValueError("Catalog coverage mismatch")
    print(f"VERIFIED {len(catalog['packages'])} packages, blocks {catalog['origin_block']}..{last}")

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--chunks",type=Path,default=Path("mainnet-chunks"))
    p.add_argument("--out",type=Path,default=Path("mainnet-packages"))
    p.add_argument("--origin",type=int,default=218425)
    p.add_argument("--target-bytes",type=int,default=10_000_000)
    p.add_argument("--verify-only",action="store_true")
    a=p.parse_args()
    if a.verify_only:verify(a)
    else:
        if a.target_bytes<1:p.error("target bytes must be positive")
        pack(a);verify(a)

if __name__=="__main__":main()
