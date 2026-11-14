const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let view,busy=false,adapter='cultivation';
const labels={item:'物资',quantity:'拟用数量',target:'拟赠对象',condition:'玩家条件',action:'行动',source:'来源版本'};
const statuses={clarification:'待你补充','rule-ready':'规则预检通过','intent-only':'尚无执行规则',accepted:'已接受意图',blocked:'暂不能预检',stale:'来源已变化',unsupported:'目标尚不支持',cancelled:'已取消'};
const descriptions={clarification:'先补齐下面的信息，不会替你猜测对象或数量。','rule-ready':'当前快照下可形成方案；尚未提交、扣除物资或执行。','intent-only':'可以保留与接受赠送意图，但当前没有赠送执行规则。',accepted:'仅接受这一版意图。导出不会触发执行。',blocked:'保留你的意图，请核对限制或修改方案。',stale:'旧预检和接受已不能用于交接；可继续编辑，或明确重新读取情境。',unsupported:'已保留原文；不会忽略目标限制后改做其他行动。',cancelled:'本方案已取消。请从左侧选择行动开始新方案。'};
const errors={STATE_INSUFFICIENT:'登记数量不足，未自动缩减你的拟用数量。',STATE_INITIAL_VALUE_REQUIRED:'数量未设定，需要先由来源所有者确认。',E4_SOURCE_STALE:'来源已变化，请重新读取情境。',E4_DRAFT_CONFLICT:'草稿已变化，请刷新并核对。',E4_REQUEST_UNKNOWN:'有结果未知的请求，不能再次派发。',E4_CANCELLED:'方案已取消，请选择行动开始新方案。',E4_QUANTITY:'数量必须是正整数。',E4_QUANTITY_EVIDENCE:'这段文字没有提供可核对的明确数量。'};
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-RPG-E4':'proposal-only'}:{},body:body?JSON.stringify(body):undefined});const x=await r.json();if(!r.ok)throw Error(errors[x.error]??x.error);return x;}
function name(k,v){if(v===null||v===undefined)return '未明确';if(k==='item')return view.resources.find(x=>x.id===v)?.name??'引用已失效';if(k==='target')return view.people.find(x=>x.id===v)?.name??'引用已失效';if(k==='action')return view.capabilities.find(x=>x.id===v)?.label??v;return String(v);}
function options(items,value,label){return '<option value="">'+label+'</option>'+items.map(x=>'<option value="'+esc(x.id)+'"'+(x.id===value?' selected':'')+'>'+esc(x.name)+'</option>').join('');}
function render(){
 const d=view.draft?.current,pending=!!view.draft?.pending,state=view.stale?'stale':d?.status;
 $('world').value=adapter;$('scene').textContent=view.title;$('identity').textContent=view.subtitle;
 const rule=view.capabilities.find(x=>x.kind==='rule');
 $('resources').innerHTML=view.resources.map(x=>'<div class="resource"><b>'+esc(x.name)+'</b><strong>'+esc(x.quantity===null?'未设定':x.quantity+' 份')+'</strong><p class="muted">当前登记数量 · '+esc(x.source)+'</p><button data-action="'+esc(rule.id)+'" data-item="'+esc(x.id)+'">'+esc(rule.label)+' · '+esc(x.name)+'</button></div>').join('');
 $('capabilities').innerHTML=view.capabilities.filter(x=>x.kind!=='read').map(c=>'<button data-action="'+esc(c.id)+'">'+esc(c.label)+'<small>'+ (c.kind==='rule'?'已有规则，仅作预检':'只拟意图，尚无执行规则')+'</small></button>').join('');
 $('source-label').textContent=view.sourceLabel;$('coverage').textContent=view.coverage;$('source').textContent=JSON.stringify(view.source,null,2);
 $('status').textContent=pending?'结果待核对':statuses[state]??'尚未开始';
 $('status-detail').textContent=pending?'请求记录已保存；不会自动重发。':descriptions[state]??'从左侧选一项行动，或输入下方的固定模拟样例。';
 const fields=d?.action==='gift'?['item','quantity','target','condition']:['item','quantity'];
 $('slots').innerHTML=d?'<dl><dt>行动</dt><dd>'+esc(name('action',d.action))+'</dd>'+fields.map(k=>'<dt>'+labels[k]+'</dt><dd>'+esc(name(k,d.intent[k]?.value))+(d.intent[k]?'<span class="provenance">'+(d.intent[k].status==='explicit'?'界面明确选择':'模拟解释，待核对')+' · '+esc(d.intent[k].source.quote)+'</span>':'')+'</dd>').join('')+'</dl>':'';
 const qs=[...(d?.questions??[]),...(d?.diagnostics??[]).map(x=>errors[x]??x),...(d?.worldConditions??[]).map(x=>x.text)];
 $('questions').hidden=!qs.length;$('questions').innerHTML='<ul>'+qs.map(q=>'<li>'+esc(q)+'</li>').join('')+'</ul>';
 const pf=!view.stale&&d?.status!=='cancelled'?d?.preflight:null;
 $('preflight').innerHTML=pf?'<div class="estimate"><strong>预检：'+esc(name('item',pf.action.itemId))+' '+pf.expected[0].before+' → '+pf.expected[0].after+' 份</strong><p>拟用 '+pf.action.quantity+' 份；实际登记数量仍为 '+pf.expected[0].before+' 份。</p><p class="muted fine">按已登记的物资消耗规则预检；不承诺额外药效或剧情结果。</p></div>':d?.action==='gift'?'<p class="muted">赠送没有正式规则提案，不会转成服用，也不会把人物登记视为在场或同意。</p>':'';
 $('edit-form').hidden=!d||!d.action||d.status==='cancelled';$('item').innerHTML=options(view.resources,d?.intent.item?.value,'暂不指定物资');$('quantity').value=d?.intent.quantity?.value??'';
 $('target').innerHTML=options(view.people,d?.intent.target?.value,'对象先留空');$('condition').value=d?.intent.condition?.value??'';$('gift-fields').hidden=d?.action!=='gift';
 $('accept').disabled=busy||pending||view.stale||!['rule-ready','intent-only'].includes(d?.status);
 $('revoke').hidden=d?.status!=='accepted';$('export').disabled=busy||pending||view.stale||d?.status!=='accepted';
 $('refresh').hidden=!d||!view.stale;$('cancel').disabled=busy||pending||!d||d.status==='cancelled';
 $('send').disabled=busy||pending||d?.status==='cancelled'||!view.sourceReady;$('world').disabled=busy;
 $('history').innerHTML=(view.draft?.history??[]).slice().reverse().map(h=>'<div class="history-entry"><b>'+esc(h.text)+'</b><p class="fine muted">'+esc(h.kind)+' · '+esc(h.at)+'</p>'+ (h.diff??h.after?.diff??[]).map(c=>'<p>'+esc(labels[c.field]??c.field)+'：'+esc(name(c.field,c.before))+' → '+esc(name(c.field,c.after))+'</p>').join('')+'</div>').join('')||'<p>尚无修订。</p>';
 $('evidence').textContent=JSON.stringify(Object.values(view.draft?.operations??{}).filter(o=>o.request).map(o=>({status:o.status,error:o.error??null,request:o.request,receipt:o.receipt??null})),null,2);
 $('samples').innerHTML=view.samples.map(s=>'<button data-sample="'+esc(s)+'">'+esc(s)+'</button>').join('');
 $('metrics').textContent='真实调用 0 次 · 本草稿模拟解释 '+view.simulatedCalls+' 次';
 for(const b of document.querySelectorAll('[data-action],#edit-form button,#revoke'))b.disabled=busy||pending;
 $('input').disabled=busy||pending;
}
async function load(){view=await api('/api/view?adapter='+adapter);render();}
async function act(kind,extra={}){
 if(busy)return;busy=true;render();$('notice').textContent='';
 try{view=await api('/api/draft',{adapterId:adapter,kind,operationId:'op-'+crypto.randomUUID(),expectedHash:view.recordHash,...extra});
 const failed=Object.values(view.draft?.operations??{}).at(-1);if(failed?.error)$('notice').textContent=errors[failed.error]??failed.error;
 }catch(e){$('notice').textContent=e.message;try{view=await api('/api/view?adapter='+adapter);}catch{}}
 finally{busy=false;render();}
}
document.addEventListener('click',e=>{
 const b=e.target.closest('button');if(!b)return;
 if(b.dataset.sample!==undefined){$('input').value=b.dataset.sample;$('input').focus();}
 if(b.dataset.action){$('inspection').hidden=true;act('select',{action:b.dataset.action,changes:b.dataset.item?[{field:'item',value:b.dataset.item,quote:'点击登记物资'}]:[]});}
});
$('world').addEventListener('change',async()=>{adapter=$('world').value;$('input').value='';$('notice').textContent='';$('inspection').hidden=true;try{await load();}catch(e){$('notice').textContent=e.message;}});
$('reply-form').addEventListener('submit',async e=>{e.preventDefault();const text=$('input').value.trim();if(!text)return;await act('reply',{text});});
$('edit-form').addEventListener('submit',async e=>{e.preventDefault();const d=view.draft.current,values={item:$('item').value||null,quantity:$('quantity').value===''?null:Number($('quantity').value)};
 if(d.action==='gift'){values.target=$('target').value||null;values.condition=$('condition').value.trim()||null;}
 const changes=Object.entries(values).filter(([k,v])=>v!==(d.intent[k]?.value??null)).map(([field,value])=>({field,value,quote:'界面明确修改'}));
 if(!changes.length){$('notice').textContent='没有需要保存的变化。';return;}await act('edit',{changes});
});
for(const [id,kind] of [['accept','accept'],['revoke','revoke'],['refresh','refresh'],['cancel','cancel']])$(id).onclick=()=>act(kind);
$('inspect').onclick=async()=>{try{const q=await api('/api/inspect?adapter='+adapter);$('inspection').hidden=false;$('inspection').open=true;$('query-result').innerHTML=q.result.results.map(r=>'<p><b>'+esc(({ 'complete-registered-scope':'已返回本次登记范围内的全部条目', partial:'仅返回部分登记条目' })[r.coverage]??'登记范围说明')+'</b></p><ul>'+r.entries.map(x=>'<li>'+esc(x.name??x.id)+' · '+esc(x.quantity===null?'数量未设定':x.quantity===undefined?x.value:x.quantity+' 份')+'</li>').join('')+'</ul>').join('')+'<p class="fine">'+esc(view.coverage)+'</p>';}catch(e){$('notice').textContent=e.message;}};
$('export').onclick=async()=>{try{const data=await api('/api/export?adapter='+adapter),url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='e4-intent-'+adapter+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);$('notice').textContent='已导出意图交接预览；没有执行行动。';}catch(e){$('notice').textContent=e.message;}};
load().catch(e=>{$('notice').textContent=e.message;});
