#!/usr/bin/env python3
"""Run pinned Alloy against synthetic local Kubernetes/files/OTLP only; no cluster access."""
import collections
import gzip
import http.server
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
BINARY = os.environ.get('ALLOY_BINARY', '')


def eventually(check, label, timeout=25):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        time.sleep(.1)
    raise AssertionError('Timed out: ' + label)


def main():
    if not BINARY:
        raise SystemExit('Set ALLOY_BINARY to verified Alloy v1.21.3')
    version = subprocess.check_output([BINARY, '--version'], text=True)
    assert 'v1.21.3' in version
    received, events, attempts, native_ids = [], [], [], []
    released, rejecting, stopping = threading.Event(), threading.Event(), threading.Event()
    uid = 'aaaaaaaa-1111-2222-3333-444444444444'
    ns, name = 'never-registered-application', 'finished-job'

    def pod(owned=True):
        return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {
            'name': name if owned else 'not-owned', 'namespace': ns, 'uid': uid if owned else 'unowned-uid',
            'resourceVersion': '2', 'labels': {'observability.hopcharlie.dev/enabled': 'true' if owned else 'false',
                'observability.hopcharlie.dev/kind': 'job', 'hopcharlie.dev/app': ns, 'hopcharlie.dev/service': 'worker'}},
            'spec': {'nodeName': 'proof-node', 'containers': [{'name': 'worker', 'image': 'synthetic:fixture',
                'ports': [{'name': 'http', 'containerPort': 8080}, {'name': 'metrics', 'containerPort': 9090}]}]},
            'status': {'phase': 'Succeeded', 'podIP': '192.0.2.1', 'containerStatuses': [{'name': 'worker',
                'ready': False, 'containerID': 'containerd://synthetic', 'restartCount': 0,
                'image': 'synthetic:fixture', 'imageID': 'synthetic', 'state': {'terminated': {'exitCode': 0}}}]}}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == '/api/v1/pods':
                if urllib.parse.parse_qs(parsed.query).get('watch') in (['true'], ['1']):
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    sent = 0
                    # client-go streaming lists require an initial-events-end bookmark.
                    bookmark = {'type': 'BOOKMARK', 'object': {'apiVersion': 'v1', 'kind': 'Pod',
                        'metadata': {'resourceVersion': '1', 'annotations': {'k8s.io/initial-events-end': 'true'}}}}
                    if released.is_set():
                        for item in [pod(), pod(False)]:
                            self.wfile.write(json.dumps({'type':'ADDED','object':item}).encode()+b'\n')
                        sent = len(events)
                    self.wfile.write(json.dumps(bookmark).encode()+b'\n')
                    self.wfile.flush()
                    try:
                        while not stopping.is_set():
                            for event in events[sent:]:
                                self.wfile.write(json.dumps(event).encode() + b'\n')
                                self.wfile.flush()
                                sent += 1
                            time.sleep(.05)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                data = {'apiVersion': 'v1', 'kind': 'PodList', 'metadata': {'resourceVersion': '1'},
                        'items': [pod(), pod(False)] if released.is_set() else []}
            elif parsed.path == '/api/v1/namespaces':
                data = {'apiVersion': 'v1', 'kind': 'NamespaceList', 'metadata': {'resourceVersion': '1'}, 'items': []}
            else:
                self.send_error(404)
                return
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            data = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            if self.headers.get('Content-Encoding') == 'gzip':
                data = gzip.decompress(data)
            attempts.append(1)
            if rejecting.is_set():
                self.send_error(503)
                return
            obj = json.loads(data)
            for resource in obj.get('resourceLogs', []):
                attrs = {x['key']: x['value'].get('stringValue') for x in resource['resource'].get('attributes', [])}
                for scope in resource.get('scopeLogs', []):
                    for record in scope.get('logRecords', []):
                        received.append((json.loads(record['body']['stringValue'])['event_id'], attrs))
                        native_ids.append((record.get('traceId'),record.get('spanId')))
            body = b'{}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = 'http://127.0.0.1:' + str(server.server_port)
    with tempfile.TemporaryDirectory(prefix='charlie-alloy-proof-') as tmp:
        tmp = Path(tmp)
        base = tmp / 'pods'
        log = base / f'{ns}_{name}_{uid}' / 'worker' / '0.log'
        log.parent.mkdir(parents=True)
        unowned = base / f'{ns}_not-owned_unowned-uid' / 'worker' / '0.log'
        unowned.parent.mkdir(parents=True)

        def append(path, event):
            with path.open('a') as f:
                f.write(time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()) + '.000000000Z stdout F ' + json.dumps({'event_id': event, 'trace_id':'11111111111111111111111111111111', 'span_id':'2222222222222222'}) + '\n')

        for i in range(3):
            append(log, f'first-{i}')
        append(unowned, 'MUST-NOT-BE-READ')
        production = json.loads((ROOT / 'clusters/aws-dev-workload/values/alloy.yaml').read_text())['alloy']['configMap']['content']
        config = production.replace('role = "pod"\n  selectors', f'role = "pod"\n  api_server = "{endpoint}"\n  selectors', 1)
        config = config.replace('/var/log/pods/', str(base) + '/')
        config = config.replace('directory = "/var/lib/alloy/queue"', f'directory = "{tmp}/queue"')
        config = config.replace('otelcol.exporter.otlphttp "gateway" {', 'otelcol.exporter.otlphttp "gateway" {\n  encoding = "json"')
        config = config.replace('endpoint = "https://observability-gateway.observability.svc:4318"', f'endpoint = "{endpoint}"')
        config = config.replace('tls { ca_file = "/etc/otel/client/ca.crt" }', '')
        path = tmp / 'config.alloy'
        path.write_text(config)
        output = (tmp / 'alloy.log').open('w+')
        proc = None

        def start():
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            sock.close()
            return subprocess.Popen([BINARY, 'run', '--stability.level=public-preview', '--disable-reporting',
                '--server.http.listen-addr=127.0.0.1:' + str(port), '--storage.path=' + str(tmp / 'state'), str(path)],
                env={**os.environ, 'NODE_NAME': 'proof-node', 'OTLP_AUTH_TOKEN': 'synthetic-only'}, stdout=output, stderr=output)

        def has(event):
            return any(x[0] == event for x in received)

        try:
            proc = start()
            time.sleep(3)
            assert proc.poll() is None, 'Alloy failed to start'
            assert not received, 'File read before metadata discovery'
            assert not any(uid in f.read_text(errors='ignore') for f in (tmp / 'state').rglob('*positions*') if f.is_file())
            print('PASS: metadata absent; no reader/checkpoint or export', flush=True)
            released.set()
            events.extend([{'type': 'ADDED', 'object': pod()}, {'type': 'ADDED', 'object': pod(False)}])
            eventually(lambda: len(received) >= 3, 'completed Pod first logs')
            time.sleep(1)
            counts = collections.Counter(x[0] for x in received)
            assert counts == {f'first-{i}': 1 for i in range(3)}, counts
            assert all(x[1]['k8s.pod.uid'] == uid and x[1]['service.name'] == ns + '-worker' for x in received)
            assert all(x[1]['service.version']=='synthetic:fixture' for x in received)
            assert set(native_ids) == {('11111111111111111111111111111111','2222222222222222')}, native_ids
            print('PASS: already-completed Pod, first 3 IDs, duplicate ports, identity, native trace/span IDs, unowned exclusion', flush=True)
            log.rename(log.with_name('0.log.old'))
            append(log, 'after-rotation')
            eventually(lambda: has('after-rotation'), 'rotation')
            rejecting.set()
            before = len(attempts)
            append(log, 'queued-before-crash')
            eventually(lambda: len(attempts) > before, 'failed delivery queued')
            # Allow the independent file positions sync to flush before the hard crash.
            time.sleep(12)
            proc.kill()
            proc.wait(timeout=5)
            rejecting.clear()
            proc = start()
            eventually(lambda: has('queued-before-crash'), 'persistent queue restart')
            time.sleep(1)
            assert not has('MUST-NOT-BE-READ')
            positions = [f.read_text(errors='ignore') for f in (tmp / 'state').rglob('*positions*') if f.is_file()]
            assert not any('unowned-uid' in x for x in positions), positions
            print('PASS: rotation, hard-crash queue recovery, unowned file absent from positions', flush=True)
            events.append({'type':'DELETED','object':pod()})
            time.sleep(4)
            append(log,'MUST-NOT-READ-DELETED-POD')
            time.sleep(3)
            assert not has('MUST-NOT-READ-DELETED-POD')
            print('PASS: deleted Pod target no longer read',flush=True)
            print(json.dumps({'unique_event_ids': len(set(x[0] for x in received)),
                              'duplicates_after_crash': len(received) - len(set(x[0] for x in received))}), flush=True)
        except Exception:
            output.flush()
            output.seek(0)
            print(output.read()[-10000:])  # Synthetic-only fixture; no production credentials/data.
            raise
        finally:
            if proc and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            stopping.set()
            server.shutdown()
            output.close()


if __name__ == '__main__':
    main()
