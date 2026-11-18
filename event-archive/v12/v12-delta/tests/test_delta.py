import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bson import BSON, Binary, Decimal128, Int64, ObjectId, Regex, Timestamp
from bson.raw_bson import RawBSONDocument
from bson.datetime_ms import DatetimeMS
from datetime import datetime, timezone
from types import SimpleNamespace

import delta


class Collection:
    def __init__(self, rows):
        self.rows = {delta.id_key(raw): raw for raw in rows}
        self.fail = False
        self.calls = 0

    def list_indexes(self):
        return [{'v': 2, 'key': {'_id': 1}, 'name': '_id_'}]

    def bulk_write(self, operations, ordered):
        self.calls += 1
        if self.fail:
            raise RuntimeError('synthetic interruption')
        counts = dict(inserted_count=0, matched_count=0, deleted_count=0)
        for operation in operations:
            if isinstance(operation, delta.InsertOne):
                raw = operation._doc.raw
                key = delta.id_key(raw)
                if key in self.rows:
                    raise ValueError('duplicate')
                self.rows[key] = raw
                counts['inserted_count'] += 1
            else:
                key = BSON.encode(operation._filter)
                if key in self.rows:
                    if isinstance(operation, delta.DeleteOne):
                        del self.rows[key]
                        counts['deleted_count'] += 1
                    else:
                        self.rows[key] = operation._doc.raw
                        counts['matched_count'] += 1
        return SimpleNamespace(**counts)


class Database:
    def __init__(self, collections, name='v11_disposable_test'):
        self.name = name
        self.collections = {name: Collection(rows) for name, rows in collections.items()}

    def list_collections(self):
        return [{'name': name, 'type': 'collection', 'options': {}} for name in self.collections]

    def __getitem__(self, name):
        return self.collections[name]

    def get_collection(self, name, **kwargs):
        return self[name]


def fake_documents(db, name):
    return iter(db[name].rows.values()) if name in db.collections else iter(())


class DeltaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.patch = patch.object(delta, 'documents', fake_documents)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        rich = dict(_id=ObjectId('000000000000000000000001'), integer=Int64(7),
                    decimal=Decimal128('1.200'), binary=Binary(b'\x00\xff', 128),
                    timestamp=Timestamp(7, 3), extreme_date=DatetimeMS(2**62), date=datetime(2020, 1, 1, tzinfo=timezone.utc),
                    regex=Regex('a', 'im'), nested={'z': [None, True, 4.2, {'x': 'a'}]})
        self.source_rows = [BSON.encode(rich), BSON.encode({'_id': 'replace', 'a': 1}),
                            BSON.encode({'_id': 'delete', 'value': 1})]
        self.target_rows = [BSON.encode(rich), BSON.encode({'_id': 'replace', 'a': Int64(1)}),
                            BSON.encode({'_id': 'insert', 'value': [1, 2]})]
        self.source = Database({'a': self.source_rows, 'empty': []})
        self.target = Database({'a': self.target_rows, 'empty': []})

    def generate(self):
        package = self.work / 'delta.tar.gz'
        report = delta.generate(self.source, self.target, package, self.work,
                                expected_collections=2, min_overlap=0.5, memory_bytes=1024)
        return package, report

    def test_end_to_end_exact_bson_metadata_counts_and_journal(self):
        original = copy.deepcopy(self.source['a'].rows)
        package, report = self.generate()
        self.assertEqual(original, self.source['a'].rows)
        self.assertEqual(report['source_documents'], 3)
        self.assertEqual(report['target_documents'], 3)
        with delta.unpack(package, self.work) as (manifest, operations):
            stats = manifest['collections']['a']
            self.assertEqual([stats[k] for k in ('insert', 'replace', 'delete', 'unchanged')], [1, 1, 1, 1])
            self.assertEqual(stats['shared_ids'], 2)
        state = delta.apply_package(self.source, package, self.work / 'journal.json', self.work, memory_bytes=1024)
        self.assertEqual(state['status'], 'applied_checkpoint_verified')
        self.assertEqual(self.source['a'].rows, self.target['a'].rows)
        self.assertTrue(delta.verify(self.source, self.target, self.work, 1024)['equal'])
        self.assertGreater(self.source['a'].calls, 0)

    def test_application_metrics_separate_scans_and_actual_bulk_calls(self):
        package, _ = self.generate()
        from itertools import count
        journal = self.work / 'metrics.json'
        # Each timer sample advances one second; no database or wall-clock sleeps.
        with patch.object(delta.time, 'monotonic', side_effect=count()):
            state = delta.apply_package(self.source, package, journal, self.work,
                                        memory_bytes=1024, batch_bytes=1)
        metrics = state['metrics']
        self.assertGreater(metrics['package_extraction_validation_seconds'], 0)
        for phase in ('starting_checkpoint', 'final_checkpoint'):
            scan = metrics[phase]
            self.assertEqual(scan['documents_scanned'], 3)
            self.assertEqual(scan['collections']['a']['documents_scanned'], 3)
            self.assertEqual(scan['collections']['empty']['documents_scanned'], 0)
            self.assertGreater(scan['collections']['empty']['seconds'], 0)
            self.assertGreaterEqual(scan['seconds'], sum(
                row['seconds'] for row in scan['collections'].values()))
        writes = metrics['bulk_writes']
        self.assertEqual(writes['batches'], 3)
        self.assertEqual(writes['seconds'], 3)
        self.assertEqual(writes['completed_operations'], 3)
        self.assertEqual(writes['operations_per_second'], 1)
        self.assertEqual(writes['collections']['a']['batches'], 3)
        self.assertEqual(writes['collections']['empty']['batches'], 0)
        self.assertEqual(writes['collections']['empty']['operations_per_second'], 0)
        self.assertGreater(state['application_seconds'], sum((
            metrics['package_extraction_validation_seconds'],
            metrics['starting_checkpoint']['seconds'], writes['seconds'],
            metrics['final_checkpoint']['seconds'])))
        self.assertEqual(json.loads(journal.read_text()), state)

    def test_full_comparison_detects_type_field_order_array_and_id_changes(self):
        variants = [({'_id': 1, 'x': 1}, {'_id': 1, 'x': Int64(1)}),
                    ({'_id': 1, 'x': 1, 'y': 2}, {'_id': 1, 'y': 2, 'x': 1}),
                    ({'_id': 1, 'x': [1, 2]}, {'_id': 1, 'x': [2, 1]}),
                    ({'_id': 1}, {'_id': 2})]
        for a, b in variants:
            result = delta.verify(Database({'a': [BSON.encode(a)]}), Database({'a': [BSON.encode(b)]}), self.work, 1024)
            self.assertFalse(result['equal'])
            self.assertTrue(result['collections']['a']['examples'])

    def test_external_sort_multiple_passes_and_all_identity_types(self):
        ids = [ObjectId(), 's', 2, Int64(3), None, False, Decimal128('3.14'),
               Binary(b'abc', 128), datetime(2020, 1, 1), Timestamp(2, 1), {'nested': 'id'}, DatetimeMS(2**62)]
        rows = [BSON.encode({'_id': identity, 'padding': 'x' * 600}) for identity in ids]
        actual = list(delta.sorted_documents(iter(rows[::-1]), self.work, 1024, fan_in=2))
        self.assertEqual(actual, sorted((delta.id_key(row), row) for row in rows))
        self.assertEqual(list(self.work.iterdir()), [])

    def test_all_64_collections_reconstruct(self):
        self.source = Database({f'collection_{n:02}': [BSON.encode({'_id': 1, 'value': n})] for n in range(64)})
        self.target = Database({f'collection_{n:02}': [BSON.encode({'_id': 1, 'value': n + 1}),
                                BSON.encode({'_id': 2, 'value': n})] for n in range(64)})
        package = self.work / 'all64.tar.gz'
        report = delta.generate(self.source, self.target, package, self.work, memory_bytes=1024)
        self.assertEqual((report['source_documents'], report['target_documents']), (64, 128))
        delta.apply_package(self.source, package, self.work / 'journal', self.work, 1024)
        report = delta.verify(self.source, self.target, self.work, 1024)
        self.assertTrue(report['equal'])
        self.assertEqual(len(report['collections']), 64)

    def test_duplicate_ids_and_truncated_records_rejected(self):
        raw = BSON.encode({'_id': 1})
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            list(delta.sorted_documents([raw, raw], self.work, 1024))
        for content in [b'\x05', b'\x08\x00\x00\x00abc', b'\xff\xff\xff\xff']:
            with self.assertRaises(ValueError):
                list(delta.frames(io.BytesIO(content)))

    def test_identity_discontinuity_fails_without_package(self):
        self.target = Database({'a': [BSON.encode({'_id': 'all-new'})], 'empty': []})
        with self.assertRaisesRegex(ValueError, 'Identity continuity'):
            self.generate()
        self.assertFalse((self.work / 'delta.tar.gz').exists())

    def test_wrong_checkpoint_and_protected_name_never_write(self):
        package, _ = self.generate()
        self.source['a'].rows.clear()
        with self.assertRaisesRegex(ValueError, 'checkpoint mismatch'):
            delta.apply_package(self.source, package, self.work / 'j', self.work, 1024)
        self.assertEqual(self.source['a'].calls, 0)
        self.assertFalse((self.work / 'j').exists())
        self.source.name = delta.ORIGINAL_DB
        with self.assertRaisesRegex(ValueError, 'separate database'):
            delta.apply_package(self.source, package, self.work / 'j', self.work)

    def test_interruption_is_durable_and_retry_refused(self):
        package, _ = self.generate()
        self.source['a'].fail = True
        journal = self.work / 'journal.json'
        with self.assertRaisesRegex(RuntimeError, 'interruption'):
            delta.apply_package(self.source, package, journal, self.work, 1024)
        self.assertEqual(json.loads(journal.read_text())['status'], 'failed_restore_required')
        self.source['a'].fail = False
        with self.assertRaises(FileExistsError):
            delta.apply_package(self.source, package, journal, self.work, 1024)
        self.assertEqual(self.source['a'].calls, 1)

    def test_metadata_change_rejected_before_writes(self):
        self.target.collections['extra'] = Collection([])
        with self.assertRaisesRegex(ValueError, 'metadata/index'):
            self.generate()

    def test_tampered_payload_and_unsafe_member_rejected(self):
        package, _ = self.generate()
        with delta.unpack(package, self.work) as (manifest, operations):
            payload = operations.read_bytes()
        import tarfile
        for unsafe in (False, True):
            bad = self.work / f'bad-{unsafe}.tar.gz'
            with tarfile.open(bad, 'w:gz') as archive:
                for name, content in [('manifest.json', json.dumps(manifest).encode()),
                                      ('../operations.bson' if unsafe else 'operations.bson', payload[:-1])]:
                    info = tarfile.TarInfo(name)
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))
            with self.assertRaises(ValueError):
                with delta.unpack(bad, self.work):
                    self.fail('Must reject corruption')

    def test_operation_validation_rejects_mismatched_identity_and_counts(self):
        package, _ = self.generate()
        with delta.unpack(package, self.work) as (manifest, operations):
            manifest['collections']['a']['insert'] += 1
            with self.assertRaisesRegex(ValueError, 'count mismatch'):
                delta.validate_operations(operations, manifest)
            operation = {'collection': 'a', 'op': 'replace', 'key': Binary(BSON.encode({'_id': 1})),
                         'document': Binary(BSON.encode({'_id': 2}))}
            operations.write_bytes(BSON.encode(operation))
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                delta.validate_operations(operations, manifest)

    def test_readonly_connections_fail_closed_without_effective_read_privileges(self):
        from unittest.mock import MagicMock
        statuses = [
            {'authenticatedUsers': [], 'authenticatedUserRoles': [], 'authenticatedUserPrivileges': []},
            {'authenticatedUsers': [{'user': 'writer'}], 'authenticatedUserRoles': [{'role': 'readWrite'}],
             'authenticatedUserPrivileges': [{'actions': ['find', 'insert']}]},
            {'authenticatedUsers': [{'user': 'empty'}], 'authenticatedUserRoles': [{'role': 'read'}],
             'authenticatedUserPrivileges': []},
        ]
        for status in statuses:
            client = MagicMock()
            client.admin.command.return_value = {'authInfo': status}
            with patch.object(delta, 'MongoClient', return_value=client):
                with self.assertRaisesRegex(ValueError, 'read-only'):
                    delta.readonly_database('synthetic://unused', 'db')
            client.close.assert_called_once()
        client = MagicMock()
        client.admin.command.return_value = {'authInfo': {
            'authenticatedUsers': [{'user': 'reader'}], 'authenticatedUserRoles': [{'role': 'read'}],
            'authenticatedUserPrivileges': [{'actions': ['find', 'listCollections', 'listIndexes', 'dbHash']}]}}
        with patch.object(delta, 'MongoClient', return_value=client):
            connected, db = delta.readonly_database('synthetic://unused', 'db')
        self.assertIs(connected, client)
        client.get_database.assert_called_once()

    def test_partial_application_never_claims_success(self):
        package, _ = self.generate()
        collection = self.source['a']
        original_bulk = collection.bulk_write
        calls = 0
        def interrupt(operations, ordered):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt('synthetic hard interruption')
            return original_bulk(operations, ordered)
        collection.bulk_write = interrupt
        journal = self.work / 'partial.json'
        with self.assertRaises(KeyboardInterrupt):
            delta.apply_package(self.source, package, journal, self.work, 1024, batch_bytes=1)
        state = json.loads(journal.read_text())
        self.assertEqual(state['status'], 'failed_restore_required')
        self.assertEqual(state['completed_operations'], 1)
        self.assertEqual(state['metrics']['bulk_writes']['batches'], 2)
        self.assertEqual(state['metrics']['bulk_writes']['completed_operations'], 1)
        self.assertEqual(state['metrics']['final_checkpoint'], {})
        self.assertGreaterEqual(state['application_seconds'], state['metrics']['bulk_writes']['seconds'])
        self.assertFalse(delta.verify(self.source, self.target, self.work, 1024)['equal'])
        with self.assertRaises(FileExistsError):
            delta.apply_package(self.source, package, journal, self.work, 1024)

    def test_empty_databases_and_collection_count_guard(self):
        self.source = Database({'empty': []})
        self.target = Database({'empty': []})
        with self.assertRaisesRegex(ValueError, 'Expected 2'):
            self.generate()
        package = self.work / 'empty.tar.gz'
        delta.generate(self.source, self.target, package, self.work, expected_collections=1)
        state = delta.apply_package(self.source, package, self.work / 'j', self.work)
        self.assertEqual(state['metrics']['bulk_writes']['batches'], 0)
        self.assertEqual(state['metrics']['bulk_writes']['operations_per_second'], 0)
        self.assertTrue(delta.verify(self.source, self.target, self.work)['equal'])


if __name__ == '__main__':
    unittest.main()
