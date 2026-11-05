#!/usr/bin/env python3
"""Validate the one-time management scaleout; never print raw plan values."""
import hashlib
import json
import sys

TARGET = 'module.eks.aws_eks_node_group.az["ap-northeast-2a"]'
OTHER = 'module.eks.aws_eks_node_group.az["ap-northeast-2c"]'
APPROVED_DRIFT_HASH = '495482837a709de5d095c17f8d88ccbc3fbe2ae1500d60725ce486f76e554f98'


def require(ok):
    if not ok:
        raise ValueError('Scaleout contract mismatch')


def unknown(value):
    if isinstance(value, dict):
        return any(unknown(v) for v in value.values())
    if isinstance(value, list):
        return any(unknown(v) for v in value)
    return value is True


def drift_projection(drift):
    projected = []
    for item in sorted(drift, key=lambda x: x['address']):
        change = item['change']
        before, after = change['before'], change['after']
        keys = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        projected.append({'address': item['address'], 'actions': change['actions'],
                          'diff': {k: {'before': before.get(k), 'after': after.get(k)} for k in keys}})
    return projected


def verify_ownership(resources, drift):
    """Observed role attachments and SG rules must match separately managed resources."""
    values = [r['change']['after'] for r in resources if r.get('mode') == 'managed']
    attachments = [r['change']['after'] for r in resources if r.get('type') == 'aws_iam_role_policy_attachment']
    for item in drift:
        after = item['change']['after']
        if item['type'] == 'aws_iam_role':
            expected = sorted(a['policy_arn'] for a in attachments if a['role'] == after['name'])
            require(expected and sorted(after['managed_policy_arns']) == expected)
        elif item['type'] == 'aws_iam_policy':
            require(after['attachment_count'] == len([a for a in attachments if a['policy_arn'] == after['arn']]) == 1)
        elif item['type'] == 'aws_security_group':
            if item['address'].endswith('.api'):
                rules = [v for v in values if v.get('security_group_id') == after['id'] and v.get('referenced_security_group_id')]
                require(len(rules) == 1 and len(after['ingress']) == 1)
                observed, declared = after['ingress'][0], rules[0]
                require(observed['security_groups'] == [declared['referenced_security_group_id']])
                require(observed['protocol'] == declared['ip_protocol'] == 'tcp')
                require(observed['from_port'] == declared['from_port'] == 443 and observed['to_port'] == declared['to_port'] == 443)
            else:
                rules = [v for v in values if v.get('security_group_id') == after['id'] and v.get('cidr_ipv4') == '0.0.0.0/0']
                require(len(rules) == 1 and len(after['egress']) == 1)
                require(rules[0]['ip_protocol'] == after['egress'][0]['protocol'] == '-1')
                require(after['egress'][0]['cidr_blocks'] == ['0.0.0.0/0'])


def check(plan):
    require(isinstance(plan, dict) and str(plan.get('format_version', '')).startswith('1.'))
    require(plan.get('errored') is not True and plan.get('complete') is True and not plan.get('deferred_changes') and not plan.get('action_invocations'))
    resources = plan['resource_changes']
    require(isinstance(resources, list))
    indexed = {}
    updates = []
    for item in resources:
        address, change = item['address'], item['change']
        require(address not in indexed and not item.get('previous_address') and not change.get('importing'))
        indexed[address] = item
        if item['mode'] == 'data':
            require(change['actions'] in (['read'], ['no-op']))
        else:
            require(item['mode'] == 'managed' and not unknown(change.get('after_unknown', {})))
            require(change['actions'] in (['no-op'], ['update']))
            if change['actions'] == ['update']:
                updates.append(item)
            else:
                require(change.get('before') == change.get('after'))
    require(all(c['actions'] == ['no-op'] and not unknown(c.get('after_unknown', {})) for c in plan.get('output_changes', {}).values()))
    for address, count in ((TARGET, 2), (OTHER, 1)):
        item = indexed[address]
        require(item['mode'] == 'managed' and item['type'] == 'aws_eks_node_group')
        after = item['change']['after']
        require(after['cluster_name'] == 'charlie-dev-management')
        require(after['node_group_name'] == 'charlie-dev-management-' + ('ap-northeast-2a' if address == TARGET else 'ap-northeast-2c'))
        require(after['arn'].startswith('arn:aws:eks:ap-northeast-2:525091405886:nodegroup/charlie-dev-management/'))
        require(after['instance_types'] == ['m7i-flex.large'])
        require(after['scaling_config'] == [{'min_size': count, 'max_size': count, 'desired_size': count}])
    drift = plan.get('resource_drift', [])
    require(isinstance(drift, list))
    if not updates:
        require(not drift)
        return 'noop'
    require(len(updates) == 1 and updates[0]['address'] == TARGET)
    change = updates[0]['change']
    before, after = change['before'], change['after']
    require([k for k in set(before) | set(after) if before.get(k) != after.get(k)] == ['scaling_config'])
    require(before['scaling_config'] == [{'min_size': 1, 'max_size': 1, 'desired_size': 1}])
    require(len(drift) == 11)
    digest = hashlib.sha256(json.dumps(drift_projection(drift), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    require(digest == APPROVED_DRIFT_HASH)
    verify_ownership(resources, drift)
    return 'update'


def main():
    try:
        result = check(json.load(sys.stdin))
    except (ValueError, KeyError, TypeError, AttributeError):
        print('Management scaleout guard: rejected plan; no apply.', file=sys.stderr)
        return 1
    print('Management scaleout guard: ' + ('already at 2a=2/2c=1; no apply.' if result == 'noop' else 'approved 2a scaling 1->2 only.'))
    return 3 if result == 'noop' else 0


if __name__ == '__main__':
    sys.exit(main())
