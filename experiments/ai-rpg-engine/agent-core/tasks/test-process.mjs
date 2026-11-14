import {openRuntime} from './composition.mjs';
const rt=await openRuntime(process.argv[2],{offlinePreview:true});
const r=await rt.create({operationId:'process-start'});
const v=await rt.execute(r.id,{operationId:'process-action',revision:0,fault:'unknown'});
process.send({id:r.id,taskId:v.tasks[0].id,calls:v.budget.calls});
setInterval(()=>{},1000);
