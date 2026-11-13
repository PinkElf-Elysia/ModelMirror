// Host-owned chat overrides. Native authoring and legacy sessions omit this state.
import {sameFutureChange} from './comparison-future.mjs';
import {canonical,keys,isId,isHash,sha256,fail} from './contracts.mjs';
import {MODEL_PURPOSES,PARAMETER_CHOICE_FORMAT,PARAMETER_SELECTION_FORMAT,validateModelIntent,validateModelChoice,modelChoiceInput,modelChoiceAt} from './model-control.mjs';
export const CHAT_PARAMETERS_FORMAT='modelmirror.rpg.chat-parameters/1';
export const PARAMETER_PLUGIN='rpg.generation-parameters';
export const CHAT_PARAMETER_LIMIT=1000;
export const PARAMETER_DISPATCH_FAILURES=['PARAMETER_AUTHORIZATION_CHANGED','PARAMETER_CONFIGURATION_CHANGED'];
const digest=v=>sha256(canonical(v)),same=(a,b)=>canonical(a)===canonical(b),clone=v=>structuredClone(v),integer=v=>Number.isSafeInteger(v)&&v>=0;
export const emptyChatParameters=()=>({format:CHAT_PARAMETERS_FORMAT,revision:0,current:{story:null,summary:null,memory:null},changes:[]});
export const purposeParameterRevision=(state,purpose)=>state.changes.filter(c=>c.intent.purpose===purpose).length;
export function chatParameterIntent(v){
 canonical(v);keys(v,['sessionId','operationId','expectedRevision','expectedModelRevision','expectedParameterRevision','expectedPurposeRevision','purpose','choice']);
 const {expectedParameterRevision,expectedPurposeRevision,...model}=v;validateModelIntent(model);
 if(!integer(expectedParameterRevision)||!integer(expectedPurposeRevision)||v.choice!==null&&v.choice.format!==PARAMETER_SELECTION_FORMAT)fail('CHAT_PARAMETER_INPUT_INVALID');
 return clone(v);
}
export const parameterConfigurationHash=(intent,base,choice)=>digest({intent,base,choice});
export function requireParameterModel(base,choice,purpose){
 if(!base)fail('PARAMETER_MODEL_REQUIRED');
 if(choice!==null){validateModelChoice(choice);if(choice.format!==PARAMETER_CHOICE_FORMAT||choice.parameterProfile.purpose!==purpose||choice.entry.modelId!==base.entry.modelId||choice.entry.selectionId!==base.entry.selectionId)fail('PARAMETER_MODEL_MISMATCH');}
}
export function requireChatDefault(input){
 if(input.choice===null||input.choice.format!==PARAMETER_SELECTION_FORMAT||Object.values(input.choice.profile.parameters.values).some(v=>v.mode!=='default'))fail('CHAT_MODEL_DEFAULTS_REQUIRED');
}
export function validateChatParameters(s){
 const state=s.generationParameters;if(!state)return;
 keys(state,['format','revision','current','changes']);keys(state.current,MODEL_PURPOSES);
 if(state.format!==CHAT_PARAMETERS_FORMAT||!integer(state.revision)||!Array.isArray(state.changes)||state.revision!==state.changes.length||state.revision>CHAT_PARAMETER_LIMIT)fail('CHAT_PARAMETER_STATE_INVALID');
 const current={story:null,summary:null,memory:null},counts={story:0,summary:0,memory:0};let previous=-1;const ids=new Set();
 for(const [index,c] of state.changes.entries()){
  keys(c,['revision','purposeRevision','intent','intentHash','choice','configurationHash','receipt']);const i=chatParameterIntent(c.intent),receipt=c.receipt;
  if(c.revision!==index+1||c.purposeRevision!==counts[i.purpose]+1||i.sessionId!==s.id||i.expectedParameterRevision!==index||i.expectedPurposeRevision!==counts[i.purpose]||
   !integer(i.expectedRevision)||i.expectedRevision<=previous||i.expectedRevision>=s.revision||i.expectedModelRevision>s.models.revision||
   s.models.changes.some(x=>(x.revision<=i.expectedModelRevision)!==(x.intent.expectedRevision<i.expectedRevision||sameFutureChange(s,x,c)))||ids.has(i.operationId)||c.intentHash!==digest(i))fail('CHAT_PARAMETER_STATE_INVALID');
  const base=modelChoiceAt(s.models,i.expectedModelRevision,i.purpose);requireParameterModel(base,c.choice,i.purpose);
  if(!same(i.choice,c.choice===null?null:modelChoiceInput(c.choice))||c.configurationHash!==parameterConfigurationHash(i,base,c.choice))fail('CHAT_PARAMETER_STATE_INVALID');
  keys(receipt,['pluginId','version','artifactSha256','manifestSha256','capability','sessionId','sessionRevision','resourceHash','installationId','epoch','parameterRevision','proposal']);
  const proposal={kind:'parameter-configuration-request',sessionId:s.id,purpose:i.purpose,parameterRevision:index,configurationHash:c.configurationHash};
  if(receipt.pluginId!==PARAMETER_PLUGIN||receipt.version!=='1.0.0'||receipt.capability!=='session.parameters.configure'||receipt.sessionId!==s.id||receipt.sessionRevision!==i.expectedRevision||receipt.parameterRevision!==index||
   receipt.resourceHash!==s.binding.resourceHash||![receipt.artifactSha256,receipt.manifestSha256].every(isHash)||![receipt.installationId,receipt.epoch].every(isId)||!same(receipt.proposal,proposal))fail('CHAT_PARAMETER_RECEIPT_INVALID');
  counts[i.purpose]++;current[i.purpose]=c.choice;ids.add(i.operationId);previous=i.expectedRevision;
 }
 if(!same(state.current,current))fail('CHAT_PARAMETER_STATE_INVALID');
 // A chat model change cannot bypass the separate parameter permission.
 for(const c of s.models.changes)requireChatDefault(c.intent);
}
function at(s,revision){
 if(!s.generationParameters||!integer(revision)||revision>s.generationParameters.revision)fail('CHAT_PARAMETER_SNAPSHOT_INVALID');
 const current={story:null,summary:null,memory:null};for(const c of s.generationParameters.changes.slice(0,revision))current[c.intent.purpose]=c.choice;return current;
}
export function parameterChoices(s,snapshot,modelRevision=s.models.revision){
 const overrides=at(s,snapshot.revision),choices={};
 for(const purpose of MODEL_PURPOSES){
  const base=modelChoiceAt(s.models,modelRevision,purpose),override=overrides[purpose];
  if(snapshot.authorization.enabled&&override){requireParameterModel(base,override,purpose);choices[purpose]=override;}else choices[purpose]=base;
 }
 return choices;
}
export function freezeChatParameters(s,authorization){
 keys(authorization,['enabled','revision']);if(typeof authorization.enabled!=='boolean'||!isHash(authorization.revision))fail('CHAT_PARAMETER_AUTHORIZATION_INVALID');
 const snapshot={revision:s.generationParameters.revision,authorization:clone(authorization)};
 return {...snapshot,choiceHashes:Object.fromEntries(Object.entries(parameterChoices(s,snapshot)).map(([p,c])=>[p,c?digest(c):null]))};
}
export function validateRunParameters(s,run){
 if(!s.generationParameters){if(Object.hasOwn(run,'parameters')||Object.hasOwn(run.intent,'expectedParameterAuthorizationRevision'))fail('CHAT_PARAMETER_SNAPSHOT_INVALID');return;}
 const snap=run.parameters;keys(snap,['revision','authorization','choiceHashes']);keys(snap.authorization,['enabled','revision']);keys(snap.choiceHashes,MODEL_PURPOSES);
 if(run.intent.expectedParameterAuthorizationRevision!==snap.authorization.revision||typeof snap.authorization.enabled!=='boolean'||!isHash(snap.authorization.revision)||s.generationParameters.changes.some(c=>(c.revision<=snap.revision)!==(c.intent.expectedRevision<run.intent.expectedRevision)))fail('CHAT_PARAMETER_SNAPSHOT_INVALID');
 const choices=parameterChoices(s,snap,run.intent.expectedModelRevision);
 if(!same(snap.choiceHashes,Object.fromEntries(Object.entries(choices).map(([p,c])=>[p,c?digest(c):null]))))fail('CHAT_PARAMETER_SNAPSHOT_INVALID');
}
export function taskModelChoice(s,modelRevision,purpose,operationId){
 const compared=s.runs?.find(r=>r.operationId===operationId)?.comparison;if(purpose==='story'&&compared)return clone(compared.choice);
 if(!s.generationParameters)return modelChoiceAt(s.models,modelRevision,purpose);
 const run=s.runs.find(r=>r.operationId===operationId||r.plan.stages.some(t=>t.operationId===operationId));
 if(!run||run.intent.expectedModelRevision!==modelRevision)fail('CHAT_PARAMETER_SNAPSHOT_INVALID');
 validateRunParameters(s,run);return parameterChoices(s,run.parameters,modelRevision)[purpose];
}
