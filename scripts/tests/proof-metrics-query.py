#!/usr/bin/env python3
"""Production NGINX config against local synthetic Prometheus and ephemeral test TLS."""
import base64
import datetime
import http.server
import ipaddress
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[2]

def main():
    binary = os.environ['NGINX_BINARY']
    assert 'nginx/1.30.6' in subprocess.run([binary, '-v'], capture_output=True, text=True).stderr
    seen = []
    class Prometheus(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            seen.append((self.path, self.headers.get('Authorization'), self.headers.get('Cookie')))
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"status":"success","data":[]}')
        do_POST = do_GET
    upstream = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Prometheus)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory(prefix='charlie-query-proof-') as temp:
        temp = Path(temp)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        cert = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=1)).not_valid_after(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(key,hashes.SHA256())
        (temp/'tls.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (temp/'tls.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        # Synthetic password only; no real credential leaves memory or cluster.
        hashed = subprocess.check_output(['openssl','passwd','-apr1','-stdin'],input=b'synthetic-test-only\n').decode().strip()
        (temp/'htpasswd').write_text('proof:'+hashed+'\n')
        docs = yaml.safe_load_all(subprocess.check_output([os.environ.get('HELM','helm'),'template','boundary',str(ROOT/'helm/charts/observability-boundary'),'-n','observability','-f',str(ROOT/'clusters/aws-dev-workload/values/observability-boundary.yaml')],text=True))
        config = next(d['data']['nginx.conf'] for d in docs if d and d['kind']=='ConfigMap')
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        config=config.replace('listen 8443 ssl;',f'listen 127.0.0.1:{port} ssl;').replace('/etc/query/tls',str(temp)).replace('/etc/query/auth',str(temp)).replace('/tmp/nginx.pid',str(temp/'nginx.pid')).replace('/tmp/client',str(temp/'client')).replace('/tmp/proxy',str(temp/'proxy')).replace('http://monitoring-prometheus.observability.svc:9090',f'http://127.0.0.1:{upstream.server_port}')
        conf=temp/'nginx.conf';conf.write_text(config)
        subprocess.run([binary,'-t','-p',str(temp)+'/', '-c',str(conf)],check=True,capture_output=True)
        process=subprocess.Popen([binary,'-p',str(temp)+'/', '-c',str(conf),'-g','daemon off;'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            context=ssl.create_default_context(cafile=str(temp/'tls.crt'))
            def request(path,auth=None,method='GET',host='localhost',ctx=context):
                req=urllib.request.Request(f'https://{host}:{port}'+path,method=method,headers={'Cookie':'synthetic=must-not-forward'})
                if auth: req.add_header('Authorization','Basic '+base64.b64encode(auth.encode()).decode())
                try:
                    with urllib.request.urlopen(req,context=ctx,timeout=3) as r: return r.status
                except urllib.error.HTTPError as error: return error.code
            for _ in range(30):
                try:
                    if request('/healthz')==200: break
                except OSError: time.sleep(.1)
            else: raise AssertionError('NGINX did not start')
            assert request('/api/v1/query?query=up')==401
            assert request('/api/v1/query?query=up','proof:wrong')==401
            assert request('/api/v1/query?query=up','proof:synthetic-test-only')==200
            assert request('/api/v1/query_range','proof:synthetic-test-only','POST')==200
            assert all(auth is None and cookie is None for _,auth,cookie in seen)
            before=len(seen)
            for path in ('/-/reload','/api/v1/admin/tsdb/delete_series','/api/v1/write','/api/v1/read','/api/v2/alerts'):
                assert request(path,'proof:synthetic-test-only')==404, path
            assert request('/api/v1/query','proof:synthetic-test-only','DELETE')==405
            assert len(seen)==before
            for host,ctx in [('127.0.0.1',context),('localhost',ssl.create_default_context())]:
                try: request('/api/v1/query',host=host,ctx=ctx)
                except urllib.error.URLError: pass
                else: raise AssertionError('TLS hostname/untrusted CA accepted')
            print('PASS: actual NGINX TLS, BasicAuth, GET/POST query, header stripping, forbidden paths/methods, hostname and CA rejection')
        finally:
            process.terminate();process.wait(timeout=5);upstream.shutdown()
if __name__=='__main__': main()
