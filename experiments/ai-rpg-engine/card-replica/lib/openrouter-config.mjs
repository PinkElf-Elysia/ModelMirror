import {readFile} from 'node:fs/promises';
import {readFileSync} from 'node:fs';
import {parseEnv} from 'node:util';
import {assemble,defaults,hash,validateParameters} from './assembly.mjs';
const runSettings=JSON.parse(readFileSync(new URL('../resources/RUN_SETTINGS.json',import.meta.url),'utf8'));
export const comparisonParameters=Object.freeze(validateParameters({...defaults,...runSettings}));
export const MODEL='google/gemini-3.8-flash';
export const ENDPOINT='google-ai-studio';
export const CREDENTIAL_SOURCE='C:/Users/21547/Documents/模型浏览器/server/.env';
export const CATALOG='https://openrouter.ai/api/v1/models/google/gemini-3.8-flash/endpoints';
// Only the named secret is used. No env dump, key fragment/hash, authentication probe or copying.
export async function inspectCredential(read=readFile){
 try {
  const value=parseEnv(await read(CREDENTIAL_SOURCE,'utf8')).OPENROUTER_API_KEY?.trim();
  return {source:CREDENTIAL_SOURCE,variable:'OPENROUTER_API_KEY',present:!!value,valuePrinted:false,valueCopied:false,authentication:'not-tested'};
 } catch {throw Error('CREDENTIAL_SOURCE_UNAVAILABLE');}
}
export function buildComparison(characterText,catalog){
 if(typeof characterText!=='string'||!characterText.trim())throw Error('CHARACTER_REQUIRED');
 const model=catalog.data;
 const endpoint=model?.endpoints?.find(e=>e.tag===ENDPOINT);
 if(model?.id!==MODEL||!endpoint||endpoint.status!==0)throw Error('EXACT_ENDPOINT_UNAVAILABLE');
 const names=['temperature','top_p','max_tokens'];
 if(names.some(n=>!endpoint.supported_parameters?.includes(n)))throw Error('REQUIRED_PARAMETERS_UNAVAILABLE');
 // This is a prepared request only. No generation method is exposed by this module.
 const input='开始这一世。';
 const {messages}=assemble({characterText,input,world:'表世界',enabledRuleIds:[]});
 const payload={model:MODEL,messages,temperature:defaults.temperature,top_p:defaults.top_p,max_tokens:comparisonParameters.max_tokens,
  provider:{only:[ENDPOINT],order:[ENDPOINT],allow_fallbacks:false,require_parameters:true},transforms:[],stream:true};
 const profile={schema:'earth-card-openrouter-comparison/1',status:'configured-not-dispatchable',
  model:MODEL,apiBase:'https://openrouter.ai/api/v1',endpoint:ENDPOINT,
  credential:{source:CREDENTIAL_SOURCE,variable:'OPENROUTER_API_KEY',loadPolicy:'server-internal; no copy or client exposure'},
  world:'表世界',characterTextHash:hash(characterText),characterSource:'user verbatim; no normalization',
  discrepancies:['header: 2000年代 / about 2008 / about 18; background: 2010年代; preserved as supplied'],
  proposedPlayerInput:input,playerInputStatus:'same as previous test; prepared only; not dispatched',
  requestedParameters:comparisonParameters,
  wireParameters:{temperature:payload.temperature,top_p:payload.top_p,max_tokens:payload.max_tokens},
  notSent:{top_k:'not advertised by selected endpoint',presence_penalty:'not advertised by selected endpoint',frequency_penalty:'not advertised by selected endpoint',
   thinking_budget:'no verified equivalent for zero; reasoning not configured',context:'UI 8192 is not a truncation policy'},
  reasoningParity:'unresolved; do not claim zero thinking or equality with original site',
  worldbookEnabled:[],automaticRetry:false,allowFallbacks:false,
  ledger:'resources/CALL_LEDGER.json',providerDispatchesThisPreparation:0,
  activeRoute:'existing GPT route unchanged; Gemini controlled-route activation and qualification not run',
  readiness:'configuration only; existing GPT transport must not be used for this model',
  requestHash:hash(JSON.stringify(payload)),catalogModel:model.id,contextLength:endpoint.context_length,
  supportedParameters:endpoint.supported_parameters};
 return {profile,payload};
}
