import {checkPanelTemplate} from './variable-template-syntax.mjs';
import Handlebars from 'handlebars';
import {Worker, isMainThread, parentPort, workerData} from 'node:worker_threads';
import {fail} from './contracts.mjs';
import {checkPanelData, PANEL_LIMITS} from './variable-panel.mjs';

export function renderPanelTemplate(template,data) {
  checkPanelData(data);
  const ast=checkPanelTemplate(template),h=Handlebars.create();
  for(const [key,fn] of Object.entries({eq:(a,b)=>a===b,ne:(a,b)=>a!==b,gt:(a,b)=>a>b,gte:(a,b)=>a>=b,lt:(a,b)=>a<b,lte:(a,b)=>a<=b,not:a=>!a,and:(...args)=>args.slice(0,-1).every(Boolean),or:(...args)=>args.slice(0,-1).some(Boolean)}))h.registerHelper(key,fn);
  let output;
  try { output=h.compile(ast) (data,{allowProtoMethodsByDefault:false,allowProtoPropertiesByDefault:false,allowCallsToHelperMissing:false}); }
  catch { fail('PANEL_TEMPLATE_RENDER'); }
  if(Buffer.byteLength(output)>512*1024)fail('PANEL_TEMPLATE_CAPACITY');
  return output;
}
let active=0;
export function renderPanelTemplateBounded(template,data,{timeoutMs=1500}={}) {
  if(!Number.isInteger(timeoutMs)||timeoutMs<1||timeoutMs>5000)fail('PANEL_TEMPLATE_TIMEOUT');
  checkPanelData(data);if(typeof template!=='string'||[...template].length>PANEL_LIMITS.templateChars)fail('PANEL_TEMPLATE_CAPACITY');
  if(active>=4)fail('PANEL_TEMPLATE_BUSY');active++;
  return new Promise((resolve,reject)=>{
    let worker;
    try { worker=new Worker(new URL(import.meta.url),{workerData:{kind:'variable-template',template,data},resourceLimits:{maxOldGenerationSizeMb:48,stackSizeMb:4}}); }
    catch { active--;try{fail('PANEL_TEMPLATE_RENDER');}catch(e){reject(e);}return; }
    let settled=false;
    const finish=(code,output)=>{if(settled)return;settled=true;active--;clearTimeout(timer);void worker.terminate();if(code){try{fail(code);}catch(e){reject(e);}}else resolve(output);};
    const timer=setTimeout(()=>finish('PANEL_TEMPLATE_TIMEOUT'),timeoutMs);
    worker.once('message',r=>finish(r.error,r.output));worker.once('error',()=>finish('PANEL_TEMPLATE_CAPACITY'));worker.once('exit',()=>finish('PANEL_TEMPLATE_RENDER'));
  });
}
if(!isMainThread && workerData?.kind==='variable-template') {
  try {parentPort.postMessage({output:renderPanelTemplate(workerData.template,workerData.data)});}
  catch(e){parentPort.postMessage({error:e.code||'PANEL_TEMPLATE_RENDER'});}
}
