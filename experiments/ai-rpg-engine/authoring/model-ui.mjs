import {modelSessionKey} from './session-selection.mjs';
import {openHistorySettings} from './history-ui.mjs';
// Browser-facing projection only. The host remains the authority for all choices.
const purposes={story:'剧情',summary:'摘要',memory:'记忆宫殿'};
const labels={temperature:'温度',topP:'Top P',maxOutputTokens:'最大回复长度'};
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const copy=value=>structuredClone(value);
const fail=(code,path='')=>{throw {code,path};};
const messages={
  AUTHORING_RUNTIME_OPENING_INVALID:'请先关闭面板，在玩家输入框填写开局文本，再创建会话。',
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
// Browser-only drafts never contain provider fields or authority. The host revalidates on apply.
const typedFormat='modelmirror.rpg.parameter-config/2';
const typedLabels={...labels,topK:'Top K',frequencyPenalty:'频率惩罚',presencePenalty:'存在惩罚',stop:'停止词',seed:'Seed','thinking.enabled':'启用思考','thinking.effort':'思考档位','thinking.budgetTokens':'思考预算'};
const defaultValue=f=>f?.default?.mode==='value'?copy(f.default.value):f?.type==='text-list'?[]:f?.type==='boolean'?false:f?.type==='enum'?f.options[0]:'';
const draftValue=(f,v)=>['integer','number'].includes(f?.type)?String(v):copy(v);
export function typedParameterDraft(entry,configuration=null){
  const fields=entry?.parameterPolicy?.fields||{},settings=configuration?.values||{},draft={};
  for(const key of new Set([...Object.keys(fields),...Object.keys(settings)])){
    const f=fields[key],setting=settings[key]||{mode:'default'};
    draft[key]={mode:setting.mode,value:draftValue(f,setting.mode==='explicit'?setting.value:defaultValue(f))};
  }
  return draft;
}
export function switchParameterDraft(draft,entry){
  const next=copy(draft),fields=entry.parameterPolicy?.fields||{};
  for(const [key,f] of Object.entries(fields)){
    if(!next[key]||next[key].mode==='default')next[key]={mode:'default',value:draftValue(f,defaultValue(f))};
    else if(['number','integer'].includes(f.type)&&typeof next[key].value==='number')next[key].value=String(next[key].value);
  }
  return next;
}
export function typedParameterConfig(entry,draft){
  const values={};
  for(const [key,s] of Object.entries(draft)){
    if(s.mode==='default'){values[key]={mode:'default'};continue;}
    const f=entry?.parameterPolicy?.fields?.[key];let value=copy(s.value);
    if(['integer','number'].includes(f?.type)){
      if(typeof value!=='string'||!value.trim())fail('PARAMETER_REQUIRED',key);
      value=Number(value);if(!Number.isFinite(value))fail('PARAMETER_RANGE',key);
    }
    values[key]={mode:'explicit',value};
  }
  return {format:typedFormat,values};
}
export function typedDraftIssues(entry,draft,purpose){
  const issues=[],add=(code,field='')=>issues.push({code,field});
  if(!entry)return [{code:'MODEL_PARAMETER_REQUIRED',field:'模型'}];
  if(!entry.available)add('MODEL_SELECTION_UNAVAILABLE');
  const policy=entry.parameterPolicy;
  if(!policy)return [{code:'MODEL_CAPABILITY_UNKNOWN',field:''}];
  if(!policy.purposes.includes(purpose))add('PARAMETER_PURPOSE_UNSUPPORTED');
  const effective={};
  for(const key of Object.keys(draft))if(!Object.hasOwn(policy.fields,key))add('PARAMETER_UNSUPPORTED',key);
  for(const [key,f] of Object.entries(policy.fields)){
    const setting=draft[key]||{mode:'default'};
    if(setting.mode==='default'&&f.default.mode==='omit'){if(f.required)add('PARAMETER_REQUIRED',key);continue;}
    let v=setting.mode==='default'?f.default.value:setting.value;
    if(['number','integer'].includes(f.type)&&setting.mode==='explicit')v=typeof v==='string'&&v.trim()?Number(v):NaN;
    effective[key]=v;
    const valid=['number','integer'].includes(f.type)?typeof v==='number'&&Number.isFinite(v)&&v>=f.minimum&&v<=f.maximum&&(f.type!=='integer'||Number.isSafeInteger(v)):
      f.type==='enum'?f.options.includes(v):f.type==='boolean'?typeof v==='boolean':Array.isArray(v)&&v.length>=f.minItems&&v.length<=f.maxItems&&new Set(v).size===v.length&&v.every(x=>typeof x==='string'&&x.isWellFormed()&&[...x].length>0&&[...x].length<=f.maxItemLength);
    if(!valid||policy.kind==='fixed'&&canonical(v)!==canonical(f.default.value))add('PARAMETER_RANGE',key);
  }
  for(const group of policy.exclusive)if(group.filter(key=>Object.hasOwn(effective,key)).length>1)add('PARAMETER_CONFLICT',group.join(' / '));
  const matches=x=>x.op==='set'?Object.hasOwn(effective,x.field):x.op==='unset'?!Object.hasOwn(effective,x.field):Object.hasOwn(effective,x.field)&&canonical(effective[x.field])===canonical(x.value);
  for(const rule of policy.requires)if(matches(rule.if)&&!matches(rule.then))add('PARAMETER_DEPENDENCY',rule.then.field);
  for(const [left,right] of policy.lessThan)if(Object.hasOwn(effective,left)&&(!Object.hasOwn(effective,right)||!(effective[left]<effective[right])))add('PARAMETER_DEPENDENCY',left);
  if(Object.hasOwn(effective,'thinking.effort')&&Object.hasOwn(effective,'thinking.budgetTokens'))add('PARAMETER_CONFLICT','thinking.effort / thinking.budgetTokens');
  if(effective['thinking.enabled']===false&&['thinking.effort','thinking.budgetTokens'].some(k=>Object.hasOwn(effective,k)))add('PARAMETER_CONFLICT','thinking.enabled');
  return issues;
}
export function typedDraftFingerprint(entry,draft){
  return canonical({selectionId:entry?.selectionId||null,selectionRevision:entry?.selectionRevision||null,capabilityRevision:entry?.capabilityRevision||null,
    values:Object.fromEntries(Object.entries(draft).map(([key,v])=>[key,v.mode==='default'?{mode:'default'}:{mode:'explicit',value:['number','integer'].includes(entry?.parameterPolicy?.fields?.[key]?.type)&&typeof v.value==='string'&&v.value.trim()&&Number.isFinite(Number(v.value))?Number(v.value):v.value}]))});
}
export function typedSelection(catalog,entry,draft,purpose){
  const issues=typedDraftIssues(entry,draft,purpose);if(issues.length)fail(issues[0].code,issues[0].field);
  if(!catalog?.models.some(e=>e.selectionId===entry.selectionId&&e.selectionRevision===entry.selectionRevision&&e.capabilityRevision===entry.capabilityRevision))fail('MODEL_CONTROL_CHANGED');
  return {format:'modelmirror.rpg.parameter-selection/2',catalogRevision:catalog.revision,selectionId:entry.selectionId,selectionRevision:entry.selectionRevision,capabilityRevision:entry.capabilityRevision,
    profile:{format:'modelmirror.rpg.parameter-profile/1',purpose,modelId:entry.modelId,parameters:typedParameterConfig(entry,draft)}};
}
Object.assign(messages,{PARAMETER_REQUIRED:'请显式填写必需参数。',PARAMETER_RANGE:'参数值不符合当前模型的取值范围。',PARAMETER_UNSUPPORTED:'当前模型不支持此参数，请核对后移除。',PARAMETER_CONFLICT:'参数存在互斥冲突，请调整后应用。',PARAMETER_DEPENDENCY:'参数依赖条件未满足，请核对思考开关、档位及预算。',PARAMETER_PURPOSE_UNSUPPORTED:'该模型不支持当前用途。',PARAMETER_CONTROL_V2_REQUIRED:'当前宿主尚未支持新版参数，请保留原配置。'});
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
// Preset writes have their own journal: they never block or impersonate a story request.
const presetPrefix='modelmirror.authoring.parameter-preset-pending/';
export function pendingPresetOperation(storage){
  try{
    const key=Object.keys(storage).filter(k=>k.startsWith(presetPrefix)).sort()[0];if(!key)return null;
    const body=JSON.parse(storage.getItem(key));
    if(!body||key!==presetPrefix+body.operationId||typeof body.id!=='string'||typeof body.name!=='string'||!Number.isSafeInteger(body.expectedRevision)||body.expectedRevision<0||body.expectedPresetRevision!==0||!body.profile||!body.selection)fail('MODEL_UI_STORAGE_UNAVAILABLE');
    return body;
  }catch{fail('MODEL_UI_STORAGE_UNAVAILABLE');}
}
export async function presetReceiptMatches(body,op){
  if(!op||op.operationId!==body.operationId||op.revision!==body.expectedRevision+1)return false;
  const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(body))))).map(v=>v.toString(16).padStart(2,'0')).join('');
  const r=op.result;
  return op.requestHash===hash&&r?.id===body.id&&r.name===body.name&&r.revision===body.expectedPresetRevision+1&&canonical(r.configuration?.profile)===canonical(body.profile)&&canonical(r.configuration?.selection)===canonical(body.selection);
}
export function profileDraft(entry,profile,purpose){
  if(profile?.format!=='modelmirror.rpg.parameter-profile/1'||profile.purpose!==purpose)fail('PARAMETER_PURPOSE_INVALID');
  // Cross-model loads retain every setting, including unsupported ones, for explicit review.
  return typedParameterDraft(entry,profile.parameters);
}
export function withRecommendedProfile(document,profile){
  if(profile?.format!=='modelmirror.rpg.parameter-profile/1'||!Object.hasOwn(purposes,profile.purpose)||Object.keys(profile).sort().join(',')!=='format,modelId,parameters,purpose')fail('PARAMETER_PROFILE_INVALID');
  if(document.model?.selection!==null||document.model?.parameters!==null)fail('PARAMETER_LEGACY_RECOMMENDATION');
  const next=copy(document);
  next.model.recommendations??={format:'modelmirror.rpg.parameter-recommendations/1',story:null,summary:null,memory:null};
  next.model.recommendations[profile.purpose]=copy(profile);return next;
}
Object.assign(messages,{PARAMETER_REVISION_CONFLICT:'预设库已被其他标签修改。名称和参数仍保留，请刷新预设后再保存。',PARAMETER_PRESET_CAPACITY:'已达到 100 份预设上限。现有预设保留。',PARAMETER_OPERATION_CAPACITY:'预设记录已达到容量上限，未覆盖原记录。',PARAMETER_PREFLIGHT_REJECTED:'预设未通过当前模型能力校验，请返回参数检查。',PARAMETER_PURPOSE_INVALID:'该配置属于其他用途，不能载入。',PARAMETER_PROFILE_INVALID:'这份参数配置格式不正确。',PARAMETER_LEGACY_RECOMMENDATION:'作品已有旧版推荐配置，请先在作品配置中明确处理；本次没有覆盖。',AUTHORING_REVISION_CONFLICT:'作品已被其他标签修改，请关闭面板，在作品中核对最新草稿。',AUTHORING_OPERATION_PENDING:'作品有保存结果待核对，请关闭面板后恢复原操作。',PARAMETER_PRESET_PENDING:'有预设保存结果待核对，请先打开预设核对原操作。'});
const path='/model-settings';
export function createModelSettingsUI({button,api,getWork,getOpeningText,notice,getRecommendation,saveRecommendation}){
  const style=document.createElement('link');style.rel='stylesheet';style.href='./model-ui.css';document.head.append(style);
  let active=null,enabled=false,initializing=false,realProvider=false,presetsEnabled=false;
  const store=(key,value)=>{try{if(value===null)localStorage.removeItem(key);else localStorage.setItem(key,JSON.stringify(value));}catch{fail('MODEL_UI_STORAGE_UNAVAILABLE');}};
  const read=key=>{try{const s=localStorage.getItem(key);return s?JSON.parse(s):null;}catch{fail('MODEL_UI_STORAGE_UNAVAILABLE');}};
  function close(){if(active){active.picker?.close();active.discard?.close();active.dialog.close();active=null;}}
  function dialog(title,className='model-settings-dialog'){
    const node=document.createElement('dialog');node.className=className;
    node.setAttribute('aria-label',title);
    node.innerHTML='<header><h2>'+escape(title)+'</h2><button type="button" class="icon-button" aria-label="关闭'+escape(title)+'">×</button></header><div class="model-body"></div><footer class="model-footer"></footer>';
    const dismiss=()=>node.requestClose?node.requestClose():node.close();
    node.querySelector('header button').onclick=dismiss;
    node.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();dismiss();}});
    node.addEventListener('click',event=>{if(event.target!==node)return;const b=node.getBoundingClientRect();if(event.clientX<b.left||event.clientX>b.right||event.clientY<b.top||event.clientY>b.bottom)dismiss();});
    document.body.append(node);node.addEventListener('close',()=>node.remove(),{once:true});node.showModal();return node;
  }
  const current=c=>active===c&&c.dialog.open&&getWork()?.id===c.workId;
  const status=(c,text)=>{if(current(c))c.dialog.querySelector('[data-model-status]').textContent=text;};
  function choice(c){if(c.typed)return typedSelection(c.catalog,c.entry,c.draft,c.purpose);return c.entry?{catalogRevision:c.catalog.revision,selectionId:c.entry.selectionId,selectionRevision:c.entry.selectionRevision,parameters:parametersFromDraft(c.entry,c.draft)}:null;}
  function setDraft(c,purpose){
    c.purpose=purpose;
    const saved=c.view?.session.models.current[purpose];
    c.entry=saved&&(!c.typed||saved.parameterProfile)?copy(saved.entry):null;
    c.draft=c.typed?typedParameterDraft(c.entry,saved?.parameterProfile?.parameters||(saved?{values:Object.fromEntries(Object.entries(saved.parameters).map(([k,v])=>[k,{mode:'explicit',value:v}]))}:null)):parameterDraft(c.entry,saved?.parameters);c.saved=saved;
    if(c.typed)c.baselines[purpose]=typedDraftFingerprint(c.entry,c.draft);
  }
  function lock(c,value){
    c.busy=value;
    if(c.typed){updateTypedState(c);return;}
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
    c.view=view;button.textContent=c.typed?'模型与参数':view.session.models.current.story?.entry.name||'添加模型';
    if(!preserve)setDraft(c,c.purpose);
  }
  async function catalog(c,refresh=false){
    const result=await api(path+(c.typed?'/catalog?parameterVersion=2':refresh?'/refresh':'/catalog'),!c.typed&&refresh?{}:undefined);
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
    if(record.kind==='configure'){
      lock(c,true);status(c,'正在核对模型能力…');
      try{await api(path+'/check',{choice:record.body.choice});}
      catch(error){if(current(c)){status(c,modelError(error));lock(c,false);}return;}
      if(!current(c))return;
    }
    try{store(pendingKey(c.workId,record.body.operationId,c.work.publishedVersionId),record);}catch(error){status(c,modelError(error));lock(c,false);return;}
    c.pending=record;lock(c,true);status(c,'正在保存配置…');
    let persisted=false;
    try{
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
      button.textContent=c.typed?'模型与参数':view.session.models.current.story?.entry.name||'添加模型';
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
    if(c.typed&&s){renderTyped(c);return;}
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
  function typedDirty(c,purpose=c.purpose){
    const d=purpose===c.purpose?{entry:c.entry,draft:c.draft}:c.drafts[purpose];
    return !!d?.draft&&(typedDraftFingerprint(d.entry,d.draft)!==c.baselines[purpose]||!!c.stopBuffers?.[purpose]);
  }
  function requestClose(c){
    if(c.discard?.open)return;
    if(!Object.keys(purposes).some(p=>typedDirty(c,p))||c.pending){c.dialog.close();return;}
    const node=dialog('放弃未应用的修改？','model-picker-dialog model-discard-dialog');c.discard=node;
    node.querySelector('.model-body').innerHTML='<p>未应用的参数将被放弃，当前会话配置保持不变。</p>';
    node.querySelector('footer').innerHTML='<button type="button" data-stay>继续编辑</button><button type="button" data-discard>放弃修改</button>';
    node.querySelector('[data-stay]').onclick=()=>node.close();
    node.querySelector('[data-discard]').onclick=()=>{node.close();c.dialog.close();};
    node.addEventListener('close',()=>{c.discard=null;if(current(c))c.dialog.querySelector('[data-cancel]')?.focus();},{once:true});
    node.querySelector('[data-stay]').focus();
  }
  function typedIssues(c){
    const issues=typedDraftIssues(c.entry,c.draft||{},c.purpose);
    if(c.entry&&!c.catalog?.models.some(e=>e.selectionId===c.entry.selectionId&&e.selectionRevision===c.entry.selectionRevision&&e.capabilityRevision===c.entry.capabilityRevision))issues.push({code:'MODEL_CONTROL_CHANGED',field:''});
    if(c.stopBuffers?.[c.purpose])issues.push({code:'STOP_NOT_ADDED',field:'stop'});
    return issues;
  }
  function updateTypedState(c){
    if(!current(c))return;
    const blocked=c.busy||!!c.pending||!!c.historyPending||!!c.view?.busy||!!c.view?.pending||!!c.blockingError;
    for(const node of c.dialog.querySelectorAll('[data-mutation],[data-purpose],[data-refresh],[data-check]'))node.disabled=blocked;
    for(const node of c.dialog.querySelectorAll('[data-field-control]')){
      const key=node.dataset.fieldControl;node.disabled=blocked||c.draft?.[key]?.mode!=='explicit'||c.entry?.parameterPolicy?.kind==='fixed';
    }
    const issues=typedIssues(c),area=c.dialog.querySelector('[data-issues]');
    if(area){area.innerHTML=issues.map(i=>'<p>'+escape(i.code==='STOP_NOT_ADDED'?'请先添加输入中的停止词，或清空该输入框。':(typedLabels[i.field]?typedLabels[i.field]+'：':'')+(messages[i.code]||i.code))+(i.field&&!typedLabels[i.field]?' <span>'+escape(i.field)+'</span>':'')+'</p>').join('');area.hidden=!issues.length;}
    const save=c.dialog.querySelector('[data-save]');if(save)save.disabled=blocked||!c.catalog||issues.length>0||!typedDirty(c);
    const check=c.dialog.querySelector('[data-check]');if(check)check.disabled=blocked||!c.catalog||issues.length>0;
    const presetSave=c.dialog.querySelector('[data-preset-save]');if(presetSave)presetSave.disabled=blocked||!c.catalog||issues.length>0;
    const dirty=c.dialog.querySelector('[data-dirty]');if(dirty)dirty.textContent=Object.keys(purposes).filter(p=>typedDirty(c,p)).map(p=>purposes[p]).join('、')+(Object.keys(purposes).some(p=>typedDirty(c,p))?'有未应用修改':'');
    for(const node of c.dialog.querySelectorAll('[data-field-control]'))node.setAttribute('aria-invalid',String(issues.some(i=>i.field===node.dataset.fieldControl)));
  }
  function renderTyped(c){
    const s=c.view.session,body=c.dialog.querySelector('.model-body');
    c.stopBuffers??={};
    body.innerHTML='<nav class="model-purposes" aria-label="参数用途">'+Object.entries(purposes).map(([key,name])=>'<button type="button" data-purpose="'+key+'" aria-pressed="'+(key===c.purpose)+'">'+name+'</button>').join('')+'</nav><p class="model-scope">仅本会话 · '+(c.purpose==='story'?'用于后续剧情':c.purpose==='summary'?'自动总结启用时使用':'记忆宫殿启用时使用')+'</p><p data-model-status role="status">'+escape(c.catalogError||'')+'</p><div data-pending></div><div class="model-selection"><strong>模型</strong><button type="button" data-select data-mutation aria-haspopup="dialog">'+escape(c.entry?.name||'选择模型')+' ⌄</button></div><div data-issues class="parameter-issues" role="status" hidden></div><div data-parameters></div><details class="model-evidence"><summary>当前生效配置</summary><p>'+escape(s.models.current[c.purpose]?.entry.name||'尚未选择')+'</p><pre>'+escape(JSON.stringify(s.models.current[c.purpose]?.parameters||{},null,2))+'</pre><details><summary>参数来源与记录</summary><pre>'+escape(JSON.stringify({profile:s.models.current[c.purpose]?.parameterProfile||null,sources:s.models.current[c.purpose]?.parameterResolution?.sources||null,revision:s.models.revision},null,2))+'</pre></details></details>';
    for(const b of body.querySelectorAll('[data-purpose]'))b.onclick=()=>{
      c.drafts[c.purpose]={entry:c.entry,draft:c.draft};const next=b.dataset.purpose;setDraft(c,next);if(c.drafts[next])Object.assign(c,c.drafts[next]);render(c);c.dialog.querySelector('[data-purpose="'+next+'"]').focus();
    };
    body.querySelector('[data-select]').onclick=()=>picker(c);
    if(presetsEnabled){
      body.querySelector('[data-issues]').insertAdjacentHTML('beforebegin','<nav class="model-families" aria-label="参数配置管理"><button type="button" data-presets data-mutation aria-haspopup="dialog">载入预设</button><button type="button" data-preset-save data-mutation aria-haspopup="dialog">另存为预设</button>'+(getRecommendation&&saveRecommendation?'<button type="button" data-recommend data-mutation aria-haspopup="dialog">作品推荐</button>':'')+'</nav>');
      body.querySelector('[data-presets]').onclick=()=>presets(c,'load');
      body.querySelector('[data-preset-save]').onclick=()=>presets(c,'save');
      body.querySelector('[data-recommend]')?.addEventListener('click',()=>recommend(c));
    }
    renderTypedParameters(c);
    c.dialog.querySelector('.model-footer').innerHTML='<p data-dirty role="status"></p><div class="parameter-actions"><button type="button" data-refresh>刷新目录</button><button type="button" data-check>预检</button><button type="button" data-cancel>取消</button><button type="button" class="primary" data-save data-mutation>应用到会话</button></div>';
    c.dialog.querySelector('[data-cancel]').onclick=()=>requestClose(c);
    c.dialog.querySelector('[data-refresh]').onclick=async()=>{
      if(c.busy||c.pending)return;lock(c,true);
      try{await catalog(c,true);await refreshView(c,true);if(current(c)){c.catalogError='';render(c);status(c,'已读取最新状态。编辑保留，请核对后应用。');}}
      catch(error){if(current(c)){c.catalog=null;c.catalogError=modelError(error);render(c);}}
      finally{if(current(c))lock(c,false);}
    };
    c.dialog.querySelector('[data-check]').onclick=async()=>{
      if(c.busy||c.pending)return;lock(c,true);status(c,'正在核对参数…');
      try{await api(path+'/check',{choice:choice(c)});status(c,'参数可用，尚未应用。');}
      catch(error){status(c,modelError(error));}finally{if(current(c))lock(c,false);}
    };
    c.dialog.querySelector('[data-save]').onclick=async()=>{
      if(c.busy||c.pending||c.historyPending||c.view.busy||c.view.pending||c.blockingError)return;
      try{const record={kind:'configure',workId:c.workId,sessionId:c.sessionId,body:{sessionId:c.sessionId,operationId:crypto.randomUUID(),expectedRevision:c.view.session.revision,expectedModelRevision:c.view.session.models.revision,purpose:c.purpose,choice:choice(c)}};await execute(c,record);}
      catch(error){status(c,modelError(error));}
    };
    renderPending(c);updateTypedState(c);
    if(c.view.pending||c.historyPending)status(c,'有结果待核对，请先恢复原操作。');
    else if(c.view.busy)status(c,'会话正在处理请求，完成后可继续配置。');
  }
  function subpanel(c,title,returnTo){
    const node=dialog(title,'model-picker-dialog');c.picker=node;
    node.addEventListener('close',()=>{if(c.picker===node)c.picker=null;if(current(c))c.dialog.querySelector(returnTo)?.focus();},{once:true});
    return node;
  }
  const parameterText=setting=>!setting||setting.mode==='default'?'使用当前模型默认值':typeof setting.value==='string'?setting.value:JSON.stringify(setting.value);
  function profileReview(c,profile){
    const draft=profileDraft(c.entry,profile,c.purpose),issues=typedDraftIssues(c.entry,draft,c.purpose);
    const entries=Object.entries(draft).sort(([a],[b])=>Object.keys(typedLabels).indexOf(a)-Object.keys(typedLabels).indexOf(b));
    const row=([key,value])=>{const before=parameterText(c.draft[key]),after=parameterText(value);return '<div class="model-row"><strong>'+escape(typedLabels[key]||key)+'</strong><span>'+escape(before===after?after:before+' → '+after)+'</span></div>';};
    const defaults=entries.filter(([k,v])=>v.mode==='default'&&(!c.draft[k]||c.draft[k].mode==='default')&&Object.hasOwn(c.entry?.parameterPolicy?.fields||{},k));
    const rows=entries.filter(([k])=>!defaults.some(([d])=>d===k)).map(row).join('')+(defaults.length?'<details class="model-evidence"><summary>'+defaults.length+' 项使用当前模型默认值</summary>'+defaults.map(row).join('')+'</details>':'');
    return '<p class="model-hint">来源：'+escape(profile.modelId)+'<br>当前模型：'+escape(c.entry?.modelId||'尚未选择')+'</p>'+(profile.modelId!==c.entry?.modelId?'<p class="model-warning">跨模型载入：保留当前模型，不转换参数。请逐项核对。</p>':'')+(issues.length?'<div class="parameter-issues">'+issues.map(i=>'<p>'+escape(typedLabels[i.field]||i.field)+'：'+escape(messages[i.code]||i.code)+'</p>').join('')+'</div>':'')+rows;
  }
  function loadProfile(c,profile,node){
    if(!c.entry){status(c,'请先选择模型，再载入配置。');node.close();return;}
    c.draft=profileDraft(c.entry,profile,c.purpose);c.stopBuffers[c.purpose]='';node.close();render(c);
    status(c,'已载入参数，尚未应用到会话。请核对后应用。');
  }
  async function presets(c,mode){
    const node=subpanel(c,mode==='save'?'另存为预设':'载入预设',mode==='save'?'[data-preset-save]':'[data-presets]');
    let snapshot=null,pending=null,saving=false,name='',queryText='',retry=false;
    const live=()=>current(c)&&node.open,report=text=>{if(live())node.querySelector('[data-preset-status]').textContent=text;};
    const frozen=mode==='save'?choice(c):null;
    const footer=html=>{node.querySelector('footer').innerHTML=html;};
    function draw(){
      if(!live())return;
      node.querySelector('h2').textContent=mode==='save'?'另存为预设':'载入预设';
      node.querySelector('.model-body').innerHTML='<p class="model-hint">'+escape(purposes[c.purpose])+'用途 · '+(mode==='save'?escape(c.entry.name)+'。预设保存在本地创作库，不会应用到会话。':'载入后先核对，再应用到当前会话。')+'</p><p data-preset-status role="status" class="model-warning"></p>'+(pending&&!saving?'<p class="model-hint">有一份预设保存结果待核对。不会自动重发。</p><button type="button" data-preset-recover>核对原操作</button>':'')+(mode==='save'?'<label class="model-search">预设名称<input type="text" aria-label="预设名称" autocomplete="off" value="'+escape(name)+'" placeholder="例如：细腻叙述"></label><p class="model-hint">1–80 字符 · 同名预设会分别保留</p>':'<label class="model-search">搜索预设<input type="search" aria-label="搜索预设" value="'+escape(queryText)+'" placeholder="搜索名称或模型"></label><div data-preset-list></div>');
      footer('<button type="button" data-preset-refresh>刷新预设</button><button type="button" data-preset-cancel>取消</button>'+(mode==='save'?'<button type="button" class="primary" data-preset-write>保存预设</button>':'')+(pending&&retry?'<button type="button" data-preset-retry>原编号再保存</button>':''));
      node.querySelector('[data-preset-cancel]').onclick=()=>node.close();
      node.querySelector('[data-preset-refresh]').onclick=()=>refresh();
      node.querySelector('[data-preset-recover]')?.addEventListener('click',()=>recover());
      node.querySelector('[data-preset-retry]')?.addEventListener('click',()=>write(pending));
      if(mode==='save'){
        const input=node.querySelector('input'),save=node.querySelector('[data-preset-write]');
        const update=()=>{save.disabled=saving||!!pending||!snapshot||!name.trim()||!name.isWellFormed()||[...name].length>80;};
        input.oninput=()=>{name=input.value;update();};update();
        input.onkeydown=e=>{if(e.key==='Enter'&&!e.isComposing){e.preventDefault();if(!save.disabled)save.click();}};
        save.onclick=()=>{if(save.disabled)return;const {profile,format,...selection}=frozen;write({operationId:crypto.randomUUID(),expectedRevision:snapshot.revision,id:crypto.randomUUID(),name,expectedPresetRevision:0,profile,selection});};
      }else{
        const list=()=>{
          const entries=(snapshot?.presets||[]).filter(p=>p.profile.purpose===c.purpose&&(!queryText.trim()||(p.name+' '+p.profile.modelId).toLowerCase().includes(queryText.trim().toLowerCase())));
          node.querySelector('[data-preset-list]').innerHTML=entries.length?entries.map(p=>'<button type="button" class="model-row" data-load-preset="'+escape(p.id)+'"'+(!c.entry||pending?' disabled':'')+'><strong>'+escape(p.name)+'</strong><span>'+escape(p.profile.modelId)+'</span></button>').join(''):'<p class="model-hint">'+(snapshot?'没有匹配的'+purposes[c.purpose]+'预设。':'正在读取预设…')+'</p>';
          if(!c.entry)report('请先返回选择模型，再载入预设。');
          for(const button of node.querySelectorAll('[data-load-preset]'))button.onclick=()=>{
            const p=entries.find(p=>p.id===button.dataset.loadPreset);node.querySelector('h2').textContent='核对预设';
            node.querySelector('.model-body').innerHTML='<h3>'+escape(p.name)+'</h3>'+profileReview(c,p.profile)+(p.capabilityRevision!==c.entry.capabilityRevision?'<p class="model-hint">保存时的能力与当前不同，已列出当前兼容性；应用前仍会由控制面重新校验。</p>':'');
            footer('<button type="button" data-back>返回列表</button><button type="button" class="primary" data-load>载入待核对参数</button>');
            node.querySelector('[data-back]').onclick=draw;node.querySelector('[data-load]').onclick=()=>loadProfile(c,p.profile,node);
          };
        };
        node.querySelector('input').oninput=e=>{queryText=e.target.value;list();};list();
      }
    }
    async function refresh(){
      if(saving)return;
      try{pending=pendingPresetOperation(localStorage);snapshot=await api(path+'/presets');if(live()){retry=false;draw();}}
      catch(error){if(live()){draw();report(modelError(error));}}
    }
    async function recover(){
      if(saving||!pending)return;saving=true;const original=pending;
      node.querySelectorAll('button:not(header button)').forEach(b=>b.disabled=true);
      try{
        const result=await api(path+'/presets/reconcile',{operationId:original.operationId});
        if(result.status==='committed'){
          if(!await presetReceiptMatches(original,result.operation))fail('NETWORK_RESULT_UNKNOWN');
          store(presetPrefix+original.operationId,null);saving=false;await refresh();report('已核对原预设回执，没有重新保存。');
        }else if(result.status==='not_found'){retry=true;if(live()){draw();report('未找到原回执。可用原编号再次保存，不会自动提交。');}}
        else fail('NETWORK_RESULT_UNKNOWN');
      }catch(error){saving=false;if(live()){draw();report(modelError(error));}}finally{saving=false;}
    }
    async function write(body){
      if(saving||!body)return;
      try{const other=pendingPresetOperation(localStorage);if(other&&other.operationId!==body.operationId)fail('PARAMETER_PRESET_PENDING');store(presetPrefix+body.operationId,body);}
      catch(error){report(modelError(error));return;}
      saving=true;pending=body;draw();node.querySelectorAll('input,button:not(header button)').forEach(b=>b.disabled=true);report('正在保存预设…');
      try{
        const result=await api(path+'/presets',body);if(!await presetReceiptMatches(body,result))fail('NETWORK_RESULT_UNKNOWN');
        store(presetPrefix+body.operationId,null);
        if(live()){node.close();status(c,'预设已保存。当前会话参数保持原样。');}
      }catch(error){
        try{
          if(error.httpStatus&&error.httpStatus<500&&!/UNKNOWN|STORAGE|WRITE_FAILED|OWNER|PENDING/.test(error.code||''))store(presetPrefix+body.operationId,null);
          pending=pendingPresetOperation(localStorage);
        }catch(storageError){pending=body;error=storageError;}
        saving=false;if(live()){draw();report(modelError(error));}
      }finally{saving=false;}
    }
    draw();await refresh();if(live())node.querySelector('input')?.focus();
  }
  function recommend(c){
    const node=subpanel(c,'作品推荐配置','[data-recommend]'),prior=getRecommendation(c.purpose),canSave=!!c.entry&&typedIssues(c).length===0;
    node.querySelector('.model-body').innerHTML='<p class="model-hint">'+escape(purposes[c.purpose])+'用途 · 推荐配置随作品导出，供新会话手动载入。</p><h3>当前推荐</h3><p class="model-hint">'+escape(prior?.modelId||'尚未设置')+'</p>'+(prior?'<button type="button" data-load-recommend'+(!c.entry?' disabled':'')+'>核对并载入推荐</button>':'')+'<h3>设为新的推荐</h3><p class="model-hint">'+escape(c.entry?.name||'请先选择模型')+'</p><p class="model-hint">将一起保存当前编辑的作品草稿。不会发布版本，也不会修改任何已有会话。</p><p data-recommend-status role="status" class="model-warning"></p>';
    node.querySelector('footer').innerHTML='<button type="button" data-cancel-recommend>取消</button><button type="button" class="primary" data-save-recommend'+(!canSave?' disabled':'')+'>保存草稿与推荐</button>';
    node.querySelector('[data-cancel-recommend]').onclick=()=>node.close();
    node.querySelector('[data-load-recommend]')?.addEventListener('click',()=>{
      node.querySelector('.model-body').innerHTML=profileReview(c,prior);node.querySelector('footer').innerHTML='<button type="button" data-back>返回</button><button type="button" class="primary" data-load>载入待核对参数</button>';
      node.querySelector('[data-back]').onclick=()=>{node.close();recommend(c);};node.querySelector('[data-load]').onclick=()=>loadProfile(c,prior,node);
    });
    node.querySelector('[data-save-recommend]').onclick=async()=>{
      const selected=choice(c),workId=c.workId;node.querySelectorAll('button:not(header button)').forEach(b=>b.disabled=true);
      try{
        await api(path+'/check',{choice:selected});if(!current(c)||!node.open)return;
        await saveRecommendation(copy(selected.profile),workId);
        if(current(c)&&node.open){node.close();status(c,'作品推荐与草稿已保存。会话参数未改变。');}
      }catch(error){if(current(c)&&node.open){node.querySelector('[data-recommend-status]').textContent=modelError(error);node.querySelectorAll('button').forEach(b=>b.disabled=false);}}
    };
  }
  function renderTypedParameters(c){
    const root=c.dialog.querySelector('[data-parameters]'),policy=c.entry?.parameterPolicy;
    if(!policy){root.innerHTML='<p class="model-hint">选择模型后，可调整它支持的参数。</p>';return;}
    const fieldHtml=([key,f])=>{
      const d=c.draft[key],value=d.mode==='default'?defaultValue(f):d.value,label=typedLabels[key]||key,id='typed-'+key;
      const mode='<label class="parameter-mode"><input type="checkbox" data-mode="'+key+'" data-mutation'+(d.mode==='explicit'?' checked':'')+'> 显式设置'+label+'</label>';
      let control='';
      if(['number','integer'].includes(f.type)){
        const step=f.type==='integer'?'1':parameterStep(key,f,value);
        control='<div class="parameter-number"><input type="range" data-range="'+key+'" data-field-control="'+key+'" aria-label="'+label+'滑杆" min="'+f.minimum+'" max="'+f.maximum+'" step="'+step+'" value="'+escape(value===''?f.minimum:value)+'"><input id="'+id+'" type="number" data-number="'+key+'" data-field-control="'+key+'" aria-label="'+label+'数值" min="'+f.minimum+'" max="'+f.maximum+'" step="'+step+'" value="'+escape(value)+'" placeholder="默认"></div>';
      }else if(f.type==='enum'||f.type==='boolean'){
        const options=f.type==='boolean'?[false,true]:f.options;
        control='<select id="'+id+'" data-enum="'+key+'" data-field-control="'+key+'" aria-label="'+label+'">'+options.map(v=>'<option value="'+escape(String(v))+'"'+(v===value?' selected':'')+'>'+escape(typeof v==='boolean'?(v?'开启':'关闭'):v)+'</option>').join('')+'</select>';
      }else{
        control='<ul class="parameter-stops">'+(Array.isArray(value)?value:[]).map((v,i)=>'<li><code>'+escape(v)+'</code><button type="button" data-remove-stop="'+i+'" data-field-control="'+key+'" aria-label="移除停止词 '+escape(v)+'">×</button></li>').join('')+'</ul><div class="parameter-stop-add"><input type="text" data-stop-input data-field-control="'+key+'" aria-label="新停止词" placeholder="输入停止词，按回车添加" value="'+escape(c.stopBuffers[c.purpose]||'')+'"><button type="button" data-add-stop data-field-control="'+key+'">添加</button></div>';
      }
      const hint=f.default.mode==='omit'?'默认：不传此参数':'默认：'+JSON.stringify(f.default.value);
      return '<section class="parameter-field"><div class="parameter-field-title"><label for="'+id+'">'+escape(label)+'</label><button type="button" data-reset="'+key+'" data-mutation>恢复默认</button></div>'+control+'<div class="parameter-field-meta">'+mode+'<span>'+escape(hint)+'</span></div><small>'+(['number','integer'].includes(f.type)?f.minimum+' – '+f.maximum:f.type==='text-list'?'最多 '+f.maxItems+' 项，每项 '+f.maxItemLength+' 字符':'')+'</small></section>';
    };
    const fields=Object.entries(policy.fields).sort(([a],[b])=>Object.keys(typedLabels).indexOf(a)-Object.keys(typedLabels).indexOf(b)),basic=fields.filter(([k])=>Object.hasOwn(labels,k)),advanced=fields.filter(([k])=>!Object.hasOwn(labels,k));
    root.innerHTML=(policy.kind==='fixed'?'<p class="model-hint">此模型使用固定档位。</p>':'')+basic.map(fieldHtml).join('')+(advanced.length?'<details class="parameter-advanced"'+(c.advanced[c.purpose]?' open':'')+'><summary>高级参数 <span>'+advanced.length+'</span></summary>'+advanced.map(fieldHtml).join('')+'</details>':'')+Object.keys(c.draft).filter(k=>!Object.hasOwn(policy.fields,k)).map(k=>'<p class="parameter-unsupported">保留的参数：'+escape(typedLabels[k]||k)+'<button type="button" data-drop="'+escape(k)+'" data-mutation>移除此不兼容项</button></p>').join('');
    root.querySelector('details')?.addEventListener('toggle',event=>{c.advanced[c.purpose]=event.target.open;});
    const changed=()=>{status(c,'');updateTypedState(c);};
    for(const b of root.querySelectorAll('[data-mode]'))b.onchange=()=>{const key=b.dataset.mode;c.draft[key].mode=b.checked?'explicit':'default';if(key==='stop'&&!b.checked)c.stopBuffers[c.purpose]='';renderTypedParameters(c);changed();c.dialog.querySelector('[data-mode="'+key+'"]').focus();};
    for(const b of root.querySelectorAll('[data-reset]'))b.onclick=()=>{const key=b.dataset.reset;c.draft[key]={mode:'default',value:draftValue(policy.fields[key],defaultValue(policy.fields[key]))};if(key==='stop')c.stopBuffers[c.purpose]='';renderTypedParameters(c);changed();c.dialog.querySelector('[data-reset="'+key+'"]').focus();};
    for(const b of root.querySelectorAll('[data-drop]'))b.onclick=()=>{delete c.draft[b.dataset.drop];renderTypedParameters(c);changed();};
    for(const input of root.querySelectorAll('[data-number],[data-range]'))input.oninput=()=>{
      const key=input.dataset.number||input.dataset.range;c.draft[key].value=input.value;
      const other=root.querySelector('['+(input.dataset.number?'data-range':'data-number')+'="'+key+'"]');other.value=input.value;changed();
    };
    for(const input of root.querySelectorAll('[data-enum]'))input.onchange=()=>{const key=input.dataset.enum;c.draft[key].value=policy.fields[key].type==='boolean'?input.value==='true':input.value;changed();};
    for(const b of root.querySelectorAll('[data-remove-stop]'))b.onclick=()=>{c.draft.stop.value.splice(Number(b.dataset.removeStop),1);renderTypedParameters(c);changed();root.querySelector('[data-stop-input]')?.focus();};
    const stop=root.querySelector('[data-stop-input]');
    if(stop){
      stop.oninput=()=>{c.stopBuffers[c.purpose]=stop.value;changed();};
      const add=()=>{const value=stop.value,f=policy.fields.stop;if(!value||!value.isWellFormed()||[...value].length>f.maxItemLength||c.draft.stop.value.length>=f.maxItems||c.draft.stop.value.includes(value)){status(c,'停止词为空、重复或超出容量，请修改。');return;}c.draft.stop.value.push(value);c.stopBuffers[c.purpose]='';renderTypedParameters(c);changed();root.querySelector('[data-stop-input]').focus();};
      root.querySelector('[data-add-stop]').onclick=add;stop.onkeydown=event=>{if(event.key==='Enter'&&!event.isComposing){event.preventDefault();add();}};
    }
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
        const eligible=c.typed?e.available&&e.parameterPolicy?.purposes.includes(c.purpose):e.eligible;
        const reason=eligible?(e.parameterPolicy.kind==='fixed'?'固定参数':'可配置参数'):(messages[e.reason]||'当前不可选择');
        return '<button type="button" class="model-row" data-choice="'+escape(e.selectionId)+'"'+(!eligible?' disabled':'')+'><strong>'+escape(e.name)+'</strong><span>'+escape(e.modelId)+' · 线路 '+route+'</span><span>'+escape(reason)+'</span>'+(c.entry?.selectionId===e.selectionId?'<em>当前选择</em>':'')+'</button>';
      }).join(''):'<p class="model-hint">没有匹配的模型。</p>';
      for(const b of node.querySelectorAll('[data-choice]'))b.onclick=()=>{
        c.entry=copy(c.catalog.models.find(e=>e.selectionId===b.dataset.choice));c.draft=c.typed?switchParameterDraft(c.draft,c.entry):parameterDraft(c.entry);node.close();render(c);status(c,c.typed?'已保留本次参数，请核对后应用。':'已更新本次编辑，请点“保存配置”后生效。');c.dialog.querySelector('[data-select]').focus();
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
    const c={dialog:dialog('模型与参数'),typed:realProvider,baselines:{},advanced:{},workId:getWork().id,work:copy(getWork()),purpose,view:null,sessionId:null,catalog:null,realProvider,catalogError:'',pending:null,busy:false,picker:null,drafts:{}};active=c;
    if(c.typed){c.dialog.classList.add('generation-panel');c.dialog.requestClose=()=>requestClose(c);}
    c.dialog.querySelector('.model-body').innerHTML='<p role="status">正在读取配置…</p>';
    c.dialog.addEventListener('close',()=>{c.picker?.close();c.discard?.close();if(active===c)active=null;button.focus({preventScroll:true});},{once:true});
    try{await load(c);}catch(error){if(current(c)){c.blockingError=true;render(c);lock(c,false);status(c,modelError(error));}}
  }
  return {
    open,
    async initialize(){
      if(initializing)return;initializing=true;button.disabled=true;button.textContent='正在读取模型入口';
      try{const availability=await api(path);enabled=availability.enabled;realProvider=availability.realProvider===true;presetsEnabled=availability.presetsEnabled===true;button.disabled=!enabled;button.textContent=enabled?(realProvider?'模型与参数':'添加模型'):'模型配置未启用';button.onclick=()=>open();}
      catch{button.disabled=false;button.textContent='重试模型入口';button.onclick=()=>this.initialize();}
      finally{initializing=false;}
    },
    workChanged(){close();if(enabled)button.textContent=realProvider?'模型与参数':'添加模型';},
  };
}
