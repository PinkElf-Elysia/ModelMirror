const $=id=>document.getElementById(id),labels={target:'赠送对象',item:'丹药种类',quantity:'拟赠数量',condition:'玩家条件'},statuses={clarification:'需要澄清',review:'待核对',accepted:'已接受 · 未执行',blocked:'条件不足',unsupported:'暂不支持',cancelled:'已取消'};
let view,busy=false;
const node=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
const message=e=>({DRAFT_CONFLICT:'草稿已变化，请刷新后再修改。',SOURCE_STALE:'来源状态已变化，请先重新核对。',REQUEST_UNKNOWN:'上次请求结果未知，请先核对记录；本页不会重发。',PROPOSAL_NOT_READY:'请先补齐信息并核对条件。',DRAFT_CANCELLED:'此计划已取消，请开始新计划。',UNSUPPORTED_PLAN:'当前目标不支持，请明确开始新计划。'}[e]||e);
async function load(){const r=await fetch('/api/view');const v=await r.json();if(!r.ok)throw Error(v.error);view=v;render();}
function showSlot(k,slot){if(!slot)return '未明确';if(k==='target')return view.world.state.relations.find(x=>x.id===slot.value)?.name??'引用失效';if(k==='item')return view.world.state.items.find(x=>x.id===slot.value)?.name??'引用失效';return String(slot.value);}
function list(title,values,parent){if(!values.length)return;parent.append(node('h3',title));const ul=node('ul','');values.forEach(v=>ul.append(node('li',v)));parent.append(ul);}
function control(){const k=$('field').value,slot=view?.draft?.current?.intent[k];let c;
 if(['target','item'].includes(k)){c=document.createElement('select');c.append(new Option('请选择，不默认绑定',''));for(const e of (k==='target'?view.world.state.relations:view.world.state.items.filter(x=>x.kind==='丹药')))c.append(new Option(e.name,e.id));}
 else{c=document.createElement('input');c.type=k==='quantity'?'number':'text';if(k==='quantity'){c.min='1';c.step='1';}}
 c.id='value';c.value=slot?.value??'';$('value-control').replaceChildren(c);
}
function render(){
 const p=view.draft?.current,pending=!!view.draft?.pending;
 $('status').textContent=pending?'结果待核对':view.stale?'来源已变化':statuses[p?.status]??'尚未开始';
 $('revision').textContent=p?'版本 '+p.revision:'尚未开始';$('empty').hidden=!!p;
 $('source-label').textContent=(view.world.provenance?.label??'独立测试来源')+' · 状态版本 '+view.world.version;
 $('source-items').replaceChildren(...view.world.state.items.map(x=>node('span',x.name+'：'+(x.quantity??'未设定')+' 份')));
 $('footer').textContent='模拟生成 '+view.simulatedCalls+' 次 · 真实调用 0 次 · 当前页面始终未执行行动';
 const current=$('current');current.replaceChildren();
 if(p){
  const dl=node('dl','');for(const k of Object.keys(labels)){dl.append(node('dt',labels[k]));const dd=node('dd',showSlot(k,p.intent[k]));if(p.intent[k]){const s=p.intent[k].source;const small=node('small',(s.kind==='model-interpretation'?(p.status==='accepted'?'模拟解释，已接受此版本':'模拟解释，待核对'):'用户明确修改')+' · 原文：'+s.quote);small.className='provenance';dd.append(small);}dl.append(dd);}current.append(dl);
  if(p.questions.length){const box=node('div','');box.className='questions';list('还需澄清',p.questions.map(q=>q.text),box);current.append(box);}
  list('世界条件仍未核实',p.worldConditions.map(c=>c.text),current);list('拟议步骤',p.steps,current);list('需要处理',p.diagnostics,current);
  for(const e of p.expected){const n=node('p','条件预估：当前 '+e.before+' 份，拟赠 '+e.requested+' 份，若交付成立则余 '+e.conditionalAfter+' 份。实际库存未变。');n.className='estimate';current.append(n);}
  list('本次修订',p.diff.map(d=>labels[d.field]+'：'+d.before+' → '+d.after),current);
  if(p.confirmation)current.append(node('p','已接受版本 '+p.confirmation.revision+' 的意图。没有执行授权，也没有发出邀请或交付物品。'));
 }
 const turns=$('turns');turns.replaceChildren();for(const t of view.draft?.turns??[]){const el=node('div','');el.className='turn';el.append(node('b','版本 '+t.revision+' · '+(t.kind==='reply'?'你的回复':'本地操作')),node('p',t.kind==='reply'?t.text:({edit:'界面明确修改',accept:'接受当前方案，暂不执行',revoke:'撤销接受',cancel:'取消计划',new:'开始新计划',refresh:'按当前来源重新核对'}[t.kind]??t.text)));turns.append(el);}turns.scrollTop=turns.scrollHeight;
 const ops=Object.values(view.draft?.operations??{}),last=ops.at(-1);
 if(last?.error)$('notice').textContent='本次未形成有效修订：'+message(last.error)+'。上一有效信息已保留。';
 if(pending)$('notice').textContent='请求结果未知。请保留记录并核对；刷新、核对记录均不会重发。';
 $('evidence').replaceChildren();for(const op of ops.filter(x=>x.request).slice(-3)){
  const d=node('details','');d.append(node('summary','模拟请求 · '+op.status),node('pre',JSON.stringify({input:op.request.input,carry:op.request.carry,raw:op.receipt?.raw,error:op.error},null,2)));$('evidence').append(d);
 }
 $('history').replaceChildren(...(view.draft?.revisions??[]).slice().reverse().map(r=>{const el=node('div','版本 '+r.revision+' · '+(statuses[r.status]??r.status)+' · '+Object.keys(labels).map(k=>labels[k]+': '+showSlot(k,r.intent[k])).join('；'));el.className='history-entry';return el;}));
 $('samples').replaceChildren(...view.samples.map(s=>{const b=node('button',s);b.type='button';b.onclick=()=>{$('reply').value=s;$('reply').focus();};return b;}));
 control();
 document.querySelectorAll('button').forEach(b=>b.disabled=busy);
 for(const id of ['send','clear'])$(id).disabled=busy||pending||p?.status==='cancelled'||p?.unsupported;
 $('edit-form').querySelector('[type=submit]').disabled=$('clear').disabled||view.stale;
 $('accept').disabled=busy||pending||view.stale||p?.status!=='review';
 $('revoke').disabled=busy||pending||p?.status!=='accepted';
 $('export').disabled=busy||pending||view.stale||p?.status!=='accepted';
 $('cancel').disabled=busy||pending||!p||p.status==='cancelled';
 $('new').disabled=busy||pending;
 $('rebase').disabled=busy||pending||!view.stale||p?.status==='cancelled';
}
async function send(kind,extra={}){
 if(busy)return;busy=true;$('notice').textContent='';render();
 try{const r=await fetch('/api/draft',{method:'POST',headers:{'Content-Type':'application/json','x-rpg-e3':'proposal-only'},body:JSON.stringify({kind,operationId:crypto.randomUUID(),expectedHash:view.recordHash,...extra})});
 const result=await r.json();if(!r.ok)throw Error(result.error);await load();
 }catch(e){$('notice').textContent=message(e.message);}finally{busy=false;render();}
}
$('reply-form').onsubmit=async e=>{e.preventDefault();const text=$('reply').value.trim();if(!text)return;await send('reply',{text});};
$('field').onchange=control;
$('edit-form').onsubmit=e=>{e.preventDefault();const field=$('field').value,raw=$('value').value;if(!raw.trim()){$('notice').textContent='请选择或填写值；要取消此项请使用清除此项。';return;}send('edit',{changes:[{field,value:field==='quantity'?Number(raw):raw}]});};
$('clear').onclick=()=>send('edit',{changes:[{field:$('field').value,value:null}]});
for(const [id,kind] of [['accept','accept'],['revoke','revoke'],['cancel','cancel'],['new','new'],['rebase','refresh'],['reconcile','reconcile']])$(id).onclick=()=>send(kind);
$('refresh-view').onclick=()=>load().catch(e=>{$('notice').textContent=e.message;});
$('export').onclick=async()=>{try{const r=await fetch('/api/export'),v=await r.json();if(!r.ok)throw Error(v.error);const url=URL.createObjectURL(new Blob([JSON.stringify(v,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='e3-proposal.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){$('notice').textContent=message(e.message);}};
load().catch(e=>{$('notice').textContent='读取失败：'+e.message;});
