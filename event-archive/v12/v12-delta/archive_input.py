"""Read-only archive adapter. All archive framing is decoded by MongoDB's Go parser."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from bson import json_util
from bson.codec_options import DatetimeConversion

ROOT = Path(__file__).resolve().parent
TOOLS_REVISION = '6d4f001be3fcf673de04d20176e90ee02ef233a9'
DEFAULT_DECODER = ROOT / 'archive-tools' / 'archive-extract'


def v11_path(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f'Archive mode output/scratch must stay inside V11: {path}')
    return path


def load_json(path):
    if Path(path).stat().st_size > 16 * 1024 * 1024:
        raise ValueError('JSON exceeds 16 MiB limit')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f'Duplicate JSON field: {key}')
            value[key] = item
        return value
    return json.loads(Path(path).read_text(), object_pairs_hook=unique)


def checkpoint_record(path, block, database):
    record = load_json(path)
    if (record.get('block') != block or record.get('database') != database or
            record.get('consistent_snapshot') is not True or
            not isinstance(record.get('snapshot_method'), str) or not record['snapshot_method'].strip() or
            not re.fullmatch('[0-9a-f]{64}', record.get('archive_sha256', '')) or
            type(record.get('documents')) is not int or record['documents'] < 0):
        raise ValueError(f'Checkpoint provenance must attest a consistent snapshot at block {block}, database, SHA-256, and document count')
    return record


class ArchiveCollection:
    def __init__(self, indexes):
        self.indexes = indexes

    def list_indexes(self):
        return iter(self.indexes)


class ArchiveDatabase:
    """Only metadata and BSON iterators, with no MongoDB client or write API."""
    def __init__(self, directory, database, inventory):
        self.directory, self.name = Path(directory), database
        self.inventory = inventory
        self.collections = {}
        files = set()
        for entry in inventory['collections']:
            name = entry['collection']
            if (entry['database'] != database or not name or name in self.collections or
                    name.startswith('system.') or entry['type'] not in ('', 'collection')):
                raise ValueError('Unsupported or duplicate collection definition')
            filename = entry['file']
            if not re.fullmatch(r'c[0-9]{6}\.bson', filename) or filename in files:
                raise ValueError('Invalid extracted file name')
            files.add(filename)
            # MongoDB metadata is Extended JSON, not ordinary JSON. Keep the original
            # metadata text in provenance as well as its typed BSON representation.
            options = json_util.JSONOptions(datetime_conversion=DatetimeConversion.DATETIME_AUTO)
            definition = json_util.loads(entry['metadata'], json_options=options)
            if set(definition) - {'options', 'indexes', 'uuid', 'collectionName', 'type'}:
                raise ValueError('Unsupported archive metadata fields')
            if definition.get('collectionName', name) != name or definition.get('type', 'collection') != 'collection':
                raise ValueError('Metadata namespace/type mismatch')
            definition.setdefault('options', {})  # mongodump uses omitempty for empty options.
            if not isinstance(definition['options'], dict) or not isinstance(definition.get('indexes'), list):
                raise ValueError('Archive must include complete collection options and indexes')
            indexes = definition['indexes']
            names = [index.get('name') for index in indexes]
            if any(not isinstance(n, str) for n in names) or len(set(names)) != len(names):
                raise ValueError('Invalid or missing index definitions')
            if any(not isinstance(index.get('key'), dict) for index in indexes):
                raise ValueError('Index keys missing')
            self.collections[name] = (entry, definition)

    def list_collections(self):
        for name, (_, definition) in self.collections.items():
            yield {'name': name, 'type': 'collection', 'options': definition['options']}

    def __getitem__(self, name):
        return ArchiveCollection(self.collections[name][1]['indexes'])

    def v11_documents(self, name):
        from delta import frames
        with (self.directory / self.collections[name][0]['file']).open('rb') as stream:
            yield from frames(stream)


def extract_archive(path, directory, database, expected_collections, decoder, expected):
    """Only run the explicit local helper; no shell, Docker, or fallback decoder."""
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError('Archive must be a regular local file')
    decoder = Path(decoder).resolve(strict=True)
    if not decoder.is_file() or not os.access(decoder, os.X_OK):
        raise ValueError('Build/test the official MongoDB archive helper first')
    before = path.stat()
    # Diagnostics go to bounded-memory local scratch, never PIPE capture of unbounded logs.
    log = Path(directory).parent / (Path(directory).name + '.decoder.log')
    with log.open('xb') as diagnostics:
        result = subprocess.run([str(decoder), '--input', str(path), '--out', str(directory),
                                 '--database', database, '--expected-collections', str(expected_collections)],
                                stdin=subprocess.DEVNULL, stdout=diagnostics, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        with log.open('rb') as stream:
            message = stream.read(4096).decode(errors='replace')
        raise ValueError(f'Official archive helper failed ({result.returncode}): {message}')
    inventory = load_json(Path(directory) / 'inventory.json')
    if (inventory.get('protocol') != 1 or inventory.get('tools_revision') != TOOLS_REVISION or
            inventory.get('archive_version') != '0.1' or len(inventory.get('collections', [])) != expected_collections):
        raise ValueError('Unsupported helper inventory/version')
    if inventory['input_sha256'] != expected['archive_sha256']:
        raise ValueError('Archive SHA-256 does not match checkpoint provenance')
    # Verify helper output before comparison, including all extracted raw BSON byte streams.
    database_view = ArchiveDatabase(directory, database, inventory)
    total = 0
    for entry in inventory['collections']:
        extracted = Path(directory) / entry['file']
        digest, actual = hashlib.sha256(), 0
        for raw in database_view.v11_documents(entry['collection']):
            digest.update(raw)
            actual += 1
        if extracted.stat().st_size != entry['bytes'] or digest.hexdigest() != entry['sha256']:
            raise ValueError('Extracted BSON SHA-256/length mismatch')
        if actual != entry['count']:
            raise ValueError('Extracted BSON count mismatch')
        total += actual
    if 'documents' in expected and total != expected['documents']:
        raise ValueError('Archive document count does not match checkpoint provenance')
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('Input archive changed during extraction')
    return database_view


def generate_archives(source, target, source_checkpoint, target_checkpoint, output, scratch,
                      database='influence_mainnet_replay_v10', expected_collections=64, min_overlap=0.9,
                      event_archive=None, memory_bytes=32*1024*1024, decoder=DEFAULT_DECODER):
    from delta import generate, unpack
    started = time.monotonic()
    scratch, output = v11_path(scratch), v11_path(output)
    report_path = v11_path(str(output) + '.report.json')
    if output.exists() or report_path.exists():
        raise FileExistsError('Package or generation report already exists')
    if not 0 <= min_overlap <= 1 or memory_bytes < 1024 or not 1 <= expected_collections <= 4096:
        raise ValueError('Invalid archive generation limits')
    if event_archive is not None and Path(event_archive).stat().st_size == 0:
        raise ValueError('Event archive must not be empty')
    source_record = checkpoint_record(source_checkpoint, 698999, database)
    target_record = checkpoint_record(target_checkpoint, 699999, database)
    # Refuse missing dependencies before reading any input archive.
    if not Path(decoder).is_file() or not os.access(decoder, os.X_OK):
        raise ValueError('Official archive helper unavailable; build and run its offline Go tests first (see README)')
    if Path(source).resolve() == Path(target).resolve():
        raise ValueError('Source and target archives must differ')
    with tempfile.TemporaryDirectory(dir=scratch, prefix='v11-archives-') as work:
        views, provenance = [], {'backend': 'mongodb-database-tools-archive', 'tools_revision': TOOLS_REVISION}
        for side, path, record in [('source', source, source_record), ('target', target, target_record)]:
            directory = Path(work) / side
            directory.mkdir()
            side_start = time.monotonic()
            view = extract_archive(path, directory, database, expected_collections, decoder, record)
            views.append(view)
            provenance[side] = {'checkpoint': record, 'inventory': view.inventory,
                                'extraction_seconds': time.monotonic() - side_start}
        staged = Path(work) / 'delta.tar.gz'
        report = generate(*views, staged, work, expected_collections, min_overlap, event_archive, memory_bytes,
                          provenance=provenance, expected_counts=(source_record['documents'], target_record['documents']), started=started)
        # Reuse the existing complete package integrity and operation validation before publishing.
        with unpack(staged, work):
            pass
        report['generation_seconds'] = time.monotonic() - started
        report['archive_inputs'] = {side: {'sha256': provenance[side]['checkpoint']['archive_sha256'],
                                           'extraction_seconds': provenance[side]['extraction_seconds']} for side in ('source', 'target')}
        # Same-filesystem exclusive hard link publishes only a fully validated package.
        os.link(staged, output)
        try:
            with report_path.open('x') as stream:
                json.dump(report, stream, indent=2)
                stream.write('\n')
        except BaseException:
            output.unlink()
            raise
        return report
