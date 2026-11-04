import {randomBytes} from 'node:crypto';
import {buildCGPlan,replayCGPlan,CG_LIMITS} from './cg.mjs';
import {canonical,sha256,keys,isId,fail,cgIssues} from './contracts.mjs';

export const CG_REPLAY_LIMITS=Object.freeze({ttl:600000,records:128,bytes:8*1024*1024});
/** Offline authoring only. Expiry leaves a tombstone: an operation never redraws. */
export function createCGReplayCache({now=Date.now,draw=()=>randomBytes(6).readUIntBE(0,6)/2**48}={}){
  const records=new Map();let bytes=0;
  const prune=()=>{for(const record of records.values())if(record.value&&record.expiresAt<=now()){bytes-=record.bytes;record.value=null;record.error='AUTHORING_CG_REPLAY_EXPIRED';}};
  return {prepare({current,document,reply,request}){
    keys(request,['action','operationId','previousOperationId','user','device'],'/cg');
    if(!['create','replay'].includes(request.action)||!isId(request.operationId)||request.previousOperationId!==null&&!isId(request.previousOperationId)||!['desktop','mobile'].includes(request.device))fail('AUTHORING_CG_REPLAY_REQUEST');
    if(typeof request.user!=='string'||!request.user.isWellFormed()||Buffer.byteLength(request.user)+Buffer.byteLength(reply)>CG_LIMITS.sourceBytes)fail('AUTHORING_CG_SOURCE_SIZE');
    const key=current.id+'/'+request.operationId;
    const context=sha256(canonical({workId:current.id,revision:current.revision,document,assets:current.assets,device:request.device}));
    const fingerprint=sha256(canonical({context,reply,user:request.user,previousOperationId:request.previousOperationId}));
    prune();const prior=records.get(key);
    if(prior){
      if(prior.fingerprint!==fingerprint)fail('AUTHORING_CG_REPLAY_CONFLICT');
      if(prior.error)fail(prior.error);
      replayCGPlan(prior.value.plan,{binding:prior.value.plan.binding,sha256:prior.value.plan.sha256});
      return structuredClone(prior.value);
    }
    if(request.action==='replay')fail('AUTHORING_CG_REPLAY_NOT_FOUND');
    if(records.size>=CG_REPLAY_LIMITS.records)fail('AUTHORING_CG_REPLAY_CAPACITY');
    const record={fingerprint,context,error:null,value:null,expiresAt:now()+CG_REPLAY_LIMITS.ttl,bytes:0};records.set(key,record);
    try{
      let parent=null;
      if(request.previousOperationId!==null){
        parent=records.get(current.id+'/'+request.previousOperationId);
        if(!parent?.value||parent.error||parent.context!==context)fail('AUTHORING_CG_REPLAY_PARENT');
      }
      const cg=document.enhancements.cg;
      const issue=cgIssues(cg,current.assets)[0];if(issue)fail(issue.code,issue.path);
      const input={workId:current.id,revision:current.revision,versionId:'preview-'+context,operationId:request.operationId,
        cg,assets:current.assets,turn:{id:'turn-'+request.operationId,complete:true,user:request.user,assistant:reply},
        capacity:{scanUnits:CG_LIMITS.scanUnits,images:CG_LIMITS.images,bytes:CG_LIMITS.bytes},
        draws:cg.entries.filter(entry=>entry.enabled&&entry.mode==='weighted').map(entry=>({id:entry.id,value:draw()}))};
      // A host sample is supplied once for each enabled weighted rule. The core
      // records it only for matches and measures capacity on the actual choices.
      // Never preflight capacity with substitute choices: their byte sizes differ.
      const plan=buildCGPlan(input);
      const defaultAlias=document.settings.media[request.device]||document.settings.media.desktop;
      const initial=defaultAlias?current.assets.find(asset=>asset.alias===defaultAlias):null;
      if(defaultAlias&&(!initial||!initial.mediaType.startsWith('image/')))fail('AUTHORING_ASSET_MISSING');
      const background=plan.background.action==='set'?plan.background.image:parent?.value.background??initial??null;
      const carried=[...plan.chat,...(background?[background]:[])],unique=new Map(carried.map(image=>[image.sha256,image.size]));
      if(carried.length>CG_LIMITS.images||[...unique.values()].reduce((a,b)=>a+b,0)>CG_LIMITS.bytes)fail('AUTHORING_CG_OUTPUT_CAPACITY');
      const value={plan,background,backgroundSource:plan.background.action==='set'?'selected':parent?.value.background?'inherited':initial?'default':'none',previousOperationId:request.previousOperationId,device:request.device,expiresAt:record.expiresAt};
      record.bytes=Buffer.byteLength(canonical(value));
      if(bytes+record.bytes>CG_REPLAY_LIMITS.bytes)fail('AUTHORING_CG_REPLAY_CAPACITY');
      record.value=value;bytes+=record.bytes;return structuredClone(value);
    }catch(error){record.error=error.code||'AUTHORING_CG_REPLAY_FAILED';throw error;}
  }};
}
