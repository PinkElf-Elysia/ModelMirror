import {openRuntime} from './composition.mjs';
const rt=await openRuntime(process.argv[2],{offlinePreview:true}),h=rt.host;
const r=await h.create({operationId:'process-create'});
await h.worldAction(r.id,{operationId:'process-use',revision:0});await h.narrateWorld(r.id);await h.deliver(r.id);
await h.respond(r.id,{operationId:'actor-unknown',actorId:'witness',input:'刚才发生了什么？',fault:'unknown'});
process.send({id:r.id});setInterval(()=>{},1000);
