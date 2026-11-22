import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {createRequire} from 'node:module';
import {prompts} from './session.mjs';
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:'+Number(process.env.FRONTIER_PORT||18974)+'/rpg-app/ancient/chat/';
const atlasPath=process.env.ATLAS_CAPTURE_DIR;
if(atlasPath)await mkdir(atlasPath,{recursive:true});
const mapName='展开全国舆图';
async function map(page){await page.getByRole('button',{name:new RegExp(mapName)}).click();const dlg=page.getByRole('dialog',{name:'大明舆图'});await dlg.waitFor();return dlg;}
async function capture(page,name){if(atlasPath)await page.screenshot({path:join(atlasPath,name+'.png')});}
const status=await (await fetch(base+'api/status')).json();
assert.deepEqual(status,{providerEnabled:false,frontier:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
  const p=await browser.newPage({viewport:{width:1440,height:960}});p.setDefaultTimeout(9000);
  p.on('pageerror',e=>console.error('pageerror',e.message));
  await p.goto(base);
  if(process.env.ANCIENT_STORY_MAP==='1'){
    await p.evaluate(async()=>{
      const {empty,save}=await import('/rpg-app/custom-config/config.mjs');
      const config=empty();config.depth=1;config.system='朝议行文从简。';
      config.worldbook.entries.push({...config.worldbook.defaults,id:'east',name:'青册',enabled:true,order:0,depth:0,keywords:['东番'],categoryId:null,regex:false,chance:100,mode:'regular',body:'御前称地点登记卷为青册。',contents:[]});
      await save('global',config,0);localStorage.setItem('rpg-custom-config-used','1');
    });
  }
  const f=p.frameLocator('iframe[title="古代人生开局面板"]');
  await f.getByRole('button',{name:'踏入此世'}).click();
  await f.locator('.route-item').filter({hasText:'帝王之路'}).click();
  const next=()=>f.locator('.step.active .nav button[onclick="next()"]').click();
  await next();
  await f.locator('#empList .acc-head').filter({has:f.locator('h4',{hasText:/^明$/})}).click();
  await f.locator('.emp-row').filter({hasText:'崇祯帝朱由检'}).click();
  await next();await next();await next();
  const scroll=await f.locator('#pvBox').innerText();
  assert.match(scroll,/【帝王】崇祯帝朱由检/);
  await f.locator('#open-chat').click();
  const input=p.getByRole('textbox',{name:'对话输入'}),send=p.getByRole('button',{name:'发送',exact:true});
  assert.equal(await input.inputValue(),scroll);
  let count=0;
  async function submit(text,expected){
    if(text!==null)await input.fill(text);
    await send.click();
    const out=p.locator('.conversation article .model-output').nth(count++);
    await out.waitFor();assert.match(await out.innerText(),expected);
    const open=await p.locator('dialog.ming-atlas[open]').count();assert.equal(open,0);
    await p.waitForFunction(()=>document.querySelector('textarea[aria-label="对话输入"]')?.value==='');
  }
  await submit(null,/京师/);
  if(process.env.ANCIENT_PLACE_REGISTER==='1'){
    const {checkPlaceRegister}=await import('./place-register-browser.mjs');
    await checkPlaceRegister(p,browser,submit,map);
  }else{
  if(process.env.ANCIENT_ATLAS_QUERIES==='1'){
    await submit('请比较吐鲁番与叶尔羌。',/分治/);
    await submit('科尔沁与后金有什么关系？',/通婚结盟/);
  }
  if(atlasPath) {
    await input.fill('尚在斟酌的奏问');
    const revision=await (await fetch(base+'api/sessions/'+new URL(p.url()).searchParams.get('session'))).json();
    const dlg=await map(p);
    assert.equal(await dlg.locator('[data-region]').count(),15);
    await capture(p,'01-country');
    await dlg.getByLabel('查阅地点').selectOption('jiangxi');
    assert.match(await dlg.locator('aside').innerText(),/南昌府/);
    await dlg.getByRole('button',{name:'定位辽东'}).click();
    assert.match(await dlg.locator('aside').innerText(),/京师尚未收到关于宁远的新报/);
    const near=await dlg.locator('svg').getAttribute('viewBox');
    await dlg.getByLabel('查阅地点').selectOption('guizhou');
    assert.match(await dlg.locator('aside').innerText(),/平越/);
    assert.notEqual(await dlg.locator('svg').getAttribute('viewBox'),near);
    await dlg.getByRole('button',{name:'放大'}).click();
    const graphic=dlg.locator('svg'),beforeDrag=await graphic.getAttribute('viewBox'),box=await graphic.boundingBox();
    await p.mouse.move(box.x+box.width*.55,box.y+box.height*.7);
    await p.mouse.down();await p.mouse.move(box.x+box.width*.55+60,box.y+box.height*.7+20,{steps:8});await p.mouse.up();
    assert.notEqual(await graphic.getAttribute('viewBox'),beforeDrag);
    await p.keyboard.press('Escape');await dlg.waitFor({state:'hidden'});
    assert.equal(await input.inputValue(),'尚在斟酌的奏问');
    assert.equal((await (await fetch(base+'api/sessions/'+revision.id)).json()).revision,revision.revision);
  }
  await submit(prompts.ask,/尚无新报/);
  await submit(prompts.report,/关报呈到御前/);
  if(atlasPath){
    const dlg=await map(p);await dlg.getByRole('button',{name:'定位辽东'}).click();
    assert.match(await dlg.locator('aside').innerText(),/欠饷鼓噪/);
    assert.equal(await dlg.locator('[data-city=ningyuan]').getAttribute('data-holder'),'明');
    await capture(p,'02-report');await p.keyboard.press('Escape');
  }
  await submit(prompts.ask,/欠饷鼓噪/);
  await submit(prompts.draft,/等你用印/);
  await submit(prompts.decree,/宁远仍登记在明/);
  await submit(prompts.receive,/交割/);
  await submit(prompts.handover,/待你亲自确认/);
  assert.match(await p.locator('.frontier-register p').first().innerText(),/守方 明/);
  await p.getByRole('button',{name:prompts.settle,exact:true}).click();
  assert.equal(await input.inputValue(),prompts.settle);
  await submit(null,/明 → 后金/);
  const url=p.url();await p.close();
  const saved=new URL(url).searchParams.get('session');assert.ok(saved);
  const reopen=await browser.newPage();await reopen.goto(base);
  await reopen.getByRole('button',{name:'会话历史'}).click();
  const history=reopen.getByRole('dialog').getByRole('button',{name:new RegExp(saved.slice(0,6))});
  await history.click();
  assert.equal(new URL(reopen.url()).searchParams.get('session'),saved);
  await reopen.getByRole('textbox',{name:'对话输入'}).fill('接上次，告诉我宁远现在归谁。');
  await reopen.getByRole('button',{name:'发送',exact:true}).click();
  const continued=reopen.locator('.conversation article .model-output').nth(count);
  await continued.waitFor();assert.match(await continued.innerText(),/宁远现登记为后金/);
  assert.equal(await reopen.locator('dialog.ming-atlas[open]').count(),0);
  const dlg=await map(reopen);await dlg.getByRole('button',{name:'定位辽东'}).click();
  assert.deepEqual(await dlg.locator('[data-city]').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('data-holder'))),['明','明','后金','明']);
  assert.match(await dlg.locator('aside').innerText(),/京师第.*刻获知/);
  await capture(reopen,'03-restored');
  if(atlasPath){
    await reopen.setViewportSize({width:390,height:844});
    await dlg.getByRole('button',{name:'全国总览'}).click();
    await dlg.getByRole('button',{name:'定位辽东'}).click();
    assert.equal(await reopen.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
    await capture(reopen,'04-mobile');
  }
  console.log(JSON.stringify({url:reopen.url(),turns:count+1,checked:'命书、获报前后同问、交割、关闭重开、续写后首次打开地图'}));
  }
}finally{await browser.close();}
