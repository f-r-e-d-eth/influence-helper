#!/usr/bin/env python3
"""V11 offline-tested BSON delta proof of concept. No database writes by default."""
import argparse
import contextlib
import gzip
import hashlib
import heapq
import json
import os
from pathlib import Path
import struct
import tarfile
import tempfile
import time

from bson import BSON, Binary
from bson.codec_options import CodecOptions, DatetimeConversion
from bson.raw_bson import RawBSONDocument
from pymongo import MongoClient, InsertOne, ReplaceOne, DeleteOne
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

VERSION = 1
ORIGINAL_DB = 'v12_reconstruction'
RAW = CodecOptions(document_class=RawBSONDocument, datetime_conversion=DatetimeConversion.DATETIME_AUTO)
MAX_RECORD = 64 * 1024 * 1024


def encode(value):
    return BSON.encode(value)


def frames(stream):
    while True:
        prefix = stream.read(4)
        if not prefix:
            return
        if len(prefix) != 4:
            raise ValueError('Truncated BSON length')
        size = struct.unpack('<i', prefix)[0]
        if not 5 <= size <= MAX_RECORD:
            raise ValueError('Invalid BSON record size')
        rest = stream.read(size - 4)
        if len(rest) != size - 4:
            raise ValueError('Truncated BSON record')
        yield prefix + rest


def id_key(raw):
    doc = RawBSONDocument(raw, codec_options=RAW)
    if '_id' not in doc:
        raise ValueError('Document lacks _id')
    return encode({'_id': doc['_id']})


def sorted_documents(documents, directory, memory_bytes=32 * 1024 * 1024, fan_in=32):
    """External byte sort, bounded chunks and bounded file descriptor fan-in."""
    if memory_bytes < 1024 or fan_in < 2:
        raise ValueError('Invalid sort limits')
    with tempfile.TemporaryDirectory(dir=directory) as scratch:
        paths, chunk, size, serial = [], [], 0, 0

        def write_run(items):
            nonlocal serial
            path = Path(scratch) / str(serial)
            serial += 1
            with path.open('wb') as out:
                for key, raw in items:
                    out.write(encode({'key': Binary(key), 'raw': Binary(raw)}))
            return path

        def records(path):
            with path.open('rb') as stream:
                for raw in frames(stream):
                    item = BSON(raw).decode()
                    yield bytes(item['key']), bytes(item['raw'])

        for raw in documents:
            raw = bytes(raw)
            key = id_key(raw)
            chunk.append((key, raw))
            size += len(raw) + len(key) + 128
            if size >= memory_bytes:
                paths.append(write_run(sorted(chunk)))
                chunk, size = [], 0
        if chunk:
            paths.append(write_run(sorted(chunk)))
        while len(paths) > fan_in:
            merged = []
            for offset in range(0, len(paths), fan_in):
                group = paths[offset:offset + fan_in]
                merged.append(write_run(heapq.merge(*(records(p) for p in group))))
                for path in group:
                    path.unlink()
            paths = merged
        previous = None
        for key, raw in heapq.merge(*(records(p) for p in paths)):
            if key == previous:
                raise ValueError('Duplicate exact BSON identity')
            previous = key
            yield key, raw


def collection_delta(left, right, emit):
    a, b = next(left, None), next(right, None)
    counts = dict(source=0, target=0, shared_ids=0, unchanged=0, insert=0, replace=0, delete=0)
    hashes = [hashlib.sha256(), hashlib.sha256()]
    while a is not None or b is not None:
        if b is None or (a is not None and a[0] < b[0]):
            counts['source'] += 1
            hashes[0].update(a[1])
            counts['delete'] += 1
            emit('delete', a[0], None)
            a = next(left, None)
        elif a is None or b[0] < a[0]:
            counts['target'] += 1
            hashes[1].update(b[1])
            counts['insert'] += 1
            emit('insert', b[0], b[1])
            b = next(right, None)
        else:
            counts['source'] += 1
            counts['target'] += 1
            counts['shared_ids'] += 1
            hashes[0].update(a[1])
            hashes[1].update(b[1])
            if a[1] == b[1]:
                counts['unchanged'] += 1
            else:
                counts['replace'] += 1
                emit('replace', b[0], b[1])
            a, b = next(left, None), next(right, None)
    counts['source_sha256'], counts['target_sha256'] = [h.hexdigest() for h in hashes]
    denominator = min(counts['source'], counts['target'])
    counts['identity_overlap'] = counts['shared_ids'] / denominator if denominator else 1.0
    return counts


def metadata(db):
    result = {}
    for info in db.list_collections():
        name = info['name']
        if name.startswith('system.') or info['type'] != 'collection':
            raise ValueError(f'Unsupported system collection/view: {name}')
        options = info.get('options', {})
        if options.get('capped') or options.get('timeseries') or options.get('encryptedFields'):
            raise ValueError(f'Unsupported collection options: {name}')
        indexes = [dict(index) for index in db[name].list_indexes()]
        indexes.sort(key=lambda index: index['name'])
        result[name] = encode({'options': options, 'indexes': indexes}).hex()
    return dict(sorted(result.items()))


def documents(db, name):
    from pymongo.database import Database
    if not isinstance(db, Database):
        yield from db.v11_documents(name)
        return
    with db.get_collection(name, codec_options=RAW).find({}, batch_size=256) as cursor:
        for document in cursor:
            yield document.raw


def readonly_database(uri, name):
    client = MongoClient(uri, serverSelectionTimeoutMS=10000, appname='v11-delta-read-only')
    try:
        status = client.admin.command('connectionStatus', showPrivileges=True)
        auth = status['authInfo']
        roles = auth.get('authenticatedUserRoles', [])
        privileges = auth.get('authenticatedUserPrivileges', [])
        allowed = {'find', 'listCollections', 'listIndexes', 'killCursors', 'collStats', 'dbStats', 'changeStream', 'dbHash', 'indexStats', 'listSearchIndexes', 'planCacheRead'}
        if not auth.get('authenticatedUsers') or not roles or not privileges or any(set(p['actions']) - allowed for p in privileges):
            raise ValueError('A dedicated authenticated read-only principal is required; unauthenticated/admin connections refused')
        return client, client.get_database(name, read_concern=ReadConcern('majority'))
    except BaseException:
        client.close()
        raise


def sha_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def generate(source, target, output, scratch, expected_collections=64, min_overlap=0.9, event_archive=None, memory_bytes=32*1024*1024, *, provenance=None, expected_counts=None, started=None, source_block=698999, target_block=699999):
    started = time.monotonic() if started is None else started
    source_meta, target_meta = metadata(source), metadata(target)
    if source_meta != target_meta:
        raise ValueError('Collection metadata/index changes unsupported')
    if len(source_meta) != expected_collections:
        raise ValueError(f'Expected {expected_collections} collections, found {len(source_meta)}')
    output = Path(output)
    if output.exists():
        raise ValueError('Output already exists')
    with tempfile.TemporaryDirectory(dir=scratch) as work:
        ops = Path(work) / 'operations.bson'
        manifest = dict(format='influence-mongodb-delta', version=VERSION, source_block=source_block,
                        target_block=target_block, metadata=source_meta, collections={})
        with ops.open('wb') as stream:
            for name in source_meta:
                def emit(op, key, raw):
                    stream.write(encode({'collection': name, 'op': op, 'key': Binary(key),
                                         'document': Binary(raw) if raw is not None else None}))
                with contextlib.closing(sorted_documents(documents(source, name), work, memory_bytes)) as left, contextlib.closing(sorted_documents(documents(target, name), work, memory_bytes)) as right:
                    stats = collection_delta(left, right, emit)
                if stats['identity_overlap'] < min_overlap:
                    raise ValueError(f'Identity continuity failed for {name}: {stats["identity_overlap"]:.3%}')
                manifest['collections'][name] = stats
        if expected_counts is not None and tuple(sum(stats[side] for stats in manifest['collections'].values()) for side in ('source', 'target')) != tuple(expected_counts):
            raise ValueError('Snapshot document totals do not match checkpoint provenance')
        if provenance is not None:
            manifest['archive_inputs'] = provenance
        if metadata(source) != source_meta or metadata(target) != target_meta:
            raise ValueError('Metadata changed during generation')
        manifest['identity_policy'] = {'minimum_overlap': min_overlap, 'measure': 'shared exact BSON ids / min(source_count,target_count)'}
        manifest['operations_sha256'] = sha_file(ops)
        manifest['operations_bytes'] = ops.stat().st_size
        manifest['scan_seconds'] = time.monotonic() - started
        manifest_path = Path(work) / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
        # Exclusive creation prevents accidental overwrite. Remove incomplete output on errors.
        try:
            with output.open('xb') as rawout, gzip.GzipFile(fileobj=rawout, mode='wb', mtime=0, filename='') as compressed, tarfile.open(fileobj=compressed, mode='w|') as archive:
                for name, path in [('manifest.json', manifest_path), ('operations.bson', ops)]:
                    info = tarfile.TarInfo(name)
                    info.size = path.stat().st_size
                    with path.open('rb') as stream:
                        archive.addfile(info, stream)
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    report = {'generation_seconds': time.monotonic() - started, 'compressed_bytes': output.stat().st_size,
              'package_sha256': sha_file(output), 'source_documents': sum(s['source'] for s in manifest['collections'].values()),
              'target_documents': sum(s['target'] for s in manifest['collections'].values())}
    if event_archive:
        report['event_archive_bytes'] = Path(event_archive).stat().st_size
        report['delta_to_event_archive_ratio'] = report['compressed_bytes'] / report['event_archive_bytes']
        report['event_archive_sha256'] = sha_file(event_archive)
    report['generation_seconds'] = time.monotonic() - started
    Path(str(output) + '.report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


@contextlib.contextmanager
def unpack(package, scratch):
    with tempfile.TemporaryDirectory(dir=scratch) as work:
        paths = {}
        with tarfile.open(package, 'r|gz') as archive:
            for entry in archive:
                if entry.name not in {'manifest.json', 'operations.bson'} or entry.name in paths or not entry.isfile():
                    raise ValueError('Unexpected package member')
                if entry.name == 'manifest.json' and entry.size > 16 * 1024 * 1024:
                    raise ValueError('Manifest too large')
                path = Path(work) / entry.name
                with archive.extractfile(entry) as source, path.open('wb') as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                paths[entry.name] = path
        if set(paths) != {'manifest.json', 'operations.bson'}:
            raise ValueError('Missing package member')
        manifest = json.loads(paths['manifest.json'].read_text())
        if manifest['format'] != 'influence-mongodb-delta' or manifest['version'] != VERSION:
            raise ValueError('Unsupported delta format/version')
        ops = paths['operations.bson']
        if ops.stat().st_size != manifest['operations_bytes'] or sha_file(ops) != manifest['operations_sha256']:
            raise ValueError('Operations integrity failure')
        validate_operations(ops, manifest)
        yield manifest, ops


def validate_operations(path, manifest):
    counts = {name: dict(insert=0, replace=0, delete=0) for name in manifest['collections']}
    previous = None
    with path.open('rb') as stream:
        for raw in frames(stream):
            item = BSON(raw).decode()
            name, op, key = item['collection'], item['op'], bytes(item['key'])
            if name not in counts or op not in counts[name]:
                raise ValueError('Unknown collection or operation')
            pair = (name, key)
            if previous is not None and pair <= previous:
                raise ValueError('Duplicate or out-of-order operation')
            previous = pair
            identity = BSON(key).decode(codec_options=RAW)
            if list(identity) != ['_id']:
                raise ValueError('Invalid identity')
            if op == 'delete':
                if item['document'] is not None:
                    raise ValueError('Delete contains document')
            elif id_key(bytes(item['document'])) != key:
                raise ValueError('Document identity mismatch')
            counts[name][op] += 1
    if set(manifest['metadata']) != set(counts):
        raise ValueError('Metadata/collection mismatch')
    for name, actual in counts.items():
        stats = manifest['collections'][name]
        if any(actual[op] != stats[op] for op in actual):
            raise ValueError('Operation count mismatch')
        if stats['source'] - stats['delete'] + stats['insert'] != stats['target']:
            raise ValueError('Invalid document count transition')


def checkpoint(db, manifest, side, scratch, memory_bytes, metrics=None):
    metrics = metrics if metrics is not None else {}
    metrics.update(seconds=0.0, documents_scanned=0, collections={})
    started = time.monotonic()
    try:
        if metadata(db) != manifest['metadata']:
            raise ValueError('Checkpoint metadata/index mismatch')
        for name, stats in manifest['collections'].items():
            measured = dict(seconds=0.0, documents_scanned=0)
            metrics['collections'][name] = measured
            collection_started = time.monotonic()

            def scanned_documents():
                for raw in documents(db, name):
                    measured['documents_scanned'] += 1
                    metrics['documents_scanned'] += 1
                    yield raw

            count, digest = 0, hashlib.sha256()
            try:
                for _, raw in sorted_documents(scanned_documents(), scratch, memory_bytes):
                    count += 1
                    digest.update(raw)
                if count != stats[side] or digest.hexdigest() != stats[side + '_sha256']:
                    raise ValueError(f'{side} checkpoint mismatch: {name}')
            finally:
                measured['seconds'] = time.monotonic() - collection_started
    finally:
        metrics['seconds'] = time.monotonic() - started


def journal_write(path, value, exclusive=False):
    # Create durable intent before any writes. Subsequent state changes use atomic replacement.
    if exclusive:
        with open(path, 'x') as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        temp = Path(str(path) + '.tmp')
        with temp.open('w') as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    fd = os.open(str(Path(path).resolve().parent), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def apply_package(db, package, journal, scratch, memory_bytes=32*1024*1024, batch_bytes=8*1024*1024, *, verify_fingerprints=True):
    if db.name == ORIGINAL_DB or not db.name.startswith('v12_disposable_'):
        raise ValueError('Application requires a separate database named v12_disposable_*')
    started = time.monotonic()
    metrics = dict(package_extraction_validation_seconds=0.0, starting_checkpoint={},
                   final_checkpoint={}, bulk_writes=dict(seconds=0.0, batches=0,
                   completed_operations=0, operations_per_second=0.0, collections={}))
    with unpack(package, scratch) as (manifest, ops):
        metrics['package_extraction_validation_seconds'] = time.monotonic() - started
        if Path(journal).exists():
            raise FileExistsError('Existing application journal; restore a fresh disposable database before retrying')
        if verify_fingerprints:
            checkpoint(db, manifest, 'source', scratch, memory_bytes, metrics['starting_checkpoint'])
        elif metadata(db) != manifest['metadata']:
            raise ValueError('Source metadata/index mismatch')
        state = dict(status='applying', database=db.name, package_sha256=sha_file(package), completed_operations=0, metrics=metrics)
        metrics['bulk_writes']['collections'] = {
            name: dict(seconds=0.0, batches=0, completed_operations=0, operations_per_second=0.0)
            for name in manifest['collections']}
        journal_write(journal, state, exclusive=True)
        try:
            batch, size, current = [], 0, None

            def flush():
                nonlocal batch, size
                if not batch:
                    return
                writes = metrics['bulk_writes']
                collection_metrics = writes['collections'][current]
                collection = db.get_collection(current, codec_options=RAW)
                write_started = time.monotonic()
                try:
                    result = collection.bulk_write(batch, ordered=True)
                finally:
                    elapsed = time.monotonic() - write_started
                    for measured in (writes, collection_metrics):
                        measured['seconds'] += elapsed
                        measured['batches'] += 1
                        measured['operations_per_second'] = (
                            measured['completed_operations'] / measured['seconds']
                            if measured['seconds'] else 0.0)

                expected_insert = sum(isinstance(op, InsertOne) for op in batch)
                expected_replace = sum(isinstance(op, ReplaceOne) for op in batch)
                expected_delete = sum(isinstance(op, DeleteOne) for op in batch)
                if (result.inserted_count, result.matched_count, result.deleted_count) != (expected_insert, expected_replace, expected_delete):
                    raise ValueError('Bulk result count mismatch; disposable database must be restored')
                state['completed_operations'] += len(batch)
                for measured in (writes, collection_metrics):
                    measured['completed_operations'] += len(batch)
                    measured['operations_per_second'] = (
                        measured['completed_operations'] / measured['seconds']
                        if measured['seconds'] else 0.0)
                journal_write(journal, state)
                batch, size = [], 0

            with ops.open('rb') as stream:
                for raw in frames(stream):
                    item = BSON(raw).decode()
                    if current != item['collection'] or size + len(raw) > batch_bytes:
                        flush()
                    current = item['collection']
                    identity = BSON(bytes(item['key'])).decode(codec_options=RAW)
                    op = item['op']
                    if op == 'delete':
                        operation = DeleteOne(identity)
                    elif op == 'insert':
                        operation = InsertOne(RawBSONDocument(bytes(item['document']), codec_options=RAW))
                    else:
                        operation = ReplaceOne(identity, RawBSONDocument(bytes(item['document']), codec_options=RAW), upsert=False)
                    batch.append(operation)
                    size += len(raw)
                flush()
            if verify_fingerprints:
                checkpoint(db, manifest, 'target', scratch, memory_bytes, metrics['final_checkpoint'])
            elif metadata(db) != manifest['metadata']:
                raise ValueError('Target metadata/index mismatch')
            state.update(status='applied_checkpoint_verified' if verify_fingerprints else 'applied_unverified_benchmark',
                         fingerprints_verified=verify_fingerprints, application_seconds=time.monotonic() - started,
                         full_comparison_required=True)
            journal_write(journal, state)
        except BaseException as exc:
            state.update(status='failed_restore_required', error=str(exc),
                         application_seconds=time.monotonic() - started)
            journal_write(journal, state)
            raise
    return state


def verify(left_db, right_db, scratch, memory_bytes=32*1024*1024, max_examples=20):
    started = time.monotonic()
    left_meta, right_meta = metadata(left_db), metadata(right_db)
    report = {'equal': left_meta == right_meta, 'metadata_equal': left_meta == right_meta,
              'metadata_differences': [n for n in sorted(set(left_meta) | set(right_meta)) if left_meta.get(n) != right_meta.get(n)],
              'collections': {}}
    for name in sorted(set(left_meta) | set(right_meta)):
        examples = []
        def difference(op, key, raw):
            if len(examples) < max_examples:
                examples.append({'difference': {'insert': 'missing_in_result', 'delete': 'extra_in_result', 'replace': 'different_BSON'}[op], 'id_bson_hex': key.hex()})
        with contextlib.closing(sorted_documents(documents(left_db, name), scratch, memory_bytes)) as left, contextlib.closing(sorted_documents(documents(right_db, name), scratch, memory_bytes)) as right:
            stats = collection_delta(left, right, difference)
        stats['examples'] = examples
        report['collections'][name] = stats
        if stats['insert'] or stats['replace'] or stats['delete']:
            report['equal'] = False
    report['verification_seconds'] = time.monotonic() - started
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch', default='.', help='Temporary files; use a path inside v11-delta')
    parser.add_argument('--memory-mib', type=int, default=32)
    commands = parser.add_subparsers(dest='command', required=True)
    gen = commands.add_parser('generate')
    gen.add_argument('--source-uri', required=True)
    gen.add_argument('--target-uri', required=True)
    gen.add_argument('--database', default=ORIGINAL_DB)
    gen.add_argument('--output', required=True)
    gen.add_argument('--expected-collections', type=int, default=64)
    gen.add_argument('--min-identity-overlap', type=float, default=0.9)
    gen.add_argument('--event-archive')
    archives = commands.add_parser('generate-archives', help='Offline archives via the official MongoDB archive parser')
    archives.add_argument('--source-archive', required=True)
    archives.add_argument('--target-archive', required=True)
    archives.add_argument('--source-checkpoint', required=True)
    archives.add_argument('--target-checkpoint', required=True)
    archives.add_argument('--database', default=ORIGINAL_DB)
    archives.add_argument('--output', required=True)
    archives.add_argument('--expected-collections', type=int, default=64)
    archives.add_argument('--min-identity-overlap', type=float, default=0.9)
    archives.add_argument('--event-archive')
    archives.add_argument('--archive-decoder', default=str(Path(__file__).resolve().parent / 'archive-tools' / 'archive-extract'))
    app = commands.add_parser('apply')
    app.add_argument('--uri', required=True)
    app.add_argument('--database', required=True)
    app.add_argument('--package', required=True)
    app.add_argument('--journal', required=True)
    app.add_argument('--allow-disposable-writes', action='store_true')
    check = commands.add_parser('verify')
    check.add_argument('--result-uri', required=True)
    check.add_argument('--result-database', required=True)
    check.add_argument('--target-uri', required=True)
    check.add_argument('--target-database', default=ORIGINAL_DB)
    check.add_argument('--report', required=True)
    args = parser.parse_args()
    memory = args.memory_mib * 1024 * 1024
    with contextlib.ExitStack() as stack:
        def read(uri, name):
            client, db = readonly_database(uri, name)
            stack.callback(client.close)
            return db
        if args.command == 'generate':
            if not 0 <= args.min_identity_overlap <= 1:
                parser.error('Identity overlap must be between 0 and 1')
            result = generate(read(args.source_uri, args.database), read(args.target_uri, args.database), args.output,
                              args.scratch, args.expected_collections, args.min_identity_overlap, args.event_archive, memory)
        elif args.command == 'generate-archives':
            from archive_input import generate_archives
            result = generate_archives(args.source_archive, args.target_archive, args.source_checkpoint,
                                       args.target_checkpoint, args.output, args.scratch, args.database,
                                       args.expected_collections, args.min_identity_overlap, args.event_archive,
                                       memory, args.archive_decoder)
        elif args.command == 'apply':
            if not args.allow_disposable_writes:
                parser.error('--allow-disposable-writes is required')
            client = stack.enter_context(MongoClient(args.uri, serverSelectionTimeoutMS=10000, appname='v11-disposable-importer'))
            db = client.get_database(args.database, read_concern=ReadConcern('majority'), write_concern=WriteConcern(w='majority', j=True))
            result = apply_package(db, args.package, args.journal, args.scratch, memory)
        else:
            result = verify(read(args.result_uri, args.result_database), read(args.target_uri, args.target_database), args.scratch, memory)
            Path(args.report).write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))
        if args.command == 'verify' and not result['equal']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
