// @vitest-environment node
import {createServer, type Server} from 'node:http';
import {JSDOM} from 'jsdom';
import {afterAll,beforeAll,expect,it} from 'vitest';
import {serveTypeMoon} from '../../../experiments/ai-rpg-engine/type-moon-card/assets.mjs';
import {cardRoot,worlds} from '../../../experiments/ai-rpg-engine/card-replica/src/presentation';

let server:Server, origin:string;
beforeAll(async()=>{
  server=createServer(async(req,res)=>{
    if(!await serveTypeMoon(req,res,new URL(req.url!,origin).pathname,[origin]))res.writeHead(404).end();
  });
  await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
  const address=server.address();
  if(!address||typeof address==='string')throw Error('No port');
  origin='http://127.0.0.1:'+address.port;
});
afterAll(()=>server.close());

it('renders the registered card, preserves role-specific selections and enforces skill limits',async()=>{
  expect(cardRoot('type-moon')).toBe('/rpg-app/type-moon/chat/');
  expect(worlds.filter(w=>w.id==='type-moon')).toHaveLength(1);
  const base=origin+'/rpg-app/type-moon/';
  const response=await fetch(base);
  expect(response.headers.get('content-security-policy')).toContain("connect-src 'none'");
  const dom=new JSDOM(await response.text(),{url:base+'?chat=1',runScripts:'dangerously',beforeParse(w){
    w.scrollTo=()=>{};
    w.matchMedia=()=>({matches:true}) as MediaQueryList;
    w.HTMLCanvasElement.prototype.getContext=(()=>null) as typeof w.HTMLCanvasElement.prototype.getContext;
  }});
  try{
    const doc=dom.window.document;
    const button=(name:string)=>{const el=[...doc.querySelectorAll('button')].find(b=>b.textContent===name);expect(el,name).toBeTruthy();el!.click();};
    const enter=(label:string,value:string)=>{const el=[...doc.querySelectorAll('label')].find(l=>l.childNodes[0]?.textContent===label)!.querySelector('input,textarea') as HTMLInputElement;el.value=value;el.dispatchEvent(new dom.window.Event('input'));};
    expect(doc.querySelectorAll('nav button')).toHaveLength(9);
    button('魔术师故事线');expect(doc.querySelectorAll('.choice')).toHaveLength(17);
    (doc.querySelector('.choice') as HTMLButtonElement).click();
    expect(doc.querySelectorAll('.entry')).toHaveLength(9);
    (doc.querySelector('.entry input') as HTMLInputElement).click();
    button('天赋抽取');const candidates=doc.querySelectorAll<HTMLButtonElement>('.choice');
    expect(candidates).toHaveLength(10);
    candidates[0].click();(doc.querySelectorAll('.choice')[1] as HTMLButtonElement).click();(doc.querySelectorAll('.choice')[2] as HTMLButtonElement).click();
    expect(doc.querySelectorAll('.choice[aria-pressed=true]')).toHaveLength(2);
    button('魔术师登录');enter('姓名','<观察者>');button('生成档案文本');
    expect(doc.querySelector('.preview')!.textContent).toContain('姓名: <观察者>');
    expect(doc.querySelector('.preview')!.textContent).toContain('Fate/Grand Order (序章)');
    button('英灵切入点');expect(doc.querySelectorAll('.choice')).toHaveLength(14);
    button('英灵登录');enter('真名 (TRUE NAME)','夜');button('生成英灵档案');
    expect(doc.querySelector('.preview')!.textContent).toContain('真名: 夜');
    expect(doc.querySelector('.preview')!.textContent).not.toContain('观察者');
    expect(doc.querySelector('.preview')!.textContent).toContain('（未选择）');
    expect(doc.querySelector('.preview')!.textContent).toContain('筋力: C | 耐久: C');
    expect((await fetch(base+'fate.mp3')).headers.get('content-type')).toBe('audio/mpeg');
    button('NPC档案');expect(doc.querySelectorAll('.portrait img')).toHaveLength(30);
    expect((await fetch(base+'portraits/npc-30.webp')).headers.get('content-type')).toBe('image/webp');
    expect((await fetch(base+'resources/archive-ui.json')).status).toBe(404);
    button('世界观');expect(doc.querySelectorAll('details')).toHaveLength(16);
    button('更新日志');expect(doc.querySelector('.log')!.textContent).toContain('首次发布');
    button('魔术师登录');button('生成档案文本');expect(doc.querySelector('.preview')!.textContent).toContain('姓名: <观察者>');
  }finally{dom.window.close();}
});
