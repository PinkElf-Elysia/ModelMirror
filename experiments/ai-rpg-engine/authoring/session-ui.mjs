import {attachSessionPanel} from './browser.mjs';
import {createSessionClient,sessionPolicyText,summarySettingsIntent,summaryTaskIntent,storyIntent} from './session-client.mjs';
import {pendingOperation as pendingModelOperation} from './model-ui.mjs';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const messages={
 SESSION_RESULT_PENDING:'上次操作尚未核对，请先恢复记录。',SESSION_LOCAL_RECORD_INVALID:'浏览器中的操作记录无法读取。请保留页面与记录。',SESSION_LOCAL_STORAGE_UNAVAILABLE:'浏览器无法保存恢复记录，本次操作尚未开始。',SESSION_RECEIPT_MISMATCH:'返回记录与原会话不一致，请保留记录后核对。',
 SUMMARY_MODEL_REQUIRED:'请先在模型设置中选择独立摘要模型。',SUMMARY_UPDATE_REQUIRED:'摘要尚未覆盖较早回合，请先更新摘要。',SUMMARY_ALREADY_CURRENT:'摘要已覆盖需要整理的回合。',SUMMARY_RESULT_PENDING:'摘要结果待核对，请恢复原记录。',SUMMARY_SETTINGS_RESULT_PENDING:'插件设置待核对，请先恢复设置。',SUMMARY_SETTINGS_RESULT_UNKNOWN:'设置结果尚未确认。请核对原记录。',
 OFFLINE_REVISION_CONFLICT:'会话已在其它位置更新。输入已保留，请刷新后核对。',SUMMARY_POLICY_CONFLICT:'上下文策略已变化，请刷新后核对。',PLUGIN_REGISTRY_CONFLICT:'插件设置已变化，请刷新后核对。',OFFLINE_OPENING_MISMATCH:'首轮须使用创建会话时选定的开局。可点“填入本会话开局”。',OFFLINE_SESSION_BUSY:'会话仍在处理中，请等待完成。',MODEL_SELECTION_REQUIRED:'请先选择剧情模型。',OFFLINE_RESULT_PENDING:'剧情结果待核对，请先恢复原记录。',OFFLINE_FILES_CHANGED_RESTART_REQUIRED:'此会话对应的运行版本已变化。请保留数据，并使用对应版本的服务。',
};
export const sessionError=e=>(messages[e?.code]||'操作未完成，原记录与输入已保留。');
const summaryLabel={idle:'尚无摘要',prepared:'等待整理',dispatching:'正在整理',unknown:'结果待核对',awaiting_compression:'等待压缩确认',compression_dispatching:'正在压缩',compression_unknown:'压缩结果待核对',applied:'已更新',rejected:'整理未通过',revoked:'已撤销授权',not_dispatched:'未派发',blocked:'更新已暂停',stale:'旧结果未采用'};
function install({api,getWork,getInput,setInput,openModels,notice}){
 const panel=document.createElement('section');panel.className='runtime-panel';panel.setAttribute('aria-label','会话调试');document.querySelector('.preview-tabs').before(panel);
 const send=document.createElement('button');send.type='button';send.className='primary runtime-send';send.textContent='发送演练';document.querySelector('.composer footer').append(send);
 const input=document.querySelector('#player-input');let sessionId=null,view=null,settings=null,client=null,busy=false,error=null,dialog=null,timer=null,generation=0,loadedWork=null,retryRecord=null,loading=false;
 const sourceButton=document.querySelector('#model-button');
 const current=()=>getWork()?.id===loadedWork;
 const pending=()=>client?.pending()||[];
 const notify=(text,code='',tone='warning')=>{error={text,code,tone};render();};
 function localBlocked(){try{return !!pending().length||!!pendingModelOperation(localStorage,loadedWork);}catch(e){error={text:sessionError(e),code:e.code};return true;}}
 function serverPending(){if(!view)return[];const s=view.session;return [
  ...s.operations.filter(o=>['prepared','dispatching','unknown'].includes(o.state)).map(o=>({kind:'send',workId:loadedWork,sessionId,body:o.intent})),
  ...s.summary.jobs.filter(j=>['prepared','dispatching','unknown','compression_dispatching','compression_unknown'].includes(j.state)).map(j=>({kind:'update',workId:loadedWork,sessionId,body:j.intent})),
 ];}
 function unresolved(){const found=pending(),remote=serverPending();if(settings?.unconfirmed?.sessionId===sessionId)remote.push({kind:'settings',workId:loadedWork,sessionId,body:settings.unconfirmed});return [...found,...remote.filter(r=>!found.some(f=>f.body.operationId===r.body.operationId))];}
 function recordIsCurrent(){return client&&view&&!busy&&!localBlocked()&&!view.busy&&!view.pending&&!settings?.unconfirmed;}
 async function refresh(silent=false){
  const gen=++generation,work=getWork();clearTimeout(timer);loading=true;if(!work?.id){loadedWork=null;view=null;settings=null;client=null;sessionId=null;loading=false;render();return;}
  loadedWork=work.id;let id;try{id=JSON.parse(localStorage.getItem('modelmirror.authoring.model-session/'+work.id)||'null');}catch{throw {code:'SESSION_LOCAL_RECORD_INVALID'};}
  if(id!==null&&(typeof id!=='string'||!/^session-[a-f0-9]{64}$/.test(id)))throw {code:'SESSION_LOCAL_RECORD_INVALID'};
  if(!id){view=null;settings=null;sessionId=null;client=null;loading=false;render();return;}
  const candidate=createSessionClient({api,storage:localStorage,sessionId:id,workId:work.id});
  const [next,summary]=await Promise.all([candidate.view(),api('/summary-settings/sessions/'+id)]);
  if(gen!==generation||getWork()?.id!==work.id)return;
  sessionId=id;client=candidate;view=next;settings=summary;loading=false;
  if(!silent)error=null;render();
  if(view.busy)timer=setTimeout(()=>refresh(true).catch(showError),1000);
 }
 function showError(e){loading=false;notify(sessionError(e),e?.code||'NETWORK_RESULT_UNKNOWN');}
 function dialogShell(title){const d=document.createElement('dialog');d.className='runtime-dialog';d.setAttribute('aria-label',title);d.innerHTML='<header><h2>'+esc(title)+'</h2><button type="button" aria-label="关闭'+esc(title)+'">×</button></header><div class="runtime-dialog-body"></div><footer><button type="button">关闭</button></footer>';
  d.querySelector('header button').onclick=()=>d.close();d.querySelector('footer button').onclick=()=>d.close();d.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();d.close();}});d.addEventListener('close',()=>{d.remove();if(dialog===d)dialog=null;panel.querySelector('[data-summary]')?.focus();},{once:true});document.body.append(d);d.showModal();dialog=d;return d;
 }
 function render(){
  if(!current()&&loadedWork)return;
  const blocked=localBlocked();
  panel.innerHTML='<div class="runtime-heading"><strong>会话调试</strong><span class="runtime-mode">离线演练</span><button type="button" data-refresh aria-label="刷新会话状态">刷新</button></div>';
  if(loading&&!view)panel.insertAdjacentHTML('beforeend','<p role="status">正在读取会话记录…</p>');
  else if(!view){panel.insertAdjacentHTML('beforeend','<p>先选择已保存的作品版本并配置模型，即可演练对话流程。</p><button type="button" data-models>配置模型与会话</button>');panel.querySelector('[data-models]').onclick=openModels;}
  else{
   panel.insertAdjacentHTML('beforeend','<div class="runtime-overview"><span>'+esc(sessionPolicyText(view))+'</span><span>已完成 '+view.session.turns.length+' 回合</span></div><div class="runtime-actions"><button type="button" data-summary>自动总结'+(view.effectivePolicy.authorization.summary.enabled?' · 已启用':'')+'</button><button type="button" data-history>对话记录</button>'+(view.session.turns.length===0?'<button type="button" data-opening>填入本会话开局</button>':'')+'</div>');
   sourceButton.textContent=view.session.models.current.story?.entry.name||'选择模型';
   panel.querySelector('[data-summary]').onclick=openSummary;panel.querySelector('[data-history]').onclick=openHistory;
   const opening=panel.querySelector('[data-opening]');if(opening)opening.onclick=()=>{setInput(view.session.binding.openingText);input.focus();};
   if(view.busy)panel.insertAdjacentHTML('beforeend','<p role="status">'+(view.summaryStatus==='dispatching'?'剧情已保存，正在整理摘要…':'正在处理本次请求…')+'</p>');
   else if(view.pending||blocked||settings?.unconfirmed)panel.insertAdjacentHTML('beforeend','<p class="runtime-warning">有结果待核对。不会自动重发。</p><button type="button" data-recover>恢复原操作</button>');
   else if(['incomplete','invalid','failed','not_dispatched'].includes(view.status))panel.insertAdjacentHTML('beforeend','<p class="runtime-warning">本次回复未完成。原始结果已保留，可在对话记录中核对后再决定是否重新发送。</p>');
   if(!view.effectivePolicy.canAssemble&&!view.pending&&!view.busy)panel.insertAdjacentHTML('beforeend','<p class="runtime-warning">'+esc(messages[view.effectivePolicy.reason]||'请核对会话插件状态后发送。')+'</p>');
   if(panel.querySelector('[data-recover]'))panel.querySelector('[data-recover]').onclick=recoverAll;
  }
  if(error)panel.insertAdjacentHTML('beforeend','<p class="runtime-warning'+(error.tone==='success'?' runtime-success':'')+'" role="status">'+esc(error.text)+'</p>'+(error.code?'<details class="runtime-diagnostic"><summary>诊断信息</summary><code>'+esc(error.code)+'</code></details>':''));
  panel.querySelector('[data-refresh]').onclick=()=>refresh().catch(showError);
  send.disabled=loading||!view||busy||blocked||view.busy||view.pending||!!settings?.unconfirmed||!view.session.models.current.story||!view.effectivePolicy.canAssemble||!input.value.trim();
  send.textContent=busy||view?.busy?'处理中…':'发送演练';
  panel.querySelector('[data-refresh]').disabled=busy;
  if(dialog?.dataset.kind==='summary')renderSummary();
 }
 async function execute(kind,body,again=false){
  if(busy||!client)return;const heldClient=client,workId=loadedWork,oldText=getInput();busy=true;error=null;render();
  try{const result=await heldClient.submit(kind,body,again);if(!current()||workId!==loadedWork)return;
   if(kind==='send'&&result.operation.state==='completed'&&getInput()===oldText)setInput('');
   retryRecord=null;await refresh(true);notice(kind==='settings'?'插件设置已保存。':kind==='send'?(result.operation.state==='completed'?'剧情记录已保存。':'本次请求记录已保存，请核对结果。'):'摘要任务已收束。');
  }catch(e){if(current()&&workId===loadedWork)showError(e);}finally{busy=false;if(current())render();}
 }
 send.onclick=()=>{if(!recordIsCurrent())return;execute('send',storyIntent(view,crypto.randomUUID(),getInput()));};
 input.addEventListener('input',render);
 async function recoverAll(){
  if(busy||!client)return;busy=true;error=null;render();
  try{
   const records=unresolved();if(!records.length){notify('请在模型设置中核对待确认的模型配置。');return;}
   for(const r of records){const result=await client.recover(r);if(['not_applied','not_submitted'].includes(result.state))retryRecord=r;}
   await refresh(true);const remaining=unresolved().length;notify(remaining?'已核对原记录；仍未确认的结果需要继续等待。':'原记录已恢复，没有重新发送。','',remaining?'warning':'success');
  }catch(e){showError(e);}finally{busy=false;render();}
 }
 function openHistory(){if(!view)return;const d=dialogShell('对话记录');d.dataset.kind='history';const body=d.querySelector('.runtime-dialog-body');
  body.innerHTML='<p class="runtime-muted">这里保留原始输入与输出。展示样式可在“回复回放”中核对。</p>'+view.session.turns.map(t=>'<article class="runtime-turn"><h3>第 '+t.number+' 回合</h3><details><summary>玩家输入</summary><pre>'+esc(t.user)+'</pre></details><pre>'+esc(t.assistant)+'</pre><details class="runtime-diagnostic"><summary>请求记录</summary><pre>'+esc(JSON.stringify(view.session.operations.find(o=>o.operationId===t.operationId)?.request.messages,null,2))+'</pre></details></article>').join('')+view.session.operations.filter(o=>o.state!=='completed').map(o=>'<article class="runtime-turn"><h3>未完成的请求</h3><p class="runtime-muted">此份结果尚未计入完整历史。</p><pre>'+esc(o.receipt?.result.text||'暂未取得可确认的回复。')+'</pre><details class="runtime-diagnostic"><summary>原始输入与状态</summary><pre>'+esc(o.intent.input)+'\n\n'+esc(o.state)+'</pre></details></article>').join('')+(!view.session.operations.length?'<p>尚无对话请求。</p>':'');
 }
 function openSummary(){if(!view||!settings)return;dialogShell('自动总结').dataset.kind='summary';renderSummary();}
 function renderSummary(){
  if(!dialog?.open||!settings||!view)return;const root=dialog.querySelector('.runtime-dialog-body'),auth=view.effectivePolicy.authorization.summary.enabled;
  const consent=root.querySelector('[data-consent]')?.checked||false,details=new Set([...root.querySelectorAll('details[open]')].map(d=>d.dataset.section));
  const state=view.session.summary.snapshot,version=state.versions.find(v=>v.id===state.activeVersionId);
  root.innerHTML='<p class="runtime-lead">将较早的剧情整理为摘要，并保留最近一回合原文。</p><dl class="runtime-facts"><div><dt>摘要模型</dt><dd>'+esc(settings.model?.entry.name||'尚未选择')+'</dd></div><div><dt>处理进度</dt><dd>'+settings.coveredThrough+' / '+Math.max(0,settings.completedTurns-1)+' 回合</dd></div><div><dt>状态</dt><dd>'+esc(summaryLabel[view.summaryStatus]||view.summaryStatus)+'</dd></div></dl><button type="button" data-model>更改模型</button>';
  if(!settings.plugin.installed)root.insertAdjacentHTML('beforeend','<p>此插件尚未安装。</p><button type="button" data-action="install">安装自动总结</button>');
  else if(!auth)root.insertAdjacentHTML('beforeend','<section class="runtime-consent"><h3>在本会话启用</h3><p>允许读取本会话原始剧情并生成摘要。真实调试时会产生独立的模型费用。</p><label><input type="checkbox" data-consent'+(consent?' checked':'')+'> 同意在本会话使用自动总结</label><button type="button" class="primary" data-action="enable">启用</button></section>');
  else root.insertAdjacentHTML('beforeend','<div class="runtime-actions"><button type="button" data-action="disable">停用自动总结</button><button type="button" data-update>立即更新摘要</button></div>');
  const canContinue=view.session.summary.jobs.at(-1)?.state==='awaiting_compression';
  if(canContinue)root.insertAdjacentHTML('beforeend','<p class="runtime-warning">原摘要超过长度限制，等待一次压缩处理。</p><button type="button" data-continue>继续这次压缩</button>');
  root.insertAdjacentHTML('beforeend','<details data-section="text"><summary>查看当前摘要</summary><pre>'+esc(version?.effectiveText||'尚无有效摘要。')+'</pre></details><details data-section="records"><summary>更新记录</summary>'+view.session.summary.jobs.slice().reverse().map(j=>'<p>'+esc(summaryLabel[j.state]||j.state)+' · '+esc(j.operationId.slice(0,16))+'</p>').join('')+'</details><details data-section="installation" class="runtime-diagnostic"><summary>安装与权限</summary><p>安装供本地作品使用；授权仅限本会话。卸载保留历史与摘要，但会停用此服务中的自动总结。</p><ul>'+settings.plugin.permissions.map(p=>'<li>'+esc(p)+'</li>').join('')+'</ul>'+(settings.plugin.installed?'<button type="button" data-action="uninstall">卸载自动总结</button>':'')+'</details>');
  for(const d of root.querySelectorAll('details'))d.open=details.has(d.dataset.section);
  const blocked=busy||localBlocked()||!!settings.unconfirmed;
  root.querySelector('[data-model]').onclick=()=>{dialog.close();openModels();};
  for(const b of root.querySelectorAll('[data-action]')){
   const action=b.dataset.action;b.disabled=blocked||(action==='enable'&&(!consent||!settings.model||view.busy||view.pending));
   b.onclick=()=>execute('settings',summarySettingsIntent(settings,action,crypto.randomUUID()));
  }
  const c=root.querySelector('[data-consent]');if(c)c.onchange=()=>{root.querySelector('[data-action="enable"]').disabled=blocked||!c.checked||!settings.model||view.busy||view.pending;};
  const update=root.querySelector('[data-update]');if(update){update.disabled=!recordIsCurrent()||settings.coveredThrough>=Math.max(0,settings.completedTurns-1);update.onclick=()=>execute('update',summaryTaskIntent(view,crypto.randomUUID(),'update'));}
  const compress=root.querySelector('[data-continue]');if(compress){compress.disabled=busy||localBlocked()||view.busy;compress.onclick=()=>execute('continue',summaryTaskIntent(view,view.session.summary.jobs.at(-1).operationId,'continue'));}
  if(view.pending||localBlocked()||settings.unconfirmed){root.insertAdjacentHTML('beforeend','<p class="runtime-warning">有操作结果待核对，停用不会清除原任务。</p><button type="button" data-recover>恢复原操作</button>');root.querySelector('[data-recover]').onclick=recoverAll;root.querySelector('[data-recover]').disabled=busy||view.busy;}
  if(retryRecord){root.insertAdjacentHTML('beforeend','<p>已确认该设置未生效。</p><button type="button" data-retry>用原记录再次保存</button><button type="button" data-discard>放弃未生效设置</button>');root.querySelector('[data-retry]').onclick=()=>execute(retryRecord.kind,retryRecord.body,true);root.querySelector('[data-discard]').onclick=async()=>{try{await client.discardConfirmed(retryRecord);retryRecord=null;await refresh();}catch(e){showError(e);}};}
  if(busy)root.insertAdjacentHTML('beforeend','<p role="status">正在保存或核对，请稍候…</p>');
  if(error)root.insertAdjacentHTML('beforeend','<p role="status" class="runtime-warning'+(error.tone==='success'?' runtime-success':'')+'">'+esc(error.text)+'</p>');
 }
 document.addEventListener('close',event=>{if(event.target.matches('.model-settings-dialog'))refresh().catch(showError);},true);
 window.addEventListener('storage',event=>{if(event.key?.startsWith('modelmirror.authoring.'))refresh().catch(showError);});
 window.addEventListener('pagehide',()=>clearTimeout(timer));
 sourceButton.textContent='模型与参数';
 return {workChanged(){generation++;clearTimeout(timer);dialog?.close();view=null;settings=null;client=null;loadedWork=getWork()?.id||null;loading=true;error=null;retryRecord=null;render();refresh().catch(showError);},initialize(){refresh().catch(showError);}};
}
attachSessionPanel(install);
