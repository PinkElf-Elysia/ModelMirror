// @vitest-environment node
import {afterEach, beforeEach, expect, it} from 'vitest';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';

const html=readFileSync(new URL('../../../experiments/ai-rpg-engine/harry-potter-card/public/index.html',import.meta.url),'utf8');
let page:JSDOM;
let doc:Document;
beforeEach(async()=>{
  page=new JSDOM(html,{runScripts:'dangerously',url:'http://localhost/rpg-app/harry-potter/',beforeParse(window){
    Object.defineProperty(window,'matchMedia',{value:()=>({matches:true})});
  }});
  await new Promise<void>(resolve=>page.window.addEventListener('load',()=>resolve(),{once:true}));
  doc=page.window.document;
});
afterEach(()=>page.window.close());
function click(selector:string){(doc.querySelector(selector) as HTMLElement).click();}
function set(id:string,value:string){const el=doc.getElementById(id) as HTMLInputElement;el.value=value;el.dispatchEvent(new page.window.Event('change',{bubbles:true}));}
function preview(){click('.tab-btn[data-tab="preview"]');return doc.getElementById('preview-box')!.textContent!;}

it('reduced-motion users reach the six-tab card without a blocking overlay',()=>{
  expect(doc.getElementById('opening')!.style.display).toBe('none');
  expect(doc.getElementById('main')!.classList.contains('active')).toBe(true);
  expect([...doc.querySelectorAll('.tab-btn')].map(el=>el.textContent)).toEqual(['世界','作品','角色','能力','命运','此生']);
  expect(doc.getElementById('open-chat')!.hidden).toBe(true,'standalone card cannot send a message to an unrelated window');
});

it('custom magic and fate names remain literal text in both lists and preview',()=>{
  const value='<img src=x onerror="alert(1)"> & "魔法"';
  set('custom-ab-name',value);set('custom-ab-desc','<b>原样说明</b>');click('button[onclick="addCustomAbility()"]');
  set('custom-tl-name',value);set('custom-tl-desc','<svg onload="alert(2)">');click('button[onclick="addCustomTalent()"]');
  for(const id of ['custom-ab-list','custom-tl-list']){
    const list=doc.getElementById(id)!;expect(list.querySelector('img,svg')).toBeNull();expect(list.textContent).toContain(value);
  }
  expect(preview()).toContain(value);
});

it('identity labels, course levels and spell toggles agree with the generated opening',async()=>{
  set('identity','待入学新生');
  const house=doc.getElementById('house')!;
  expect(doc.getElementById(house.getAttribute('aria-labelledby')!)!.textContent).toBe('期望学院');
  const level=doc.querySelector('select[aria-label="魔咒学掌握程度"]') as HTMLSelectElement;
  level.value=[...level.options].find(o=>o.text==='初学')!.value;
  level.dispatchEvent(new page.window.Event('change',{bubbles:true}));
  const spell=[...doc.querySelectorAll('.ability-item')].find(el=>el.textContent!.startsWith('荧光闪烁')) as HTMLElement;
  spell.click();expect(spell.querySelector('input')!.checked).toBe(true);
  expect(preview()).toContain('魔咒学：初学');expect(preview()).toContain('荧光闪烁');
  spell.click();expect(spell.querySelector('input')!.checked).toBe(false);expect(preview()).not.toContain('荧光闪烁');
});
