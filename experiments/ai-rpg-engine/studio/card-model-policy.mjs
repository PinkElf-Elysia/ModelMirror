import {canonical,sha,fail} from '../plugins/catalog.mjs';
export function choice(value){const config=structuredClone(value);return {...config,configHash:sha(canonical(config))};}
const parameters=v=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.values(v).every(n=>typeof n==='number'&&Number.isFinite(n));
// Configuration comes from the host's frozen card profile, not from the client.
// No card default, fallback model or cross-card exception is supplied here.
export function createModelPolicy({cardId,fixed=null,parametersFor}){
 if(typeof cardId!=='string'||!/^[a-z][a-z0-9-]{0,63}$/.test(cardId)||typeof parametersFor!=='function')throw fail('CARD_MODEL_POLICY_INVALID');
 const bound=structuredClone(fixed);
 const expected=model=>{const p=parametersFor(model);return parameters(p)?structuredClone(p):null;};
 if(bound&&(typeof bound.model!=='string'||!bound.model||!parameters(bound.parameters)||canonical(bound.parameters)!==canonical(expected(bound.model))))throw fail('CARD_MODEL_POLICY_INVALID');
 function validChoice(c){
  if(!c||typeof c.model!=='string'||!c.model||c.model.length>256||!parameters(c.parameters))return false;
  const p=expected(c.model);if(!p||canonical(c.parameters)!==canonical(p))return false;
  const keys=c.kind==='fixed'?['kind','model','parameters','configHash']:c.kind==='controlled'?['kind','model','parameters','selectionId','selectionRevision','configHash']:[];
  if(!keys.length||Object.keys(c).sort().join(',')!==keys.sort().join(','))return false;
  if(c.kind==='fixed'&&(!bound||c.model!==bound.model))return false;
  if(c.kind==='controlled'&&[c.selectionId,c.selectionRevision].some(x=>typeof x!=='string'||! /^[A-Za-z0-9_-]{1,128}$/.test(x)))return false;
  const {configHash,...value}=c;return configHash===sha(canonical(value));
 }
 return Object.freeze({cardId,validChoice,
  parametersFor(model){const p=expected(model);if(!p)throw fail('CARD_MODEL_UNCONFIGURED');return p;},
  sameParameters(model,value){const p=expected(model);return !!p&&parameters(value)&&canonical(p)===canonical(value);},
  fixedChoice(){if(!bound)throw fail('CARD_MODEL_SELECTION_REQUIRED');return choice({kind:'fixed',...bound});},
  controlledChoice(m){
   const p=expected(m?.model);if(!p)throw fail('CARD_MODEL_UNCONFIGURED');
   const c=choice({kind:'controlled',model:m.model,selectionId:m.selectionId,selectionRevision:m.selectionRevision,parameters:p});
   if(!validChoice(c))throw fail('MODEL_SELECTION_INVALID');return c;
  }
 });
}
