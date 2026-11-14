import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {actionAdapters} from '../../agent-core/actions/adapters.mjs';
import {ActionStore} from '../../agent-core/actions/store.mjs';
import {CoreStore} from '../../agent-core/store.mjs';
import {clone,hash,need} from '../../agent-core/contracts.mjs';

// Presentation metadata only; action identity, parameters and arithmetic come from R5.
export const PRESENTATION={
 cultivation:{title:'青云药圃',subtitle:'林清 · 成年修士测试档案',query:{queries:[{domain:'items'}]},gift:true},
 lighthouse:{title:'海岬灯塔',subtitle:'许舟 · 成年守灯人测试档案',query:{queries:[{domain:'supplies'}]},gift:false}
};
export const reference=r=>({session:r.id,branch:r.id,revision:r.revision,stateRevision:r.stateRevision,bindingHash:hash(r.binding),stateHash:hash(r.state),recordHash:hash(r)});
export function project(adapter,record){
 new CoreStore(null,adapter).validate(record);
 need(record.binding.adapterVersion==='5','E4_SOURCE_VERSION');
 need(record.config&&Object.keys(record.config).every(k=>['profiles','mods'].includes(k))&&['profiles','mods'].every(k=>Array.isArray(record.config[k])&&!record.config[k].length),'E4_CONFIG_UNSUPPORTED');
 const presentation=PRESENTATION[adapter.id];need(presentation,'E4_ADAPTER');
 const declaration=adapter.tools.entries.find(e=>e.name==='prepare_action'&&e.effect==='proposal');
 need(declaration?.tool.function.parameters.properties.actionId.enum.includes(adapter.rule.id),'E4_CAPABILITY');
 const ref=reference(record),resources=clone(adapter.resources(record.state));
 return {adapterId:adapter.id,reference:ref,record:clone(record),ready:record.enabled&&!record.pending,
  title:presentation.title,subtitle:presentation.subtitle,
  resources,people:adapter.id==='cultivation'?record.state.relations.map(({id,name})=>({id,name})):[],
  capabilities:[{id:'inspect',label:'查看登记物资',kind:'read'},
   {id:adapter.rule.id,label:'拟'+adapter.rule.label+'方案',kind:'rule',ruleVersion:adapter.rule.version,parameters:clone(declaration.tool.function.parameters)},
   ...(presentation.gift?[{id:'gift',label:'考虑赠送',kind:'intent-only'}]:[])],
  coverage:'只覆盖本次登记范围；登记人物不等于在场，未建模容器关系未知。'};
}
export function query(adapter,frame){
 need(frame.ready,'E4_SOURCE_BUSY');return adapter.tools.execute('query_local_state',PRESENTATION[adapter.id].query,frame.record,{allowed:['query_local_state']}).output;
}
export function giftWorld(frame){need(PRESENTATION[frame.adapterId].gift,'E4_GIFT_UNSUPPORTED');return {session:frame.record.id,branch:frame.record.id,version:frame.record.stateRevision,state:clone(frame.record.state)};}
export function sourceFromReader(adapter,read){return {adapter,async read(){return project(adapter,await read());}};}
export function fileSource(adapter,file){return sourceFromReader(adapter,async()=>{const raw=await readFile(file,'utf8');need(Buffer.byteLength(raw)<=16*1024*1024,'E4_SOURCE_SIZE');return JSON.parse(raw);});}
export async function openSources(directory){
 const list=await actionAdapters(),sources=new Map();await mkdir(directory,{recursive:true});
 for(const adapter of list){
  const file=join(directory,adapter.id+'.json');
  try{await readFile(file);}catch(e){
   if(e.code!=='ENOENT')throw e;
   const store=await ActionStore.open({directory:join(directory,adapter.id+'-seed'),adapter});
   try{const record=await store.create({operationId:'e4-fixture',initial:adapter.initial(),confirmed:true,source:{kind:'example',id:'e4-adult-'+adapter.id,history:[]}});
    await writeFile(file,JSON.stringify(record,null,2),'utf8');
   }finally{await store.close();}
  }
  const source=fileSource(adapter,file);await source.read();sources.set(adapter.id,source);
 }
 return sources;
}
