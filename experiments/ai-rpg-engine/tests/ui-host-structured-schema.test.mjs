import test from 'node:test';import assert from 'node:assert/strict';import Ajv from 'ajv/dist/2020.js';
import {compileStructuredSchema} from '../ui-host/structured-schema.mjs';import {loadBuiltinBundle} from '../ui-host/setup.mjs';import {validateTurnExchange} from '../src/index.mjs';
const card=loadBuiltinBundle().cardPackage,request={exchangeId:'ex.offline',input:{kind:'action',text:'观察'}};
function example(){return {format:'modelmirror.ai-rpg.turn-exchange',formatVersion:'0.1.0',exchangeId:request.exchangeId,cardPackageRef:{id:card.package.id,version:card.package.version},input:request.input,proposal:{narrative:'离线',suggestedActions:[],informationModules:[],stateProposals:[],uncertainties:[]}};}
test('deterministic schema rejects module/field/value mismatch and accepts frozen-valid output',()=>{
 const out=compileStructuredSchema(card,request);assert.deepEqual(out,compileStructuredSchema(card,request));const check=new Ajv({strict:false}).compile(out.responseFormat.json_schema.schema);const x=example();assert.ok(check(x));assert.ok(validateTurnExchange(x,card).valid);
 x.proposal.informationModules=[{moduleRef:'info.rpg04.memory',values:[{fieldRef:'field.rpg04.memory.ltm',value:'（空）'}]}];assert.equal(check(x),false);x.proposal.informationModules[0].values[0].value=[];assert.ok(check(x));assert.ok(validateTurnExchange(x,card).valid);x.proposal.informationModules[0].moduleRef='info.rpg04.scene';assert.equal(check(x),false);
});
test('query excludes state changes; optional rationale supports absent and present without null coercion',()=>{
 const f=card.stateFields.find(f=>f.valueType==='shortText'&&f.modelMayPropose),x=example(),check=new Ajv({strict:false}).compile(compileStructuredSchema(card,request).responseFormat.json_schema.schema);x.proposal.stateProposals=[{fieldRef:f.id,proposedValue:'离线'}];assert.ok(check(x));x.proposal.stateProposals[0].rationale='说明';assert.ok(check(x));x.proposal.stateProposals[0].rationale=null;assert.equal(check(x),false);
 const q={...request,input:{kind:'query',text:'现状'}};x.input=q.input;delete x.proposal.stateProposals[0].rationale;assert.equal(new Ajv({strict:false}).compile(compileStructuredSchema(card,q).responseFormat.json_schema.schema)(x),false);
});

test('invalid input shape cannot become a trusted schema',()=>{
 for(const input of [{kind:'action',text:42},{kind:'action',text:'x',system:'forbidden'},{kind:'command',commandRef:'missing',text:'x'}])assert.throws(()=>compileStructuredSchema(card,{...request,input}),/STRUCTURED_REQUEST_INVALID/);
});
