const $=s=>document.querySelector(s),op=()=> 'op-'+crypto.randomUUID(),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let bootstrap,adapter,record,busy=false,candidate=null,rewriteReview=null;
const apiPath=(action='')=>'/api/'+adapter.id+'/sessions'+(record?'/'+record.id:'')+(action?'/'+action:'');
async function api(path,data){const response=await fetch(path,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json','x-rpg-core':'offline-preview'},body:data===undefined?undefined:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw Error(result.error||'请求失败');return result;}
function notice(message=''){$('#notice').textContent=message;}
function lock(value){busy=value;document.querySelectorAll('button').forEach(b=>b.disabled=value);$('#adapter').disabled=value;if(!value&&record){$('#send').disabled=!!record.pending||!record.enabled;$('#preview').disabled=!!record.pending||!record.enabled;$('#adopt').disabled=!!record.pending||!record.enabled;}}
async function run(fn){if(busy)return;notice();lock(true);try{await fn();}catch(e){notice(e.message==='CONTEXT_OUTPUT_CONFLICT'?'配置冲突：当前 Mod 或自定义词要求输出思维链／检查过程。请移除冲突配置后新建 R4 会话；原模式保持可用。':'未完成：'+e.message);if(record)try{await refresh();}catch{} }finally{lock(false);$('#progress').textContent='';}}
function choose(id){adapter=bootstrap.adapters.find(a=>a.id===id)||bootstrap.adapters[0];$('#adapter').value=adapter.id;$('#description').textContent=adapter.description;$('#initial').value=JSON.stringify(adapter.initial,null,2);$('#confirmed').checked=false;$('#config').value=JSON.stringify({profiles:[],mods:[]});$('#input').value='';$('#fault').value='';$('#variant').value='calm';$('#context-options').value='{}';$('#material-mode').innerHTML=adapter.contextChoices.map(([id,name])=>'<option value="'+esc(id)+'">'+esc(name)+'</option>').join('');$('#material-refs').value='';candidate=null;$('#candidate').hidden=true;$('#initial-panel').innerHTML='<p class="muted">'+esc(adapter.opening)+'</p>';}
function setLocation(){const u=new URL(location.href);u.searchParams.set('adapter',adapter.id);if(record)u.searchParams.set('session',record.id);else u.searchParams.delete('session');history.replaceState({},'',u);}
function changesHtml(changes){return changes.map(c=>'<div class="change"><strong>'+esc(c.path)+'</strong><br>'+esc(JSON.stringify(c.before))+' → '+esc(JSON.stringify(c.after))+'<small>'+esc(c.reason)+' · '+esc(c.authority)+'</small></div>').join('')||'<p class="muted">没有状态变化。</p>';}
function traceHtml(t){if(!t)return '';const labels={full:'完整资料',scoped:'有限场景资料','full-fallback':'已回退完整资料'},selected=t.materials.filter(m=>m.selected),refs=t.materials.filter(m=>m.purpose==='world-reference');return '<details><summary>装配依据 · '+esc(labels[t.mode])+' · '+selected.length+'/'+t.materials.length+' 项</summary><p>场景资料 '+refs.filter(m=>m.selected).length+'/'+refs.length+' 项；回合开始时冻结，工具续写不重新裁剪。</p>'+(t.fallbackReasons.length?'<p class="pending">回退原因：'+esc(t.fallbackReasons.join('；'))+'</p>':'')+'<p>本轮输入：'+esc(JSON.stringify(t.selection))+'</p>'+t.diagnostics.filter(d=>d.message).map(d=>'<p class="muted">'+esc(d.message)+'</p>').join('')+t.materials.map(m=>'<details><summary>'+esc((m.selected?'纳入':'未纳入')+' · '+m.id+' · '+m.nature+' · '+m.bytes.toLocaleString()+' 字节')+'</summary><p>'+esc(m.reason)+' · '+esc(m.purpose)+'</p><pre>'+esc(JSON.stringify({source:m.source,scope:m.scope,hash:m.hash},null,2))+'</pre></details>').join('')+'<details><summary>完整轨迹 JSON（不含原始工具签名）</summary><pre>'+esc(JSON.stringify(t,null,2))+'</pre></details></details>';}
function render(){
 $('#setup').hidden=!!record;$('#play').hidden=!record;if(!record)return;
 $('#title').textContent=adapter.name;$('#session-label').textContent='公共运行核心 / '+record.id;
 $('#status').textContent=(record.enabled?'新回合已启用':'已停用新回合，资产与记录保留')+' · '+record.turns.length+' 个已完成回合';
 $('#state-version').textContent='状态 v'+record.stateRevision;$('#panels').innerHTML=record.panels;
 $('#state-json').textContent=JSON.stringify(record.state,null,2);$('#binding').textContent=JSON.stringify({binding:record.binding,ownership:adapter.ownership},null,2);
 $('#changes').innerHTML=changesHtml(record.events.at(-1)?.changes||[]);
 $('#journal').innerHTML='<article class="turn"><p class="turn-input">开局 · 已确认档案</p><div class="narrative">'+esc(adapter.opening)+'</div></article>'+record.messages.map(t=>'<article class="turn"><p class="turn-input">'+esc(t.input)+'</p><div class="narrative">'+(t.html??esc(t.body))+'</div><small>已提交 · 状态 v'+t.stateRevision+' · 事件 '+esc(t.eventId||t.id)+'</small></article>').join('');
 const pending=record.operations[record.pending];$('#pending').hidden=!pending;$('#pending-text').textContent=pending?pending.status+' · '+(pending.error||'原请求尚未确认完成'):'';
 $('#discard').hidden=!pending||!['failed','rejected'].includes(pending.status);
 $('#scenarios').replaceChildren();for(const [,input] of adapter.scenarios){const b=document.createElement('button');b.type='button';b.textContent=input;b.onclick=()=>{$('#input').value=input;$('#input').focus();};$('#scenarios').append(b);}
 $('#stages').innerHTML=Object.entries(record.operations).filter(([,o])=>o.kind==='turn').sort(([,a],[,b])=>a.stateRevision-b.stateRevision).map(([id,o])=>'<div class="stage"><strong>'+esc(o.input)+'</strong><p>'+esc(o.status)+' · '+(o.stages?.length||0)+' 次模拟生成 · 真实费用 $0</p>'+ (o.stages||[]).map(s=>'<p>阶段 '+s.index+' · '+esc(s.status)+' · 请求 '+s.measurement.wireBytes.toLocaleString()+' 字节 · '+Math.round(s.timing?.completedMs||0)+' ms（模拟）</p>').join('')+(o.query?'<details><summary>本轮查询依据</summary><pre>'+esc(JSON.stringify(o.query,null,2))+'</pre></details>':'')+traceHtml(o.contextTrace)+'<small>'+esc(id)+'</small></div>').join('')||'<p class="muted">完成第一轮后，这里会展示实际模拟请求链。</p>';
 $('#checkpoint').innerHTML='<option value="">初始确认状态</option>'+record.events.map(e=>'<option value="'+esc(e.id)+'">状态 v'+e.stateRevision+' · '+esc(e.kind)+' · '+esc(e.id)+'</option>').join('');
 $('#rewrite').value=JSON.stringify(record.state,null,2);$('#toggle').textContent=record.enabled?'停用新回合':'重新启用新回合';$('#rewrite-diff').hidden=true;$('#apply-rewrite').hidden=true;rewriteReview=null;
 if(record.projectionError)notice('状态已保存，面板投影失败：'+record.projectionError+'。可刷新，不需要重新执行回合。');
 lock(busy);
}
async function refresh(){if(!record)return;record=await api(apiPath());setLocation();render();}
function options(){const mode=$('#material-mode').value,refs=$('#material-refs').value.split(/[,，]/).map(s=>s.trim()).filter(Boolean).map(s=>s.startsWith('object:')?s:'object:'+s);return {variant:$('#variant').value,fault:$('#fault').value||null,contextOptions:{...JSON.parse($('#context-options').value),selection:{mode:mode==='full'?'full':'scoped',refs:mode==='full'?refs:[mode,...refs]}}};}
async function send(input,opts){
 const request={operationId:op(),revision:record.revision,input,options:opts};
 $('#progress').textContent='正在执行离线模拟…';
 const timer=setInterval(()=>api(apiPath('progress')).then(p=>{$('#progress').textContent=p.text||'正在保存本轮记录…';}).catch(()=>{}),300);
 try{await api(apiPath('turn'),request);}finally{clearInterval(timer);}
 candidate=null;$('#candidate').hidden=true;await refresh();
}
$('#adapter').onchange=()=>choose($('#adapter').value);
$('#create').onclick=()=>run(async()=>{if(!$('#confirmed').checked)throw Error('请先确认初始档案和来源');const r=await api(apiPath(),{operationId:op(),confirmed:true,initial:JSON.parse($('#initial').value),config:JSON.parse($('#config').value)});record=r;await refresh();});
$('#new').onclick=()=>{if(busy)return;record=null;candidate=null;choose(adapter.id);setLocation();render();notice();};
$('#reload').onclick=()=>run(refresh);
$('#composer').onsubmit=e=>{e.preventDefault();run(()=>send($('#input').value,options()));};
$('#recover').onclick=()=>run(async()=>{await api(apiPath('recover'),{});await refresh();});
$('#discard').onclick=()=>run(async()=>{await api(apiPath('discard'),{operationId:op(),revision:record.revision,pendingId:record.pending,reason:'用户在离线验收中选择保留失败记录并结束本轮'});await refresh();});
$('#preview').onclick=()=>run(async()=>{const input=$('#input').value;if(!input.trim())throw Error('请先选择或输入行动');const opts=options();if(opts.fault)throw Error('候选预览请关闭模拟故障');const result=await api(apiPath('preview'),{input,options:opts});candidate={...result,input,options:opts};$('#candidate-body').textContent=result.body;$('#candidate-changes').textContent=JSON.stringify(result.changes,null,2);$('#candidate').hidden=false;});
$('#adopt').onclick=()=>run(async()=>{if(!candidate)throw Error('没有候选');await send(candidate.input,{...candidate.options,previewHash:candidate.previewHash});});
$('#cancel-candidate').onclick=()=>{candidate=null;$('#candidate').hidden=true;};
$('#review-rewrite').onclick=()=>{try{const state=JSON.parse($('#rewrite').value),reason=$('#reason').value.trim();if(!reason)throw Error('请填写改写原因');rewriteReview={state,reason,revision:record.revision};$('#rewrite-diff').textContent=JSON.stringify({before:record.state,after:state,reason},null,2);$('#rewrite-diff').hidden=false;$('#apply-rewrite').hidden=false;}catch(e){notice(e.message);}};
$('#apply-rewrite').onclick=()=>run(async()=>{if(!rewriteReview)throw Error('请先查看差异');await api(apiPath('override'),{operationId:op(),...rewriteReview});await refresh();});
$('#fork').onclick=()=>run(async()=>{const child=await api(apiPath('fork'),{operationId:op(),revision:record.revision,eventId:$('#checkpoint').value||null});record=child;await refresh();notice('已创建独立分支，原会话保持不变。');});
$('#toggle').onclick=()=>run(async()=>{await api(apiPath('enabled'),{operationId:op(),revision:record.revision,enabled:!record.enabled});await refresh();});
$('#evidence').onclick=()=>run(async()=>{const result=await api(apiPath('evidence')),url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=record.id+'-offline-evidence.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
await run(async()=>{bootstrap=await api('/api/bootstrap');$('#adapter').innerHTML=bootstrap.adapters.map(a=>'<option value="'+esc(a.id)+'">'+esc(a.name)+'</option>').join('');const params=new URLSearchParams(location.search);choose(params.get('adapter'));if(params.has('session')){record={id:params.get('session')};try{await refresh();}catch(e){record=null;render();throw e;}}else render();});
