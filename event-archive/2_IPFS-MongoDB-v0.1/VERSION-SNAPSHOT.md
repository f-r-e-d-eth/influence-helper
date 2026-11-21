# Adalaris v0.1 — archived release

Frozen on 2026-11-21 from commit `cb91f3cebc6d4a40253b500260309432607ef0b1`.

The working folder remains `event-archive/2_IPFS-MongoDB`. This archive includes the original documentation, catalog and application, plus its frozen delta application engine. The sole application change is the engine import path, so future changes to the shared V12 engine cannot affect this snapshot.

To run from this directory:

```bash
python3 -m venv .venv
.venv/bin/pip install PySide6 pymongo
.venv/bin/python world_builder.py
```

Uses the same external data, backgrounds and worlds directories as v0.1. No generated datasets or user databases are included. Do not run two builds against the same paused database.
