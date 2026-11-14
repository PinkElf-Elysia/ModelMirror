import {cultivationAdapter} from '../adapters/cultivation/index.mjs';
import {lighthouseAdapter} from '../adapters/lighthouse/index.mjs';
import {assemblePrepared,material,VERSION} from './assemble.mjs';
import {cultivationSource} from './cultivation-source.mjs';
import {clone,hash,need,keys} from '../contracts.mjs';
import {validateConfig as resolveConfig} from '../../local-state/context.mjs';
const FINAL='【生成职责】工具阶段仅提出只读查询，可有简短进度；最终阶段仅生成剧情、原卡未接管的必要展示及末尾一个 state_delta。不要输出思维链、检查过程、PHASE 表或宿主维护的完整面板。材料标注为转述、推测或待采用提案时，不能当作已确认发生的事实。状态校验只证明结构合法，不证明剧情判断属实。';
const scopeOf=r=>({session:r.id,stateRevision:r.stateRevision});
function options(o){keys(o,['selection','quantityOnly']);if(o.selection){keys(o.selection,['mode','refs']);need(['full','scoped'].includes(o.selection.mode),'CONTEXT_SELECTION');need(Array.isArray(o.selection.refs)&&o.selection.refs.length<=20&&o.selection.refs.every(x=>typeof x==='string'&&x.length<=120),'CONTEXT_REFS');}return o.selection||{mode:'full',refs:[]};}
function historical(scope,messages){return messages.slice(1).map((m,i)=>material(scope,'message-'+i,m.role==='user'?'player-input':'history','reference',typeof m.content==='string'?m.content:JSON.stringify(m.content),{kind:'frozen-message',index:i+1,hash:hash(m)},{required:true,placement:'trace-only'}));}
function bind(base,prepare,contentHash){
 const adapter={...base,binding:{...base.binding,adapterVersion:'4',effectiveContextHash:contentHash},prepare};
 adapter.assemble=async(r,input,o={})=>{const {baseContext,...prepared}=await prepare(r,input,o),out=assemblePrepared(prepared);return {...baseContext,...out,mode:VERSION,hash:hash({messages:out.messages,tools:baseContext.tools,tool_choice:'auto'}),binding:clone(adapter.binding)};};return adapter;
}
export async function cultivationContextAdapter(){
 const base=await cultivationAdapter(),source=await cultivationSource();
 const prepare=async(r,input,o={})=>{
  const selection=options(o),baseContext=await base.assemble(r,input,{quantityOnly:o.quantityOnly}),scope=scopeOf(r);
  const suffix=baseContext.messages[0].content.slice(source.legacy.system.length);
  need(baseContext.messages[0].content.startsWith(source.legacy.system),'CONTEXT_PREFIX_CHANGED');
  // Evaluate plugins using the original complete trigger pool BEFORE replacing source material.
  const cfg=await resolveConfig(r.config),authored=[...cfg.profiles.map(x=>JSON.stringify(x)),...cfg.mods.map(x=>x.text)].join('\n');
  need(!/<cot>|必须[^\n]{0,30}输出[^\n]{0,15}思维链|输出[^\n]{0,15}PHASE/.test(authored),'CONTEXT_OUTPUT_CONFLICT');
  const materials=source.materials(scope),stateAt=suffix.indexOf('【本轮只读状态与已确认事件】'),toolsAt=suffix.indexOf('【本地状态工具模式】');
  need(stateAt>=0&&toolsAt>stateAt,'CONTEXT_RUNTIME_ANCHOR');
  for(const [id,purpose,nature,text] of [['plugins','plugin-projection','instruction',suffix.slice(0,stateAt)],['state-events','state-and-events','confirmed',suffix.slice(stateAt,toolsAt)],['query-protocol','tool-contract','instruction',suffix.slice(toolsAt)]])materials.push(material(scope,id,purpose,nature,text,{kind:'legacy-assembly',configHash:hash(r.config),hash:hash(text)},{required:true,placement:'trace-only'}));
  materials.push(...historical(scope,baseContext.messages),material(scope,'generation','output-contract','instruction',FINAL,{kind:'adapter/4',hash:hash(FINAL)},{required:true}));
  const bindings=clone(source.bindings);
  for(const group of ['items','abilities','relations'])for(const e of r.state[group]){
   const refs=['object:'+e.id,'object:'+e.name],id='selected-'+group+'-'+e.id;
   if(refs.some(ref=>selection.refs.includes(ref))){materials.push(material(scope,id,'state',e.quantity===null?'unset':'confirmed',JSON.stringify(e),{kind:'local-state',group,id:e.id,revision:r.stateRevision},{required:true}));for(const ref of refs)bindings[ref]=[id];}
  }
  const issues=selection.mode==='scoped'&&!selection.refs.some(x=>x.startsWith('scene:'))?['missing-scene-binding']:[];
  return {baseContext,scope,selection,materials,bindings,issues,messages:baseContext.messages,replacePrefix:source.legacy.system,diagnostics:[{kind:'selection-boundary',message:'只使用显式场景与对象绑定；自由输入涉及范围外事实时请选完整资料。未做全文语义审查。'},{kind:'source-audit',sourceHash:source.source.hash,segments:source.audit}]};
 };
 const adapter=bind(base,prepare,hash({source:source.source,quality:source.materials({session:'binding',stateRevision:0}),FINAL}));
 adapter.name+=' · R4';adapter.description='同一公共执行核心；有限场景资料选择，未命中回退完整资料。原始素材不变。';
 adapter.contextChoices=[['full','完整资料'],['scene:qingyun','青云宗 · 有限资料'],['scene:qingniu','青牛镇 · 有限资料']];
 const validate=base.validateConfig;adapter.validateConfig=async c=>{const config=await validate(c),authored=[...config.profiles.map(x=>JSON.stringify(x)),...config.mods.map(x=>x.text)].join('\n');need(!/<cot>|必须[^\n]{0,30}输出[^\n]{0,15}思维链|输出[^\n]{0,15}PHASE/.test(authored),'CONTEXT_OUTPUT_CONFLICT');return config;};
 adapter.sourceAudit=source.audit;return adapter;
}
export const CONTINUITY=[
 {id:'promise',input:'我承诺交接前检查窗栓，先记下来，暂时不执行。',nature:'confirmed',body:'你在交接簿上记下检查窗栓的承诺，把笔搁在一旁。窗边的检查还没有进行。',duty:'守灯；已承诺交接前检查窗栓（待履行）'},
 {id:'claim',input:'同伴说窗栓已经检查好了；我猜可能不用再查，暂不确认。',nature:'character-claim',hypothesis:'玩家猜测可能不用再查；尚未核实。',body:'同伴说他已经看过窗栓。你听见了这句话，却还没有亲自核实；交接簿里的承诺仍未勾销。'},
 {id:'proposal',input:'考虑取消检查窗栓的承诺，仅提出方案，尚未采用。',nature:'pending-proposal',body:'你把取消检查的想法暂记在草稿边，尚未决定采用。交接簿上的待履行安排保持原样。'},
 {id:'complete',input:'我现在逐一检查并确认窗栓牢固，将交接前检查记为完成。',nature:'confirmed',body:'你走到窗边，逐一扳紧窗栓并确认牢固，在交接簿上勾掉这一项。笔尖停在下一行的空白处。',duty:'守灯；交接前窗栓检查已完成'}
];
export async function lighthouseContextAdapter(){
 const base=await lighthouseAdapter(),definitions=CONTINUITY.map(x=>({...x}));
 const prepare=async(r,input,o={})=>{
  const selection=options(o),baseContext=await base.assemble(r,input,{quantityOnly:o.quantityOnly}),scope=scopeOf(r),system=baseContext.messages[0].content;
  const materials=[material(scope,'rules','global-rule','instruction',system.split('\n当前简况：')[0],{kind:'lighthouse-adapter/3',hash:base.binding.sourceHash},{required:true}),
   material(scope,'current','state','confirmed',JSON.stringify({keeper:r.state.keeper,duty:r.state.duty}),{kind:'local-state',revision:r.stateRevision},{required:true}),
   material(scope,'generation','output-contract','instruction',FINAL,{kind:'adapter/4',hash:hash(FINAL)},{required:true}),
   material(scope,'lighthouse-setting','world-reference','reference','原创灯塔夜班交接场景。未经实际确认，听闻和猜测不等于窗栓检查已经完成。',{kind:'original-fixture',hash:hash('lighthouse-context/4')}),...historical(scope,baseContext.messages)];
  // Exact fixture annotations, NOT an automatic truth detector for arbitrary prose.
  const sources=[...r.turns.map(t=>({input:t.input,event:t.id})),{input,event:'current-input'}];
  for(const [i,s] of sources.entries()){
   const d=definitions.find(d=>d.input===s.input);if(!d)continue;
   materials.push(material(scope,'continuity-'+i,'continuity',d.nature,d.input,{kind:'fixture-annotation',event:s.event,fixture:d.id,hash:hash(d)},{required:true}));
   if(d.hypothesis)materials.push(material(scope,'hypothesis-'+i,'continuity','hypothesis',d.hypothesis,{kind:'fixture-annotation',event:s.event,fixture:d.id},{required:true}));
  }
  materials.push(material(scope,'duty-authority','state-contract','instruction','duty 保存已接受的安排；转述完成不等于实际核实，未采用的取消提案不修改 duty。仅明确接受的安排或明确执行的结果可提出 setDuty。结构校验不验证叙事真假。',{kind:'fixture-rule',version:4},{required:true}));
  return {baseContext,scope,selection,materials,bindings:{'scene:lighthouse':['lighthouse-setting'],'object:window':['lighthouse-setting'],'object:lamp-oil':['current']},messages:baseContext.messages,replacePrefix:system,diagnostics:[{kind:'fixture-boundary',message:'连续性性质仅标注固定样例；不自动判定任意对话真假，不新增承诺或任务表。'}]};
 };
 const adapter=bind(base,prepare,hash({definitions,FINAL,source:base.binding.sourceHash}));
 adapter.name+=' · R4';adapter.description='同一装配接口；验证承诺、转述、推测、提案与明确完成的区别。';adapter.contextChoices=[['full','完整资料'],['scene:lighthouse','灯塔 · 有限资料']];
 adapter.scenarios=[...definitions.map(x=>[x.id,x.input]),...base.scenarios];
 adapter.fixture=(request,meta)=>{const d=definitions.find(x=>x.input===meta.input);if(!d)return base.fixture(request,meta);return {text:d.body+'\n<state_delta>'+JSON.stringify({operations:d.duty?[{type:'setDuty',value:d.duty,reason:'固定中性样例中明确接受的安排或执行结果'}]:[]})+'</state_delta>'};};
 return adapter;
}
