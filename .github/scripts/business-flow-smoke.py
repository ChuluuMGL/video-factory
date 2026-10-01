"""Actual Chrome: SKU script, rejection repair, H3 plan, decoded video and download.

All OAuth and model providers are synthetic, real browser/ledger/media decoder.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright,expect
sys.path.insert(0,str(Path('packages/video-factory-core/tests').resolve()))
from review_fixture import Fixture
from video_factory.feishu_bridge import save
from video_factory.script_jobs import ScriptJobs,profile
from video_factory.video_jobs import configure
from video_factory.automation import issue_execution
from video_factory.dispatch import dispatch_one
from video_factory.worker import Worker

out=Path(sys.argv[1]);out.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);f=Fixture(tmp);key=Fernet.generate_key()
    with f.store.connect() as db:save(db,'setup:project:'+f.project,{'configuration':{'project':{'base_mode':'bind','products':[{'sku_id':'sku_one','name':'Test pack','truth_source':'A plain green package, no performance claims'}]}}})
    f.store.put_secret(f.admin,'script','synthetic-script-key',key);f.store.put_secret(f.admin,'video','synthetic-video-key',key)
    profile(f.store,f.admin,f.project,'secret:script','fixture_account')
    assets=Path('/work')/('business-'+str(os.getpid()));assets.mkdir(mode=0o700,parents=True)
    image=assets/'reference.png';image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
    spec={'duration':5,'references':[{'path':'reference.png','sha256':hashlib.sha256(image.read_bytes()).hexdigest()}]}
    configure(f.store,f.admin,f.project,'secret:video','fixture_account','global',{'sku_one':{'assets_root':str(assets),'specification':spec}})
    cap=issue_execution(f.store,f.admin,f.project)['token']
    clip=root/'fixture.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=green:s=180x320:r=15:d=5','-f','lavfi','-i','anullsrc=r=44100:cl=stereo','-t','5','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(clip)],check=True)
    class Script:
        calls=0
        def generate(self,sku,brief,feedback,secret):
            self.calls+=1;return {'script':'Show the green package. '+('Use one shot.' if feedback else 'End with the package.'),'provider_id':'synthetic-script-'+str(self.calls)}
    class Video:
        calls=0
        def submit(self,body,secret):self.calls+=1;return '12345678901234'
        def poll(self,pid,secret):return {'id':pid,'status':'succeeded','url':'synthetic://video'}
        def download(self,url,stream):stream.write(clip.read_bytes())
    script,video=Script(),Video()
    jobs=ScriptJobs(f.store,client_factory=f.bridge.client_factory,provider=script)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
            page=browser.new_page();errors=[];responses=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('response',lambda r:responses.append(r.text()) if '/api/' in r.url else None)
            page.goto(f.server.origin);page.click('#login-button');f.granted=True
            expect(page.locator('#workspace')).to_be_visible(timeout=15000)
            page.locator('#create-script summary').click();page.fill('#script-task','generated_demo');page.fill('#script-brief','Show the package without inventing claims')
            page.locator('#script-form button').first.click();expect(page.locator('#plan')).to_contain_text('一次模型调用');page.click('#commit')
            expect(page.locator('#task-state')).to_contain_text('脚本生成已排队')
            jobs.step(cap,f.project,'generated_demo',1,key)
            page.click('#refresh');page.locator('#tasks button').first.click();expect(page.locator('#script')).to_contain_text('green package')
            page.select_option('#decision','reject');page.fill('#feedback','Use just one shot');page.click('#prepare-review');page.click('#commit')
            expect(page.locator('#task-state')).to_contain_text('已退回')
            page.click('#revise-script');expect(page.locator('#script-context')).to_contain_text('第 2 版');page.locator('#script-form button').first.click();page.click('#commit')
            expect(page.locator('#task-title')).to_contain_text('第 2 版')
            jobs.step(cap,f.project,'generated_demo',2,key)
            page.click('#refresh');page.locator('#tasks button').first.click();expect(page.locator('#script')).to_contain_text('one shot')
            page.click('#prepare-review');page.click('#commit');expect(page.locator('#generate-video')).to_be_visible()
            page.click('#generate-video');expect(page.locator('#plan')).to_contain_text('MiniMax-H3');page.click('#commit')
            expect(page.locator('#message')).to_contain_text('已批准本次视频生成')
            factory=lambda store:Worker(store,provider_factory=lambda _:video)
            dispatch_one(f.store,cap,f.project,key,f.media,worker_factory=factory)
            dispatch_one(f.store,cap,f.project,key,f.media,worker_factory=factory)
            page.click('#refresh');page.locator('#tasks button').first.click();expect(page.locator('#video')).to_be_visible()
            page.click('#prepare-review');expect(page.locator('#plan')).to_contain_text('视频 / 通过');page.click('#commit')
            expect(page.locator('#download-video')).to_be_visible()
            with page.expect_download() as downloading:page.click('#download-video')
            artifact=downloading.value;path=artifact.path();assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==hashlib.sha256(clip.read_bytes()).hexdigest()
            page.screenshot(path=str(out/'business-flow.png'),full_page=True)
            assert not errors,errors
            assert script.calls==2 and video.calls==1
            for secret in ('synthetic-script-key','synthetic-video-key',cap,'synthetic-user-token'):
                assert secret not in '\n'.join(responses)
            browser.close()
        (out/'business-flow.json').write_text(json.dumps({'status':'PASS','sku_script_repair_review_video_decode_accept_download':'PASS','script_provider_calls_synthetic':2,'video_provider_calls_synthetic':1,'paid_model_requests':0,'real_feishu_authorization':'not_run','human_acceptance':'not_run'}))
    finally:
        f.close();image.unlink();assets.rmdir()
