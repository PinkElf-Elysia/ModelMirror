// Reviewed, stateless proposal adapter. The host owns parameter validation,
// current-model binding, storage and dispatch; this module receives only hashes.
const exact=(v,keys)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===keys.slice().sort().join(',');
const purpose=v=>['story','summary','memory'].includes(v);
const revision=v=>Number.isSafeInteger(v)&&v>=0;
export function validParameterChange(input) {
  return exact(input,['purpose','parameterRevision','configurationHash'])&&purpose(input.purpose)&&
    revision(input.parameterRevision)&&typeof input.configurationHash==='string'&&/^[a-f0-9]{64}$/.test(input.configurationHash);
}
export function invoke({capability,session,input={}}) {
  if(capability==='ui.parameters-action'&&exact(input,[]))return {kind:'parameters-action',action:'generation-parameters',label:'生成参数'};
  if(capability==='model.parameters.read'&&exact(input,['purpose'])&&purpose(input.purpose))return {kind:'parameter-capability-request',sessionId:session.id,purpose:input.purpose};
  if(capability==='session.parameters.configure'&&validParameterChange(input)&&input.parameterRevision===session.parameterRevision)
    return {kind:'parameter-configuration-request',sessionId:session.id,...input};
  throw Error('INVALID_PARAMETER_REQUEST');
}
