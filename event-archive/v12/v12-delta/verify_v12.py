import json
import tempfile
from pathlib import Path

from delta import verify, sha_file
from archive_input import extract_archive
from generate_v12_delta import load_checkpoint, SCRATCH, DECODER

archive = Path("artifacts/v12-reconstructed-599999.archive.gz")

with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
    work = Path(tmp)

    original, record = load_checkpoint(
        599999, work / "original"
    )

    directory = work / "reconstructed"
    directory.mkdir()

    reconstructed = extract_archive(
        archive,
        directory,
        "v12_disposable_reconstruction",
        63,
        DECODER,
        {"archive_sha256": sha_file(archive)}
    )

    report = verify(reconstructed, original, work)

    report["excluded_collections"] = ["keyv"]

    with open("artifacts/v12-final-verification.json", "x") as f:
        json.dump(report, f, indent=2)

    print("Collections:", len(report["collections"]))
    print("Metadata equal:", report["metadata_equal"])
    print("Database equal:", report["equal"])
    print("Verification seconds:", report["verification_seconds"])

    raise SystemExit(0 if report["equal"] else 1)

