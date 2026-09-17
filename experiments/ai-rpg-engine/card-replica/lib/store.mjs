import {mkdir,readFile,writeFile,rename,readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {assemble,hash,validateParameters} from './assembly.mjs';
const idPattern=/^[a-zA-Z0-9-]{1,80}$/;
export class SessionStore {
 constructor(directory,generate,realGenerate=null){this.directory=directory;this.generate=generate;this.realGenerate=realGenerate;this.running=new Map();}
 async init(){await mkdir(this.directory,{recursive:true});}
 path(id){if(!idPattern.test(id))throw Object.assign(Error('Invalid id'),{status:400});return join(this.directory,id+'.json');}
 async read(id){try{return JSON.parse(await readFile(this.path(id),'utf8'));}catch(e){if(e.code==='ENOENT')throw Object.assign(Error('会话不存在'),{status:404});throw e;}}
 async write(session){const dest=this.path(session.id),tmp=dest+'.'+randomUUID()+'.tmp';await writeFile(tmp,JSON.stringify(session,null,2),'utf8');await rename(tmp,dest);}
 async list(){return (await Promise.all((await readdir(this.directory)).filter(f=>/^[a-zA-Z0-9-]+\.json$/.test(f)).map(f=>this.read(f.slice(0,-5))))).map(s=>({id:s.id,name:s.name,created:s.created,turns:s.history.length/2,mode:s.mode})).reverse();}
 async create({characterText,world,params,mode}){
  if(mode!=='offline'&&(mode!=='real'||!this.realGenerate))throw Object.assign(Error('真实调用适配器未在此宿主启用'),{status:403});
  if(typeof characterText!=='string'||!characterText.trim()||characterText.length>200000||!['表世界','里世界'].includes(world))throw Object.assign(Error('角色资料或世界类型无效'),{status:400});
  const s={id:randomUUID(),name:(characterText.match(/姓名[：:]([^\n]+)/)?.[1]||'未命名角色').slice(0,100),created:new Date().toISOString(),characterText,world,params:validateParameters(params),mode,revision:0,history:[],turns:[],requests:{}};
  await this.write(s);return s;
 }
 async send(id,{input,requestId,revision}){
  if(typeof input!=='string'||!input.trim()||input.length>20000||!idPattern.test(requestId||'')||!Number.isInteger(revision))throw Object.assign(Error('消息或请求标识无效'),{status:400});
  // Set the lock before awaiting any disk access: simultaneous requests cannot both dispatch.
  if(this.running.has(id))throw Object.assign(Error('该会话仍在处理一条消息'),{status:409});
  const controller=new AbortController();this.running.set(id,controller);
  try{
   const s=await this.read(id);const prior=s.requests[requestId];
   if(prior){if(prior.inputHash!==hash(input))throw Object.assign(Error('同一请求标识不能用于不同输入'),{status:409});return s;}
   if(Object.values(s.requests).some(r=>r.status==='pending'))throw Object.assign(Error('存在未确认请求，请先核对处理状态'),{status:409});
   if(s.revision!==revision)throw Object.assign(Error('会话已更新，请恢复最新记录再发送'),{status:409});
   if(s.mode!=='offline'&&(s.mode!=='real'||!this.realGenerate))throw Object.assign(Error('真实调用关闭'),{status:403});
   const a=assemble({characterText:s.characterText,input,history:s.history,world:s.world});
   s.requests[requestId]={status:'pending',inputHash:hash(input),assemblyHash:hash(JSON.stringify(a.messages)),at:new Date().toISOString()};
   await this.write(s);
   try{
    const raw=await (s.mode==='real'?this.realGenerate:this.generate)({messages:a.messages,params:s.params,signal:controller.signal,sessionId:id,requestId});
    if(controller.signal.aborted){s.requests[requestId].status='cancelled';}
    else{if(typeof raw!=='string'||!raw.trim())throw Error('Empty response');s.history.push({role:'user',content:a.current},{role:'assistant',content:raw});s.turns.push({requestId,input,raw,rawHash:hash(raw),at:new Date().toISOString()});s.requests[requestId].status='complete';s.revision++;}
   }catch(e){s.requests[requestId].status=controller.signal.aborted?'cancelled':'failed';s.requests[requestId].error=controller.signal.aborted?'已取消':'生成未完成；不会自动重试';}
   await this.write(s);return s;
  }finally{this.running.delete(id);}
 }
 cancel(id){const c=this.running.get(id);if(c)c.abort();return {cancelRequested:!!c};}
}
export function projection(s){return {id:s.id,name:s.name,characterText:s.characterText,world:s.world,params:s.params,mode:s.mode,revision:s.revision,turns:s.turns,requests:Object.fromEntries(Object.entries(s.requests).map(([id,r])=>[id,{status:r.status,error:r.error}]))};}
export async function offlineGenerate({signal}){
 await new Promise(resolve=>{const t=setTimeout(resolve,3000);signal.addEventListener('abort',()=>{clearTimeout(t);resolve();},{once:true});});
 return '<details open><summary>当前情况</summary><p>📅 本地离线预览</p><p>🌍 展示样例，不是模型生成</p></details><p>这段文字仅用于核对生成中的按钮、折叠面板、保存和恢复。它不代表这张卡片的角色扮演效果。</p><details><summary>保存记录</summary><p>本轮输入与这份示例已经保存在本地会话中。</p></details>';
}
