import test from 'node:test';
import assert from 'node:assert/strict';
import { diagnoseOutput } from '../ui-host/output-diagnostics.mjs';
import { loadBuiltinBundle, hashText } from '../ui-host/setup.mjs';
const card = loadBuiltinBundle().cardPackage;
const request = { exchangeId: 'ex.offline', input: { kind: 'query', text: '已知情况' } };
const exchange = () => ({ format: 'modelmirror.ai-rpg.turn-exchange', formatVersion: '0.1.0', exchangeId: request.exchangeId, cardPackageRef: { id: card.package.id, version: card.package.version }, input: request.input, proposal: { narrative: '离线测试', suggestedActions: [], informationModules: [], stateProposals: [], uncertainties: [] } });
const report = text => ({ valid: true, value: { status: 'succeeded', text } });
test('valid output stays byte-identical; terminal evidence is explicitly inferred', () => {
 const r=report(JSON.stringify(exchange())), before=structuredClone(r), d=diagnoseOutput(r,request,card);
 assert.equal(d.contract,'passed');assert.equal(d.binding,'passed');assert.equal(d.rawSha256,hashText(r.value.text));assert.equal(d.rawSseCaptured,false);assert.match(d.terminalEvidence,/^inferred/);assert.deepEqual(r,before);
});
test('missing final brace is rejected without repair or text in diagnostics', () => {
 const text=JSON.stringify(exchange()).slice(0,-1),d=diagnoseOutput(report(text),request,card);
 assert.equal(d.json,'failed');assert.equal(d.contract,'not_checked');assert.equal(d.rawSha256,hashText(text));assert.deepEqual(d.diagnostics,[{code:'OUTPUT_JSON_PARSE_FAILED',path:''}]);assert.ok(!JSON.stringify(d).includes('离线测试'));
});
test('wrong module key is distinct from JSON syntax failure', () => {
 const x=exchange();x.proposal.informationModules=[{fieldRef:'info.rpg04.memory',values:[]}];
 const d=diagnoseOutput(report(JSON.stringify(x)),request,card);
 assert.equal(d.json,'passed');assert.equal(d.contract,'failed');assert.ok(d.diagnostics.some(x=>x.path==='/proposal/informationModules/0/moduleRef'));
});
test('state shortText does not authorize string in list-valued information fields', () => {
 for(const suffix of ['ltm','saves']) {
  assert.equal(card.stateFields.find(f=>f.id==='state.rpg04.memory.'+suffix).valueType,'shortText');
  const x=exchange();x.proposal.informationModules=[{moduleRef:'info.rpg04.memory',values:[{fieldRef:'field.rpg04.memory.'+suffix,value:'（空）'}]}];
  const bad=diagnoseOutput(report(JSON.stringify(x)),request,card);assert.ok(bad.diagnostics.some(d=>d.code==='TURN_EXCHANGE_INFORMATION_VALUE_TYPE'));
  x.proposal.informationModules[0].values[0].value=[];assert.equal(diagnoseOutput(report(JSON.stringify(x)),request,card).contract,'passed');
 }
});
test('binding failure and transport failure remain separate and never repaired', () => {
 const x=exchange();x.exchangeId='ex.other';const d=diagnoseOutput(report(JSON.stringify(x)),request,card);assert.equal(d.contract,'passed');assert.equal(d.binding,'failed');
 const failed=diagnoseOutput({valid:false,value:{status:'failed',text:'private'}},request,card);assert.equal(failed.transport,'failed');assert.equal(failed.json,'not_checked');assert.equal(failed.terminalEvidence,'not_established');
});


test('observed transport and schema success do not conceal a rejected contract', () => {
 const text='private original text',evidence={rawBytes:40,outputValidation:{transport:'passed',json:'passed',schema:'passed',contract:'failed',binding:'not_checked',rawText:text,diagnostics:[{code:'TURN_EXCHANGE_INFORMATION_MODULE_DUPLICATE',path:'/proposal/informationModules/1/moduleRef'}]}};
 const d=diagnoseOutput({valid:false,value:{status:'failed',text}},request,card,evidence);
 assert.equal(d.transport,'passed');assert.equal(d.wireSchema,'passed');assert.equal(d.contract,'failed');assert.equal(d.binding,'not_checked');assert.equal(d.rawSha256,hashText(text));assert.ok(!JSON.stringify(d).includes(text));
});

test('converted output diagnostics retain original model text hash separately from converted hash', () => {
 const converted=JSON.stringify(exchange()),raw='original keyed JSON';
 const d=diagnoseOutput(report(converted),request,card,{rawBytes:40,outputValidation:{transport:'passed',json:'passed',schema:'passed',contract:'passed',binding:'passed',rawText:raw,diagnostics:[]},conversion:{format:'rpg05-keyed-codec/1',convertedSha256:hashText(converted)}});
 assert.equal(d.rawSha256,hashText(raw));assert.equal(d.convertedSha256,hashText(converted));assert.equal(d.contract,'passed');assert.equal(d.terminalEvidence,'client_received_sse_observation');assert.equal(d.rawSseCaptured,true);
});
