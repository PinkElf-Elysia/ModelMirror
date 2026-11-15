const $=s=>document.querySelector(s);
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state,selected=null,busy=false;
const giftText=n=>'我想赠送周禾'+n+'份养息丹。';
const errors={E6_STALE:'情境已更新，请重新读取后再选择；旧建议不会自动改写。',E6_CONFLICT:'此页已过期，请刷新后继续。',E6_QUANTITY:'请填写正整数数量。',E6_EDITED_TEXT:'文字已修改，请按自由意图保存，不转换成赠送参数。'};
function notice(text){$('#notice').textContent=text;}
async function api(path,data){const r=await fetch(path,data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{});const v=await r.json();if(!r.ok)throw Error(errors[v.error]||v.error);return v;}
async function command(data,success=''){if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);
 try{state=await api('/api/command',{...data,revision:state.revision});render();if(success)notice(success);return true;}
 catch(e){notice(e.message);return false;}
 finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);}}
function render(){
 const f=state.frame;
 $('#context').innerHTML=f.facts.resources.map(x=>'<div class="resource"><span>'+escape(x.name)+'</span><strong>'+escape(x.quantity??'未设定')+' 份</strong></div>').join('')+
 '<p class="source-note">登记人物：'+f.facts.people.map(x=>escape(x.name)).join('、')+'</p><p class="muted">'+escape(f.coverage)+'</p><span class="tag">本地状态 · 未执行行动</span>';
 const r=state.result;
 $('#results').innerHTML=!r?'<p class="muted">建议是可选的。你也可以直接写下自己的做法。</p>':
 (state.stale?'<p class="warning">情境已变化，旧建议需要重新核对。</p>':'')+
 (r.question?'<h2>先确认一件事</h2><form id="clarify"><label for="answer">'+escape(r.question)+'</label><input id="answer" maxlength="500" required placeholder="请补充对象"><button type="submit">补充后看看建议</button></form>':'')+
 (r.notice?'<p class="warning">'+escape(r.notice)+'</p>':'')+
 (r.options.length?'<h2>可以考虑这些做法</h2><p class="muted">任选、修改，或全部忽略；它们不是连续步骤。</p>':'')+
 r.options.map(o=>'<article class="suggestion"><h3>'+escape(o.title)+'</h3><p class="why">'+escape(o.reason)+'</p><p>'+escape(o.unknown)+'</p><small>'+escape(o.support)+'</small><button data-pick="'+o.id+'" '+(state.stale?'disabled':'')+'>选择这个方向</button></article>').join('')+
 (r.capabilityNote?'<p class="muted">'+escape(r.capabilityNote)+'</p>':'')+
 (r.options.length?'<button id="ignore" type="button">这些都不选</button>':'');
 $('#saved').innerHTML=state.saved.length?state.saved.slice().reverse().map(x=>'<article class="saved"><span class="tag">已保存 · 未执行</span><p>'+escape(x.text)+'</p><small>'+
 (x.plan?'赠送意图已用 E5 保存；没有执行规则或预检结论。':'自由意图已原样保留；未转换为规则动作。')+'</small><details><summary>选择来源</summary><pre>'+escape(JSON.stringify({origin:x.origin,modelSuggestion:x.modelSuggestion,source:x.source,executionStatus:x.executionStatus},null,2))+'</pre></details></article>').join(''):'<p class="muted">选择一个方向，或写下自己的想法。保存不会扣除物资。</p>';
 $('#trace').textContent=JSON.stringify({mode:state.mode,realCalls:state.realCalls,context:f,request:r?.request??null},null,2);
}
function edit(option){
 selected=option;$('#choice').hidden=false;$('#quantity-row').hidden=option?.kind!=='gift';$('#quantity').value='';
 $('#choice-text').value=option?.text??'';$('#choice-title').textContent=option?'留下你的选择':'写下自己的想法';
 $('#choice-mode').textContent=option?.kind==='gift'?'请自行填写数量。修改下方文字后，将只保存自由意图。':'文字可以直接修改；不会自动推断对象、数量或行动结果。';
 $('#choice-text').focus();
}
$('#goal-form').addEventListener('submit',async e=>{e.preventDefault();selected=null;$('#choice').hidden=true;await command({kind:'suggest',goal:$('#goal').value},'已生成固定模拟建议；请自行选择。');});
$('#example').onclick=()=>{$('#goal').value='我想向他表达谢意。';$('#goal').focus();};
$('#results').addEventListener('submit',async e=>{if(e.target.id!=='clarify')return;e.preventDefault();await command({kind:'suggest',goal:state.result.goal,answer:$('#answer').value},'已根据补充展示模拟建议。');});
$('#results').onclick=async e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.pick&&!state.stale)edit(state.result.options.find(x=>x.id===b.dataset.pick));if(b.id==='ignore'){await command({kind:'dismiss'},'已忽略这些建议，你可以自由输入。');edit(null);}};
$('#quantity').oninput=()=>{const n=Number($('#quantity').value);if(Number.isSafeInteger(n)&&n>0)$('#choice-text').value=giftText(n);};
$('#choice-text').oninput=()=>{$('#choice-mode').textContent='已编辑文字；若与数量选择不一致，将原样保存为自由意图，不自动转换。';};
$('#save-form').onsubmit=async e=>{e.preventDefault();const text=$('#choice-text').value,n=Number($('#quantity').value);
 const data={kind:'save',text,optionId:selected?.id};
 if(selected?.kind==='gift'&&text===giftText(n)&&$('#quantity').value)data.quantity=n;
 if(await command(data,'意图已保存，尚未执行；世界物资没有变化。')){selected=null;$('#choice').hidden=true;}};
$('#free').onclick=()=>edit(null);$('#cancel-choice').onclick=()=>{selected=null;$('#choice').hidden=true;};
$('#refresh').onclick=async()=>{try{state=await api('/api/view');selected=null;$('#choice').hidden=true;render();notice(state.stale?'情境已更新，请重新生成建议。':'情境已重新读取；未触发模型调用。');}catch(e){notice(e.message);}};
try{state=await api('/api/view');render();}catch(e){notice('无法读取独立情境：'+e.message);}
