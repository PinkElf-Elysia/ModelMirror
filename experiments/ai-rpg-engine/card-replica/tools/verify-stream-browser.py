"""Offline real-browser regression. Requires Python Playwright + Chromium.
Run after npm ci in the engine and card-replica packages. No provider calls.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlparse
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
PORT = 19871
BASE = f"http://127.0.0.1:{PORT}"
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-src 'self'; base-uri 'none'; form-action 'none'"

async def verify(browser, card, mobile=False):
    context = await browser.new_context(viewport={"width":390 if mobile else 1440,"height":844 if mobile else 1000})
    page = await context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    state = dict(text='', request=None, turn=None, cancelled=False, supported=True, outage=False, config_failure=False)
    release = asyncio.Event()
    started = asyncio.Event()
    async def respond(route, value, status=200):
        await route.fulfill(status=status, content_type='application/json', body=json.dumps(value, ensure_ascii=False))
    def session():
        return dict(id='fixture',name='流式排版离线验收',revision=1 if state['turn'] else 0,mode='real',characterText='中性旅人',turns=[state['turn']] if state['turn'] else [],requests={state['request']:{'status':'complete' if state['turn'] else 'cancelled' if state['cancelled'] else 'pending'}} if state['request'] else {})
    async def routing(route):
        url = urlparse(route.request.url)
        if url.hostname != '127.0.0.1':
            raise AssertionError('Unexpected external request: '+url.hostname)
        path = url.path
        if path == f'/rpg-app/{card}/':
            await route.fulfill(content_type='text/html', body=(ROOT/'tests/stream-browser.html').read_text(encoding='utf-8'),headers={'Content-Security-Policy':CSP})
        elif path.startswith('/rpg-app/custom-config/'):
            if state['config_failure'] and path.endswith('/worker.mjs'):
                await route.fulfill(status=503,body='unavailable');return
            file = ROOT.parent/'plugins/custom-config/public'/path.rsplit('/',1)[-1]
            await route.fulfill(content_type='text/javascript',body=file.read_text(encoding='utf-8'))
        elif '/api/' in path:
            if path.endswith('/message-tools/progress'):
                if state['outage']: await respond(route,{'error':'FIXTURE_OUTAGE'},503)
                else: await respond(route,{'supported':state['supported'],'text':state['text']})
            elif path.endswith('/send'):
                body=route.request.post_data_json
                body=body.get('request',body)
                state['request']=body['requestId']; started.set()
                await release.wait()
                if not state['cancelled']:
                    state['turn']=dict(requestId=state['request'],input=body['input'],raw=state['text'],html='<details><summary>状态</summary><p>旅人抵达港口。</p></details>',at='2026-01-01')
                await respond(route,session())
            elif path.endswith('/cancel'):
                state['cancelled']=True; release.set(); await respond(route,session())
            elif path.endswith('/plugins/rpg.message-tools'):
                await respond(route,dict(enabled=True,installed=True,sessionRevision=0,registryRevision=1))
            elif path.endswith('/plugins'):
                await respond(route,dict(revision=1,plugins=[dict(id='rpg.message-tools',name='消息工具',version='1',artifactSha256='fixture',manifestSha256='fixture',permissions=[],installed=True)]))
            elif path.endswith('/status'): await respond(route,dict(budget=dict(remaining=10,used=0,reserved=0,limit=10)))
            elif path.endswith('/sessions'): await respond(route,[])
            elif path.endswith('/sessions/fixture'): await respond(route,session())
            else: await respond(route,{'error':'UNEXPECTED_FIXTURE_API'},404)
        else: await route.continue_()
    await context.route('**/*',routing)
    await page.goto(BASE+f'/rpg-app/{card}/')
    await page.get_by_role('button',name='完整输出',exact=True).click()
    await page.get_by_role('textbox',name='对话输入').fill('旅人抵达港口，观察四周。')
    await page.get_by_role('button',name='发送',exact=True).click()
    await asyncio.wait_for(started.wait(),10)
    stream=page.get_by_role('region',name='正在生成的回复')
    state['text']='<details><summary>状态</summary>\n\n**旅人**抵达港口。'
    await expect(stream.locator('summary')).to_have_text('状态')
    await expect(stream.locator('strong')).to_have_text('旅人')
    await stream.locator('summary').click()
    await stream.locator('summary').focus()
    state['text']+='\n\n| 时间 | 地点 |\n| --- | --- |\n| 清晨 | 港口 |\n'
    await expect(stream.locator('table')).to_be_visible()
    assert await stream.locator('details').evaluate('(e)=>e.open')
    assert await stream.locator('summary').evaluate('(e)=>e===document.activeElement')
    state['text']+='\n```html\n<div>示例代码</div>\n'
    await expect(stream.locator('code')).to_contain_text('<div>示例代码</div>')
    state['text']+='```\n<script>window.bad=true</script></details>'
    await expect(stream.locator('script')).to_have_count(0)
    assert await page.evaluate('window.bad===undefined')
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    if card in ('god','douluo'):
        state['text']+='<label class="flip-button-wrapper"><input type="checkbox" class="flip-toggle">切换视角</label>'
        await stream.locator('label.flip-button-wrapper').click()
        await expect(stream.get_by_role('checkbox')).to_be_checked()
        state['text']+='更新视角。'
        await expect(stream).to_contain_text('更新视角。')
        await expect(stream.get_by_role('checkbox')).to_be_checked()
    if card == 'douluo':
        state['text']+='<div class="live-container"><div class="topic">港口直播</div><div class="danmu-area"><marquee><span>旅人一路平安</span></marquee></div></div><div class="aa"><label><input class="bb" type="checkbox"><img src="https://invalid.example/avatar.png"></label><div class="cc"><div class="dd">吐槽：别忘了带地图。</div></div></div>'
        await expect(stream.locator('.live-container')).to_be_visible()
        await expect(stream.locator('.danmu-track')).to_have_count(1)
        await stream.locator('.commentary-placeholder').click()
        await expect(stream.locator('.aa input.bb')).to_be_checked()
        await expect(stream.locator('.aa .cc')).to_be_visible()
        await expect(stream.locator('.aa img')).to_have_count(0)
        assert await stream.locator('.live-container').evaluate('(e)=>getComputedStyle(e).backgroundColor') != 'rgba(0, 0, 0, 0)'
    # A transient polling failure retains the last safe frame and recovers.
    state['outage']=True
    await expect(page.get_by_role('status').filter(has_text='实时预览暂不可用')).to_be_visible()
    await expect(stream.locator('table')).to_be_visible()
    state['outage']=False
    await expect(page.get_by_role('status').filter(has_text='实时预览暂不可用')).to_have_count(0)
    await page.get_by_role('button',name='流式输出',exact=True).click()
    await expect(stream).to_have_count(0)
    await page.get_by_role('button',name='完整输出',exact=True).click()
    await expect(stream.locator('table')).to_have_count(1)
    # Install the actual display-only regex plugin in the isolated browser store.
    await page.evaluate("""async () => {
      const {empty,save}=await import('/rpg-app/custom-config/config.mjs');
      const c=empty(); c.rules=[{id:'test',name:'强调旅人',pattern:'旅人',replacement:'<em>访客</em>',flags:'g',scope:'assistant',enabled:true,renderOnly:true}];
      await save('global',c,0); localStorage.setItem('rpg-custom-config-used','1');
      const channel=new BroadcastChannel('rpg-custom-config');channel.postMessage('changed');channel.close();
    }""")
    await expect(stream.locator('em').first).to_have_text('访客')
    state['text']+='\n继续前行。'
    await expect(stream).to_contain_text('继续前行。')
    await expect(stream.locator('em').first).to_have_text('访客')
    # An empty display-only projection must clear the draft, not reveal raw.
    await page.evaluate("""async () => {
      const {empty,save}=await import('/rpg-app/custom-config/config.mjs');
      const c=empty(); c.rules=[{id:'hide',name:'隐藏',pattern:'.+',replacement:'',flags:'gs',scope:'assistant',enabled:true,renderOnly:true}];
      await save('global',c,1);
      const channel=new BroadcastChannel('rpg-custom-config');channel.postMessage('changed');channel.close();
    }""")
    await expect(stream.locator('.model-output')).to_have_text('')
    await page.evaluate("""async () => {
      const {empty,save}=await import('/rpg-app/custom-config/config.mjs');
      const c=empty(); c.rules=[{id:'test',name:'强调旅人',pattern:'旅人',replacement:'<em>访客</em>',flags:'g',scope:'assistant',enabled:true,renderOnly:true}];
      await save('global',c,2);
      const channel=new BroadcastChannel('rpg-custom-config');channel.postMessage('changed');channel.close();
    }""")
    await expect(stream.locator('em').first).to_have_text('访客')
    # A failed display worker must not reveal unprocessed source; retry is local.
    state['config_failure']=True
    await page.evaluate("() => {const c=new BroadcastChannel('rpg-custom-config');c.postMessage('changed');c.close();}")
    await expect(stream.get_by_role('button',name='重试显示',exact=True)).to_be_visible()
    await expect(stream.locator('.model-output')).to_have_text('')
    state['config_failure']=False
    await stream.get_by_role('button',name='重试显示',exact=True).click()
    await expect(stream.locator('em').first).to_have_text('访客')
    await stream.locator('summary').click()
    evidence=ROOT.parent/'.rpg04-work/stream-browser';evidence.mkdir(parents=True,exist_ok=True)
    await page.screenshot(path=str(evidence/f'{card}-{mobile}.png'),full_page=True)
    release.set()
    await expect(stream).to_have_count(0)
    await expect(page.locator('.conversation em').first).to_have_text('访客')
    assert state['turn']['raw']==state['text']
    # Next request has no previous draft, including after a cancelled response.
    state.update(text='',request=None,cancelled=False);release.clear();started.clear()
    await page.get_by_role('textbox',name='对话输入').fill('再看看天空。')
    await page.get_by_role('button',name='发送',exact=True).click()
    await asyncio.wait_for(started.wait(),10)
    await expect(stream).to_have_count(0)
    state['text']='<details><summary>新回合</summary>天空晴朗。'
    await expect(stream.locator('summary')).to_have_text('新回合')
    await page.get_by_role('button',name='取消生成').click()
    await expect(stream).to_have_count(0)
    assert not errors,errors
    await context.close()
    print(f'PASS {card} mobile={mobile}: progressive UI, disclosure/focus, fences, XSS, outage, toggle, real config, final handoff, new request/cancel')

async def main():
    process=subprocess.Popen(['node','node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',str(PORT),'--strictPort'],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            for card,mobile in [('earth',False),('god',False),('cultivation',True),('douluo',False)]:
                await verify(browser,card,mobile)
            await browser.close()
    finally:
        process.terminate();process.wait(timeout=10)

if __name__=='__main__': asyncio.run(main())
