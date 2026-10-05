"""Cloud-only installed setup window, synthetic OAuth, real Chrome and ledger."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from urllib.request import urlopen

sys.path.insert(0, str(Path('packages/video-factory-core/tests').resolve()))
from setup_connect_fixture import SetupFixture
from video_factory.postgres_store import PostgresStore
from playwright.sync_api import sync_playwright, expect

out = Path(sys.argv[1]).resolve(); out.mkdir(exist_ok=True)
postgres = bool(os.environ.get('VF_DATABASE_URL_FILE'))
for create in (False, True):
    with tempfile.TemporaryDirectory(prefix='vf-setup-browser-') as temporary:
        root = Path(temporary)
        store = PostgresStore('/state') if postgres else None
        admin = store.login('admin', 'cloud-stack-fixture-password')['token'] if store else None
        # Real ECS returned a valid 6,599-character token; exercise the same
        # length through the installed CLI, browser, API headers and creation.
        user_token = 'synthetic-' + 'x'*6589 if create else 'synthetic-user-token'
        fixture = SetupFixture(root, store, admin, create=create, user_token=user_token)
        browser_process = None
        try:
            # Real installed CLI startup, private capability and window expiration.
            for name, value in [('app-secret','synthetic-app-secret'), ('admin-token',fixture.admin)]:
                path = root/name; path.write_text(value); path.chmod(0o600)
            with socket.socket() as sock: sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            process = subprocess.Popen([str(Path(sys.executable).with_name('vfctl')), 'setup-feishu', 'connect',
                '--root', '/state' if postgres else str(root/'state'), '--session', str(root/'setup-connection.json'),
                '--token-file', str(root/'admin-token'), '--project', fixture.project, '--app-id','cli_fixture',
                '--app-secret-file', str(root/'app-secret'), '--port', str(port), '--seconds','3'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                ready = json.loads(process.stdout.readline()); assert ready.get('status') == 'ready', ready
                origin,capability = ready['url'].split('/#'); assert len(capability) == 43
                with urlopen(origin+'/api/session', timeout=3) as response:
                    state = json.load(response); assert not state['authenticated'] and capability not in json.dumps(state)
                stdout,stderr = process.communicate(timeout=10); assert process.returncode == 0, stderr
            finally:
                if process.poll() is None: process.kill(); process.wait()
            with socket.socket() as sock: assert sock.connect_ex(('127.0.0.1', port)) != 0
            # Fixture-only code injection into a separate installed CLI process;
            # no product endpoint override or real tenant/provider call is enabled.
            with socket.socket() as sock: sock.bind(('127.0.0.1', 0)); live_port = sock.getsockname()[1]
            wrapper = "import sys; from video_factory.feishu_oauth import DeviceOAuth; from video_factory.feishu_client import FeishuClient; DeviceOAuth.accounts_origin=DeviceOAuth.token_origin=FeishuClient.origin=sys.argv[1]; from video_factory.cli import main; raise SystemExit(main(sys.argv[2:]))"
            live_command = [sys.executable, '-c', wrapper, fixture.wire.oauth.accounts_origin, 'setup-feishu', 'connect',
                '--root', '/state' if postgres else str(root/'state'), '--session', str(root/'setup-connection.json'),
                '--token-file', str(root/'admin-token'), '--project', fixture.project, '--app-id','cli_fixture',
                '--app-secret-file', str(root/'app-secret'), '--port', str(live_port), '--seconds','60']
            browser_process = subprocess.Popen(live_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            ready = json.loads(browser_process.stdout.readline()); assert ready.get('status') == 'ready', ready
            browser_origin, capability = ready['url'].split('/#')
            with sync_playwright() as api:
                browser = api.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox'])
                page = browser.new_page(viewport={'width':1280,'height':900})
                failures=[]; external=[]; responses=[]
                page.on('pageerror',lambda error: failures.append(str(error)))
                page.on('request',lambda request: external.append(request.url) if not request.url.startswith(browser_origin+'/') else None)
                page.on('response',lambda response: responses.append(response.text()) if '/api/' in response.url else None)
                page.goto(browser_origin+'/#'+capability)
                expect(page.locator('#login-button')).to_be_visible()
                assert '#' not in page.url
                page.click('#login-button'); expect(page.locator('#user-code')).to_have_text('ABCD-EFGH')
                fixture.wire.granted = True
                expect(page.locator('#workspace')).to_be_visible(timeout=15000)
                expect(page.locator('#permission-summary')).to_contain_text('创建 Base' if create else '仅需读取')
                page.click('#prepare'); expect(page.locator('#plan')).to_contain_text('ou_video')
                if create:
                    expect(page.locator('#plan')).to_contain_text('VF_TEST_1')
                    expect(page.locator('#plan')).to_contain_text('fldcnDestination')
                assert fixture.wire.writes == []
                assert not fixture.service.status(fixture.admin, fixture.session.snapshot())['binding_matches_draft']
                page.screenshot(path=str(out/('setup-create-desktop.png' if create else 'setup-connect-desktop.png')),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                page.screenshot(path=str(out/('setup-create-mobile.png' if create else 'setup-connect-mobile.png')),full_page=True)
                page.click('#cancel'); expect(page.locator('#confirmation')).to_be_hidden()
                assert not fixture.service.status(fixture.admin, fixture.session.snapshot())['binding_matches_draft']
                assert fixture.wire.writes == []
                page.click('#prepare'); expect(page.locator('#confirmation')).to_be_visible()
                page.click('#commit'); expect(page.locator('#done')).to_be_visible()
                expect(page.locator('#handoff')).to_contain_text('stack-review')
                assert fixture.service.status(fixture.admin, fixture.session.snapshot())['binding_matches_draft']
                stdout, stderr = browser_process.communicate(timeout=10)
                assert browser_process.returncode == 0, 'SETUP_CLI_DID_NOT_EXIT_AFTER_SAVE'
                saved = json.loads(stdout)
                assert saved['status'] == 'connection_binding_saved'
                if create:
                    assert saved['created_base'] == 'bascnCreated' and saved['test_task_count'] == 2
                    assert len(fixture.wire.writes) == 5
                    draft = fixture.session.snapshot()
                    # Reopened authorization resumes the same durable journal;
                    # it must not create another Base, table or record.
                    plan = fixture.service.prepare(fixture.admin, draft, user_token)
                    again = fixture.service.apply(fixture.admin, draft, user_token, plan['plan_sha256'])
                    assert again['feishu_writes'] == 0 and len(fixture.wire.writes) == 5
                    # A used workspace no longer contains the installation seed
                    # values. Its installed browser must review and preserve v2.
                    revised_id = next(rid for rid, row in fixture.wire.created_records.items()
                                      if row['fields'].get('任务编号') == 'VF_TEST_1')
                    fixture.wire.created_records[revised_id]['fields']['来源版本'] = 'test-v2'
                    browser_process = subprocess.Popen(live_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    resumed = json.loads(browser_process.stdout.readline())
                    assert resumed.get('status') == 'ready'
                    # The private URL differs only in its fragment. Leave the
                    # completed document first so this exercises a fresh window,
                    # as the installer handoff does, not same-document navigation.
                    page.goto('about:blank')
                    page.goto(resumed['url'])
                    expect(page.locator('#login-button')).to_be_visible()
                    page.click('#login-button')
                    expect(page.locator('#workspace')).to_be_visible(timeout=15000)
                    page.click('#prepare')
                    expect(page.locator('#plan')).to_contain_text('将保留现有内容')
                    expect(page.locator('#commit')).to_have_text('确认恢复现有工作区连接')
                    expect(page.locator('#write-summary')).to_contain_text('不创建或覆盖飞书记录')
                    page.click('#commit'); expect(page.locator('#done')).to_be_visible()
                    expect(page.locator('#message')).to_contain_text('现有工作区连接已恢复')
                    stdout, stderr = browser_process.communicate(timeout=10)
                    assert browser_process.returncode == 0
                    assert json.loads(stdout)['reused_workspace'] is True
                    assert fixture.wire.created_records[revised_id]['fields']['来源版本'] == 'test-v2'
                    assert len(fixture.wire.writes) == 5
                with socket.socket() as sock: assert sock.connect_ex(('127.0.0.1', live_port)) != 0
                assert all(secret not in '\n'.join(responses) for secret in (fixture.admin,capability,user_token,'synthetic-app-secret','private-device-code','must-discard-refresh'))
                assert not failures,failures
                assert not external,external
                browser.close()
            # Read through the separate employee service using the newly saved
            # binding, not just a metadata status flag. Exact SKU belongs to Setup.
            from video_factory.review_service import ReviewService
            review = ReviewService(fixture.store, fixture.project, fixture.wire.media, client_factory=fixture.service.client_factory)
            fixture.wire.fields['sku_id'] = 'EXAMPLE-001'
            record = next((rid for rid, row in fixture.wire.created_records.items() if row['fields'].get('任务编号') == 'VF_TEST_1'), 'recFixture')
            plan = review.bridge.prepare_import(user_token, fixture.project, record, 0)
            review.bridge.import_task(user_token, fixture.project, record, plan['plan_sha256'], 0)
            detail = review.task(user_token, 'VF_TEST_1' if create else 'task_one', 1)
            assert detail['state'] == 'awaiting_script_review' and detail['review_stages'] == ['script'], detail
            assert len(review.tasks(user_token)['items']) == 1
            result={'new_base_create_and_resume':'synthetic_PASS' if create else 'not_applicable', 'status':'PASS','backend':'postgresql' if postgres else 'sqlite', 'first_binding_device_authorization':'synthetic_PASS',
                    'new_binding_employee_import_and_script_role':'PASS', 'installed_cli_expiry':'PASS', 'installed_cli_exits_after_confirmed_save':'PASS','no_save_before_confirmation':'PASS','cancel_reprepare_save':'PASS',
                    'desktop_mobile':'PASS','credential_response_leaks':0,'real_feishu_authorization':'not_run',
                    'paid_model_requests':0,'human_acceptance':'not_run'}
            (out/('setup-create.json' if create else 'setup-connect.json')).write_text(json.dumps(result,indent=2)+'\n'); print(json.dumps(result))
        finally:
            if browser_process and browser_process.poll() is None: browser_process.kill(); browser_process.wait()
            fixture.close()
