"""Cloud-only installed-wheel browser and HTTP employee acceptance fixture.

OAuth grants and Feishu responses are synthetic loopback services. Chromium,
FFmpeg, ledger transactions and shipped HTML/JS are real. Never a real login.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

sys.path.insert(0, str(Path('packages/video-factory-core/tests').resolve()))
from review_fixture import Fixture
from video_factory.postgres_store import PostgresStore

out = Path(sys.argv[1]).resolve(); out.mkdir(exist_ok=True)
postgres = bool(os.environ.get('VF_DATABASE_URL_FILE'))
with tempfile.TemporaryDirectory(prefix='vf-browser-') as temporary:
    root = Path(temporary)
    store = PostgresStore('/state') if postgres else None
    admin = store.login('admin', 'cloud-stack-fixture-password')['token'] if store else None
    fixture = Fixture(root, store, admin)
    try:
        fixture.import_task()
        # Separate shipped CLI process really opens and expires its listener.
        with socket.socket() as sock: sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        secret = root/'app-secret'; secret.write_text('synthetic-app-secret'); secret.chmod(0o600)
        command = [str(Path(sys.executable).with_name('vfctl')), 'review-ui', '--root', '/state' if postgres else str(root/'state'),
                   '--media-root', str(fixture.media), '--project', fixture.project, '--app-id', 'cli_fixture',
                   '--app-secret-file', str(secret), '--port', str(port), '--seconds', '3']
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            ready = json.loads(process.stdout.readline()); assert ready['status'] == 'ready', ready
            with urlopen(ready['url']+'/api/session', timeout=3) as response:
                assert not json.load(response)['authenticated']
            stdout, stderr = process.communicate(timeout=10); assert process.returncode == 0, stderr
        finally:
            if process.poll() is None: process.kill(); process.wait()
        with socket.socket() as sock: assert sock.connect_ex(('127.0.0.1', port)) != 0

        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as browser_api:
            # Use the official channel for H.264; bundled Chromium lacks some
            # proprietary codecs (playwright.dev/docs/browsers#media-codecs).
            browser = browser_api.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox'])
            browser_version = browser.version
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            failures = []; external = []; responses = []; media_responses = []
            page.on('pageerror', lambda error: failures.append(str(error)))
            page.on('request', lambda request: external.append(request.url) if not request.url.startswith(fixture.server.origin+'/') else None)
            def capture(response):
                if '/api/' in response.url: responses.append(response.text())
                if '/media/' in response.url: media_responses.append(response.status)
            page.on('response', capture)
            page.goto(fixture.server.origin)
            assert page.locator('#video').evaluate('(video) => video.canPlayType(\'video/mp4; codecs="avc1.42E01E"\')'), 'BROWSER_H264_CODEC_REQUIRED'
            expect(page.locator('#project')).to_contain_text('ui_brand')
            page.screenshot(path=str(out/'review-login.png'), full_page=True)
            page.click('#login-button')
            expect(page.locator('#user-code')).to_have_text('ABCD-EFGH')
            expect(page.locator('#authorization-link')).to_have_attribute('href', 'https://accounts.feishu.cn/oauth/fixture')
            fixture.granted = True  # synthetic consent; no external browser navigation
            expect(page.locator('#workspace')).to_be_visible(timeout=15000)
            page.locator('#tasks button').first.click()
            expect(page.locator('#script')).to_contain_text('<img src=x onerror=alert(1)>')
            assert page.locator('#script img').count() == 0
            page.select_option('#decision', 'reject'); page.fill('#feedback', 'Correct product name')
            page.click('#prepare-review'); expect(page.locator('#plan')).to_contain_text('Correct product name')
            assert fixture.service.task('synthetic-user-token', 'task_one', 1)['state'] == 'awaiting_script_review'
            page.click('#commit'); expect(page.locator('#task-state')).to_contain_text('已退回')
            fixture.fields.update(script='Corrected product script', source_revision='source_two')
            page.locator('summary').click(); page.fill('#record', 'recFixture'); page.fill('#expected-revision', '1')
            page.locator('#import-form button').click(); expect(page.locator('#plan')).to_contain_text('第 2 版')
            page.click('#commit'); expect(page.locator('#task-title')).to_contain_text('第 2 版')
            expect(page.locator('#history')).to_contain_text('Correct product name')
            page.click('#prepare-review'); expect(page.locator('#plan')).to_contain_text('脚本 / 通过')
            page.click('#commit'); expect(page.locator('#task-state')).to_contain_text('脚本已通过')
            media = fixture.media/'clip.mp4'
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=green:s=320x240:d=1',
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(media)], check=True)
            subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(media), '-f', 'null', '-'], check=True)
            digest = fixture.artifact(media, 2)
            page.click('#refresh'); page.locator('#tasks button').first.click()
            expect(page.locator('#video')).to_be_visible(); expect(page.locator('#video-hash')).to_contain_text(digest)
            def wait_video(predicate):
                # wait_for_function evaluates a string inside the page and is
                # correctly blocked by the shipped CSP. Read via DevTools;
                # never relax the product policy for the test harness.
                for _ in range(100):
                    if page.locator('#video').evaluate(predicate): return
                    time.sleep(.1)
                page.screenshot(path=str(out/'review-playback-failure.png'), full_page=True)
                info=page.locator('#video').evaluate('(video) => ({ready:video.readyState,network:video.networkState,error:video.error?.code})')
                raise AssertionError(('VIDEO_PLAYBACK_TIMEOUT', info, media_responses))
            # The product deliberately preloads metadata, not video frames.
            # Start playback before requiring current frame data.
            wait_video('(video) => video.readyState >= 1')
            page.locator('#video').evaluate('(video) => video.play()')
            wait_video('(video) => video.readyState >= 2')
            wait_video('(video) => video.currentTime > 0')
            page.screenshot(path=str(out/'review-video-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path=str(out/'review-video-mobile.png'), full_page=True)
            page.click('#prepare-review'); expect(page.locator('#plan')).to_contain_text('视频 / 通过')
            page.click('#commit'); expect(page.locator('#task-state')).to_have_text('审核通过')
            page.click('#logout'); expect(page.locator('#workspace')).to_be_hidden()
            assert page.locator('#script').text_content() == ''
            assert not failures, failures
            assert not external, external
            assert all(secret not in '\n'.join(responses) for secret in ('synthetic-user-token', 'synthetic-app-secret', 'private-device-code', 'must-discard-refresh'))
            browser.close()
        detail = fixture.service.task('synthetic-user-token', 'task_one', 2)
        assert len(detail['history']) == 3 and detail['state'] == 'accepted'
        result = {'status': 'PASS', 'backend': 'postgresql' if postgres else 'sqlite', 'installed_cli_window_expiry': 'PASS',
                  'browser': 'Google Chrome', 'browser_version': browser_version, 'oauth': 'synthetic_device_grant_over_http', 'script_reject_repair_accept': 'PASS',
                  'video_decode_playback_and_accept': 'PASS', 'review_history_count': len(detail['history']),
                  'desktop_mobile': 'PASS', 'secrets_not_in_browser_responses': True, 'external_browser_requests': 0,
                  'real_feishu_authorization': 'not_run', 'paid_model_requests': 0, 'human_acceptance': 'not_run'}
        (out/'review-ui.json').write_text(json.dumps(result, indent=2)+'\n'); print(json.dumps(result))
    finally: fixture.close()
