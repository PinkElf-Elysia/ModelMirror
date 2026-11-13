import {mountComparison} from './comparison-ui.mjs';
import {createAuthoringApi} from './browser-api.mjs';
import {createJointClient,parameterEditIntent,pluginIntent,jointTaskIntent} from './joint-client.mjs';
import {typedParameterDraft,switchParameterDraft,typedSelection,typedDraftIssues,typedDraftFingerprint,filteredModels,savedOperation,modelError,profileDraft,presetReceiptMatches} from './model-ui.mjs';
const purposes={story:'剧情',summary:'摘要',memory:'记忆'};
const labels={temperature:'温度',topP:'Top P',maxOutputTokens:'最大输出长度',topK:'Top K',frequencyPenalty:'频率惩罚',presencePenalty:'存在惩罚',stop:'停止词',seed:'Seed','thinking.enabled':'启用思考','thinking.effort':'思考档位','thinking.budgetTokens':'思考预算'};
const basic=['temperature','topP','maxOutputTokens'];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clone=v=>structuredClone(v),fail=code=>{throw {code};};
const normalized=v=>Array.isArray(v)?v.map(normalized):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,normalized(v[k])])):v;
const same=(a,b)=>JSON.stringify(normalized(a))===JSON.stringify(normalized(b));
export function chatParameterDraft(view,purpose,catalog){
 const base=view.session.models.current[purpose],override=view.session.generationParameters.current[purpose];
 const entry=catalog?.models.find(e=>e.selectionId===base?.entry.selectionId&&e.modelId===base?.entry.modelId)??null;
 const draft=typedParameterDraft(entry,override?.parameterProfile.parameters??null);
 return {purpose,entry,catalog,draft,view:clone(view),buffer:'',fingerprint:typedDraftFingerprint(entry,draft),advanced:false,
  incompatible:!!override&&(!base||override.entry.selectionId!==base.entry.selectionId||override.entry.modelId!==base.entry.modelId)};
}
export const chatDraftDirty=d=>!!d&&(!!d.buffer||d.incompatible||typedDraftFingerprint(d.entry,d.draft)!==d.fingerprint);
export function chatDraftIssues(d){
 return [...typedDraftIssues(d.entry,d.draft,d.purpose),...(d.buffer?[{code:'STOP_NOT_ADDED',field:'stop'}]:[]),
  ...(!d.catalog?.models.some(e=>e.selectionId===d.entry?.selectionId&&e.capabilityRevision===d.entry?.capabilityRevision)?[{code:'MODEL_CONTROL_CHANGED',field:''}]:[])];
}
export function addChatStop(d,value){
 const f=d.entry?.parameterPolicy?.fields.stop;
 if(!f||!value||!value.isWellFormed()||[...value].length>f.maxItemLength||d.draft.stop.value.includes(value)||d.draft.stop.value.length>=f.maxItems)fail('STOP_INVALID');
 d.draft.stop.value.push(value);d.buffer='';
}
export function parameterValueText(choice){
 if(!choice)return '尚未选择模型';
 const explicit=Object.entries(choice.parameterProfile?.parameters?.values||{}).filter(([,v])=>v.mode==='explicit');
 return explicit.length?explicit.map(([k,v])=>(labels[k]||k)+' '+(Array.isArray(v.value)?v.value.join('、'):String(v.value))).join(' · '):'使用模型默认值';
}
export function createChatModelClient({api,storage,sessionId,workId}){
 const key='modelmirror.rpg.chat-model-pending/'+sessionId;
 const check=r=>{if(!r||r.workId!==workId||r.kind!=='configure'||r.body?.sessionId!==sessionId||typeof r.body.operationId!=='string')fail('CHAT_LOCAL_RECORD_INVALID');return r;};
 const read=()=>{try{const s=storage.getItem(key);return s?check(JSON.parse(s)):null;}catch{fail('CHAT_LOCAL_RECORD_INVALID');}};
 const write=r=>{try{if(r){const text=JSON.stringify(r);storage.setItem(key,text);if(storage.getItem(key)!==text)throw Error();}else{storage.removeItem(key);if(storage.getItem(key)!==null)throw Error();}}catch{fail('JOINT_LOCAL_STORAGE_UNAVAILABLE');}};
 async function recover(){const r=read();if(!r)return {state:'settled'};
  const result=await api('/model-settings/reconcile',{sessionId,operationId:r.body.operationId,kind:'configure'});
  if(result.view?.session.id!==sessionId||result.view.session.binding.workId!==workId)fail('JOINT_RECEIPT_MISMATCH');
  if(result.found){if(!savedOperation(result.view,r))fail('JOINT_RECEIPT_MISMATCH');write(null);return {state:'settled',view:result.view};}
  return {state:result.view.busy?'unknown':'not_applied',view:result.view};
 }
 return {pending:read,recover,async discard(){if((await recover()).state!=='not_applied')fail('JOINT_RESULT_PENDING');write(null);},async submit(body){
  if(read())fail('JOINT_RESULT_PENDING');const r=check({kind:'configure',workId,body:clone(body)});write(r);
  try{const result=await api('/model-settings/configure',body);if(!same(result.change?.intent,body))fail('JOINT_RECEIPT_MISMATCH');write(null);return result;}
  catch(e){if(e.httpStatus&&e.httpStatus<500&&!/UNKNOWN|STORAGE|WRITE|OWNER|CORRUPT/.test(e.code||''))write(null);throw e;}
 }};
}
const messages={STOP_NOT_ADDED:'请先添加停止词，或清空输入。',STOP_INVALID:'停止词不能为空、重复或超过容量。',PARAMETER_AUTHORIZATION_REQUIRED:'请先在会话插件中启用生成参数。',PARAMETER_MODEL_REQUIRED:'请先选择这个用途的模型。',PARAMETER_MODEL_MISMATCH:'模型已切换，原参数仍保留。请核对后重新应用或恢复默认。',PARAMETER_REVISION_CONFLICT:'其他页面已修改参数。编辑仍保留，请刷新状态后核对。',PARAMETER_AUTHORIZATION_CHANGED:'参数授权已变化。请刷新会话状态。',PLUGIN_NOT_AUTHORIZED:'请先在会话插件中启用对应功能。',PLUGIN_NOT_INSTALLED:'请先安装对应插件。',JOINT_BUDGET_EXHAUSTED:'剩余额度不足以完成剧情及后台整理。',NETWORK_READ_FAILED:'暂时读不到最新状态，请稍后刷新。',NETWORK_RESULT_UNKNOWN:'保存结果尚未确认。请核对原操作，不要重复保存。',CHAT_LOCAL_RECORD_INVALID:'本地恢复记录无法读取，请保留浏览器数据并核对。',SUMMARY_MODEL_REQUIRED:'请先选择摘要模型。',MEMORY_MODEL_REQUIRED:'请先选择记忆模型。',MODEL_SELECTION_REQUIRED:'请先选择剧情模型。',JOINT_RESULT_PENDING:'有操作待核对，请先恢复原记录。',OFFLINE_SESSION_BUSY:'正在处理剧情，结束后才能调整配置。'};
function readable(e){return messages[e?.code]||modelError(e).replace(/ \[[^\]]+\]$/,'');}
export async function mountChatParameters(root,{api=createAuthoringApi('/rpg-app/api/authoring'),storage=localStorage,sessionId}={}){
 if(!/^session-[a-f0-9]{64}$/.test(sessionId||''))fail('OFFLINE_SESSION_NOT_FOUND');
 let view=await api('/joint/sessions/'+sessionId);if(!view.session.generationParameters)fail('CHAT_PARAMETER_HOST_REQUIRED');
 const workId=view.session.binding.workId,client=createJointClient({api,storage,sessionId,workId}),modelClient=createChatModelClient({api,storage,sessionId,workId});
 const draftKey='modelmirror.rpg.chat-input/'+sessionId;
 const runtime=await api('/joint');
 let settings=await api('/joint/settings/'+sessionId),work=await api('/works/'+workId+'/export?kind=configuration&version='+view.session.binding.versionId),busy=false,reading=false,refreshQueued=false,panel=null,sub=null,discard=null,purpose='story',drafts={},status='',timer=null,quoteTimer=null,quoteSeq=0,quote=null,frozenSend=null,presetPending=null;
 const pendingPresetKey='modelmirror.rpg.chat-preset-pending/'+sessionId;
 try{presetPending=JSON.parse(storage.getItem(pendingPresetKey)||'null');}catch{fail('CHAT_LOCAL_RECORD_INVALID');}
 root.innerHTML='<div class="chat-shell"><header class="chat-header"><div><p class="chat-kicker">RPG · 游玩</p><h1></h1></div><button type="button" data-settings aria-haspopup="dialog">会话设置 <span aria-hidden="true">⚙</span></button></header><div class="chat-config"><span data-current></span><button type="button" data-parameters aria-haspopup="dialog">生成参数</button><button type="button" data-refresh>刷新状态</button></div><p class="chat-status" data-status role="status"></p><div data-recovery></div><main class="chat-history" aria-label="正式剧情"></main><form class="chat-composer"><label for="chat-input">你的行动</label><textarea id="chat-input" rows="3" placeholder="写下你接下来的行动…"></textarea><footer><span data-budget></span><button type="submit" class="primary">发送</button></footer></form></div>';
 root.querySelector('h1').textContent=work.document?.metadata.name||'游玩会话';
 const input=root.querySelector('textarea'),send=root.querySelector('[type=submit]');
 try{input.value=storage.getItem(draftKey)??view.session.binding.openingText;}catch{fail('JOINT_LOCAL_STORAGE_UNAVAILABLE');}
 const localPending=()=>client.pending().length||modelClient.pending();
 let comparison=null,comparisonBlocked=false;
 const blocked=()=>comparisonBlocked||busy||reading||view.busy||view.pending||!!settings.unconfirmed||!!localPending();
 const hasDirty=()=>Object.values(drafts).some(chatDraftDirty);
 function report(text){status=text;root.querySelector('[data-status]').textContent=text;if(panel)panel.querySelector('[data-panel-status]').textContent=text;}
 function error(e){report(readable(e));if(panel){const n=panel.querySelector('[data-diagnostic]');n.textContent=e?.code||'NETWORK_RESULT_UNKNOWN';n.parentElement.hidden=false;}}
 function persistInput(){try{storage.setItem(draftKey,input.value);if(storage.getItem(draftKey)!==input.value)throw Error();return true;}catch{report('浏览器无法保存输入草稿，请保留文字后检查存储。');return false;}}
 function render(){
  const actual=view.parameterSettings?.effective?.story;
  root.querySelector('[data-current]').textContent=(actual?.entry.name||view.session.models.current.story?.entry.name||'尚未选择剧情模型')+' · '+(view.parameterSettings?.diagnostic?'参数待核对':parameterValueText(actual));
  root.querySelector('[data-parameters]').hidden=!settings.plugins.parameters?.enabled;
  comparison?.setEnabled(!!settings.plugins.comparison?.enabled);
  root.querySelector('[data-refresh]').disabled=busy||reading;
  root.querySelector('[data-budget]').textContent='剩余 '+view.budget.remaining+' 次'+(quote?' · 本次最多 '+quote.plan.maximumCalls+' 次':'');
  send.disabled=blocked()||!quote||!frozenSend||frozenSend.input!==input.value||quote.budget.remaining<quote.plan.maximumCalls;send.textContent=busy||view.busy?'处理中…':'发送';
  renderHistory();
  const recovery=root.querySelector('[data-recovery]');recovery.innerHTML='';
  if(localPending()||view.pending&&!comparisonBlocked||settings.unconfirmed){recovery.innerHTML='<p>有结果待核对，已保留原操作。</p><button type="button">核对原操作</button>';recovery.querySelector('button').disabled=busy;recovery.querySelector('button').onclick=recover;}
  if(panel)updatePanelState();
 }
 // Keep a single isolated rendering alive. Parameter edits and read-only refreshes
 // do not recreate it, reset folds, or replay display side effects.
 let historyKey=null,frameToken=null,frameEpoch=0,frameBox=null,dead=false;
 async function releaseFrame(){const old=frameToken;frameToken=null;if(old)try{await api('/presentations/release',{token:old});}catch{}}
 async function displayTurn(turn,host,button){
  const epoch=++frameEpoch;await releaseFrame();if(epoch!==frameEpoch||dead)return;
  if(frameBox)frameBox.replaceChildren();frameBox=host;button.disabled=true;host.textContent='正在载入剧情…';
  try{const prepared=await api('/joint/playback/prepare',{sessionId,turnNumber:turn.number,role:'assistant',device:innerWidth<=600?'mobile':'desktop'});
   if(epoch!==frameEpoch||dead||!host.isConnected){await api('/presentations/release',{token:prepared.token});return;}
   frameToken=prepared.token;const frame=document.createElement('iframe');frame.title='第 '+turn.number+' 回合剧情';frame.className='chat-render';frame.setAttribute('sandbox','allow-scripts');frame.referrerPolicy='no-referrer';
   frame.setAttribute('allow',"camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'; fullscreen 'none'");frame.src=prepared.frameUrl;host.replaceChildren(frame);
  }catch{if(epoch===frameEpoch)host.textContent='暂未载入展示，完整原文仍可查看。';}finally{if(button.isConnected)button.disabled=false;}
 }
 function renderHistory(){
  const key=JSON.stringify(view.session.turns);if(historyKey===key)return;historyKey=key;frameEpoch++;void releaseFrame();frameBox=null;
  const history=root.querySelector('.chat-history');history.replaceChildren();
  if(!view.session.turns.length){const empty=document.createElement('section');empty.className='chat-empty';empty.innerHTML='<span aria-hidden="true">✦</span><h2>故事从这里开始</h2><p>开局已放入输入框。你可以先调整会话模型与参数，再发送第一轮。</p>';history.append(empty);return;}
  for(const turn of view.session.turns){
   const user=document.createElement('article');user.className='chat-message user';user.innerHTML='<strong>你</strong><div class="chat-original"></div>';user.lastElementChild.textContent=turn.user;history.append(user);
   const assistant=document.createElement('article');assistant.className='chat-message assistant';assistant.innerHTML='<strong>剧情 · 第 '+turn.number+' 回合</strong><div class="chat-frame-host"></div><details><summary>查看完整原文</summary><div class="chat-original"></div></details>';
   assistant.querySelector('.chat-original').textContent=turn.assistant;history.append(assistant);
   if(runtime.turnPlayback){const b=document.createElement('button');b.type='button';b.className='quiet';b.textContent='显示剧情';assistant.insertBefore(b,assistant.querySelector('details'));b.onclick=()=>displayTurn(turn,assistant.querySelector('.chat-frame-host'),b);
    if(turn===view.session.turns.at(-1))void displayTurn(turn,assistant.querySelector('.chat-frame-host'),b);
   }else assistant.querySelector('details').open=true;
  }
 }
 async function refresh({preserve=true,acknowledge=false}={}){
  if(reading){refreshQueued=true;return;}reading=true;quote=null;frozenSend=null;render();
  try{const [v,s]=await Promise.all([client.view(),api('/joint/settings/'+sessionId)]);view=v;settings=s;
   if(!preserve)drafts={};else if(acknowledge)for(const d of Object.values(drafts))d.view=clone(view);
   render();if(panel&&!sub)await drawPurpose();
  }finally{reading=false;render();clearTimeout(timer);if(refreshQueued){refreshQueued=false;void refresh().catch(error);}else if(view.busy)timer=setTimeout(()=>refresh().catch(error),1500);else scheduleQuote();}
 }
 function scheduleQuote(){clearTimeout(quoteTimer);const seq=++quoteSeq;quote=null;frozenSend=null;render();if(blocked()||!input.value.trim())return;
  quoteTimer=setTimeout(async()=>{const body=jointTaskIntent(view,crypto.randomUUID(),input.value);try{const result=await api('/joint/estimate',body);if(seq!==quoteSeq)return;quote=result;frozenSend=body;render();}catch(e){if(seq===quoteSeq){report(readable(e));render();}}},300);
 }
 async function mutation(action){if(blocked())return false;busy=true;render();try{await action();await refresh();return true;}catch(e){error(e);return false;}finally{busy=false;render();scheduleQuote();}}
 async function pluginChange(key,action){await mutation(async()=>{await client.submit('settings',pluginIntent(settings,key,action,crypto.randomUUID()));report(action==='enable'?'已启用。':action==='disable'?'已停用，原配置保留。':action==='install'?'已安装，可为当前会话启用。':'已卸载，记录保留。');});}
 function modal(title,className=''){
  const el=document.createElement('dialog');el.className='chat-dialog '+className;el.setAttribute('aria-label',title);el.innerHTML='<header><h2>'+esc(title)+'</h2><button type="button" aria-label="关闭'+esc(title)+'">×</button></header><div class="chat-dialog-body"></div><footer></footer>';document.body.append(el);el.showModal();return el;
 }
 function child(title){if(sub)sub.close();const focus=document.activeElement,node=modal(title,'chat-subdialog');sub=node;node.querySelector('header button').onclick=()=>node.close();node.oncancel=e=>{e.preventDefault();node.close();};node.onclose=()=>{node.remove();if(sub===node)sub=null;if(focus?.isConnected)focus.focus();};return node;}
 function closePanel(){if(sub){sub.close();return;}if(!panel)return;panel.close();}
 function requestClose(){if(sub){sub.close();return;}if(!hasDirty()||busy){closePanel();return;}if(discard)return;
  const node=modal('放弃未应用修改？','chat-confirm');discard=node;node.querySelector('.chat-dialog-body').textContent='本次参数编辑尚未应用，正式配置保持原样。';node.querySelector('footer').innerHTML='<button type="button" data-stay>继续编辑</button><button type="button" data-drop>放弃修改</button>';
  const cancel=()=>node.close();node.querySelector('header button').onclick=cancel;node.querySelector('[data-stay]').onclick=cancel;node.oncancel=e=>{e.preventDefault();cancel();};node.onclose=()=>{node.remove();discard=null;panel?.querySelector('[data-cancel]')?.focus();};node.querySelector('[data-drop]').onclick=()=>{drafts={};node.close();closePanel();};node.querySelector('[data-stay]').focus();
 }
 async function openPanel(mode='parameters'){
  if(panel)return;const focus=document.activeElement;panel=modal('模型与生成参数','chat-parameter-panel');const d=panel;
  d.querySelector('header button').onclick=requestClose;d.oncancel=e=>{e.preventDefault();requestClose();};d.onclose=()=>{sub?.close();sub=null;d.remove();panel=null;drafts={};focus?.focus();};
  d.querySelector('.chat-dialog-body').innerHTML='<nav class="chat-tabs" aria-label="设置分类"><button type="button" data-mode="parameters">生成参数</button><button type="button" data-mode="plugins">会话插件</button></nav><p data-panel-status role="status"></p><section data-content></section><details hidden><summary>诊断信息</summary><code data-diagnostic></code></details>';
  d.querySelector('footer').innerHTML='<p data-dirty role="status"></p><div><button type="button" data-cancel>取消</button><button type="button" data-check>预检</button><button type="button" class="primary" data-apply>应用参数</button></div>';
  d.dataset.mode=mode;d.querySelector('[data-cancel]').onclick=requestClose;d.querySelector('[data-check]').onclick=preflight;d.querySelector('[data-apply]').onclick=apply;
  for(const b of d.querySelectorAll('[data-mode]'))b.onclick=async()=>{d.dataset.mode=b.dataset.mode;await drawPurpose();};await drawPurpose();
 }
 let drawEpoch=0;
 async function drawPurpose(){
  if(!panel)return;const pane=panel,epoch=++drawEpoch;for(const b of pane.querySelectorAll('[data-mode]'))b.setAttribute('aria-pressed',String(b.dataset.mode===pane.dataset.mode));
  const content=pane.querySelector('[data-content]');
  if(pane.dataset.mode==='plugins'){
   const descriptions={comparison:'本轮比较多份回复，只有采用的一份进入剧情。',parameters:'分别设置剧情、摘要、记忆的参数；停用后恢复模型默认值。',model:'选择后续剧情的默认模型。',history:'限定带入模型的历史回合。',summary:'后台整理剧情摘要，使用独立模型。',memory:'按关键词召回记忆，使用独立模型。'};
   content.innerHTML='<h3>当前会话的插件</h3><p class="muted">安装后仍需为本会话启用。</p><ul class="plugin-list">'+['comparison','parameters','model','history','summary','memory'].filter(key=>settings.plugins[key]).map(key=>{
    const p=settings.plugins[key],enabled=['comparison','parameters','model'].includes(key)?p.enabled:settings.effectivePolicy.authorization[key]?.enabled;
    return '<li><div><strong>'+esc(p.name)+'</strong><p>'+descriptions[key]+'</p><small>'+(!p.installed?'未安装':enabled?'本会话已启用':'本会话未启用')+'</small></div><div class="plugin-buttons">'+(!p.installed?'<button type="button" data-plugin="'+key+'" data-action="install">安装</button>':'<button type="button" data-plugin="'+key+'" data-action="'+(enabled?'disable':'enable')+'">'+(enabled?'停用':'启用')+'</button><button type="button" class="quiet" data-plugin="'+key+'" data-action="uninstall">卸载</button>')+'</div></li>';
   }).join('')+'</ul>';
   for(const b of content.querySelectorAll('[data-plugin]')){b.disabled=blocked();b.onclick=()=>pluginChange(b.dataset.plugin,b.dataset.action);}
   updatePanelState();return;
  }
  content.innerHTML='<nav class="chat-tabs purpose-tabs" aria-label="参数用途">'+Object.entries(purposes).map(([key,name])=>'<button type="button" data-purpose="'+key+'" aria-pressed="'+(key===purpose)+'">'+name+'</button>').join('')+'</nav><p class="muted scope-note">仅影响本会话 · '+(purpose==='story'?'用于后续剧情':purpose==='summary'?'自动总结启用时使用':'记忆宫殿启用时使用')+'</p><div data-purpose-body></div>';
  for(const b of content.querySelectorAll('[data-purpose]'))b.onclick=async()=>{purpose=b.dataset.purpose;await drawPurpose();pane.querySelector('[data-purpose="'+purpose+'"]')?.focus();};
  const body=content.querySelector('[data-purpose-body]'),base=view.session.models.current[purpose];
  body.innerHTML='<div class="model-line"><span>当前模型</span><strong>'+esc(base?.entry.name||'尚未选择')+'</strong><button type="button" data-select aria-haspopup="dialog">选择模型</button></div>';
  body.querySelector('[data-select]').disabled=blocked();body.querySelector('[data-select]').onclick=picker;
  if(!settings.plugins.parameters?.enabled){body.insertAdjacentHTML('beforeend','<section class="permission-empty"><h3>为本会话启用生成参数</h3><p>剧情、摘要和记忆分别保存。启用前沿用现有模型配置。</p><button type="button" data-open-plugins>管理会话插件</button></section>');body.querySelector('[data-open-plugins]').onclick=()=>{pane.dataset.mode='plugins';drawPurpose();};updatePanelState();return;}
  if(!base){body.insertAdjacentHTML('beforeend','<p class="muted">先选择'+purposes[purpose]+'模型，再调整它支持的参数。</p>');updatePanelState();return;}
  let d=drafts[purpose];
  if(!d||d.entry?.selectionId!==base.entry.selectionId||d.reload){
   body.insertAdjacentHTML('beforeend','<p data-loading role="status">正在读取模型支持的参数…</p>');
   try{const result=await api('/joint/parameters/'+sessionId+'/'+purpose);if(!panel||panel!==pane||epoch!==drawEpoch)return;
    const next=chatParameterDraft(view,purpose,result.catalog);if(d){next.draft=switchParameterDraft(d.draft,next.entry);next.buffer=d.buffer;next.advanced=d.advanced;}
    d=next;drafts[purpose]=d;
   }catch(e){if(panel===pane&&epoch===drawEpoch){body.querySelector('[data-loading]')?.remove();body.insertAdjacentHTML('beforeend','<p class="warning">'+esc(readable(e))+'</p><button type="button" data-reload>重新读取能力</button>');body.querySelector('[data-reload]').onclick=()=>drawPurpose();updatePanelState();}return;}
  }
  if(!panel||panel!==pane||epoch!==drawEpoch)return;body.querySelector('[data-loading]')?.remove();
  body.insertAdjacentHTML('beforeend','<div class="parameter-tools"><button type="button" data-presets>载入预设</button><button type="button" data-save-preset>另存为预设</button><button type="button" data-defaults>恢复默认</button></div>'+(d.incompatible?'<p class="warning">模型已切换，旧参数仍保留。核对兼容性后应用，或恢复默认。</p>':'')+'<div data-issues class="warning" role="status"></div><div data-fields></div><details class="effective"><summary>下一轮实际配置</summary><p>'+esc(parameterValueText(view.parameterSettings.effective?.[purpose]))+'</p><details><summary>参数来源与记录</summary><pre>'+esc(JSON.stringify({model:view.parameterSettings.effective?.[purpose]?.entry.modelId,parameters:view.parameterSettings.effective?.[purpose]?.parameters,authorization:view.parameterSettings.authorization,parameterRevision:view.parameterSettings.revision},null,2))+'</pre></details></details><button type="button" data-refresh-capabilities>刷新能力与状态</button>');
  body.querySelector('[data-presets]').onclick=()=>presetDialog('load');body.querySelector('[data-save-preset]').onclick=()=>presetDialog('save');
  body.querySelector('[data-defaults]').onclick=()=>{d.draft=typedParameterDraft(d.entry);d.buffer='';renderFields(d);report('已恢复为模型默认值，应用后生效。');};
  body.querySelector('[data-refresh-capabilities]').onclick=async()=>{if(blocked())return;for(const draft of Object.values(drafts))draft.reload=true;try{await refresh({acknowledge:true});report('已读取最新状态，编辑保留。请核对后应用。');}catch(e){error(e);}};
  renderFields(d);updatePanelState();
 }
 function renderFields(d){
  if(!panel)return;const target=panel.querySelector('[data-fields]');if(!target)return;
  const fields=d.entry?.parameterPolicy?.fields||{},fixed=d.entry?.parameterPolicy?.kind==='fixed';
  function field(key){const f=fields[key],s=d.draft[key],name=labels[key]||key,id='chat-param-'+key.replaceAll('.','-');
   if(!f)return '<section class="parameter-row"><strong>'+esc(name)+'</strong><p class="warning">当前模型不支持此项，原值保留：'+esc(JSON.stringify(s.value))+'</p><button type="button" data-remove-unsupported="'+esc(key)+'">移除此项</button></section>';
   const on=s.mode==='explicit'&&!fixed,value=s.value;
   let control='';
   if(['number','integer'].includes(f.type))control='<div class="numeric-pair"><input type="range" data-range="'+esc(key)+'" aria-label="'+esc(name)+'滑杆" min="'+f.minimum+'" max="'+f.maximum+'" step="'+(f.type==='integer'?'1':'0.01')+'" value="'+esc(value===''?f.minimum:value)+'"'+(!on?' disabled':'')+'><input type="number" id="'+id+'" aria-label="'+esc(name)+'" data-number="'+esc(key)+'" min="'+f.minimum+'" max="'+f.maximum+'" step="'+(f.type==='integer'?'1':'any')+'" value="'+esc(value)+'"'+(!on?' disabled':'')+'></div><p class="field-help">'+f.minimum+'–'+f.maximum+(f.type==='integer'?' · 整数':'')+'</p>';
   else if(f.type==='boolean'||f.type==='enum'){const opts=f.type==='boolean'?[false,true]:f.options;control='<select id="'+id+'" aria-label="'+esc(name)+'" data-enum="'+esc(key)+'"'+(!on?' disabled':'')+'>'+opts.map(v=>'<option value="'+esc(v)+'"'+(v===value?' selected':'')+'>'+esc(f.type==='boolean'?v?'开启':'关闭':v)+'</option>').join('')+'</select>';}
   else control='<ul class="stop-list">'+value.map((v,i)=>'<li><span>'+esc(v)+'</span><button type="button" data-remove-stop="'+i+'" aria-label="删除停止词 '+esc(v)+'"'+(!on?' disabled':'')+'>×</button></li>').join('')+'</ul><div class="stop-entry"><input type="text" aria-label="新停止词" placeholder="输入后按 Enter 添加" value="'+esc(d.buffer)+'" data-stop-input'+(!on?' disabled':'')+'><button type="button" data-add-stop'+(!on?' disabled':'')+'>添加</button></div><p class="field-help">最多 '+f.maxItems+' 条，每条 '+f.maxItemLength+' 字符；保留空格。</p>';
   const def=f.default.mode==='omit'?'由模型决定':Array.isArray(f.default.value)?f.default.value.join('、'):String(f.default.value);
   return '<section class="parameter-row" data-field="'+esc(key)+'"><div class="parameter-label"><label for="'+id+'">'+esc(name)+'</label><label class="default-toggle"><input type="checkbox" data-default="'+esc(key)+'" aria-label="'+esc(name)+'使用默认"'+(s.mode==='default'?' checked':'')+(fixed?' disabled':'')+'> 使用默认</label></div>'+control+(s.mode==='default'?'<p class="default-caption">默认：'+esc(def)+'</p>':'')+'</section>';
  }
  const keys=Object.keys(d.draft),first=keys.filter(k=>basic.includes(k)),advanced=keys.filter(k=>!basic.includes(k));
  target.innerHTML=first.map(field).join('')+(advanced.length?'<details class="advanced"'+(d.advanced?' open':'')+'><summary>高级参数 <span>'+advanced.length+' 项</span></summary>'+advanced.map(field).join('')+'</details>':'');
  target.querySelector('.advanced')?.addEventListener('toggle',e=>{d.advanced=e.target.open;});
  for(const b of target.querySelectorAll('[data-default]'))b.onchange=()=>{const key=b.dataset.default;d.draft[key].mode=b.checked?'default':'explicit';renderFields(d);target.querySelector('[data-default="'+key+'"]')?.focus();};
  for(const b of target.querySelectorAll('[data-remove-unsupported]'))b.onclick=()=>{delete d.draft[b.dataset.removeUnsupported];renderFields(d);};
  for(const n of target.querySelectorAll('[data-range],[data-number]'))n.oninput=()=>{const key=n.dataset.range||n.dataset.number;d.draft[key].value=n.value;const other=target.querySelector('['+(n.dataset.range?'data-number':'data-range')+'="'+key+'"]');other.value=n.value;updatePanelState();};
  for(const n of target.querySelectorAll('[data-enum]'))n.onchange=()=>{const key=n.dataset.enum;d.draft[key].value=fields[key].type==='boolean'?n.value==='true':n.value;updatePanelState();};
  for(const n of target.querySelectorAll('[data-remove-stop]'))n.onclick=()=>{d.draft.stop.value.splice(Number(n.dataset.removeStop),1);renderFields(d);target.querySelector('[data-stop-input]').focus();};
  const stop=target.querySelector('[data-stop-input]');if(stop){const add=()=>{try{addChatStop(d,stop.value);renderFields(d);target.querySelector('[data-stop-input]').focus();}catch(e){error(e);}};stop.oninput=()=>{d.buffer=stop.value;updatePanelState();};stop.onkeydown=e=>{if(e.key==='Enter'&&!e.isComposing){e.preventDefault();add();}};target.querySelector('[data-add-stop]').onclick=add;}
  updatePanelState();
 }
 function updatePanelState(){if(!panel)return;const d=drafts[purpose],parameterMode=panel.dataset.mode==='parameters',enabled=settings.plugins.parameters?.enabled;
  const issues=d?chatDraftIssues(d):[],outdated=d&&d.view.session.revision!==view.session.revision;
  const issueArea=panel.querySelector('[data-issues]');if(issueArea){issueArea.innerHTML=(outdated?'<p>会话已更新，编辑仍保留。请点“刷新能力与状态”核对后应用。</p>':'')+issues.map(i=>'<p>'+esc(labels[i.field]||i.field)+'：'+esc(readable(i))+'</p>').join('');issueArea.hidden=!issueArea.textContent;}
  panel.querySelector('[data-dirty]').textContent=Object.entries(drafts).filter(([,d])=>chatDraftDirty(d)).map(([p])=>purposes[p]).join('、')+(hasDirty()?'有未应用修改':'');
  for(const name of ['check','apply']){const b=panel.querySelector('[data-'+name+']');b.hidden=!parameterMode;b.disabled=blocked()||!enabled||!d||issues.length>0||outdated||(name==='apply'&&!chatDraftDirty(d));}
  for(const b of panel.querySelectorAll('[data-purpose],[data-mode],[data-select],[data-refresh-capabilities],[data-presets],[data-save-preset],[data-defaults]'))b.disabled=busy||reading||!!localPending();
  for(const b of panel.querySelectorAll('[data-default],[data-remove-unsupported]'))b.disabled=blocked()||d?.entry?.parameterPolicy?.kind==='fixed';
  for(const n of panel.querySelectorAll('[data-number],[data-range],[data-enum],[data-stop-input],[data-add-stop],[data-remove-stop]')){const key=n.dataset.number||n.dataset.range||n.dataset.enum||'stop';n.disabled=blocked()||d?.draft[key]?.mode!=='explicit'||d?.entry?.parameterPolicy?.kind==='fixed';n.setAttribute('aria-invalid',String(issues.some(i=>i.field===key)));}
  const grantKey=purpose==='story'?'model':purpose,grant=settings.plugins[grantKey];const authorized=grantKey==='model'?grant?.enabled:settings.effectivePolicy.authorization[grantKey]?.enabled;
  const select=panel.querySelector('[data-select]');if(select){select.disabled=blocked()||!authorized;select.title=authorized?'':'请先在会话插件中启用'+(grant?.name||'对应功能');}
  for(const b of panel.querySelectorAll('[data-plugin]'))b.disabled=blocked();
 }
 async function preflight(){const d=drafts[purpose];if(!d||blocked()||chatDraftIssues(d).length)return;busy=true;render();try{await api('/model-settings/check',{choice:typedSelection(d.catalog,d.entry,d.draft,purpose)});report('参数预检通过，尚未应用或生成。');}catch(e){error(e);}finally{busy=false;render();scheduleQuote();}}
 async function apply(){const d=drafts[purpose];if(!d||blocked()||chatDraftIssues(d).length||!chatDraftDirty(d))return;
  const chosen=typedSelection(d.catalog,d.entry,d.draft,purpose),p=purpose;
  await mutation(async()=>{await client.submit('parameters',parameterEditIntent(d.view,crypto.randomUUID(),p,chosen));delete drafts[p];for(const old of Object.values(drafts))old.reload=true;report('已应用'+purposes[p]+'参数，下一轮生效。');});
 }
 async function picker(){if(blocked())return;const p=purpose,node=child('选择'+purposes[p]+'模型');let query='',family='All',selected=null,catalog=null;
  node.querySelector('.chat-dialog-body').innerHTML='<p class="muted">只更改'+purposes[p]+'的默认模型。已有参数保留，冲突需核对。</p><label class="search-label">搜索模型<input type="search" aria-label="搜索模型" placeholder="名称或模型 ID"></label><nav class="chat-tabs" data-families></nav><div data-model-list role="status">正在读取目录…</div><p data-picker-message></p>';
  node.querySelector('footer').innerHTML='<button type="button" data-cancel>取消</button><button type="button" class="primary" data-choose disabled>应用模型</button>';node.querySelector('[data-cancel]').onclick=()=>node.close();
  function list(){if(!node.open)return;node.querySelector('[data-model-list]').innerHTML=filteredModels(catalog,query,family).map(e=>'<button type="button" class="model-option" data-choice="'+esc(e.selectionId)+'" aria-pressed="'+(selected?.selectionId===e.selectionId)+'"'+(!e.available||!e.parameterPolicy?.purposes.includes(p)?' disabled':'')+'><strong>'+esc(e.name)+'</strong><small>'+esc(e.modelId)+'</small><span>'+(!e.available?'当前不可用':selected?.selectionId===e.selectionId?'已选择':'')+'</span></button>').join('')||'<p>没有匹配的模型。</p>';
   for(const b of node.querySelectorAll('[data-choice]'))b.onclick=()=>{selected=catalog.models.find(e=>e.selectionId===b.dataset.choice);list();node.querySelector('[data-choose]').disabled=false;};
  }
  try{({catalog}=await api('/model-settings/sessions/'+sessionId+'/models?parameterVersion=2'));if(!node.open)return;
   const nav=node.querySelector('[data-families]');nav.innerHTML=['All',...new Set(catalog.models.map(e=>e.modelId.split('/')[0]))].map(f=>'<button type="button" data-family="'+esc(f)+'" aria-pressed="'+(f===family)+'">'+esc(f==='All'?'全部':f)+'</button>').join('');for(const b of nav.querySelectorAll('button'))b.onclick=()=>{family=b.dataset.family;for(const o of nav.querySelectorAll('button'))o.setAttribute('aria-pressed',String(o===b));list();};
   node.querySelector('input').oninput=e=>{query=e.target.value;list();};list();node.querySelector('input').focus();
   node.querySelector('[data-choose]').onclick=async()=>{if(!selected||blocked())return;const body={sessionId,operationId:crypto.randomUUID(),expectedRevision:view.session.revision,expectedModelRevision:view.session.models.revision,purpose:p,choice:typedSelection(catalog,selected,typedParameterDraft(selected),p)};
    node.querySelector('[data-choose]').disabled=true;node.close();await mutation(async()=>{await modelClient.submit(body);if(drafts[p])drafts[p].reload=true;report('已更新'+purposes[p]+'模型。已有参数保留，请核对是否兼容。');});
   };
  }catch(e){if(node.open)node.querySelector('[data-picker-message]').textContent=readable(e);}
 }
 function storePreset(body){try{if(body){const text=JSON.stringify(body);storage.setItem(pendingPresetKey,text);if(storage.getItem(pendingPresetKey)!==text)throw Error();}else{storage.removeItem(pendingPresetKey);if(storage.getItem(pendingPresetKey)!==null)throw Error();}presetPending=body;}catch{fail('JOINT_LOCAL_STORAGE_UNAVAILABLE');}}
 async function presetDialog(mode){
  const d=drafts[purpose];if(!d||blocked())return;const p=purpose,node=child(mode==='save'?'另存为预设':'载入预设');let snapshot=null,saving=false;
  node.querySelector('.chat-dialog-body').innerHTML='<p class="muted">'+purposes[p]+'用途 · 保存或载入不会直接应用到会话。</p><p data-preset-status role="status"></p><div data-preset-content></div>';
  const tell=t=>{if(node.open)node.querySelector('[data-preset-status]').textContent=t;};
  const content=node.querySelector('[data-preset-content]');
  async function pendingView(){content.innerHTML='<p>有预设保存结果待核对。原编号与内容已保留。</p><button type="button" data-recover-preset>核对原保存</button>';node.querySelector('footer').innerHTML='<button type="button" data-cancel>关闭</button>';node.querySelector('[data-cancel]').onclick=()=>node.close();
   content.querySelector('button').onclick=async()=>{if(saving)return;saving=true;try{const original=presetPending;if(!original?.operationId||!original.profile||!original.selection)fail('CHAT_LOCAL_RECORD_INVALID');const r=await api('/model-settings/presets/reconcile',{operationId:original.operationId});
    if(r.status==='committed'){if(!await presetReceiptMatches(original,r.operation))fail('JOINT_RECEIPT_MISMATCH');storePreset(null);node.close();report('已恢复预设保存记录，没有重复保存。');}
    else if(r.status==='not_found'){tell('已确认这次预设尚未保存。可以放弃原操作后重新编辑。');const b=document.createElement('button');b.type='button';b.textContent='放弃未保存记录';b.onclick=async()=>{const check=await api('/model-settings/presets/reconcile',{operationId:original.operationId});if(check.status!=='not_found'){tell('状态已变化，请重新核对。');return;}storePreset(null);node.close();};content.append(b);}
    else tell('结果仍未确认，请保留原记录。');
   }catch(e){tell(readable(e));}finally{saving=false;}};
  }
  if(presetPending){await pendingView();return;}
  try{snapshot=await api('/model-settings/presets');if(!node.open)return;}catch(e){tell(readable(e));return;}
  node.querySelector('footer').innerHTML='<button type="button" data-cancel>取消</button>';node.querySelector('[data-cancel]').onclick=()=>node.close();
  if(mode==='save'){
   const issues=chatDraftIssues(d);if(issues.length){tell('请先修正参数问题，再保存预设。');return;}
   content.innerHTML='<label class="search-label">预设名称<input type="text" aria-label="预设名称" placeholder="例如：细腻叙述" maxlength="160"></label><p class="field-help">1–80 字符，同名预设分别保留。</p>';
   const save=document.createElement('button');save.type='button';save.className='primary';save.textContent='保存预设';save.disabled=true;node.querySelector('footer').append(save);const name=content.querySelector('input');name.oninput=()=>{save.disabled=!name.value.trim()||!name.value.isWellFormed()||[...name.value].length>80||saving;};name.focus();
   name.onkeydown=e=>{if(e.key==='Enter'&&!e.isComposing){e.preventDefault();if(!save.disabled)save.click();}};
   save.onclick=async()=>{if(saving||save.disabled)return;const {format,profile,...selection}=typedSelection(d.catalog,d.entry,d.draft,p);const body={operationId:crypto.randomUUID(),expectedRevision:snapshot.revision,id:crypto.randomUUID(),name:name.value,expectedPresetRevision:0,profile,selection};
    try{storePreset(body);}catch(e){tell(readable(e));return;}saving=true;save.disabled=true;name.disabled=true;
    try{const r=await api('/model-settings/presets',body);if(!await presetReceiptMatches(body,r))fail('JOINT_RECEIPT_MISMATCH');storePreset(null);node.close();report('预设已保存，当前会话配置未改变。');}
    catch(e){if(e.httpStatus&&e.httpStatus<500&&!/UNKNOWN|STORAGE|WRITE|PENDING/.test(e.code||''))storePreset(null);tell(readable(e));if(presetPending)await pendingView();}finally{saving=false;}
   };
  }else{
   content.innerHTML='<label class="search-label">搜索预设<input type="search" aria-label="搜索预设" placeholder="名称或模型"></label><div data-presets-list></div>';
   const render=q=>{const rows=snapshot.presets.filter(x=>x.profile.purpose===p&&(!q||(x.name+' '+x.profile.modelId).toLowerCase().includes(q.toLowerCase()))),list=content.querySelector('[data-presets-list]');list.innerHTML=rows.map(x=>'<button type="button" class="model-option" data-preset="'+esc(x.id)+'"><strong>'+esc(x.name)+'</strong><small>'+esc(x.profile.modelId)+'</small></button>').join('')||'<p class="muted">没有匹配的'+purposes[p]+'预设。</p>';
    for(const b of list.querySelectorAll('button'))b.onclick=()=>{const selected=rows.find(x=>x.id===b.dataset.preset),loaded=profileDraft(d.entry,selected.profile,p),issues=typedDraftIssues(d.entry,loaded,p);
     content.innerHTML='<h3>'+esc(selected.name)+'</h3><p class="muted">来源模型：'+esc(selected.profile.modelId)+'</p><p>当前模型：'+esc(d.entry.name)+'</p>'+(selected.profile.modelId!==d.entry.modelId?'<p class="warning">跨模型载入，保留当前模型和原参数值；不兼容项需手动修正。</p>':'')+issues.map(i=>'<p class="warning">'+esc(labels[i.field]||i.field)+'：'+esc(readable(i))+'</p>').join('')+'<dl>'+Object.entries(selected.profile.parameters.values).map(([k,v])=>'<div><dt>'+esc(labels[k]||k)+'</dt><dd>'+esc(v.mode==='default'?'使用默认':JSON.stringify(v.value))+'</dd></div>').join('')+'</dl>';
     node.querySelector('footer').innerHTML='<button type="button" data-back>返回列表</button><button type="button" class="primary" data-load>载入待核对参数</button>';node.querySelector('[data-back]').onclick=()=>{node.close();presetDialog('load');};node.querySelector('[data-load]').onclick=()=>{d.draft=loaded;d.buffer='';node.close();drawPurpose();report('已载入预设，核对后应用到会话。');};
    };
   };content.querySelector('input').oninput=e=>render(e.target.value);render('');content.querySelector('input').focus();
  }
 }
 async function recover(){if(busy)return;busy=true;render();let unresolved=false;
  try{
   if(modelClient.pending()){const result=await modelClient.recover();if(result.state==='not_applied'){unresolved=true;recoveryDiscard(()=>modelClient.discard());}else if(result.state!=='settled')unresolved=true;}
   for(const record of client.pending()){const result=await client.recover(record);if(['not_applied','not_submitted'].includes(result.state)){unresolved=true;recoveryDiscard(()=>client.discardConfirmed(record));}else if(!['settled','applied'].includes(result.state))unresolved=true;}
   if(settings.unconfirmed&&!client.pending().length){const r=await api('/joint/settings/reconcile',settings.unconfirmed);if(r.state!=='applied')unresolved=true;}
   if(view.pending&&!client.pending().length){const run=view.session.runs.findLast(r=>!['completed','paused','not_dispatched'].includes(r.state));if(run){await api('/joint/recover',{sessionId,operationId:run.operationId,expectedRevision:view.session.revision,expectedSummaryRevision:view.session.summary.snapshot.revision,expectedMemoryRevision:view.session.memory.snapshot.revision});}}
   await refresh();report(unresolved?'原操作已核对，尚未确认的记录继续保留。':'已核对原操作，没有重新发送。');
  }catch(e){error(e);}finally{busy=false;render();scheduleQuote();}
 }
 function recoveryDiscard(action){const n=child('原操作尚未应用');n.querySelector('.chat-dialog-body').textContent='已核对服务器记录。放弃后可重新编辑，不会自动重发。';n.querySelector('footer').innerHTML='<button type="button" data-keep>保留记录</button><button type="button" data-discard>放弃未应用记录</button>';n.querySelector('[data-keep]').onclick=()=>n.close();n.querySelector('[data-discard]').onclick=async()=>{try{await action();n.close();await refresh();report('已放弃未应用记录。');}catch(e){error(e);}};}
 root.querySelector('[data-settings]').onclick=()=>openPanel('plugins').catch(error);root.querySelector('[data-parameters]').onclick=()=>openPanel().catch(error);
 root.querySelector('[data-refresh]').onclick=()=>refresh({acknowledge:true}).then(()=>report('已刷新会话状态。')).catch(error);
 input.oninput=()=>{persistInput();scheduleQuote();};
 input.onkeydown=e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)&&!e.isComposing){e.preventDefault();if(!send.disabled)root.querySelector('form').requestSubmit();}};
 root.querySelector('form').onsubmit=async e=>{e.preventDefault();if(send.disabled||!persistInput())return;const intent=frozenSend,original=input.value;
  await mutation(async()=>{const r=await client.submit('send',intent);if(r.operation?.state==='completed'&&input.value===original){input.value='';persistInput();}report('请求已保存。');});
 };
 const storageChanged=e=>{if(e.key===draftKey)return;if(e.key?.startsWith('modelmirror.'))refresh().then(()=>report('其他页面的会话状态已更新，未应用编辑仍保留。')).catch(error);};window.addEventListener('storage',storageChanged);
 const beforeUnload=e=>{if(hasDirty()){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',beforeUnload);
 const pageHide=()=>{dead=true;frameEpoch++;if(frameToken)fetch('/rpg-app/api/authoring/presentations/release',{method:'POST',headers:{'Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify({token:frameToken}),keepalive:true}).catch(()=>{});};window.addEventListener('pagehide',pageHide);
 if(runtime.comparison){const node=document.createElement('div');root.querySelector('.chat-composer').before(node);const base='/joint/comparisons/'+sessionId;
  comparison=mountComparison(node,{api,base,sessionId,getInput:()=>input.value,setInput:value=>{input.value=value;persistInput();scheduleQuote();},enabled:!!settings.plugins.comparison?.enabled,onBlocked:value=>{if(comparisonBlocked===value)return;comparisonBlocked=value;render();if(!value)scheduleQuote();},onChange:()=>refresh(),prepare:(b,c)=>api(base+'/'+b.plan.id+'/preview/'+c),release:token=>api('/presentations/release',{token})});}
 render();scheduleQuote();return {refresh,get view(){return view;},open:openPanel,destroy(){comparison?.dispose();clearTimeout(timer);clearTimeout(quoteTimer);window.removeEventListener('storage',storageChanged);window.removeEventListener('beforeunload',beforeUnload);window.removeEventListener('pagehide',pageHide);dead=true;frameEpoch++;void releaseFrame();panel?.close();}};
}
if(typeof document!=='undefined'&&document.querySelector('[data-chat-root]')){
 const root=document.querySelector('[data-chat-root]');mountChatParameters(root,{sessionId:new URL(location.href).searchParams.get('session')}).catch(e=>{root.innerHTML='<section class="chat-fatal"><h1>暂时无法打开会话</h1><p>'+esc(readable(e))+'</p><p>请保留原会话地址，恢复服务后重新打开。</p><details><summary>诊断信息</summary><code>'+esc(e.code||'NETWORK_READ_FAILED')+'</code></details></section>';});
}
