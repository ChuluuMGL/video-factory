"""Real Chrome over fixture HTTPS; synthetic OAuth, two projects and two tabs."""
import json
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect
sys.path.insert(0,str(Path('packages/video-factory-core/tests').resolve()))
from portal_fixture import PortalFixture

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);f=PortalFixture(root)
    try:
        cert,key=root/'cert.pem',root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert,key)
        f.server.socket=tls.wrap_socket(f.server.socket,server_side=True)
        origin='https://127.0.0.1:'+str(f.server.server_port);f.server.origin=origin
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',args=['--no-sandbox'])
            # The only certificate bypass is this local test's self-signed certificate.
            context=browser.new_context(ignore_https_errors=True)
            unexpected=[];errors=[]
            def route(request):
                if urlsplit(request.request.url).netloc != urlsplit(origin).netloc:
                    unexpected.append(request.request.url);request.abort()
                else: request.continue_()
            context.route('**/*',route)
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(origin);expect(page.locator('#project-picker')).to_be_hidden()
            f.f.granted=True;page.click('#login-button')
            expect(page.locator('#project-select')).to_have_value('alpha',timeout=15000)
            page.locator('#tasks button').first.click();expect(page.locator('#script')).to_contain_text('Original')
            page.select_option('#decision','reject');page.fill('#feedback','Only alpha changes');page.click('#prepare-review')
            expect(page.locator('#confirmation')).to_be_visible()
            other=context.new_page();other.on('pageerror',lambda e:errors.append(str(e)));other.goto(origin+'/#beta')
            expect(other.locator('#project-select')).to_have_value('beta');other.locator('#tasks button').first.click()
            expect(other.locator('#script')).to_have_text('Other project script')
            page.click('#commit');expect(page.locator('#task-state')).to_contain_text('已退回')
            other.click('#refresh');other.locator('#tasks button').first.click();expect(other.locator('#task-state')).to_contain_text('等待脚本审核')
            # Switching clears private content, unsent text, plans and media URLs.
            other.fill('#feedback','unsent beta feedback');other.select_option('#project-select','alpha')
            expect(other.locator('#detail')).to_be_hidden();expect(other.locator('#confirmation')).to_be_hidden()
            expect(other.locator('#feedback')).to_have_value('');expect(other.locator('#script')).to_have_text('')
            # A late alpha response must not repaint the currently selected beta project.
            held=[]
            other.route('**/p/alpha/api/tasks',lambda r:held.append(r))
            other.click('#refresh')
            for _ in range(20):
                if held: break
                other.wait_for_timeout(50)
            assert held
            other.select_option('#project-select','beta');expect(other.locator('#tasks button')).to_have_count(1)
            held.pop().fulfill(status=200,content_type='application/json',body=json.dumps({'items':[{'id':'STALE_ALPHA','revision':1,'state':'failed'}],'skus':[],'can_import':True,'has_more':False}))
            expect(other.locator('#tasks')).not_to_contain_text('STALE_ALPHA')
            other.unroute('**/p/alpha/api/tasks')
            # Real H264 playback uses an explicitly project-scoped media request.
            media=root/'sample.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=blue:s=160x120:d=1','-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',str(media)],check=True)
            f.media('beta',media.read_bytes());other.click('#refresh');other.locator('#tasks button').first.click()
            expect(other.locator('#video')).to_have_attribute('src',__import__('re').compile('/p/beta/media/'))
            other.locator('#video').evaluate('(v)=>v.play()')
            for _ in range(50):
                if other.locator('#video').evaluate('(v)=>v.readyState>=2 && v.currentTime>0'): break
                other.wait_for_timeout(100)
            assert other.locator('#video').evaluate('(v)=>v.readyState>=2 && v.currentTime>0')
            old_media=other.locator('#video').get_attribute('src')
            f.revoke('beta');other.click('#refresh');expect(other.locator('#detail')).to_be_hidden()
            assert context.request.get(origin+old_media).status==409
            other.reload();expect(other.locator('#project-select option')).to_have_count(1)
            other.set_viewport_size({'width':390,'height':844})
            assert other.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.click('#logout');expect(page.locator('#login')).to_be_visible()
            other.click('#refresh');expect(other.locator('#detail')).to_be_hidden()
            assert not unexpected and not errors
            browser.close()
        print(json.dumps({'portal_browser':'PASS','projects':2,'shared_cookie_tabs':2,'synthetic_identity':True,'real_employee_acceptance':False}))
    finally:f.close()
