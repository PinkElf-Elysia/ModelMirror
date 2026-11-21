const $=id=>document.getElementById(id);
const make=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
const option=(value,text)=>{const n=make('option',text);n.value=value;return n;};
const op=()=> 'op-'+crypto.randomUUID();
const states={completed:'已采用',proposed:'等待行动确认',failed:'明确失败',rejected:'回应未通过校验',unknown:'结果未知',pending:'待处理',running:'处理中','awaiting-adoption':'待采用',stale:'来源已变化，未采用',discarded:'未采用'};
let current=null,busy=false,choicesPanel=null;
async function api(path='',data){const res=await fetch('/api/runs'+path,data?{method:'POST',headers:{'Content-Type':'application/json','X-RPG-Scene':'offline-preview'},body:JSON.stringify(data)}:{});const v=await res.json();if(!res.ok)throw Error(v.error);return v;}
const post=(action,data={})=>api('/'+current.id+'/'+action,data);
function button(text,fn,cls){const n=make('button',text,cls);n.type='button';n.onclick=()=>work(fn);return n;}
function evidence(parent,title,value){const d=make('details');d.append(make('summary',title),make('pre',JSON.stringify(value,null,2)));parent.append(d);}
async function list(){const c=await api();$('saved').replaceChildren(option('','选择记录'));for(const r of c.runs)$('saved').append(option(r.id,(r.adapterId==='lighthouse'?'灯塔':'修仙')+' · '+r.id.slice(-8)+(r.source?' · 分支':'')));if(current)$('saved').value=current.id;return c;}
function controls(){if(!current)return;const waiting=current.tasks.some(t=>['pending','running','unknown','awaiting-adoption'].includes(t.adoption)),blocked=busy||!!current.world.pending||waiting;
 $('send').disabled=blocked;$('checkpoint').disabled=blocked;$('fork').disabled=blocked||!current.checkpoints.length;
 document.querySelectorAll('[data-text]').forEach(b=>b.disabled=blocked);choicesPanel?.controls(blocked);}
function scope(){if(!current)return;const p=current.scene.participants.find(p=>'participant-'+p.id===$('owner').value);$('scope').textContent=p?p.scope+'。回应采用后才成为已发生的对话。':'使用世界已登记的状态和工具；行动提案需要单独确认。';}
async function work(fn){if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);$('notice').className='';$('notice').textContent='正在处理本地模拟任务…';
 try{const v=await fn();if(v?.world){current=v;history.replaceState(null,'','?run='+v.id);render();}await list();$('notice').textContent='本地记录已保存。真实调用 0 次。';}
 catch(e){$('notice').className='error';$('notice').textContent='操作未完成：'+e.message+'。请刷新记录核对原操作。';}
 finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);controls();}}
function render(){
 const v=current;$('workspace').hidden=false;$('diagnostics').hidden=false;$('scene-name').textContent=(v.adapterId==='lighthouse'?'灯塔 · 值夜室':'修仙 · 药圃休息廊')+(v.source?' · 检查点分支':'');
 $('resources').replaceChildren();for(const r of v.world.resources){const row=make('div',undefined,'quantity');row.append(make('span',r.name),make('strong',r.quantity===null?'未设定':String(r.quantity)));$('resources').append(row);}
 $('state').textContent=JSON.stringify(v.world.state,null,2);$('receipt').replaceChildren();const event=v.world.events.filter(e=>e.kind==='action').at(-1);
 if(event)$('receipt').append(make('p','已结算：'+event.changes.map(x=>x.before+' → '+x.after).join('；'),'receipt'));
 $('participants').replaceChildren();for(const p of v.scene.participants){const row=make('div',undefined,'person');row.append(make('strong',p.name),make('p',p.scope));$('participants').append(row);}
 $('story').replaceChildren();if(!v.timeline.length)$('story').append(make('p','场景刚刚开始。先与值守员交谈，或向世界提出行动。','empty'));
 for(const t of v.timeline){const item=make('article',undefined,'message');item.append(make('small','你：'+t.input,'input'),make('div',t.kind==='speech'?t.name+' · 已采用对话':'世界 · 已完成叙事','speaker'),make('div',t.body));$('story').append(item);}
 $('review').replaceChildren();for(const t of v.tasks.filter(t=>t.adoption==='awaiting-adoption')){
  const p=v.scene.participants.find(p=>'participant-'+p.id===t.purpose),box=make('div',undefined,'review'),actions=make('div',undefined,'actions');
  box.append(make('strong',(p?.name??'人物')+'的回应 · 待采用'),make('p',t.body),make('p','尚未进入场景；采用只记录这段对话，不改变世界状态。','muted'));
  actions.append(button('采用这段回应',()=>post('adopt',{taskId:t.id}),'primary'),button('不采用',()=>post('dismiss',{taskId:t.id})));box.append(actions);$('review').append(box);}
 $('proposal').replaceChildren();for(const [proposalId,row] of Object.entries(v.world.proposals).filter(([,x])=>x.status==='review')){
  const box=make('div',undefined,'proposal');box.append(make('strong','行动提案 · 尚未执行'),make('p','使用 '+row.proposal.action.quantity+' 份；确认后登记数量 '+row.proposal.expected.map(x=>x.before+' → '+x.after).join('；')+'。'),
  button('确认执行一次',()=>post('confirm',{operationId:'execute-'+proposalId,revision:v.world.revision,proposalId,proposalHash:row.proposal.hash}),'primary'));if(choicesPanel)box.append(button('放弃提案',()=>post('cancel',{operationId:'cancel-'+proposalId,revision:v.world.revision,proposalId,proposalHash:row.proposal.hash})));$('proposal').append(box);}
 $('pending').replaceChildren();
 if(v.world.pending){const box=make('div',undefined,'pending'),task=v.tasks.find(t=>t.operationId===v.world.pending&&t.purpose==='world-narration');box.append(make('p','行动回合：'+(states[v.world.pendingStatus]??v.world.pendingStatus)));
  if(v.world.pendingStatus==='pending'&&!task)box.append(button('叙述已确认结果',()=>post('narrate',{fault:$('fault').value||null}),'primary'));
  if(['failed','rejected'].includes(v.world.pendingStatus))box.append(button('保留回执，结束本轮',()=>post('finish',{operationId:op(),reason:'离线验收中明确选择结束失败回合'})));$('pending').append(box);}
 for(const t of v.tasks.filter(t=>['unknown','failed','rejected','stale'].includes(t.adoption))){
  const box=make('div',undefined,'pending');box.append(make('strong',states[t.adoption]),make('p',t.error||'请核对记录。','muted'));
  if(t.adoption==='unknown')box.append(button('核对原请求',()=>post('recover',{taskId:t.id})));
  $('pending').append(box);}
 $('checkpoints').replaceChildren(...v.checkpoints.map(c=>option(c.id,'正文 '+c.turns+' 段 · '+c.id.slice(-8))));
 $('budget').textContent='全部任务共用记录：模拟请求 '+v.budget.calls+' / '+v.budget.limitCalls+' 次；真实费用 $0；未知请求 '+v.budget.unresolved+' 次。不启用自动重试或备用切换。';
 $('tasks').replaceChildren();for(const t of v.tasks){const box=make('article',undefined,'task');box.append(make('strong',states[t.adoption]??t.adoption),make('small',' · '+t.purpose+' · '+t.stages.length+' 阶段'));
  for(const s of t.stages)if(s.queryResults.length)evidence(box,'本次查询依据',s.queryResults);evidence(box,'任务、模型策略与来源',t);$('tasks').append(box);}
 if(choicesPanel){
  const sample=document.querySelector('[data-owner="world-dialogue"]');
  sample.dataset.text=v.adapterId==='cultivation'?'我决定自己服用 2 份养息丹。':'添油 2';
 }
 choicesPanel?.render(v);scope();controls();
}
$('create').onclick=()=>work(()=>api('',{operationId:op(),adapterId:$('scene').value}));
$('saved').onchange=()=>{if($('saved').value)work(()=>api('/'+$('saved').value));};
$('refresh').onclick=()=>work(()=>current?api('/'+current.id):list());
$('owner').onchange=scope;
$('composer').onsubmit=e=>{e.preventDefault();const input=$('input').value.trim();if(!input){$('input').focus();return;}work(()=>post('submit',{operationId:op(),ownerId:$('owner').value,input,fault:$('fault').value||null}));};
document.querySelectorAll('[data-text]').forEach(b=>b.onclick=()=>{$('owner').value=b.dataset.owner;$('input').value=b.dataset.text;scope();$('input').focus();});
$('checkpoint').onclick=()=>work(()=>post('checkpoint',{operationId:op()}));
$('fork').onclick=()=>work(()=>post('fork',{operationId:op(),checkpointId:$('checkpoints').value}));
try{const c=await list();if(c.sceneChoices){const {mountChoices}=await import('/choices.mjs');choicesPanel=mountChoices({post,work});document.title='选择与行动 · 离线场景';document.querySelector('h1').textContent='场景中的选择与行动';document.querySelector('footer').textContent='第十轮独立预览 · 确定性离线样例';}for(const w of c.worlds)$('scene').append(option(w.id,w.id==='lighthouse'?'灯塔 · 值夜室':'修仙 · 药圃休息廊'));$('scene').value=c.sceneChoices?'cultivation':'lighthouse';const saved=new URL(location.href).searchParams.get('run');if(saved)await work(()=>api('/'+saved));else $('notice').textContent='选择一个场景开始，或打开已有记录。';}
catch(e){$('notice').className='error';$('notice').textContent='无法读取本地记录：'+e.message;}
