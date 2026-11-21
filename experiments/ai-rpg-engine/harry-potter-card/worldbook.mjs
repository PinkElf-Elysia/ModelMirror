import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';

const read=name=>readFileSync(new URL(name,import.meta.url),'utf8');
export const source=JSON.parse(read('resources/prompts.json')).system;
export const config=JSON.parse(read('resources/worldbook.json'));
const sha=s=>createHash('sha256').update(s).digest('hex');
if(sha(source)!==config.sourceSha256)throw Error('HARRY_SOURCE_DRIFT');
const lines=source.match(/[^\n]*\n|[^\n]+$/g);
export const slots=config.slots.map(s=>({...s,body:lines.slice(s.line-1,s.end).join('')}));
if(slots.map(s=>s.body).join('')!==source)throw Error('HARRY_SOURCE_LAYOUT');
export const fixed=slots.filter(s=>s.kind==='固定').map(s=>s.body).join('');
const works=slots.filter(s=>s.kind==='作品');
const extra='哈利·波特：魔法觉醒';
const years=[1991,1992,1993,1994,1995,1996,1997,1890,1926,1927,1932,2017];
export const titles=new Map(works.map((s,i)=>[s.name,years[i]]).concat([[extra,2008]]));
const main=name=>titles.get(name)>=1991&&titles.get(name)<=1997;
const groups=new Map();
function index(group,name){if(!groups.has(group))groups.set(group,new Set());groups.get(group).add(name);}
for(const [name,year] of [...titles].sort((a,b)=>a[1]-b[1])) index(year<1991?'前传作品':year<=1997?'正篇作品':'后传作品',name);
const primaryNames=new Set(slots.map(s=>s.name));
for(const e of slots){
  if(e.kind==='固定'||e.kind==='作品')continue;
  index(e.kind==='人物'?'正篇人物专录':e.kind,e.name);
  for(const r of e.rows)if(r.key&&!primaryNames.has(r.key))index(r.type??('场所·'+e.name),r.key);
}
export const guide='以建卡所选作品为时间锚点。按类别选用剧情涉及的规范名，人物身份和事件以本作原文为准。查阅其他作品时沿用当前时点。《哈利·波特：魔法觉醒》沿用建卡选项，仅有共通资料。';
export const library=guide+'\n'+[...groups].map(([g,n])=>g+'｜'+[...n].join('、')).join('\n');
export const indexHeading='【世界书分类索引】\n';
export const digest=()=>sha([read('worldbook.mjs'),read('resources/worldbook.json'),source].map(sha).join('|'));
function titleInField(text){
  const value=text.match(/(?:^|\n)(?:【作品】|(?:当前作品|所选作品|开局作品)[：:])\s*([^\r\n]+)/)?.[1]?.trim();
  if(value)return [...titles.keys()].find(name=>value===name||value.startsWith('《'+name+'》')||value.startsWith(name+'（')||value.startsWith(name+' ('))??'';
  return '';
}
function inferOpening(text){
  const exact=[...titles.keys()].filter(name=>text.trim()===name||text.includes('《'+name+'》'));
  return titleInField(text)||(exact.length===1?exact[0]:'');
}
const stripThought=s=>s.replace(/<(think|thinking)\b[^>]*>[\s\S]*?(?:<\/\1>|$)|<!--[\s\S]*?(?:-->|$)/gi,'');
const visible=s=>stripThought(s).replace(/<details\b[^>]*>[\s\S]*?(?:<\/details>|$)/gi,'').split(/【(?:可前往|下一步行动|已召回记忆|临近原著\/世界事件)】/)[0];
function namesText(s){
  let value=visible(s).replace(/^.*(?:【作品】|当前作品[：:]|所选作品[：:]|开局作品[：:]|魔法世界·角色创建).*$/gm,'');
  for(const name of titles.keys())value=value.replaceAll(name,'');
  return value;
}
function topic(text){
  if(!/(?:^|\n)【作品】/.test(text))return namesText(text);
  if(!text.includes('【入场时机】')&&!text.includes('—— 当前处境 ——'))return namesText(text);
  return [text.match(/【入场时机】([^\n]+)/)?.[1],text.match(/【原著扮演】([^\n]+)/)?.[1],
    text.match(/—— 当前处境 ——\r?\n([\s\S]*?)(?=\r?\n——|$)/)?.[1]].filter(Boolean).map(namesText).join('\n');
}
const placeKeys=new Set(slots.filter(s=>s.kind==='地点'||s.kind==='势力').flatMap(s=>[s.name,...s.aliases,...s.rows.filter(r=>r.key&&!r.type).flatMap(r=>[r.key,...(r.aliases??[])])]));
function arrival(text){
  const action=visible(text).trim().match(/^(?:我(?:已经|已)?|我们)?(?:走进|抵达|来到|返回|回到|到达|进入|离开[^，。\n]{1,24}[，,]走进)([^，。！？\n]+)/);
  if(!action)return '';
  return [...placeKeys].filter(k=>action[1].startsWith(k)).sort((a,b)=>b.length-a.length)[0]??'';
}
function context(opening,history,input){
  let work='',place='',narrative='';
  const messages=[{role:'user',content:opening},...history,{role:'user',content:input}];
  for(const m of messages){
    const user=m.role==='user', raw=stripThought(m.content);
    if(user){
      const picked=!work?inferOpening(raw):'';
      const changed=raw.trim().match(/^(?:切换作品|进入下一部作品)[：:]\s*《([^》]+)》[。！!]?$/)?.[1];
      if(changed&&titles.has(changed)){work=changed;place='';narrative='';}
      else if(picked)work=picked;
    }
    const address=(user?visible(raw):raw).match(/【当前位置】[ \t]*(?:\r?\n)?([^【<\r\n]+)/)?.[1];
    const profile=user&&raw.match(/—— 当前处境 ——\r?\n([^\n]+)/)?.[1];
    const moved=user&&arrival(raw);
    if(address||moved||profile){place=address||moved||profile;if(moved)narrative='';}
    if(!user)narrative=namesText(raw).split(/【世界信息】|【当前位置】/)[0];
  }
  return {work,text:[namesText(place),narrative,topic(input)].join('\n')};
}
function permitted(scope,work){
  if(scope==='通用')return true;
  const start=titles.get(work);
  if(work===extra)return false;
  if(scope==='首学年')return start===1991;
  if(scope==='正篇')return main(work);
  if(typeof scope!=='number'||!start)return false;
  // Shared dossiers use the opening year. The selected work itself retains its full school-year chronology.
  const lastYear=start===2017?2020:start;
  return lastYear>=scope;
}
export function assembleWorldbook({opening='',history=[],input=''}){
  const {work,text}=context(opening,history,input);
  const hits=[];let system='';
  for(const s of slots){
    if(s.line===config.indexBefore)system+=indexHeading+library+'\n\n';
    if(s.kind==='固定'){system+=s.body;continue;}
    if(s.kind==='作品'){
      if(work===s.name){system+=s.body;hits.push({id:s.id,name:s.name,type:s.kind,start:s.line,end:s.end});}
      continue;
    }
    const direct=[s.name,...s.aliases].some(term=>text.includes(term));
    const chosen=new Map();const fragments=[];
    for(const r of s.rows){
      if(!direct&&!(r.key&&[r.key,...(r.aliases??[])].some(key=>text.includes(key))))continue;
      if(s.name==='魂器' && !(titles.get(work)>=1996))continue;
      const raw=lines[r.line-1];let value=raw;
      if(!permitted(r.scope,work)){
        if(!r.commonPrefixBefore)continue;
        const at=raw.indexOf(r.commonPrefixBefore);if(at<0)throw Error('HARRY_PREFIX_DRIFT');
        value=raw.slice(0,at)+'\n';fragments.push({line:r.line,characters:at});
      }
      if(r.header)chosen.set(r.header,lines[r.header-1]);
      chosen.set(r.line,value);
    }
    if(!chosen.size)continue;
    system+=lines[s.line-1]+'\n'+[...chosen].sort(([a],[b])=>a-b).map(([,v])=>v).join('')+'\n';
    hits.push({id:s.id,name:s.name,type:s.kind,start:s.line,end:s.end,lines:[...chosen.keys()].sort((a,b)=>a-b),fragments});
  }
  return {system,work,hits};
}
