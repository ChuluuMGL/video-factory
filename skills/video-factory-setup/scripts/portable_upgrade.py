"""Explicit, disabled-only upgrade of locally recorded portable workflows.

No credentials are exported, no workflow is created/activated and no ledger is
written. A timed-out PUT is reconciled by readback, never automatically repeated.
"""
from contextlib import ExitStack, contextmanager
import copy
import json
from pathlib import Path
from portable_n8n import Fault, digest, identifier, locked, private_write


def read(root, relative):
    path = root / relative
    if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root.parent):
        raise Fault('UPGRADE_SYMLINK_REJECTED')
    return json.loads(path.read_text(encoding='utf-8'))


@contextmanager
def exclusive(root, project):
    from portable_events import receiver_lock
    root = Path(root).resolve()
    with ExitStack() as stack:
        stack.enter_context(locked(root))
        events = root / (project + '-events')
        if events.exists():
            stack.enter_context(locked(events))
            stack.enter_context(receiver_lock(events))
        yield root


def inactive(value):
    if value.get('active') or value.get('activeVersionId') or value.get('isArchived'):
        raise Fault('UPGRADE_REQUIRES_PAUSED_UNARCHIVED_WORKFLOWS')


def matches(live, expected):
    return (live.get('nodes') == expected['nodes'] and
            live.get('connections') == expected['connections'] and
            all(live.get('settings', {}).get(k) == v for k, v in expected['settings'].items()))


def scope(api, root, project):
    event_file = project + '-events/events.json'
    event = read(root, event_file) if (root / event_file).exists() else None
    project_file = 'project-' + project + '.json'
    config = read(root, project_file) if (root / project_file).exists() else event
    if not config or config['origin'] != api.url:
        raise Fault('UPGRADE_PROJECT_SCOPE_REQUIRED')
    for key in ('base', 'table'):
        identifier(config[key])
    if event and any(event.get(k) != config.get(k) for k in ('origin', 'base', 'table')):
        raise Fault('UPGRADE_EVENT_SCOPE_CHANGED')
    files = {project_file: config} if (root / project_file).exists() else {}
    if event:
        files[event_file] = event
    ledgers = {}
    for name in (project, project + '-approval'):
        relative = 'ledger-' + name + '.json'
        if not (root / relative).exists():
            if event: raise Fault('UPGRADE_EXISTING_LEDGER_REQUIRED')
            continue
        saved = read(root, relative)
        if (saved.get('origin') != api.url or saved.get('project') != name or
                saved.get('base') != config['base'] or saved.get('task_table') != config['table'] or saved.get('pending')):
            raise Fault('UPGRADE_LEDGER_SCOPE_CHANGED')
        table = identifier(saved['table'])
        rows = api.request('GET', '/api/v1/data-tables/' + table + '/rows?limit=100')['data']
        allowed = ('idle', 'consumed') if name.endswith('-approval') else ('idle',)
        if len(rows) != 1 or rows[0].get('id') != saved['row'] or rows[0].get('status') not in allowed:
            raise Fault('UPGRADE_RECONCILE_EXECUTION_AND_CANCEL_PENDING_PLAN_FIRST')
        files[relative] = saved
        ledgers[name] = {'table': table, 'row': saved['row']}
        # Bind the preview to complete row contents, retaining existing receipts.
        files['@row:' + table] = rows
    return config, event, ledgers, files


def templates(project, config, event, ledgers, root):
    from n8n_workflows import probe_workflow, schema_workflow, production_workflow, guard_workflow, recovery_workflow
    from portable_approval import scheduled, plan_workflow, confirmation_workflow
    base, table = config['base'], config['table']
    result = {}
    execution = ledgers.get(project)
    for flow in ('probe', 'schema', 'script', 'video-submit', 'video-poll', 'recover'):
        name = project + '-' + flow
        if not (root / ('binding-' + name + '.json')).exists(): continue
        if flow in ('script', 'video-submit', 'recover') and not execution:
            raise Fault('UPGRADE_EXISTING_LEDGER_REQUIRED')
        template = (probe_workflow(base, table) if flow == 'probe' else
                    schema_workflow(base, table) if flow == 'schema' else
                    recovery_workflow(base, table, execution) if flow == 'recover' else
                    production_workflow(base, table, flow))
        if flow in ('script', 'video-submit'): template = guard_workflow(template, execution)
        result[name] = template
    if event:
        approval = ledgers[project + '-approval']
        for logical, item in event['workflows'].items():
            name = identifier(item['project'])
            if not name.startswith(project + '-'):
                raise Fault('UPGRADE_FOREIGN_WORKFLOW_REFERENCE')
            if logical == 'video-poll':
                template = scheduled(production_workflow(base, table, 'video-poll'))
            elif logical.startswith('plan-'):
                flow = logical[5:]
                if flow not in ('script', 'video-submit'): raise Fault('UPGRADE_UNKNOWN_FLOW')
                template = scheduled(plan_workflow(base, table, flow, approval, event['policy'],
                                                  verification_only=event['verification_only']))
            elif logical.startswith('confirm-'):
                flow = logical[8:]
                if flow not in ('script', 'video-submit'): raise Fault('UPGRADE_UNKNOWN_FLOW')
                template = confirmation_workflow(base, table, flow, execution, approval, event['policy'],
                    event['hooks'][flow], verification_only=event['verification_only'])
            else: raise Fault('UPGRADE_UNKNOWN_FLOW')
            binding = read(root, 'binding-' + name + '.json')
            if binding['workflow'] != item['workflow']: raise Fault('UPGRADE_WORKFLOW_REFERENCE_CHANGED')
            if name in result: raise Fault('UPGRADE_DUPLICATE_WORKFLOW')
            result[name] = template
    if not result: raise Fault('UPGRADE_NO_OWNED_WORKFLOWS')
    return result


def bound(template, binding):
    body = copy.deepcopy(template)
    body = {k: body[k] for k in ('nodes', 'connections', 'settings')}
    for node in body['nodes']:
        for kind, ref in node.get('credentials', {}).items():
            role = 'relay' if ref.get('id') == 'RELAY_UNBOUND' else kind
            saved = binding['credentials'][role]
            identifier(saved['id'])
            body_ref = {'id': saved['id'], 'name': saved['name']}
            node['credentials'][kind] = body_ref
    return body


def preview(api, root, project):
    identifier(project)
    with exclusive(root, project) as root:
        for receipt in root.glob('upgrade-*-receipt.json'):
            value = read(root, receipt.name)
            if not value.get('complete'):
                raise Fault('UPGRADE_RESUME_EXISTING_PLAN_BEFORE_NEW_PREVIEW')
        config, event, ledgers, scope_files = scope(api, root, project)
        targets = []
        seen = set()
        for name, template in templates(project, config, event, ledgers, root).items():
            relative = 'binding-' + name + '.json'
            binding = read(root, relative)
            if binding.get('origin') != api.url or binding.get('project') != name or binding.get('pending'):
                raise Fault('UPGRADE_BINDING_SCOPE_CHANGED')
            wid = identifier(binding['workflow'])
            if wid in seen: raise Fault('UPGRADE_DUPLICATE_WORKFLOW')
            seen.add(wid)
            live = api.request('GET', '/api/v1/workflows/' + wid)
            inactive(live)
            if not matches(live, binding['owned_template']): raise Fault('UPGRADE_OWNED_WORKFLOW_MODIFIED')
            new = bound(template, binding)
            # Never discard extra server/user settings when updating.
            new['settings'] = {**live.get('settings', {}), **new['settings']}
            new_binding = {**binding, 'template_sha256': digest(template),
                           'owned_template': bound(template, binding), 'verified_disabled': True}
            targets.append({'binding_file': relative, 'workflow': wid, 'before': binding,
                            'after': new_binding, 'old_live': {k: live.get(k) for k in ('name', 'nodes', 'connections', 'settings')},
                            'body': {'name': live['name'], **new}})
        plan = {'schema': 1, 'origin': api.url, 'project': project, 'scope': scope_files, 'targets': targets}
        plan_id = digest(plan)
        path = root / ('upgrade-' + plan_id + '.json')
        if path.exists() and read(root, path.name) != plan: raise Fault('UPGRADE_PLAN_CHANGED')
        if not path.exists(): private_write(path, plan)
        return {'plan': plan_id, 'workflows': len(targets),
                'changed': sum(t['before'] != t['after'] for t in targets), 'active': False, 'provider_requests': 0}


def apply(api, root, project, expected):
    identifier(project)
    if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
        raise Fault('UPGRADE_PLAN_HASH_REQUIRED')
    with exclusive(root, project) as root:
        plan = read(root, 'upgrade-' + expected + '.json')
        if digest(plan) != expected or plan['project'] != project or plan['origin'] != api.url:
            raise Fault('UPGRADE_PLAN_CHANGED')
        config, event, ledgers, current_scope = scope(api, root, project)
        if current_scope != plan['scope']: raise Fault('UPGRADE_SCOPE_OR_LEDGER_CHANGED_REPLAN')
        generated = templates(project, config, event, ledgers, root)
        journal_path = root / ('upgrade-' + expected + '-receipt.json')
        journal = read(root, journal_path.name) if journal_path.exists() else {'plan': expected, 'items': {}}
        if journal.get('plan') != expected: raise Fault('UPGRADE_RECEIPT_CHANGED')
        # Check the entire set before any PUT, including the current package.
        for target in plan['targets']:
            name = target['before']['project']
            if digest(generated[name]) != target['after']['template_sha256']:
                raise Fault('UPGRADE_PACKAGE_CHANGED_REPLAN')
            binding = read(root, target['binding_file'])
            if binding not in (target['before'], target['after']): raise Fault('UPGRADE_BINDING_CHANGED')
            live = api.request('GET', '/api/v1/workflows/' + target['workflow'])
            inactive(live)
            stage = journal['items'].get(target['workflow'])
            old = all(live.get(k) == v for k, v in target['old_live'].items())
            new = matches(live, target['body']) and live.get('name') == target['body']['name']
            if stage and not new:
                raise Fault('UPGRADE_PREVIOUS_PUT_UNCERTAIN_RECONCILE_NO_RETRY')
            if not stage and not old: raise Fault('UPGRADE_LIVE_CHANGED_REPLAN')
        updated = 0
        for target in plan['targets']:
            wid = target['workflow']
            if target['before'] == target['after']: continue
            if wid not in journal['items']:
                # Recheck immediately before sending, after the full-set preflight.
                live = api.request('GET', '/api/v1/workflows/' + wid); inactive(live)
                if any(live.get(k) != v for k, v in target['old_live'].items()):
                    raise Fault('UPGRADE_LIVE_CHANGED_REPLAN')
                journal['items'][wid] = 'intent'; private_write(journal_path, journal)
                api.request('PUT', '/api/v1/workflows/' + wid, target['body'])
                updated += 1
            live = api.request('GET', '/api/v1/workflows/' + wid); inactive(live)
            if not matches(live, target['body']) or live.get('name') != target['body']['name']:
                raise Fault('UPGRADE_READBACK_MISMATCH_NO_RETRY')
            private_write(root / target['binding_file'], target['after'])
            journal['items'][wid] = 'verified'; private_write(journal_path, journal)
        journal['complete'] = True; private_write(journal_path, journal)
        return {'plan': expected, 'complete': True, 'updated': updated, 'active': False,
                'provider_requests': 0, 'next': '升级已回读；先检查配置与零费用验收，再显式恢复事件接收器和审核入口。'}
