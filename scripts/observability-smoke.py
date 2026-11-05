#!/usr/bin/env python3
"""Build synthetic correlated OTLP data; sending requires explicit --send and HTTPS."""
import argparse
import json
import http.client
import socket
import os
from pathlib import Path
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


def payloads(count=100, trace_count=10, cluster='synthetic', run_id='clickstack-smoke'):
    if not (1 <= count <= 10000 and 1 <= trace_count <= 1000):
        raise ValueError('Counts must be bounded: logs 1..10000, traces 1..1000')
    now = time.time_ns()
    ids = [(secrets.token_hex(16), secrets.token_hex(8)) for _ in range(trace_count)]
    attrs = [{'key': key, 'value': {'stringValue': value}} for key, value in (
        ('service.name', 'clickstack-smoke'), ('service.version', 'synthetic'),
        ('deployment.environment.name', 'synthetic'), ('k8s.cluster.name', cluster), ('run_id', run_id))]
    logs = [{'timeUnixNano': str(now + i), 'severityNumber': 17, 'severityText': 'ERROR',
             'body': {'stringValue': f'Synthetic failure {i}; not a real application error'},
             'traceId': ids[i % trace_count][0], 'spanId': ids[i % trace_count][1],
             'attributes': [{'key': 'event_id', 'value': {'stringValue': f'{run_id}-{i}'}}]}
            for i in range(count)]
    spans = [{'traceId': tid, 'spanId': sid, 'name': 'synthetic-request', 'kind': 2,
              'startTimeUnixNano': str(now), 'endTimeUnixNano': str(now + 10000000),
              'status': {'code': 2, 'message': 'Synthetic error'}} for tid, sid in ids]
    return ({'resourceLogs': [{'resource': {'attributes': attrs}, 'scopeLogs': [{'scope': {'name': 'charlie-smoke'}, 'logRecords': logs}]}]},
            {'resourceSpans': [{'resource': {'attributes': attrs}, 'scopeSpans': [{'scope': {'name': 'charlie-smoke'}, 'spans': spans}]}]})


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the bearer token through a redirect.


class LoopbackHTTPS(urllib.request.HTTPSHandler):
    """Connect to a local port-forward while retaining the URL hostname for SNI/CA checks."""
    def __init__(self, context, address):
        super().__init__(context=context)
        if address not in ('127.0.0.1', '::1'):
            raise ValueError('Only a loopback connection override is permitted')
        self.address = address

    def https_open(self, request):
        def connection(host, **kwargs):
            conn = http.client.HTTPSConnection(host, **kwargs)
            conn._create_connection = lambda target, timeout, source_address=None: socket.create_connection(
                (self.address, conn.port), timeout, source_address)
            return conn
        return self.do_open(connection, request, context=self._context)


def send(endpoint, ca_file, token, logs, traces, connect_address=None):
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('An HTTPS endpoint without credentials/query/fragment is required')
    if not token:
        raise ValueError('OTLP_AUTH_TOKEN is required in the environment')
    context = ssl.create_default_context(cafile=ca_file)
    handler = LoopbackHTTPS(context, connect_address) if connect_address else urllib.request.HTTPSHandler(context=context)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), handler) if connect_address else urllib.request.build_opener(NoRedirect(), handler)
    for path, data in (('logs', logs), ('traces', traces)):
        request = urllib.request.Request(endpoint.rstrip('/') + '/v1/' + path,
                                         data=json.dumps(data).encode(), method='POST',
                                         headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        try:
            response = opener.open(request, timeout=15)
        except urllib.error.HTTPError as error:
            error.close()
            raise ValueError('Collector HTTP response rejected') from None
        with response:
            body = response.read(1024 * 1024)
            result = json.loads(body) if body else {}
            if result.get('partialSuccess'):
                raise ValueError('Collector reported partial success; inspect stored event IDs')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=100)
    parser.add_argument('--trace-count', type=int, default=10)
    parser.add_argument('--cluster', default='synthetic')
    parser.add_argument('--run-id', default='clickstack-smoke')
    parser.add_argument('--write-fixtures', type=Path)
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--endpoint')
    parser.add_argument('--ca-file')
    parser.add_argument('--connect-address', choices=('127.0.0.1', '::1'))
    args = parser.parse_args()
    try:
        logs, traces = payloads(args.count, args.trace_count, args.cluster, args.run_id)
        if args.write_fixtures:
            args.write_fixtures.mkdir(parents=True, exist_ok=True)
            for name, body in [('logs.json', logs), ('traces.json', traces)]:
                (args.write_fixtures / name).write_text(json.dumps(body, indent=2) + '\n')
        if args.send:
            if not args.endpoint:
                parser.error('--send requires --endpoint')
            send(args.endpoint, args.ca_file, os.environ.get('OTLP_AUTH_TOKEN', ''), logs, traces, args.connect_address)
            print(f'Collector accepted requests: logs={args.count}, traces={args.trace_count}; verify stored IDs separately')
        else:
            print(f'Synthetic payloads prepared: logs={args.count}, traces={args.trace_count}; nothing sent')
    except (ValueError, OSError, urllib.error.URLError):
        # Do not log request headers, credentials, server bodies or data URLs.
        parser.exit(1, 'Smoke check failed; check TLS/auth/connectivity and stored IDs without exposing credentials\n')


if __name__ == '__main__':
    main()
