import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8');
const sha = text => createHash('sha256').update(text).digest('hex');
export const source = read('author-system.txt');
export const manifest = JSON.parse(read('worldbook-slots.json'));
const lines = source.match(/[^\n]*\n|[^\n]+$/g);
const slice = (start, end) => lines.slice(start - 1, end).join('');
export const slots = manifest.slots.map(s => ({...s, body:slice(s.startLine,s.endLine)}));
if (sha(source) !== manifest.sourceSha256 || slots.map(s=>s.body).join('') !== source ||
    slots.some(s => sha(s.body) !== s.sha256)) throw Error('CULTIVATION_SOURCE_DRIFT');

export const entries = slots.filter(s=>s.kind==='entry');
export const fixedPrompt = slots.filter(s=>s.kind==='fixed').map(s=>s.body).join('');
const realmsByName = new Map();
const catalogs = new Map(['凡界','仙界','神界'].map(realm => [realm,new Map()]));
function register(realm,name,type,roster='') {
  if (!realmsByName.has(name)) realmsByName.set(name,new Set());
  realmsByName.get(name).add(realm);
  const names=catalogs.get(realm);
  if (!names.has(name)) names.set(name,{type,roster});
  const item=names.get(name);
  if (item.type!==type && [item.type,type].every(t=>['势力','地点','势力兼地点'].includes(t))) item.type='势力兼地点';
}
for (const e of entries) register(e.realm,e.name,e.indexType);
for (const e of entries) {
  for (const row of e.rows??[]) {
    for (const name of row.names) register(e.realm,name,row.type,row.type==='人物'?e.name:'');
  }
}
const display=(name,realm)=>realmsByName.get(name).size===1?name:name+'（'+realm+'）';
export const libraryNames=[];
export const libraryGuide='按当前界域查找剧情涉及的人、地、势力，叙事时自然使用规范名。人物按原文收录处列出。具体设定以本轮载入的词条为准，相遇和见闻以角色经历为准。查阅异界资料时沿用当前地点。';
const blocks=[libraryGuide];
for (const [realm,names] of catalogs) {
  const lines=['【'+realm+'】'];
  for (const type of ['地点','势力','势力兼地点','人物','事件','规则']) {
    const rosters=new Map();
    for (const [name,item] of names) {
      if (item.type!==type) continue;
      if (!rosters.has(item.roster)) rosters.set(item.roster,[]);
      const label=display(name,realm);
      rosters.get(item.roster).push(label);
      libraryNames.push(label);
    }
    if (!rosters.size) continue;
    if (type==='人物' && rosters.size>1) {
      lines.push('人物');
      for (const [roster,members] of rosters) lines.push('  '+(roster?display(roster,realm):'人物专录')+'｜'+members.join('、'));
    } else {
      lines.push(type+'｜'+[...rosters.values()].flat().join('、'));
    }
  }
  blocks.push(lines.join('\n'));
}
export const library=blocks.join('\n\n');
export const versionHash = () => sha(['author-system.txt','worldbook-slots.json','worldbook.mjs'].map(f=>sha(read(f))).join('|'));

const hidden = s => s.replace(/<(think|thinking)\b[^>]*>[\s\S]*?(?:<\/\1>|$)|<!--[\s\S]*?(?:-->|$)/gi,'');
const visible = s => hidden(s).replace(/<details\b[^>]*>[\s\S]*?(?:<\/details>|$)/gi,'')
  .split(/【(?:可前往|区域情报|修真界风云|关系网络|下一步行动|已召回记忆)】/)[0]
  .replace(/^(?:出身界|原籍|出生界域)[：:].*$/gm,'');
function scene(text,assistant) {
  const raw=hidden(text);
  const fields=assistant ? (raw.match(/【(?:当前位置|世界信息)】[^【<]*/g)??[]).join('\n') : visible(text);
  const position=fields.match(/【当前位置】\s*([^\r\n【<]+)/)?.[1]??'';
  const realm=(fields.match(/(?:当前所在界|当前界域|所在界域)[：:]\s*(凡界|仙界|神界)/)?.[1] ??
    position.match(/^(凡界|仙界|神界)/)?.[1] ??
    (!assistant && fields.match(/(?:^|[，。\s])(?:在)?(凡界|仙界|神界)开局/)?.[1])) ||
    (!assistant && fields.match(/(?:我已(?:经)?(?:飞升|抵达|来到|进入)|切换|我现在身处|我现在位于)(?:至|到|了)?(凡界|仙界|神界)/)?.[1]);
  const region=fields.match(/所在区域[：:]\s*([^\n\r【<]+)/)?.[1];
  const place=fields.match(/具体地点[：:]\s*([^\n\r【<]+)/)?.[1] ??
    fields.match(/(?:我已(?:经)?(?:抵达|来到)|开局[，、 ]*在|起点[：:]|出生在)([\p{Script=Han}]{2,12}?)(?=[，。\s]|$)/u)?.[1];
  return {realm,location:[region,place,position].filter(Boolean).join(' ')};
}
const divineQuery = /(?:查阅|询问|打听|了解|请教|调查|谈论|讲述|介绍|神界相关)[^\n。！？]{0,40}神界|(?:在神界|神界(?:相关|传说|典籍|诸神|之事|的|[，：·]|之中))/;
function termsFor(opening,history,input) {
  let realm='凡界', location='', narration='';
  for(const {role,content} of [{role:'user',content:opening},...history,{role:'user',content:input}]) {
    const assistant=role==='assistant';
    const next=scene(content,assistant);
    if(next.realm && next.realm!==realm){realm=next.realm;location='';narration='';}
    if(next.location)location=next.location;
    if(assistant)narration=visible(content).split(/【(?:世界信息|当前位置)】/)[0];
  }
  const discussion=[narration,visible(input)].join('\n');
  return {realm,text:[location,discussion].join('\n'), divine:realm==='神界'||divineQuery.test(discussion)};
}
export function assembleWorldbook({opening='',history=[],input=''}) {
  const {realm,text,divine}=termsFor(opening,history,input);
  const matched=new Map();
  const candidates=[];
  const remember=(term,e,row=null)=>{
    if(!term)return;
    for(let at=text.indexOf(term);at>=0;at=text.indexOf(term,at+term.length)) {
      const end=at+term.length;
      const qualifier=text.slice(end,end+4).match(/^[（(](凡界|仙界|神界)[）)]/);
      if(!qualifier||qualifier[1]===e.realm)candidates.push({term,e,row,at,end});
    }
  };
  for(const e of entries) {
    if(e.originalTrigger ? !divine : e.realm!==realm)continue;
    for(const term of new Set([e.name,...e.aliases,e.originalTrigger?.term]))remember(term,e);
    for(const row of e.rows??[])for(const term of row.names)remember(term,e,row);
  }
  // Nested names resolve to the longest occurrence. Identical spans may belong to two original slots.
  candidates.sort((a,b)=>a.at-b.at||b.end-a.end);
  let lastStart=-1,furthestEnd=-1;
  const winning=candidates.filter(c=>{
    if(c.at===lastStart&&c.end===furthestEnd)return true;
    if(c.end<=furthestEnd)return false;
    lastStart=c.at;furthestEnd=c.end;return true;
  });
  const parents=new Set(winning.filter(c=>!c.row).map(c=>c.e.name));
  const locatedTerms=new Set(winning.filter(c=>c.row&&parents.has(c.e.name)).map(c=>c.term));
  for(const c of winning){
    if(c.row&&!parents.has(c.e.name)&&locatedTerms.has(c.term))continue;
    if(!matched.has(c.e.id))matched.set(c.e.id,{full:false,rows:new Set(),terms:new Set()});
    const m=matched.get(c.e.id);m.terms.add(c.term);
    if(c.row){m.rows.add(c.e.startLine);m.rows.add(c.row.header);m.rows.add(c.row.line);}else m.full=true;
  }
  const headings=new Set(entries.filter(e=>matched.has(e.id)).map(e=>e.section));
  let system='';const hits=[];
  for(const s of slots){
    if(s.startLine===manifest.indexBeforeLine)system+='【世界书中文规范名】\n'+library+'\n\n';
    if(s.kind==='fixed'||(s.kind==='section'&&headings.has(s.id))){system+=s.body;continue;}
    const m=matched.get(s.id);if(!m)continue;
    const selectedLines=m.full?null:[...m.rows].sort((a,b)=>a-b);
    system+=m.full?s.body:selectedLines.map(n=>lines[n-1]).join('');
    hits.push({id:s.id,name:s.name,realm:s.realm,startLine:s.startLine,endLine:s.endLine,terms:[...m.terms],selectedLines});
  }
  return {system,realm,hits};
}
