const $=id=>document.getElementById(id);
const el=(tag,content)=>{const n=document.createElement(tag);n.textContent=content;return n;};
let boot,view,epoch=0,timer,busy=false;
const parts=new Map();
async function api(route,body){
  const r=await fetch('/api/'+route,body?{method:'POST',headers:{'content-type':'application/json','x-csrf-token':boot.csrf},body:JSON.stringify(body)}:{});
  const v=await r.json();if(!r.ok)throw Error(v.error);return v;
}
function run(fn){return async()=>{if(busy)return;busy=true;$('error').textContent='';try{await fn();}catch(e){$('error').textContent=e.message;}finally{busy=false;}};}
function button(label,fn){const n=el('button',label);n.onclick=run(fn);return n;}
function query(params){return new URLSearchParams(params).toString();}
async function library(){
  ++epoch;clearTimeout(timer);view=null;boot=await api('start');$('back').href=boot.back;document.querySelector('header strong').textContent='模镜 · '+boot.executionLabel;$('desk').hidden=true;$('library').hidden=false;
  $('desks').replaceChildren(...boot.desks.map(d=>button(d.title+' · '+d.id.slice(-6),()=>open(d.id))));
  $('sources').replaceChildren(...boot.sources.flatMap(f=>f.branches.map(b=>{
    const n=button((f.source.kind==='group'?'群聊':'单聊')+' · '+b.title,async()=>{
      const created=await api('create',{operationId:crypto.randomUUID(),familyId:f.id,branchId:b.id,expectedRevision:f.revision});
      await open(created.desk);
    });n.disabled=f.pending;return n;
  })));
}
async function open(key,actor){
  const turn=++epoch;clearTimeout(timer);
  const v=await api('view?'+query({desk:key,...(actor?{actor}:{})}));
  if(turn!==epoch)return;
  view=v;history.replaceState(null,'','/live?'+query({desk:key,actor:v.actor}));
  $('library').hidden=true;$('desk').hidden=false;
  $('actor').replaceChildren(...v.members.map(m=>new Option(v.members.filter(n=>n.name===m.name).length>1?m.name+' · '+m.id.slice(-6):m.name,m.id)));$('actor').value=v.actor;
  $('auto').checked=v.clock;$('budget').textContent='提案上限 '+v.limit.calls+' 次 · US$'+(v.limit.microUsd/1e6).toFixed(2)+'。任务共余 '+(v.budget.calls-v.budget.usedCalls)+' 次。'+(v.autoError?' · '+v.autoError:'');
  $('propose').disabled=v.busy||!v.limit.calls||!v.canPropose;$('send').disabled=v.busy;
  $('input').value=sessionStorage.getItem('draft:'+key+':'+v.actor)??'';
  $('history').replaceChildren(...v.history.map(m=>{const a=el('article','');a.append(el('strong',m.speakerName),el('p',m.content));return a;}));
  $('responses').replaceChildren();$('reviews').replaceChildren();$('notes').replaceChildren(...v.notes.map(n=>el('p',n.content)));
  for(const r of v.requests){
    const a=el('article',''),label=({prepared:'准备中',dispatching:'正在生成',completed:'已完成',failed:'未完成',unknown:'结果未知','invalid-proposal':'提案来源校验失败'})[r.status];
    a.append(el('strong',label));
    if(r.reviewState==='pending'){
      const content=el('textarea','');const draftKey='review:'+v.id+':'+v.actor+':'+r.id;content.value=sessionStorage.getItem(draftKey)??r.review.content;
      content.oninput=()=>sessionStorage.setItem(draftKey,content.value);content.setAttribute('aria-label','提案正文');
      a.append(content,el('small','来源：'+r.review.refs.map(ref=>v.history.find(m=>m.id===ref.id)?.content??'来源已变化').join(' / ')),
        button('采纳',()=>act('adopt',{requestId:r.id,content:content.value})),button('拒绝',()=>act('reject',{requestId:r.id})));
    }else if(r.reviewState)a.append(el('p',r.reviewState==='adopted'?'已采纳':'已拒绝'));
    if(r.candidate)a.append(el('p',r.candidate),button('采用回复',()=>act('select',{requestId:r.id})));
    if(r.kind==='chat'&&['prepared','dispatching','unknown','failed'].includes(r.status)){
      const out=el('pre','');out.id=r.id;a.append(out);
      if(['prepared','dispatching'].includes(r.status))a.append(button('中止本次回复',async()=>{
        await api('stop',{desk:key,actor:v.actor,request:r.id});await open(key,v.actor);
      }));
      a.append(el('small','请求附件 · 尚未进入对话'));
      await progress(r,out,v,turn);
    }
    if(r.kind==='proposal'&&r.status==='prepared')a.append(button('关闭未派发的提案',async()=>{await api('stop',{desk:key,actor:v.actor,request:r.id});await open(key,v.actor);}));
    if(r.hostStopped)a.append(el('p','宿主已中止读取。供应商用量仍以原回执为准。'));
    else if(r.stopIntent||r.stop)a.append(el('p','已请求中止，正在核对原请求。'));
    if(r.receipt){const detail=el('details','');detail.append(el('summary','查看回执'),el('pre',JSON.stringify(r.receipt,null,2)));a.append(detail);}
    if(r.status==='unknown'||r.status==='dispatching')a.append(button('核对原请求',async()=>{
      await api('recover',{desk:key,actor:v.actor,request:r.id});await open(key,v.actor);
    }));
    if(turn!==epoch)return;
    $(r.kind==='chat'?'responses':'reviews').append(a);
  }
  if(v.requests.some(r=>['prepared','dispatching'].includes(r.status)))timer=setTimeout(()=>poll(v,turn),450);
}
async function progress(r,out,v,turn){
  const p=parts.get(r.id)??{cursor:0,text:''};
  try{const next=await api('progress?'+query({desk:v.id,actor:v.actor,request:r.id,cursor:p.cursor}));
    if(turn!==epoch)return;p.text+=next.text;p.cursor=next.next;parts.set(r.id,p);out.textContent=p.text;
  }catch{out.textContent=p.text||'等待可核对的输出';}
}
async function poll(v,turn){
  if(turn!==epoch)return;
  try{
    const fresh=await api('view?'+query({desk:v.id,actor:v.actor}));
    if(turn!==epoch)return;
    if(fresh.revision!==v.revision)return open(v.id,v.actor);
    for(const r of v.requests.filter(r=>r.kind==='chat'&&r.status==='dispatching')){
      const out=document.getElementById(r.id);if(out)await progress(r,out,v,turn);
    }
  }catch(e){$('error').textContent=e.message;}
  if(turn===epoch)timer=setTimeout(()=>poll(v,turn),450);
}
async function act(action,payload){const {id,actor,revision}=view;
  await api('action',{desk:id,actor,operationId:crypto.randomUUID(),expectedRevision:revision,action,payload});
  if(['adopt','reject'].includes(action))sessionStorage.removeItem('review:'+id+':'+actor+':'+payload.requestId);
  if(action==='send')sessionStorage.removeItem('draft:'+id+':'+actor);await open(id,actor);
}
$('input').oninput=()=>sessionStorage.setItem('draft:'+view.id+':'+view.actor,$('input').value);
$('composer').onsubmit=e=>{e.preventDefault();run(()=>act('send',{input:$('input').value}))();};
$('auto').onchange=run(()=>act('auto',{enabled:$('auto').checked}));
$('propose').onclick=run(()=>act('propose',{}));$('actor').onchange=run(()=>open(view.id,$('actor').value));
$('export').onclick=run(async()=>{const {text}=await api('export?'+query({desk:view.id,actor:view.actor}));
  const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));
  const a=document.createElement('a');a.href=url;a.download='角色可见对话.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
$('all').onclick=run(async()=>{history.replaceState(null,'','/live');await library();});
const saved=new URLSearchParams(location.search);
try{await library();if(saved.has('desk'))await open(saved.get('desk'),saved.get('actor'));}catch(e){$('error').textContent=e.message;}
