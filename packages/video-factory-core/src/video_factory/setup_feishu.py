"""Resumable connection questions and reviewed binding of an imported Setup.

Draft files contain no credentials or claims of remote verification. Live plans
bind the installed Setup, current binding, remote identity and resolved fields.
"""
import copy

from .onboarding import SessionStore, SetupError, validate_session
from .setup_project import session_plan
from .feishu_bridge import FeishuBridge, configuration, meta, save, target
from .feishu_client import FeishuClient, resource
from .runtime_store import RuntimeFault, fingerprint
from .feishu_provision import ProvisionClient, Provisioner, is_create, journal_key


QUESTIONS = (
    ('table_id', '任务所在的飞书表 ID（tbl 开头）', 'tbl'),
    ('task', '任务 ID 对应的文本字段 ID（fld 开头）', 'fld'),
    ('sku_id', 'SKU 对应的文本字段 ID（fld 开头）', 'fld'),
    ('script', '脚本对应的文本字段 ID（fld 开头）', 'fld'),
    ('source_revision', '来源版本对应的文本字段 ID（fld 开头）', 'fld'),
    ('submitters', '允许导入任务的员工 open_id 数组', 'ou_'),
)


def source_plan(setup):
    plan = session_plan(setup)
    config = plan['configuration']
    if config['project']['base_mode'] == 'bind':
        resource(config['project']['base_target'])
    resource(config['deployment']['feishu_tenant'])
    for key in ('script_reviewer', 'video_reviewer'):
        resource(config['project'][key].removeprefix('feishu:'), 'ou_')
    return plan


def answer_value(field, value):
    if field == 'workspace_kind':
        if value not in ('test', 'production'): raise SetupError('SETUP_WORKSPACE_KIND_INVALID')
        return value
    if field == 'folder_token':
        if value != '': resource(value)
        return value
    prefix = next((prefix for name, _, prefix in QUESTIONS if name == field), None)
    if prefix is None:
        raise SetupError('SETUP_FEISHU_UNKNOWN_FIELD')
    if field == 'submitters':
        if (not isinstance(value, list) or not 1 <= len(value) <= 100
                or not all(isinstance(v, str) for v in value) or len(set(value)) != len(value)):
            raise SetupError('SETUP_FEISHU_OPEN_ID_ARRAY_REQUIRED')
        for item in value:
            resource(item, prefix)
    else:
        resource(value, prefix)
    return value


def validate_draft(draft):
    if (not isinstance(draft, dict) or set(draft) != {'schema', 'revision', 'setup', 'answers'}
            or type(draft['schema']) is not int or draft['schema'] != 1
            or type(draft['revision']) is not int or draft['revision'] < 0
            or not isinstance(draft['answers'], dict)):
        raise SetupError('SETUP_FEISHU_SESSION_INVALID')
    validate_session(draft['setup'])
    source_plan(draft['setup'])
    allowed = {'workspace_kind', 'folder_token', 'submitters'} if is_create(draft) else {q[0] for q in QUESTIONS}
    if set(draft['answers']) - allowed: raise SetupError('SETUP_FEISHU_UNKNOWN_FIELD')
    for field, value in draft['answers'].items():
        answer_value(field, value)
    fields = [v for k, v in draft['answers'].items() if k in ('task', 'sku_id', 'script', 'source_revision')]
    if len(set(fields)) != len(fields):
        raise SetupError('SETUP_FEISHU_DUPLICATE_FIELD_MAPPING')


def describe(draft):
    validate_draft(draft)
    plan = source_plan(draft['setup'])
    questions = ((('workspace_kind', '新工作区用途：test 测试（两条测试任务） / production 正式（不放测试任务）', ''),
                  ('folder_token', '存放位置：输入飞书文件夹 Token，回车使用当前用户云空间根目录', ''),
                  QUESTIONS[-1]) if is_create(draft) else QUESTIONS)
    missing = [(key, label, prefix) for key, label, prefix in questions if key not in draft['answers']]
    question = None
    if missing:
        key, label, prefix = missing[0]
        item = {'type': 'string', 'pattern': '^' + prefix + '[A-Za-z0-9_-]{4,128}$'}
        schema = {'type': 'array', 'minItems': 1, 'maxItems': 100, 'uniqueItems': True, 'items': item} if key == 'submitters' else item
        if key == 'workspace_kind': schema = {'type': 'string', 'enum': ['test', 'production']}
        if key == 'folder_token': schema = {'type': 'string', 'pattern': '^([A-Za-z0-9_-]{4,128})?$'}
        question = {'field': key, 'question': label, 'input_schema': schema, 'accepts_secret_value': False}
    return {'status': 'needs_input' if missing else 'connection_draft_ready',
            'revision': draft['revision'], 'next_question': question,
            'setup_plan_sha256': plan['plan_sha256'], 'target': plan['target'],
            'credentials_saved': False, 'remote_verification': 'not_run',
            'business_ready': False, 'model_calls': 0, 'feishu_writes': 0}


class ConnectionSession(SessionStore):
    validate = staticmethod(validate_draft)

    def snapshot(self):
        # Operations consume a private, validated snapshot, never edit it.
        # Atomic configure saves mean an open fd sees one complete revision.
        # Do not create/open a writable lock in the read-only /work mount.
        return self._read()

    def start(self, source=None):
        source_plan(source)
        with self.locked():
            if self.path.exists() or self.path.is_symlink():
                draft = self._read()
                if source_plan(source)['plan_sha256'] != source_plan(draft['setup'])['plan_sha256']:
                    raise SetupError('SETUP_FEISHU_SOURCE_CHANGED_USE_NEW_SESSION')
                return draft
            draft = {'schema': 1, 'revision': 0, 'setup': copy.deepcopy(source), 'answers': {}}
            self._save(draft)
            return draft

    def answer(self, answers, expected_revision):
        with self.locked():
            draft = self._read()
            if type(expected_revision) is not int or expected_revision != draft['revision']:
                raise SetupError('SETUP_REVISION_CONFLICT')
            if not isinstance(answers, dict) or not answers:
                raise SetupError('SETUP_ANSWERS_OBJECT_REQUIRED')
            updated = copy.deepcopy(draft)
            for field, value in answers.items():
                updated['answers'][field] = answer_value(field, value)
            validate_draft(updated)
            if updated != draft:
                updated['revision'] += 1
                self._save(updated)
            return describe(updated)


def draft_binding(draft):
    if is_create(draft): raise RuntimeFault('SETUP_FEISHU_CREATION_RECEIPT_REQUIRED')
    if describe(draft)['status'] != 'connection_draft_ready':
        raise RuntimeFault('SETUP_FEISHU_QUESTIONS_INCOMPLETE')
    config = source_plan(draft['setup'])['configuration']
    script = config['project']['script_reviewer'].removeprefix('feishu:')
    video = config['project']['video_reviewer'].removeprefix('feishu:')
    answers = draft['answers']
    return configuration({'tenant_key': config['deployment']['feishu_tenant'],
                          'base_token': config['project']['base_target'], 'table_id': answers['table_id'],
                          'fields': {k: answers[k] for k in ('task', 'sku_id', 'script', 'source_revision')},
                          'submitters': answers['submitters'], 'reviewers': sorted({script, video}),
                          'script_reviewers': [script], 'video_reviewers': [video]})


class SetupFeishu:
    def __init__(self, store, *, client_factory=FeishuClient, provision_client_factory=ProvisionClient):
        self.store = store
        self.client_factory = client_factory
        self.provision_client_factory = provision_client_factory

    def context(self, db, admin, draft):
        actor = self.store.authorize(db, admin, 'admin')
        plan = source_plan(draft['setup'])
        project = plan['configuration']['project']['id']
        stored = meta(db, 'setup:project:' + project)
        if not stored or stored['plan_sha256'] != plan['plan_sha256'] or stored['configuration'] != plan['configuration']:
            raise RuntimeFault('SETUP_FEISHU_IMPORTED_PLAN_MISMATCH')
        deployment = db.execute("SELECT value FROM meta WHERE key='deployment'").fetchone()[0]
        if deployment != plan['configuration']['deployment']['id']:
            raise RuntimeFault('SETUP_DEPLOYMENT_MISMATCH')
        row = db.execute('SELECT digest FROM projects WHERE id=?', (project,)).fetchone()
        if not row:
            raise RuntimeFault('PROJECT_MISSING')
        current = meta(db, 'feishu:binding:' + project)
        return {'deployment': deployment, 'project': project, 'actor': actor,
                'setup_plan_sha256': plan['plan_sha256'], 'runtime_digest': row[0],
                'previous_binding': current, 'requires_reconfirmation': bool(meta(db, 'feishu:reconfirm:' + project))}

    def prepare(self, admin, draft, user):
        if is_create(draft): return Provisioner(self).prepare(admin, draft, user)
        binding = draft_binding(draft)
        with self.store.connect() as db:
            context = self.context(db, admin, draft)
        if context['previous_binding'] and target(context['previous_binding']) != target(binding):
            raise RuntimeFault('FEISHU_TARGET_IMMUTABLE_USE_NEW_PROJECT')
        client = self.client_factory(user)
        identity = client.identity()
        if identity['tenant_key'] != binding['tenant_key']:
            raise RuntimeFault('FEISHU_TENANT_OR_ROLE_DENIED')
        fields = FeishuBridge._schema(client, binding)
        with self.store.connect() as db:
            if self.context(db, admin, draft) != context:
                raise RuntimeFault('SETUP_FEISHU_CONTEXT_CHANGED')
        plan = {'context': context, 'binding': binding, 'verified_operator': identity,
                'resolved_fields': fields, 'draft_sha256': fingerprint(draft)}
        return {'plan': plan, 'plan_sha256': fingerprint(plan), 'business_ready': False,
                'reviewer_identity_acceptance': 'not_run', 'feishu_writes': 0, 'model_calls': 0}

    def apply(self, admin, draft, user, expected_plan):
        if is_create(draft): return Provisioner(self).apply(admin, draft, user, expected_plan)
        prepared = self.prepare(admin, draft, user)
        if expected_plan != prepared['plan_sha256']:
            raise RuntimeFault('SETUP_FEISHU_PLAN_CHANGED')
        plan = prepared['plan']
        with self.store.connect() as db:
            context = self.context(db, admin, draft)
            if context != plan['context']:
                raise RuntimeFault('SETUP_FEISHU_CONTEXT_CHANGED')
            project = context['project']
            save(db, 'feishu:binding:' + project, plan['binding'])
            db.execute('DELETE FROM meta WHERE key=?', ('feishu:reconfirm:' + project,))
            self.store.audit(db, context['actor'], 'setup_feishu_binding', project)
        return {'status': 'connection_binding_saved', 'project': project,
                'binding_sha256': fingerprint(plan['binding']), 'plan_sha256': expected_plan,
                'operator_identity_verified': True, 'field_schema_verified': True,
                'reviewer_identity_acceptance': 'not_run', 'business_ready': False,
                'feishu_writes': 0, 'model_calls': 0}

    def status(self, admin, draft):
        if is_create(draft):
            with self.store.connect() as db:
                context = self.context(db, admin, draft)
                journal = meta(db, journal_key(draft))
                recovery = bool(meta(db, 'setup:provision-recovery:'+context['project']))
            binding = journal.get('binding') if journal else None
            return {'project': context['project'], 'binding_matches_draft': bool(binding) and binding == context['previous_binding'] and journal['draft_sha256'] == fingerprint(draft),
                    'requires_reconfirmation': context['requires_reconfirmation'] or recovery,
                    'provisioning': journal, 'remote_verification': 'not_run_in_status',
                    'business_ready': False, 'model_calls': 0, 'feishu_writes': 0}
        binding = draft_binding(draft)
        with self.store.connect() as db:
            context = self.context(db, admin, draft)
        return {'project': context['project'], 'binding_matches_draft': context['previous_binding'] == binding,
                'binding_sha256': fingerprint(context['previous_binding']) if context['previous_binding'] else None,
                'requires_reconfirmation': context['requires_reconfirmation'],
                'remote_verification': 'not_run_in_status', 'business_ready': False,
                'feishu_writes': 0, 'model_calls': 0}


def interactive(store, *, read=input, write=print, next_step=None):
    write('欢迎连接飞书项目。租户、Base、分阶段审核人和 SKU 沿用已安装的 Setup。')
    write('本步骤只保存连接草稿；不收取密码或 Key。输入 :quit 保存退出。')
    while True:
        result = describe(store.read())
        question = result['next_question']
        if question is None:
            write(next_step or '连接草稿完成；下一步 connect 打开飞书授权向导，核对身份和字段后确认保存绑定；已有用户 token 也可继续 plan/apply。')
            return result
        try:
            write(question['question'])
            if question['field'] == 'submitters':
                write('终端可用英文逗号分隔；Agent JSON 必须提交数组。')
            raw = read('输入> ').strip()
            if raw == ':quit':
                return result
            if len(raw) > 16000:
                raise SetupError('SETUP_INPUT_TOO_LARGE')
            value = [v.strip() for v in raw.split(',')] if question['field'] == 'submitters' else raw
            store.answer({question['field']: value}, result['revision'])
        except (EOFError, KeyboardInterrupt):
            result['interrupted'] = True
            return result
        except (SetupError, RuntimeFault) as error:
            write('未保存此项：' + str(error))
