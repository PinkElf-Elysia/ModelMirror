import {parametersFor,sameParameters} from './model-parameters.mjs';
import {readFile} from 'node:fs/promises';
import {earthRuntime,setupHash,runtimeStatus} from './runtime-binding.mjs';
import {models} from './provider.mjs';
import {canonical,sha,fail} from '../plugins/catalog.mjs';

const root=new URL('../',import.meta.url);
const contentPaths=['card-replica/lib/assembly.mjs','card-replica/resources/prompts.json','card-replica/resources/MANIFEST.json','card-replica/src/data.ts'];
const extra=['studio/model-parameters.mjs','studio/model-runtime.mjs','studio/model-session-store.mjs','studio/model-branch-archive.mjs','studio/controlled-provider.mjs','studio/provider.mjs','studio/models.json','studio/versioned-store.mjs','studio/branch-archive.mjs','card-replica/lib/store.mjs',...contentPaths];
const artifacts=async()=>Object.fromEntries(await Promise.all(extra.map(async p=>[p,sha(await readFile(new URL(p,root)))])));
const frozen=await artifacts();
const descriptor={format:2,cardId:'earth',content:{author:earthRuntime.descriptor.author,cardArtifacts:Object.fromEntries(contentPaths.map(p=>[p,frozen[p]])),worldbook:earthRuntime.descriptor.worldbook},hostArtifacts:frozen,historyPolicy:'complete-raw-history',modelPolicy:'core-persisted-selection-v1'};
// Only this previously verified v1 runtime may continue. No wildcard or frontend opt-in.
export const LEGACY_RUNTIME_HASH='05d0d34f2604bbcd547ddad89e183742c55bc769bd99cb0be81fbe34688895ca';
export const modelRuntime={descriptor,hash:sha(canonical(descriptor)),async verify(){if(canonical(await artifacts())!==canonical(frozen))throw fail('RUNTIME_FILES_CHANGED_RESTART_REQUIRED');}};
export const bindModelRuntime=s=>({format:2,hash:modelRuntime.hash,descriptor:structuredClone(descriptor),setupHash:setupHash(s)});
export function modelRuntimeStatus(s){
 if(s.runtime?.format!==2){const status=runtimeStatus(earthRuntime,s);return status.compatible&&s.runtime.hash!==LEGACY_RUNTIME_HASH?{compatible:false,code:'RUNTIME_VERSION_MISMATCH'}:status;}
 if(s.runtime.hash!==sha(canonical(s.runtime.descriptor))||s.runtime.setupHash!==setupHash(s))return {compatible:false,code:'RUNTIME_BINDING_INVALID'};
 return s.runtime.hash===modelRuntime.hash?{compatible:true,code:null,hash:s.runtime.hash}:{compatible:false,code:'RUNTIME_VERSION_MISMATCH'};
}
export function choice(value){const config=structuredClone(value);return {...config,configHash:sha(canonical(config))};}
export const fixedChoice=()=>choice({kind:'fixed',model:models.earth.model,parameters:models.earth.parameters});
export const controlledChoice=m=>choice({kind:'controlled',model:m.model,selectionId:m.selectionId,selectionRevision:m.selectionRevision,parameters:parametersFor(m.model)});
export function validChoice(c){
 if(!c||typeof c.model!=='string'||!c.model||c.model.length>256||!sameParameters(c.model,c.parameters))return false;
 const keys=c.kind==='fixed'?['kind','model','parameters','configHash']:c.kind==='controlled'?['kind','model','parameters','selectionId','selectionRevision','configHash']:[];
 if(!keys.length||Object.keys(c).sort().join(',')!==keys.sort().join(','))return false;
 if(c.kind==='fixed'&&c.model!==models.earth.model)return false;
 if(c.kind==='controlled'&&[c.selectionId,c.selectionRevision].some(x=>typeof x!=='string'||! /^[A-Za-z0-9_-]{1,128}$/.test(x)))return false;
 const {configHash,...value}=c;return configHash===sha(canonical(value));
}
export const initialModelState=current=>({revision:0,current:structuredClone(current),operations:{},timeline:[{revision:0,choice:structuredClone(current)}]});
export function requireModelState(s){
 const m=s.modelState;
 if(!m||!Number.isSafeInteger(m.revision)||m.revision<0||!validChoice(m.current)||!m.operations||typeof m.operations!=='object'||Array.isArray(m.operations)||!Array.isArray(m.timeline)||m.timeline.length!==m.revision+1||m.timeline.some((t,i)=>t.revision!==i||!validChoice(t.choice))||canonical(m.timeline.at(-1).choice)!==canonical(m.current))throw fail('MODEL_STATE_INVALID');
 const entries=Object.entries(m.operations);
 if(entries.length!==m.revision||new Set(entries.map(([,o])=>o?.revision)).size!==m.revision||entries.some(([id,o])=>! /^[a-zA-Z0-9][a-zA-Z0-9-]{0,79}$/.test(id)||!o||! /^[a-f0-9]{64}$/.test(o.hash)||!Number.isSafeInteger(o.revision)||o.revision<1||o.revision>m.revision||canonical(o.selection)!==canonical(m.timeline[o.revision].choice)))throw fail('MODEL_STATE_INVALID');
 for(const turn of s.turns){
  const original=s.requests[turn.requestId]||s.provenance?.requestOrigins?.find(o=>o.requestId===turn.requestId)?.record;
  if(!validChoice(turn.model?.selection)||!original||original.status!=='complete'||canonical(original.selection)!==canonical(turn.model.selection)||original.selectionRevision!==turn.model.selectionRevision)throw fail('MODEL_TURN_BINDING_INVALID');
 }
 return m;
}
