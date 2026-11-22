import {keys,isId} from '../../authoring/contracts.mjs';
import {boundary,check,digest,visible} from '../revisions/contracts.mjs';

// Project a settled local receipt for the member who generated it. Inherited replies
// are accessible through their visible message, never by an unrelated branch request ID.
export async function displayReceipt(desk,args){
  keys(args,['familyId','branchId','targetId','by','id']);
  const {familyId,branchId,targetId,by,id}=args;
  check([familyId,branchId,targetId,id].every(isId)&&['message','request'].includes(by),'DISPLAY_REFERENCE');
  return desk.serial(async()=>{
    // A pending author publication is normally reconciled by load(). This read must
    // never finish that write. The shelf cannot change between these reads under serial().
    check(!(await desk.records.get('shelf'))?.data.publication,'DISPLAY_PUBLICATION_PENDING');
    const {data}=await desk.load();
    const family=Object.hasOwn(data.families,familyId)&&data.families[familyId].f;
    const branch=family&&Object.hasOwn(family.branches,branchId)&&family.branches[branchId];
    check(branch&&boundary(branch).members.some(m=>m.id===targetId&&m.kind==='character'),'DISPLAY_REFERENCE');
    const history=visible(branch,targetId);
    let rid=id;
    if(by==='message'){
      const message=history.find(m=>m.id===id);
      check(message?.speakerId===targetId&&message.generation,'DISPLAY_REFERENCE');
      rid=message.generation.requestId;
      if(!Object.hasOwn(family.requests,rid))return {available:false};
    }
    const r=Object.hasOwn(family.requests,rid)&&family.requests[rid];
    check(r&&r.targetId===targetId&&(by==='message'||r.branchId===branchId),'DISPLAY_REFERENCE');
    if(r.status!=='completed')return {available:false};
    const receipt=await desk.fixture.lookup(r);
    check(receipt&&digest(receipt)===digest(r.receipt),'DISPLAY_RECEIPT_MISMATCH');
    const {hash,...body}=receipt;
    check(hash===digest(body)&&receipt.requestId===r.id&&receipt.requestHash===r.hash&&
      receipt.modelId===r.model.id&&receipt.targetId===targetId&&receipt.status==='completed'&&
      receipt.raw?.kind==='fixture'&&receipt.raw.requestHash===r.hash&&typeof receipt.raw.text==='string','DISPLAY_RECEIPT_MISMATCH');
    return {available:true,kind:r.kind,requestId:r.id,contextHash:r.contextHash,receiptHash:hash,
      modelId:r.model.id,rawText:receipt.raw.text,responseText:receipt.text};
  });
}
