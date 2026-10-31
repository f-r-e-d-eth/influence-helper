#!/usr/bin/env python3
"""Download and verify self-contained Influence Mainnet chunks.

Never modifies MongoDB. Existing chunks are never overwritten.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("/home/iu/Influence-Mainnet")
SCRIPTS = Path(__file__).resolve().parent
DEFAULT_CHUNKS = ROOT / "mainnet-chunks"
DEFAULT_RPC = "http://127.0.0.1:6061"


def run(command):
    command = [str(x) for x in command]
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True)



def install_chunk(folder, destination, output, overwrite=False):
    """Install a verified chunk, preserving the old version on replacement."""

    if destination.is_symlink():
        raise RuntimeError("Refusing symbolic-link destination")

    if destination.exists():
        if not overwrite:
            raise RuntimeError("Destination already exists")
        if not destination.is_dir():
            raise RuntimeError("Existing destination is not a directory")

        from datetime import datetime, timezone

        backup_root = output / "replaced-chunk-backups"
        backup_root.mkdir(exist_ok=True)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = backup_root / f"{destination.name}-{stamp}"

        if backup.exists():
            raise RuntimeError(f"Backup collision: {backup}")

        destination.rename(backup)
        print(f"BACKUP: {backup}", flush=True)

        try:
            folder.rename(destination)
        except BaseException:
            if not destination.exists():
                backup.rename(destination)
            raise

        print(f"REPLACED: {destination}", flush=True)
    else:
        folder.rename(destination)



def process(start, output, rpc, overwrite=False):
    end = start + 999
    name = f"blocks_{start}_{end}"
    destination = output / name

    print(f"\n=== DOWNLOAD {start}-{end} ===", flush=True)

    if destination.is_symlink():
        raise RuntimeError(f"Refusing symbolic link: {destination}")

    if destination.exists():
        if not destination.is_dir():
            raise RuntimeError(f"Not a directory: {destination}")

        if not overwrite:
            print("EXISTING CHUNK — comparing against Juno", flush=True)
            run([
                sys.executable,
                SCRIPTS / "check_mainnet_chunk.py",
                destination,
                "--rpc", rpc,
            ])
            run([
                sys.executable,
                SCRIPTS / "verify_chunk_metadata.py",
                destination,
            ])
            print(f"IDENTICAL: {name}", flush=True)
            return

        print("OVERWRITE REQUESTED — checking existing chunk", flush=True)

        checks = [
            [
                sys.executable,
                SCRIPTS / "check_mainnet_chunk.py",
                destination,
                "--rpc", rpc,
            ],
            [
                sys.executable,
                SCRIPTS / "verify_chunk_metadata.py",
                destination,
            ],
        ]

        valid = True
        for command in checks:
            result = subprocess.run(
                [str(x) for x in command],
                check=False,
            )
            if result.returncode == 4:
                raise RuntimeError(
                    "RPC failure — refusing overwrite"
                )
            if result.returncode not in (0, 2, 3):
                raise RuntimeError(
                    f"Unexpected verification failure "
                    f"(exit code {result.returncode}) — refusing overwrite"
                )
            if result.returncode != 0:
                valid = False
                break

        if valid:
            print(f"IDENTICAL — overwrite unnecessary: {name}", flush=True)
            return

        print("CHECK FAILED — preparing verified replacement", flush=True)

    # Keep incomplete downloads hidden in a temporary directory.
    with tempfile.TemporaryDirectory(
        prefix=".influence-download-", dir=output
    ) as temporary_name:
        temporary = Path(temporary_name)
        folder = temporary / name
        metadata_output = temporary / "metadata-output"

        run([
            sys.executable, SCRIPTS / "build_mainnet_chunks.py",
            "--rpc", rpc,
            "--start", start,
            "--end", end,
            "--step", 1000,
            "--page-size", 100,
            "--out", temporary,
            "--compress",
        ])

        # Generate metadata using the newly downloaded events.
        run([
            sys.executable, SCRIPTS / "build_block_metadata.py",
            "--rpc", rpc,
            "--chunks", temporary,
            "--out", metadata_output,
        ])

        generated_metadata = (
            metadata_output / f"{name}.metadata.json"
        )
        if not generated_metadata.is_file():
            raise RuntimeError("Generated metadata missing")

        generated_metadata.rename(folder / "metadata.json")

        # Check the complete self-contained chunk.
        run([
            sys.executable,
            SCRIPTS / "verify_chunk_metadata.py",
            folder,
        ])

        manifest = json.loads(
            (folder / "manifest.json").read_text()
        )

        if (
            manifest["from_block"] != start
            or manifest["to_block"] != end
        ):
            raise RuntimeError("Incorrect chunk boundaries")

        install_chunk(folder, destination, output, overwrite)

        print(
            f"CHUNK INSTALLED: {destination} "
            f"({manifest['event_count']} events)",
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--chunks", type=int, required=True)
    parser.add_argument("--rpc", default=DEFAULT_RPC)
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_CHUNKS
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.overwrite and args.check_only:
        parser.error("--overwrite and --check-only cannot be combined")


    if (
        args.start < 218000
        or args.start % 1000 != 0
        or args.chunks < 1
    ):
        parser.error("Invalid block range or chunk count")

    if not args.dry_run:
        args.out.mkdir(parents=True, exist_ok=True)

    for i in range(args.chunks):
        start = args.start + i * 1000
        name = f"blocks_{start}_{start + 999}"

        if args.dry_run:
            print(f"PLANNED: {name}")
        elif args.check_only:
            folder = args.out / name
            if not folder.is_dir():
                raise RuntimeError(f"Missing chunk: {folder}")
            run([
                sys.executable,
                SCRIPTS / "check_mainnet_chunk.py",
                folder,
                "--rpc", args.rpc,
            ])
            run([
                sys.executable,
                SCRIPTS / "verify_chunk_metadata.py",
                folder,
            ])
        else:
            process(start, args.out, args.rpc, args.overwrite)

    print("DOWNLOAD BATCH COMPLETE", flush=True)


if __name__ == "__main__":
    main()
