"""Import a reviewed Setup into the customer ledger, without activating providers.

Shared by SQLite tests and PostgreSQL deployments. Metadata lives in schema-1
meta rows, so old releases can retain it through backup/migration/rollback.
"""
from .onboarding import describe
from .runtime_store import RuntimeFault, canonical, fingerprint
import json


DISABLED_CONFIGURATION = {'video_route': 'deferred', 'credential_ref': 'secret:unconfigured',
                          'billing_owner': 'unconfigured'}


def session_plan(session):
    result = describe(session)
    if result['status'] != 'plan_ready':
        raise RuntimeFault('SETUP_PLAN_INCOMPLETE')
    return result['plan']


def binding(configuration):
    return {group: configuration[group] for group in ('organization', 'deployment')}


def import_project(store, token, session):
    plan = session_plan(session)
    config = plan['configuration']
    project = config['project']['id']
    binding_json = canonical(binding(config))
    key = 'setup:project:' + project
    with store.connect() as db:
        actor = store.authorize(db, token, 'admin')
        deployment = db.execute("SELECT value FROM meta WHERE key='deployment'").fetchone()[0]
        if deployment != config['deployment']['id']:
            raise RuntimeFault('SETUP_DEPLOYMENT_MISMATCH')
        previous_binding = db.execute("SELECT value FROM meta WHERE key='setup:binding'").fetchone()
        if previous_binding and previous_binding[0] != binding_json:
            raise RuntimeFault('SETUP_CUSTOMER_BINDING_CONFLICT')
        previous = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        existing = db.execute('SELECT digest FROM projects WHERE id=?', (project,)).fetchone()
        if previous:
            stored = json.loads(previous[0])
            if (stored['plan_sha256'] != plan['plan_sha256'] or not existing
                    or existing[0] != stored['runtime_configuration_digest']):
                raise RuntimeFault('SETUP_PROJECT_CHANGED_RECONCILIATION_REQUIRED')
            reused = True
        else:
            if existing:
                raise RuntimeFault('SETUP_EXISTING_PROJECT_NOT_OWNED')
            runtime_digest = fingerprint(DISABLED_CONFIGURATION)
            stored = {'schema': 1, 'plan_sha256': plan['plan_sha256'], 'configuration': config,
                      'runtime_configuration_digest': runtime_digest}
            db.execute('INSERT INTO projects VALUES(?,?,?)',
                       (project, canonical(DISABLED_CONFIGURATION), runtime_digest))
            db.execute('INSERT INTO meta VALUES(?,?)', (key, canonical(stored)))
            db.execute("INSERT INTO meta VALUES('setup:binding',?) ON CONFLICT(key) DO NOTHING", (binding_json,))
            store.audit(db, actor, 'setup_project_import', project)
            reused = False
    return receipt(stored, reused=reused)


def receipt(stored, *, reused=None):
    config = stored['configuration']
    result = {'project': config['project']['id'], 'deployment': config['deployment']['id'],
              'plan_sha256': stored['plan_sha256'], 'sku_count': len(config['project']['products']),
              'project_configuration_saved': True, 'runtime_configuration_digest': stored['runtime_configuration_digest'],
              'requested_video_route': config['project']['video_route'], 'active_video_route': 'deferred',
              'credentials_resolved': False, 'feishu_identity': 'not_verified',
              'feishu_resources': 'not_created', 'paid_execution_enabled': False,
              'technical_canary': 'not_run', 'human_acceptance': 'not_run'}
    if reused is not None:
        result['reused'] = reused
    return result


def inspect_project(store, token, session):
    plan = session_plan(session)
    with store.connect() as db:
        store.authorize(db, token, 'admin')
        row = db.execute('SELECT value FROM meta WHERE key=?', ('setup:project:' + plan['configuration']['project']['id'],)).fetchone()
        if not row:
            raise RuntimeFault('SETUP_PROJECT_NOT_IMPORTED')
        stored = json.loads(row[0])
        if stored['plan_sha256'] != plan['plan_sha256']:
            raise RuntimeFault('SETUP_PROJECT_CHANGED_RECONCILIATION_REQUIRED')
        current = db.execute('SELECT digest FROM projects WHERE id=?', (plan['configuration']['project']['id'],)).fetchone()
        if not current or current[0] != stored['runtime_configuration_digest']:
            raise RuntimeFault('SETUP_PROJECT_CHANGED_RECONCILIATION_REQUIRED')
        return receipt(stored)
