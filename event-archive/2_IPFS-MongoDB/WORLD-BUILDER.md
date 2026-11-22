# Influence World Builder v0.3

Experimental Linux desktop app. Builds a fresh, isolated MongoDB from the V12 bootstrap and verified deltas, without a Starknet node. Existing databases are never selected or overwritten. Each run creates a new uniquely named Docker container and volume, exposes MongoDB on an automatically assigned loopback port, and retains a compressed database archive, result JSON and application journals. Successful runs stop and remove their temporary container and volumes after the archive has been gzip-checked and hashed. A failed build is not ready to use; its container and volume are retained for inspection. Never start two containers against the same volume.

## Run from source on a desktop

Requirements: Python 3.12+, functional Docker with permission to run containers, disk space for downloads, temporary files and MongoDB. The first run may download mongo:7. The application uses the consumer-local `delta-engine/delta.py` engine from this repository.

```bash
python3 -m venv .venv
.venv/bin/pip install PySide6 pymongo
.venv/bin/python world_builder.py
```

The desktop UI uses a single RUN / PAUSE / RESUME button and the benchmark checkbox. It automatically reads `~/Adalaris/data/manifest.json` (or the configured HTTPS manifest), looks for local archives in `~/Adalaris/data`, and targets the latest checkpoint in that manifest. If an archive is absent locally, the existing HTTPS IPFS gateway is used when the trusted manifest supplies a CID. Outputs and paused builds are stored in `~/Adalaris/worlds`. A copy of the manifest is pinned inside each new build. The dashboard shows current file size, checkpoint progress, manifest document and operation counts, measured fingerprint and write durations, and output size. Missing statistics are shown as unknown. Two horizontal bar charts rank the last completed delta’s top ten collections by final fingerprint documents scanned and actual completed write operations. Zero-operation collections are omitted, and skipped fingerprint scans are explicitly labeled. These are collection counters, not blockchain event types. Files are always checked against their manifest size and SHA256 before use. By default, Adalaris scans the initial source, every second delta's target, and the final target. Between scans it validates expected checkpoint continuity, metadata and operation counts; those intermediate journals explicitly defer verification. After a pause/resume it scans again before continuing (or before export if all deltas are complete). The standalone delta CLI continues to scan both sides by default. For timing experiments only, select **Benchmark only — skip database fingerprint scans**. Package integrity, metadata/index and bulk operation-count checks remain enabled; the resulting archive and journals are explicitly marked UNVERIFIED. Verified output proves equality to the published target fingerprints, not independent blockchain correctness.

## Backgrounds

Place PNG, JPG, JPEG, WebP or BMP images in `~/Adalaris/backgrounds` (automatically created). The first image is shown on startup; the background advances in alphabetical filename order after each completed delta and wraps around. Resumed builds use the completed checkpoint count to restore their position. Invalid images are skipped; an empty folder uses the default gradient.

## Streaming reconstruction (v0.2)

The coordinator starts a background downloader and consumes packages in manifest order. By default one waiting package (including any download in flight) is reserved, plus the package being applied. Each is validated by size and SHA256 before application. Downloaded packages are deleted from the per-build cache after successful checkpoint journaling; original local inputs are never deleted. A safe pause cancels prefetch and discards queued temporary downloads. Network failures before import preserve the last safely completed checkpoint for resume. A failed application is not automatically resumed.

The dashboard displays download progress, speed, buffer occupancy and available disk space alongside import status. This is a package-count bound, not a byte quota: reserve space for MongoDB, up to two packages by default, fingerprint scratch files and the final export.

The default is the trusted manifest on GitHub unless a local manifest exists. To test a trusted regularly updated HTTPS manifest without copying it locally, launch with:

```bash
ADALARIS_MANIFEST_URL=https://your-trusted-manifest-host/manifest.json .venv/bin/python world_builder.py
```

A fresh build pins the fetched manifest; resume uses that pinned version. The publisher must provide reliable CIDs and hashes. The configured Filebase IPFS gateway supplies the data. Test the bounded downloader independently with `python -m unittest -v test_pipeline`.

## Recovery archives (v0.3, step 1)

See [SETTINGS.md](SETTINGS.md) for the editable `~/Adalaris/settings.json`. By default, blocks 199999 and 399999 receive verified intermediate recovery dumps when rebuilding through 599999. The final verified dump replaces all intermediate dumps. The newest intermediate archive is retained until its replacement is safely recorded. If disk space is insufficient, the intermediate dump is skipped; the previous recovery point is retained. The dashboard distinguishes applied, verified and saved blocks. Automatic restoration/retry is planned for step 2. Run `python -m unittest -v` for settings and mocked pipeline tests.

## Pause and resume

Click **PAUSE** and wait for PAUSED confirmation. An active delta is completed before MongoDB is cleanly stopped; a request during final export may instead finish the build. You can close the app or shut down the computer after confirmation. Keep the stopped container, its volumes, the build directory, input manifest and delta files. Do not manually start or modify the paused database.

On another day open Adalaris: the oldest safely paused build in `~/Adalaris/worlds` is discovered automatically. Click **RESUME**; no file selection is required. The unchanged manifest, container identity and completed journals are checked; the original verification mode is retained. Completed checkpoints are skipped. Unexpected termination or an incomplete journal is not treated as a safe pause.

## Output and timing

Each successful build writes `adalaris-<end>.archive.gz` (or `adalaris-<end>-UNVERIFIED.archive.gz`) inside its unique `build-...` folder in Build storage. This is a native compressed `mongodump --archive` backup, not a continuously running database. The build record includes SHA256, byte size, applied/verified checkpoint, cleanup status and aggregate fingerprint, bulk-write, application, export and total durations. The archive is checked for gzip integrity before cleanup; this is not a restore test. Keep the archive and journals for later restoration. MongoDB images are cached for subsequent builds. If export fails, the temporary database is retained for recovery.

## Make a manifest on the producer (read-only)

Run from this directory on Bahn. No GUI or extra Python packages are needed for this step.

```bash
python3 world_builder.py manifest --data ../v12/v12_data --end 599999 --output manifest.json
```

This validates bootstrap SHA256, every package SHA256 and the matching successful application journal. Copy the manifest and matching bootstrap/delta files to King (or supply IPFS CIDs for remote downloads). `ipfs-catalog.json` records the six manually supplied CIDs and sizes, not verified content hashes. They may come from a different replay, so do not silently combine these CIDs with another set of deltas. Add the matching bootstrap CID and size, then use `--cids ipfs-catalog.json` when generating the manifest; mismatched sizes are rejected. The application still validates the downloaded SHA256 against the trusted manifest. Never publish a manifest before verifying that every referenced file is retrievable and matches its hash.

## Scope

Version 0.3 builds a new world through the latest published checkpoint, shows progress and automatically resumes paused work. Incremental updates, signed remote manifest distribution, automatic IPFS publication and AppImage packaging are future work. Do not expose the unauthenticated local MongoDB port to the LAN or internet. For older builds or failures, stop an idle builder container with `docker stop <container name from result.json>`; it can be restarted with `docker start <name>`. This does not remove the world's persistent volume.
