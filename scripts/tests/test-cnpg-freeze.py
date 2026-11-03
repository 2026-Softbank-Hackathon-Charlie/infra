#!/usr/bin/env python3
"""Archive cutoff/copy failure contracts against a fake subprocess; no AWS."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('cnpg_freeze', ROOT / 'scripts/cnpg-freeze-archive.py')
freeze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(freeze)

BUCKET = 'charlie-dev-cnpg-111122223333-ap-northeast-2'
T0 = '2020-01-01T00:10:00Z'
BASE = 'source/postgres-poc/base/20200101T000000/'
WALROOT = 'source/postgres-poc/wals/0000000100000000/'


class FakeS3:
    def __init__(self):
        self.calls = []
        self.store = {}
        self.copy_count = 0
        self.fail_copy_at = None
        self.mutate_source = False
        self.corrupt_destination = False
        self.paginate = False
        self.outside_prefix = False
        self.add(BASE + 'backup.info', b'status=DONE\nend_time=2020-01-01 00:02:00+00:00\nbegin_wal=000000010000000000000001\nend_wal=000000010000000000000002\nxlog_segment_size=16777216\nparent_backup_id=None\n')
        self.add(BASE + 'data.tar.gz', b'fake-base-data')
        for number in (1, 2, 3):
            self.add(WALROOT + f'0000000100000000{number:08X}.gz', b'fake-wal')
        self.add(WALROOT + '000000010000000000000004.gz', b'post-cutoff-wal', modified='2020-01-01T00:10:01Z')

    def add(self, key, body, modified='2020-01-01T00:03:00Z', etag=None, size=None):
        self.store[key] = {'body': body, 'LastModified': modified,
                           'ETag': etag or '"' + hashlib.md5(body).hexdigest() + '"',
                           'Size': len(body) if size is None else size}

    def listing(self, key):
        return dict(Key=key, **{k: self.store[key][k] for k in ('LastModified', 'ETag', 'Size')})

    def run(self, args, **kwargs):
        self.calls.append(list(args))
        assert args[args.index('--profile') + 1] == 'approved-restore'
        assert args[args.index('--region') + 1] == 'ap-northeast-2'
        assert kwargs['capture_output'] and kwargs['text'] and kwargs['timeout'] == 120
        operation = args[args.index('s3api') + 1]

        def option(name):
            return args[args.index(name) + 1]

        def result(value, returncode=0, stderr=''):
            return subprocess.CompletedProcess(args, returncode, json.dumps(value), stderr)

        if operation == 'list-objects-v2':
            prefix = option('--prefix')
            keys = sorted(k for k in self.store if k.startswith(prefix))
            if self.outside_prefix and prefix == freeze.SOURCE:
                value = self.listing(keys[0])
                value['Key'] = 'source/postgres-poc-other/base/backup.info'
                return result({'Contents': [value]})
            start = int(option('--continuation-token')) if '--continuation-token' in args else 0
            count = 2 if self.paginate else len(keys)
            page = keys[start:start + count]
            value = {'Contents': [self.listing(k) for k in page], 'IsTruncated': start + count < len(keys)}
            if value['IsTruncated']:
                value['NextContinuationToken'] = str(start + count)
            return result(value)
        key = option('--key')
        if operation == 'head-object':
            item = self.store[key]
            return result({'ETag': item['ETag'], 'ContentLength': item['Size'], 'LastModified': item['LastModified']})
        if operation == 'get-object':
            assert option('--if-match') == self.store[key]['ETag']
            target = Path(args[-1])
            assert target.stat().st_mode & 0o777 == 0o600
            target.write_bytes(self.store[key]['body'])
            return result({'ETag': self.store[key]['ETag']})
        if operation == 'put-object':
            if '--if-none-match' in args:
                assert option('--if-none-match') == '*' and key not in self.store
            else:
                assert option('--if-match') == self.store[key]['ETag']
            path = Path(option('--body'))
            assert path.stat().st_mode & 0o777 == 0o600
            self.add(key, path.read_bytes(), modified='2020-01-01T00:11:00Z')
            return result({'ETag': self.store[key]['ETag']})
        assert operation == 'copy-object'
        self.copy_count += 1
        if self.fail_copy_at == self.copy_count:
            return result({}, returncode=7, stderr='FAKE_SECRET_MUST_NOT_BE_LOGGED=private-backup-info')
        assert key.startswith('restore/trial-001/input/postgres-poc/') and key not in self.store
        assert option('--if-none-match') == '*'
        source = unquote(option('--copy-source')).removeprefix(BUCKET + '/')
        item = self.store[source]
        assert option('--copy-source-if-match') == item['ETag']
        copied_etag = '"copy-' + item['ETag'].strip('"') + '"'
        self.add(key, item['body'], modified='2020-01-01T00:11:00Z', etag=copied_etag, size=item['Size'])
        if self.mutate_source:
            self.store[source]['ETag'] = '"changed-source"'
        if self.corrupt_destination:
            self.store[key]['Size'] += 1
        return result({'CopyObjectResult': {'ETag': copied_etag}})


class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manifest = Path(self.temp.name) / 'snapshot.json'
        self.fake = FakeS3()
        self.runner = patch.object(freeze.subprocess, 'run', side_effect=self.fake.run).start()
        self.addCleanup(patch.stopall)
        self.args = ['--bucket', BUCKET, '--run-id', 'trial-001', '--t0', T0,
                     '--profile', 'approved-restore', '--manifest', str(self.manifest)]

    def invoke(self, approved=True, expected=0, extra=()):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = freeze.main([*self.args, *(('--execute-approved',) if approved else ()), *extra])
        self.assertEqual(code, expected, stdout.getvalue() + stderr.getvalue())
        self.assertNotIn('FAKE_SECRET_MUST_NOT_BE_LOGGED', stdout.getvalue() + stderr.getvalue())
        self.assertNotIn('private-backup-info', stdout.getvalue() + stderr.getvalue())
        return stdout.getvalue(), stderr.getvalue()

    def document(self):
        return json.loads(self.manifest.read_text())

    def test_no_execution_flag_makes_no_aws_calls_or_files(self):
        self.invoke(approved=False, expected=1)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(self.manifest.exists())

    def test_success_cutoff_etags_copy_guards_and_private_manifest(self):
        self.invoke()
        doc = self.document()
        self.assertEqual(doc['state'], 'complete')
        self.assertFalse(doc['restoreValidated'])
        self.assertEqual(doc['capturedObjects'], 5)
        self.assertEqual(doc['latestArchivedWal'], '000000010000000000000003')
        self.assertEqual(doc['backupEndTime'], '2020-01-01T00:02:00Z')
        self.assertEqual(self.manifest.stat().st_mode & 0o777, 0o600)
        self.assertFalse(any('000000010000000000000004' in i['key'] for i in doc['objects']))
        self.assertTrue(all(i['etag'] != i['destinationETag'] for i in doc['objects']))
        self.assertTrue(all(i['destinationKey'].startswith('restore/trial-001/input/postgres-poc/') for i in doc['objects']))
        marker = json.loads(self.fake.store['restore/trial-001/freeze-status.json']['body'])
        self.assertEqual(marker['state'], 'complete')
        writes = [c for c in self.fake.calls if c[c.index('s3api') + 1] in ('copy-object', 'put-object')]
        self.assertTrue(all(c[c.index('--key') + 1].startswith('restore/trial-001/') for c in writes))

    def test_explicit_pagination_freezes_complete_inventory(self):
        self.fake.paginate = True
        self.invoke()
        self.assertEqual(len(self.document()['objects']), 5)
        self.assertTrue(any('--continuation-token' in c for c in self.fake.calls))

    def test_nonempty_target_is_never_overwritten(self):
        self.fake.add('restore/trial-001/existing', b'existing')
        self.invoke(expected=1)
        self.assertEqual(self.document()['state'], 'incomplete')
        self.assertEqual(self.fake.copy_count, 0)
        self.assertEqual(self.fake.store['restore/trial-001/existing']['body'], b'existing')

    def test_copy_failure_retains_unusable_marker_no_cleanup_or_secret_output(self):
        self.fake.fail_copy_at = 2
        self.invoke(expected=1)
        self.assertEqual(self.document()['state'], 'incomplete')
        marker = json.loads(self.fake.store['restore/trial-001/freeze-status.json']['body'])
        self.assertEqual(marker['state'], 'incomplete')
        self.assertTrue(any(k.startswith('restore/trial-001/input/') for k in self.fake.store))
        self.assertFalse(any(c[c.index('s3api') + 1].startswith('delete') for c in self.fake.calls))

    def test_source_mutation_after_copy_fails(self):
        self.fake.mutate_source = True
        self.invoke(expected=1)
        self.assertEqual(self.document()['state'], 'incomplete')

    def test_destination_size_change_fails(self):
        self.fake.corrupt_destination = True
        self.invoke(expected=1)
        self.assertEqual(self.document()['state'], 'incomplete')

    def test_incomplete_backup_is_not_selected(self):
        self.fake.add(BASE + 'backup.info', b'status=STARTED\n')
        self.invoke(expected=1)
        self.assertEqual(self.fake.copy_count, 0)

    def test_missing_wal_fails_before_copy(self):
        del self.fake.store[WALROOT + '000000010000000000000002.gz']
        self.invoke(expected=1)
        self.assertEqual(self.fake.copy_count, 0)

    def test_missing_base_data_fails_before_copy(self):
        del self.fake.store[BASE + 'data.tar.gz']
        self.invoke(expected=1)
        self.assertEqual(self.fake.copy_count, 0)

    def test_large_selected_object_requires_separate_copy_design(self):
        self.fake.store[BASE + 'data.tar.gz']['Size'] = freeze.MAX_COPY_SIZE + 1
        self.invoke(expected=1)
        self.assertEqual(self.fake.copy_count, 0)

    def test_outside_prefix_response_fails_closed(self):
        self.fake.outside_prefix = True
        self.invoke(expected=1)
        self.assertEqual(self.fake.copy_count, 0)

    def test_invalid_cutoff_or_traversal_rejected_without_aws(self):
        for extra in (('--run-id', '../source'), ('--t0', '2020-01-01T00:10:00'),
                      ('--t0', '2999-01-01T00:00:00Z')):
            with self.subTest(extra=extra):
                self.invoke(expected=1, extra=extra)
                self.assertEqual(self.fake.calls, [])

    def test_existing_manifest_is_not_replaced(self):
        self.manifest.write_text('existing')
        self.invoke(expected=1)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.manifest.read_text(), 'existing')


if __name__ == '__main__':
    unittest.main()
