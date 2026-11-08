#!/usr/bin/env python3
"""Read-only precondition before syncing operator-owned Service-IP egress."""
import argparse
import ipaddress
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = tuple(ipaddress.ip_network(cidr) for cidr in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def check_binding(config, service):
    expected_ip = config.get('serviceClusterIP', '')
    expected_uid = config.get('serviceUID', '')
    try:
        address = ipaddress.IPv4Address(expected_ip)
    except ipaddress.AddressValueError:
        raise ValueError('One exact private Service IPv4 address is required') from None
    if not any(address in network for network in PRIVATE) or not expected_uid:
        raise ValueError('Private Service IP and UID binding are required')
    metadata, spec = service.get('metadata', {}), service.get('spec', {})
    if metadata.get('namespace') != 'langfuse' or metadata.get('name') != 'langfuse-web':
        raise ValueError('Unexpected Service identity')
    if metadata.get('uid') != expected_uid or spec.get('clusterIP') != expected_ip:
        raise ValueError('Langfuse Service IP or UID changed; sync blocked')
    if spec.get('selector') != {'app.kubernetes.io/name': 'langfuse', 'app.kubernetes.io/instance': 'langfuse', 'app': 'web'}:
        raise ValueError('Langfuse destination selector changed; sync blocked')
    if not any(port.get('port') == 3000 and port.get('protocol', 'TCP') == 'TCP' for port in spec.get('ports', [])):
        raise ValueError('Langfuse destination port changed; sync blocked')
    return {'service': 'langfuse/langfuse-web', 'uid': expected_uid, 'clusterIP': expected_ip, 'matched': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig', required=True)
    args = parser.parse_args()
    config = json.loads((ROOT / 'clusters/aws-dev-management/values/clickstack.yaml').read_text(encoding='utf8'))['langfuse']
    if not config.get('enabled'):
        print(json.dumps({'enabled': False, 'networkCalls': 0}))
        return
    command = ['kubectl', '--kubeconfig', args.kubeconfig, '--request-timeout=10s', '-n', 'langfuse', 'get', 'service', 'langfuse-web', '-o', 'json']
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf8', timeout=20)
    if result.returncode:
        raise SystemExit('Service binding unavailable; sync blocked, provider diagnostic withheld')
    print(json.dumps(check_binding(config, json.loads(result.stdout))))


if __name__ == '__main__':
    main()
