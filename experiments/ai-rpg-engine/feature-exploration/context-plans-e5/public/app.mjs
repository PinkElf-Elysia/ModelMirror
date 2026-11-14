const $=s=>document.querySelector(s),esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state=null,busy=false,adapter='cultivation';
const labels={'rule-ready':'当前快照单步预检','intent-only':'仅意图，无执行规则',waiting:'依赖前序结果，未预检',condition:'条件待核实，未预检',clarification:'待补充','order-review':'待确认顺序',blocked:'暂不能预检',stale:'来源已变化'};
const names={use_medicine:'服用丹药',gift:'赠送',fuel_lamp:'为灯添油'};
const errorText={E5_CONFLICT:'计划已在其他页面更新，请重新读取后核对。',E5_REVIEW_REQUIRED:'请先处理澄清、顺序或来源问题。',E5_NOT_SAVED:'请先保存当前版本的只读计划。',E5_REQUEST_UNKNOWN:'请求结果尚不明确，不能重新派发；可重新读取本地记录。',E5_SOURCE_STALE:'来源已变化，请重新读取并核对计划。'};
async function api(url,body){const r=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json','X-RPG-E5':'plan-only'},body:JSON.stringify(body)}:{});const v=await r.json();if(!r.ok)throw Error(errorText[v.error]??v.error);return v;}
async function load(){state=await api('/api/view?adapter='+adapter);render();}
async function command(kind,{text=null,ops}={}){
 if(busy)return;busy=true;render();$('#notice').textContent='';
 const c={adapterId:adapter,kind,operationId:'op-'+crypto.randomUUID(),expectedHash:state.recordHash,text};if(ops)c.ops=ops;
 try{state=await api('/api/plan',c);const last=Object.values(state.operations).at(-1);if(last?.error)$('#notice').textContent='本次输入未采用：'+last.error;
  else if(kind==='save')$('#notice').textContent='已保存只读计划；没有执行任何步骤。';
 }catch(e){$('#notice').textContent=e.message;await load().catch(()=>{});}finally{busy=false;render();}
}
function render(){
 if(!state)return;const p=state.plan,steps=p?.steps??[];
 $('#world-title').textContent=state.title;$('#subtitle').textContent=state.subtitle;
 $('#resources').innerHTML=state.resources.map(r=>`<div class="resource"><span>${esc(r.name)}</span><strong>${r.quantity===null?'未设定':esc(r.quantity)+' 份'}</strong></div>`).join('');
 $('#people').textContent=state.people.length?state.people.map(x=>x.name).join('、')+'（仅登记）':'本情境未登记人物';$('#coverage').textContent=state.coverage;
 $('#source').textContent=JSON.stringify(state.source,null,2);$('#plan-count').textContent=steps.length+' / 3 步';
 const message=state.pending?'模拟请求结果未知，停止继续派发。':p?.stale?'来源已变化，请重新读取并核对计划。':p?.limit??p?.nextQuestion??'';
 $('#question').hidden=!message;$('#question').textContent=message;$('#order-actions').hidden=!p?.orderReview;
 $('#steps').innerHTML=steps.length?steps.map((s,i)=>{
  const slots=s.atomic.intent,item=state.resources.find(r=>r.id===slots.item?.value),target=state.people.find(r=>r.id===slots.target?.value);
  const e=s.preflight?.expected?.[0];
  return `<article class="step" data-id="${s.id}"><div class="step-heading"><h3>第 ${i+1} 步 · ${esc(names[s.atomic.action]??s.atomic.action)}<span class="step-index">${s.id}</span></h3><span class="tag">${esc(labels[s.status]??s.status)}</span></div><div class="step-body"><p>${esc(item?.name??'物资待明确')} · ${slots.quantity?esc(slots.quantity.value)+' 份':'数量待明确'}${s.atomic.action==='gift'?' · 对象：'+esc(target?.name??'待明确'):''}</p><p class="dependency">${s.after?'在 '+esc(s.after)+' 之后；需要届时重新读取结果':'当前顺序的首个步骤'}</p>${s.condition?`<div class="condition"><strong>${s.condition.kind==='player-claim'?'玩家陈述，未核实':'执行前提，未核实'}</strong>${esc(s.condition.value)}<p class="fine">相关世界事实仍需核实。</p></div>`:''}${e?`<p class="estimate">仅此步、基于当前快照：${esc(item?.name)} ${e.before} → ${e.after} 份。实际数量未变。</p>`:''}${s.diagnostics.map(x=>`<p class="fine">${esc(x)}</p>`).join('')}${s.atomic.action==='gift'?'<p class="fine muted">赠送尚无执行规则，人物在场与同意均未知。</p>':''}</div><div class="step-tools"><button data-do="remove">撤回此步</button><button data-do="up" ${i===0?'disabled':''}>上移</button><button data-do="down" ${i===steps.length-1?'disabled':''}>下移</button></div><details><summary>明确修改本步</summary><form data-edit="${s.id}"><div class="edit-fields"><label>数量<input name="quantity" type="number" min="1" max="999999" value="${slots.quantity?.value??''}" placeholder="未明确"></label><label>条件类别<select name="conditionKind"><option value="prerequisite" ${s.condition?.kind==='prerequisite'?'selected':''}>执行前提</option><option value="player-claim" ${s.condition?.kind==='player-claim'?'selected':''}>玩家陈述</option></select></label><label class="wide">条件原文（留空撤回）<textarea name="condition" rows="2" maxlength="500">${esc(s.condition?.value??'')}</textarea></label></div><button type="submit">应用本步修改</button></form></details><details><summary>原文依据</summary><blockquote>${esc(s.quote)}</blockquote><pre>${esc(JSON.stringify({origin:s.origin,slots:s.atomic.intent,condition:s.condition,lastRevision:s.lastRevision},null,2))}</pre></details></article>`;
 }).join(''):'<div class="empty"><strong>从一段明确的计划开始</strong><p>先使用下方完整样例，再试着修改第二步或撤回一个行动。计划会自动保留在这个独立预览中。</p></div>';
 $('#samples').innerHTML=state.samples.map(t=>`<button type="button" data-sample="${esc(t)}">${esc(t)}</button>`).join('');
 $('#saved').textContent=p?.saved?'当前第 '+p.revision+' 版已保存为只读计划。下载不派发请求，也不执行。':'尚未保存当前版本；输入和修订记录已自动保留。';
 $('#history').innerHTML=state.history.length?state.history.slice().reverse().map(h=>`<div class="history"><strong>${esc(h.text)}</strong><p class="fine">步骤顺序：${esc(h.orderBefore.join(' → ')||'空')} → ${esc(h.orderAfter.join(' → ')||'空')}</p><details><summary>本次差异</summary><pre>${esc(JSON.stringify(h.diff,null,2))}</pre></details></div>`).join(''):'<p class="muted">尚无修订。</p>';
 $('#evidence').textContent=JSON.stringify(state.operations,null,2);$('#calls').textContent='模拟解释 '+state.simulatedCalls+' 次 · 真实调用 0';
 document.querySelectorAll('button,select,input,textarea').forEach(el=>{el.disabled=busy||!!state.pending;});
 // Restore per-control eligibility after the global in-flight lock.
 if(!busy&&!state.pending){
  document.querySelectorAll('.step').forEach((el,i)=>{el.querySelector('[data-do="up"]').disabled=i===0;el.querySelector('[data-do="down"]').disabled=i===steps.length-1;});
  $('#save').disabled=!steps.length||p.stale||p.orderReview||!!p.question||!!p.limit||!state.sourceReady||steps.some(s=>['clarification','blocked','stale','unsupported'].includes(s.status));
  $('#export').disabled=!p?.saved||p.stale;$('#clear').disabled=!steps.length&&!p?.question&&!p?.limit;
 }
 if(state.pending){$('#refresh').disabled=false;$('#scene').disabled=false;}
}
$('#scene').addEventListener('change',async()=>{adapter=$('#scene').value;$('#input').value='';$('#notice').textContent='';await load();});
$('#compose').addEventListener('submit',async e=>{e.preventDefault();const text=$('#input').value;if(!text.trim())return;await command('reply',{text});});
$('#samples').addEventListener('click',e=>{if(e.target.dataset.sample){$('#input').value=e.target.dataset.sample;$('#input').focus();}});
$('#save').onclick=()=>command('save');$('#clear').onclick=()=>command('clear');
$('#refresh').onclick=async()=>{if(state.pending){state=await api('/api/plan',{kind:'reconcile',adapterId:adapter});render();}else await command('refresh');};
$('#review-order').onclick=()=>command('edit',{text:'确认剩余步骤顺序',ops:[{type:'review-order',quote:'确认剩余步骤顺序'}]});
$('#export').onclick=async()=>{try{const data=await api('/api/export?adapter='+adapter),a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));a.href=url;a.download='e5-plan-preview.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){$('#notice').textContent=e.message;}};
$('#steps').addEventListener('click',e=>{const doWhat=e.target.dataset.do;if(!doWhat)return;const el=e.target.closest('.step'),id=el.dataset.id,index=state.plan.steps.findIndex(s=>s.id===id),text='界面明确'+(doWhat==='remove'?'撤回':'改序')+id;
 const op=doWhat==='remove'?{type:'remove',stepId:id,quote:text}:{type:'move',stepId:id,to:index+(doWhat==='up'?-1:1),quote:text};command('edit',{text,ops:[op]});});
$('#steps').addEventListener('submit',e=>{e.preventDefault();const form=e.target,id=form.dataset.edit;if(!id)return;const fields=new FormData(form),raw=fields.get('quantity'),q=raw===''?null:Number(raw),c=fields.get('condition'),text='界面明确修改 '+id+' 数量 '+raw+'；条件：'+c;
 command('edit',{text,ops:[{type:'revise',stepId:id,action:null,changes:[{field:'quantity',value:q,quote:raw===''?'撤回数量':raw}],condition:c?{kind:fields.get('conditionKind'),value:c,quote:c}:null,quote:text}]});});
load().catch(e=>$('#notice').textContent='读取失败：'+e.message);
