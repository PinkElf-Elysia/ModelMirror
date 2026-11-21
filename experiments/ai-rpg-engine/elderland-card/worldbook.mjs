import {entries,regions,locations,lines,positions,omitted,sha,source,sourceHash} from './worldbook-sources.mjs';
import {readFileSync} from 'node:fs';
export {entries as worldbook,source,sourceHash,regions,omitted};
const ids=Object.keys(regions);
const regionOf=text=>ids.filter(r=>regions[r].some(k=>k==='中央'?/(?:^中央$|中央[·地区诸王])/.test(text):text.toLowerCase().includes(k.toLowerCase())));
const lexicon=new Map();
for(const e of entries)for(const k of e.keys){
  const owners=lexicon.get(k)||new Set();owners.add(e.region);lexicon.set(k,owners);
}
const sameNames=new Map();
for(const e of entries){
  for(const name of e.names||[e.name]){const owners=sameNames.get(name)||new Set();owners.add(e.region);sameNames.set(name,owners);}
}
const nameOf=r=>regions[r]?.[0]||'共通';
const display=e=>(e.names||[e.name]).map(name=>sameNames.get(name).size>1?name+'（'+nameOf(e.region)+'）':name);
export function catalogOf(scopes){
  const rows=entries.filter(e=>e.category==='区域'||e.region==='common'||scopes.includes(e.region));
  const messages=['【世界书名称库】按所在地查阅本地条目；询问异地时可用区域名，再按返回的名称细查。询问目的地不会改变所在地。'];
  for(const group of ['区域','地点','势力','人物','任务']){
    const names=new Set(rows.filter(e=>e.category===group).flatMap(display));
    if(names.size)messages.push(group+'：'+[...names].join('、'));
  }
  return messages.join('\n');
}
const extras=new Map();
for(const e of entries)if(e.origin){const list=extras.get(e.insert)||[];list.push(e);extras.set(e.insert,list);}
function render(active,library='',state=''){
  const out=[];
  for(let n=1;n<=lines.length;n++){
    if(omitted.some(([a,b])=>n>=a&&n<=b))continue;
    if(n===812&&library)out.push(library,state);
    for(const e of extras.get(n)||[])if(active.has(e))out.push('【'+e.name+'】'+e.body);
    const owner=positions.get(n);
    if(owner&&!active.has(owner))continue;
    if(lines[n-1].trim()||out.at(-1)!=='')out.push(lines[n-1]);
  }
  return out.join('\n');
}
export const fixed=render(new Set());
function resolvePlace(text,prior={region:null,city:''}){
  const rs=regionOf(text);
  const candidates=locations.filter(c=>(!rs.length||rs.includes(c.region))&&text.includes(c.name));
  candidates.sort((a,b)=>b.name.length-a.name.length);
  const found=candidates.filter(c=>c.name===candidates[0]?.name);
  const c=found.length===1?found[0]:found.find(c=>c.region===prior.region);
  if(c)return {region:c.region,city:c.name};
  if(rs.length===1)return {region:rs[0],city:rs[0]===prior.region?prior.city:''};
  const countries=[...new Set(locations.filter(c=>text.includes(c.country)).map(c=>c.region))];
  return countries.length===1?{region:countries[0],city:countries[0]===prior.region?prior.city:''}:prior;
}
function updatePlace(prior,message,role){
  // Only completed arrivals and the host's current-location field change the scene.
  const field=message.match(/^(?:【当前位置】[ \t]*\r?\n|(?:当前位置|当前所在地|所在地)[：:][ \t]*)([^\r\n]+)/m);
  if(field)return resolvePlace(field[1],prior);
  if(role!=='user')return prior;
  for(const clause of message.split(/[。！\n]/)){
    const move=clause.trim().match(/^(?:我|我们|角色)?(?:已经|已|刚刚|刚)?(?:抵达|到达|进入|来到了|抵达了|到达了|身在|现在在)([^，,。？！?]{1,60})[。！]?$/);
    if(move){
      const destination=move[1].trim().replace(/^了/,'');
      const parsed=resolvePlace(destination,prior);
      if([...Object.values(regions).flat(),...locations.map(c=>c.name)].some(k=>destination.endsWith(k))&&!/如果|假如|想|是否|询问|问|打听|听说|查阅|了解|传闻|需要|计划/.test(destination))prior=parsed;
    }
  }
  return prior;
}
function startingPlace(opening){
  const birthplace=opening.match(/【出生地】[ \t]*\r?\n([^\r\n]+)/);
  if(birthplace)return updatePlace(resolvePlace(birthplace[1]),opening,'user');
  const prior=updatePlace({region:null,city:''},opening,'user');
  if(prior.region)return prior;
  const at=opening.match(/^(?:开局[：:]?|我|我们|角色)?(?:从|在|身处)([^。？，,！\n]{1,65})/);
  return at?resolvePlace(at[1]):prior;
}
const taskQuery=/(?:问|接|找|打听|领取|查看|有|领|看|发布|浏览|寻找|贴出|打听有无|询问)(?:.{0,12})(?:任务|委托|悬赏)|(?:任务板|委托栏|公告板|任务有哪些|任务清单)/;
function caseMatch(text,k){return k.length>1&&text.toLowerCase().includes(k.toLowerCase())||text.trim()===k;}
function narrativeInput(input){
  if(!input.includes('【出生地】'))return input;
  // A birthplace is a starting coordinate. It is not a query for every similarly named subregion.
  return input.match(/【背景故事】\s*\n([\s\S]*?)(?:\n={4,}|$)/)?.[1]||'';
}
function inquiries(text,local){
  const found=new Set();
  for(const part of text.split(/[，,；;。！\n]/)){
    const query=part.match(/(?:打听|询问|问问|请问|查阅|了解|请教|介绍|说说|告诉我|听闻|听说)(.*)/);
    if(/(?:不|别|并未|没有)(?:再|想|要)?(?:打听|问|询问|查阅|了解|听说)/.test(part))continue;
    const bare=Object.values(regions).flat().some(k=>k.toLowerCase()===part.trim().toLowerCase());
    if(!query&&!bare&&!/[？?]|如何|怎么样|在哪里|是什么|有(?:哪些|什么)/.test(part))continue;
    const scopeText=part;
    const explicit=regionOf(scopeText).filter(r=>r!==local);
    for(const r of explicit)found.add(r);
    if(!explicit.length)for(const e of entries){
      if(e.region===local||e.region==='common'||e.category==='区域')continue;
      if(e.keys.some(k=>k.length>=2&&lexicon.get(k)?.size===1&&caseMatch(scopeText,k)))found.add(e.region);
    }
  }
  return found;
}
export function assembleWorldbook({opening='',input='',history=[]}={}){
  const first=opening||history.find(m=>m.role==='user')?.content||input;
  let location=startingPlace(first);
  for(const m of history)if(m.content!==first||m.role!=='user')location=updatePlace(location,m.content,m.role);
  location=updatePlace(location,input,'user');
  const text=narrativeInput(input);
  const consulted=inquiries(text,location.region);
  if(!consulted.size&&/那边|那里|当地的|刚才打听的/.test(text)){
    const last=history.filter(m=>m.role==='user').at(-1)?.content||'';
    for(const r of inquiries(narrativeInput(last),location.region))consulted.add(r);
  }
  const scopes=[...new Set([location.region,...consulted].filter(Boolean))];
  const active=new Set();
  for(const e of entries){
    if(e.region!=='common'&&!scopes.includes(e.region))continue;
    const here=e.region===location.region,city=here?location.city:'';
    const hit=e.keys.some(k=>caseMatch(text,k));
    const parentOK=!e.parent||e.parent===city||caseMatch(text,e.parent);
    let show=e.mode==='region'||e.name===city||hit;
    if(e.mode==='board'&&taskQuery.test(text)&&parentOK)show=here?!consulted.size:consulted.has(e.region);
    if(e.mode==='secret')show=parentOK&&/秘密|秘闻|暗线|传言/.test(text);
    if(e.origin&&e.parent&&e.category!=='地点'&&city&&e.parent!==city&&!caseMatch(text,e.parent)&&sameNames.get(e.name)?.size>1&&!consulted.has(e.region))show=false;
    if(show)active.add(e);
  }
  const catalog=catalogOf(scopes);
  const state='【本轮所在地】'+(location.region?[nameOf(location.region),location.city].filter(Boolean).join(' ＞ '):'待档案或剧情确认')+
    (consulted.size?'\n【本轮异地查阅】'+[...consulted].map(nameOf).join('、'):'');
  return {location,consulted:[...consulted],catalog,hits:[...active].map(e=>e.id),system:render(active,catalog,state)};
}
const binding=sha([sourceHash,...['worldbook-sources.mjs','worldbook.mjs'].map(p=>readFileSync(new URL(p,import.meta.url),'utf8')),sha(JSON.stringify(entries))].join('\0'));
export const versionHash=()=>binding;
