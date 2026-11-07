"""Production UI and actual local session/summary/memory HTTP integration; offline only."""
import asyncio,json,os,subprocess
from pathlib import Path
from playwright.async_api import async_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
async def main():
 process=subprocess.Popen(['node','tools/preview-session-history.mjs','19874'],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
 try:
  line=await asyncio.wait_for(asyncio.to_thread(process.stdout.readline),45)
  if not line:raise RuntimeError(process.stderr.read())
  evidence=ROOT.parent/'.rpg04-work/session-history-browser';evidence.mkdir(parents=True,exist_ok=True)
  fixture=json.loads(line);base=fixture['origin']+'/rpg-app/xingjian/api/';path='sessions/'+fixture['id']
  async with async_playwright() as p:
   browser=await p.chromium.launch();context=await browser.new_context(viewport={'width':1440,'height':1000});page=await context.new_page();errors=[]
   page.on('pageerror',lambda error:errors.append(str(error)))
   async def inspect_response(response):
    if response.status>=400 and response.status!=410 and '/api/' in response.url:print('HTTP',response.status,response.url,await response.text(),flush=True)
   page.on('response',inspect_response)
   async def get(endpoint):return await (await context.request.get(base+endpoint)).json()
   async def settled(turns):
    for _ in range(120):
     s=await get(path);m=await get(path+'/memory-palace')
     if len(s.get('turns',[]))==turns and not m.get('busy') and m.get('processedThrough')==turns:return s
     await asyncio.sleep(.15)
    raise AssertionError('memory did not settle')
   before=await get(path);await page.goto(fixture['url']);await expect(page.locator('.conversation article')).to_have_count(2)
   await page.get_by_role('button',name='↻ 重新生成',exact=True).first.click()
   await page.get_by_role('dialog',name='重新生成此回合').screenshot(path=str(evidence/'regenerate.png'))
   await page.get_by_role('button',name='保留版本并重新生成',exact=True).click()
   await expect(page.locator('.conversation article')).to_have_count(1,timeout=20000)
   after=await settled(1);assert page.url==fixture['url'];assert after['id']==before['id'];assert len(await get('sessions'))==1
   await page.get_by_role('button',name='历史版本',exact=True).click()
   await page.get_by_role('dialog',name='历史版本').screenshot(path=str(evidence/'versions.png'))
   await page.get_by_role('dialog',name='历史版本').get_by_role('button',name='恢复',exact=True).first.click()
   await page.get_by_role('button',name='确认恢复',exact=True).click()
   await expect(page.locator('.conversation article')).to_have_count(2)
   restored=await get(path);assert [t['raw'] for t in restored['turns']]==[t['raw'] for t in before['turns']]
   assert (await get(path+'/memory-palace'))['processedThrough']==2
   # Delete and reload the tombstone URL; restore via the actual recycle bin.
   await page.get_by_role('button',name='删除会话：'+before['name'],exact=True).click()
   await page.get_by_role('dialog',name='移入回收站').get_by_role('button',name='移入回收站',exact=True).click()
   await expect(page.get_by_role('button',name='打开回收站',exact=True)).to_be_visible()
   assert len(await get('sessions'))==0;assert len(await get('session-trash'))==1
   await page.reload();await page.get_by_role('button',name='回收站',exact=True).click()
   await page.get_by_role('dialog',name='回收站').screenshot(path=str(evidence/'trash.png'))
   await page.get_by_role('dialog',name='回收站').get_by_role('button',name='恢复',exact=True).click()
   await page.get_by_role('button',name='确认恢复',exact=True).click();await expect(page.locator('.conversation article')).to_have_count(2)
   # Lose the rewind response after server commit. Recovery must not dispatch a send.
   lost={'once':True};lost_committed=asyncio.Event()
   async def lose_response(route):
    if route.request.method=='POST' and lost['once']:
     lost['once']=False;response=await route.fetch();assert response.ok;lost_committed.set();await route.abort()
    else:await route.continue_()
   await page.route('**/lifecycle',lose_response)
   await page.get_by_role('button',name='↻ 重新生成',exact=True).first.click();await page.get_by_role('button',name='保留版本并重新生成',exact=True).click()
   await asyncio.wait_for(lost_committed.wait(),10)
   await expect(page.get_by_role('button',name='核对会话操作',exact=True)).to_be_visible()
   assert len((await get(path))['turns'])==0
   await page.reload();await page.get_by_role('button',name='核对会话操作',exact=True).click()
   await expect(page.get_by_role('button',name='核对会话操作',exact=True)).to_have_count(0)
   assert len((await get(path))['turns'])==0
   await expect(page.get_by_role('textbox',name='对话输入')).to_have_value(before['turns'][0]['input'])
   assert len((await get(path+'/lifecycle'))['versions'])==3
   evidence=ROOT.parent/'.rpg04-work/session-history-browser';evidence.mkdir(parents=True,exist_ok=True)
   await page.get_by_role('button',name='历史版本',exact=True).click();await expect(page.get_by_role('dialog',name='历史版本').get_by_role('button',name='恢复',exact=True)).to_have_count(3);await page.screenshot(path=str(evidence/'history.png'),full_page=True)
   assert not errors,errors
   await browser.close()
   print('PASS actual HTTP + production UI: same ID rewrite, summary/memory restore, trash/reload/restore, lost response recovery without resend')
 finally:
  process.terminate();process.wait(timeout=10)
if __name__=='__main__':asyncio.run(main())
