import {keys,canonical,fail} from './contracts.mjs';
import {chatParameterIntent} from './chat-parameters.mjs';
// The HTTP server owns same-origin, body size, content type and safe errors.
// This route facade accepts intents only, never trusted messages or transport URLs.
export function createJointApi({host,settings}){
 async function parameterRead(sessionId,purpose){
  try{return await host.parameterCatalog(sessionId,purpose);}
  catch(error){if(/^PLUGIN_[A-Z_]+$/.test(error?.code||''))fail(error.code);throw error;}
 }
 return Object.freeze({async handle(method,route,body){
  if(method==='GET'){
   const parameters=/^parameters\/(session-[a-f0-9]{64})\/(story|summary|memory)$/.exec(route);
   if(parameters)return parameterRead(parameters[1],parameters[2]);
   const found=/^(sessions|settings)\/(session-[a-f0-9]{64})$/.exec(route);
   if(found)return found[1]==='settings'?settings.read(found[2]):host.read(found[2]);
  }else if(method==='POST'){
   if(route==='settings/change')return settings.change(body);
   if(route==='settings/reconcile')return settings.reconcile(body);
   if(route==='parameters/reconcile'){
    if(!host.readiness.chatParameters)fail('CHAT_PARAMETER_HOST_REQUIRED');
    const input=chatParameterIntent(body);await settings.assertOperationAvailable(input.operationId);await host.reconcileStorage();
    const view=await host.read(input.sessionId),change=view.session.generationParameters.changes.find(c=>c.intent.operationId===input.operationId);
    if(change&&canonical(change.intent)!==canonical(input))fail('OFFLINE_OPERATION_COLLISION');
    if(!change)await host.assertOperationAvailable(input.sessionId,input.operationId);
    return {sessionId:input.sessionId,intent:input,revision:view.session.revision,change:change??null,state:change?'applied':view.busy?'unknown':'not_applied'};
   }
   if(route==='parameters/configure')return settings.withOperation(body.operationId,async()=>({sessionId:body.sessionId,...await host.configureParameters(body)}));
   if(['estimate','send','update','memory/edit','branch','requirements'].includes(route)){
    await settings.assertSettled();await settings.assertOperationAvailable(body.operationId);
   }
   if(route==='requirements')return host.configureRequirements(body);
   if(route==='branch')return host.fork(body);
   if(route==='branch/reconcile')return host.reconcileBranch(body);
   if(route==='estimate')return host.estimate(body);
   if(route==='send')return host.send(body);
   if(route==='update')return host.update(body);
   if(route==='recover')return host.recover(body);
   if(route==='memory/edit')return host.editMemory(body);
   if(route==='reconcile'){keys(body,['sessionId']);await host.reconcileStorage();return host.read(body.sessionId);}
  }
  fail('AUTHORING_ROUTE_NOT_FOUND');
 }});
}
