const $=id=>document.getElementById(id),make=(tag,text,cls)=>{const el=document.createElement(tag);el.textContent=text;if(cls)el.className=cls;return el;};
let data=null,busy=false;const stored=localStorage.getItem('r6-bridge-session');
const op=()=> 'op-'+crypto.randomUUID();
const names={completed:'已完成',prepared:'已准备',running:'处理中',unknown:'结果待核对',failed:'明确失败',rejected:'未采纳',stale:'来源版本已变化'};
async function api(path,body){const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-RPG-Bridge':'offline-preview'},body:JSON.stringify(body)});const j=await r.json();if(!r.ok)throw Error(j.error);return j;}
function controls(){
 $('create').disabled=busy;$('grant').disabled=busy;
 $('prepare').disabled=busy||!data||data.world.events.length>0;
 $('execute').disabled=busy;$('cancel').disabled=busy;$('actor').disabled=busy;
 $('refresh').disabled=busy||!data;
 const actor=data?.actors.find(a=>a.id===$('actor').value);
 for(const b of document.querySelectorAll('[data-input]'))b.disabled=busy||!actor||!!actor.pending;
}
function render(){
 if(!data)return;const actor=data.actors.find(a=>a.id===$('actor').value);
 $('quantity').textContent=data.world.medicine;$('version').textContent='状态 v'+data.world.stateVersion;
 $('binding').textContent=JSON.stringify({worldId:data.world.id,actorBinding:actor.binding},null,2);
 $('scope').textContent=actor.binding.grant.witness?(actor.binding.grant.inventory?'在场观察者 · 可查询获授权的丹药登记。':'在场观察者 · 能看到服药事件，不能查询背包余量。'):'对照实例 · 未获得观察或背包授权，使用独立历史。';
 $('receipt').replaceChildren(...data.world.events.map(e=>make('p','已确认：服用 '+e.result.quantity+' 份，登记 '+e.result.before+' → '+e.result.after+'。','receipt')));
 $('narration').replaceChildren(...data.world.narration.map(t=>make('p',t)));
 $('conversation').replaceChildren();
 const greeting=make('div',actor.opening,'message');greeting.prepend(make('small','林舟 · 固定开场'));$('conversation').append(greeting);
 let reply=0;
 for(const m of actor.history){const row=make('div',m.content,'message '+(m.role==='user'?'user':''));const source=m.role==='assistant'?actor.responses[reply++]:null;
  row.prepend(make('small',m.role==='user'?'玩家发言／转述':'林舟 · 状态 v'+source.stateVersion+(source.current?'':'（历史回应，非当前状态）')));$('conversation').append(row);}
 $('observations').textContent=JSON.stringify(actor.observations,null,2);
 $('tasks').replaceChildren();
 for(const t of data.tasks){const row=make('div','','task');row.append(make('strong',(t.purpose==='world-action'?'世界行动':'角色响应 · '+(t.actorId==='witness'?'在场':'未观察'))+' — '+(names[t.status]||t.status)));
 row.append(make('small','任务 '+t.id+' · '+t.owner+(t.stateVersion===undefined?'':' · 来源 v'+t.stateVersion)));
 if(t.error)row.append(make('p','诊断：'+t.error,'muted'));
 if(t.status==='unknown'){const b=make('button','核对原任务');b.onclick=()=>run(async()=>{data=await api('/api/sessions/'+data.id+'/recover',{taskId:t.id});});row.append(b);}
 const detail=make('details','');detail.append(make('summary','查看回执与只读查询依据'),make('pre',JSON.stringify({sourceEvents:t.sourceEvents,receipt:t.receipt,stages:t.stages},null,2)));row.append(detail);$('tasks').append(row);}
 if(!data.tasks.length)$('tasks').textContent='尚无任务';
 $('metrics').textContent='真实调用 0 次 · 费用 $0 · 当前进程离线生成：世界 '+data.metrics.worldGenerations+' 次，人物 '+data.metrics.actorGenerations+' 次。持久化阶段见任务记录。';
 $('download').hidden=false;$('download').href='/api/sessions/'+data.id+'/evidence';$('conversation').scrollTop=$('conversation').scrollHeight;controls();
}
async function run(fn,message='正在处理离线任务…'){
 if(busy)return;busy=true;controls();$('notice').className='';$('notice').textContent=message;
 try{await fn();if(data){localStorage.setItem('r6-bridge-session',data.id);render();}$('notice').textContent='记录已保存。可继续对话、切换对照实例或刷新核对。';}
 catch(e){$('notice').className='error';$('notice').textContent='未完成：'+e.message+'。不会自动重发，请恢复记录核对。';}
 finally{busy=false;controls();}
}
$('create').onclick=()=>run(async()=>{data=await api('/api/sessions',{operationId:op(),inventory:$('grant').value==='inventory'});$('confirmation').hidden=true;});
$('prepare').onclick=()=>{$('confirmation').hidden=false;$('execute').focus();};
$('cancel').onclick=()=>{$('confirmation').hidden=true;$('prepare').focus();};
$('execute').onclick=()=>run(async()=>{const request={operationId:op(),revision:data.world.revision};
 data=await api('/api/sessions/'+data.id+'/execute',request);$('confirmation').hidden=true;},'正在确认行动、更新世界并投递授权观察…');
$('actor').onchange=render;
for(const b of document.querySelectorAll('[data-input]'))b.onclick=()=>run(async()=>{
 data=await api('/api/sessions/'+data.id+'/respond',{operationId:op(),actorId:$('actor').value,input:b.dataset.input});
},b.dataset.input.includes('还剩')?'正在查询该角色可读的本地状态…':'正在依据该角色的观察生成离线回应…');
$('refresh').onclick=()=>run(async()=>{data=await api('/api/sessions/'+data.id);},'读取已保存记录，不重新执行…');
if(stored)run(async()=>{data=await api('/api/sessions/'+stored);},'恢复独立场景记录…');else controls();
