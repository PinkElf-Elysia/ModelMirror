import {readFile,cp} from 'node:fs/promises';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {need,hash} from '../../contracts.mjs';
import {validateRequest} from '../../actions/transport.mjs';

const pins = {
  'stage-1.json':'4d6bd9bedd93ac142db467aaf16450ffff5bd3470485ac2ea4803de2593aa3ae',
  'ledger.json':'b503956fa2950b1386aab93df1edf9c070be9c3c960cc05c3af8e18a3921bf99',
  'review.json':'beac1ffd5f5f14edebcb47134db1a8d4530c98872baf315608ba04ef4b32eaec',
  'runtime/cultivation/sessions/records/core-8040a2a40b3038a51c547729e14ea0d47820cb65.json':'b730d71ed0aae1520876898f102bf1580a6fd66a3d5d440de1570eac00b742aa',
  'runtime/cultivation/sessions/records/core-d05a885e19aac607ea0f570cc047776656586275.json':'00bbc334c108a76f5eee1f60c6904225aadc487d27ea8818c6682ce7dc3766d9'
};
export async function loadCheckpoint(root,dir){
  const source=join(root,'real-01'), data={};
  for(const [name,digest] of Object.entries(pins)){
    const bytes=await readFile(join(source,name));
    need(createHash('sha256').update(bytes).digest('hex')===digest,'R10_CHECKPOINT_HASH');
    data[name]=JSON.parse(bytes);
  }
  const stage=data['stage-1.json'],review=data['review.json'],task=data['ledger.json'].tasks[stage.packet.taskId];
  const [first,next]=task.stages;
  need(first.attempts.length===1&&next.attempts.length===0&&next.result?.dispatched===false&&next.result.error==='R7_TASK_BUDGET','R10_CHECKPOINT_ATTEMPTS');
  const [assistant,tool]=next.request.messages.slice(-2);
  need(hash(assistant)===hash(stage.result.message)&&tool.tool_call_id===assistant.tool_calls[0].id&&tool.tool_call_id==='call_1217960083848929203','R10_CHECKPOINT_TOOL');
  const output=JSON.parse(tool.content);
  need(output.stateRevision===0&&output.results[0].entries[0].quantity===5&&output.results[1].entries[0].id==='zhou','R10_CHECKPOINT_STATE');
  validateRequest(next.request,stage.packet.request.tools,{maxTools:3});
  for(const part of ['runtime/cultivation/sessions','templates','choices'])
    await cp(join(source,part),join(dir,part),{recursive:true,force:false,errorOnExist:true});
  return {review,task,next,sourceRequest:stage.payload.requestId,requestHash:hash(next.request)};
}
export function resumeContract(base,checkpoint){
  return {...base,validateGrant(g,p){
    if(g?.slot!==1)return base.validateGrant(g,p);
    need(p.attempt===0&&p.session===checkpoint.task.session&&g.taskId===checkpoint.task.id&&p.taskId===g.taskId&&
      p.purpose===checkpoint.task.purpose&&hash(p.request)===(checkpoint.dispatchHash??checkpoint.requestHash)&&p.meta.stage===2,'R10_RESUME_ONLY');
  }};
}
export async function resumeGift(rt,checkpoint,config){
  const {task,next}=checkpoint,run=await rt.run(task.runId),port=rt.port(run),host=port.hosts.get(task.purpose),opId=task.ownerRef.operationId;
  const before=await port.store.read(run.worldId),old=before.operations[opId];
  need(before.pending===opId&&before.stateRevision===0&&old.status==='failed'&&old.error==='R7_TASK_BUDGET'&&old.stages.length===2&&
    old.stages[0].status==='queried'&&old.stages[1].id===next.id&&hash(old.stages[1].request)===checkpoint.requestHash,'R10_RESUME_WORLD');
  await rt.ledger.begin({id:task.id,purpose:task.purpose,session:task.session,runId:task.runId,input:task.input,sourceHash:task.sourceHash,
    source:task.source,ownerRef:task.ownerRef,observation:null,strategy:task.strategy,fault:null},config);
  // Only the verified, never dispatched continuation in this copy becomes ready.
  await port.store.update(run.worldId,row=>{
    const op=row.operations[opId],stage=op.stages[1];
    if(checkpoint.dispatchHash){
      stage.request.messages[0].content=port.store.modelProposalPolicy.instruction+'\n'+stage.request.messages[0].content;
      need(hash(stage.request)===checkpoint.dispatchHash,'R10_GIFT_REQUEST_DIFF');
      stage.id='stage-'+hash([next.id,checkpoint.dispatchHash]).slice(0,40);
    }
    op.status='pending';op.error=null;stage.status='ready';stage.result=null;
  });
  const result=await host.provider.using(task.id,()=>host.advance(run.worldId,opId,false));
  const op=result.operations[opId];
  await rt.ledger.adopted(task.id,op.status,op.receipt??null,op.error??null);
  return rt.view(task.runId);
}
