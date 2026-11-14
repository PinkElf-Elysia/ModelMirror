const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const op=()=> 'op-'+crypto.randomUUID(),pretty=x=>JSON.stringify(x,null,2);
let bootstrap,record=null,source={kind:'example',id:'adult-fixture',history:[]},busy=false;
const errors={STATE_INSUFFICIENT:'余额或数量不足，本轮未入账。',STATE_STALE:'状态已变化，请刷新核对后重新确认。',STATE_REPLY_INCOMPLETE:'回复被截断，本轮正文与变化均未提交。',STATE_RESULT_UNKNOWN:'结果尚未确认，请核对原操作，不要重发。',STATE_INITIAL_VALUE_REQUIRED:'涉及的数量尚未初始化，请明确设置后再操作。',STATE_CONFIG_CONFLICT:'配置与本地状态维护冲突，请移除重复的面板或状态输出要求。',STATE_INITIAL_CONFIRMATION_REQUIRED:'请先核对并确认开局初值。',STATE_DISABLED:'本地状态模式已停用；档案保留，续写暂停。',STATE_STALE_CANDIDATE:'候选对应的状态已变化，请重新预览。'};
function notice(message=''){$('notice').textContent=message;}
async function api(path,data){
 const res=await fetch(path,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json','X-Local-State':'preview'},body:data===undefined?undefined:JSON.stringify(data)});
 const out=await res.json();if(!res.ok)throw Error(errors[out.error]||out.error);return out;
}
async function task(fn){if(busy)return;busy=true;notice();$('send').disabled=true;try{await fn();}catch(e){notice(e.message);}finally{busy=false;syncControls();}}
function syncControls(){const blocked=busy||!record||!!record.pending||!record.enabled;$('send').disabled=blocked;$('compare').disabled=blocked;$('input').disabled=!!record&&(!!record.pending||!record.enabled);}
function modal(title,body){$('dialog-title').textContent=title;$('dialog-body').innerHTML=body;$('dialog').showModal();}
$('close-dialog').onclick=()=>$('dialog').close();
$('help').onclick=()=>modal('这次预览验证什么？','<p>这里使用固定的中性模拟回复，不调用真实模型，也不表示正文质量已经提升。选择样例动作可以检验整条链路：开局确认 → 一次回复 → 增量校验与计算 → 本地面板 → 下一回合。</p><p>程序负责灵石、贡献点、物品数量、关系与信任的计算；修炼、疲惫、伤势、人物反应仍属于模型判断。没有额外的升级、伤害或恢复公式。</p><p>所有原始回复保存在独立目录。候选仅预览，采用时才提交。结果未知请“核对原操作”；截断或非法增量不会入账。明确失败后可以放弃整条回复，不自动重试。</p><p>“档案工具”可以明确改写、按检查点建立分支或停用。停用保留数据，并暂停此适配分支续写。原版卡片与共享存档未改动。</p>');
function setup(){
 record=null;source={kind:'example',id:'adult-fixture',history:[]};history.replaceState(null,'','/');
 $('play').hidden=true;$('setup').hidden=false;$('confirmed').checked=false;
 $('initial-json').value=pretty(bootstrap.state);$('config-json').value=pretty({profiles:[],mods:[]});$('source-note').textContent='当前来源：原创中性样例';fieldsFromJSON();
}
function fieldsFromJSON(){const s=JSON.parse($('initial-json').value);$('init-name').value=s.profile.name??'';$('init-age').value=s.profile.initialAge??'';$('init-low').value=s.balances.low??'';$('init-medicine').value=s.items.find(i=>i.id==='medicine')?.quantity??'';}
for(const id of ['init-name','init-age','init-low','init-medicine'])$(id).onchange=()=>{
 try{const s=JSON.parse($('initial-json').value);s.profile.name=$('init-name').value||null;s.profile.initialAge=$('init-age').value||null;s.traits.age=s.profile.initialAge;
 s.balances.low=$('init-low').value===''?null:Number($('init-low').value);const item=s.items.find(i=>i.id==='medicine');if(item)item.quantity=$('init-medicine').value===''?null:Number($('init-medicine').value);
 $('initial-json').value=pretty(s);}catch(e){notice('初值 JSON 无法解析：'+e.message);}
};
$('initial-json').onchange=()=>{try{fieldsFromJSON();}catch(e){notice(e.message);}};
$('new-session').onclick=()=>{if(!record||confirm('当前记录仍会保留，另建一个独立档案？'))setup();};
$('example-config').onclick=()=>{
 const c=structuredClone(bootstrap.profile);c.prefix='保持第二人称，沿用药圃场景。';c.suffix='只描写本轮实际发生的事情。';
 c.rules=[{id:'medicine-display',name:'仅显示用词',pattern:'养息丹',replacement:'药丸',scope:'assistant',enabled:true,renderOnly:true,flags:'g'}];
 c.worldbook.entries=[{id:'garden',name:'药圃约定',categoryId:null,keywords:['药圃'],body:'药圃的竹架由同门共同维护。',depth:1,order:0,mode:'regular',enabled:true,regex:false,chance:100,contents:[],roles:['user','assistant'],targets:['system'],rule:'or'}];
 $('config-json').value=pretty({profiles:[c],mods:['common.rationality.v1']});
};
$('import').onchange=()=>task(async()=>{
 const file=$('import').files[0];if(!file)return;const data=JSON.parse(await file.text()),r=data.record||data;
 if(r.pending)throw Error('源记录有未确认操作，请先在原预览核对。');
 const state=r.state||r;source={kind:'confirmed-import',id:'import-'+crypto.randomUUID(),history:r.turns?[...(r.source?.history||[]),...r.turns.map(t=>({input:t.input,body:t.body}))]:[]};
 $('initial-json').value=pretty(state);$('config-json').value=pretty(r.config||{profiles:[],mods:[]});fieldsFromJSON();$('confirmed').checked=false;$('source-note').textContent='来源：'+file.name+'；将建立独立新分支，原文件不变。';
});
$('create').onclick=()=>task(async()=>{
 if(!$('confirmed').checked)throw Error(errors.STATE_INITIAL_CONFIRMATION_REQUIRED);
 const r=await api('/api/sessions',{operationId:op(),initial:JSON.parse($('initial-json').value),confirmed:true,source,config:JSON.parse($('config-json').value)});
 await load(r.id);
});
function changeLabel(path){
 const parts=path.split('.'),names={quantity:'数量',progress:'掌握程度',condition:'状态',relation:'关系',trust:'信任',low:'下品灵石',mid:'中品灵石',high:'上品灵石',top:'极品灵石',contribution:'宗门贡献点',stamina:'体力',injury:'伤势',mental:'精神',energy:'灵力',realm:'境界',storage:'储物装备',natal:'本命法宝',external:'外物法宝',defense:'防御法宝'};
 const entry=record?.state[parts[0]]?.find?.(e=>e.id===parts[1]);
 return (entry?.name||names[parts[1]]||({abilities:'能力档案',items:'物资档案',relations:'人物档案',traits:'动态状态',marks:'外貌印记','explicit-override':'明确改写'})[parts[0]]||'档案')+(parts[2]?' · '+(names[parts[2]]||'资料'):'');
}
function changeValue(v){if(v===null)return '无';if(typeof v!=='object')return String(v);return v.name? v.name+(v.quantity!==undefined?' × '+(v.quantity??'未设定'):'')+(v.progress?' · '+v.progress:''):'已更新资料';}
function changeHTML(changes){return '<ul class="change-list">'+changes.map(c=>'<li><strong>'+esc(changeLabel(c.path))+'</strong><br>'+esc(changeValue(c.before))+' → '+esc(changeValue(c.after))+'<small>'+esc(({ 'rule-calculation':'规则计算','model-judgment':'模型判断','user-override':'明确改写'})[c.authority]||c.authority)+' · '+esc(c.reason)+'</small></li>').join('')+'</ul>';}
async function load(id){
 record=await api('/api/sessions/'+id);history.replaceState(null,'','/?session='+id);render();
}
function render(){
 $('setup').hidden=true;$('play').hidden=false;$('session-title').textContent=(record.state.profile.name||'未命名修士')+'的手记';
 $('session-meta').textContent=(record.state.traits.affiliation||'所属未设定')+' · '+(record.state.traits.realm||'境界未设定')+' · '+(record.enabled?'本地状态已启用':'已停用，档案保留');
 $('state-version').textContent='状态 v'+record.stateRevision;$('panels').innerHTML=record.panels;
 const qty=record.state.items.find(i=>i.id==='medicine')?.quantity,rel=record.state.relations.find(r=>r.id==='zhou')?.relation;
 $('metrics').innerHTML=[[qty,'养息丹'],[record.state.balances.low,'下品灵石'],[rel,'与周禾关系']].map(([n,label])=>'<div><strong>'+esc(n??'—')+'</strong><small>'+label+'</small></div>').join('');
 const last=record.events.at(-1);$('changes').innerHTML=last?'<details open><summary>本轮已提交变化</summary>'+changeHTML(last.changes)+'</details>':'<p class="footnote">已确认开局初值，等待第一步行动。</p>';
 $('story').innerHTML=(record.inheritedTurns||[]).map(t=>'<article class="turn"><p class="turn-input">继承正文 · '+esc(t.input)+'</p><div class="narrative">'+t.html+'</div></article>').join('')+'<article class="turn"><p class="turn-input">序 · 已确认开局</p><div class="narrative"><p>'+esc(record.opening)+'</p></div></article>'+
 record.turns.map((t,i)=>'<article class="turn"><p class="turn-input">'+String(i+1).padStart(2,'0')+' / '+esc(t.input)+'</p><div class="narrative">'+t.html+'</div><details><summary>本轮变化与原始回复</summary>'+changeHTML(t.changes)+'<button data-raw="'+esc(t.id)+'">查看原始回复</button></details></article>').join('');
 document.querySelectorAll('[data-raw]').forEach(b=>b.onclick=()=>{const saved=record.operations[b.dataset.raw];modal('原始模拟回复',saved?'<p class="muted">只读；协议区块未进入后续正文历史。</p><pre>'+esc(saved.receipt?.text||saved.error)+'</pre>':'<p>此回合来自检查点。正文和状态已保留；完整原回执请查看源会话导出。</p>');});
 const p=record.pendingOperation;$('pending').hidden=!p;if(p){
 $('pending').innerHTML='<p><strong>本轮尚未完成</strong> · '+esc(({unknown:'结果未知',pending:'等待结果',received:'待提交',failed:'明确失败',rejected:'未通过校验'})[p.status]||p.status)+'</p><p>'+esc(errors[p.error]||p.error||'正在等待原操作结果')+'</p><button id="recover">核对原操作</button>'+(['failed','rejected'].includes(p.status)?'<button id="discard">放弃未完成回复</button>':'')+'<button id="pending-raw">查看记录</button>';
 $('recover').onclick=()=>task(async()=>{await api('/api/sessions/'+record.id+'/recover',{});await load(record.id);if(!record.pending)$('input').value='';});
 if($('discard'))$('discard').onclick=()=>task(async()=>{await api('/api/sessions/'+record.id+'/discard',{operationId:op(),revision:record.revision,pendingId:record.pending,reason:'用户在独立预览放弃未完成回复'});await load(record.id);});
 $('pending-raw').onclick=()=>modal('未完成操作记录','<pre>'+esc(pretty(p))+'</pre>');
 }syncControls();
}
async function send(options={}){
 const input=$('input').value.trim();if(!input)throw Error('请填写行动。');
 await api('/api/sessions/'+record.id+'/turn',{operationId:op(),revision:record.revision,input,options});await load(record.id);if(!record.pending)$('input').value='';
}
$('composer').onsubmit=e=>{e.preventDefault();task(()=>send());};
document.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>{$('input').value=b.dataset.action;$('input').focus();});
$('scenario').onchange=()=>{if($('scenario').value)$('input').value=$('scenario').value;};
$('refresh').onclick=()=>task(()=>load(record.id));
$('export').onclick=()=>task(async()=>{const proof=await api('/api/sessions/'+record.id+'/evidence'),url=URL.createObjectURL(new Blob([pretty(proof)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=record.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
$('compare').onclick=()=>task(async()=>{
 const input=$('input').value.trim();if(!input)throw Error('请先填写行动。');
 const candidates=[];for(const variant of ['calm','warm'])candidates.push(await api('/api/sessions/'+record.id+'/preview',{input,variant}));
 modal('候选预览 · 尚未入账','<p class="muted">两份固定模拟文案。采用一份后才提交变化；不会重复消耗。</p><div class="candidates">'+candidates.map((c,i)=>'<section class="candidate"><h3>候选 '+(i+1)+'</h3><div class="narrative">'+c.html+'</div><details><summary>变化预览</summary>'+changeHTML(c.changes)+'</details><button class="primary" data-adopt="'+i+'">采用此回复</button></section>').join('')+'</div>');
 document.querySelectorAll('[data-adopt]').forEach(b=>b.onclick=()=>task(async()=>{const c=candidates[Number(b.dataset.adopt)];$('input').value=c.input;await send({variant:c.variant,previewHash:c.previewHash});$('dialog').close();}));
});
$('tools').onclick=()=>{
 modal('档案工具','<p class="muted">操作仅影响当前适配分支。明确改写会保留前后值与原因。</p><div class="tool-row"><label>从检查点创建新分支<select id="checkpoint"><option value="">开局初值</option>'+record.events.map((e,i)=>'<option value="'+esc(e.id)+'">#'+(i+1)+' '+esc(e.kind==='override'?'明确改写':record.turns.find(t=>t.id===e.id)?.input||e.kind)+'</option>').join('')+'</select></label><button id="fork">创建分支</button></div><details><summary>明确改写状态</summary><label>目标状态 JSON<textarea id="rewrite-state" rows="12" spellcheck="false">'+esc(pretty(record.state))+'</textarea></label><label>改写原因<input id="rewrite-reason" maxlength="500"></label><button id="diff">查看前后差异</button><pre id="rewrite-diff" class="diff" hidden></pre><button id="apply-rewrite" disabled>确认改写</button></details><div class="dialog-actions"><button id="toggle">'+(record.enabled?'停用并保留档案':'重新启用')+'</button><button id="request-view">查看实际装配与记忆任务</button></div><details class="diagnostics"><summary>离线故障样例</summary><p class="muted">只对模拟供应商生效。使用聊天输入框中的行动，不自动重试。</p><button data-fault="truncated">模拟截断</button> <button data-fault="unknown">模拟结果未知</button> <button data-fault="failed">模拟明确失败</button></details>');
 $('fork').onclick=()=>task(async()=>{const r=await api('/api/sessions/'+record.id+'/fork',{operationId:op(),revision:record.revision,eventId:$('checkpoint').value||null});$('dialog').close();await load(r.id);});
 let reviewed=null;
 $('diff').onclick=()=>{try{const next=JSON.parse($('rewrite-state').value),lines=[];function walk(a,b,path){if(JSON.stringify(a)===JSON.stringify(b))return;if(a&&b&&!Array.isArray(a)&&!Array.isArray(b)&&typeof a==='object'&&typeof b==='object'){for(const key of new Set([...Object.keys(a),...Object.keys(b)]))walk(a[key],b[key],path+'.'+key);}else lines.push(path+': '+JSON.stringify(a)+' → '+JSON.stringify(b));}walk(record.state,next,'状态');$('rewrite-diff').textContent=lines.join('\n')||'没有变化';$('rewrite-diff').hidden=false;reviewed=$('rewrite-state').value;$('apply-rewrite').disabled=!lines.length;}catch(e){notice(e.message);}};
 $('rewrite-state').oninput=()=>{$('apply-rewrite').disabled=true;reviewed=null;};
 $('apply-rewrite').onclick=()=>task(async()=>{if(reviewed!==$('rewrite-state').value)throw Error('请先检查前后差异。');await api('/api/sessions/'+record.id+'/override',{operationId:op(),revision:record.revision,state:JSON.parse(reviewed),reason:$('rewrite-reason').value});$('dialog').close();await load(record.id);});
 $('toggle').onclick=()=>task(async()=>{await api('/api/sessions/'+record.id+'/enabled',{operationId:op(),revision:record.revision,enabled:!record.enabled});$('dialog').close();await load(record.id);});
 $('request-view').onclick=()=>task(async()=>{const e=await api('/api/sessions/'+record.id+'/evidence'),last=Object.values(e.record.operations).filter(o=>o.kind==='turn').at(-1);modal('实际装配 · 只读','<p class="muted">这里展示冻结请求与待执行的总结／记忆输入；没有调用真实模型或记忆模型。</p><pre>'+esc(pretty({request:last?.context||'尚无回合',memoryTasks:e.memoryTasks}))+'</pre>');});
 document.querySelectorAll('[data-fault]').forEach(b=>b.onclick=()=>task(async()=>{await send({fault:b.dataset.fault});$('dialog').close();}));
};
try{bootstrap=await api('/api/bootstrap');for(const [,name]of bootstrap.scenarios){const option=document.createElement('option');option.value=name;option.textContent=name;$('scenario').append(option);}const id=new URL(location.href).searchParams.get('session');if(id)await load(id);else setup();}catch(e){notice(e.message);}
