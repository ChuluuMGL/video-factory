"""Cloud Chrome drives the actual private-input bridge; no real secrets."""
import json
from pathlib import Path
import socket
import sys
import threading
from playwright.sync_api import sync_playwright, expect
from video_factory.setup_browser import BrowserInput
out=Path(sys.argv[1]);out.mkdir(exist_ok=True)
with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
ui=BrowserInput(port,30);answers=[]
def wizard():
    ui.write('欢迎使用 Video Factory 安装向导')
    answers.append(ui.read('项目名称'))
    answers.append(ui.hidden('管理员密码'))
    ui.write('配置已保存，等待下一阶段。')
    ui.finish({'error':'AUTH_FAILED'})
    ui.close()
thread=threading.Thread(target=wizard,daemon=True);thread.start()
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
        page=browser.new_page();responses=[];errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('response',lambda r:responses.append(r.text()) if '/api/' in r.url else None)
        page.goto(ui.url);expect(page.locator('#label')).to_have_text('项目名称');assert '#' not in page.url
        page.fill('#answer','浏览器测试项目');page.locator('#form button').click()
        expect(page.locator('#label')).to_have_text('管理员密码');expect(page.locator('#answer')).to_have_attribute('type','password')
        page.fill('#answer','synthetic-hidden-browser-password');page.locator('#form button').click()
        expect(page.locator('#messages')).to_contain_text('配置已保存')
        expect(page.locator('#status')).to_contain_text('管理员密码未通过验证')
        expect(page.locator('#answer')).to_have_value('')
        expect(page.locator('#form')).to_be_hidden()
        thread.join(3);assert answers==['浏览器测试项目','synthetic-hidden-browser-password']
        assert not thread.is_alive()
        # The real listener has closed; the page must retain its terminal receipt.
        page.wait_for_timeout(1000)
        expect(page.locator('#status')).to_contain_text('管理员密码未通过验证')
        assert 'synthetic-hidden-browser-password' not in '\n'.join(responses)+page.locator('body').inner_text()
        assert not errors
        page.screenshot(path=str(out/'setup-browser.png'))
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(out/'setup-browser-mobile.png'));browser.close()
    (out/'setup-browser.json').write_text(json.dumps({'status':'PASS','human_secret_input':'synthetic_browser_only','response_and_ui_secret_echo':False,'terminal_receipt_survives_listener_close':True,'real_human':'not_run'}))
finally:ui.close()
