import {canonical,keys,isId,isHash,fail} from './contracts.mjs';
export const MODEL_PLUGIN='rpg.model-selector';
const same=(a,b)=>canonical(a)===canonical(b);
export function modelPluginInput(change){
 const {intent,choice}=change;
 if(intent.purpose!=='story'||!choice||choice.entry.selectionId!==intent.choice?.selectionId||!Number.isSafeInteger(intent.expectedModelRevision)||intent.expectedModelRevision<0)fail('MODEL_PLUGIN_SELECTION_INVALID');
 // The reviewed plugin accepts a numeric revision. This host uses its model
 // configuration revision; the full opaque catalog/selection hashes stay in
 // the host-owned immutable change and are validated separately.
 return {selectionId:choice.entry.selectionId,selectionRevision:intent.expectedModelRevision};
}
export function validateModelPluginReceipt(receipt,change,resourceHash){
 keys(receipt,['pluginId','version','artifactSha256','manifestSha256','capability','sessionId','sessionRevision','resourceHash','installationId','epoch','proposal']);
 const expected={kind:'model-selection-request',sessionId:change.intent.sessionId,...modelPluginInput(change)};
 if(receipt.pluginId!==MODEL_PLUGIN||receipt.version!=='2.0.0'||receipt.capability!=='session.model.select'||receipt.sessionId!==change.intent.sessionId||
  receipt.sessionRevision!==change.intent.expectedRevision||!isHash(resourceHash)||receipt.resourceHash!==resourceHash||
  ![receipt.artifactSha256,receipt.manifestSha256].every(isHash)||!isId(receipt.installationId)||!isId(receipt.epoch)||!same(receipt.proposal,expected))fail('MODEL_PLUGIN_RECEIPT_INVALID');
 return receipt;
}
export async function commitPluginModel({service,change,resourceHash,commit,beforeCommit=async()=>{}}){
 const input=modelPluginInput(change),result=await service.invoke({pluginId:MODEL_PLUGIN,sessionId:change.intent.sessionId,capability:'session.model.select',input});
 const {ticket,...receipt}=result;validateModelPluginReceipt(receipt,change,resourceHash);
 await beforeCommit(structuredClone(receipt));
 return service.commitResult(result,()=>commit({...structuredClone(change),pluginReceipt:receipt}));
}
export async function checkPluginCatalog(service,sessionId){
 const result=await service.invoke({pluginId:MODEL_PLUGIN,sessionId,capability:'model.catalog.read',input:{}});
 if(!same(result.proposal,{kind:'model-catalog-request',sessionId}))fail('MODEL_PLUGIN_CATALOG_INVALID');
 await service.validateResult(result);
}
