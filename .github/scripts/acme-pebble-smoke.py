"""Cloud fixture: real Certbot / HTTP-01 / Pebble, never a public CA.

Endpoint injection exists only in this script, not in the installed CLI.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from video_factory import workspace_acme as acme
from video_factory.runtime_store import RuntimeFault
from video_factory.stack import run
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'packages/video-factory-core/tests'))
import test_stack

source=Path(sys.argv[1]).resolve()
fixture=test_stack.StackTests();fixture.setUp()
process=None
try:
    stack=fixture.prepare();stack.config['runtime_image']='sha256:'+'a'*64
    config=json.loads((source/'test/config/pebble-config.json').read_text())
    config['pebble'].update(listenAddress='127.0.0.1:14000',managementListenAddress='127.0.0.1:15000',httpPort=80)
    config_path=fixture.parent/'pebble.json';config_path.write_text(json.dumps(config))
    ca=source/'test/certs/pebble.minica.pem'
    environment={**os.environ,'PEBBLE_VA_NOSLEEP':'1','PEBBLE_WFE_NONCEREJECT':'0','REQUESTS_CA_BUNDLE':str(ca)}
    os.environ['REQUESTS_CA_BUNDLE']=str(ca)
    with tempfile.TemporaryFile() as log:
        process=subprocess.Popen([str(source/'pebble'),'-config',str(config_path)],cwd=source,env=environment,stdout=log,stderr=log)
        for _ in range(80):
            if process.poll() is not None:raise AssertionError('PEBBLE_START_FAILED')
            try:
                with socket.create_connection(('127.0.0.1',14000),timeout=.2):break
            except OSError:time.sleep(.25)
        else:raise AssertionError('PEBBLE_START_TIMEOUT')
        # Resolve the synthetic name inside this ephemeral runner only. Pebble
        # checks the real standalone challenge server; validation is not skipped.
        with open('/etc/hosts','a') as hosts:hosts.write('\n127.0.0.1 acme-fixture.example\n')
        endpoint='https://localhost:14000/dir'
        calls=[]
        def client(argv,**kwargs):
            assert argv[argv.index('--server')+1]==endpoint
            calls.append(argv)
            return run(argv,**kwargs)
        with patch.dict(acme.DIRECTORIES,{'staging':endpoint,'production':endpoint}):
            stage=acme.plan(stack,'fixture','https://acme-fixture.example','operator@example.com',['8.8.8.8'],'staging')
            prod={**stage,'environment':'production'}
            acme.issue(stack,stage,accept_terms=True,runner=client,network_check=lambda _:None)
            acme.issue(stack,prod,accept_terms=True,runner=client,network_check=lambda _:None)
            root=acme.folder(stack,'fixture')/'production'
            first=acme.load(root/'receipt.json')
            # A second order exercises persisted account reuse and new key/cert.
            acme.issue(stack,prod,accept_terms=True,runner=client,network_check=lambda _:None)
            second=acme.load(root/'receipt.json')
            assert first['certificate_sha256']!=second['certificate_sha256']
            assert len(list((root/'accounts').rglob('regr.json')))==1
            assert all(not p.is_symlink() for p in root.rglob('*'))
            def interrupted(argv,**kwargs):
                client(argv,**kwargs)
                raise RuntimeFault('FIXTURE_INTERRUPTED')
            try:acme.issue(stack,prod,accept_terms=True,runner=interrupted,network_check=lambda _:None)
            except RuntimeFault:pass
            else:raise AssertionError('INTERRUPTION_REQUIRED')
            before=len(calls)
            assert acme.recover(stack,'fixture','production')['status']=='issued'
            assert len(calls)==before==4
        print(json.dumps({'status':'PASS','scope':'real_certbot_http01_against_ephemeral_pebble',
            'orders':4,'account_reuse':True,'recovery_without_resubmission':True,
            'public_ca_orders':0,'real_domain_acceptance':'not_run'}))
finally:
    if process is not None:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait()
    fixture.tearDown()
