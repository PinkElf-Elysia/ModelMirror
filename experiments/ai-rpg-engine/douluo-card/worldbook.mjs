import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {aliases,extraAbilities,first,groups,omittedRanges,plotNames,second} from './worldbook-index.mjs';

const read = file => readFileSync(new URL(file,import.meta.url),'utf8');
const hash = text => createHash('sha256').update(text).digest('hex');
export const source = JSON.parse(read('./resources/prompts.json')).system;
if (hash(source)!==JSON.parse(read('./resources/MANIFEST.json')).system.sha256) throw Error('DOULUO_SOURCE_CHANGED');
const lines = source.split('\n');
const entries = [], slots = new Map(), headers = new Map();
const clean = words => [...new Set(words.filter(Boolean))];
function entry(line,group,names,keys=[],work=first,extra={}) {
  const row={id:work+'-'+line,group,names:clean(names),keys:clean([...names,...keys]),work,spans:[],...extra};
  entries.push(row);
  return row;
}
function assign(row,start,end=start,from=0,to=lines[end-1].length) {
  for(let n=start;n<=end;n++){
    const a=n===start?from:0,b=n===end?to:lines[n-1].length;
    const span={line:n,from:a,to:b,text:lines[n-1].slice(a,b)};
    row.spans.push(span);
    slots.set(n,{row,span});
  }
}
function inline(line,phrase,names,keys) {
  const i=lines[line-1].indexOf(phrase);
  if(i<0)throw Error('Missing source fragment at '+line);
  assign(entry(line,'修炼设定',names,keys,second),line,line,i,i+phrase.length);
}
inline(247,'95-98级超级斗罗，99级极限斗罗',['超级斗罗','极限斗罗'],['封号斗罗','95级','99级']);
inline(443,'及本书逐步建立的魂灵体系',['魂灵'],['魂灵体系','魂环来源']);
inline(507,lines[506].split('。')[1]+'。',['超级斗罗','极限斗罗'],['封号斗罗','九十五级','九十九级']);
const blocks=[];
for(let n=520;n<=1044;n++){
  const match=lines[n-1].match(/^(?:【主要角色设定】|### )\d+\. (.+)$/);
  if(!match)continue;
  let end=n;
  while(end<1044 && lines[end].trim() && !/^(?:#|---)/.test(lines[end]))end++;
  blocks.push({n,end,name:match[1].split('／')[0]});
}
function category(n) {
  if(n<873)return '人物';
  if([876,882,906,918,930,936,1010,1015,1018,1032].includes(n))return '学院与宗门';
  if([900,996,1001,1006,1027,1036].includes(n))return '势力';
  if(n===990)return '剧情';
  return '地点';
}
function abilities(n) {
  const [field,value]=lines[n-1].split('：');
  if(!value)return [];
  const names=[];
  if(/^(武魂|魂技示例|技能示例|魂技|自创魂技|领域|神位|唐门绝学)$/.test(field)){
    const primary=value.split(/[，；。]/)[0];
    if(!primary.includes('相关传承'))names.push(...primary.split('、').map(s=>s.replace(/（.*?）/g,'')));
    if(n===708)names.push('七窍玲珑心');
  }
  if(field==='魂骨'&&![527,617,629,674].includes(n))names.push(...value.split(/[，。]/)[0].split('、'));
  return clean([...names,...(extraAbilities[n]||[])]);
}
for(const {n,end,name} of blocks) {
  if(n===1040)continue; // General rules stay fixed at their original position.
  const owner=entry(n,category(n),[name],aliases[name]);
  assign(owner,n);
  for(let pos=n+1;pos<=end;pos++){
    const names=n<873?abilities(pos):[];
    if(names.length){
      const child=entry(pos,'武魂与魂技',names,owner.keys,first,{parent:owner.id});
      assign(child,pos);
    } else assign(owner,pos);
  }
}
for(let n=1068;n<=1093;n++){
  const [date,body]=lines[n-1].split('：'), years=date.match(/\d+/g).map(Number);
  const event=entry(n,'剧情',[plotNames[n-1068]],body.split(/[，、。]/).filter(s=>s.length>=4),first,{years:[years[0],years.at(-1)]});
  assign(event,n);
}
// Retain original section labels only when they introduce selected material.
for(const [line,start,end] of [[740,742,868],[873,876,1038],[874,876,946],[948,950,984],[988,990,1021],[1025,1027,1044],[1066,1068,1093]])headers.set(line,[start,end]);

export const worldbook=entries;
const version=hash([source,read('./worldbook-index.mjs'),read('./worldbook.mjs')].join('\0'));
export const versionHash=()=>version;
const guide='【世界书查阅】按当前作品和场景使用下列中文规范名。需要具体资料时，在剧情或行动中自然写出对应名称。剧情按斗罗历年份查阅。';
function catalog(rows) {
  return [guide,...groups.flatMap(group=>{
    const names=clean(rows.filter(r=>r.group===group).flatMap(r=>r.names));
    return names.length?[group+'：'+names.join('、')]:[];
  })].join('\n');
}
function workOf(opening) {
  const value=opening.match(/^(?:作品(?:名)?|所属作品|作品线)[：:]\s*([^\r\n]+)/m)?.[1]?.trim().replace(/^《([^》]+)》$/,'$1');
  if(!value||['斗罗大陆','斗罗大陆第一部','斗罗大陆（第一部）','第一部'].includes(value))return first;
  if(['绝世唐门','斗罗大陆Ⅱ绝世唐门','斗罗大陆第二部','斗罗大陆第二部绝世唐门','第二部'].includes(value))return second;
  return '其他作品';
}
function yearIn(text) {
  return Number(text.match(/^(?:当前时间|当前年份|斗罗历年份)[：:]\s*(?:斗罗历)?(\d{4})年?/m)?.[1])||null;
}
function render(active,library) {
  const result=[];
  const headings=new Set([...active].filter(r=>r.headingOnly).map(r=>r.spans[0].line));
  for(let n=1;n<=lines.length;n++){
    if(omittedRanges.some(([a,b])=>n>=a&&n<=b))continue;
    let line=lines[n-1];
    if(n===1430||n===1450)line=line.replace('斗罗龄（HTML注释中写出现实龄）','年龄');
    if(n===1229)line=line.replace('六个不同角度','不同角度');
    if(n===1239||n===1240)line=line.replace('④','③').replace('⑤','④');
    if(n===214&&library)result.push(library,'');
    if(headers.has(n)){
      const [start,end]=headers.get(n);
      if(![...active].some(r=>r.spans.some(s=>s.line>=start&&s.line<=end)) && n!==1025)continue;
    }
    const selected=slots.get(n);
    if(selected&&!active.has(selected.row)&&!headings.has(n)){
      const {span}=selected;
      line=line.slice(0,span.from)+line.slice(span.to);
      if(!line)continue;
    }
    // Do not leave hundreds of vacated separators from the archive on the wire.
    if(!line.trim()&&result.at(-1)==='')continue;
    result.push(line);
  }
  return result.join('\n');
}
export const fixed=render(new Set(),'');
export function assembleWorldbook({opening='',history=[],input='',progress=[]}={}) {
  const archive=opening||history.find(m=>m.role==='user')?.content||input;
  const work=workOf(archive);
  const year=yearIn(input)||[...progress].reverse().map(yearIn).find(Boolean)||yearIn(archive);
  const scope=entries.filter(r=>r.work===work&&(!r.years||year!==null&&r.years[0]<=year));
  const library=catalog(scope);
  const recent=history.slice(-2).map(m=>m.content);
  // A mention of a title in dialogue cannot select a work or match the sect 唐门.
  const context=[archive,...recent,input].join('\n').replace(/(?:斗罗大陆[Ⅱ二2]?[：:· ]*)?绝世唐门/g,'');
  const active=new Set(scope.filter(r=>r.years
    ? (year<=r.years[1]||r.keys.some(k=>context.includes(k)))
    : r.keys.some(k=>context.includes(k))));
  // A skill-only hit retains its original owner heading, without unrelated fields.
  const headingIds=new Set([...active].map(r=>r.parent).filter(Boolean));
  const selected=new Set(active);
  for(const r of entries)if(headingIds.has(r.id)&&!active.has(r)){
    // Heading coverage is rendered separately so the rest of the profile stays dormant.
    selected.add({...r,spans:[r.spans[0]],headingOnly:true});
  }
  return {work,year,catalog:library,hits:entries.filter(r=>active.has(r)).map(r=>r.id),system:render(selected,library)};
}
