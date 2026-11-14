// Child fixture only: parent kills this process after a persisted unknown receipt.
import {openRuntime} from './composition.mjs';
const runtime=await openRuntime(process.argv[2],{offlinePreview:true}),host=runtime.get('lighthouse');
const r=await host.create({operationId:'op-child-create',initial:host.adapter.initial(),confirmed:true});
await host.turn(r.id,{operationId:'op-child-turn',revision:r.revision,input:'先查询灯油，再给灯具添油两份'},{fault:'unknown'});
process.send({id:r.id});setInterval(()=>{},1000);
