"""Cloud-only comparison against the hydrated official AI page in the same Chrome."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from playwright.sync_api import sync_playwright

root = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *_): pass
server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(root)))
threading.Thread(target=server.serve_forever, daemon=True).start()
url = 'https://www.yueyu.tech/zh/solutions/ai-workflow-models'
report = {'official_url': url, 'comparisons': [], 'intentional_adaptations': [
    'Guide body and controls remain 16–18px or larger for readability.',
    'White primary-action text uses #2563eb for contrast; brand accent remains #3b82f6.',
    'Application headings are sized for task forms, with the same family and medium weight.'
]}

def styles(page, selector):
    return page.locator(selector).first.evaluate('''el => {
      const s=getComputedStyle(el); return Object.fromEntries(
      ['fontFamily','fontWeight','fontSize','lineHeight','letterSpacing','color','backgroundColor','borderRadius'].map(k=>[k,s[k]]));
    }''')

def fonts(page):
    page.evaluate('''() => { const e=document.createElement('span');e.id='brand-font-probe';
      e.textContent='商品资料生成视频审核下载';e.style.fontWeight='500';document.body.append(e); }''')
    cdp = page.context.new_cdp_session(page)
    cdp.send('DOM.enable'); cdp.send('CSS.enable')
    doc = cdp.send('DOM.getDocument')
    node = cdp.send('DOM.querySelector', {'nodeId':doc['root']['nodeId'], 'selector':'#brand-font-probe'})
    result = cdp.send('CSS.getPlatformFontsForNode', {'nodeId':node['nodeId']})['fonts']
    page.locator('#brand-font-probe').evaluate('(e)=>e.remove()'); cdp.detach()
    return sorted({f['familyName'] for f in result if f['glyphCount']})

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', args=['--no-sandbox'])
        ctx = browser.new_context(reduced_motion='reduce')
        official = ctx.new_page(); local = ctx.new_page()
        for width, height, label in ((1440,1000,'desktop'), (390,844,'mobile')):
            for page in (official,local): page.set_viewport_size({'width':width,'height':height})
            assert official.goto(url, wait_until='networkidle', timeout=60000).ok
            official.locator('.ai-workflow-page #business-challenges h1').wait_for()
            official.evaluate('document.fonts.ready')
            assert local.goto(f'http://127.0.0.1:{server.server_port}').ok
            local.evaluate('document.fonts.ready')
            a=styles(official,'.ai-workflow-page #business-challenges h1'); b=styles(local,'h1')
            af=fonts(official); bf=fonts(local)
            row={'viewport':label,'official_heading':a,'guide_heading':b,'official_actual_chinese_fonts':af,'guide_actual_chinese_fonts':bf}
            report['comparisons'].append(row)
            for key in ('fontFamily','fontWeight','fontSize','lineHeight','letterSpacing'):
                assert a[key]==b[key], (label,key,a[key],b[key])
            assert af and af==bf, (af,bf)
            assert styles(official,'.ai-workflow-page')['backgroundColor']==styles(local,'body')['backgroundColor']
            assert styles(official,'#business-challenges h1 span:last-child')['color']==styles(local,'h1 em')['color']
            assert float(styles(local,'.primary')['borderRadius'].removesuffix('px'))>=9999
            assert local.evaluate('document.documentElement.scrollWidth <= innerWidth')
            official.screenshot(path=str(out/f'official-{label}.png'))
            local.screenshot(path=str(out/f'aligned-{label}.png'))
        # Verify the exact shipped stylesheets for each installed surface too.
        # Runtime workflows separately cover authenticated setup/review behavior.
        for surface in ('setup','wizard','review'):
            css=Path(f'packages/video-factory-core/src/video_factory/{surface}_assets/style.css').read_text()
            page=ctx.new_page(); page.set_content('<!doctype html><html lang="zh-CN"><body><main><h1>安装与项目工作区</h1><p>商品资料生成视频审核下载</p><button>继续配置</button></main></body></html>')
            page.add_style_tag(content=css)
            assert styles(page,'h1')['fontFamily']==a['fontFamily']
            assert styles(page,'h1')['fontWeight']=='500'
            assert fonts(page)==af
            report[surface]={'heading':styles(page,'h1'),'actual_chinese_fonts':fonts(page)}
            page.close()
        report['status']='PASS'; browser.close()
finally:
    server.shutdown()
    (out/'brand-parity.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
