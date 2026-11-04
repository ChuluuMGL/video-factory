"""Explicit local Feishu receiver, supervised by the owning local runtime.

No public listener and no n8n process. Only authenticated official SDK events
enter the review queue; each callback rechecks the runtime capability/context.
"""
import argparse
import json
import os
from pathlib import Path
import threading

from .dispatch import authorize
from .feishu_bridge import meta
from .feishu_native_review import NativeReview
from .feishu_native_sync import NativeSync
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault, RuntimeStore, identifier


def context(store, project, token, key):
    native = NativeSync(store, key)
    with store.connect() as db:
        authorize(db, token, project)
        if meta(db, 'local:fake-only:v1'):
            raise RuntimeFault('LOCAL_FAKE_PROFILE_NO_EXTERNAL_IO')
        configured = meta(db, 'native:config:' + project)
        if not configured or not configured.get('enabled'):
            raise RuntimeFault('LOCAL_FEISHU_NATIVE_NOT_CONFIGURED')
        current = native.results.context(db, project)
        if configured['context'] != current:
            raise RuntimeFault('FEISHU_NATIVE_CONTEXT_CHANGED')
    return current


def callback(store, project, token, key, expected):
    receiver = NativeReview(store, client_factory=lambda _: None)
    def receive(data):
        import lark_oapi as lark
        if context(store, project, token, key) != expected:
            raise RuntimeFault('FEISHU_NATIVE_CONTEXT_CHANGED')
        return receiver.enqueue_verified(project, json.loads(lark.JSON.marshal(data)))
    return receive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--execution-key-file', type=Path, required=True)
    parser.add_argument('--master-key-file', type=Path, required=True)
    parser.add_argument('--parent-pid', type=int, required=True)
    args = parser.parse_args()
    identifier(args.project)
    if args.parent_pid <= 1 or os.getppid() != args.parent_pid:
        raise RuntimeFault('LOCAL_FEISHU_PARENT_REQUIRED')
    if os.environ.get('VF_DATABASE_URL_FILE') or os.environ.get('VF_CONTAINER_MODE') == '1':
        raise RuntimeFault('LOCAL_SQLITE_ENVIRONMENT_REQUIRED')
    store = RuntimeStore(args.root)
    token = secret_input(args.execution_key_file, '')
    key = secret_input(args.master_key_file, '')
    expected = context(store, args.project, token, key)
    def watch_parent():
        while True:
            threading.Event().wait(1)
            if os.getppid() != args.parent_pid:
                os._exit(0)
            try:
                if context(store, args.project, token, key) != expected:
                    os._exit(0)
            except Exception:
                os._exit(0)
    threading.Thread(target=watch_parent, daemon=True).start()
    profile = expected['app_profile']
    import lark_oapi as lark
    handler = (lark.EventDispatcherHandler.builder('', '')
               .register_p2_drive_file_bitable_record_changed_v1(
                   callback(store, args.project, token, key, expected)).build())
    client = lark.ws.Client(profile['app_id'],
                           store.resolve_secret(profile['credential_ref'][7:], key),
                           event_handler=handler, log_level=lark.LogLevel.ERROR)
    client.start()


if __name__ == '__main__':
    main()
