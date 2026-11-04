import {validateDocument,canonical,sha256,fail,isId,keys} from './contracts.mjs';
import {evaluateRegexBatch} from './worldbook-regex.mjs';

export const WORLDBOOK_PLAN_FORMAT='modelmirror.rpg.author-worldbook-plan/1';
const channels=['system','prefix','suffix'];
const clone=value=>structuredClone(value);
const chars=value=>[...value].length;
const numeric=(value,min,max,code)=>{if(typeof value!=='number'||!Number.isFinite(value)||value<min||value>max)fail(code);};
const freeze=value=>{if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;};
const joinParts=parts=>parts.filter(text=>text!=='').join('\n\n');

// Exact decimal proportions avoid overflow, underflow and floating tie errors.
function decimal(value){
  const [mantissa,power='0']=value.toString().split('e');const dot=mantissa.indexOf('.');
  return {units:BigInt(mantissa.replace('.','')),power:Number(power)-(dot<0?0:mantissa.length-dot-1)};
}
function integerWeights(contents){
  if(!Array.isArray(contents)||!contents.length||contents.length>200)fail('AUTHORING_WORLDBOOK_WEIGHTS');
  const decimals=contents.map(item=>{numeric(item?.weight,0,Number.MAX_VALUE,'AUTHORING_WORLDBOOK_WEIGHTS');return decimal(item.weight);});
  const exponent=Math.min(...decimals.map(item=>item.power));
  const units=decimals.map(item=>item.units*10n**BigInt(item.power-exponent));
  const total=units.reduce((sum,value)=>sum+value,0n);if(total===0n)fail('AUTHORING_WORLDBOOK_ZERO_WEIGHT');
  return {units,total};
}
function fraction(value){const item=decimal(value);return item.power>=0?{n:item.units*10n**BigInt(item.power),d:1n}:{n:item.units,d:10n**BigInt(-item.power)};}
function draw(value){numeric(value,0,1,'AUTHORING_WORLDBOOK_DRAW_REQUIRED');if(value===1)fail('AUTHORING_WORLDBOOK_DRAW_REQUIRED');return fraction(value);}
export function normalizeWorldbookWeights(contents){
  const {units,total}=integerWeights(contents);
  const points=units.map(value=>Number(value*10000n/total));
  const rank=units.map((value,index)=>({index,remainder:value*10000n%total})).sort((a,b)=>a.remainder===b.remainder?a.index-b.index:a.remainder>b.remainder?-1:1);
  for(let i=0,remaining=10000-points.reduce((a,b)=>a+b,0);i<remaining;i++)points[rank[i].index]++;
  return contents.map((item,index)=>({...clone(item),weight:points[index]/100}));
}
export function chooseWorldbookContent(contents,value){
  const {units,total}=integerWeights(contents),random=draw(value);let accumulated=0n;
  for(let index=0;index<units.length;index++){accumulated+=units[index];if(units[index]>0n&&accumulated*random.d>random.n*total)return index;}
  fail('AUTHORING_WORLDBOOK_WEIGHTS');
}
function trigger(chance,value){
  if(chance===0)return false;if(chance===100)return true;
  const random=draw(value),threshold=fraction(chance);return random.n*100n*threshold.d<threshold.n*random.d;
}
function capacityOf(value){
  keys(value,['scanChars','injectedChars','systemChars','userChars'],'/capacity');
  for(const n of Object.values(value))if(!Number.isSafeInteger(n)||n<0||n>2000000)fail('AUTHORING_WORLDBOOK_CAPACITY_CONFIG');
  return clone(value);
}
function text(value){if(typeof value!=='string'||!value.isWellFormed()||value.length>4000000)fail('AUTHORING_WORLDBOOK_SOURCE');return value;}
function orderedParts(base,selected,document,target){
  const parts=[{id:'source:'+target,text:base,order:document.settings.orders[target],index:-1},...selected.map(({entry,index,body})=>({id:entry.id,text:body,order:entry.order,index}))];
  parts.sort((a,b)=>a.order-b.order||a.index-b.index||(a.id<b.id?-1:a.id>b.id?1:0));
  return parts;
}

/** Host-only candidate planner. Supplied draws must be frozen by the later operation coordinator. */
export async function buildWorldbookPlan(request){
  keys(request,['operationId','revision','document','currentInput','turns','draws','capacity']);
  if(!isId(request.operationId)||!Number.isSafeInteger(request.revision)||request.revision<0)fail('AUTHORING_WORLDBOOK_IDENTITY');
  // Snapshot before any await: concurrent editor changes cannot alter this plan.
  const {operationId,revision}=request;
  const document=validateDocument(request.document),book=document.worldbook;
  const currentInput=text(request.currentInput),capacity=capacityOf(request.capacity);
  if(!Array.isArray(request.turns)||request.turns.length>100000)fail('AUTHORING_WORLDBOOK_HISTORY');
  const complete=[];const turnIds=new Set();
  for(const turn of request.turns){
    keys(turn,['id','complete','user','assistant'],'/turns');
    if(!isId(turn.id)||turnIds.has(turn.id)||typeof turn.complete!=='boolean')fail('AUTHORING_WORLDBOOK_HISTORY');turnIds.add(turn.id);
    if(turn.complete)complete.push(turn);
  }
  const draws=new Map();
  if(!Array.isArray(request.draws)||request.draws.length>2000)fail('AUTHORING_WORLDBOOK_DRAW_REQUIRED');
  for(const item of request.draws){
    keys(item,['id','trigger','content'],'/draws');
    if(!book.entries.some(entry=>entry.id===item.id)||draws.has(item.id))fail('AUTHORING_WORLDBOOK_DRAW_REQUIRED');
    if(item.trigger!==null)draw(item.trigger);if(item.content!==null)draw(item.content);draws.set(item.id,clone(item));
  }
  const enabled=book.entries.map((entry,index)=>({entry,index})).filter(({entry})=>entry.enabled);
  for(const {entry} of enabled){
    if(!entry.keywords.length||entry.keywords.some(word=>word.trim()===''))fail('AUTHORING_WORLDBOOK_KEYWORDS','/worldbook/entries/'+entry.id);
    if(entry.mode==='weighted')integerWeights(entry.contents);
  }
  const depth=Math.max(0,...enabled.map(({entry})=>entry.depth));
  const recent=(depth?complete.slice(-depth):[]).map(turn=>({id:turn.id,user:text(turn.user),assistant:text(turn.assistant)}));
  const pool=[{source:'current',role:'user',text:currentInput},...recent.flatMap((turn,index)=>[{source:turn.id,turnIndex:index,role:'user',text:turn.user},{source:turn.id,turnIndex:index,role:'assistant',text:turn.assistant}])];
  const pieces={system:[],prefix:[],suffix:[]},decisions=new Map(),scan={};let comparisons=0,injected=0;
  const pick=item=>{
    const {entry,index}=item;let decision=decisions.get(entry.id);
    if(!decision){decision={entryId:entry.id,index,phases:[],trigger:null,content:null,targets:[]};decisions.set(entry.id,decision);}
    if(decision.trigger===null){
      const random=draws.get(entry.id);const pass=trigger(entry.chance,random?.trigger);
      decision.trigger={chance:entry.chance,draw:entry.chance>0&&entry.chance<100?random.trigger:null,pass};
      if(pass){const selected=entry.mode==='weighted'?chooseWorldbookContent(entry.contents,random?.content):null;const body=selected===null?entry.body:entry.contents[selected].text;decision.content={index:selected,draw:selected===null?null:random.content,sha256:sha256(body)};}
    }
    return decision;
  };
  for(const phase of ['wrappers','system']){
    if(phase==='system')pool[0]={source:'current-with-wrappers',role:'user',text:joinParts([joinParts(orderedParts(document.sources.prefix,pieces.prefix,document,'prefix').map(p=>p.text)),currentInput,joinParts(orderedParts(document.sources.suffix,pieces.suffix,document,'suffix').map(p=>p.text))])};
    const candidates=enabled.filter(({entry})=>phase==='system'?entry.targets.includes('system'):entry.targets.some(t=>t!=='system'));
    const indexes=new Map(),regexJobs=[],scanned=new Set();
    for(const {entry} of candidates){
      const selected=pool.map((message,i)=>({message,i})).filter(({message,i})=>entry.roles.includes(message.role)&&(i===0||entry.depth>0&&message.turnIndex>=recent.length-entry.depth)).map(({i})=>i);
      indexes.set(entry.id,selected);selected.forEach(i=>scanned.add(i));
      comparisons+=entry.keywords.length*selected.reduce((n,i)=>n+pool[i].text.length+1,0);
      if(comparisons>20000000)fail('AUTHORING_WORLDBOOK_MATCH_CAPACITY');
      if(entry.regex)regexJobs.push({id:entry.id,patterns:entry.keywords,messageIndexes:selected});
    }
    const scannedIndexes=[...scanned].sort((a,b)=>a-b);
    scan[phase]={chars:scannedIndexes.reduce((n,i)=>n+chars(pool[i].text),0),sources:scannedIndexes.map(i=>({source:pool[i].source,role:pool[i].role}))};
    if(scan[phase].chars>capacity.scanChars)fail('AUTHORING_WORLDBOOK_SCAN_CAPACITY');
    // Transfer only selected regex inputs; unrelated messages never consume worker capacity.
    const regexIndexes=[...new Set(regexJobs.flatMap(job=>job.messageIndexes))].sort((a,b)=>a-b);
    const compact=new Map(regexIndexes.map((index,i)=>[index,i]));
    const results=regexJobs.length?await evaluateRegexBatch(regexJobs.map(job=>({...job,messageIndexes:job.messageIndexes.map(i=>compact.get(i))})),regexIndexes.map(i=>pool[i].text)):[];
    const regexResults=new Map(results.map(result=>[result.id,result.hits.map(hit=>hit?{...hit,messageIndex:regexIndexes[hit.messageIndex]}:null)]));
    for(const item of candidates){
      const {entry,index}=item;let hits;
      if(entry.regex)hits=regexResults.get(entry.id);
      else hits=entry.keywords.map((keyword,keywordIndex)=>{const needle=keyword.toLowerCase();for(const i of indexes.get(entry.id)){const offset=pool[i].text.toLowerCase().indexOf(needle);if(offset>=0)return {keywordIndex,messageIndex:i,offset};}return null;});
      const matched=entry.rule==='and'?hits.every(Boolean):hits.some(Boolean);
      let decision=decisions.get(entry.id);
      if(!decision){decision={entryId:entry.id,index,phases:[],trigger:null,content:null,targets:[]};decisions.set(entry.id,decision);}
      decision.phases.push({phase,matched,hits:hits.filter(Boolean).map(hit=>({keywordIndex:hit.keywordIndex,source:pool[hit.messageIndex].source,role:pool[hit.messageIndex].role}))});
      if(!matched)continue;decision=pick(item);if(!decision.trigger.pass)continue;
      const body=decision.content.index===null?entry.body:entry.contents[decision.content.index].text;
      for(const target of entry.targets.filter(t=>phase==='system'?t==='system':t!=='system')){
        pieces[target].push({entry,index,body});decision.targets.push(target);injected+=chars(body);
        if(injected>capacity.injectedChars)fail('AUTHORING_WORLDBOOK_INJECTION_CAPACITY');
      }
    }
  }
  const assembly={},segments={};
  for(const target of channels){segments[target]=orderedParts(document.sources[target],pieces[target],document,target);assembly[target]=joinParts(segments[target].map(part=>part.text));}
  assembly.user=joinParts([assembly.prefix,currentInput,assembly.suffix]);
  if(chars(assembly.system)>capacity.systemChars||chars(assembly.user)>capacity.userChars)fail('AUTHORING_WORLDBOOK_OUTPUT_CAPACITY');
  const context={operationId,revision,worldbook:book,sources:Object.fromEntries(channels.map(key=>[key,document.sources[key]])),orders:document.settings.orders,currentInput,turns:recent,draws:[...draws.values()],capacity};
  const payload={format:WORLDBOOK_PLAN_FORMAT,operationId,revision,contextHash:sha256(canonical(context)),capacity,scan,assembly,segments,decisions:[...decisions.values()].sort((a,b)=>a.index-b.index),injectedChars:injected};
  return freeze({...payload,sha256:sha256(canonical(payload))});
}

/** Replay a host-owned frozen artifact, never run matching or randomness again. Not an authenticity signature. */
export function replayWorldbookPlan(plan,{operationId,revision,contextHash}){
  const saved=clone(plan),hash=saved.sha256;delete saved.sha256;
  if(saved.format!==WORLDBOOK_PLAN_FORMAT||saved.operationId!==operationId||saved.revision!==revision||saved.contextHash!==contextHash||sha256(canonical(saved))!==hash)fail('AUTHORING_WORLDBOOK_PLAN_MISMATCH');
  return freeze({...saved,sha256:hash});
}
