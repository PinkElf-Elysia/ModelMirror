// Offline subprocess fixture; no credentials and no upstream dispatches.
import {start} from './server.mjs';
const [directory,kind='offline']=process.argv.slice(2);
try{
 const fetcher=async(url,options)=>{
  if(options.method!=='GET'||!url.pathname.endsWith('/models'))throw Error('REAL_DISPATCH_FORBIDDEN');
  return Response.json({models:[{model:'google/gemini-3.8-flash',name:'cached acceptance selection',available:true,selectionId:'296896ac5e719b70190b242f1a662c59b232e2f9e3e482bda2388dbab2868359',selectionRevision:'3335a7e67aacb86e644f4d5cdfcd0d9187e90ae30f92bf4c38ae419e8e87198f',parameters:{temperature:0.7,top_p:0.8,max_tokens:16384}}]});
 };
 const host=await start({port:0,directory,memoryPalace:true,archiveControls:kind==='real'?{'harry-potter':{enabled:true,limit:4,baseURL:'http://127.0.0.1:8000',serviceToken:'synthetic-offline-restoration-only-token',fetcher}}:{}});
 console.log(JSON.stringify({port:host.server.address().port,pid:process.pid}));
 process.on('message',async message=>{if(message==='close'){host.server.close();await host.closed;process.exit(0);}});
}catch(e){console.log(JSON.stringify({error:e.code||e.message}));process.exitCode=1;}
