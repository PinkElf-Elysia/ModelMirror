import {startServer} from '../server.mjs';
import {openRuntime} from './composition.mjs';
import {fileURLToPath} from 'node:url';import {resolve} from 'node:path';
export function startContextServer({directory,port=18851,offlinePreview=false}={}){
 return startServer({directory:directory||fileURLToPath(new URL('../../.rpg03-work/context-r4',import.meta.url)),port,offlinePreview,runtimeFactory:openRuntime,
 publicFiles:{'/':new URL('./public/index.html',import.meta.url),'/app.mjs':new URL('./public/app.mjs',import.meta.url),'/style.css':new URL('../public/style.css',import.meta.url)},
 viewProjection:async(view,host)=>{const r=await host.store.read(view.id);for(const [id,o] of Object.entries(view.operations)){if(r.operations[id].context?.contextTrace)o.contextTrace=r.operations[id].context.contextTrace;}return view;}});
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const i=process.argv.indexOf('--port'),d=process.argv.indexOf('--data-dir'),app=await startContextServer({offlinePreview:process.argv.includes('--offline-preview'),port:i>=0?Number(process.argv[i+1]):18851,directory:d>=0?process.argv[d+1]:undefined});
 console.log('RPG Context R4 OFFLINE preview: '+app.url);
 for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await app.close();process.exit(0);});
}
