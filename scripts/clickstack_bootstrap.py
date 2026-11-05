"""Pure credential and context contracts for the opt-in ClickStack CI bootstrap."""
import base64
import datetime as dt
import json
import re
import secrets
import uuid
from pathlib import Path
from urllib.parse import quote

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ACCOUNT = '525091405886'
REGION = 'ap-northeast-2'
CLUSTER = 'charlie-dev-management'
REPO = '2026-Softbank-Hackathon-Charlie/infra'
ROLE = f'arn:aws:iam::{ACCOUNT}:role/charlie-dev-github-terraform'
POLICY = 'arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy'
OWNER = 'charlie-clickstack-ci'
OWNER_KEY = 'charlie.dev/bootstrap-owner'
RUN_KEY = 'charlie.dev/bootstrap-run-id'
ATTEMPT_KEY = 'charlie.dev/bootstrap-attempt'
REPO_KEY = 'charlie.dev/bootstrap-repo'
BUNDLE_KEY = 'charlie.dev/bundle-id'
WORKFLOW = '.github/workflows/clickstack-management.yml'
APPS = ('charlie-management-clickstack-operators', 'charlie-management-clickstack',
        'charlie-management-observability-gateway', 'charlie-management-observability-agent')
SECRET_NAMES = ('clickstack-bootstrap-ca', 'clickstack-clickhouse-auth', 'clickstack-mongodb-auth',
                'clickstack-collector-auth', 'clickstack-runtime', 'clickstack-ui-config', 'clickstack-otlp-tls',
                'observability-client-auth', 'observability-client-ca', 'observability-gateway-tls', 'clickstack-demo-login')


class Rejected(RuntimeError):
    """Only constant, credential-free messages belong in this exception."""


def require(ok, message='Bootstrap contract rejected'):
    if not ok:
        raise Rejected(message)


def context(env, action=None):
    action = action or env.get('CLICKSTACK_ACTION')
    require(env.get('GITHUB_REPOSITORY') == REPO and env.get('GITHUB_REF') == 'refs/heads/main', 'Repository/branch rejected')
    require(env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', 'Manual dispatch required')
    sha = env.get('EXPECTED_SHA', '')
    require(re.fullmatch('[0-9a-f]{40}', sha) and env.get('GITHUB_SHA') == sha, 'Execution SHA rejected')
    require(env.get('AWS_ACCOUNT_ID') == ACCOUNT and env.get('AWS_REGION') == REGION, 'Account/region rejected')
    require(env.get('AWS_ROLE_ARN') == ROLE, 'CI role rejected')
    require(action in ('preflight', 'deploy', 'cleanup-access'), 'Action rejected')
    require(re.fullmatch('[0-9]+', env.get('GITHUB_RUN_ID', '')), 'Workflow run ID rejected')
    require(re.fullmatch('[0-9]+', env.get('GITHUB_RUN_ATTEMPT', '')), 'Workflow attempt rejected')
    if action in ('deploy', 'cleanup-access'):
        require(env.get('CONFIRM_EXECUTION') == 'true', 'Explicit execution confirmation required')
    if action == 'cleanup-access':
        require(re.fullmatch('[0-9]+', env.get('CLEANUP_OWNER_RUN_ID', '')), 'Cleanup owner run ID required')
    return action, sha, env['GITHUB_RUN_ID']


def access_owned(entry, owner_run):
    tags = entry.get('tags', {})
    return (entry.get('principalArn') == ROLE and entry.get('type') == 'STANDARD'
            and not entry.get('kubernetesGroups') and tags.get(OWNER_KEY) == OWNER
            and tags.get(RUN_KEY) == str(owner_run) and re.fullmatch('[0-9]+', tags.get(ATTEMPT_KEY, '')) and tags.get(REPO_KEY) == REPO.replace('/', '.'))


def certificate_pair(ca=None, name=None):
    now = dt.datetime.now(dt.timezone.utc)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name or 'Charlie ClickStack demo CA')])
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(ca[0].subject if ca else subject)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=7 if ca else 30))
               .add_extension(x509.BasicConstraints(ca=ca is None, path_length=0 if ca is None else None), critical=True))
    builder = builder.add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    builder = builder.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca[1].public_key() if ca else key.public_key()), critical=False)
    builder = builder.add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
        key_encipherment=False, data_encipherment=False, key_agreement=False,
        key_cert_sign=ca is None, crl_sign=ca is None, encipher_only=False, decipher_only=False), critical=True)
    if ca:
        builder = builder.add_extension(x509.SubjectAlternativeName([
            x509.DNSName(name), x509.DNSName(name + '.cluster.local')]), critical=False)
        builder = builder.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    cert = builder.sign(ca[1] if ca else key, hashes.SHA256())
    return cert, key


def encode_pair(pair, ca=False):
    cert, key = pair
    return {('ca.crt' if ca else 'tls.crt'): cert.public_bytes(serialization.Encoding.PEM).decode(),
            ('ca.key' if ca else 'tls.key'): key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()}


def validate_pair(data, ca=None, name=None):
    is_ca = ca is None
    crt, keyname = ('ca.crt', 'ca.key') if is_ca else ('tls.crt', 'tls.key')
    try:
        require(set(data) == {crt, keyname})
        cert = x509.load_pem_x509_certificate(data[crt].encode())
        key = serialization.load_pem_private_key(data[keyname].encode(), password=None)
        require(isinstance(key, ec.EllipticCurvePrivateKey) and isinstance(key.curve, ec.SECP256R1))
        require(cert.public_key().public_numbers() == key.public_key().public_numbers())
        now = dt.datetime.now(dt.timezone.utc)
        require(cert.not_valid_before_utc <= now and cert.not_valid_after_utc > now + dt.timedelta(days=1), 'Certificate expiry rejected')
        require(cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca == is_ca)
        issuer = cert if is_ca else ca[0]
        require(cert.issuer == issuer.subject)
        issuer.public_key().verify(cert.signature, cert.tbs_certificate_bytes, ec.ECDSA(cert.signature_hash_algorithm))
        if not is_ca:
            sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
            require(set(sans) == {name, name + '.cluster.local'}, 'Certificate SAN rejected')
            require(ExtendedKeyUsageOID.SERVER_AUTH in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value)
        return cert, key
    except Rejected:
        raise
    except Exception:
        raise Rejected('Certificate material rejected') from None


class Bundle:
    """11 named Secrets only, canonical-before-derived order, create-only API."""
    def __init__(self, get, create, root):
        self.get, self.create, self.root = get, create, Path(root)
        self.id = None
        self.values = {}

    def read(self, name):
        require(name in SECRET_NAMES)
        obj = self.get(name)
        if obj is None:
            return None
        labels = obj.get('metadata', {}).get('labels', {})
        require(obj.get('kind') == 'Secret' and obj['metadata'].get('namespace') == 'observability' and obj['metadata'].get('name') == name)
        require(labels.get(OWNER_KEY) == OWNER, 'Existing Secret owner rejected')
        bundle_id = labels.get(BUNDLE_KEY, '')
        require(re.fullmatch('[0-9a-f-]{36}', bundle_id) and str(uuid.UUID(bundle_id)) == bundle_id, 'Secret bundle ID rejected')
        if self.id is None:
            self.id = bundle_id
        require(bundle_id == self.id, 'Mixed Secret bundles rejected')
        try:
            return {k: base64.b64decode(v, validate=True).decode() for k, v in obj['data'].items()}
        except Exception:
            raise Rejected('Secret encoding rejected') from None

    def put(self, name, factory, *, derived=False, tls=None):
        current = self.read(name)
        expected = factory() if current is None or derived else current
        require(isinstance(expected, dict) and all(isinstance(v, str) and v for v in expected.values()))
        if current is not None:
            require(set(current) == set(expected), 'Secret keys rejected')
            if derived:
                require(current == expected, 'Derived Secret inconsistency rejected')
        else:
            require(self.id is not None)
            obj = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': name, 'namespace': 'observability',
                   'labels': {OWNER_KEY: OWNER, BUNDLE_KEY: self.id}},
                   'type': 'kubernetes.io/tls' if tls else 'Opaque',
                   'data': {k: base64.b64encode(v.encode()).decode() for k, v in expected.items()}}
            self.create(obj)  # create only; an AlreadyExists race is not an overwrite.
        self.values[name] = current if current is not None else expected
        return self.values[name]

    def build(self):
        ca_existing = self.get(SECRET_NAMES[0])
        if ca_existing is None:
            # A partial unowned bundle without its canonical CA must never be adopted.
            require(all(self.get(n) is None for n in SECRET_NAMES[1:]), 'Secrets exist without canonical CA')
            self.id = str(uuid.uuid4())
        if ca_existing is not None:
            for name in SECRET_NAMES:
                self.read(name)  # Reject all foreign/mixed/invalid encodings before creating anything.
        dependencies = {
            'clickstack-clickhouse-auth': ('clickstack-collector-auth', 'clickstack-runtime', 'clickstack-ui-config'),
            'clickstack-mongodb-auth': ('clickstack-runtime',),
            'clickstack-collector-auth': ('observability-client-auth',),
        }
        for canonical, dependent in dependencies.items():
            require(self.get(canonical) is not None or all(self.get(d) is None for d in dependent),
                    'Canonical credential missing; no regeneration')
        ca_data = self.put('clickstack-bootstrap-ca', lambda: encode_pair(certificate_pair(), ca=True))
        ca = validate_pair(ca_data)
        ch = self.put('clickstack-clickhouse-auth', lambda: {k: secrets.token_urlsafe(32) for k in ('read-password', 'ingest-password', 'default-password')})
        mongo = self.put('clickstack-mongodb-auth', lambda: {'password': secrets.token_urlsafe(32)})
        collector = self.put('clickstack-collector-auth', lambda: {'CLICKHOUSE_PASSWORD': ch['ingest-password'], 'OTLP_AUTH_TOKEN': secrets.token_urlsafe(32)})
        require(set(ch) == {'read-password', 'ingest-password', 'default-password'} and set(mongo) == {'password'})
        require(set(collector) == {'CLICKHOUSE_PASSWORD', 'OTLP_AUTH_TOKEN'} and collector['CLICKHOUSE_PASSWORD'] == ch['ingest-password'])
        require(all(re.fullmatch('[A-Za-z0-9_-]{32,128}', v) for v in [*ch.values(), mongo['password'], collector['OTLP_AUTH_TOKEN']]))
        mongo_uri = 'mongodb://hyperdx:' + quote(mongo['password'], safe='') + '@clickstack-mongodb-svc.observability.svc:27017/hyperdx?authSource=hyperdx&replicaSet=clickstack-mongodb'
        runtime = self.put('clickstack-runtime', lambda: {'MONGO_URI': mongo_uri, 'EXPRESS_SESSION_SECRET': secrets.token_urlsafe(48),
            'TOKEN_ENCRYPTION_KEY': base64.b64encode(secrets.token_bytes(32)).decode(), 'HYPERDX_API_KEY': str(uuid.uuid4())})
        require(set(runtime) == {'MONGO_URI', 'EXPRESS_SESSION_SECRET', 'TOKEN_ENCRYPTION_KEY', 'HYPERDX_API_KEY'} and runtime['MONGO_URI'] == mongo_uri)
        require(len(base64.b64decode(runtime['TOKEN_ENCRYPTION_KEY'], validate=True)) == 32 and len(runtime['EXPRESS_SESSION_SECRET']) >= 32)
        require(str(uuid.UUID(runtime['HYPERDX_API_KEY'])) == runtime['HYPERDX_API_KEY'])
        connections = json.loads((self.root / 'helm/charts/clickstack/ci/connections.json').read_text())
        require(connections[0]['password'] == 'FIXTURE_READ_PASSWORD')
        connections[0]['password'] = ch['read-password']
        self.put('clickstack-ui-config', lambda: {'connections.json': json.dumps(connections, sort_keys=True),
                 'sources.json': (self.root / 'helm/charts/clickstack/ci/sources.json').read_text()}, derived=True)
        for secret, hostname in (('clickstack-otlp-tls', 'clickstack-otel-collector.observability.svc'),
                                 ('observability-gateway-tls', 'observability-gateway.observability.svc')):
            data = self.put(secret, lambda h=hostname: encode_pair(certificate_pair(ca, h)), tls=True)
            validate_pair(data, ca, hostname)
        self.put('observability-client-auth', lambda: {'OTLP_AUTH_TOKEN': collector['OTLP_AUTH_TOKEN']}, derived=True)
        self.put('observability-client-ca', lambda: {'ca.crt': ca_data['ca.crt']}, derived=True)
        demo = self.put('clickstack-demo-login', lambda: {'email': 'observability-demo@example.com', 'password': secrets.token_urlsafe(32)})
        require(set(demo) == {'email', 'password'} and demo['email'] == 'observability-demo@example.com' and len(demo['password']) >= 32)
        require(set(self.values) == set(SECRET_NAMES))
        return self.values
