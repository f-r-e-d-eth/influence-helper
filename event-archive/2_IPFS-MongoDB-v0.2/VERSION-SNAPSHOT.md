# Adalaris v0.2 — archived release

Frozen on 2026-11-22 from commit `9c65bc8403470fb5baf0ffd742b985a85c00183e`.

The active development folder remains `event-archive/2_IPFS-MongoDB`. This snapshot includes all v0.2 application modules, pipeline tests, documentation, manifest, and frozen delta engine. Only the coordinator's engine path and default manifest URL are changed to isolate this release from future development; local manifests and explicit URL overrides still work.

Verified on King: IPFS-only reconstruction through block 599,999, final archive gzip check and temporary MongoDB cleanup. This is a record of the earlier v0.2 test, not a new integration test of the archive.

To run from this directory:

```bash
python3 -m venv .venv
.venv/bin/pip install PySide6 pymongo
.venv/bin/python world_builder.py
```

Uses the existing external data, backgrounds and worlds directories under ~/Adalaris. No generated databases, datasets or credentials are included. Do not run two builds against the same paused database.
