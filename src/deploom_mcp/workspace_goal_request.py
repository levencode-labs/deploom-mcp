"""Resolve concise goals into explicit, inventory-bound workspace authority."""
from __future__ import annotations

import re
from deploom_mcp.goal_contract import GoalContractError, canonical_digest
from deploom_mcp.workspace_policy_request import validate_workspace_policy
from deploom_mcp.workspace_tools import observe_workspace_inventory_v1

_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_FIELDS = ('owner', 'section', 'name')
_SECTIONS = {'dependencies', 'devDependencies', 'optionalDependencies'}


def validate_goal_request(request):
    if (not isinstance(request, dict) or type(request.get('schemaVersion')) is not int
            or request['schemaVersion'] != 3):
        raise GoalContractError('INVALID_WORKSPACE_REQUEST_VERSION')
    health = request.get('goalKind') == 'MONOREPO_TO_POLICY'
    if request.get('goalKind') not in {'TARGETED_UPGRADE', 'MONOREPO_TO_POLICY'}:
        raise GoalContractError('UNSUPPORTED_WORKSPACE_GOAL')
    required = {'schemaVersion', 'goalKind', 'scope', 'registry'} | ({'policy'} if health else set())
    if not required <= request.keys() <= required | {'environment', 'candidateLimit', 'preflight'}:
        raise GoalContractError('INVALID_WORKSPACE_GOAL_REQUEST')
    limit = request.get('candidateLimit', 8)
    if type(limit) is not int or not 2 <= limit <= 32:
        raise GoalContractError('INVALID_WORKSPACE_CANDIDATE_LIMIT')
    scope = request['scope']
    if not isinstance(scope, list) or not (0 if health else 1) <= len(scope) <= 64:
        raise GoalContractError('INVALID_WORKSPACE_GOAL_SCOPE')
    if health:
        validate_workspace_policy(request['policy'])
    for row in scope:
        if (not isinstance(row, dict) or not {'selector', 'approvedLines'} <= row.keys()
                <= {'selector', 'approvedLines', 'exactTarget'}):
            raise GoalContractError('INVALID_WORKSPACE_GOAL_SCOPE')
        selector = row['selector']
        if (not isinstance(selector, dict) or not {'name'} <= selector.keys() <= set(_FIELDS)
                or any(not isinstance(value, str) or not 1 <= len(value) <= 512 for value in selector.values())
                or 'section' in selector and selector['section'] not in _SECTIONS):
            raise GoalContractError('INVALID_WORKSPACE_SELECTOR')
        lines, exact = row['approvedLines'], row.get('exactTarget')
        if (not isinstance(lines, list) or not 1 <= len(lines) <= 32
                or any(type(line) is not int or not 0 <= line <= 999999 for line in lines)
                or len(set(lines)) != len(lines)
                or exact is not None and (not isinstance(exact, str) or len(exact) > 64
                    or not _VERSION.fullmatch(exact) or int(exact.split('.')[0]) not in lines)):
            raise GoalContractError('INVALID_WORKSPACE_VERSION_AUTHORITY')
    if not health and not any(row.get('exactTarget') for row in scope):
        raise GoalContractError('WORKSPACE_TARGETED_GOAL_REQUIRES_EXACT_TARGET')


async def resolve_goal_request(project_path, request):
    validate_goal_request(request)
    result = await observe_workspace_inventory_v1(project_path)
    if not result['ok']:
        return result
    inventory = result['inventory']
    targets, mutable, seen = [], [], set()
    for row in request['scope']:
        selector = row['selector']
        matches = [item for item in inventory['declarations'] if item['section'] in _SECTIONS
                   and all(item[field] == value for field, value in selector.items())]
        if len(matches) != 1:
            return {'ok': False, 'error': {'code': 'AMBIGUOUS_SCOPE' if matches else 'WORKSPACE_SCOPE_NOT_FOUND',
                    'selector': selector, 'validSelectors': [{field: item[field] for field in _FIELDS}
                                                           for item in matches[:16]],
                    'matchCount': len(matches)}, 'executionReady': False}
        selected = {field: matches[0][field] for field in _FIELDS}
        key = tuple(selected[field] for field in _FIELDS)
        if key in seen:
            raise GoalContractError('DUPLICATE_WORKSPACE_SELECTOR')
        seen.add(key)
        targets.append({'selector': selected, 'approvedLines': row['approvedLines'],
                        'versions': None, 'exactTarget': row.get('exactTarget')})
        mutable.append({'declaration': list(key), 'approvedLines': row['approvedLines'],
                        'versions': None, 'exactTarget': row.get('exactTarget')})
    selection = {'rule': 'LATEST_STABLE_PLUS_BASELINE_V1', 'limit': request.get('candidateLimit', 8)}
    normalized = {'schemaVersion': 3, 'goalKind': request['goalKind'], 'targets': targets,
                  'inventoryDigest': inventory['inventoryDigest'], 'environment': request.get('environment', {}),
                  'topology': None, 'registry': request['registry'], 'candidateSelection': selection,
                  'goalRequestDigest': canonical_digest(request)}
    for field in ('policy', 'preflight'):
        if field in request:
            normalized[field] = request[field]
    snapshot = {key: value for key, value in normalized.items()
                if key not in {'schemaVersion', 'goalKind', 'targets', 'goalRequestDigest'}}
    snapshot['mutable'] = mutable
    snapshot['requireUpgrade'] = request['goalKind'] == 'TARGETED_UPGRADE'
    if 'policy' in snapshot:
        snapshot['policy'] = validate_workspace_policy(snapshot['policy'])
    return {'ok': True, 'request': normalized, 'snapshot': snapshot}
