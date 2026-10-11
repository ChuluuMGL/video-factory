"""Private Feishu long-connection event receiver; no public callback URL.

Only the official SDK's authenticated dispatcher can enqueue review events.
The worker later performs live Base readback before changing the local ledger.
"""
import argparse
import inspect
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

from .feishu_bridge import meta
from .feishu_native_review import NativeReview
from .postgres_store import selected_store
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault, identifier


def _proxy_sdk_websocket():
    # lark-oapi 1.7.3 explicitly disables websockets 15's environment proxy.
    # Route its fixed Feishu WSS destination through our allowlisted CONNECT
    # relay instead of giving the event container unrestricted internet.
    import websockets
    if 'proxy' not in inspect.signature(websockets.connect).parameters:
        raise RuntimeFault('FEISHU_WS_PROXY_UNSUPPORTED')
    original = websockets.connect
    def connect(uri, *args, **kwargs):
        parsed = urlsplit(uri)
        if parsed.scheme != 'wss' or parsed.hostname != 'msg-frontier.feishu.cn' or parsed.port not in (None, 443):
            raise RuntimeFault('FEISHU_WS_HOST_DENIED')
        kwargs['proxy'] = 'http://egress:8443'
        return original(uri, *args, **kwargs)
    websockets.connect = connect


def serve(store, master_key, project):
    if os.environ.get('VF_CONTAINER_MODE') != '1' or os.environ.get('VF_WORKER_EGRESS') != '1':
        raise RuntimeFault('FEISHU_EVENTS_CONTAINER_REQUIRED')
    _proxy_sdk_websocket()
    import lark_oapi as lark
    receiver = NativeReview(store, client_factory=lambda _: None)
    while True:
        with store.connect() as db:
            config = meta(db, 'native:config:'+project)
            profile = meta(db, 'setup:feishu-app:'+project)
        if not config or not config.get('enabled') or not profile or profile['app_id'] != config['app_id']:
            time.sleep(15); continue
        secret = store.resolve_secret(profile['credential_ref'][7:], master_key)
        def on_record(data):
            # The SDK authenticated and parsed the Feishu frame. Persist only
            # the bounded status change, then return promptly for its ACK.
            envelope = json.loads(lark.JSON.marshal(data))
            receiver.enqueue_verified(project, envelope)
        handler = (lark.EventDispatcherHandler.builder('', '')
                   .register_p2_drive_file_bitable_record_changed_v1(on_record).build())
        client = lark.ws.Client(config['app_id'], secret, event_handler=handler, log_level=lark.LogLevel.ERROR)
        try: client.start()
        except Exception:
            # No credentials or raw SDK error are logged. The live acceptance
            # test must prove delivery; process liveness alone is not enough.
            time.sleep(15)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--project', required=True)
    args = parser.parse_args(); identifier(args.project)
    serve(selected_store()('/state'), secret_input(Path('/run/secrets/runtime_master'), ''), args.project)


if __name__ == '__main__': main()
