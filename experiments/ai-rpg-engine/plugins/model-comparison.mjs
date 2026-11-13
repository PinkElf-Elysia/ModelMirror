// Stateless contribution. The trusted host owns requests, quotas and adoption.
export function invoke({capability,session,input={}}){
 if(capability==='ui.comparison-action'&&Object.keys(input).length===0)return {kind:'comparison-action',action:'model-comparison',label:'本轮对比'};
 if(capability==='session.comparison.generate'&&Object.keys(input).join(',')==='batchHash'&&/^[a-f0-9]{64}$/.test(input.batchHash))return {kind:'comparison-request',sessionId:session.id,batchHash:input.batchHash};
 throw Error('INVALID_COMPARISON_REQUEST');
}
