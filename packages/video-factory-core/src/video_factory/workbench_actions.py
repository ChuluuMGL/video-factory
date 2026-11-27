"""Project-scoped, durable confirmations for local workbench actions."""
from difflib import unified_diff
import hashlib
import json
import re
import secrets
import time

from .agent_plans import VideoPlans, authorize
from .feishu_bridge import meta, save
from .runtime_store import RuntimeFault, canonical, fingerprint, identifier
from .worker import job_key
from .workbench_views import SHA, STAGES, STATUS, brief, envelope, iso, text, notes_for, stage_status, version

ROLES = {'repair', 'review', 'edit', 'cost'}
PRINCIPAL = re.compile('auth-[a-f0-9]{64}')
VERSION = re.compile(r'rev-[1-9][0-9]*-[a-f0-9]{64}')
CONFIRMATION = re.compile('cf_[a-f0-9]{48}')
RISK = re.compile('全网第一|绝对第一|最有效|百分之百|100%有效|国家级')
LEADING_RISK_MODIFIER = re.compile(
    rf'^([ \t]*)(?:全网(?=绝对第一))?(?:{RISK.pattern})[ \t]*的[ \t]*', re.MULTILINE)
HOURS = re.compile(r'(\d+)\s*小时')
FIELDS = {
    'stats': {'window', 'stage'},
    'failures': {'kinds', 'stuck_minutes', 'cursor', 'limit'},
    'reviews': {'stage', 'cursor', 'limit'},
    'repair/preview': {'target_type', 'target_id', 'expected_version', 'repair_action', 'instruction', 'reschedule'},
    'repair/apply': {'confirmation_id', 'proposal_sha256', 'expected_digest', 'expected_version', 'accept_provider_charges'},
    'script/prereview': {'task_key', 'expected_version', 'focus'},
    'script/edit-preview': {'task_key', 'expected_version', 'instruction'},
    'script/edit-commit': {'confirmation_id', 'draft_sha256', 'expected_digest', 'expected_version'},
    'review/prepare': {'task_key', 'expected_version', 'stage', 'decision', 'feedback', 'annotations',
                       'note_statuses', 'advance_next_stage'},
    'review/commit': {'confirmation_id', 'proposal_sha256', 'expected_digest', 'expected_version', 'accept_provider_charges'},
    'plan/approve': {'phase', 'plan_type', 'plan_id', 'plan_sha256', 'confirmation_id',
                     'expected_digest', 'proposal_sha256', 'expected_version', 'accept_provider_charges'},
}


def require(condition, error):
    if not condition:
        raise RuntimeFault(error)


def pattern(value, regex):
    return isinstance(value, str) and regex.fullmatch(value) is not None


def boolean(body, key):
    value = body.get(key, False)
    require(type(value) is bool, 'WORKBENCH_FIELDS_INVALID')
    return value


def bounded(value, size, *, nonempty=False):
    require(isinstance(value, str) and len(value) <= size and (not nonempty or value.strip()),
            'WORKBENCH_FIELDS_INVALID')
    return value


def authorize_role(db, token, project, principal, role):
    grant, _ = authorize(db, token, project, write=True, workbench=True)
    policy = meta(db, 'workbench:delegation:' + project) or {}
    require(pattern(principal, PRINCIPAL) and grant.get('workbench_principal') == principal
            and role in grant.get('workbench_roles', []) and role in policy.get(principal, []),
            'AUTH_WORKBENCH_ROLE_DENIED')
    return principal


def cost_gate(paid):
    return {'may_incur_charges': paid, 'quote_available': False, 'amount': None, 'currency': None,
            **({'approval_basis': 'one_submission_at_configured_provider_rate', 'max_submissions': 1} if paid else {})}


def row_for(store, db, project, task, expected):
    row = db.execute('SELECT * FROM tasks WHERE project=? AND id=? ORDER BY revision DESC LIMIT 1',
                     (project, task)).fetchone()
    require(row is not None and pattern(expected, VERSION) and version(row) == expected,
            'WORKBENCH_VERSION_CHANGED')
    return store._current(db, project, task, row['revision'])


def task_id(project, key):
    require(isinstance(key, str) and key.startswith(project + ':'), 'WORKBENCH_TASK_SCOPE_DENIED')
    return identifier(key.removeprefix(project + ':'))


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def plan_for(db, project, kind, plan_id):
    require(kind in ('script', 'video') and pattern(plan_id, SHA), 'WORKBENCH_PLAN_INVALID')
    plan = meta(db, f'agent:{kind}:plan:{plan_id}')
    require(plan and plan.get('project') == project, 'WORKBENCH_PLAN_INVALID')
    return plan


def video_queue(store, db, project, actor, row, request):
    plans = VideoPlans(store, project)
    prepared, profile_digest = plans._prepare(db, actor, row['id'], row['revision'])
    plan_id = fingerprint([project, request])
    saved = {'project': project, 'task': row['id'], 'revision': row['revision'],
             'request_id': request, 'plan_id': plan_id, 'created_by': actor,
             'expires_at': time.time() + 3600, 'profile_digest': profile_digest,
             'worker_plan': prepared['plan'], 'worker_plan_sha256': prepared['request_plan_sha256']}
    saved['plan_sha256'] = fingerprint(saved)
    key = job_key(project, row['id'], row['revision'])
    require(not meta(db, key) and not meta(db, 'agent:video:plan:' + plan_id), 'WORKBENCH_PLAN_EXISTS')
    save(db, key, {'plan': saved['worker_plan'], 'plan_sha256': saved['worker_plan_sha256'],
                   'expires_at': saved['expires_at'], 'provider_receipt': None, 'agent_plan_id': plan_id})
    save(db, 'agent:video:plan:' + plan_id, {**saved, 'approved_by': actor, 'submitted': True})
    store.audit(db, actor, 'workbench_video_one_submission_approved', project, row['id'], row['revision'])
    return {'plan_id': plan_id, 'plan_sha256': saved['plan_sha256'], 'enqueued': True,
            'video_spending_approved': True, 'provider_requests': 0}


def risks(script, facts):
    fact_hours = set(HOURS.findall(facts))
    result = []
    for number, line in enumerate(script.splitlines(), 1):
        if RISK.search(line):
            result.append({'severity': 'high', 'line': number, 'quote': text(line),
                           'reason': '绝对化表述', 'suggestion': '删除或改用可核验表述'})
        if len(fact_hours) == 1 and any(number not in fact_hours for number in HOURS.findall(line)):
            result.append({'severity': 'medium', 'line': number, 'quote': text(line),
                           'reason': '时长与商品事实不一致', 'suggestion': '按商品事实核对时长'})
        if len(result) >= 30:
            break
    return result[:30]


def revised(script, instruction, facts):
    result = script
    if re.search('绝对|违禁|绝对化', instruction):
        result = LEADING_RISK_MODIFIER.sub(r'\1', result)
        result = RISK.sub('', result)
    pair = re.fullmatch(r'(?:将|把)“([^”]{1,300})”改为“([^”]{1,300})”[。]?', instruction)
    if pair and pair[1] in result:
        result = result.replace(pair[1], pair[2], 1)
    if '时长' in instruction or '规格' in instruction or '事实' in instruction:
        fact_hours = set(HOURS.findall(facts))
        if len(fact_hours) == 1:
            result = HOURS.sub(next(iter(fact_hours)) + ' 小时', result)
    length = re.search(r'(?:开场|首行).*?(\d{1,2})\s*字', instruction)
    if length and 1 <= int(length[1]) <= 80:
        lines = result.split('\n')
        lines[0] = lines[0][:int(length[1])]
        result = '\n'.join(lines)
    require(result.strip() and result != script and len(result) <= 12000, 'WORKBENCH_EDIT_RULE_UNAVAILABLE')
    return result


class WorkbenchActions:
    def __init__(self, store, project, script_plans=None, *, video_plans_enabled=False, clock=time.time):
        self.store, self.project, self.script_plans, self.clock = store, project, script_plans, clock
        self.video_plans_enabled = video_plans_enabled

    def read(self, db, route, body, media_preview):
        from .workbench_views import projection
        return projection(db, self.project, route, body, self.clock(), media_preview=media_preview)

    @staticmethod
    def _envelope(db, skill, project, at, result):
        native = meta(db, 'native:config:' + project) or {}
        board = 'feishu_bitable' if native.get('enabled') else 'self_hosted'
        return envelope(skill, project, at, result, board=board)

    def _preview(self, db, kind, principal, row, payload, paid, summary, *, digest=None):
        ref = 'cf_' + secrets.token_hex(24)
        expiry = self.clock() + 300
        expected = version(row)
        binding = fingerprint(meta(db, 'feishu:binding:' + self.project))
        native = fingerprint(meta(db, 'native:config:' + self.project))
        profile = fingerprint(meta(db, 'video:profile:' + self.project)) if paid else None
        proposal = digest or fingerprint([kind, self.project, principal, expected, payload, binding, native, profile])
        value = {'kind': kind, 'principal': principal, 'task': row['id'], 'revision': row['revision'],
                 'expected': expected, 'digest': proposal, 'expires_at': expiry, 'payload': payload,
                 'binding': binding, 'native': native, 'profile': profile, 'paid': paid}
        save(db, f'workbench:confirm:{self.project}:{ref}', value)
        return {'required': True, 'confirmation_id': ref, 'proposal_sha256': proposal,
                'expected_digest': proposal, 'expected_version': expected, 'expires_at': iso(expiry),
                'summary': summary, 'cost_gate': cost_gate(paid)}

    def _open(self, db, kind, principal, body):
        ref = body.get('confirmation_id')
        require(pattern(ref, CONFIRMATION), 'WORKBENCH_CONFIRMATION_REQUIRED')
        key = f'workbench:confirm:{self.project}:{ref}'
        saved = meta(db, key)
        require(saved and saved['principal'] == principal and saved['kind'] == kind,
                'WORKBENCH_CONFIRMATION_REQUIRED')
        digests = [body[name] for name in ('expected_digest', 'proposal_sha256', 'draft_sha256') if name in body]
        require(digests and all(digest == saved['digest'] for digest in digests)
                and body.get('expected_version') == saved['expected'], 'WORKBENCH_CONFIRMATION_CHANGED')
        signature = fingerprint(body)
        if 'receipt' in saved:
            require(signature == saved['signature'], 'WORKBENCH_CONFIRMATION_REPLAY_CONFLICT')
            return key, saved, None
        require(saved['expires_at'] > self.clock(), 'WORKBENCH_CONFIRMATION_EXPIRED')
        payload = saved['payload']
        if kind == 'approve' and payload['plan_type'] == 'script' and not db.execute(
                'SELECT 1 FROM tasks WHERE project=? AND id=?', (self.project, saved['task'])).fetchone():
            plan = plan_for(db, self.project, 'script', payload['plan_id'])
            row = self._first_script_row(db, plan['source_plan'], saved['expected'])
        else:
            row = row_for(self.store, db, self.project, saved['task'], saved['expected'])
        require(saved['binding'] == fingerprint(meta(db, 'feishu:binding:' + self.project))
                and saved['native'] == fingerprint(meta(db, 'native:config:' + self.project))
                and (not saved['paid'] or saved['profile'] == fingerprint(meta(db, 'video:profile:' + self.project))),
                'WORKBENCH_CONFIRMATION_CHANGED')
        return key, saved, row

    def action(self, route, token, principal, body):
        require(not set(body) - FIELDS[route], 'WORKBENCH_FIELDS_INVALID')
        role = {'repair': 'repair', 'script': 'edit', 'review': 'review', 'plan': 'cost'}[route.split('/')[0]]
        if route == 'script/prereview':
            with self.store.connect() as db:
                authorize(db, token, self.project, workbench=True)
                return self._script(db, route, principal, body)
        # Script approval revalidates the exact Feishu identity and source
        # outside the SQLite transaction, then fences the result inside it.
        fresh = None
        if route == 'plan/approve' and body.get('phase') == 'commit':
            with self.store.connect() as db:
                authorize_role(db, token, self.project, principal, 'cost')
                _, saved, row = self._open(db, 'approve', principal, body)
                payload = saved['payload']
                if row is not None:
                    require(body.get('accept_provider_charges') is True, 'WORKBENCH_COST_CONSENT_REQUIRED')
                    require(all(body.get(k) == payload[k] for k in ('plan_type', 'plan_id', 'plan_sha256')),
                            'WORKBENCH_PLAN_CHANGED')
                if row is not None and payload['plan_type'] == 'script':
                    require(self.script_plans is not None, 'AGENT_SCRIPT_PLANS_DISABLED')
                    plan = plan_for(db, self.project, 'script', payload['plan_id'])
                else:
                    plan = None
            if plan:
                fresh = self.script_plans._fresh(plan['request'], plan)
        with self.store.connect() as db:
            actor = authorize_role(db, token, self.project, principal, role)
            if route.endswith('/preview') or route == 'script/edit-preview':
                return (self._script(db, route, actor, body) if role == 'edit'
                        else self._repair_preview(db, actor, body))
            if route == 'review/prepare':
                return self._review_preview(db, actor, body)
            if route == 'plan/approve' and body.get('phase') == 'prepare':
                return self._approve_preview(db, actor, body)
            return self._commit(db, route, token, actor, body, fresh)

    def _script(self, db, route, actor, body):
        project = self.project
        task = task_id(project, body.get('task_key'))
        row = row_for(self.store, db, project, task, body.get('expected_version'))
        require(row['state'] in ('awaiting_script_review', 'rejected') and stage_status(db, row)[0] == 'script',
                'WORKBENCH_EDIT_STATE_DENIED')
        current = json.loads(row['input'])
        script = current['script']
        setup = meta(db, 'setup:project:' + project) or {}
        facts = next((p['truth_source'] for p in setup.get('configuration', {}).get('project', {}).get('products', [])
                      if p['sku_id'] == current['sku_id']), '')
        result = {'task_key': project + ':' + task, 'content_is_instruction': False, 'model_calls': 0}
        if route == 'script/prereview':
            bounded(body.get('focus', ''), 2000)
            notes = risks(script, facts)
            result.update(mode='ai_prereview', revision=row['revision'], is_human_approval=False, risks=notes,
                          brief=brief(f'规则预审发现 {len(notes)} 项风险。', '按行核对。', '仍需人工审核。'))
        else:
            instruction = bounded(body.get('instruction'), 2000, nonempty=True)
            draft = revised(script, instruction, facts)
            patch = '\n'.join(unified_diff([text(line, 1000) for line in script.splitlines()],
                                           [text(line, 1000) for line in draft.splitlines()],
                                           fromfile='current', tofile='draft', lineterm=''))[:16000]
            result.update(mode='edit_preview', base_revision=row['revision'], target_revision=row['revision'] + 1,
                          diff={'format': 'unified', 'base_sha256': sha(script), 'draft_sha256': sha(draft), 'patch': patch},
                          confirmation=self._preview(db, 'edit', actor, row, {'draft': draft}, False,
                                                     '确认保存脚本新版本', digest=sha(draft)),
                          brief=brief('脚本改稿预览已生成。', '核对差异后确认。', '尚未写入任务账本。'))
        return self._envelope(db, 'review_or_edit_script', project, self.clock(), result)

    def _repair_preview(self, db, actor, body):
        bounded(body.get('instruction', ''), 2000)
        require(body.get('target_type') == 'task_state', 'WORKBENCH_WORKFLOW_PATCH_UNAVAILABLE')
        row = row_for(self.store, db, self.project, task_id(self.project, body.get('target_id')), body.get('expected_version'))
        require(row['state'] not in ('submission_unknown', 'script_submission_unknown'),
                'WORKBENCH_SUBMISSION_UNKNOWN_RECONCILE_FIRST')
        schedule = boolean(body, 'reschedule')
        require(not schedule or self.video_plans_enabled, 'AGENT_PLANS_DISABLED')
        require(row['state'] == 'failed' and body.get('repair_action') == 'requeue_failed_task',
                'WORKBENCH_REPAIR_STATE_DENIED')
        previous = meta(db, job_key(self.project, row['id'], row['revision'])) or {}
        require(previous.get('failure_code') or db.execute(
            "SELECT 1 FROM audit WHERE project=? AND task=? AND revision=? AND action='provider_failed_no_retry'",
            (self.project, row['id'], row['revision'])).fetchone(), 'WORKBENCH_DEFINITE_VIDEO_FAILURE_REQUIRED')
        return self._envelope(db, 'repair_and_schedule', self.project, self.clock(), {
            'phase': 'preview', 'brief': brief('已准备修复预览。', '核对新版本及费用。'),
            'diff': {'format': 'unified', 'summary': '保留旧失败版本，创建新版本',
                     'patch': '- state: failed\n+ state: ' + ('ready (approved queue)' if schedule else 'ready')},
            'confirmation': self._preview(db, 'repair', actor, row, {'schedule': schedule}, schedule, '确认修复任务'),
        })

    def _review_preview(self, db, actor, body):
        project = self.project
        row = row_for(self.store, db, project, task_id(project, body.get('task_key')), body.get('expected_version'))
        stage, decision = body.get('stage'), body.get('decision')
        require(stage in STAGES and row['state'] == 'awaiting_' + stage + '_review'
                and decision in ('accept', 'reject'), 'WORKBENCH_REVIEW_STATE_DENIED')
        annotations = body.get('annotations', [])
        statuses = body.get('note_statuses', [])
        feedback = bounded(body.get('feedback', ''), 8000)
        lines = json.loads(row['input'])['script'].splitlines()
        require(isinstance(annotations, list) and len(annotations) <= 8 and isinstance(statuses, list) and len(statuses) <= 8,
                'WORKBENCH_REVIEW_NOTES_INVALID')
        require('[VF_' not in feedback, 'WORKBENCH_REVIEW_NOTES_INVALID')
        known = notes_for(db, project, row['id'], row['revision'])
        prepared = {}
        for note in statuses:
            require(isinstance(note, dict) and set(note) == {'id', 'status'} and pattern(note['id'], SHA)
                    and note['id'] in known and note['id'] not in prepared
                    and note['status'] in ('resolved', 'pending', 'needs_changes'), 'WORKBENCH_REVIEW_NOTES_INVALID')
            prepared[note['id']] = note['status']
        for note in annotations:
            require(isinstance(note, dict) and set(note) == {'line', 'comment'} and type(note['line']) is int
                    and 1 <= note['line'] <= len(lines), 'WORKBENCH_REVIEW_NOTES_INVALID')
            bounded(note['comment'], 1000, nonempty=True)
        require(decision == 'accept' or feedback.strip() or annotations, 'REVIEW_FEEDBACK_REQUIRED')
        if decision == 'accept':
            remaining = sum(status != 'resolved' for status in known.values())
            delta = sum((updated != 'resolved') - (known[k] != 'resolved') for k, updated in prepared.items())
            require(not annotations and remaining + delta == 0, 'WORKBENCH_REVIEW_NOTES_UNRESOLVED')
        advance = boolean(body, 'advance_next_stage')
        require(not advance or decision == 'accept' and stage in ('script', 'storyboard'), 'WORKBENCH_ADVANCE_INVALID')
        value = {'stage': stage, 'decision': decision, 'feedback': feedback, 'advance': advance,
                 'notes': {**known, **prepared}, 'annotations': annotations, 'notes_digest': fingerprint(known)}
        return self._envelope(db, 'submit_review_decision', project, self.clock(), {
            'phase': 'prepare', 'brief': brief('审核结论待确认。', '确认前核对版本和费用。'),
            'confirmation': self._preview(db, 'review', actor, row, value, advance, '确认审核结论'),
        })

    def _approve_preview(self, db, actor, body):
        require(not ({'confirmation_id', 'expected_digest', 'proposal_sha256', 'accept_provider_charges'} & set(body)),
                'WORKBENCH_FIELDS_INVALID')
        kind, plan_id, plan_sha = body.get('plan_type'), body.get('plan_id'), body.get('plan_sha256')
        require(kind != 'video' or self.video_plans_enabled, 'AGENT_PLANS_DISABLED')
        plan = plan_for(db, self.project, kind, plan_id)
        require(plan.get('plan_sha256') == plan_sha and not plan.get('submitted') and not plan.get('revoked')
                and plan['expires_at'] > self.clock(), 'WORKBENCH_PLAN_CHANGED')
        source = plan['source_plan'] if kind == 'script' else plan
        current = db.execute('SELECT state FROM tasks WHERE project=? AND id=? ORDER BY revision DESC LIMIT 1',
                             (self.project, source['task'])).fetchone()
        if kind == 'script' and current is None:
            row = self._first_script_row(db, source, body.get('expected_version'))
        else:
            row = row_for(self.store, db, self.project, source['task'], body.get('expected_version'))
            require(row['revision'] == source['revision'] - (1 if kind == 'script' else 0),
                    'WORKBENCH_PLAN_CHANGED')
        return self._envelope(db, 'submit_review_decision', self.project, self.clock(), {
            'phase': 'prepare', 'brief': brief('费用审批待确认。', '核对计划摘要和单次调用范围。'),
            'confirmation': self._preview(db, 'approve', actor, row, {'plan_type': kind, 'plan_id': plan_id,
                                         'plan_sha256': plan_sha}, True, '确认单次费用审批'),
        })

    def _first_script_row(self, db, source, expected):
        from .workbench_views import first_script_row
        row = first_script_row(db, self.project, source)
        require(source['revision'] == 1 and expected == version(row), 'WORKBENCH_VERSION_CHANGED')
        return row

    def _commit(self, db, route, token, actor, body, fresh):
        kind = {'repair/apply': 'repair', 'script/edit-commit': 'edit',
                'review/commit': 'review', 'plan/approve': 'approve'}.get(route)
        require(kind is not None and (kind != 'approve' or body.get('phase') == 'commit'), 'WORKBENCH_FIELDS_INVALID')
        key, saved, row = self._open(db, kind, actor, body)
        if row is None:
            result = {**saved['receipt'], 'replayed': True}
            if result.get('board_writeback') == 'reconciliation_required':
                from .workbench_writeback import receipt_status
                board, next_plan = receipt_status(db, self.project, body['confirmation_id'])
                result['board_writeback'] = board
                if next_plan:
                    result['next_stage_plan'] = next_plan
            return result
        payload = saved['payload']
        consent = boolean(body, 'accept_provider_charges')
        paid = False
        if saved['paid'] and consent:
            try:
                authorize_role(db, token, self.project, actor, 'cost')
                paid = True
            except RuntimeFault:
                if kind != 'review':
                    raise
        if saved['paid'] and not paid and kind != 'review':
            raise RuntimeFault('WORKBENCH_COST_CONSENT_REQUIRED')
        project, task, revision = self.project, row['id'], row['revision']
        ref = body['confirmation_id']
        result = {'phase': 'commit', 'replayed': False, 'audit_id': ref, 'provider_requests': 0,
                  'automatic_resubmit': False}
        if kind == 'approve':
            plan_type = payload['plan_type']
            plan_id = payload['plan_id']
            plan = plan_for(db, project, plan_type, plan_id)
            require(plan['plan_sha256'] == payload['plan_sha256'] and not plan.get('submitted'),
                    'WORKBENCH_PLAN_CHANGED')
            require(plan_type != 'video' or self.video_plans_enabled, 'AGENT_PLANS_DISABLED')
            if plan_type == 'script':
                require(fresh and fresh.get('plan_sha256') == plan['source_plan_sha256'],
                        'WORKBENCH_PLAN_CHANGED')
                self.script_plans._active(plan)
                self.script_plans.jobs._fence_prepared(db, plan['source_plan'])
            else:
                VideoPlans(self.store, project)._fence(db, actor, plan)
                require(not meta(db, job_key(project, task, revision)), 'WORKBENCH_PLAN_EXISTS')
            plan.setdefault('approved_by', actor)
            save(db, 'agent:' + plan_type + ':plan:' + plan_id, plan)
            result.update(approved=True, plan_id=plan_id, plan_sha256=plan['plan_sha256'], enqueued=False)
        elif kind == 'edit':
            current = json.loads(row['input'])
            require(body.get('draft_sha256') == sha(payload['draft']), 'WORKBENCH_CONFIRMATION_CHANGED')
            if row['state'] == 'awaiting_script_review':
                db.execute("UPDATE tasks SET state='rejected' WHERE project=? AND id=? AND revision=?",
                           (project, task, revision))
            self.store._create_task(db, actor, project, task, {**current, 'script': payload['draft']}, revision)
            notes = notes_for(db, project, task, revision)
            if notes:
                save(db, f'workbench:notes:{project}:{task}:{revision + 1}',
                     dict.fromkeys(notes, 'pending'))
            newer = self.store._current(db, project, task, revision + 1)
            from .workbench_writeback import enqueue
            result.update(mode='edit_commit', new_revision=revision + 1, new_version_token=version(newer),
                          draft_sha256=sha(payload['draft']), content_is_instruction=False,
                          board_writeback=enqueue(db, project, ref, row, newer, 'edit', ''),
                          video_spending_approved=False)
        elif kind == 'review':
            prior_notes = notes_for(db, project, task, revision)
            require(fingerprint(prior_notes) == payload['notes_digest'], 'WORKBENCH_REVIEW_NOTES_CHANGED')
            feedback = payload['feedback']
            annotations = payload['annotations']
            notes = payload['notes']
            for note in annotations:
                notes[fingerprint([ref, note])] = 'pending'
            if annotations:
                feedback = bounded(feedback + '\n' + '\n'.join(
                    f"第 {note['line']} 行：{note['comment']}" for note in annotations), 8000)
            stage, decision = payload['stage'], payload['decision']
            receipt = self.store._review(db, actor, ref, project, task, revision, stage, decision, feedback)
            save(db, f'workbench:notes:{project}:{task}:{revision}', notes)
            newer = self.store._current(db, project, task, revision)
            from .workbench_writeback import enqueue
            deferred = {'principal': actor, 'expires_at': saved['expires_at'],
                        'profile_sha256': saved['profile'],
                        'grant_key': hashlib.sha256(token.encode()).hexdigest()} if (
                            self.video_plans_enabled and paid and payload['advance']) else None
            writeback = enqueue(db, project, ref, row, newer, 'review', feedback, deferred)
            next_plan = {'plan_id': None, 'enqueued': False, 'video_spending_approved': False}
            if deferred and writeback == 'synced':
                next_plan = video_queue(self.store, db, project, actor, newer, ref)
            result.update(review_recorded=True, new_canonical_status=STATUS[receipt['state']][1],
                          board_writeback=writeback, next_stage_plan=next_plan)
        else:
            payload_data = json.loads(row['input'])
            self.store._create_task(db, actor, project, task, payload_data, revision)
            db.execute("UPDATE tasks SET state='ready' WHERE project=? AND id=? AND revision=?",
                       (project, task, revision + 1))
            newer = self.store._current(db, project, task, revision + 1)
            next_plan = video_queue(self.store, db, project, actor, newer, ref) if paid else None
            result.update(applied=True, new_version=version(newer), scheduled=next_plan is not None,
                          next_stage_plan=next_plan)
        skill = {'repair': 'repair_and_schedule', 'edit': 'review_or_edit_script',
                 'review': 'submit_review_decision', 'approve': 'submit_review_decision'}[kind]
        result = self._envelope(db, skill, project, self.clock(), {
            **result, 'brief': brief('已记录已确认的操作。', '回读结果。'),
        })
        self.store.audit(db, actor, 'workbench_' + kind, project, task, revision)
        save(db, key, {**saved, 'signature': fingerprint(body), 'receipt': result})
        return result
