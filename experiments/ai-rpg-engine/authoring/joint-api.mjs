import {keys,fail} from './contracts.mjs';
// The HTTP server owns same-origin, body size, content type and safe errors.
// This route facade accepts intents only, never trusted messages or transport URLs.
export function createJointApi({host,settings}){
 return Object.freeze({async handle(method,route,body){
  if(method==='GET'){
   const found=/^(sessions|settings)\/(session-[a-f0-9]{64})$/.exec(route);
   if(found)return found[1]==='settings'?settings.read(found[2]):host.read(found[2]);
  }else if(method==='POST'){
   if(route==='settings/change')return settings.change(body);
   if(route==='settings/reconcile')return settings.reconcile(body);
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
