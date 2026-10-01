"""Cloud-only delivered page/Skill acceptance with actual browser interaction."""
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
import threading
import zipfile
from playwright.sync_api import sync_playwright

root = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]); evidence.mkdir(parents=True, exist_ok=True)
manifest = json.loads((root / 'delivery-manifest.json').read_text())
for name, digest in manifest['files'].items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
with zipfile.ZipFile(root / 'video-factory-setup.zip') as bundle:
    names = bundle.namelist()
    assert 'video-factory-setup/SKILL.md' in names
    for required in ('operations.md', 'start.md', 'handoff.md'):
        assert f'video-factory-setup/references/{required}' in names
    for name in names:
        assert '..' not in Path(name).parts
        relative = name.removeprefix('video-factory-setup/')
        assert bundle.read(name) == (root / 'skill' / relative).read_bytes()
markdown_links=[]
for path in [*(root / 'skill').rglob('*.md'), *root.glob('*.md')]:
    for link in re.findall(r'\]\(([^)]+)\)', path.read_text()):
        if '://' not in link and not link.startswith('#'):
            target=(path.parent / link.split('#')[0]).resolve()
            assert target.is_relative_to(root) and target.is_file(), (path.name, link)
            markdown_links.append(target.relative_to(root).as_posix())

class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *_): pass

server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(root)))
thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
origin = f'http://127.0.0.1:{server.server_port}'
errors = []
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox'])
        context = browser.new_context(permissions=['clipboard-read', 'clipboard-write'], accept_downloads=True)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        for width, height, label in ((1440, 1000, 'desktop'), (390, 844, 'mobile')):
            page.set_viewport_size({'width': width, 'height': height})
            assert page.goto(origin).status == 200
            page.wait_for_function('window.VF_DELIVERY && document.querySelector("#agent-prompt").value.includes("Skill")')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), label
            for mode in ('install', 'project', 'resume', 'repair'):
                button = page.locator(f'[data-mode="{mode}"]'); button.click()
                assert button.get_attribute('aria-pressed') == 'true'
                text = page.locator('#agent-prompt').input_value()
                assert 'Skill 阅读地址' in text and '密码与密钥' in text
                assert 'TRUSTED_' not in text and '<我们的' not in text
                page.locator('#copy-prompt').click()
                assert page.evaluate('navigator.clipboard.readText()') == text
            page.locator('#choose-project').click()
            assert page.locator('[data-mode="project"]').get_attribute('aria-pressed') == 'true'
            with page.expect_download() as downloaded:
                page.locator('#download-skill').click()
            download = downloaded.value
            assert download.suggested_filename == 'video-factory-setup.zip'
            download.save_as(evidence / f'{label}-skill.zip')
            assert (evidence / f'{label}-skill.zip').read_bytes() == (root / 'video-factory-setup.zip').read_bytes()
            page.evaluate('window.scrollTo(0,0)')
            page.screenshot(path=str(evidence / f'{label}.png'), full_page=True)
        # All relative resource and anchor links must resolve, including docs.
        for href in page.locator('a[href]').evaluate_all('(links) => links.map(a => a.getAttribute("href"))'):
            if href.startswith('#'):
                assert href == '#' or page.locator(href).count() == 1
            elif not href.startswith('https://'):
                assert context.request.get(origin + '/' + href).status == 200, href
        assert not errors, errors
        for href in markdown_links:
            assert context.request.get(origin + '/' + href).status == 200, href
        browser.close()
finally:
    server.shutdown(); server.server_close()
result = {'status': 'PASS', 'version': manifest['version'], 'source_commit': manifest['source_commit'],
          'desktop_mobile': 'PASS', 'four_agent_entry_points': 'PASS', 'clipboard': 'PASS',
          'complete_skill_download': 'PASS', 'relative_links': 'PASS', 'markdown_links': 'PASS', 'page_errors': errors,
          'customer_installation': 'not_run', 'business_ready': False}
(evidence / 'delivery-result.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))
