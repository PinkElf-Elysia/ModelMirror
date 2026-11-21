import {readFileSync} from 'node:fs';
import {digest,entries,works,stages,FUSION,COMMON,fixedRows,source,html,fixed} from './worldbook-sources.mjs';
export {entries,works,stages,source,fixed};
const read=p=>readFileSync(new URL(p,import.meta.url),'utf8');
export const versionHash=()=>digest([read('./resources/system.txt'),read('./resources/original.html'),read('./worldbook-sources.mjs'),read('./worldbook.mjs')].map(s=>digest(s.replaceAll('\r\n','\n'))).join('|'));
const allWorks=[...works,FUSION];
const aliases={'天龙八部':'天龙','射雕英雄传':'射雕','神雕侠侣':'神雕','倚天屠龙记':'倚天','笑傲江湖':'笑傲','金庸群侠传':'融合宇宙'};
const lookup=t=>allWorks.find(w=>t===w||t===aliases[w]);
function pick(text){
 if(!text)return null;
 const row=text.match(/^【世界归属】[ \t]*\r?\n([^\r\n]+)/m)?.[1]??text.match(/^(单一作品线\s*·[^\r\n]+|融合宇宙线\s*·[^\r\n]+)/m)?.[1]??text.trim();
 const single=row.match(/^单一作品线\s*·\s*([^→\r\n]+?)(?:\s*→\s*(.+))?$/);
 if(single){const work=lookup(single[1].trim());return work?{work,stage:stages[work]?.includes(single[2])?single[2]:null}:null;}
 const fused=row.match(/^融合宇宙线\s*·\s*金庸群侠传\s*·\s*(.+)$/);
 if(fused)return {work:FUSION,region:fused[1].trim()};
 const named=row.match(/^(?:作品[：:]\s*)?《([^》]+)》(?:\s*(?:开局|开场|[，。]|$))/)||row.match(/^([\p{Script=Han}]+?)(?:开局|开场|$)/u);
 const work=named&&lookup(named[1]);return work?{work,stage:stages[work]?.find(s=>row.includes(s))??null}:null;
}
function visible(s){
 return s.replace(/<(details|think|checking)\b[^>]*>[\s\S]*?(?:<\/\1>|$)/gi,'').replace(/[\x60]{3}[\s\S]*?(?:[\x60]{3}|$)/g,'');
}
const memberships=new Map();
for(const e of entries){if(!memberships.has(e.name))memberships.set(e.name,new Set());memberships.get(e.name).add(e.work);}
const categories=['作品','人物','门派与势力','地点','武学','剧情阶段','剧情篇章','剧情线索','江湖设定'];
const label=e=>memberships.get(e.name).size>1?e.name+'（'+e.work+'）':e.name;
function available(e,anchor,complete){
 if(e.work!==anchor.work&&e.work!==COMMON)return false;
 if(e.region&&!complete&&e.region!==anchor.region)return false;
 if(!complete&&e.stage&&e.stage!==anchor.stage)return false;
 if(e.minStage!==undefined&&!complete&&Math.max(0,stages[anchor.work]?.indexOf(anchor.stage)??-1)<e.minStage)return false;
 return true;
}
export function library(anchor,complete=false){
 const eligible=entries.filter(e=>available(e,anchor,complete));
 const parts=['【分类触发库】在当前作品和入场阶段内按名称检索。人物、门派、地点与武学只提供对应资料；查阅具体篇章请用篇章名。推进本作阶段可写“推进至”后接阶段原名。'];
 for(const c of categories){
   const names=[...new Set(eligible.filter(e=>e.category===c).map(label))];
   if(names.length)parts.push(c+'　'+names.join('、'));
 }
 return parts.join('\n');
}
const sourceLines=source.split('\n');
export const worldbook={
 version:1,sourceSha256:digest(source),formSha256:digest(html),coordinate:'行从一计，UTF-16 列从零计。parts 是作者原文切片。',
 placement:{catalogBefore:773,formSingleAfter:845,formFusionAfter:1041},
 selectors:{single:'开局人物帖的世界归属决定作品。只接受本作品的阶段。',fusion:'融合宇宙分别检索本区域和通用设定。',work:'选择作品后装入。',stage:'人物帖选中的阶段，或玩家在本作明确推进到的阶段。',chapter:'本作品内按篇章名或原节名检索。',mention:'在作品与阶段内，根据角色档案、当前输入和最近可见叙事命中名称或别名。'},
 aliases,stages,entries,
 manuscript:sourceLines.slice(772).map((text,i)=>({line:i+773,text})),
 excluded:[{from:1,to:24,reason:'原文件中的模拟器身份覆写前言；新存档从作者的江湖角色定义起。',sha256:digest(sourceLines.slice(0,24).join('\n'))}]
};
export function assembleWorldbook({opening='',history=[],progress=history.filter(m=>m.role==='user').map(m=>m.content),input=''}={}){
 const first=history.find(m=>m.role==='user')?.content||input;
 const anchor=pick(opening)||pick(first)||progress.map(pick).find(Boolean)||pick(input)||{work:null};
 const previous=history.filter(m=>m.role==='assistant').at(-1)?.content||'';
 if(anchor.work && stages[anchor.work]){
   for(const t of [...progress,input]){
     for(const m of t.matchAll(/(?:^|[\n。！；])\s*(?:推进至|切换阶段至)\s*[「《]?([^」》\n。！；]+)[」》]?/g)){
       if(stages[anchor.work].includes(m[1].trim()))anchor.stage=m[1].trim();
     }
   }
 }
 const mentions=[opening,first,input,visible(previous)].map(t=>t.replace(/^【世界归属】[ \t]*\r?\n[^\n]+/gm,'')).join('\n');
 const hits=entries.filter(e=>{
   if(available(e,anchor,false)){
     if(e.rule==='work'||e.rule==='stage')return true;
     if(e.category==='地点'&&e.region&&e.name===e.region&&e.region===anchor.region)return true;
     return e.keys.some(k=>mentions.includes(k));
   }
   // A fusion player can identify a book's named dossier. Future events and
   // graded character examples still belong exclusively to the original work.
   if(anchor.work!==FUSION||!works.includes(e.work)||e.minStage!==undefined||e.rank||e.rule)return false;
   if(!['人物','门派与势力','地点','武学'].includes(e.category))return false;
   return e.keys.some(k=>input.includes(k+'（'+e.work+'）')||input.includes('《'+e.work+'》的'+k));
 });
 const slots=new Map(),supplement=new Map(),add=(line,part)=>{if(!slots.has(line))slots.set(line,[]);slots.get(line).push(part);};
 for(const [line,text] of fixedRows)add(line,{column:0,text});
 for(const e of hits){
   if(e.parts[0].source==='resources/original.html'){
     const text=e.category+'　'+[...new Set(e.parts.map(p=>p.text))].join('　');
     const anchorLine=anchor.work===FUSION?1041:845;
     if(!supplement.has(anchorLine))supplement.set(anchorLine,new Set());supplement.get(anchorLine).add(text);
   }else for(const p of e.parts)add(p.line,p);
 }
 const extra=anchor.work===FUSION?[...new Set(hits.filter(e=>e.work!==FUSION&&e.work!==COMMON).map(e=>e.category+'　'+e.name+'（'+e.work+'）'))]:[];
 const index=anchor.work?[library(anchor),...extra].join('\n'):'【分类触发库】请先在人物帖中选择作品或融合宇宙。';
 const out=[];
 for(let n=25;n<=1041;n++){
   if(n===773)out.push(index);
   const parts=slots.get(n);
   if(parts){
     parts.sort((a,b)=>a.column-b.column||b.text.length-a.text.length);
     const first=parts[0];let text=first.text,end=first.column+first.text.length;
     for(const p of parts.slice(1)){
       if(p.column+p.text.length<=end)continue;
       if(p.column>end)text+='、';
       text+=p.text.slice(Math.max(0,end-p.column));end=p.column+p.text.length;
     }
     if(hits.some(e=>e.rank&&e.parts[0].line===n))text='· 代表人物：'+text;
     out.push(text);
   }
   if(supplement.has(n))out.push(...supplement.get(n));
 }
 return {...anchor,system:out.join('\n'),catalog:index,hits:hits.map(({id,work,category,name,parts})=>({id,work,category,name,source:parts[0].source,line:parts[0].line}))};
}
