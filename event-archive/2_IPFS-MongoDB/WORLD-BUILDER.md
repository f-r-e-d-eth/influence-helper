# Influence World Builder v0.1

Experimental Linux desktop app. Builds a fresh, isolated MongoDB from the V12 bootstrap and verified deltas, without a Starknet node. Existing databases are never selected or overwritten. Each run creates a new uniquely named Docker container and volume, exposes MongoDB on an automatically assigned loopback port, and retains a result JSON and application journals. A failed build is not ready to use; its container and volume are retained for inspection. Never start two containers against the same volume.

## Run from source on a desktop

Requirements: Python 3.12+, functional Docker with permission to run containers, disk space for downloads, temporary files and MongoDB. The first run may download mongo:7. The application uses the existing sibling `../v12/v12-delta/delta.py` engine from this repository.

```bash
python3 -m venv .venv
.venv/bin/pip install PySide6 pymongo
.venv/bin/python world_builder.py
```

Select a trusted manifest, local data folder (optional if every file has an IPFS CID), build storage folder and target checkpoint. You can choose an image from your computer and adjust panel opacity; preferences persist locally. The IPFS gateway is optional for local archives. Files are always checked against their manifest size and SHA256 before use. The existing delta engine verifies exact starting and ending database fingerprints for every delta. This proves equality to the published checkpoint fingerprints, not independent blockchain correctness.

## Make a manifest on the producer (read-only)

Run from this directory on Bahn. No GUI or extra Python packages are needed for this step.

```bash
python3 world_builder.py manifest --data ../v12/v12_data --end 599999 --output manifest.json
```

This validates bootstrap SHA256, every package SHA256 and the matching successful application journal. Copy the manifest and matching bootstrap/delta files to King (or supply IPFS CIDs for remote downloads). `ipfs-catalog.json` records the six manually supplied CIDs and sizes, not verified content hashes. They may come from a different replay, so do not silently combine these CIDs with another set of deltas. Add the matching bootstrap CID and size, then use `--cids ipfs-catalog.json` when generating the manifest; mismatched sizes are rejected. The application still validates the downloaded SHA256 against the trusted manifest. Never publish a manifest before verifying that every referenced file is retrievable and matches its hash.

## Scope

Version 0.1 builds a new world, selects historical checkpoints, shows progress and saves settings. Incremental update/resume, signed remote manifest distribution, automatic IPFS publication and AppImage packaging are future work. Do not expose the unauthenticated local MongoDB port to the LAN or internet. Stop an idle builder container with `docker stop <container name from result.json>`; it can be restarted with `docker start <name>`. This does not remove the world's persistent volume.
