"""Container-local authenticated project import. Private input is stdin only."""
import hashlib
import json
import sys
from .onboarding import read_json, SetupError
from .postgres_store import PostgresStore
from .runtime_store import RuntimeFault
from .setup_project import import_project, inspect_project


def main():
    store = PostgresStore('/state')
    payload = read_json(sys.stdin)
    if not isinstance(payload, dict) or set(payload) != {'action', 'session', 'password'}:
        raise RuntimeFault('SETUP_IMPORT_FIELDS_INVALID')
    if payload['action'] not in ('apply', 'status'):
        raise RuntimeFault('SETUP_IMPORT_ACTION_INVALID')
    token = store.login('admin', payload['password'])['token']
    try:
        operation = import_project if payload['action'] == 'apply' else inspect_project
        return operation(store, token, payload['session'])
    finally:
        with store.connect() as db:
            db.execute('DELETE FROM sessions WHERE digest=?', (hashlib.sha256(token.encode()).hexdigest(),))


if __name__ == '__main__':
    try:
        print(json.dumps(main()))
    except (RuntimeFault, SetupError) as error:
        print(json.dumps({'error': str(error)}))
    except Exception:
        print(json.dumps({'error': 'SETUP_PROJECT_OPERATION_FAILED'}))
