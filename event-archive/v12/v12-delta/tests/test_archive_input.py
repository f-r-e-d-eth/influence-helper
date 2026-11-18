"""Offline helper-contract tests. Archive wire decoding is tested in Go, not mocked as proof."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from bson import BSON, Binary, Int64, json_util
from bson.datetime_ms import DatetimeMS

import archive_input
import delta


class ArchiveInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=archive_input.ROOT, prefix='v11-test-')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.source = self.work / 'source.gz'
        self.target = self.work / 'target.gz'
        # Opaque inputs: subprocess is replaced with an inventory contract double.
        # These tests do not claim to validate the archive wire format.
        self.source.write_bytes(b'synthetic-source-contract')
        self.target.write_bytes(b'synthetic-target-contract')
        self.decoder = self.work / 'decoder'
        self.decoder.write_text('contract double, never executed')
        self.decoder.chmod(0o700)
        rich = {'_id': {'nested': 'id'}, 'large_date': DatetimeMS(2**62), 'value': Int64(7), 'bytes': Binary(b'\x00\xff', 128)}
        self.rows = {'source': [BSON.encode(rich), BSON.encode({'_id': 'changed', 'value': 1}), BSON.encode({'_id': 'deleted'})],
                     'target': [BSON.encode(rich), BSON.encode({'_id': 'changed', 'value': Int64(1)}), BSON.encode({'_id': 'inserted'})]}
        self.metadata = {'collectionName': 'a', 'uuid': 'retained-uuid',
                         'indexes': [{'v': 2, 'key': {'_id': 1}, 'name': '_id_'},
                                     {'v': 2, 'key': {'z': -1, 'x': 1}, 'name': 'compound', 'partialFilterExpression': {'n': {'$gt': Int64(9)}}}]}
        self.records = {}
        for side, path, block in [('source', self.source, 698999), ('target', self.target, 699999)]:
            record = {'block': block, 'database': delta.ORIGINAL_DB, 'consistent_snapshot': True,
                      'snapshot_method': 'synthetic frozen checkpoint', 'archive_sha256': delta.sha_file(path), 'documents': 3}
            checkpoint = self.work / f'{side}.checkpoint.json'
            checkpoint.write_text(json.dumps(record))
            self.records[side] = checkpoint
        self.output = self.work / 'result.tar.gz'
        self.inventory_mutator = lambda inv, directory: None
        self.runner = patch.object(archive_input.subprocess, 'run', side_effect=self.fake_extract)
        self.runner.start()
        self.addCleanup(self.runner.stop)

    def fake_extract(self, command, **kwargs):
        args = dict(zip(command[1::2], command[2::2]))
        side = 'source' if Path(args['--input']) == self.source else 'target'
        directory = Path(args['--out'])
        entries = []
        for index, (name, rows) in enumerate([('a', self.rows[side]), ('empty', [])]):
            raw = b''.join(rows)
            filename = f'c{index:06d}.bson'
            (directory / filename).write_bytes(raw)
            definition = copy.deepcopy(self.metadata)
            definition['collectionName'] = name
            entries.append({'database': delta.ORIGINAL_DB, 'collection': name, 'metadata': json_util.dumps(definition, json_options=json_util.CANONICAL_JSON_OPTIONS),
                            'type': 'collection', 'file': filename, 'count': len(rows), 'bytes': len(raw),
                            'sha256': hashlib.sha256(raw).hexdigest(), 'crc64': 'checked-by-official-parser'})
        inv = {'protocol': 1, 'tools_revision': archive_input.TOOLS_REVISION, 'archive_version': '0.1',
               'server_version': '7.0', 'tool_version': '100.10.0', 'input_sha256': delta.sha_file(args['--input']), 'collections': entries}
        self.inventory_mutator(inv, directory)
        (directory / 'inventory.json').write_text(json.dumps(inv))
        return SimpleNamespace(returncode=0)

    def generate(self, **kwargs):
        return archive_input.generate_archives(self.source, self.target, self.records['source'], self.records['target'],
                    self.output, self.work, expected_collections=2, min_overlap=0.5, memory_bytes=1024, decoder=self.decoder, **kwargs)

    def test_contract_reuses_exact_bson_comparison_packaging_and_event_metrics(self):
        before = [delta.sha_file(self.source), delta.sha_file(self.target)]
        events = self.work / 'events'
        events.write_bytes(b'event fixture' * 100)
        with patch.object(delta, 'MongoClient', side_effect=AssertionError('must not connect')):
            report = self.generate(event_archive=events)
        self.assertEqual(before, [delta.sha_file(self.source), delta.sha_file(self.target)])
        self.assertEqual(report['event_archive_bytes'], events.stat().st_size)
        self.assertEqual(report['package_sha256'], delta.sha_file(self.output))
        with delta.unpack(self.output, self.work) as (manifest, operations):
            self.assertEqual(manifest['version'], 1)
            self.assertEqual(manifest['collections']['a']['replace'], 1)
            self.assertEqual(manifest['collections']['a']['delete'], 1)
            self.assertEqual(manifest['collections']['a']['insert'], 1)
            self.assertEqual(manifest['source_block'], 698999)
            self.assertIn('retained-uuid', manifest['archive_inputs']['source']['inventory']['collections'][0]['metadata'])
            definition = BSON(bytes.fromhex(manifest['metadata']['a'])).decode()
            self.assertEqual(list(definition['indexes'][1]['key']), ['z', 'x'])
            observed = {}
            with operations.open('rb') as stream:
                for raw in delta.frames(stream):
                    item = BSON(raw).decode()
                    observed[item['op']] = bytes(item['document']) if item['document'] is not None else None
            self.assertIn(observed['replace'], self.rows['target'])
        self.assertEqual(sorted(p.name for p in self.work.iterdir() if p.name.startswith('v11-archives-')), [])

    def test_hash_and_block_consistency_attestations_required(self):
        for field, bad in [('block', 699000), ('consistent_snapshot', False), ('archive_sha256', '0'*64), ('documents', 99)]:
            path = self.records['source']
            original = path.read_text()
            record = json.loads(original)
            record[field] = bad
            path.write_text(json.dumps(record))
            with self.assertRaises(ValueError):
                self.generate()
            self.assertFalse(self.output.exists())
            path.write_text(original)

    def test_extracted_stream_hash_and_count_validation(self):
        for field, value in [('sha256', '0'*64), ('count', 999), ('file', '../../escape.bson')]:
            def mutate(inv, directory, field=field, value=value):
                inv['collections'][0][field] = value
            self.inventory_mutator = mutate
            with self.assertRaises(ValueError):
                self.generate()
            self.assertFalse(self.output.exists())

    def test_metadata_changes_unsupported_fields_and_views_rejected(self):
        for mode in ['changed_index', 'unknown_metadata', 'view']:
            def mutate(inv, directory, mode=mode):
                if mode == 'view':
                    inv['collections'][0]['type'] = 'view'
                else:
                    entry = inv['collections'][0]
                    definition = json_util.loads(entry['metadata'])
                    if mode == 'unknown_metadata':
                        definition['futureMetadata'] = 'must not discard'
                    elif directory.name == 'target':
                        definition['indexes'][1]['key'] = {'x': 1, 'z': -1}
                    entry['metadata'] = json_util.dumps(definition)
            self.inventory_mutator = mutate
            with self.assertRaises(ValueError):
                self.generate()
            self.assertFalse(self.output.exists())

    def test_missing_official_helper_fails_before_input_read(self):
        self.decoder.unlink()
        with self.assertRaisesRegex(ValueError, 'helper unavailable'):
            self.generate()
        self.assertFalse(self.output.exists())

    def test_helper_failure_cleans_scratch_and_cannot_publish(self):
        self.runner.stop()
        with patch.object(archive_input.subprocess, 'run', return_value=SimpleNamespace(returncode=2)):
            with self.assertRaisesRegex(ValueError, 'helper failed'):
                self.generate()
        self.assertFalse(self.output.exists())
        self.assertFalse(any(p.name.startswith('v11-archives-') for p in self.work.iterdir()))

    def test_v11_paths_and_existing_output_are_protected(self):
        with self.assertRaisesRegex(ValueError, 'inside V11'):
            archive_input.v11_path('/tmp/outside-v11')
        self.output.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            self.generate()
        self.assertEqual(self.output.read_bytes(), b'keep')

    def test_unknown_helper_version_and_duplicate_names_are_rejected(self):
        for mode in ['version', 'duplicate']:
            def mutate(inv, directory, mode=mode):
                if mode == 'version':
                    inv['tools_revision'] = 'unrecognized'
                else:
                    inv['collections'][1]['collection'] = 'a'
            self.inventory_mutator = mutate
            with self.assertRaises(ValueError):
                self.generate()
            self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
