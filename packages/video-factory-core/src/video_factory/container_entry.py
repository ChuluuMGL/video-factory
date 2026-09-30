"""Idempotent first bootstrap inside the installed image, never a paid worker."""
import json
import os
import sys
from pathlib import Path
from .postgres_store import PostgresStore
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault


def main():
    root = Path('/state')
    if sys.argv[1:] == ['--revoke-sessions']:
        store=PostgresStore(root)
        with store.connect() as db:
            db.execute('DELETE FROM sessions')
            store.invalidate_worker_approvals(db)
            store.audit(db,'os_owner','stack_restore_revoke_sessions')
        print(json.dumps({'sessions_revoked':True}))
        return
    deployment = os.environ['VF_DEPLOYMENT']
    try:
        store = PostgresStore(root)
    except RuntimeFault as error:
        if str(error) != 'RUNTIME_NOT_INSTALLED':
            raise
        store = PostgresStore.install(root, deployment, secret_input(Path('/run/secrets/bootstrap_password'), ''))
    if store.doctor()['deployment'] != deployment:
        raise RuntimeFault('DEPLOYMENT_MISMATCH')
    print(json.dumps({'bootstrap': 'ready', 'doctor': store.doctor()}))


if __name__ == '__main__':
    main()
