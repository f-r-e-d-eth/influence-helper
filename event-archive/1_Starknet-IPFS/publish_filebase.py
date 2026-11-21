#!/usr/bin/env python3
"""Publish an explicitly bounded, verified Adalaris manifest to Filebase/IPFS."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.request import urlopen

BUCKET = 'adalaris-mainnet'
GATEWAY = 'https://ipfs.filebase.io/ipfs/'
INITIAL = [
    ('v12-bootstrap.archive.gz', -1, 19506667, '59c132dc09f6e63bf9ff278b4ba3ea52fce68275eb7f7a0287bd79bfe584fe15'),
    ('v12-delta-0-99999.tar.gz', 99999, 5607, 'fcc07de2031a9a0d0f04b8b2d46881bfa35314d5efe4b0c25dccd503b5e9a589'),
    ('v12-delta-100000-199999.tar.gz', 199999, 5656, '722c19f3cbbddaf3676a4628f6ae72f523b2056924e04fff833d8ffee0900f05'),
    ('v12-delta-200000-299999.tar.gz', 299999, 1245566, 'ccdc4ac2da6fec385293d5bc2e92157ff09f330b0966d4c1f4b9f638631d7a7a'),
    ('v12-delta-300000-399999.tar.gz', 399999, 5623037, '0d357838cd4ff8d2d306f3d3242b7348c4a98de63af94ed58a5f0471a01d9145'),
    ('v12-delta-400000-499999.tar.gz', 499999, 2503487, '669ddc8dfbf9baac7c69fc3f9b1043186ffa82da489fffea85e173590d636007'),
    ('v12-delta-500000-599999.tar.gz', 599999, 2543194, '8df65b8e58f434b0ddf2a7cda922c5c954887bd20cf4d76426f10c05dc092ef5'),
]


def measure(stream, limit):
    digest = hashlib.sha256()
    count = 0
    while chunk := stream.read(1024 * 1024):
        count += len(chunk)
        if count > limit:
            raise ValueError('File exceeds expected size')
        digest.update(chunk)
    return count, digest.hexdigest()


def preflight(root, entries):
    paths = []
    for name, end, size, sha in entries:
        candidates = list(root.rglob(name))
        if len(candidates) != 1 or not candidates[0].is_file():
            raise ValueError(f'Expected exactly one file: {name}; found {len(candidates)}')
        path = candidates[0]
        with path.open('rb') as stream:
            if measure(stream, size) != (size, sha):
                raise ValueError(f'Local SHA-256 or size mismatch: {path}')
        print(f'Local verified: {name} ({size:,} bytes)', flush=True)
        paths.append(path)
    return paths


def gateway_check(cid, size, sha):
    for attempt in range(5):
        try:
            with urlopen(GATEWAY + cid, timeout=60) as response:
                if measure(response, size) != (size, sha):
                    raise ValueError('IPFS download differs from local file')
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))


def publish(client, entries, paths):
    from botocore.exceptions import ClientError
    records = []
    for (name, end, size, sha), path in zip(entries, paths, strict=True):
        try:
            head = client.head_object(Bucket=BUCKET, Key=name)
        except ClientError as exc:
            if exc.response['Error']['Code'] not in ('404', 'NoSuchKey', 'NotFound'):
                raise
            print(f'Uploading: {name}', flush=True)
            client.upload_file(str(path), BUCKET, name)
            head = client.head_object(Bucket=BUCKET, Key=name)
        cid = head.get('Metadata', {}).get('cid') or head.get('ResponseMetadata', {}).get('HTTPHeaders', {}).get('x-amz-meta-cid')
        if not cid or not re.fullmatch(r'b[a-z2-7]+|Qm[1-9A-HJ-NP-Za-km-z]{44}', cid):
            raise ValueError(f'No valid IPFS CID for {name}; retry after pinning')
        if head['ContentLength'] != size:
            raise ValueError(f'Remote size mismatch: {name}; refusing overwrite')
        gateway_check(cid, size, sha)
        records.append(dict(filename=name, end_block=end, bytes=size, sha256=sha, cid=cid))
        print(f'IPFS verified: {name} {cid}', flush=True)
    return dict(format='influence-v12-world', version=1, bootstrap=records[0], deltas=records[1:])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True, help='Root containing the bootstrap and deltas')
    parser.add_argument('--output', type=Path, default=Path('manifest-filebase.json'))
    parser.add_argument('--upload', action='store_true', help='Allow upload; default is local validation only')
    args = parser.parse_args()
    paths = preflight(args.data, INITIAL)
    if not args.upload:
        print('Dry run complete. No network requests made.')
        return
    if args.output.exists():
        parser.error('Output already exists; choose a new path')
    import boto3
    from botocore.config import Config
    client = boto3.client('s3', endpoint_url='https://s3.filebase.com', region_name='us-east-1',
                          aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
                          aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'],
                          config=Config(signature_version='s3v4', request_checksum_calculation='when_required', response_checksum_validation='when_required'))
    manifest = publish(client, INITIAL, paths)
    with tempfile.NamedTemporaryFile(mode='w', dir=args.output.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(manifest, stream, indent=2)
        stream.write('\n')
    try:
        os.link(temporary, args.output)
    finally:
        temporary.unlink()
    print(f'Published and verified manifest: {args.output}')


if __name__ == '__main__':
    main()
