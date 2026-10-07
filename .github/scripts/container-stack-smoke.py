"""Cloud Docker integration: PostgreSQL, real n8n execution and full cold restore."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from cryptography.fernet import Fernet
from video_factory.stack import Stack,run,images,preflight


def free_port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def post(port,path,body,token=None):
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    req=Request(f'http://127.0.0.1:{port}'+path,data=json.dumps(body).encode(),headers=headers)
    with urlopen(req,timeout=10) as response:return json.load(response)


def n8n_fixture(stack,root,*,import_first):
    fixture={'id':'VfStackHealth001','name':'CI synthetic health fixture','active':False,
             'nodes':[{'id':'start','name':'Start','type':'n8n-nodes-base.manualTrigger','typeVersion':1,'position':[0,0],'parameters':{}},
                      {'id':'http','name':'Read runtime health','type':'n8n-nodes-base.httpRequest','typeVersion':4.2,'position':[200,0],
                       'parameters':{'url':'http://runtime:8787/healthz','options':{}}}],
             'connections':{'Start':{'main':[[{'node':'Read runtime health','type':'main','index':0}]]}},'settings':{'executionOrder':'v1'}}
    if import_first:
        path=root/'fixture.json';path.write_text(json.dumps(fixture));path.chmod(0o644)
        stack.compose('cp',str(path),'n8n:/tmp/fixture.json')
        stack.compose('exec','-T','n8n','n8n','import:workflow','--input=/tmp/fixture.json')
        credentials=[{'id':'vfFixtureHeader','name':'CI fixture credential','type':'httpHeaderAuth','data':{'name':'X-CI-Fixture','value':'NOT-A-REAL-CREDENTIAL'}}]
        path=root/'credentials.json';path.write_text(json.dumps(credentials));path.chmod(0o644)
        stack.compose('cp',str(path),'n8n:/tmp/credentials.json')
        stack.compose('exec','-T','n8n','n8n','import:credentials','--input=/tmp/credentials.json')
    output=stack.compose('run','--rm','--no-deps','-e','N8N_RUNNERS_BROKER_PORT=5689','n8n','execute','--id=VfStackHealth001','--rawOutput',timeout=180).decode()
    assert 'runtime_ledger_alpha' in output and '"up"' in output,'REAL_N8N_EXECUTION_FAILED'
    stack.compose('exec','-T','n8n','n8n','export:credentials','--id=vfFixtureHeader','--decrypted','--output=/tmp/credential-check.json')
    output=stack.compose('exec','-T','n8n','node','-e',"let a=JSON.parse(require('fs').readFileSync('/tmp/credential-check.json'));if(a[0].data.value!=='NOT-A-REAL-CREDENTIAL')process.exit(2);console.log('CREDENTIAL_DECRYPT_OK')")
    assert b'CREDENTIAL_DECRYPT_OK' in output


with tempfile.TemporaryDirectory(prefix='vf-stack-',dir='/root') as temp:
    root=Path(temp);wheelhouse=root/'wheels';wheelhouse.mkdir(mode=0o700)
    for source in (Path(sys.argv[1]),Path(sys.argv[2])):
        for p in source.glob('*.whl'):shutil.copyfile(p,wheelhouse/p.name)
    baseline=root/'baseline';baseline.mkdir(mode=0o700)
    for p in Path(sys.argv[3]).glob('*.whl'):shutil.copyfile(p,baseline/p.name)
    for p in Path(sys.argv[2]).glob('*.whl'):shutil.copyfile(p,baseline/p.name)
    firstroot=root/'first';firstroot.mkdir(mode=0o700)
    runtime_port,n8n_port=free_port(),free_port()
    environment=preflight(firstroot,runtime_port,n8n_port)
    first=None
    setup_root=None
    stacks=[]
    try:
        # Exercise the shipped one-command installer, including an interrupted
        # derived-file write and repeat installation without replacing secrets.
        passwordfile=root/'bootstrap-password';passwordfile.write_text('cloud-stack-fixture-password');passwordfile.chmod(0o600)
        # Use the actual prior CLI to produce the prior deployment schema/assets.
        baseline_venv=root/'baseline-cli'
        run([sys.executable,'-m','venv',str(baseline_venv)])
        run([str(baseline_venv/'bin/pip'),'install','--no-index','--find-links',str(baseline),str(next(baseline.glob('video_factory_core-*.whl')))])
        install=[str(baseline_venv/'bin/vfctl'),'stack','install','--root',str(firstroot),
                 '--deployment','ci-customer','--wheelhouse',str(baseline),'--password-file',str(passwordfile),
                 '--runtime-port',str(runtime_port),'--n8n-port',str(n8n_port)]
        print('STAGE: single-command install with fixed image acquisition',file=sys.stderr,flush=True)
        assert json.loads(run(install,timeout=900))['infrastructure_ready']
        first=Stack(firstroot);stacks.append(first)
        assert first.config['schema']==2 and 'worker' in json.loads((firstroot/'compose.json').read_text())['services']
        before_key=(firstroot/'secrets/runtime_master').read_bytes()
        (firstroot/'compose.json').unlink()
        assert json.loads(run(install,timeout=900))['infrastructure_ready']
        assert before_key==(firstroot/'secrets/runtime_master').read_bytes()
        wrong=install.copy();wrong[wrong.index('--deployment')+1]='wrong-customer'
        rejected=subprocess.run(wrong,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        assert rejected.returncode==2 and b'INSTALL_RESUME_TARGET_OR_RELEASE_CHANGED' in rejected.stdout
        assert not (firstroot/'secrets/bootstrap_password').exists()
        # Dedicated additional database is a fixture only, not a production role grant.
        first.compose('exec','-T','postgres','psql','-U','postgres','-d','postgres','-c','CREATE DATABASE vf_migration OWNER vf_runtime;')
        deny=first.compose('exec','-T','postgres','psql','-U','postgres','-d','postgres','-Atc',"SELECT has_database_privilege('vf_n8n','vf_runtime','CONNECT');")
        assert deny.strip()==b'f','N8N_CAN_CONNECT_TO_PRODUCT_DB'
        print('STAGE: PostgreSQL transaction and migration checks',file=sys.stderr,flush=True)
        script=Path('.github/scripts/postgres-ledger-smoke.py').read_bytes()
        output=first.compose('exec','-T','runtime','python','-',data=script)
        assert json.loads(output)['status']=='PASS'
        first.compose('exec','-T','runtime','python','-c',"from video_factory.postgres_store import PostgresStore; import json; s=PostgresStore('/state'); c=s.connect(); d=c.__enter__(); d.execute('INSERT INTO meta VALUES(?,?)',('worker:recovery_fixture',json.dumps({'expires_at':9999999999,'provider_receipt':'fixture-provider-id'}))); c.__exit__(None,None,None)")
        first.compose('exec','-T','runtime','python','-c',"from video_factory.postgres_store import PostgresStore; import json; s=PostgresStore('/state'); c=s.connect(); d=c.__enter__(); d.execute('INSERT INTO meta VALUES(?,?)',('automation:key:recovery_fixture',json.dumps({'expires_at':9999999999,'scope':'queue_read','project':'brand'}))); c.__exit__(None,None,None)")
        print('STAGE: real n8n workflow and credential checks',file=sys.stderr,flush=True)
        n8n_fixture(first,root,import_first=True)
        # Media and master-key hashes must survive full recovery, not just DB rows.
        media=firstroot/'data/media/fixture.txt';media.write_text('synthetic media fixture');os.chown(media,10001,10001)
        master=(firstroot/'secrets/runtime_master').read_bytes()
        first.stop();assert first.up()['infrastructure_ready']
        print('STAGE: cold backup and isolated restore',file=sys.stderr,flush=True)
        key=Fernet.generate_key();archive=root/'complete.vfb';first.backup(archive,key)
        secondroot=root/'restored';secondroot.mkdir(mode=0o700)
        second=Stack.restore(archive,secondroot,key,runtime_port=free_port(),n8n_port=free_port());stacks.append(second)
        assert first.config['instance']!=second.config['instance']
        second.build();assert second.up()['infrastructure_ready']
        assert media.read_bytes()==(secondroot/'data/media/fixture.txt').read_bytes()
        assert master==(secondroot/'secrets/runtime_master').read_bytes()
        oldtoken=(secondroot/'data/runtime/old-test-token').read_text()
        try:post(second.config['runtime_port'],'/v1/doctor',{},oldtoken)
        except HTTPError as error:assert error.code==401
        else:raise AssertionError('RESTORE_DID_NOT_REVOKE_SESSIONS')
        token=post(second.config['runtime_port'],'/v1/login',{'name':'admin','password':'cloud-stack-fixture-password'})['token']
        task=post(second.config['runtime_port'],'/v1/tasks/read',{'project':'brand','task':'one'},token)
        assert task['versions'][0]['state']=='submission_unknown'
        n8n_fixture(second,root,import_first=False)
        proof_output=second.compose('exec','-T','runtime','python','-c',"from pathlib import Path; from video_factory.postgres_store import PostgresStore; assert PostgresStore('/state').resolve_secret('fixture',Path('/run/secrets/runtime_master').read_bytes())=='fixture-provider-not-a-real-key'; print('VAULT_RESTORE_OK')")
        assert b'VAULT_RESTORE_OK' in proof_output
        print('STAGE: product release upgrade and failure rollback',file=sys.stderr,flush=True)
        old_version=second.compose('exec','-T','runtime','vfctl','--version').decode().strip()
        assert old_version=='0.1.0a19'
        upgrade_root=root/'upgraded';upgrade_root.mkdir(mode=0o700)
        upgrade=second.upgrade(upgrade_root,wheelhouse,root/'pre-upgrade.vfb',key)
        assert upgrade['status']=='upgraded',upgrade
        third=Stack(upgrade_root);stacks.append(third)
        assert third.config['schema']==2
        assert third.status()['egress']=='disabled' and 'worker' not in third.status()['components']
        new_version=third.compose('exec','-T','runtime','vfctl','--version').decode().strip()
        expected_version=json.loads((Path(sys.argv[4])/'release.json').read_text())['version']
        assert new_version==expected_version and new_version!=old_version
        third.compose('exec','-T','runtime','python','-c',"from video_factory.postgres_store import PostgresStore; import json; s=PostgresStore('/state'); c=s.connect(); d=c.__enter__(); v=json.loads(d.execute(\"SELECT value FROM meta WHERE key='worker:recovery_fixture'\").fetchone()[0]); assert v['expires_at']==0 and v['provider_receipt']=='fixture-provider-id'; c.__exit__(None,None,None)")
        third.compose('exec','-T','runtime','python','-c',"from video_factory.postgres_store import PostgresStore; import json; s=PostgresStore('/state'); c=s.connect(); d=c.__enter__(); v=json.loads(d.execute(\"SELECT value FROM meta WHERE key='automation:key:recovery_fixture'\").fetchone()[0]); assert v['expires_at']==0; c.__exit__(None,None,None)")
        try:post(third.config['runtime_port'],'/v1/doctor',{},token)
        except HTTPError as error:assert error.code==401
        else:raise AssertionError('UPGRADE_DID_NOT_REVOKE_OLD_SESSIONS')
        new_token=post(third.config['runtime_port'],'/v1/login',{'name':'admin','password':'cloud-stack-fixture-password'})['token']
        assert post(third.config['runtime_port'],'/v1/tasks/read',{'project':'brand','task':'one'},new_token)['versions'][0]['state']=='submission_unknown'
        n8n_fixture(third,root,import_first=False)
        broken=root/'bad-wheels';broken.mkdir(mode=0o700)
        for p in Path(sys.argv[2]).glob('*.whl'):shutil.copyfile(p,broken/p.name)
        (broken/f'video_factory_core-{new_version}-py3-none-any.whl').write_bytes(b'INVALID_WHEEL_FAILURE_INJECTION_NOT_A_RELEASE')
        bad_root=root/'bad-candidate';bad_root.mkdir(mode=0o700)
        rollback=third.upgrade(bad_root,broken,root/'failed-upgrade-checkpoint.vfb',key)
        assert rollback['status']=='rolled_back',rollback
        assert third.status()['infrastructure_ready']
        assert third.compose('exec','-T','runtime','vfctl','--version').decode().strip()==new_version
        assert post(third.config['runtime_port'],'/v1/tasks/read',{'project':'brand','task':'one'},new_token)['versions'][0]['state']=='submission_unknown'
        print('STAGE: Setup plan to fresh installation and two project imports',file=sys.stderr,flush=True)
        setup_root=root/'setup-deployed'
        session=root/'customer.setup.json'
        answers=json.loads(Path('packages/video-factory-core/examples/setup/answers.json').read_text())
        answers['deployment.id']='setup-customer'
        answers.update({'project.base_mode':'bind','project.base_target':'bascnFixture'})
        answerfile=root/'answers.json';answerfile.write_text(json.dumps(answers));answerfile.chmod(0o600)
        release_dir=Path(sys.argv[4])
        release_receipt=json.loads((release_dir/'release.json').read_text())
        delivered=root/'delivered-release'
        shutil.copytree(next(p for p in release_dir.iterdir() if p.is_dir()),delivered)
        installed_cli=json.loads(run([sys.executable,'-I',str(delivered/'install.py'),'--prefix',str(root/'release-cli'),
            '--manifest-sha256',release_receipt['manifest_sha256']],timeout=300))
        assert installed_cli['status']=='cli_installed' and installed_cli['version']==new_version
        vfctl=installed_cli['cli']
        wheelhouse=root/'release-cli/release/wheels'
        common=['--session',str(session),'--root',str(setup_root),'--host',answers['deployment.host'],
                '--wheelhouse',str(wheelhouse),'--runtime-port',str(free_port()),'--n8n-port',str(free_port())]
        import runpy
        drive=runpy.run_path('.github/scripts/setup-run-pty.py')['drive']
        wizard=[vfctl,'setup-run','--session',str(session),'--root',str(setup_root),'--wheelhouse',str(wheelhouse),
            '--runtime-port',common[common.index('--runtime-port')+1],'--n8n-port',common[common.index('--n8n-port')+1],
            '--connection-port',str(free_port()),'--review-port',str(free_port()),'--seconds','15']
        # Customer-style rehearsal starts with no Setup file and only delivered
        # files plus public terminal prompts. No bulk answers or product imports
        # manufacture this project's configuration.
        assert not session.exists() and not setup_root.exists()
        products=root/'customer-products.json'
        shutil.copyfile(root/'release-cli/release/templates/products.json',products);products.chmod(0o600)
        assert json.loads(products.read_text())==answers['project.products']
        first_questions=drive(wizard,[('输入> ',answers['organization.id']),('输入> ',answers['organization.name']),('输入> ',':quit')])
        assert 'configuration_saved' in first_questions and not setup_root.exists()
        question_answers=[
            ('输入> ',answers['organization.admin_ref']),('输入> ',answers['deployment.id']),
            ('输入> ',answers['deployment.host']),('输入> ',answers['deployment.ssh_user']),
            ('引用> ',answers['deployment.ssh_identity_ref']),('输入> ',answers['deployment.feishu_tenant']),
            ('引用> ',answers['deployment.feishu_credential_ref']),('输入> ',''),
            ('输入> ',answers['project.id']),('输入> ',answers['project.name']),
            ('输入> ',answers['project.product_category']),('输入> ',answers['project.target_market']),
            ('输入> ',answers['project.language']),('输入> ',answers['project.business_goal']),
            ('输入> ',answers['project.category_rule_source']),('输入> ','bind'),
            ('输入> ',answers['project.base_target']),('输入> ',answers['project.script_reviewer']),
            ('输入> ',answers['project.video_reviewer']),('输入> ',str(root/'missing-products.json')),
            ('输入> ',str(products)),('输入> ',answers['project.video_route']),
            ('引用> ',answers['project.video_credential_ref']),('输入> ',answers['project.billing_owner']),
            ('输入> ',''),('确认在这台机器安装或继续','n')]
        configured=drive(wizard,question_answers)
        assert 'installation_not_started' in configured and 'SETUP_INPUT_OR_STORAGE_UNAVAILABLE' in configured
        assert '规划新 Base' not in configured and not setup_root.exists()
        saved=json.loads(session.read_text())['configuration']
        for field,value in answers.items():
            group,key=field.split('.');assert saved[group][key]==value,field
        reviewed=json.loads(run([vfctl,'setup-deploy','plan',*common]))
        assert not setup_root.exists()
        rejected=subprocess.run([vfctl,'setup-deploy','apply',*common,'--expect-plan','0'*64,'--password-file',str(passwordfile)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        assert rejected.returncode==2 and not setup_root.exists()
        command=[vfctl,'setup-deploy','apply',*common,'--expect-plan',reviewed['execution_sha256'],'--password-file',str(passwordfile)]
        transcript=drive(wizard,[('确认在这台机器安装或继续','y'),('设置产品管理员密码','cloud-stack-fixture-password'),
            ('再次输入管理员密码','cloud-stack-fixture-password'),('输入> ','tblFixture'),('输入> ','fldTask'),('输入> ','fldSkuId'),
            ('输入> ','fldScript'),('输入> ','fldSource'),('输入> ','ou_employee'),('App ID（:quit 保存退出）> ','cli_fixture'),
            ('App Secret（隐藏输入）: ','synthetic-wizard-secret'),('确认保存应用凭据','y'),('现在开始飞书本人授权与终端确认','n')],
            secrets=('cloud-stack-fixture-password','synthetic-wizard-secret'))
        assert 'installed_credentials_saved' in transcript
        fourth=Stack(setup_root);stacks.append(fourth)
        # Same command resumes without replaying questions or reentering the key.
        # The Feishu terminal grant itself is exercised with synthetic OAuth in
        # setup-terminal-smoke; this container check must not contact a real tenant.
        resumed=drive(wizard,[('确认在这台机器安装或继续','y'),('产品管理员密码: ','cloud-stack-fixture-password'),
            ('沿用已保存的应用密钥','y'),('现在开始飞书本人授权与终端确认','n')],
            secrets=('cloud-stack-fixture-password','synthetic-wizard-secret'),timeout=120)
        assert 'installed_credentials_saved' in resumed
        interrupted=drive(wizard,[('确认在这台机器安装或继续','y'),('产品管理员密码: ','cloud-stack-fixture-password'),
            ('沿用已保存的应用密钥','y'),('现在开始飞书本人授权与终端确认',None)],
            secrets=('cloud-stack-fixture-password','synthetic-wizard-secret'),timeout=120,expected_code=130)
        assert 'interrupted' in interrupted

        assert not list((fourth.root/'data/worker').glob('.setup-run-*'))
        assert fourth.status()['egress']=='disabled'
        imported=json.loads(run(command,timeout=900))
        assert imported['project']['reused']

        assert imported['status']=='infrastructure_and_project_draft_ready' and not imported['business_ready']
        assert imported['project']['sku_count']==1 and imported['project']['reused']
        assert not imported['project']['credentials_resolved'] and imported['project']['active_video_route']=='deferred'
        assert json.loads(run(command,timeout=900))['project']['reused']
        status=json.loads(run([vfctl,'setup-deploy','status',*common,'--password-file',str(passwordfile)]))
        assert status['infrastructure']['infrastructure_ready'] and status['project']['sku_count']==1
        # A new project in the same customer stack receives a separate imported SKU set.
        second_session=root/'second.setup.json'
        new_answers={k:v for k,v in answers.items() if k.startswith('project.')}
        new_answers['project.id']='second_brand'
        new_answers['project.products']=[{**answers['project.products'][0],'sku_id':'SECOND-001'}]
        answerfile.write_text(json.dumps(new_answers))
        run([vfctl,'setup','--session',str(second_session),'--from-session',str(session),'--answers',str(answerfile),'--expect-revision','0','--json'])
        second_common=common.copy();second_common[second_common.index('--session')+1]=str(second_session)
        second_plan=json.loads(run([vfctl,'setup-deploy','plan',*second_common]))
        second_import=json.loads(run([vfctl,'setup-deploy','apply',*second_common,'--expect-plan',second_plan['execution_sha256'],'--password-file',str(passwordfile)],timeout=900))
        assert second_import['project']['project']=='second_brand' and not second_import['project']['reused']
        # No temporary administrator login from import/readback may survive.
        check=fourth.compose('exec','-T','runtime','python','-c',"from video_factory.postgres_store import PostgresStore; s=PostgresStore('/state'); c=s.connect(); db=c.__enter__(); assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==0; assert db.execute('SELECT count(*) FROM projects').fetchone()[0]==2; c.__exit__(None,None,None); print('SETUP_IMPORT_OK')")
        assert b'SETUP_IMPORT_OK' in check
        fourth.compose('exec','-T','postgres','psql','-U','postgres','-d','postgres','-c','CREATE DATABASE vf_worker_migration OWNER vf_runtime;')
        pg_worker=json.loads(fourth.compose('exec','-T','runtime','python','-',data=Path('.github/scripts/postgres-worker-smoke.py').read_bytes()))
        assert pg_worker['status']=='PASS'
        print('STAGE: container FFmpeg worker and egress boundaries',file=sys.stderr,flush=True)
        fixture=Path('.github/scripts/worker-package-smoke.py').read_bytes()
        bootstrap=("import os,runpy; from pathlib import Path; os.environ.pop('VF_DATABASE_URL_FILE',None); p=Path('/tmp/worker-smoke.py'); p.write_bytes("+repr(fixture)+"); runpy.run_path(str(p),run_name='__main__')").encode()
        worker_proof=json.loads(fourth.compose('run','--rm','-T','--no-deps','--entrypoint','python','worker','-',data=bootstrap,timeout=180))
        assert worker_proof['status']=='PASS' and worker_proof['full_media_decode']=='PASS'
        fourth.compose('up','-d','--pull','never','--wait','--wait-timeout','40','egress',timeout=60)
        egress_proof=json.loads(fourth.compose('run','--rm','-T','--no-deps','--entrypoint','python','worker','-',data=Path('.github/scripts/container-egress-smoke.py').read_bytes(),timeout=60))
        fourth.compose('stop','--timeout','5','egress',timeout=20)
        assert egress_proof['status']=='PASS' and fourth.status()['egress']=='disabled'
        # Exercise the operator wrapper on the upgraded durable task, then a
        # rejected step. The temporary relay must be stopped on failure too.
        work=third.root/'data/worker'
        token_path=work/'admin.token';token_path.write_text(new_token);token_path.chmod(0o600);os.chown(token_path,10001,10001)
        worker_command=[vfctl,'stack-worker','status','--stack-root',str(third.root),'--token-file','/work/admin.token','--project','brand','--task','one','--revision','1']
        assert json.loads(run(worker_command))['state']=='submission_unknown'
        worker_command[2]='step'
        rejected=subprocess.run(worker_command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
        assert rejected.returncode==2 and third.status()['egress']=='disabled'
        assert 'worker' not in third.status()['components']
        print('STAGE: Feishu identity import and repair on PostgreSQL, real n8n queue read',file=sys.stderr,flush=True)
        fixture=Path('.github/scripts/feishu-package-smoke.py').read_bytes()
        bootstrap=("import runpy; from pathlib import Path; p=Path('/tmp/feishu-smoke.py'); p.write_bytes("+repr(fixture)+"); runpy.run_path(str(p),run_name='__main__')").encode()
        # Keep synthetic child output private; failure receipts expose only the stage and code.
        fixture_result=subprocess.run(['docker','compose','--profile','worker','--project-directory',str(fourth.root),'-f',str(fourth.root/'compose.json'),
            'run','--rm','-T','--no-deps','--entrypoint','python','worker','-'],input=bootstrap,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=300)
        if fixture_result.returncode:
            print(json.dumps({'stage':'feishu_container_fixture','returncode':fixture_result.returncode}),file=sys.stderr)
            raise AssertionError('FEISHU_CONTAINER_FIXTURE_FAILED')
        feishu_proof=json.loads(fixture_result.stdout)
        assert feishu_proof['status']=='PASS' and feishu_proof['backend']=='postgresql'
        admin=post(fourth.config['runtime_port'],'/v1/login',{'name':'admin','password':'cloud-stack-fixture-password'})['token']
        work=fourth.root/'data/worker'
        for name,data in [('connection.json',(fourth.root/'data/runtime/feishu-connection.json').read_bytes()),('admin.token',admin.encode())]:
            p=work/name;p.write_bytes(data);p.chmod(0o600);os.chown(p,10001,10001)
        connection_status=json.loads(run([vfctl,'setup-feishu','status','--stack-root',str(fourth.root),
            '--session','/work/connection.json','--token-file','/work/admin.token'],timeout=90))
        assert connection_status['binding_matches_draft'] and not connection_status['requires_reconfirmation']
        assert fourth.status()['egress']=='disabled'
        feishu_proof['container_connection_status_without_egress']='PASS'
        print('STAGE: employee window container launch and expiry cleanup',file=sys.stderr,flush=True)
        app_secret=work/'review-app-secret';app_secret.write_text('synthetic-app-secret');app_secret.chmod(0o600);os.chown(app_secret,10001,10001)
        review_port=free_port()
        review_process=subprocess.Popen([vfctl,'stack-review','--stack-root',str(fourth.root),'--project','fs_brand',
            '--app-id','cli_fixture','--app-secret-file','/work/review-app-secret','--port',str(review_port),'--seconds','15'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            # CLI emits readiness only after probing the actual one-shot container.
            ready=json.loads(review_process.stdout.readline());assert ready.get('status')=='ready',ready
            with urlopen(ready['url']+'/api/session',timeout=5) as response:assert not json.load(response)['authenticated']
            with urlopen(ready['url']+'/',timeout=5) as response:assert b'Video Factory' in response.read()
            tail,error=review_process.communicate(timeout=60);assert review_process.returncode==0,(tail,error)
            assert json.loads(tail)['status']=='closed'
        finally:
            if review_process.poll() is None:review_process.terminate();review_process.wait(timeout=30)
        assert fourth.status()['egress']=='disabled'
        assert not run(['docker','container','ls','--all','--filter','name=vf-review-'+fourth.config['instance'],'--format','{{.ID}}']).strip()
        with socket.socket() as sock:assert sock.connect_ex(('127.0.0.1',review_port))!=0
        feishu_proof['employee_window_container_and_cleanup']='PASS'
        print('STAGE: administrator setup window container launch and expiry cleanup',file=sys.stderr,flush=True)
        setup_port=free_port()
        setup_process=subprocess.Popen([vfctl,'setup-feishu','connect','--stack-root',str(fourth.root),
            '--project','fs_brand','--session','/work/connection.json','--token-file','/work/admin.token',
            '--app-id','cli_fixture','--app-secret-file','/work/review-app-secret','--port',str(setup_port),'--seconds','15'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            ready=json.loads(setup_process.stdout.readline());assert ready.get('status')=='ready',ready
            origin,capability=ready['url'].split('/#');assert len(capability)==43
            with urlopen(origin+'/healthz',timeout=5) as response:
                health=json.load(response);assert health['scope']=='administrator_setup_window'
            with urlopen(origin+'/api/session',timeout=5) as response:
                session=json.load(response);cookie=response.headers['Set-Cookie'].split(';')[0]
                assert capability not in json.dumps(session) and not session['authenticated']
            from urllib.request import Request
            unlock=Request(origin+'/api/unlock',data=json.dumps({'capability':capability}).encode(),
                headers={'Cookie':cookie,'Origin':origin,'X-VF-CSRF':session['csrf'],'Content-Type':'application/json'})
            with urlopen(unlock,timeout=5) as response:assert json.load(response)['status']=='unlocked'
            with urlopen(origin+'/',timeout=5) as response:assert b'Video Factory' in response.read()
            tail,error=setup_process.communicate(timeout=60);assert setup_process.returncode==0,(tail,error)
            assert json.loads(tail)['status']=='closed'
        finally:
            if setup_process.poll() is None:setup_process.terminate();setup_process.wait(timeout=30)
        assert fourth.status()['egress']=='disabled'
        assert not run(['docker','container','ls','--all','--filter','name=vf-setup-'+fourth.config['instance'],'--format','{{.ID}}']).strip()
        with socket.socket() as sock:assert sock.connect_ex(('127.0.0.1',setup_port))!=0
        feishu_proof['setup_window_container_unlock_and_cleanup']='PASS'
        print('STAGE: terminal wizard existing binding to Feishu handoff',file=sys.stderr,flush=True)
        completed_draft=json.loads((fourth.root/'data/runtime/feishu-connection.json').read_text())
        employee_session=root/'employee.setup.json';employee_session.write_text(json.dumps(completed_draft['setup']));employee_session.chmod(0o600)
        employee_connection=employee_session.with_name(employee_session.name+'.feishu.json')
        employee_connection.write_text(json.dumps(completed_draft));employee_connection.chmod(0o600)
        employee_wizard=wizard.copy();employee_wizard[employee_wizard.index('--session')+1]=str(employee_session)
        transcript=drive(employee_wizard,[('确认在这台机器安装或继续','y'),('产品管理员密码: ','cloud-stack-fixture-password'),
            ('App ID（:quit 保存退出）> ','cli_fixture'),('App Secret（隐藏输入）: ','synthetic-wizard-secret'),
            ('确认保存应用凭据','y')],secrets=('cloud-stack-fixture-password','synthetic-wizard-secret'))
        assert 'connection_ready' in transcript and '现在启动员工审核入口' not in transcript
        assert fourth.status()['egress']=='disabled'
        assert not list((fourth.root/'data/worker').glob('.setup-run-*'))
        # Decrypt only inside the actual customer runtime and assert; no raw
        # key or token is sent back into the evidence log.
        probe="from video_factory.postgres_store import PostgresStore; from video_factory.feishu_bridge import meta; from pathlib import Path; s=PostgresStore('/state'); c=s.connect(); db=c.__enter__(); p=meta(db,'setup:feishu-app:fs_brand'); c.__exit__(None,None,None); assert s.resolve_secret(p['credential_ref'][7:],Path('/run/secrets/runtime_master').read_bytes())=='synthetic-wizard-secret'; print('ENCRYPTED_PROFILE_OK')"
        assert b'ENCRYPTED_PROFILE_OK' in fourth.compose('exec','-T','runtime','python','-c',probe)
        feishu_proof['fixed_release_installer_to_real_setup_run']='PASS'
        feishu_proof['empty_session_all_terminal_questions_and_shipped_sku_template']='PASS'
        feishu_proof['configuration_quit_resume_missing_sku_retry_and_install_decline']='PASS'
        feishu_proof['independent_human_operator']='not_run'
        feishu_proof['release_archive_sha256']=release_receipt['archive_sha256']
        feishu_proof['release_manifest_sha256']=release_receipt['manifest_sha256']
        feishu_proof['terminal_welcome_install_resume_vault_and_feishu_handoff']='PASS'
        feishu_proof['terminal_sigterm_and_temporary_session_cleanup']='PASS'


        from video_factory.automation import template
        queue_token=(fourth.root/'data/runtime/feishu-queue-token').read_text()
        draft=template('fs_brand','vfProjectQueue');draft['id']='VfProjectQueue001'
        draft['nodes'][0].update(type='n8n-nodes-base.manualTrigger',typeVersion=1,parameters={})
        fixture_path=root/'queue-workflow.json';fixture_path.write_text(json.dumps(draft));fixture_path.chmod(0o644)
        fourth.compose('cp',str(fixture_path),'n8n:/tmp/queue-workflow.json')
        fourth.compose('exec','-T','n8n','n8n','import:workflow','--input=/tmp/queue-workflow.json')
        credentials=[{'id':'vfProjectQueue','name':'Video Factory project queue','type':'httpHeaderAuth','data':{'name':'Authorization','value':'Bearer '+queue_token}}]
        fixture_path=root/'queue-credential.json';fixture_path.write_text(json.dumps(credentials));fixture_path.chmod(0o644)
        fourth.compose('cp',str(fixture_path),'n8n:/tmp/queue-credential.json')
        fourth.compose('exec','-T','n8n','n8n','import:credentials','--input=/tmp/queue-credential.json')
        output=fourth.compose('run','--rm','--no-deps','-e','N8N_RUNNERS_BROKER_PORT=5689','n8n','execute','--id=VfProjectQueue001','--rawOutput',timeout=180).decode()
        assert 'queued_task' in output and 'awaiting_script_review' in output and 'queue_read' in output
        try:post(fourth.config['runtime_port'],'/v1/automation/queue',{'project':'second_brand'},queue_token)
        except HTTPError as error:assert error.code==401
        else:raise AssertionError('QUEUE_TOKEN_CROSSED_PROJECT')
        admin=post(fourth.config['runtime_port'],'/v1/login',{'name':'admin','password':'cloud-stack-fixture-password'})['token']
        import hashlib
        post(fourth.config['runtime_port'],'/v1/automation/revoke',{'key_id':hashlib.sha256(queue_token.encode()).hexdigest()},admin)
        try:post(fourth.config['runtime_port'],'/v1/automation/queue',{'project':'fs_brand'},queue_token)
        except HTTPError as error:assert error.code==401
        else:raise AssertionError('REVOKED_QUEUE_TOKEN_ACCEPTED')
        import runpy
        runner_proof=runpy.run_path('.github/scripts/runner-stack-smoke.py')['smoke'](fourth,root)
        workspace_proof=runpy.run_path('.github/scripts/workspace-stack-smoke.py')['smoke'](fourth,root,json.loads(employee_session.read_text()))
        proof={'private_runner':runner_proof,'workspace_https':workspace_proof,'status':'PASS','scope':'isolated_cloud_three_component_stack','backend':'postgresql',
               'feishu_bridge':feishu_proof,'n8n_project_queue':'PASS','queue_cross_project_denied':True,'queue_revoke_and_upgrade_invalidation':'PASS','container_worker_full_decode':'PASS','controlled_egress':egress_proof,'worker_relay_stopped_after_rejection':True,'deployment_schema_upgrade':'2_preserved','migrated_worker_permissions_revoked':'PASS','restored_worker_permissions_revoked':'PASS','postgres_generic_worker_concurrency':'PASS','setup_to_fresh_install_and_two_projects':'PASS','setup_import_resume':'PASS','execution_digest_change_rejected':True,'single_command_install_and_resume':'PASS','target_change_rejected':True,'environment':environment,'roles_isolated':True,'postgres_ledger_concurrency_and_migration':'PASS','real_n8n_health_workflow_runs':3,'product_upgrade':{'from':old_version,'to':new_version},'failed_upgrade_rollback':'PASS',
               'n8n_credential_decryption_after_restore':True,'restart':'PASS','full_cold_restore_new_directory':'PASS',
               'media_and_keys_preserved':True,'old_sessions_revoked':True,'unknown_submission_preserved':True,
               'paid_model_requests':0,'human_acceptance':'not_run','real_customer_host':'not_run','egress':'internal_network_only'}
        print(json.dumps(proof))
    except Exception:
        if first is None and (firstroot/'stack.json').exists():
            try:stacks.append(Stack(firstroot,repair_derived=True))
            except Exception:pass
        if setup_root and (setup_root/'stack.json').exists() and not any(s.root==setup_root for s in stacks):
            try:stacks.append(Stack(setup_root,repair_derived=True))
            except Exception:pass
        # These are synthetic CI services only; no customer secrets are loaded.
        for stack in stacks:
            try:
                print(stack.compose('ps','--all').decode(),file=sys.stderr)
                # Service logs can contain generated n8n resume tokens that are
                # not present in secrets/. Preserve status, not raw service logs.
                print('SERVICE_LOGS_OMITTED_FROM_CI_EVIDENCE',file=sys.stderr)
            except Exception:pass
        raise
    finally:
        for stack in reversed(stacks):
            try:
                from video_factory.runner import stop_all as stop_all_runners
                stop_all_runners(stack)
            except Exception:pass
            try:
                from video_factory.workspace import stop_all
                stop_all(stack)
            except Exception:pass
            try:stack.compose('down','--timeout','30',timeout=120)
            except Exception:pass
