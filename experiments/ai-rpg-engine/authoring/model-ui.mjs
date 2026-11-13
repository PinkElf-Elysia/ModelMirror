import {modelSessionKey} from './session-selection.mjs';
import {openHistorySettings} from './history-ui.mjs';
// Browser-facing projection only. The host remains the authority for all choices.
const purposes={story:'剧情',summary:'摘要',memory:'记忆宫殿'};
const labels={temperature:'温度',topP:'Top P',maxOutputTokens:'最大回复长度'};
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const copy=value=>structuredClone(value);
const fail=(code,path='')=>{throw {code,path};};
const messages={
  MODEL_CONTROL_REFRESH_REQUIRED:'目录缓存已过期，请刷新目录后重新核对。',
  MODEL_CONTROL_TIMEOUT:'控制面校验超时，原配置保留；不会自动重试。',
  MODEL_CONTROL_CHANGED:'控制面能力已变化，请刷新目录后重新核对。',
  MODEL_PARAMETERS_REJECTED:'控制面未接受这些参数，原配置保留。',
  MODEL_CONTROL_UNAVAILABLE:'控制面目录暂不可用，原配置保留。',
  MODEL_CONTROL_UNAUTHORIZED:'模型连接尚未就绪，请在平台设置中核对认证。',
  MODEL_CATALOG_CHANGED:'目录已变化，请刷新目录并重新选择，未覆盖原配置。',
  MODEL_SELECTION_CHANGED:'这条模型线路已变化，请重新选择。',
  MODEL_SELECTION_UNAVAILABLE:'这条模型线路当前不可用。',
  MODEL_CAPABILITY_UNKNOWN:'缺少参数能力，不能配置这条模型。',
  MODEL_PARAMETER_RANGE:'参数必须在已声明范围内，输出长度必须为整数。',
  MODEL_PARAMETER_REQUIRED:'请填写必需的参数。',
  MODEL_PARAMETER_CONFLICT:'这些采样参数互斥，请只启用其中一项。',
  MODEL_PARAMETER_EXCLUSIVE:'这些采样参数互斥，请只启用其中一项。',
  MODEL_PARAMETER_UNSUPPORTED:'所选模型不支持这个参数。',
  MODEL_REVISION_CONFLICT:'其他标签已修改模型配置。本次编辑保留，请先核对最新状态。',
  OFFLINE_REVISION_CONFLICT:'会话已变化。本次编辑保留，请先核对最新状态。',
  OFFLINE_SESSION_BUSY:'会话正在处理任务，暂不能更换配置。',
  OFFLINE_RESULT_PENDING:'会话有未确认结果，须先核对原请求。',
  OFFLINE_FILES_CHANGED_RESTART_REQUIRED:'本预览的实现已变化，请使用对应冻结服务。',
  OFFLINE_SESSION_NOT_FOUND:'未找到原会话。引用已保留，请检查是否打开了正确预览。',
  MODEL_UI_STORAGE_UNAVAILABLE:'浏览器不能保存操作记录，未开始写入。',
  MODEL_UI_REFERENCE_MISMATCH:'会话与当前作品不一致，未采用。',
  HISTORY_SETTINGS_RESULT_PENDING:'会话插件有配置待核对，请打开会话插件核对原操作。',
  MODEL_UI_PENDING:'上一次配置结果尚未核对，请先核对原操作。',
  NETWORK_READ_FAILED:'暂未读取到最新配置。原配置仍保留，请稍后刷新。',
  NETWORK_RESULT_UNKNOWN:'结果尚未确认，原操作已保留，不会自动重发。',
};
export const modelError=error=>(messages[error?.code]||'操作未完成，原配置仍保留。')+(error?.path?' 字段：'+error.path:'')+' ['+(error?.code||'NETWORK_RESULT_UNKNOWN')+']';
export function modelPluginSettingsPath(session){
  if(['modelmirror.rpg.offline-session/5','modelmirror.rpg.offline-session/6','modelmirror.rpg.controlled-session/1','modelmirror.rpg.controlled-session/2'].includes(session.format))return '/joint/settings/'+session.id;
  return session.history?'/history-settings/sessions/'+session.id:null;
}
export function parameterDraft(entry,parameters=null){
  const values={},enabled={};
  for(const [key,f] of Object.entries(entry?.parameterPolicy?.fields||{})){
    values[key]=String(parameters&&Object.hasOwn(parameters,key)?parameters[key]:f.defaultValue);
    enabled[key]=parameters?Object.hasOwn(parameters,key):true;
  }
  if(!parameters)for(const group of entry?.parameterPolicy?.exclusive||[]){
    const keep=group.find(k=>entry.parameterPolicy.fields[k].required)||group[0];
    for(const key of group)if(key!==keep)enabled[key]=false;
  }
  return {values,enabled};
}
export function parametersFromDraft(entry,draft){
  if(!entry?.parameterPolicy)fail('MODEL_CAPABILITY_UNKNOWN');
  const result={};
  for(const [key,f] of Object.entries(entry.parameterPolicy.fields)){
    if(!draft.enabled[key]){if(f.required)fail('MODEL_PARAMETER_REQUIRED',key);continue;}
    const raw=draft.values[key];
    if(typeof raw!=='string'||!raw.trim())fail('MODEL_PARAMETER_REQUIRED',key);
    const value=Number(raw);
    if(!Number.isFinite(value)||value<f.minimum||value>f.maximum||(key==='maxOutputTokens'&&!Number.isSafeInteger(value)))fail('MODEL_PARAMETER_RANGE',key);
    result[key]=value;
  }
  for(const group of entry.parameterPolicy.exclusive)if(group.filter(k=>Object.hasOwn(result,k)).length>1)fail('MODEL_PARAMETER_EXCLUSIVE',group.join('/'));
  return result;
}
export function parameterStep(key,field,value){
  if(key==='maxOutputTokens')return '1';
  for(let places=key==='temperature'?1:2;places<=6;places++){
    const scale=10**places;
    if([field.minimum,field.maximum,Number(value)].every(n=>Math.abs(n*scale-Math.round(n*scale))<1e-8))return String(1/scale);
  }
  return 'any';
}
export function filteredModels(catalog,query='',family='All'){
  const q=query.trim().toLowerCase();
  return catalog.models.filter(e=>(family==='All'||e.modelId.split('/')[0]===family)&&(!q||(e.name+' '+e.modelId).toLowerCase().includes(q)));
}
const canonical=value=>JSON.stringify(value,(_,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
export function savedOperation(view,record){
  if(!view||view.session.binding.workId!==record.workId)return false;
  if(record.kind==='create')return view.session.create.operationId===record.body.operationId && view.session.binding.versionId===record.body.reference.versionId && view.session.binding.openingText===record.body.openingText && view.session.presetId===record.body.presetId;
  const change=view.session.models.changes.find(c=>c.intent.operationId===record.body.operationId);
  return !!change&&canonical(change.intent)===canonical(record.body);
}
const pendingKey=(id,op='',versionId=null)=>(versionId?'modelmirror.authoring.published-model-pending/'+id+'/'+versionId+'/':'modelmirror.authoring.model-pending/'+id+'/')+op;
export function pendingOperation(storage,workId,versionId=null){
  try{
    const key=Object.keys(storage).filter(key=>key.startsWith(pendingKey(workId,'',versionId))).sort()[0];
    if(!key)return null;
    const record=JSON.parse(storage.getItem(key));
    if(!record||record.workId!==workId||!['create','configure'].includes(record.kind)||typeof record.body?.operationId!=='string'||key!==pendingKey(workId,record.body.operationId,versionId))fail('MODEL_UI_STORAGE_UNAVAILABLE');
    return record;
  }catch{fail('MODEL_UI_STORAGE_UNAVAILABLE');}
}
const path='/model-settings';
export function createModelSettingsUI({button,api,getWork,getOpeningText,notice}){
  const style=document.createElement('link');style.rel='stylesheet';style.href='./model-ui.css';document.head.append(style);
  let active=null,enabled=false,initializing=false,realProvider=false;
  const store=(key,value)=>{try{if(value===null)localStorage.removeItem(key);else localStorage.setItem(key,JSON.stringify(value));}catch{fail('MODEL_UI_STORAGE_UNAVAILABLE');}};
  const read=key=>{try{const s=localStorage.getItem(key);return s?JSON.parse(s):null;}catch{fail('MODEL_UI_STORAGE_UNAVAILABLE');}};
  function close(){if(active){active.picker?.close();active.dialog.close();active=null;}}
  function dialog(title,className='model-settings-dialog'){
    const node=document.createElement('dialog');node.className=className;
    node.setAttribute('aria-label',title);
    node.innerHTML='<header><h2>'+escape(title)+'</h2><button type="button" class="icon-button" aria-label="关闭'+escape(title)+'">×</button></header><div class="model-body"></div><footer class="model-footer"></footer>';
    node.querySelector('header button').onclick=()=>node.close();
    node.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();node.close();}});
    node.addEventListener('click',event=>{if(event.target!==node)return;const b=node.getBoundingClientRect();if(event.clientX<b.left||event.clientX>b.right||event.clientY<b.top||event.clientY>b.bottom)node.close();});
    document.body.append(node);node.addEventListener('close',()=>node.remove(),{once:true});node.showModal();return node;
  }
  const current=c=>active===c&&c.dialog.open&&getWork()?.id===c.workId;
  const status=(c,text)=>{if(current(c))c.dialog.querySelector('[data-model-status]').textContent=text;};
  function choice(c){return c.entry?{catalogRevision:c.catalog.revision,selectionId:c.entry.selectionId,selectionRevision:c.entry.selectionRevision,parameters:parametersFromDraft(c.entry,c.draft)}:null;}
  function setDraft(c,purpose){
    c.purpose=purpose;
    const saved=c.view?.session.models.current[purpose];
    c.entry=saved?copy(saved.entry):null;c.draft=parameterDraft(c.entry,saved?.parameters);c.saved=saved;
  }
  function lock(c,value){
    c.busy=value;
    const blocked=value||!!c.pending||!!c.historyPending||!!c.view?.busy||!!c.view?.pending||!!c.blockingError;
    for(const node of c.dialog.querySelectorAll('[data-mutation], [data-purpose], [data-parameters] input')){
      const key=node.id.startsWith('model-param-')?node.id.slice('model-param-'.length):null;
      node.disabled=blocked||(key&&!c.draft.enabled[key])||(node.hasAttribute('data-save')&&(!c.entry||!c.catalog));
    }
    const refresh=c.dialog.querySelector('[data-refresh]');if(refresh)refresh.disabled=value||!!c.pending;
    const history=c.dialog.querySelector('[data-history]');if(history)history.disabled=value||!!c.pending;
  }
  async function refreshView(c,preserve=false){
    if(!c.sessionId)return;
    const view=await api(path+'/sessions/'+c.sessionId);
    if(view.session.binding.workId!==c.workId||(c.work.publishedVersionId&&view.session.binding.versionId!==c.work.publishedVersionId))fail('MODEL_UI_REFERENCE_MISMATCH');
    if(!current(c))return;
    const settingsPath=modelPluginSettingsPath(view.session);
    c.historyPending=settingsPath?!!(await api(settingsPath)).unconfirmed:false;
    c.view=view;button.textContent=view.session.models.current.story?.entry.name||'添加模型';
    if(!preserve)setDraft(c,c.purpose);
  }
  async function catalog(c,refresh=false){
    const result=await api(path+(refresh?'/refresh':'/catalog'),refresh?{}:undefined);
    if(current(c)){c.catalog=result.catalog;c.realProvider=result.realProvider===true;return result.catalog;}
  }
  async function load(c){
    c.pending=pendingOperation(localStorage,c.workId,c.work.publishedVersionId);
    const id=read(modelSessionKey(c.workId,c.work.publishedVersionId));
    if(typeof id==='string'&&/^session-[a-f0-9]{64}$/.test(id))c.sessionId=id;
    if(c.sessionId)await refreshView(c);
    try{await catalog(c,true);}catch(error){c.catalog=null;c.catalogError=modelError(error);}
    if(current(c))render(c);
  }
  async function execute(c,record){
    if(c.busy)return;
    try{store(pendingKey(c.workId,record.body.operationId,c.work.publishedVersionId),record);}catch(error){status(c,modelError(error));return;}
    c.pending=record;lock(c,true);status(c,'正在保存配置…');
    let persisted=false;
    try{
      if(record.kind==='configure')await api(path+'/check',{choice:record.body.choice});
      const result=await api(path+(record.kind==='create'?'/sessions':'/configure'),record.body);
      if(record.kind==='create'){
        const id=result.session?.id;if(!/^session-[a-f0-9]{64}$/.test(id||''))fail('NETWORK_RESULT_UNKNOWN');
        record.sessionId=id;c.sessionId=id;store(modelSessionKey(c.workId,c.work.publishedVersionId),id);
      }
      const view=await api(path+'/sessions/'+(record.sessionId||c.sessionId));
      if(!savedOperation(view,record))fail('NETWORK_RESULT_UNKNOWN');
      store(pendingKey(c.workId,record.body.operationId,c.work.publishedVersionId),null);persisted=true;
      if(!current(c))return;
      c.pending=pendingOperation(localStorage,c.workId,c.work.publishedVersionId);c.view=view;delete c.drafts[c.purpose];setDraft(c,c.purpose);render(c);status(c,c.pending?'已保存本次配置；另有原操作待核对，暂不能继续修改。':'已保存至本会话。未发送模型请求。');
      button.textContent=view.session.models.current.story?.entry.name||'添加模型';
    }catch(error){
      // A definite validation rejection cannot have applied. Transport/storage outcomes retain intent.
      if(error.httpStatus&&error.httpStatus<500&&!/UNKNOWN|STORAGE|WRITE_FAILED|OWNER|PENDING/.test(error.code||'')){
        try{store(pendingKey(c.workId,record.body.operationId,c.work.publishedVersionId),null);if(current(c))c.pending=null;}catch{/* Keep the original record. */}
      }
      if(current(c)){render(c);status(c,modelError(error));}
    }finally{if(current(c)){lock(c,false);if(!persisted&&c.pending)renderPending(c);}}
  }
  function renderPending(c){
    const root=c.dialog.querySelector('[data-pending]');if(!root)return;
    root.innerHTML=c.pending?'<p>有配置结果待核对。操作 '+escape(c.pending.body.operationId)+'</p><button type="button" data-reconcile>核对原操作</button><button type="button" data-retry hidden>用原编号再次保存</button>':'';
    if(!c.pending)return;
    root.querySelector('[data-reconcile]').onclick=async()=>{
      if(c.busy)return;lock(c,true);
      try{
        const record=c.pending,result=await api(path+'/reconcile',{kind:record.kind,operationId:record.body.operationId,sessionId:record.sessionId||null});
        if(!current(c))return;
        if(result.found){
          if(!savedOperation(result.view,record))fail('MODEL_UI_REFERENCE_MISMATCH');
          c.sessionId=result.view.session.id;store(modelSessionKey(c.workId,c.work.publishedVersionId),c.sessionId);store(pendingKey(c.workId,record.body.operationId,c.work.publishedVersionId),null);c.pending=pendingOperation(localStorage,c.workId,c.work.publishedVersionId);c.view=result.view;delete c.drafts[record.body.purpose];setDraft(c,c.purpose);render(c);status(c,c.pending?'已核对这份回执；另有原操作待核对，暂不能继续修改。':'已核对原配置回执，没有重新提交。');
        }else{status(c,'未找到原配置回执。没有自动提交；可用原编号再次保存。');root.querySelector('[data-retry]').hidden=false;}
      }catch(error){status(c,modelError(error));}finally{if(current(c))lock(c,false);}
    };
    root.querySelector('[data-retry]').onclick=()=>execute(c,c.pending);
  }
  function render(c){
    if(!current(c))return;
    const s=c.view?.session,blocked=!!c.pending||!!c.historyPending||c.view?.busy||c.view?.pending;
    const body=c.dialog.querySelector('.model-body');
    body.innerHTML='<p class="model-source">'+(c.catalog?.source==='control-plane-cache'||c.realProvider?'可用模型目录':'固定样例目录')+' · '+(c.realProvider?'发送时调用模型':'离线演练')+'</p><p data-model-status role="status">'+escape(c.catalogError||'')+'</p><div data-pending></div>';
    if(!s){
      body.insertAdjacentHTML('beforeend','<h3>选择作品版本</h3><p class="model-hint">选择一个版本开始调试，将使用右侧输入框中的开局文本。未保存的草稿不会带入会话。</p>'+c.work.versions.slice().reverse().map(v=>'<button type="button" class="model-version" data-mutation data-version="'+escape(v.id)+'">版本 '+v.number+' · '+escape(v.createdAt.replace('T',' ').slice(0,16))+'<span>创建会话</span></button>').join('')+(!c.work.versions.length?'<p>尚无版本。请关闭窗口，先通过“作品版本”生成版本。</p>':''));
      for(const b of body.querySelectorAll('[data-version]'))b.onclick=()=>{
        const record={kind:'create',workId:c.workId,sessionId:null,body:{operationId:crypto.randomUUID(),expectedRevision:null,reference:{workId:c.workId,versionId:b.dataset.version},openingText:getOpeningText(),presetId:null}};
        execute(c,record);
      };
      c.dialog.querySelector('.model-footer').innerHTML='<button type="button" data-close>关闭</button>';c.dialog.querySelector('[data-close]').onclick=()=>c.dialog.close();renderPending(c);lock(c,false);return;
    }
    body.insertAdjacentHTML('beforeend','<p class="model-hint">当前会话 · '+(c.pending||c.historyPending||c.view.pending?'结果待核对':c.view.busy?'处理中':'可配置')+'</p><nav class="model-purposes" aria-label="模型用途">'+Object.entries(purposes).map(([key,label])=>'<button type="button" data-purpose="'+key+'" aria-pressed="'+(key===c.purpose)+'">'+label+'</button>').join('')+'</nav><p class="model-hint">'+(c.purpose==='story'?'剧情模型仅影响本会话后续请求。':c.purpose==='summary'&&s.summary?'自动总结启用后使用此模型，剧情模型保持独立。':c.purpose==='memory'&&s.memory?'记忆宫殿启用后使用此模型，剧情与摘要模型保持独立。':'该用途目前仅保存独立的模型配置。')+'</p><div class="model-selection"><strong>模型</strong><button type="button" data-select data-mutation aria-haspopup="dialog">'+escape(c.entry?.name||'选择模型')+' ⌄</button></div><div data-parameters></div><details class="model-evidence"><summary>当前生效配置</summary><p>会话 '+escape(s.id.slice(-10))+' · 配置版本 '+s.models.revision+'</p><p>'+escape(s.models.current[c.purpose]?.entry.name||'尚未选择')+'</p><pre>'+escape(JSON.stringify(s.models.current[c.purpose]?.wireParameters||{},null,2))+'</pre><p>创建、切换模型和保存参数均不授权插件、不增加额度。</p></details>');
    for(const b of body.querySelectorAll('[data-purpose]'))b.onclick=()=>{
      c.drafts[c.purpose]={entry:c.entry,draft:c.draft};
      const next=b.dataset.purpose;setDraft(c,next);
      if(c.drafts[next])Object.assign(c,c.drafts[next]);render(c);
    };
    if(s.history&&!s.memory){
      body.insertAdjacentHTML('beforeend','<button type="button" data-history aria-haspopup="dialog">会话插件 · 历史窗口</button><p class="model-hint">安装后在会话中启用。关闭或取消会放弃未保存的编辑。</p>');
      const historyButton=body.querySelector('[data-history]');historyButton.disabled=c.busy||!!c.pending;
      historyButton.onclick=()=>{
        c.picker=openHistorySettings({api,sessionId:c.sessionId,workId:c.workId,dialog,onClose:async()=>{
          c.picker=null;
          try{await refreshView(c,true);if(current(c)){render(c);c.dialog.querySelector('[data-history]')?.focus();}}
          catch(error){if(current(c)){c.blockingError=true;lock(c,false);status(c,modelError(error));}}
        }});
      };
    }
    body.querySelector('[data-select]').onclick=()=>picker(c);
    parameters(c);
    c.dialog.querySelector('.model-footer').innerHTML='<button type="button" data-refresh>刷新目录</button><button type="button" data-cancel>取消</button><button type="button" class="primary" data-save data-mutation>保存配置</button>';
    c.dialog.querySelector('[data-cancel]').onclick=()=>c.dialog.close();
    c.dialog.querySelector('[data-refresh]').onclick=async()=>{
      if(c.busy||c.pending)return;lock(c,true);
      try{await catalog(c,true);await refreshView(c,true);if(current(c)){c.catalogError='';render(c);status(c,'目录与会话状态已刷新，本次编辑仍保留。请重新核对所选模型再保存。');}}
      catch(error){if(current(c)){c.catalog=null;c.catalogError=modelError(error);render(c);}}
      finally{if(current(c))lock(c,false);}
    };
    c.dialog.querySelector('[data-save]').onclick=async()=>{
      if(c.busy||blocked)return;
      try{
        if(!c.catalog)fail('MODEL_CONTROL_REFRESH_REQUIRED');
        if(!c.entry)fail('MODEL_PARAMETER_REQUIRED','模型');
        const selected=choice(c);
        // Freeze versions when the editor opens. Do not silently refresh them at save time.
        const record={kind:'configure',workId:c.workId,sessionId:c.sessionId,body:{sessionId:c.sessionId,operationId:crypto.randomUUID(),expectedRevision:c.view.session.revision,expectedModelRevision:c.view.session.models.revision,purpose:c.purpose,choice:selected}};
        await execute(c,record);
      }catch(error){status(c,modelError(error));}
    };
    renderPending(c);lock(c,false);
    c.dialog.querySelector('[data-save]').disabled=blocked||!c.entry||!c.catalog;
    if(blocked)status(c,c.historyPending?'会话插件有配置待核对，请打开会话插件核对原操作。':c.view.pending?'原剧情请求结果待核对，模型配置已锁定。':'配置暂不可修改，请先核对原操作。');
  }
  function parameters(c){
    const root=c.dialog.querySelector('[data-parameters]');
    if(!c.entry){root.innerHTML='<p class="model-hint">明确选择模型后显示它支持的参数。</p>';return;}
    const policy=c.entry.parameterPolicy;
    if(!policy){root.innerHTML='<p>此模型未提供可验证的参数能力。</p>';return;}
    const unavailable=!c.catalog?.models.some(e=>e.selectionId===c.entry.selectionId&&e.selectionRevision===c.entry.selectionRevision&&e.eligible);
    root.innerHTML='<h3>参数</h3>'+(unavailable?'<p class="model-warning">保存的模型当前不可用或已变更，请重新选择。</p>':'')+(policy.kind==='fixed'?'<p class="model-hint">固定档，仅展示已验证值。</p>':'')+Object.entries(policy.fields).sort(([a],[b])=>Object.keys(labels).indexOf(a)-Object.keys(labels).indexOf(b)).map(([key,f])=>{
      const fixed=policy.kind==='fixed'||f.minimum===f.maximum,value=c.draft.values[key],checked=c.draft.enabled[key];
      return '<section class="model-parameter"><div><label for="model-param-'+key+'">'+labels[key]+'</label>'+(!f.required?'<label class="model-optional"><input type="checkbox" data-enable="'+key+'"'+(checked?' checked':'')+'> 发送此参数</label>':'')+'</div><p class="model-hint">min: '+f.minimum+'　max: '+f.maximum+'</p><div class="model-slider-row">'+(fixed?'<output>'+escape(value)+'</output>':'<input type="range" id="model-param-'+key+'" aria-label="'+labels[key]+'" min="'+f.minimum+'" max="'+f.maximum+'" step="'+parameterStep(key,f,value)+'" value="'+escape(value)+'"'+(!checked?' disabled':'')+'><output data-value="'+key+'">'+escape(value)+'</output>')+'</div></section>';
    }).join('')+(policy.exclusive.length?'<p class="model-hint">互斥参数：'+escape(policy.exclusive.map(g=>g.map(k=>labels[k]).join(' / ')).join('；'))+'。请只启用一项。</p>':'')+'<p class="model-hint">切换模型会重置本次编辑的参数；未支持的字段不会发送。取消或 Esc 放弃未保存编辑。</p>';
    for(const range of root.querySelectorAll('input[type="range"]'))range.oninput=()=>{const key=range.id.slice('model-param-'.length);c.draft.values[key]=range.value;root.querySelector('[data-value="'+key+'"]').textContent=range.value;};
    for(const check of root.querySelectorAll('[data-enable]'))check.onchange=()=>{c.draft.enabled[check.dataset.enable]=check.checked;parameters(c);};
    for(const node of root.querySelectorAll('input')){if(c.busy||c.pending||c.view.busy||c.view.pending)node.disabled=true;}
  }
  function picker(c){
    if(!c.catalog){status(c,'请先刷新目录。');return;}
    const node=dialog('选择模型','model-picker-dialog');c.picker=node;
    let family='All',query='';
    const families=['All',...new Set(c.catalog.models.map(e=>e.modelId.split('/')[0]))];
    node.querySelector('.model-body').innerHTML='<nav class="model-families" aria-label="模型类别">'+families.map(f=>'<button type="button" data-family="'+escape(f)+'" aria-pressed="'+(f===family)+'">'+escape(f==='All'?'全部':f)+'</button>').join('')+'</nav><label class="model-search"><span>搜索模型</span><input type="search" placeholder="搜索名称或模型 ID" aria-label="搜索模型" autocomplete="off"></label><div class="model-results"></div>';
    const list=()=>{
      const entries=filteredModels(c.catalog,query,family);
      node.querySelector('.model-results').innerHTML=entries.length?entries.map(e=>{
        const route=c.catalog.models.filter(x=>x.modelId===e.modelId).findIndex(x=>x.selectionId===e.selectionId)+1;
        const reason=e.eligible?(e.parameterPolicy.kind==='fixed'?'固定参数':'可配置参数'):(messages[e.reason]||'当前不可选择');
        return '<button type="button" class="model-row" data-choice="'+escape(e.selectionId)+'"'+(!e.eligible?' disabled':'')+'><strong>'+escape(e.name)+'</strong><span>'+escape(e.modelId)+' · 线路 '+route+'</span><span>'+escape(reason)+'</span>'+(c.entry?.selectionId===e.selectionId?'<em>当前选择</em>':'')+'</button>';
      }).join(''):'<p class="model-hint">没有匹配的模型。</p>';
      for(const b of node.querySelectorAll('[data-choice]'))b.onclick=()=>{
        c.entry=copy(c.catalog.models.find(e=>e.selectionId===b.dataset.choice));c.draft=parameterDraft(c.entry);node.close();render(c);status(c,'已更新本次编辑，请点“保存配置”后生效。');c.dialog.querySelector('[data-select]').focus();
      };
    };
    node.querySelector('input').oninput=event=>{query=event.target.value;list();};
    for(const b of node.querySelectorAll('[data-family]'))b.onclick=()=>{family=b.dataset.family;for(const t of node.querySelectorAll('[data-family]'))t.setAttribute('aria-pressed',String(t===b));list();};
    node.querySelector('.model-footer').innerHTML='<span class="model-hint">这里显示当前连接中的可用模型。</span><button type="button">返回参数</button>';node.querySelector('footer button').onclick=()=>node.close();
    node.addEventListener('close',()=>{c.picker=null;if(current(c))c.dialog.querySelector('[data-select]')?.focus();},{once:true});list();node.querySelector('input').focus();
  }
  async function open(purpose='story'){
    if(!['story','summary','memory'].includes(purpose))return;
    if(!getWork()?.id){notice('请先创建或打开作品。');return;}
    close();
    const c={dialog:dialog('模型与参数'),workId:getWork().id,work:copy(getWork()),purpose,view:null,sessionId:null,catalog:null,realProvider,catalogError:'',pending:null,busy:false,picker:null,drafts:{}};active=c;
    c.dialog.querySelector('.model-body').innerHTML='<p role="status">正在读取配置…</p>';
    c.dialog.addEventListener('close',()=>{c.picker?.close();if(active===c)active=null;button.focus({preventScroll:true});},{once:true});
    try{await load(c);}catch(error){if(current(c)){c.blockingError=true;render(c);lock(c,false);status(c,modelError(error));}}
  }
  return {
    open,
    async initialize(){
      if(initializing)return;initializing=true;button.disabled=true;button.textContent='正在读取模型入口';
      try{const availability=await api(path);enabled=availability.enabled;realProvider=availability.realProvider===true;button.disabled=!enabled;button.textContent=enabled?'添加模型':'模型配置未启用';button.onclick=()=>open();}
      catch{button.disabled=false;button.textContent='重试模型入口';button.onclick=()=>this.initialize();}
      finally{initializing=false;}
    },
    workChanged(){close();if(enabled)button.textContent='添加模型';},
  };
}
