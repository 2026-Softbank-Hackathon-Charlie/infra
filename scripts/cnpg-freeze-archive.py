#!/usr/bin/env python3
"""Freeze a completed PoC backup and WALs as of t0; never run AWS by default.

Requires separately approved S3 reads/writes and an AWS CLI supporting conditional
CopyObject/PutObject. No database data or backup.info content is printed. A
successful freeze is a transport snapshot, not proof of successful PostgreSQL
recovery. The root runbook performs the latter with fresh PVCs.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'source/postgres-poc/'
MAX_COPY_SIZE = 5 * 1024 ** 3
MAX_INFO_SIZE = 1024 ** 2
RUN_ID = re.compile(r'^[a-z0-9][a-z0-9-]{0,19}$')
WAL = re.compile(r'^([0-9A-F]{24})(?:\.(?:gz|bz2|xz|zst|lz4|snappy))?$')


class FreezeError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise FreezeError(message)


def timestamp(value):
    require(isinstance(value, str), 'Invalid timestamp type.')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise FreezeError('Invalid timestamp; use an explicit UTC offset.') from None
    require(result.tzinfo is not None, 'Timestamp must include a UTC offset.')
    return result.astimezone(timezone.utc)


def stamp(value):
    return value.isoformat().replace('+00:00', 'Z')


class Aws:
    def __init__(self, profile):
        self.base = ['aws', '--profile', profile, '--region', 'ap-northeast-2',
                     '--no-cli-pager', '--output', 'json']

    def call(self, operation, *args):
        try:
            result = subprocess.run([*self.base, 's3api', operation, *args],
                                    capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            raise FreezeError(f'AWS {operation} failed; no response content logged.') from None
        require(result.returncode == 0,
                f'AWS {operation} failed; do not reuse this run-id if writes began.')
        try:
            response = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise FreezeError(f'AWS {operation} returned invalid JSON.') from None
        require(isinstance(response, dict), f'AWS {operation} returned an unexpected response.')
        return response


def objects(aws, bucket, prefix):
    """Explicit pagination avoids silently freezing just the first S3 page."""
    result, token, tokens = [], None, set()
    while True:
        args = ['--bucket', bucket, '--prefix', prefix, '--no-paginate']
        if token:
            args += ['--continuation-token', token]
        page = aws.call('list-objects-v2', *args)
        contents = page.get('Contents', [])
        require(isinstance(contents, list), 'Invalid S3 object list.')
        result.extend(contents)
        if not page.get('IsTruncated', False):
            break
        token = page.get('NextContinuationToken')
        require(isinstance(token, str) and token and token not in tokens,
                'Invalid or repeated S3 continuation token.')
        tokens.add(token)
    keys = [item.get('Key') for item in result]
    require(all(isinstance(k, str) and k.startswith(prefix)
                and not any(p in ('', '.', '..') for p in k.split('/'))
                and not any(ord(c) < 32 or ord(c) == 127 for c in k) for k in keys),
            'S3 returned an object outside the exact prefix or with an unsafe key.')
    require(len(keys) == len(set(keys)), 'Duplicate S3 key in the captured list.')
    return result


def object_info(item):
    require(isinstance(item.get('Size'), int) and item['Size'] >= 0,
            'Object has an invalid size.')
    require(isinstance(item.get('ETag'), str) and item['ETag'], 'Missing source ETag.')
    return {'key': item['Key'], 'size': item['Size'], 'etag': item['ETag'],
            'lastModified': stamp(timestamp(item.get('LastModified')))}


def verify_source(aws, bucket, item):
    head = aws.call('head-object', '--bucket', bucket, '--key', item['key'])
    require(head.get('ETag') == item['etag'] and head.get('ContentLength') == item['size']
            and timestamp(head.get('LastModified')) == timestamp(item['lastModified']),
            'Source object changed after the t0 inventory; snapshot is unusable.')


def backup_info(aws, bucket, item, directory):
    require(item['size'] <= MAX_INFO_SIZE, 'Unexpectedly large backup.info; refusing to read it.')
    verify_source(aws, bucket, item)
    path = directory / 'backup.info'
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        aws.call('get-object', '--bucket', bucket, '--key', item['key'],
                 '--if-match', item['etag'], str(path))
        require(path.stat().st_size <= MAX_INFO_SIZE, 'Unexpected backup.info size.')
        parsed = {}
        for line in path.read_text().splitlines():
            if not line.strip() or line.startswith('#'):
                continue
            key, separator, value = line.partition('=')
            require(separator and re.fullmatch(r'[a-z][a-z0-9_]*', key)
                    and key not in parsed, 'Malformed backup.info; contents not logged.')
            parsed[key] = value.strip()
        verify_source(aws, bucket, item)
        return parsed
    finally:
        path.unlink(missing_ok=True)


def select_snapshot(aws, bucket, cutoff, directory):
    inventory = [object_info(item) for item in objects(aws, bucket, SOURCE)]
    eligible = [i for i in inventory if timestamp(i['lastModified']) <= cutoff]
    candidates = []
    for item in eligible:
        match = re.fullmatch(re.escape(SOURCE) + r'base/([0-9]{8}T[0-9]{6})/backup\.info', item['key'])
        if not match:
            continue
        info = backup_info(aws, bucket, item, directory)
        if info.get('status') != 'DONE':
            continue
        end = timestamp(info.get('end_time'))
        if end <= cutoff:
            require(info.get('parent_backup_id', 'None') in ('', 'None', 'null'),
                    'Incremental backup chains are outside this full-backup PoC.')
            candidates.append((end, match[1], info))
    require(candidates, 'No completed full base backup was archived by t0.')
    end, backup_id, info = max(candidates, key=lambda value: (value[0], value[1]))
    prefix = SOURCE + f'base/{backup_id}/'
    base = [i for i in eligible if i['key'].startswith(prefix)]
    require(not any(i['key'].startswith(prefix) and timestamp(i['lastModified']) > cutoff
                    for i in inventory), 'Base backup has post-t0 objects; snapshot is unusable.')
    require(any(re.fullmatch(r'data(?:_[0-9]+)?\.tar(?:\.gz)?', i['key'][len(prefix):])
                and i['size'] > 0 for i in base), 'Completed backup is missing its base data archive.')
    begin_wal, end_wal = info.get('begin_wal', ''), info.get('end_wal', '')
    require(re.fullmatch(r'[0-9A-F]{24}', begin_wal) and re.fullmatch(r'[0-9A-F]{24}', end_wal)
            and begin_wal[:8] == end_wal[:8], 'Missing or unsupported backup WAL range.')
    try:
        segment_size = int(info.get('xlog_segment_size', '16777216'))
    except ValueError:
        raise FreezeError('Invalid backup WAL segment size.') from None
    require(1024 ** 2 <= segment_size <= 1024 ** 3 and segment_size & (segment_size - 1) == 0,
            'Unsupported WAL segment size.')
    segments_per_log = 2 ** 32 // segment_size

    def position(name):
        segment = int(name[16:], 16)
        require(segment < segments_per_log, 'Invalid WAL segment index.')
        return int(name[8:16], 16) * segments_per_log + segment

    begin, stop = position(begin_wal), position(end_wal)
    require(begin <= stop, 'Invalid WAL ordering.')
    wals, positions, names = [], [], set()
    for item in eligible:
        if not item['key'].startswith(SOURCE + 'wals/'):
            continue
        name = item['key'].rsplit('/', 1)[-1]
        match = WAL.fullmatch(name)
        if match:
            wal = match[1]
            require(wal[:8] == begin_wal[:8], 'Multiple WAL timelines require a separate recovery review.')
            pos = position(wal)
            if pos >= begin:
                require(wal not in names, 'Ambiguous duplicate compressed/uncompressed WAL.')
                names.add(wal)
                positions.append(pos)
                wals.append(item)
        elif re.fullmatch(r'[0-9A-F]{8}\.history(?:\.gz)?', name):
            wals.append(item)
    require(positions and min(positions) == begin and max(positions) >= stop,
            'A required base-backup WAL segment is missing at t0.')
    require(len(positions) == max(positions) - begin + 1,
            'WAL archive has a gap; snapshot is unusable.')
    return sorted(base + wals, key=lambda item: item['key']), {
        'backupId': backup_id, 'backupEndTime': stamp(end),
        'beginWal': begin_wal, 'endWal': end_wal,
        'latestArchivedWal': max(names),
    }


def write_document(stream, document):
    stream.seek(0)
    json.dump(document, stream, indent=2, sort_keys=True)
    stream.write('\n')
    stream.truncate()
    stream.flush()
    os.fsync(stream.fileno())


def put_status(aws, bucket, key, document, directory, etag=None):
    path = directory / 'freeze-status.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(document, stream, sort_keys=True)
    condition = ['--if-match', etag] if etag else ['--if-none-match', '*']
    response = aws.call('put-object', '--bucket', bucket, '--key', key,
                        '--body', str(path), '--content-type', 'application/json',
                        '--server-side-encryption', 'AES256', *condition)
    require(isinstance(response.get('ETag'), str) and response['ETag'], 'Missing snapshot status ETag.')
    return response['ETag']


def freeze(args):
    require(args.execute_approved, 'No AWS calls made. Obtain execution approval and pass --execute-approved.')
    require(re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', args.bucket), 'Invalid S3 bucket name.')
    require(RUN_ID.fullmatch(args.run_id), 'Invalid run-id; use 1-20 lowercase letters, numbers or hyphens.')
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@+-]*', args.profile), 'Invalid AWS profile name.')
    require(re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})', args.t0),
            't0 must be an RFC3339 timestamp with an explicit UTC offset.')
    cutoff = timestamp(args.t0)
    require(cutoff <= datetime.now(timezone.utc), 't0 cannot be in the future.')
    output = Path(args.manifest) if args.manifest else ROOT / '.generated/cnpg-freeze' / f'{args.run_id}.json'
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(not output.exists() and not output.is_symlink(), 'Manifest already exists; use a new run-id/path.')
    fd = os.open(output, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    aws = Aws(args.profile)
    prefix = f'restore/{args.run_id}/'
    state = {'state': 'incomplete', 'restoreValidated': False, 'bucket': args.bucket,
             'runId': args.run_id, 't0': stamp(cutoff), 'sourcePrefix': SOURCE,
             'inputPrefix': prefix + 'input/', 'outputPrefix': prefix + 'output/', 'objects': []}
    with os.fdopen(fd, 'w+') as manifest, tempfile.TemporaryDirectory(prefix='cnpg-freeze-') as temporary:
        directory = Path(temporary)
        directory.chmod(0o700)
        write_document(manifest, state)
        try:
            require(not objects(aws, args.bucket, prefix), 'Restore run prefix is not empty; never reuse it.')
            selected, backup = select_snapshot(aws, args.bucket, cutoff, directory)
            require(all(i['size'] <= MAX_COPY_SIZE for i in selected),
                    'Selected object exceeds the 5GiB single-CopyObject PoC limit.')
            state.update(backup)
            state['capturedObjects'] = len(selected)
            state['capturedBytes'] = sum(i['size'] for i in selected)
            write_document(manifest, state)
            status_key = prefix + 'freeze-status.json'
            status_etag = put_status(aws, args.bucket, status_key, state, directory)
            require([i['Key'] for i in objects(aws, args.bucket, prefix)] == [status_key],
                    'Concurrent writes found in restore run prefix; snapshot is unusable.')
            for item in selected:
                verify_source(aws, args.bucket, item)
                destination = prefix + 'input/' + item['key'][len('source/'):]
                response = aws.call('copy-object', '--bucket', args.bucket, '--key', destination,
                                    '--copy-source', quote(args.bucket + '/' + item['key'], safe='/'),
                                    '--copy-source-if-match', item['etag'], '--if-none-match', '*',
                                    '--server-side-encryption', 'AES256')
                copied_etag = response.get('CopyObjectResult', {}).get('ETag')
                require(isinstance(copied_etag, str) and copied_etag, 'Missing copy result ETag.')
                verify_source(aws, args.bucket, item)
                head = aws.call('head-object', '--bucket', args.bucket, '--key', destination)
                require(head.get('ETag') == copied_etag and head.get('ContentLength') == item['size'],
                        'Snapshot destination changed or is incomplete.')
                state['objects'].append(dict(item, destinationKey=destination, destinationETag=copied_etag))
                write_document(manifest, state)
            # Detect destination additions/deletions and ETag changes at the end.
            actual = {i['Key']: i for i in objects(aws, args.bucket, prefix)}
            require(set(actual) == {status_key, *(i['destinationKey'] for i in state['objects'])},
                    'Unexpected object set in restore run prefix.')
            for item in state['objects']:
                require(actual[item['destinationKey']].get('ETag') == item['destinationETag']
                        and actual[item['destinationKey']].get('Size') == item['size'],
                        'Snapshot destination inventory changed.')
            state['state'] = 'complete'
            put_status(aws, args.bucket, status_key, state, directory, status_etag)
            write_document(manifest, state)
        except (FreezeError, OSError, ValueError, KeyError, TypeError):
            state['state'] = 'incomplete'
            write_document(manifest, state)
            raise FreezeError('Freeze failed. Any written prefix is unusable; no cleanup or reuse is automatic.') from None
    return state, output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--t0', required=True, help='RFC3339 cutoff captured at the simulated failure')
    parser.add_argument('--profile', required=True, help='Existing approved restore/operator AWS profile')
    parser.add_argument('--manifest', help='New local metadata-only manifest path, created with mode 0600')
    parser.add_argument('--execute-approved', action='store_true', help='Assert separately approved S3 read/copy scope')
    args = parser.parse_args(argv)
    try:
        state, path = freeze(args)
    except (FreezeError, OSError) as error:
        # FreezeError messages are deliberately constant and contain no CLI/raw info.
        print(str(error) if isinstance(error, FreezeError) else 'Local manifest operation failed.', file=sys.stderr)
        return 1
    print(f"Frozen {len(state['objects'])} objects. Metadata manifest: {path}")
    print('Recovery/RPO is not verified until the fresh restore trial passes.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
