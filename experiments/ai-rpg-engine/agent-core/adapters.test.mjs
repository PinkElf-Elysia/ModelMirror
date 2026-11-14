import {test} from 'node:test';import assert from 'node:assert/strict';
import {adapters} from './composition.mjs';import {clone} from './contracts.mjs';
import {applyOperations} from './adapters/cultivation/rules.mjs';import {applyOperations as oracle} from '../local-state/rules.mjs';
import {assembleToolsContext} from '../local-state/tools/adapter.mjs';import {renderPanels} from '../local-state/panels.mjs';
import {TOOL} from '../local-state/tools/query.mjs';import {fixture} from './test-support.mjs';
test('cultivation rules match frozen oracle, shared numeric branches and semantic updates',async()=>{
 const {list}=await adapters(),a=list[0],s=a.initial(),reason='测试依据';
 const batches=[
 [{type:'quantity',id:'medicine',delta:-2,reason}], [{type:'quantity',id:'medicine',delta:-5,reason}],
 [{type:'balance',account:'low',delta:8,reason},{type:'balance',account:'contribution',delta:3,reason}],
 [{type:'relation',id:'zhou',axis:'relation',event:'help',delta:8,reason}],
 [{type:'relation',id:'zhou',axis:'trust',event:'promise',delta:3,reason}],
 [{type:'set',field:'stamina',value:'疲惫',reason},{type:'mark',value:'新印记',reason}],
 [{type:'progress',id:'breathing',value:'更熟练',reason}],
 [{type:'condition',id:'bag',value:'磨损',reason},{type:'equip',slot:'storage',id:'bag',reason}],
 [{type:'addAbility',entry:{id:'dew',kind:'术法',name:'引露术',description:'引露',progress:'初学',source:reason},reason}],
 [{type:'addItem',entry:{id:'new-bag',kind:'装备',name:'新药囊',description:'测试',quantity:1,source:reason,condition:'完好'},reason}],
 [{type:'relationInfo',id:'zhou',field:'impression',value:'可靠',reason},{type:'faction',entry:{id:'sect',name:'青云宗',description:'外门弟子'},reason}]
 ];
 for(const batch of batches)assert.deepEqual(applyOperations(s,batch,{strictRelations:true}),oracle(s,batch,{strictRelations:true}));
 for(const axis of ['relation','trust']){const top=clone(s);top.relations[0][axis]=98;const op=[{type:'relation',id:'zhou',axis,event:axis==='relation'?'help':'promise',delta:5,reason}];assert.deepEqual(applyOperations(top,op),oracle(top,op));assert.equal(applyOperations(top,op).changes[0].after,100);}
 for(const batch of [[{type:'quantity',id:'medicine',delta:-6,reason}],[{type:'balance',account:'low',delta:Number.MAX_SAFE_INTEGER,reason}],[{type:'addRelation',id:'new-zhou',name:'周禾',reason}],[{type:'set',field:'name',value:'换名',reason}]]){
  let x,y;try{applyOperations(s,batch,{strictRelations:true});}catch(e){x=e.code;}try{oracle(s,batch,{strictRelations:true});}catch(e){y=e.code;}assert.ok(x);assert.equal(x,y);
 }
 assert.throws(()=>applyOperations(s,[],{quantityOnly:{id:'medicine',delta:-2}}));
});
test('actual context, tool declaration, panel and plugin projections preserve v2',async t=>{
 const f=await fixture(t,'cultivation'),a=f.host.adapter,r=f.record,input='先查询丹药，再服用两份养息丹';
 const old=await assembleToolsContext(r,input),current=await a.assemble(r,input);assert.deepEqual(current,old);assert.deepEqual(a.query.tool,TOOL);
 assert.equal(await a.render(r.state),await renderPanels(r.state));
 const done=await f.turn('帮周禾扶好竹架');const context=await a.assemble(done,'与周禾聊聊接下来的打算');
 assert.deepEqual(context,await assembleToolsContext(done,'与周禾聊聊接下来的打算'));
 const history=context.messages.filter(m=>m.role==='assistant').map(m=>m.content).join('');
 assert.doesNotMatch(history,/<state_delta>|【自身状态】|【关系网络】/);
 const memory=JSON.stringify(await a.memory(done,done.turns[0]));assert.match(memory,/已提交变化/);assert.doesNotMatch(memory,/tool_call_id/);
});
test('lighthouse schema, unknown query and partial coverage are independent of cultivation',async()=>{
 const {list}=await adapters(),a=list[1],s=a.initial();
 assert.deepEqual(Object.keys(s).sort(),['duty','keeper','supplies']);assert.throws(()=>a.validateState(list[0].initial()));
 const output=a.query.execute(s,7,{queries:[{domain:'supplies',limit:1},{domain:'supplies',name:'绷带'},{domain:'supplies',name:'不存在'}]});
 assert.equal(output.stateRevision,7);assert.equal(output.results[0].coverage,'partial');assert.equal(output.results[1].status,'unset');assert.equal(output.results[2].status,'no-match');
 for(const bad of [{queries:[{domain:'items'}]},{queries:[{domain:'supplies',session:'other'}]},{queries:[{domain:'supplies',limit:51}]}])assert.throws(()=>a.query.validate(bad));
 const before=clone(s);a.query.execute(s,7,{queries:[{domain:'supplies'}]});assert.deepEqual(s,before);
});
