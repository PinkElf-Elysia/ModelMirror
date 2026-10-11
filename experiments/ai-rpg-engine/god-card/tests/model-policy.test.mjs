import test from 'node:test';
import assert from 'node:assert/strict';
import {createModelPolicy,choice} from '../../studio/card-model-policy.mjs';
import {fixedChoice,controlledChoice,validChoice} from '../../studio/model-runtime.mjs';
import {parametersFor} from '../../studio/model-parameters.mjs';
import {models} from '../../studio/provider.mjs';
import {canonical,sha} from '../../plugins/catalog.mjs';
test('Earth choices retain exact configured parameters and the previously approved Luna exception',()=>{
 assert.deepEqual(fixedChoice(),choice({kind:'fixed',model:models.earth.model,parameters:models.earth.parameters}));
 for(const model of [models.earth.model,'openai/gpt-5.6-luna','synthetic/unknown']){
  const m={model,selectionId:'fixture',selectionRevision:'v1'};
  assert.deepEqual(controlledChoice(m),choice({kind:'controlled',...m,parameters:parametersFor(model)}));
  assert.equal(validChoice(controlledChoice(m)),true);
 }
 assert.deepEqual(controlledChoice({model:'openai/gpt-5.6-luna',selectionId:'fixture',selectionRevision:'v1'}).parameters,{max_tokens:16384});
});
test('God has no implicit model, Earth defaults or Luna parameter exception',()=>{
 const unconfigured=createModelPolicy({cardId:'god',parametersFor:()=>null});
 assert.throws(()=>unconfigured.fixedChoice(),/CARD_MODEL_SELECTION_REQUIRED/);
 for(const name of [models.earth.model,'openai/gpt-5.6-luna'])assert.throws(()=>unconfigured.controlledChoice({model:name,selectionId:'fixture',selectionRevision:'v1'}),/CARD_MODEL_UNCONFIGURED/);
 assert.equal(unconfigured.validChoice(fixedChoice()),false);
 const god=createModelPolicy({cardId:'god',parametersFor:model=>model==='synthetic/god'?{max_tokens:1234,temperature:.25}:null});
 const selected=god.controlledChoice({model:'synthetic/god',selectionId:'mock',selectionRevision:'version-2',parameters:{max_tokens:99999},url:'https://ignored.invalid'});
 assert.deepEqual(selected.parameters,{max_tokens:1234,temperature:.25});assert.equal('url' in selected,false);
 assert.equal(god.validChoice(selected),true);assert.equal(validChoice(selected),false);
 assert.equal(god.validChoice(controlledChoice({model:models.earth.model,selectionId:'fixture',selectionRevision:'v1'})),false);
 selected.parameters.max_tokens=9;
 assert.equal(god.validChoice(selected),false);
 assert.equal(god.controlledChoice({model:'synthetic/god',selectionId:'mock',selectionRevision:'v2'}).parameters.max_tokens,1234);
});
test('unknown choices, forged hashes and extra fields cannot become trusted model configuration',()=>{
 const p=createModelPolicy({cardId:'fixture',parametersFor:model=>model==='mock'?{max_tokens:5}:null});
 const c=p.controlledChoice({model:'mock',selectionId:'select',selectionRevision:'rev'});
 for(const value of [{...c,configHash:sha('bad')},{...c,baseURL:'http://other'},choice({...c,parameters:{max_tokens:6}}),{...c,kind:'fixed'}])assert.equal(p.validChoice(value),false);
 assert.throws(()=>p.controlledChoice({model:'mock',selectionId:'../other',selectionRevision:'v1'}),/MODEL_SELECTION_INVALID/);
 assert.throws(()=>createModelPolicy({cardId:'fixture',fixed:{model:'mock',parameters:{max_tokens:6}},parametersFor:()=>({max_tokens:5})}),/CARD_MODEL_POLICY_INVALID/);
});
