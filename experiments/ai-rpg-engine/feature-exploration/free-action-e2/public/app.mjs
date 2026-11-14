const $=id=>document.getElementById(id),esc=x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
let view=null,busy=false,dirty=false;
const labels={review:'待核对',clarification:'需要澄清',blocked:'条件不足',awaiting_execution:'已确认，待执行',revoked:'已撤销确认',cancelled:'已取消'};
const messages={SOURCE_STALE:'源状态已经变化，请刷新后重新拟定。',SOURCE_TURN_PENDING:'源会话有尚未结束的回合，暂不能生成或确认。',SOURCE_UNAVAILABLE:'来源服务不可用，保留现有草稿，请恢复来源后核对。',PROPOSAL_SELECTION_IGNORED:'模拟提案未覆盖全部所选对象，请调整选择。',DRAFT_CONFLICT:'草稿已变化，请刷新核对。'};
async function api(path,data){const r=await fetch(path,data?{method:'POST',headers:{'content-type':'application/json','x-rpg-e2':'proposal-only'},body:JSON.stringify(data)}:{});const j=await r.json();if(!r.ok)throw Error(messages[j.error]||j.error);return j;}
function notice(s){$('notice').textContent=s;}
async function run(fn){if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);try{await fn();}catch(e){notice(e.message);}finally{busy=false;$('refresh').disabled=false;render();}}
const selected=()=>[...document.querySelectorAll('#objects input:checked')].map(e=>e.value);
function render(){
 if(!view)return;const w=view.world,p=view.draft?.current,selection=selected();
 $('profile').textContent=w.state.profile.name+' · '+w.state.traits.age+'岁 · '+w.state.traits.affiliation+' · '+w.state.traits.realm;
 $('version').textContent='状态 v'+w.version;$('source-kind').textContent=w.provenance.label;
 $('source-data').textContent=JSON.stringify({session:w.session,branch:w.branch,revision:w.revision,stateRevision:w.version,source:w.provenance,binding:w.binding,state:w.state},null,2);
 $('objects').innerHTML=['items','relations','abilities'].flatMap(domain=>w.state[domain].map(e=>'<label class="object"><input type="checkbox" value="'+domain+':'+esc(e.id)+'" '+(selection.includes(domain+':'+e.id)?'checked':'')+'><span class="object-copy"><span class="object-title">'+esc(e.name)+'<span class="object-count">'+(domain==='items'?(e.quantity??'未设定')+' 份':domain==='relations'?'关系 '+e.relation:'')+'</span></span><small>'+esc(domain==='items'?e.condition:domain==='relations'?e.type:e.progress)+'</small></span></label>')).join('');
 $('samples').innerHTML=view.samples.map((s,i)=>'<button type="button" data-sample="'+i+'">'+['拟赠丹药','澄清对象','帮忙与交谈','未知能力'][i]+'</button>').join('');
 $('evidence').textContent=JSON.stringify(view.draft?{request:view.draft.request,receipt:view.draft.receipt,localEdit:p.localEdit??null}: {note:'生成后显示冻结请求与模拟原文。'},null,2);
 const warning=view.stale?'来源已变化，旧方案不能确认或导出。':w.pending?'来源仍有未完成回合。':dirty?'输入或对象已修改，请重新生成提案。':'';
 if(p){
  const list=a=>'<ul>'+a.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>';
  $('proposal').innerHTML='<div class="proposal-head"><span class="state-label">'+esc(labels[p.status])+'</span><small>修订 '+p.revision+'</small></div><h2 class="goal">'+esc(p.goal)+'</h2><ol>'+p.steps.map(s=>'<li>'+esc(s)+'</li>').join('')+'</ol>'+
   (p.questions.length?'<div class="notice-box">'+list(p.questions)+'</div>':'')+
   (p.diagnostics.length?'<div class="diagnostics">'+list(p.diagnostics)+'</div>':'')+
   '<p class="projection-label">条件预估，尚未发生</p>'+
   (p.expected.length?'<table class="changes"><thead><tr><th>物资</th><th>实际</th><th>拟用</th><th>若执行后</th></tr></thead><tbody>'+p.expected.map(e=>'<tr><td>'+esc(e.name)+'</td><td>'+e.before+'</td><td>'+e.allocated+'</td><td>'+e.after+'</td></tr>').join('')+'</tbody></table>':'<p class="hint">没有拟议的物资数量变化。</p>')+
   (p.requirements.length===1?'<div class="amount-edit"><label>改为拟使用数量<input id="quantity" type="number" min="1" step="1" value="'+p.requirements[0].quantity+'"></label><button id="recalculate" type="button">本地重算</button></div><p class="hint">只修订数量，不重新模拟语义，不改变实际库存。</p>':'')+
   '<details class="basis" open><summary>依据与限制</summary>'+list(p.basis.map(x=>x.name+' · '+x.id+' · 状态 v'+x.stateRevision))+list(p.limitations)+'</details>';
  $('handoff').innerHTML='<p class="status-copy">'+(p.status==='awaiting_execution'?'方案已经确认，<strong>尚未执行</strong>。实际丹药、人物关系及故事历史都没有变化。':'确认表示保留这个方案。正式行动仍需后续宿主重新校验。')+'</p>'+
   (warning?'<p class="notice-box">'+esc(warning)+'</p>':'')+
   '<div class="actions"><button class="primary" id="confirm">确认方案，暂不执行</button><button id="revoke">撤销确认</button><button id="cancel">取消提案</button><button id="export">导出交接</button><button id="reconcile">核对保存结果</button></div>'+
   '<p class="source-meta">修订 '+p.revision+'<br>来源 '+esc(w.session)+'<br>状态版本 '+p.source.version+'</p>';
 }else{$('proposal').innerHTML='<div class="empty"><strong>先选择对象，再拟定行动</strong>试试“拟赠丹药”，核对实际数量和条件预估。</div>';$('handoff').innerHTML='<p class="status-copy">还没有提案。来源读取失败时不会用固定初值代替。</p>';}
 $('refresh').disabled=busy;$('generate').disabled=busy||!!w.pending||!w.enabled;
 for(const id of ['confirm','export','recalculate'])if($(id))$(id).disabled=busy||dirty||view.stale||!!w.pending||!w.enabled|| (id==='confirm'?p.status!=='review':id==='export'?p.status!=='awaiting_execution':false);
 if($('revoke'))$('revoke').disabled=busy||p.status!=='awaiting_execution';
 if($('cancel'))$('cancel').disabled=busy||p.status==='awaiting_execution';
}
async function load(){view=await api('/api/view');dirty=false;if(view.draft)$('intent').value=view.draft.current.input.text;notice('');render();}
$('refresh').onclick=()=>run(load);
$('intent').oninput=()=>{dirty=true;render();};
$('objects').onchange=()=>{dirty=true;render();};
$('samples').onclick=e=>{const i=e.target.dataset.sample;if(i===undefined)return;$('intent').value=view.samples[Number(i)];document.querySelectorAll('#objects input').forEach(x=>x.checked=false);dirty=true;render();};
$('composer').onsubmit=e=>{e.preventDefault();run(async()=>{const result=await api('/api/draft',{command:'revise',input:{text:$('intent').value,selection:selected()},expectedHash:view.recordHash});view={...view,...result};dirty=false;notice('模拟提案已保存。实际状态未变。');});};
document.addEventListener('click',e=>{
 const id=e.target.id,p=view?.draft?.current;
 if(['confirm','revoke','cancel'].includes(id))run(async()=>{view={...view,...await api('/api/draft',{command:id,expectedHash:view.recordHash,proposalHash:p.hash})};notice('仅更新提案记录，未执行行动。');});
 if(id==='recalculate')run(async()=>{const qty=Number($('quantity').value);view={...view,...await api('/api/draft',{command:'quantity',quantity:qty,expectedHash:view.recordHash,proposalHash:p.hash})};dirty=false;notice('数量已在本地修订，请重新确认。');});
 if(id==='reconcile')run(async()=>{view={...view,...await api('/api/draft',{command:'reconcile'})};notice('保存记录已核对，未生成或执行行动。');});
 if(id==='export')run(async()=>{const data=await api('/api/export'),a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));a.href=url;a.download='e2-proposal-not-executed.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notice('已导出待执行交接，不包含任何执行授权。');});
});
run(load);
