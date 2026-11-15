const $=id=>document.getElementById(id),make=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
let catalog,current,busy=false;
const op=()=> 'op-'+crypto.randomUUID(),labels={'query_local_state':'查询登记状态','prepare_action':'准备行动提案','count_registered':'统计登记数量'},status={completed:'已采用',proposed:'等待确认',failed:'明确失败',rejected:'结果被拒绝',unknown:'结果未知',pending:'待处理','awaiting-adoption':'生成完成，待采用',stale:'来源已变化'};
async function api(path='',data){const res=await fetch('/api/runs'+path,data?{method:'POST',headers:{'Content-Type':'application/json','X-RPG-Assembly':'offline-preview'},body:JSON.stringify(data)}:{});const v=await res.json();if(!res.ok)throw Error(v.error);return v;}
function option(value,text){const n=make('option',text);n.value=value;return n;}
function button(text,fn){const n=make('button',text);n.type='button';n.onclick=()=>work(fn);return n;}
function evidence(parent,title,value){const d=make('details'),s=make('summary',title);d.append(s);d.addEventListener('toggle',()=>{if(d.open&&!d.querySelector('pre'))d.append(make('pre',JSON.stringify(value,null,2)));});parent.append(d);}
async function refreshList(){catalog=await api();const saved=$('saved');saved.replaceChildren(option('','选择场景'));for(const r of catalog.runs)saved.append(option(r.id,(r.adapterId==='cultivation'?'修仙':'灯塔')+' · '+r.id.slice(-8)+(r.source?' · 分支':'')));if(current)saved.value=current.id;}
async function work(fn){if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);$('notice').className='';$('notice').textContent='正在处理本地模拟任务…';
 try{const v=await fn();if(v?.world){current=v;history.replaceState(null,'','?run='+v.id);render();}await refreshList();$('notice').textContent='记录已保存。真实调用 0；不会自动重试。';}
 catch(e){$('notice').className='error';$('notice').textContent='操作未完成：'+e.message+'。可刷新记录核对，勿重复提交未知请求。';}
 finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);if(current)updateControls();}}
const post=(action,data={})=>api('/'+current.id+'/'+action,data);
function updateControls(){const waiting=current.tasks.some(t=>['pending','unknown','awaiting-adoption'].includes(t.adoption));
 $('send').disabled=!!current.world.pending||waiting;document.querySelectorAll('[data-input]').forEach(b=>b.disabled=$('send').disabled);$('checkpoint').disabled=!!current.world.pending||waiting;$('fork').disabled=!current.checkpoints.length||!!current.world.pending||waiting;$('save-capabilities').disabled=!!current.world.pending||waiting;}
function render(){const v=current;$('workspace').hidden=false;$('resources').replaceChildren();for(const x of v.world.resources){const row=make('div',undefined,'quantity');row.append(make('span',x.name),make('strong',x.quantity===null?'未设定':String(x.quantity)));$('resources').append(row);}
 $('state').textContent=JSON.stringify(v.world.state,null,2);$('receipt').replaceChildren();const event=v.world.events.filter(e=>e.kind==='action').at(-1);if(event)$('receipt').append(make('p',event.changes.map(c=>c.before+' → '+c.after).join('；')+' · 已由本地规则提交','receipt'));
 $('story').replaceChildren();if(!v.world.turns.length)$('story').append(make('p','尚未完成剧情回合。先交流、查询或拟定行动方案。','muted'));for(const t of v.world.turns){const m=make('article',undefined,'message');m.append(make('small',t.input),make('div',t.body));$('story').append(m);}
 $('proposal').replaceChildren();for(const [proposalId,row] of Object.entries(v.world.proposals).filter(([,x])=>x.status==='review')){const p=make('div',undefined,'proposal');p.append(make('strong','待确认：数量 '+row.proposal.action.quantity),make('p',row.proposal.expected.map(x=>x.before+' → '+x.after).join('；')+'。当前尚未执行。'));
  p.append(button('确认执行',()=>post('confirm',{operationId:'execute-'+proposalId,revision:v.world.revision,proposalId,proposalHash:row.proposal.hash})));$('proposal').append(p);}
 $('pending').replaceChildren();if(v.world.pending){const p=make('div',undefined,'pending');p.append(make('p','本轮状态：'+(status[v.world.pendingStatus]??v.world.pendingStatus)));
  if(v.world.pendingStatus==='pending'&&!v.tasks.some(t=>t.ownerRef.operationId===v.world.pending))p.append(button('生成结果叙事',()=>post('narrate',{strategy:$('strategy').value,fault:$('fault').value||null})));
  if(['failed','rejected'].includes(v.world.pendingStatus))p.append(button('保留已确认结果，结束本轮',()=>post('finish',{operationId:op(),reason:'用户在离线预览选择结束失败回合'})));$('pending').append(p);}
 $('capabilities').replaceChildren();for(const tool of v.tools){const label=make('label'),input=make('input');input.type='checkbox';input.value=tool.name;input.checked=tool.enabled;label.append(input,make('span',labels[tool.name]??tool.name));$('capabilities').append(label);}
 $('budget').textContent='模拟请求 '+v.budget.calls+' / '+v.budget.limitCalls+' 次；实际费用 $0；模拟已知费用 $'+(v.budget.simulatedKnownMicroUSD/1e6).toFixed(4)+'；未知请求 '+v.budget.unresolved+' 次。';
 $('checkpoints').replaceChildren(...v.checkpoints.map(c=>option(c.id,'正文 '+c.turns+' 段 · '+c.id.slice(-8))));
 $('tasks').replaceChildren();for(const t of v.tasks){const item=make('article',undefined,'task'),title=make('div',undefined,'task-title');title.append(make('strong',catalog.owners.find(o=>o.id===t.purpose)?.authority==='observation'?'独立观察记录':'世界任务'),make('small',status[t.adoption]??t.adoption));item.append(title,make('small',t.input||'已执行结果叙事'));
  if(t.fallbackUsed)item.append(make('p','已切换一次备用模型桩'));if(t.error)item.append(make('p',t.error,'error'));
  if(t.adoption==='unknown')item.append(button('核对原请求',()=>post('recover',{taskId:t.id})));
  if(t.adoption==='awaiting-adoption')item.append(button('采用观察记录',()=>post('adopt',{taskId:t.id})));
  if(catalog.owners.find(o=>o.id===t.purpose)?.authority==='observation'&&t.receipt?.text)item.append(make('p',t.receipt.text));
  const queries=v.queries.filter(q=>q.operationId===t.ownerRef.operationId);if(queries.length)evidence(item,'查询与计算结果',queries);
  evidence(item,'冻结请求、回执与模型策略',t);$('tasks').append(item);}
 updateControls();}
async function submit(text){if(!text.trim())throw Error('请填写行动或对话');return post('submit',{operationId:op(),ownerId:$('owner').value,input:text,strategy:$('strategy').value,fault:$('fault').value||null});}
$('create').onclick=()=>work(()=>api('',{operationId:op(),adapterId:$('scene').value}));$('saved').onchange=()=>{if($('saved').value)work(()=>api('/'+$('saved').value));};$('refresh').onclick=()=>work(()=>current?api('/'+current.id):refreshList());
$('composer').onsubmit=e=>{e.preventDefault();work(()=>submit($('input').value));};document.querySelectorAll('[data-input]').forEach(b=>b.onclick=()=>{$('input').value=b.dataset.input;work(()=>submit(b.dataset.input));});
$('save-capabilities').onclick=()=>work(()=>post('capabilities',{operationId:op(),revision:current.world.revision,names:[...document.querySelectorAll('#capabilities input:checked')].map(x=>x.value)}));
$('checkpoint').onclick=()=>work(()=>post('checkpoint',{operationId:op()}));$('fork').onclick=()=>work(()=>post('fork',{operationId:op(),checkpointId:$('checkpoints').value}));
$('owner').onchange=()=>{$('scope').textContent=$('owner').value===catalog.assembly.entrypoints.dialogue?'使用当前世界的状态与工具权限。':'仅最近公开正文和事件编号；无完整物资、私人记忆或写入权限。生成后需单独采用。';};
await refreshList();for(const w of catalog.worlds)$('scene').append(option(w.id,w.id==='cultivation'?'修仙 · 药圃休息廊':'灯塔 · 值夜室'));
for(const o of catalog.owners.filter(o=>o.id===catalog.assembly.entrypoints.dialogue||o.authority==='observation'))$('owner').append(option(o.id,o.authority==='world'?'世界交流与行动':'独立观察记录'));$('owner').onchange();
const saved=new URL(location.href).searchParams.get('run');if(saved)await work(()=>api('/'+saved));
