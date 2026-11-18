#!/usr/bin/env python3

import argparse
import json
import tempfile
from pathlib import Path

from archive_input import extract_archive
from delta import generate, sha_file, unpack

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / "v12-prototype-v0.4" / "artifacts"
OUTPUT = ROOT / "artifacts"
SCRATCH = ROOT / "scratch"
DECODER = ROOT / "archive-tools" / "archive-extract"

DATABASE = "v12_reconstruction"
EXCLUDED = {"keyv"}
INTERVAL = 100000


def load_checkpoint(block, directory):
    name = "v12-bootstrap" if block == -1 else f"v12-block-{block}"

    record = json.loads(
        (SOURCE / f"{name}.json").read_text()
    )

    archive = SOURCE / f"{name}.archive.gz"

    if (
        record["database"] != DATABASE
        or record["completed_through_block"] != block
        or record["archive"] != archive.name
        or record["status"] != "unverified_candidate"
        or archive.stat().st_size != record["archive_bytes"]
        or sha_file(archive) != record["archive_sha256"]
        or (block >= 0 and record["from_block"] != block - INTERVAL + 1)
    ):
        raise RuntimeError(f"Invalid checkpoint: {name}")

    expected = 63 if block < 299999 else 64

    directory.mkdir()

    view = extract_archive(
        archive, directory, DATABASE,
        expected, DECODER, record
    )

    for collection in EXCLUDED:
        view.collections.pop(collection, None)

    if len(view.collections) != 63:
        raise RuntimeError("Unexpected persistent collection count")

    return view, record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("end_block", type=int)
    args = parser.parse_args()

    end = args.end_block
    start = end - INTERVAL

    if end < 99999 or end > 599999 or (end + 1) % INTERVAL:
        raise ValueError("Invalid checkpoint boundary")

    OUTPUT.mkdir(exist_ok=True)
    SCRATCH.mkdir(exist_ok=True)

    output = OUTPUT / f"v12-delta-{start + 1}-{end}.tar.gz"

    if output.exists() or Path(str(output) + ".report.json").exists():
        raise FileExistsError(f"Output already exists: {output}")

    print(f"Generating delta: {start + 1}–{end}", flush=True)

    with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
        work = Path(tmp)

        left, source_record = load_checkpoint(start, work / "source")
        right, target_record = load_checkpoint(end, work / "target")

        report = generate(
            left,
            right,
            output,
            work,
            expected_collections=63,
            source_block=start,
            target_block=end,
            provenance={
                "source": source_record,
                "target": target_record,
                "excluded_collections": sorted(EXCLUDED),
            },
        )

        with unpack(output, work):
            pass

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
