import {modelSessionKey} from './session-selection.mjs';
import {mountComparison} from './comparison-ui.mjs';
import {attachSessionPanel} from './browser.mjs';
import {memoryRejectionMessage,createJointClient,jointTaskIntent,pluginIntent,memoryEditIntent,branchIntent,requirementsEditIntent,storyProblem} from './joint-client.mjs';
import {sessionPolicyText} from './session-client.mjs';
import {pendingOperation as pendingModelOperation} from './model-ui.mjs';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names={model:'模型选择',history:'历史窗口',summary:'自动总结',memory:'记忆宫殿',branch:'分支存档'};
const states={idle:'尚未更新',prepared:'等待处理',running:'处理中',dispatching:'处理中',compression_dispatching:'正在压缩',compression_unknown:'压缩结果待核对',unknown:'结果待核对',applied:'已更新',completed:'已完成',paused:'已暂停',not_dispatched:'未派发',rejected:'内容未通过校验',revoked:'授权已撤销',stale:'旧结果未采用',blocked:'已暂停'};
const errors={CONTROL_EXECUTION_DISABLED:'模型调试服务尚未开启，本次请求未发送、未占用额度。请联系本地维护者开启后再发送。',CONTROL_READINESS_UNAVAILABLE:'暂时无法确认模型服务是否可用。本次请求未发送、未占用额度，请稍后检查连接。',NETWORK_READ_FAILED:'暂未读取到最新状态。原配置与输入仍保留，请稍后刷新。',REQUIREMENTS_TEXT_LIMIT:'持续要求最多 8000 字，请缩短后保存。',REQUIREMENTS_CAPACITY:'持续要求的修订记录已满，本次未保存。已有要求与记录仍保留。',REQUIREMENTS_REVISION_CONFLICT:'持续要求已在其他页面修改。请刷新并核对，你的编辑仍保留。',REQUIREMENTS_POLICY_CONFLICT:'会话设置已变化。请刷新核对后再保存。',JOINT_STATE_REFRESH_FAILED:'操作已记录，但页面状态尚未刷新。请刷新会话状态，不必重复操作。',BRANCH_POINT_UNSETTLED:'这轮尚未收束，请核对原操作后再创建分支。',BRANCH_LINEAGE_INVALID:'分支来源记录不完整，请保留记录并核对。',MEMORY_MODEL_REQUIRED:'请先选择记忆模型。',SUMMARY_MODEL_REQUIRED:'请先选择摘要模型。',MODEL_SELECTION_REQUIRED:'请先选择剧情模型。',
 JOINT_BUDGET_EXHAUSTED:'剩余额度不足以完成本次剧情及后台整理。',JOINT_NO_TASKS:'当前没有需要补齐的回合。',OFFLINE_OPENING_MISMATCH:'首轮请使用本会话开局。',
 OFFLINE_REVISION_CONFLICT:'会话已更新，输入已保留。请刷新后核对。',MEMORY_REVISION_CONFLICT:'记忆已在其它位置更新，请核对后再保存。',MEMORY_POLICY_CONFLICT:'会话插件设置已变化，请刷新后核对。',
 JOINT_RESULT_PENDING:'有操作待核对，请先恢复原记录。',JOINT_SETTINGS_RESULT_UNKNOWN:'设置结果尚未确认，请恢复原记录。',JOINT_SETTINGS_RESULT_PENDING:'有插件设置待核对，请先恢复原记录。',
 JOINT_LOCAL_STORAGE_UNAVAILABLE:'浏览器无法保存恢复记录，操作尚未开始。',JOINT_RECEIPT_MISMATCH:'返回记录与原操作不一致，请保留页面并核对。',MEMORY_UPDATE_FAILED:'记忆整理已暂停。可核对记录后明确再次更新，或停用记忆宫殿。',SUMMARY_UPDATE_FAILED:'摘要整理已暂停。可核对后再次更新，或停用自动总结。',
 OFFLINE_FILES_CHANGED_RESTART_REQUIRED:'会话对应的运行版本已变化，请保留原记录。'};
const errorText=e=>errors[e?.code]||'操作未完成，原记录与输入已保留。';
const preflightMessages={
 rpg_authoring_execution_disabled:'模型调试服务已关闭，本次未占用调用额度。请开启服务后再发送。',
 rpg_authoring_dns_failed:'无法解析模型服务地址。请检查服务端 DNS 设置后再发送。',
 rpg_authoring_address_blocked:'模型地址解析到了受保护网络。请核对连接地址和 DNS 设置。',
 rpg_authoring_preflight_timeout:'连接预检超时。请检查服务端网络与 DNS 后再发送。',
 rpg_authoring_preflight_network:'连接预检失败。请检查服务端网络设置后再发送。',
 rpg_authoring_preflight_failed:'请求未通过发送前检查。请核对模型配置和连接。',
};
function problemText(p){
 if(p.state==='unknown')return {title:'剧情结果待核对',text:'请恢复原操作核对结果。输入与记录已保留，不会自动重发。'};
 if(p.fixture)return {title:'离线演练未完成',text:'这次固定样例返回了失败。输入和演练记录已保留。'};
 if(p.forbiddenReason)return {title:'模型服务拒绝了这次剧情请求（HTTP 403）',text:p.forbiddenReason};
 if(p.dispatched===false)return {title:'剧情请求未发出',text:(preflightMessages[p.code]||'请求在发送前已停止。请核对模型配置、连接和额度。')+' 输入已保留，不会自动重发。'};
 return {title:'剧情未完成',text:'输入与已收到的内容已保留。请查看请求详情，处理原因后再决定是否重新发送。'};
}
function problemHtml(operation,details=false){
 const p=storyProblem(operation);if(!p)return '';const message=problemText(p);
 let html='<p class="runtime-warning" role="status"><strong>'+esc(message.title)+'</strong>。'+esc(message.text)+'</p>';
 if(details){const dispatch=p.fixture?'离线样例，无供应商调用':p.dispatched===true?'已派发至供应商':p.dispatched===false?'未派发至供应商':'尚未确认';
  const statusLine=p.httpStatus===null?'':'\nHTTP 状态：'+p.httpStatus;
  html+='<details class="runtime-diagnostic" data-section="request-'+esc(p.operationId)+'"><summary>请求详情</summary><pre>'+esc('操作编号：'+p.operationId+'\n请求编号：'+(p.requestId||'尚未建立')+'\n派发状态：'+dispatch+statusLine+'\n诊断：'+(p.code||p.state))+'</pre><h4>玩家原始输入</h4><pre>'+esc(p.input)+'</pre>'+(p.text?'<h4>已收到的内容</h4><pre>'+esc(p.text)+'</pre>':'')+'</details>';
 }return html;
}
function install({api,getWork,getInput,setInput,openModels,notice}){
 const listeners=new Set();
 const snapshot=()=>({view,settings,reading,busy:busy||reading||!!view?.busy,canSend:!send.disabled,canSwitch:!busy&&!reading&&!view?.busy&&!view?.pending&&!blocked(),error:error?.text||null,controlled,estimate,routes:client?.branches()||[]});
 const controlled=document.body.dataset.execution==='controlled',sendLabel=controlled?'发送':'发送演练';
 const panel=document.createElement('section');panel.className='runtime-panel joint-panel';panel.setAttribute('aria-label','会话调试');document.querySelector('.preview-tabs').before(panel);
 const send=document.createElement('button');send.type='button';send.className='primary runtime-send';send.textContent=sendLabel;document.querySelector('.composer footer').append(send);
 const input=document.querySelector('#player-input');let workId=null,client=null,view=null,settings=null,busy=false,error=null,dialog=null,mode=null,estimate=null,sendBody=null,timer=null,estimateTimer=null,generation=0,quoteGeneration=0,retry=null,returnFocus=null,branchTurn=1,requirementsDraft=null,requirementsBase=null,requirementsOpen=false,reading=false,requirementsSessionId=null;
 const comparisonRoot=document.createElement('div');document.querySelector('.composer footer').before(comparisonRoot);let comparison=null,comparisonSession=null,comparisonBlocked=false,comparisonAvailable=false;
 api('/joint').then(r=>{comparisonAvailable=!!r.comparison;syncComparison();}).catch(()=>{});
 function syncComparison(){const id=view?.session.id;if(id===comparisonSession)return;comparison?.dispose();comparison=null;comparisonSession=null;comparisonBlocked=false;if(!id||!comparisonAvailable)return;comparisonSession=id;
  const base='/joint/comparisons/'+id;comparison=mountComparison(comparisonRoot,{api,base,sessionId:id,getInput,setInput,creator:true,onBlocked:value=>{if(comparisonBlocked===value)return;comparisonBlocked=value;render();if(!value)void quote();},onChange:async batch=>{const count=view?.session.turns.length??0;await refresh();if(view?.session.id!==id)return;const detail={workId,sessionId:id};if(view.session.turns.length>count)window.dispatchEvent(new CustomEvent('authoring-turn-saved',{detail}));else if(batch.status==='settled')window.dispatchEvent(new CustomEvent('authoring-session-changed',{detail}));},prepare:(b,c)=>api(base+'/'+b.plan.id+'/preview/'+c),release:token=>api('/presentations/release',{token})});
 }
 const requirementsDrafts=new Map();
 function selectRequirements(id){
  if(id===requirementsSessionId)return;
  if(requirementsSessionId)requirementsDrafts.set(requirementsSessionId,{draft:requirementsDraft,base:requirementsBase,open:requirementsOpen});
  const saved=requirementsDrafts.get(id);requirementsDraft=saved?.draft??null;requirementsBase=saved?.base??null;requirementsOpen=saved?.open??false;requirementsSessionId=id;
  if(retry?.kind==='requirements')retry=null;
 }
 const current=()=>workId===getWork()?.id;
 function pending(){return client?.pending()||[];}
 function blocked(){try{return !!pending().length||!!pendingModelOperation(localStorage,workId,getWork()?.publishedVersionId);}catch(e){error={text:errorText(e),code:e.code};return true;}}
 const ready=()=>view&&!comparisonBlocked&&!reading&&!busy&&!view.busy&&!view.pending&&!blocked()&&!settings?.unconfirmed;
 function issue(e){error={text:errorText(e),code:e?.code||'NETWORK_RESULT_UNKNOWN'};render();
  if(mode==='edit'&&dialog?.open)dialog.querySelector('[data-validation]').textContent=error.text;
 }
 function displayError(){return error?'<p class="runtime-warning" role="status">'+esc(error.text)+'</p><details class="runtime-diagnostic"><summary>诊断信息</summary><code>'+esc(error.code)+'</code></details>':'';}
 async function refresh(){
  const gen=++generation,w=getWork();quoteGeneration++;if(error?.source==='estimate')error=null;clearTimeout(timer);clearTimeout(estimateTimer);estimate=null;sendBody=null;
  if(!w){workId=null;client=null;view=null;settings=null;render();return;}workId=w.id;
  let id;try{id=JSON.parse(localStorage.getItem(modelSessionKey(workId,getWork()?.publishedVersionId))||'null');}catch{throw {code:'JOINT_LOCAL_RECORD_INVALID'};}
  if(!id){selectRequirements(null);client=null;view=null;settings=null;render();return;}
  const candidate=createJointClient({api,storage:localStorage,sessionId:id,workId});
  reading=true;render();let v,s;try{[v,s]=await Promise.all([candidate.view(),api('/joint/settings/'+id)]);}finally{if(gen===generation)reading=false;}
  if(gen!==generation||getWork()?.id!==w.id)return;
  if(w.publishedVersionId&&v.session.binding.versionId!==w.publishedVersionId)throw {code:'JOINT_LOCAL_RECORD_INVALID'};
  selectRequirements(v.session.id);client=candidate;view=v;settings=s;syncComparison();render();
  if(view.busy)timer=setTimeout(()=>refresh().catch(issue),1000);else quote();
 }
 async function quote(kind='send'){
  const gen=++quoteGeneration;estimate=null;sendBody=null;if(!ready()||!getInput().trim()&&kind==='send'){render();return;}
  const body=jointTaskIntent(view,crypto.randomUUID(),kind==='send'?getInput():null);
  try{const q=await api('/joint/estimate',body);if(gen!==quoteGeneration||!current()||body.input!==getInput()&&kind==='send')return;
   estimate=q;sendBody=body;if(error?.source==='estimate')error=null;render();return q;
  }catch(e){if(gen===quoteGeneration){error={text:errorText(e),code:e.code,source:'estimate'};render();}}
 }
 function render(){
  if(!current()&&workId)return;const local=blocked();
  panel.innerHTML='<div class="runtime-heading"><strong>会话调试</strong><span class="runtime-mode">'+(controlled?'受控模型调试':'离线演练')+'</span><button type="button" data-refresh aria-label="刷新会话状态">刷新</button></div>';
  if(!view){panel.insertAdjacentHTML('beforeend','<p>'+(reading?'正在读取已保存的会话…':'选择作品版本和剧情模型后，即可开始。')+'</p><button type="button" data-models>配置模型与会话</button>');panel.querySelector('[data-models]').onclick=()=>openModels();}
  else{
   document.querySelector('#model-button').textContent=controlled?'模型与参数':view.session.models.current.story?.entry.name||'添加模型';
   panel.insertAdjacentHTML('beforeend','<div class="runtime-overview"><span>'+esc(sessionPolicyText(view))+(view.effectivePolicy.authorization.memory.enabled?' · 关键词记忆':'')+'</span><span>'+view.session.turns.length+' 回合</span></div><div class="runtime-actions runtime-primary-actions"><button type="button" data-plugins>会话插件</button><button type="button" data-memory>记忆条目</button><button type="button" data-records>对话与记录</button>'+(!view.session.turns.length?'<button type="button" data-opening>填入本会话开局</button>':'')+'</div><p class="joint-budget">本地额度剩余 '+view.budget.remaining+' 次'+(view.budget.reserved?' · 正在预留 '+view.budget.reserved+' 次':'')+(estimate?' · 本次最多 '+estimate.plan.maximumCalls+' 次':'')+'</p>');
   panel.querySelector('[data-plugins]').onclick=()=>open('plugins');panel.querySelector('[data-memory]').onclick=()=>open('memory');panel.querySelector('[data-records]').onclick=()=>open('records');
   const opening=panel.querySelector('[data-opening]');if(opening)opening.onclick=()=>{setInput(view.session.binding.openingText);input.focus();quote();};
   if(comparisonBlocked&&!local&&!settings?.unconfirmed)panel.insertAdjacentHTML('beforeend','<p role="status">请在本轮对比中选择回复或核对进度。</p>');
   else if(view.busy)panel.insertAdjacentHTML('beforeend','<p role="status">正在处理，已完成的剧情会先保存…</p>');
   else if(view.pending||local||settings?.unconfirmed)panel.insertAdjacentHTML('beforeend','<p class="runtime-warning">有结果待核对，不会自动重发。</p><button type="button" data-recover>恢复原操作</button>');
   else if(view.pauseReason){
    const detail=view.pauseReason==='MEMORY_UPDATE_FAILED'?memoryRejectionMessage(view.session.memory.jobs.at(-1)):null;
    panel.insertAdjacentHTML('beforeend','<p class="runtime-warning">'+esc(detail||errors[view.pauseReason]||'后台整理已暂停，请核对记录。')+'</p>');
   }
   else panel.insertAdjacentHTML('beforeend',problemHtml(view.session.operations.at(-1)));
   if(panel.querySelector('[data-recover]'))panel.querySelector('[data-recover]').onclick=recover;
  }
  if(view)renderRequirements();
  if(retry?.kind==='requirements'){
   const recovery=document.createElement('div');recovery.className='runtime-actions';
   recovery.innerHTML='<p>已确认这次要求尚未保存。</p><button type="button" data-retry-requirements>按原记录再次保存</button><button type="button" data-discard-requirements>放弃这次保存</button>';
   panel.append(recovery);for(const b of recovery.querySelectorAll('button'))b.disabled=busy||reading;
   recovery.querySelector('[data-retry-requirements]').onclick=async()=>{const original=retry;if(await execute('requirements',original.body,true)&&view?.session.id===original.body.sessionId){requirementsDraft=null;requirementsBase=null;render();}};
   recovery.querySelector('[data-discard-requirements]').onclick=async()=>{if(busy)return;busy=true;render();try{await client.discardConfirmed(retry);retry=null;requirementsBase=null;await refresh();notice('已放弃这次未生效的保存，编辑文字仍保留。');}catch(e){issue(e);}finally{busy=false;render();if(ready())quote();}};
  }
  panel.insertAdjacentHTML('beforeend',displayError());panel.querySelector('[data-refresh]').onclick=()=>{error=null;refresh().catch(issue);};panel.querySelector('[data-refresh]').disabled=busy;
  if(view&&client){
   const routes=client.branches(),currentId=view.session.id,parents=new Set(routes.map(r=>r.parentSessionId)),items=new Map(routes.map(r=>[r.sessionId,r.name]));
   for(const id of parents)if(!items.has(id))items.set(id,'原路线');
   if(items.size){const nav=document.createElement('nav');nav.className='runtime-actions';nav.setAttribute('aria-label','切换路线');
    for(const [id,name]of items){const b=document.createElement('button');b.type='button';b.textContent=name;b.disabled=busy||blocked()||view.busy||view.pending||id===currentId;b.setAttribute('aria-current',id===currentId?'true':'false');b.onclick=()=>switchSession(id);nav.append(b);}panel.append(nav);}
  }
  send.disabled=!ready()||!sendBody||sendBody.kind!=='send'||sendBody.input!==getInput()||estimate.budget.remaining<estimate.plan.maximumCalls;
  send.textContent=busy||view?.busy?'处理中…':sendLabel;
  if(dialog?.open&&!['edit','branch'].includes(mode))renderDialog();
  for(const listener of listeners)listener(snapshot());
 }
 function renderRequirements(){
  const currentText=view.session.requirements?.text??'',text=requirementsDraft??currentText;
  const box=document.createElement('details');box.className='runtime-requirements';box.open=requirementsOpen;
  box.innerHTML='<summary>持续要求'+(currentText?' · 已设置':'')+'</summary><form><label>每轮携带的要求<textarea name="text" rows="4" maxlength="16000" placeholder="例如：使用第二人称；回复冗长；小说模式关闭。">'+esc(text)+'</textarea></label><p class="runtime-muted">保存后每轮携带。聊天输入不会自动改写这里；取消或 Esc 放弃未保存内容。</p><div class="runtime-actions"><span data-count>'+[...text].length+' / 8000 字</span><button type="submit">保存要求</button><button type="button" data-cancel>取消</button></div></form>';
  panel.append(box);box.ontoggle=()=>{requirementsOpen=box.open;};
  const form=box.querySelector('form'),save=form.querySelector('[type=submit]');form.elements.text.disabled=busy;save.disabled=!ready()||requirementsDraft===null||requirementsDraft===currentText;
  form.elements.text.oninput=()=>{if(requirementsBase===null)requirementsBase=requirementsEditIntent(view,crypto.randomUUID(),'');requirementsDraft=form.elements.text.value;form.querySelector('[data-count]').textContent=[...requirementsDraft].length+' / 8000 字';save.disabled=!ready()||requirementsDraft===currentText||[...requirementsDraft].length>8000;};
  const cancel=()=>{requirementsDraft=null;requirementsBase=null;requirementsOpen=false;render();panel.querySelector('.runtime-requirements summary')?.focus();};
  form.querySelector('[data-cancel]').onclick=cancel;
  form.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();cancel();}};
  form.onsubmit=async e=>{e.preventDefault();if(!ready()||requirementsDraft===null||[...requirementsDraft].length>8000)return;
   const body={...(requirementsBase??requirementsEditIntent(view,crypto.randomUUID(),'')),text:requirementsDraft};if(await execute('requirements',body)&&view?.session.id===body.sessionId){requirementsDraft=null;requirementsBase=null;render();}
   else if(error?.code?.endsWith('CONFLICT')){requirementsBase=null;}
  };
 }
 function close(){dialog?.close();}
 function open(kind){
  if(!view)return;if(dialog){mode=kind;renderDialog();return;}
  returnFocus=document.activeElement;mode=kind;dialog=document.createElement('dialog');dialog.className='runtime-dialog joint-dialog';
  dialog.innerHTML='<header><h2></h2><button type="button" aria-label="关闭会话窗口">×</button></header><div class="runtime-dialog-body"></div><footer><button type="button">关闭</button></footer>';
  const d=dialog;d.querySelector('header button').onclick=close;d.querySelector('footer button').onclick=close;
  d.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();d.close();}});
  d.addEventListener('close',()=>{d.remove();if(dialog===d){dialog=null;mode=null;}if(returnFocus?.isConnected)returnFocus.focus();else panel.querySelector('[data-plugins]')?.focus();},{once:true});
  document.body.append(d);renderDialog();d.showModal();
 }
 async function switchSession(id){
  if(busy||reading||view?.busy||view?.pending||blocked()||!getWork())return;busy=true;error=null;render();
  try{
   const key=modelSessionKey(workId,getWork()?.publishedVersionId),text=JSON.stringify(id);
   try{localStorage.setItem(key,text);if(localStorage.getItem(key)!==text)throw Error();}catch{throw {code:'JOINT_LOCAL_STORAGE_UNAVAILABLE'};}
   close();try{await refresh();}catch{throw {code:'JOINT_STATE_REFRESH_FAILED'};}
   window.dispatchEvent(new CustomEvent('authoring-session-changed'));
  }catch(e){issue(e);}finally{busy=false;render();if(ready())quote();}
 }
 async function execute(kind,body,again=false){
  if(busy||!client)return false;busy=true;error=null;render();const c=client,w=workId,oldInput=getInput(),previousTurns=view.session.turns.length;let acknowledged=false;
  try{const r=await c.submit(kind,body,again);acknowledged=true;if(w!==getWork()?.id)return false;
   if(kind==='send'&&r.operation?.state==='completed'&&getInput()===oldInput)setInput('');
   retry=null;if(kind==='branch'){busy=false;await switchSession(r.session.id);notice(r.display?.state==='pending'?'新路线已创建，原展示仍需核对。':'新路线已创建。请选择模型，并按需启用插件。');return true;}await refresh();if(kind==='send'&&w===getWork()?.id&&view.session.turns.length>previousTurns)window.dispatchEvent(new CustomEvent('authoring-turn-saved',{detail:{workId:w,sessionId:view.session.id}}));
   const problem=storyProblem(r.operation);
   notice(problem?problemText(problem).title+'，请查看对话与记录。':r.run?.state==='paused'?'后台整理已暂停，请查看对话与记录。':kind==='requirements'?'持续要求已保存，将用于之后的发送。':kind==='settings'?'会话插件设置已保存。':kind==='edit'?'记忆条目已保存。':'请求记录已保存。');return !problem;
  }catch(e){if(w===getWork()?.id)issue(acknowledged?{code:'JOINT_STATE_REFRESH_FAILED'}:e);return false;}finally{busy=false;render();if(current()&&ready())quote();}
 }
 send.onclick=()=>{if(ready()&&sendBody&&sendBody.input===getInput())execute('send',sendBody);};
 async function recover(){
  if(busy||!client||view?.busy)return;busy=true;error=null;render();
  try{
   const rows=pending();for(const run of view.session.runs.filter(r=>['prepared','running','unknown'].includes(r.state)))if(!rows.some(r=>r.body.operationId===run.operationId))rows.push({kind:run.intent.kind,sessionId:view.session.id,workId,body:run.intent});
   if(settings.unconfirmed&&!rows.some(r=>r.body.operationId===settings.unconfirmed.operationId))rows.push({kind:'settings',sessionId:view.session.id,workId,body:settings.unconfirmed});
   if(!rows.length){notice('请在模型设置中核对待确认的配置。');return;}
   for(const r of rows){const result=await client.recover(r);if(['not_applied','not_submitted','checkpoint_saved'].includes(result.state))retry=r;}
   await refresh();notice(view.pending?'原结果仍未确认，请保留记录。':'原记录已核对，没有重新派发。');
  }catch(e){issue(e);}finally{busy=false;render();if(ready())quote();}
 }
 function renderDialog(){
  const root=dialog.querySelector('.runtime-dialog-body'),title=mode==='plugins'?'会话插件':mode==='memory'?'记忆宫殿':mode==='branch'?'创建聊天分支':'对话与记录';
  dialog.querySelector('h2').textContent=title;dialog.setAttribute('aria-label',title);
  const expanded=new Set([...root.querySelectorAll('details[open]')].map(e=>e.dataset.section));
  const search=root.querySelector('[data-search]')?.value||'';
  if(mode==='plugins'){
   root.innerHTML='<p class="runtime-lead">插件分别安装，并由你决定是否用于本会话。</p>'+Object.entries(names).map(([purpose,name])=>{
    const p=settings.plugins[purpose],enabled=['branch','model'].includes(purpose)?p.enabled:view.effectivePolicy.authorization[purpose].enabled;
    const progress=purpose==='summary'?settings.summaryCoveredThrough+' / '+Math.max(0,settings.completedTurns-1):settings.memoryProcessedThrough+' / '+settings.completedTurns;
    return '<section class="joint-plugin"><h3>'+name+' <small>'+(enabled?'已启用':p.installed?'未启用':'未安装')+'</small></h3><p class="runtime-muted">'+(purpose==='model'?'管理本会话的剧情模型；摘要与记忆模型独立设置。':purpose==='history'?'选择携带的历史回合；自动总结启用时以摘要策略为准。':purpose==='summary'?'较早剧情整理为摘要，保留最近一回合原文。':purpose==='branch'?'从已完成的回合开始另一条路线，保留原路线。':'从完整原始剧情整理条目，按关键词召回。')+'</p>'+(['model','summary','memory'].includes(purpose)?'<dl class="runtime-facts"><div><dt>'+ (purpose==='model'?'剧情模型':'独立模型')+'</dt><dd>'+esc(settings.models[purpose==='model'?'story':purpose]?.entry.name||'尚未选择')+'</dd></div>'+(purpose==='model'?'':'<div><dt>处理进度</dt><dd>'+progress+' 回合</dd></div>')+'</dl><button type="button" data-model="'+(purpose==='model'?'story':purpose)+'">更改模型</button>':'')+
     (!p.installed?'<button type="button" data-purpose="'+purpose+'" data-action="install">安装'+name+'</button>':enabled?'<button type="button" data-purpose="'+purpose+'" data-action="disable">停用'+name+'</button>':'<label class="joint-consent"><input type="checkbox" data-consent="'+purpose+'"> 允许'+name+'用于本会话</label><button type="button" data-purpose="'+purpose+'" data-action="enable" disabled>启用'+name+'</button>')+
     (purpose==='history'&&enabled?'<form data-history><label>历史回合数<input type="number" min="1" max="20" name="turns" value="'+settings.history.turns+'" required></label><label><input type="checkbox" name="initial"'+(settings.history.includeInitialCharacter?' checked':'')+'> 保留开局原文</label><button type="submit">保存历史窗口</button></form>':'')+
     (p.installed?'<details data-section="'+purpose+'"><summary>安装设置</summary><p class="runtime-muted">卸载将停用此服务中的插件，保留已保存资料和调用记录。</p><button type="button" data-purpose="'+purpose+'" data-action="uninstall">卸载'+name+'</button></details>':'')+'</section>';
   }).join('')+'<p class="runtime-muted">启用摘要或记忆会增加独立模型调用；发送前显示本次上限。</p><button type="button" data-update>补齐尚未处理的剧情</button>';
   for(const b of root.querySelectorAll('[data-action]')){b.disabled=busy||blocked()||!!settings.unconfirmed||(b.dataset.action==='enable');b.onclick=()=>execute('settings',pluginIntent(settings,b.dataset.purpose,b.dataset.action,crypto.randomUUID()));}
   for(const c of root.querySelectorAll('[data-consent]'))c.onchange=()=>{const purpose=c.dataset.consent;root.querySelector('[data-purpose="'+purpose+'"][data-action="enable"]').disabled=!c.checked||!ready()||(['summary','memory'].includes(purpose)&&!settings.models[purpose]);};
   for(const b of root.querySelectorAll('[data-model]'))b.onclick=()=>{close();openModels(b.dataset.model);};
   const form=root.querySelector('[data-history]');if(form)form.onsubmit=e=>{e.preventDefault();if(ready()&&form.reportValidity())execute('settings',pluginIntent(settings,'history','configure',crypto.randomUUID(),{turns:Number(form.elements.turns.value),includeInitialCharacter:form.elements.initial.checked}));};
   const update=root.querySelector('[data-update]');update.disabled=!ready();update.onclick=async()=>{update.disabled=true;const activeDialog=dialog;const body=jointTaskIntent(view,crypto.randomUUID()),q=await api('/joint/estimate',body).catch(issue);if(update.isConnected)update.disabled=!ready();if(!q||dialog!==activeDialog||!dialog?.open||mode!=='plugins')return;const d=dialog.querySelector('.runtime-dialog-body');d.querySelector('.joint-update-confirm')?.remove();d.insertAdjacentHTML('beforeend','<section class="joint-update-confirm"><p>本次补齐最多使用 '+q.plan.maximumCalls+' 次调用；可用 '+q.budget.remaining+' 次。</p><button type="button" data-confirm-update>开始补齐</button><button type="button" data-cancel-update>取消</button></section>');const section=d.querySelector('.joint-update-confirm');section.querySelector('[data-confirm-update]').disabled=q.budget.remaining<q.plan.maximumCalls;section.querySelector('[data-confirm-update]').onclick=()=>execute('update',body);section.querySelector('[data-cancel-update]').onclick=()=>section.remove();};
  }else if(mode==='branch'){
   root.innerHTML='<form class="joint-edit"><p>从第 '+branchTurn+' 回合创建一条新路线。</p><label>路线名称<input name="name" maxlength="80" required value="第 '+branchTurn+' 回合 · 新路线"></label><p class="runtime-muted">继承到此回合的剧情、设置和记忆。新路线需重新选择模型并启用插件。</p><p data-validation role="status"></p><div class="runtime-actions"><button type="submit" class="primary">确认并创建新会话</button><button type="button" data-cancel>取消</button></div></form>';
   const form=root.querySelector('form');form.querySelector('[data-cancel]').onclick=close;
   form.onsubmit=async e=>{e.preventDefault();if(!ready()||!form.reportValidity())return;const name=form.elements.name.value.trim();if(!name||[...name].length>80){form.querySelector('[data-validation]').textContent='请输入 1–80 字的路线名称。';return;}
    const button=form.querySelector('[type=submit]');button.disabled=true;const ok=await execute('branch',branchIntent(view,crypto.randomUUID(),branchTurn,name));if(!ok&&button.isConnected){button.disabled=false;form.querySelector('[data-validation]').textContent=error?.text||'创建未完成，请核对原操作。';}
   };form.elements.name.focus();
  }else if(mode==='memory'){
   const entries=view.session.memory.snapshot.entries;root.innerHTML='<p class="runtime-lead">已整理到第 '+settings.memoryProcessedThrough+' 回合。人工保存的内容自动保护，模型不会改写。</p><div class="joint-memory-tools"><label>搜索条目<input type="search" data-search value="'+esc(search)+'" placeholder="名称、关键词或正文"></label><button type="button" data-new>新增条目</button></div><div data-entries></div><details data-section="recall"><summary>上次发送携带的记忆</summary><pre>'+esc(JSON.stringify(view.session.operations.at(-1)?.request.memoryContext.recall||'尚无发送记录',null,2))+'</pre></details>';
   const list=root.querySelector('[data-entries]');function showEntries(){const q=root.querySelector('[data-search]').value.toLocaleLowerCase();list.innerHTML=entries.filter(e=>[e.value.name,e.value.content,...e.value.keywords].join(' ').toLocaleLowerCase().includes(q)).map(e=>'<article class="joint-entry"><h3>'+esc(e.value.name)+'</h3><p class="runtime-muted">'+(e.deleted?'已删除':e.value.enabled?'参与召回':'已停用')+' · '+(e.protected?'人工保护':'允许自动更新')+'</p><p>'+esc(e.value.content)+'</p><div class="runtime-actions">'+(!e.deleted?'<button type="button" data-edit="'+e.id+'">编辑</button><button type="button" data-protect="'+e.id+'">'+(e.protected?'解除保护':'保护条目')+'</button><button type="button" data-delete="'+e.id+'">删除</button>':'')+'</div><details data-section="entry-'+e.id+'"><summary>修订与恢复</summary>'+e.revisions.slice().reverse().map(r=>'<section><p>版本 '+r.version+' · '+(r.source.kind==='manual'?'人工修改':'自动整理')+'</p><pre>'+esc(r.value.content)+'</pre><button type="button" data-restore="'+e.id+'" data-version="'+r.version+'">恢复此版本</button></section>').join('')+'</details></article>').join('')||'<p class="runtime-muted">暂无匹配条目。</p>';
    for(const b of list.querySelectorAll('button')){b.disabled=!ready()||!view.effectivePolicy.authorization.memory.enabled;b.onclick=()=>{const entry=entries.find(e=>e.id===b.dataset.edit||e.id===b.dataset.protect||e.id===b.dataset.delete||e.id===b.dataset.restore);if(b.dataset.edit)return edit(entry);const change={action:b.dataset.protect?'protect':b.dataset.delete?'delete':'restore',id:entry.id,baseVersion:entry.version,...(b.dataset.protect?{protected:!entry.protected}:{}),...(b.dataset.restore?{version:Number(b.dataset.version)}:{})};execute('edit',memoryEditIntent(view,crypto.randomUUID(),change));};}
   }root.querySelector('[data-search]').oninput=showEntries;showEntries();root.querySelector('[data-new]').disabled=!ready()||!view.effectivePolicy.authorization.memory.enabled;root.querySelector('[data-new]').onclick=()=>edit();
  }else{
    const unfinished=view.session.operations.filter(storyProblem);
    root.innerHTML=(unfinished.length?'<section><h3>未完成的剧情请求</h3>'+unfinished.map(op=>'<article class="runtime-turn">'+problemHtml(op,true)+'</article>').join('')+'</section>':'')+(view.session.turns.map(t=>'<article class="runtime-turn"><h3>第 '+t.number+' 回合</h3><button type="button" data-branch="'+t.number+'">从此回合创建分支</button><details><summary>玩家原始输入</summary><pre>'+esc(t.user)+'</pre></details><pre>'+esc(t.assistant)+'</pre></article>').join('')||'<p>尚无完整剧情。</p>');
    root.insertAdjacentHTML('beforeend','<details class="runtime-diagnostic" data-section="budget"><summary>额度如何计数</summary><p>本地额度已计 '+view.budget.used+' 次，剩余 '+view.budget.remaining+' 次。失败、取消和结果未确认的提交仍计入；未提交的后台任务释放预留。是否派发到供应商请查看各请求详情，本地计数不等于供应商生成次数。</p></details>');
   for(const b of root.querySelectorAll('[data-branch]')){b.disabled=!ready()||!settings.plugins.branch?.enabled;b.onclick=()=>{branchTurn=Number(b.dataset.branch);mode='branch';renderDialog();};}
   root.insertAdjacentHTML('beforeend','<details data-section="tasks"><summary>后台整理记录</summary>'+['summary','memory'].flatMap(p=>view.session[p].jobs.map(j=>'<article><h3>'+names[p]+' · '+esc(states[j.state]||j.state)+'</h3><pre>'+esc(j.receipt?.result.text||'尚无可确认结果')+'</pre></article>')).join('')+'</details>');
  }
  for(const d of root.querySelectorAll('details[data-section]'))d.open=expanded.has(d.dataset.section);
  if(retry&&retry.kind!=='requirements'){root.insertAdjacentHTML('beforeend','<p>'+ (retry.kind==='branch'?'请按原记录继续创建，保留同一条路线。':'已确认这项设置未生效，可按原记录再次保存或放弃。') +'</p><button type="button" data-retry>按原记录再次保存</button><button type="button" data-discard>放弃未生效设置</button>');root.querySelector('[data-retry]').onclick=()=>execute(retry.kind,retry.body,true);root.querySelector('[data-discard]').disabled=retry.kind==='branch';root.querySelector('[data-discard]').onclick=async()=>{try{await client.discardConfirmed(retry);retry=null;await refresh();}catch(e){issue(e);}};}
  root.insertAdjacentHTML('beforeend',displayError());
 }
 function edit(entry){
  const v=entry?.value||{name:'',keywords:[],match:'any',roles:['user','assistant'],content:'',enabled:true};mode='edit';dialog.querySelector('h2').textContent=entry?'编辑记忆':'新增记忆';dialog.setAttribute('aria-label',entry?'编辑记忆':'新增记忆');
  const root=dialog.querySelector('.runtime-dialog-body');root.innerHTML='<form class="joint-edit"><label>名称<input name="name" maxlength="120" required value="'+esc(v.name)+'"></label><label>关键词（每行一个）<textarea name="keywords" rows="3" required>'+esc(v.keywords.join('\n'))+'</textarea></label><label>匹配方式<select name="match"><option value="any"'+(v.match==='any'?' selected':'')+'>任一命中</option><option value="all"'+(v.match==='all'?' selected':'')+'>全部命中</option></select></label><fieldset><legend>扫描内容</legend><label><input type="checkbox" name="user"'+(v.roles.includes('user')?' checked':'')+'> 玩家原始输入</label><label><input type="checkbox" name="assistant"'+(v.roles.includes('assistant')?' checked':'')+'> AI 原始回复</label></fieldset><label>正文<textarea name="content" rows="8" required>'+esc(v.content)+'</textarea></label><label><input type="checkbox" name="enabled"'+(v.enabled?' checked':'')+'> 参与关键词召回</label><p class="runtime-muted">保存后自动保护；需要模型维护时，可在列表中明确解除保护。取消或 Esc 会放弃本次未保存编辑。</p><p data-validation role="status"></p><div class="runtime-actions"><button class="primary" type="submit">保存条目</button><button type="button" data-cancel>取消</button></div></form>';
  const form=root.querySelector('form');form.querySelector('[data-cancel]').onclick=()=>{mode='memory';renderDialog();};
  form.onsubmit=async event=>{event.preventDefault();if(!ready()||!form.reportValidity())return;const f=form.elements,value={name:f.name.value,keywords:f.keywords.value.split(/\r?\n/).map(v=>v.trim()).filter(Boolean),match:f.match.value,roles:['user','assistant'].filter(r=>f[r].checked),content:f.content.value,enabled:f.enabled.checked};
   const issue=value.roles.length===0?'至少选择一种扫描内容。':value.keywords.length>20||value.keywords.some(k=>[...k].length>80)?'最多 20 个关键词，每个最多 80 字。':new Set(value.keywords.map(k=>k.toLocaleLowerCase())).size!==value.keywords.length?'关键词不能重复。':[...value.content].length>2000?'正文最多 2000 字。':null;
   if(issue){form.querySelector('[data-validation]').textContent=issue;return;}
   form.querySelector('[data-validation]').textContent='';
   const change=entry?{action:'update',id:entry.id,baseVersion:entry.version,value}:{action:'create',value};
   if(await execute('edit',memoryEditIntent(view,crypto.randomUUID(),change))){mode='memory';renderDialog();}
  };form.elements.name.focus();
 }
 input.addEventListener('input',()=>{clearTimeout(estimateTimer);estimate=null;sendBody=null;render();estimateTimer=setTimeout(()=>quote(),250);});
 document.addEventListener('close',e=>{if(e.target.matches('.model-settings-dialog'))refresh().catch(issue);},true);
 window.addEventListener('storage',e=>{if(e.key?.startsWith('modelmirror.authoring.'))refresh().catch(issue);});window.addEventListener('pagehide',()=>{clearTimeout(timer);clearTimeout(estimateTimer);});
 return {subscribe(listener){listeners.add(listener);listener(snapshot());return()=>listeners.delete(listener);},open,refresh:()=>refresh().catch(issue),recover,send:()=>send.click(),switchSession,branch(turn){branchTurn=turn;open('branch');},initialize(){refresh().catch(issue);},workChanged(){comparison?.dispose();comparison=null;comparisonSession=null;comparisonBlocked=false;generation++;quoteGeneration++;selectRequirements(null);reading=false;close();view=null;settings=null;client=null;error=null;retry=null;workId=getWork()?.id||null;refresh().catch(issue);}};
}
attachSessionPanel(install);
