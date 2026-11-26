"""Durable Feishu board outbox. A reserved request is never sent twice."""
import hashlib
import json

from .feishu_bridge import meta, plain_text, save, select_text
from .feishu_diagnostics import failure_context
from .feishu_native_review import source_key, sync_key
from .runtime_store import RuntimeFault, fingerprint
from .script_week_io import CopyOnlyWeekWriter

STATUSES = {
    'awaiting_script_review': '脚本待审核', 'awaiting_storyboard_review': '分镜待审核',
    'awaiting_video_review': '视频待审核', 'ready': '脚本通过', 'accepted': '视频通过',
}


def prefix(project):
    return 'workbench:board:' + project + ':'


def enqueue(db, project, ref, before, after, operation, feedback):
    config = meta(db, 'native:config:' + project) or {}
    if not config.get('enabled'):
        return 'synced'
    if before['state'] == 'awaiting_storyboard_review':
        raise RuntimeFault('WORKBENCH_STORYBOARD_NATIVE_MAPPING_UNAVAILABLE')
    key = prefix(project) + ref
    save(db, key, {
        'native_sha256': fingerprint(config), 'task': before['id'], 'operation': operation,
        'old_revision': before['revision'], 'revision': after['revision'],
        'old_state': before['state'], 'new_state': after['state'], 'feedback': feedback,
        'old_input': json.loads(before['input']), 'new_input': json.loads(after['input']),
        'artifact': json.loads(before['artifact']) if before['artifact'] else None,
        'input_digest': after['input_digest'], 'state': 'pending',
    })
    return 'reconciliation_required'


def receipt_status(db, project, ref):
    item = meta(db, prefix(project) + ref)
    return 'synced' if item and item['state'] == 'synced' else 'reconciliation_required'


def _same(name, value, expected, status):
    if name != status:
        return CopyOnlyWeekWriter._same_value(value, expected)
    try:
        return select_text(value) == expected
    except RuntimeFault:
        return False


def board_items(db, project):
    pattern = prefix(project).replace('_', '\\_') + '%'
    return ((row[0], json.loads(row[1])) for row in db.execute(
        "SELECT key,value FROM meta WHERE key LIKE ? ESCAPE '\\' ORDER BY key", (pattern,)))


def pending_edits(db, project):
    return {(item['task'], item['revision']) for _, item in board_items(db, project)
            if item['operation'] == 'edit' and item['state'] != 'synced'}


def flush(store, project, native, config, authorization):
    with store.connect() as db:
        authorization(db)
        pending = next(((key, item) for key, item in board_items(db, project)
                        if item['state'] == 'pending'), None)
    if not pending:
        return
    key, item = pending
    writes = 0
    try:
        names, context, previous, newer = config['names'], config['context'], item['old_input'], item['new_input']
        if fingerprint(config) != item['native_sha256']:
            raise RuntimeFault('WORKBENCH_BOARD_BINDING_CHANGED')
        base, table = context['target']['base_token'], context['target']['table_id']

        def check(db, expected):
            authorization(db)
            if (meta(db, 'native:config:' + project) != config or native.results.context(db, project) != context
                    or meta(db, key) != expected):
                raise RuntimeFault('WORKBENCH_BOARD_BINDING_CHANGED')
            latest = store._current(db, project, item['task'], item['revision'])
            if latest['input_digest'] != item['input_digest'] or latest['state'] != item['new_state']:
                raise RuntimeFault('WORKBENCH_BOARD_VERSION_CHANGED')

        with store.connect() as db:
            check(db, item)
            link = meta(db, source_key(project, item['task']))
        client = native.client(context)
        from .feishu_native_sync import schema
        fields, live_names = schema(client, base, table, context['target'])
        if fields != config['fields'] or live_names != names:
            raise RuntimeFault('WORKBENCH_BOARD_SCHEMA_CHANGED')
        record = (client.record(base, table, link['record_id']) if link
                  else client.find_task(base, table, names['task'], item['task']))
        remote, record_id = record['fields'], record['record_id']
        if (plain_text(remote.get(names['task'])) != item['task']
                or plain_text(remote.get(names['sku_id'])) != previous['sku_id']
                or plain_text(remote.get(names['source_revision'])) != previous['source_revision']):
            raise RuntimeFault('WORKBENCH_BOARD_VERSION_CHANGED')

        old_status = STATUSES.get(item['old_state'], '脚本退回')
        new_status = STATUSES.get(item['new_state'], '视频退回' if item['old_state'] == 'awaiting_video_review' else '脚本退回')
        expected = {names['status']: new_status, names['feedback']: item['feedback'],
                    names['review_revision']: str(item['revision']),
                    names['script_digest']: hashlib.sha256(newer['script'].encode()).hexdigest(),
                    names['script']: newer['script']}
        if item['operation'] == 'edit':
            expected[names['feedback']] = ''
        if item['artifact']:
            if remote.get(names['video_digest']) != item['artifact']['sha256'] and not CopyOnlyWeekWriter._same_value(
                    remote.get(names['video_digest']), item['artifact']['sha256']):
                raise RuntimeFault('WORKBENCH_BOARD_VERSION_CHANGED')

        actual = {name: value for name, value in expected.items()
                  if not _same(name, remote.get(name), value, names['status'])}
        if actual:
            prior = {names['status']: old_status, names['review_revision']: str(item['old_revision']),
                     names['script']: previous['script'],
                     names['script_digest']: hashlib.sha256(previous['script'].encode()).hexdigest()}
            # Never overwrite independently edited review feedback.
            if not all(_same(name, remote.get(name), value, names['status']) for name, value in prior.items()) or (
                    item['old_state'] != 'rejected' and not CopyOnlyWeekWriter._same_value(remote.get(names['feedback']), '')):
                raise RuntimeFault('WORKBENCH_BOARD_VERSION_CHANGED')
            with store.connect() as db:
                check(db, item)
                reserved = {**item, 'state': 'in_flight', 'fields_sha256': fingerprint(actual)}
                save(db, key, reserved)
            item = reserved
            writes = 1
            client.update_record(base, table, record_id, actual)
            remote = client.record(base, table, record_id)['fields']
            if not all(_same(name, remote.get(name), value, names['status']) for name, value in expected.items()):
                raise RuntimeFault('WORKBENCH_BOARD_READBACK_CHANGED')
        with store.connect() as db:
            check(db, item)
            save(db, key, {**item, 'state': 'synced'})
            if item['operation'] == 'edit':
                save(db, source_key(project, item['task']), {'record_id': record_id})
                source = {'record_id': record_id, 'task': item['task'], 'revision': item['revision']}
                save(db, 'native:record:' + project + ':' + record_id, source)
                snapshot = {**source, 'state': item['new_state'], 'input_digest': item['input_digest'], 'artifact': None}
                save(db, sync_key(project, item['task'], item['revision'], 'script'),
                     {'snapshot': snapshot, 'steps': {}, 'complete': True})
        return {'status': 'synced', 'feishu_writes': writes}
    except Exception as error:
        # Never mark a possibly sent operation pending again. Other operations
        # may proceed; the ambiguous receipt remains for operator inspection.
        with store.connect() as db:
            if meta(db, key) == item:
                save(db, key, {**item, 'state': 'reconciliation_required', 'failure': failure_context(error)})
        return {'status': 'attention_required', 'automatic_resubmit': False, 'feishu_writes': writes}
