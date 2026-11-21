"""Trusted manifest contract and optional HTTPS retrieval."""
import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path
STEP = 100_000

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text())


def validate(manifest):
    if manifest.get('format') != 'influence-v12-world' or manifest.get('version') != 1:
        raise ValueError('Unsupported manifest format')
    entries = [manifest['bootstrap'], *manifest['deltas']]
    for i, entry in enumerate(entries):
        end = i * STEP - 1
        expected = 'v12-bootstrap.archive.gz' if not i else f'v12-delta-{end-STEP+1}-{end}.tar.gz'
        if entry['filename'] != expected or entry['end_block'] != end:
            raise ValueError('Non-contiguous or unexpected checkpoint filename')
        if type(entry['bytes']) is not int or entry['bytes'] <= 0 or not re.fullmatch('[0-9a-f]{64}', entry['sha256']):
            raise ValueError('Invalid file size or SHA256')
        if entry.get('cid') and not re.fullmatch(r'b[a-z2-7]+|Qm[1-9A-HJ-NP-Za-km-z]{44}', entry['cid']):
            raise ValueError('Invalid IPFS CID')
    if len(entries) < 2:
        raise ValueError('At least one verified delta is required')
    return entries


def make_manifest(data, end, output, cid_file=None):
    if end < STEP-1 or (end+1) % STEP:
        raise ValueError('Endpoint must be a 100,000-block checkpoint')
    cid_map = read(cid_file) if cid_file else {}
    bootstrap = data / 'snapshots/v12-bootstrap.archive.gz'
    record = read(data / 'snapshots/v12-bootstrap.json')
    def entry(path, block, expected_hash, expected_bytes):
        size = path.stat().st_size
        if size != expected_bytes or digest(path) != expected_hash:
            raise ValueError(f'Integrity mismatch: {path.name}')
        result = dict(filename=path.name, end_block=block, bytes=size, sha256=expected_hash)
        remote = cid_map.get(path.name)
        if remote:
            if remote['bytes'] != size:
                raise ValueError(f'IPFS catalog size mismatch: {path.name}; upload the matching file')
            result['cid'] = remote['cid']
        return result
    manifest = dict(format='influence-v12-world', version=1,
                    bootstrap=entry(bootstrap, -1, record['archive_sha256'], record['archive_bytes']), deltas=[])
    for block in range(STEP-1, end+1, STEP):
        path = data / 'deltas' / f'v12-delta-{block-STEP+1}-{block}.tar.gz'
        report = read(Path(str(path)+'.report.json'))
        journal = read(data / 'verification' / f'apply-{block}.json')
        if journal['status'] != 'applied_checkpoint_verified' or journal['package_sha256'] != report['package_sha256']:
            raise ValueError(f'Delta not verified: {block}')
        item = entry(path, block, report['package_sha256'], report['compressed_bytes'])
        for key in ('source_documents', 'target_documents', 'generation_seconds'):
            if key in report:
                item[key] = report[key]
        for key in ('completed_operations', 'application_seconds'):
            if key in journal:
                item[key] = journal[key]
        manifest['deltas'].append(item)
    validate(manifest)
    with output.open('x') as stream:
        json.dump(manifest, stream, indent=2)
        stream.write('\n')


def fetch_manifest(url):
    if not url.startswith('https://'):
        raise ValueError('Manifest URL must use HTTPS')
    with urllib.request.urlopen(url, timeout=30) as response:
        if not response.geturl().startswith('https://'):
            raise ValueError('Manifest redirect must use HTTPS')
        raw = response.read(16*1024*1024+1)
    if len(raw)>16*1024*1024:
        raise ValueError('Manifest exceeds size limit')
    validate(json.loads(raw))
    return raw
