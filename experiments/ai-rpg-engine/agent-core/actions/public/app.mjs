const $=id=>document.getElementById(id),el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
let adapters=[],current=null,busy=false;
const selected=()=>adapters.find(a=>a.id===$('adapter').value),key=()=> 'rpg-actions-r5-'+$('adapter').value;
const path=(action='')=>'/api/'+selected().id+'/sessions'+(current?'/'+current.id:'')+(action?'/'+action:'');
const op=()=>'op-'+crypto.randomUUID(),base=()=>({operationId:op(),revision:current.revision});
function notice(text,error=false){$('notice').textContent=text;$('notice').className=error?'error':'';}
async function request(url,data){const r=await fetch(url,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Rpg-Actions':'offline-preview'},body:JSON.stringify(data)});const v=await r.json();if(!r.ok)throw Error(v.error||'请求失败');return v;}
async function load(){if(current){current=await request(path());localStorage.setItem(key(),current.id);}render();}
async function run(fn){if(busy)return;busy=true;render();try{await fn();await load();notice('记录已更新。');}catch(e){notice('未完成：'+e.message+'。请读取记录或核对原操作；不会自动重发。',true);try{await load();}catch{/* keep prior view */}}finally{busy=false;render();}}
function render(){
 const v=current,p=v?.pending?v.operations[v.pending]:null,locked=busy||!v||!!p||!v.enabled;
 document.querySelectorAll('button').forEach(b=>b.disabled=locked);
 $('create').disabled=busy;$('adapter').disabled=busy;$('refresh').disabled=busy||!v;
 $('recover').disabled=busy||!v;$('narrate').disabled=busy||!p||p.purpose!=='action-narration'||p.status==='unknown';
 $('discard').disabled=busy||!p||!['failed','rejected'].includes(p.status);
 $('toggle').disabled=busy||!v||!!p;$('fork').disabled=busy||!v||!!p;$('override').disabled=busy||!v||!!p;
 $('handoff').disabled=busy||!v;$('toggle').textContent=v?.enabled?'暂停新行动':'恢复新行动';
 $('session').textContent=v?'记录 '+v.id+' · 状态版本 '+v.stateRevision:'尚未开始';
 $('resources').replaceChildren(...(v?.resources||[]).map(r=>{const n=el('div',undefined,'resource');n.append(el('span',r.name),el('strong',r.quantity??'未设定'));return n;}));
 const chosen=$('item').value;$('item').replaceChildren(...(v?.resources||[]).map(r=>{const o=el('option',r.name);o.value=r.id;return o;}));if(v?.resources.some(r=>r.id===chosen))$('item').value=chosen;
 $('status').textContent=!v?'':!v.enabled?'新行动已暂停；资产仍保留。':p?'本轮尚未结束，请先处理下方结果。':'可以开始下一步。';
 $('state').textContent=v?JSON.stringify(v.state,null,2):'';
 $('metrics').textContent=v?'模拟生成 '+v.metrics.stages+' 次 · 真实调用 0 次 · 费用 $0 · token / 首字未测量':'';
 $('tools').replaceChildren(...(v?.toolRegistry||[]).map(t=>el('p',t.name+' · '+(t.effect==='read'?'只读':'仅提案'),'muted')));
 $('evidence').href=v?path('evidence'):'#';$('evidence').target='_blank';
 $('stages').textContent=v?JSON.stringify(Object.entries(v.operations).map(([id,o])=>({id,status:o.status,stages:o.stages,tools:o.toolResults})),null,2):'';
 $('proposals').replaceChildren();
 for(const [id,row] of Object.entries(v?.proposals||{})){
  const n=el('div',undefined,'proposal'),proposal=row.proposal,stale=proposal.session!==v.id||proposal.stateRevision!==v.stateRevision;
  const resource=v.resources.find(r=>r.id===proposal.action.itemId),change=proposal.expected[0];
  n.append(el('strong','方案：'+(resource?.name||proposal.action.itemId)+' × '+proposal.action.quantity),el('p','预计 '+change.before+' → '+change.after+'；'+(row.status==='review'?(stale?'状态已变化，需重新准备。':'尚未执行。'):row.status==='executed'?'已执行。':'已取消。')));
  if(row.status==='review'){
   const controls=el('div',undefined,'buttons'),yes=el('button','确认并执行','primary'),no=el('button','取消方案');
   yes.disabled=locked||stale;no.disabled=busy||!!p;
   yes.onclick=()=>run(()=>request(path('execute'),{...base(),proposalId:id,proposalHash:proposal.hash}));
   no.onclick=()=>run(()=>request(path('cancel'),{...base(),proposalId:id,proposalHash:proposal.hash}));
   controls.append(yes,no);n.append(controls);
  }$('proposals').append(n);
 }
 $('receipts').replaceChildren(...(v?.actionReceipts||[]).map(r=>el('div','已执行：'+r.result.label+' '+r.result.name+' × '+r.result.quantity+'，数量 '+r.result.before+' → '+r.result.after+' · 版本 '+r.stateRevision,'receipt')));
 const labels={pending:'行动已入账，等待生成叙事。',failed:'叙事明确失败。行动已经执行，可主动重试叙事或仅保留回执。',unknown:'调用结果未知。请先核对原操作，不会自动重发。',rejected:'返回结果未通过校验。状态以本地回执为准。'};
 $('pending').textContent=p?(labels[p.status]||'本轮正在处理。')+(p.error?' '+p.error:''):'';
 $('messages').replaceChildren(...(v?.turns||[]).map(t=>{const n=el('div',undefined,'turn');n.append(el('small',t.input),el('div',t.body));return n;}));
 if(!v?.turns.length)$('messages').append(el('p','尚无剧情。行动回执与最终叙事分开保存。','muted'));
}
$('create').onclick=()=>run(async()=>{const a=selected(),r=await request('/api/'+a.id+'/sessions',{operationId:op(),confirmed:true,initial:a.initial});current={id:r.id};});
$('refresh').onclick=()=>run(async()=>{});
$('adapter').onchange=()=>run(async()=>{const id=localStorage.getItem(key());current=id?{id}:null;});
const action=()=>({actionId:selected().rule.id,itemId:$('item').value,quantity:Number($('quantity').value)});
$('prepare').onclick=()=>run(()=>request(path('propose'),{...base(),action:action()}));
$('execute').onclick=()=>run(()=>request(path('execute'),{...base(),action:action()}));
document.querySelectorAll('[data-input]').forEach(b=>b.onclick=()=>run(()=>request(path('turn'),{...base(),input:b.dataset.input})));
$('narrate').onclick=()=>run(()=>request(path('narrate'),{options:{fault:$('fault').value||null}}));
$('recover').onclick=()=>run(()=>request(path('recover'),{}));
$('discard').onclick=()=>run(()=>request(path('discard'),{...base(),pendingId:current.pending,reason:'用户选择只保留已提交回执，结束本轮'}));
$('toggle').onclick=()=>run(()=>request(path('enabled'),{...base(),enabled:!current.enabled}));
$('fork').onclick=()=>run(async()=>{const r=await request(path('fork'),{...base(),eventId:current.events.at(-1)?.id??null});current={id:r.id};});
$('override').onclick=()=>run(async()=>{const state=structuredClone(current.state),list=selected().id==='cultivation'?state.items:state.supplies;
 const item=list.find(r=>r.id===$('item').value);if(!item)throw Error('请选择物资');item.quantity=Number($('rewrite').value);
 await request(path('override'),{...base(),state,reason:$('reason').value});});
$('handoff').onchange=()=>run(async()=>{const f=$('handoff').files[0];if(!f)return;if(f.size>131072)throw Error('文件超过 128 KB');
 const report=await request(path('e2'),JSON.parse(await f.text()));$('handoffResult').textContent=JSON.stringify(report,null,2);});
try{const data=await request('/api/bootstrap');adapters=data.adapters;$('adapter').replaceChildren(...adapters.map(a=>{const o=el('option',a.name);o.value=a.id;return o;}));const id=localStorage.getItem(key());current=id?{id}:null;await load();notice('就绪。确认样例后开始离线验收。');}catch(e){notice(e.message,true);}
