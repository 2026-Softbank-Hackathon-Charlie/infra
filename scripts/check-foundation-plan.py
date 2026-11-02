#!/usr/bin/env python3
"""Fail before apply on removal/replacement of existing foundation resources.

Only addresses/actions are emitted. Plan JSON (which can contain secrets) stays
in the pipe and is never saved, printed or attached as a CI artifact.
"""
import json
import sys

PREFIXES = {'aws_subnet.public', 'aws_subnet.private', 'aws_nat_gateway.egress', 'aws_eip.nat'}
EXACT = {'aws_vpc.shared', 'aws_s3_bucket.loki'}


def protected(address):
    return address in EXACT or any(address == p or address.startswith(p + '[') for p in PREFIXES)


def check(plan):
    if not isinstance(plan, dict) or not str(plan.get('format_version', '')).startswith('1.'):
        raise ValueError('Unsupported or absent Terraform plan format.')
    changes = plan.get('resource_changes', [])
    if not isinstance(changes, list):
        raise ValueError('Malformed resource_changes.')
    blocked = []
    for change in changes:
        address = change['address']
        previous = change.get('previous_address', address)
        actions = change['change']['actions']
        if not isinstance(address, str) or not isinstance(previous, str) or not isinstance(actions, list):
            raise ValueError('Malformed resource change.')
        if not actions or not all(isinstance(a,str) and a in {'no-op','create','read','update','delete','forget'} for a in actions):
            raise ValueError('Unsupported resource actions.')
        if 'delete' in actions and (protected(address) or protected(previous)):
            blocked.append((address, actions))
    return blocked


def main():
    try:
        blocked = check(json.load(sys.stdin))
    except (ValueError, KeyError, TypeError) as error:
        # Parser exceptions may contain raw plan values; never print them.
        print('Foundation plan guard: malformed/unsupported plan; refusing apply.', file=sys.stderr)
        return 1
    if blocked:
        for address, actions in blocked:
            print(f'Protected foundation resource: {address!r}, actions={actions!r}', file=sys.stderr)
        return 1
    print('Foundation plan guard: protected resources retained.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
