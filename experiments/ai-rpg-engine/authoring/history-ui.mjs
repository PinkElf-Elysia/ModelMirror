// Browser projection for the reviewed M1 configuration contract; no model dispatch.
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const path='/history-settings';
const fail=code=>{throw {code};};
const canonical=value=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
const key=(id,op='')=>'modelmirror.authoring.history-pending/'+id+'/'+op;
const messages={
  HISTORY_SETTINGS_RESULT_UNKNOWN:'配置结果尚未确认。请核对原操作，不会自动重发。',
  HISTORY_SETTINGS_RESULT_PENDING:'另有配置结果待核对，暂不能修改。',
  HISTORY_SETTINGS_STORAGE_UNAVAILABLE:'配置记录暂不可用，请保留原操作。',
  HISTORY_REGISTRY_RESTART_REQUIRED:'插件磁盘记录与运行状态不一致，需重启对应宿主后核对。',
  HISTORY_OPERATION_COLLISION:'操作编号对应的内容不同，未覆盖原记录。',
  HISTORY_POLICY_CONFLICT:'插件授权已变化。本次编辑保留，请刷新状态后核对。',
  HISTORY_REVISION_CONFLICT:'历史窗口设置已变化。本次编辑保留，请刷新状态后核对。',
  PLUGIN_REGISTRY_CONFLICT:'其他标签已修改插件安装或授权，请刷新状态后核对。',
  OFFLINE_REVISION_CONFLICT:'会话已变化。本次编辑保留，请刷新状态后核对。',
  OFFLINE_SESSION_BUSY:'会话正在处理任务，暂不能授权或保存设置。',
  OFFLINE_RESULT_PENDING:'原剧情结果尚未确认。停用插件不会清除原请求。',
  PLUGIN_NOT_INSTALLED:'请先安装历史窗口。安装不会自动授权。',
  PLUGIN_NOT_AUTHORIZED:'请先明确授权本会话使用历史窗口。',
  PLUGIN_PERMISSION_MISMATCH:'请核对并同意本会话所需权限。',
  HISTORY_UI_STORAGE_UNAVAILABLE:'浏览器不能保存操作记录，未开始新的写入。',
  HISTORY_CONFIGURATION_INVALID:'历史长度须为 1–50 的整数。',
  HISTORY_UI_REFERENCE_MISMATCH:'回执与原操作不一致，请保留记录。',
};
export const historyError=error=>(messages[error?.code]||'操作未完成，请保留记录并核对状态。')+' ['+(error?.code||'NETWORK_RESULT_UNKNOWN')+']';
export function historyConfig(draft){
  if(!/^\d+$/.test(String(draft.turns))||Number(draft.turns)<1||Number(draft.turns)>50||typeof draft.includeInitialCharacter!=='boolean')fail('HISTORY_CONFIGURATION_INVALID');
  return {turns:Number(draft.turns),includeInitialCharacter:draft.includeInitialCharacter};
}
export function historyIntent(settings,action,operationId,config){
  const intent={action,sessionId:settings.sessionId,operationId,expectedRevision:settings.sessionRevision,
    expectedHistoryRevision:settings.history.revision,expectedContextPolicyRevision:settings.effectivePolicy.revision,expectedRegistryRevision:settings.registryRevision};
  if(action==='configure')intent.config=historyConfig(config);
  if(action==='enable')intent.permissions=structuredClone(settings.plugin.permissions);
  return intent;
}
export function historyReceiptMatches(result,intent){
  return result?.state==='applied'&&canonical(result.intent)===canonical(intent)&&result.receipt?.operationId===intent.operationId&&result.settings?.sessionId===intent.sessionId;
}
export function pendingHistoryOperation(storage,sessionId){
  try{
    const k=Object.keys(storage).filter(k=>k.startsWith(key(sessionId))).sort()[0];if(!k)return null;
    const input=JSON.parse(storage.getItem(k));
    if(input.sessionId!==sessionId||typeof input.operationId!=='string'||k!==key(sessionId,input.operationId))fail('HISTORY_UI_STORAGE_UNAVAILABLE');
    return input;
  }catch{fail('HISTORY_UI_STORAGE_UNAVAILABLE');}
}
export function openHistorySettings({api,sessionId,workId,dialog,onClose=()=>{}}){
  const node=dialog('会话插件 · 历史窗口');
  let settings=null,draft=null,pending=null,busy=false,retry=false,loadError=false;
  const store=(input,remove=false)=>{try{if(remove)localStorage.removeItem(key(sessionId,input.operationId));else localStorage.setItem(key(sessionId,input.operationId),JSON.stringify(input));}catch{fail('HISTORY_UI_STORAGE_UNAVAILABLE');}};
  const status=text=>{if(node.open)node.querySelector('[data-model-status]').textContent=text;};
  function receive(value,preserve=false){
    if(value.sessionId!==sessionId||value.workId!==workId)fail('HISTORY_UI_REFERENCE_MISMATCH');
    settings=value;
    if(!draft||!preserve)draft={turns:String(value.history.config.turns),includeInitialCharacter:value.history.config.includeInitialCharacter};
    pending=pendingHistoryOperation(localStorage,sessionId)||(value.unconfirmed?.sessionId===sessionId?value.unconfirmed:null);
    loadError=false;
  }
  async function refresh(preserve=true){receive(await api(path+'/sessions/'+sessionId),preserve);}
  function lock(){
    const blocked=busy||loadError||!!pending||!!settings?.unconfirmed;
    for(const b of node.querySelectorAll('[data-action]')){
      const action=b.dataset.action;
      b.disabled=blocked||(['enable','configure'].includes(action)&&(settings.busy||settings.pending));
      if(action==='enable')b.disabled ||= !node.querySelector('[data-consent]')?.checked;
      if(action==='configure')b.disabled ||= !settings.effectivePolicy.authorization.history.enabled;
    }
    for(const field of node.querySelectorAll('[data-edit]'))field.disabled=busy||loadError||!!pending||!!settings?.unconfirmed;
    const r=node.querySelector('[data-recover]');if(r)r.disabled=busy;
    const again=node.querySelector('[data-retry]');if(again)again.disabled=busy;
    node.querySelector('[data-refresh]').disabled=busy;
  }
  async function execute(input){
    if(busy)return;
    try{store(input);}catch(e){status(historyError(e));return;}
    pending=input;retry=false;busy=true;render();status('正在保存…');
    try{
      const result=await api(path+'/change',input);
      if(!historyReceiptMatches(result,input))fail('HISTORY_UI_REFERENCE_MISMATCH');
      store(input,true);receive(result.settings,input.action!=='configure');
      if(node.open){render();status('已保存。未发送模型请求；未增加额度。');}
    }catch(error){
      if(error.httpStatus&&error.httpStatus<500&&!/UNKNOWN|PENDING|STORAGE|WRITE|OWNER/.test(error.code||'')){
        try{store(input,true);pending=pendingHistoryOperation(localStorage,sessionId);}catch{/* retain intent */}
      }
      if(node.open){render();status(historyError(error));}
    }finally{busy=false;if(node.open)lock();}
  }
  async function recover(){
    if(busy||!pending)return;busy=true;lock();const input=pending;
    try{
      const result=await api(path+'/reconcile',input);
      if(canonical(result.intent)!==canonical(input))fail('HISTORY_UI_REFERENCE_MISMATCH');
      if(result.state==='applied'){
        if(!historyReceiptMatches(result,input))fail('HISTORY_UI_REFERENCE_MISMATCH');
        store(input,true);receive(result.settings,input.action!=='configure');retry=false;render();status('已核对原回执，没有重新提交。');
      }else if(['not_applied','not_submitted'].includes(result.state)){
        receive(result.settings,true);pending=input;retry=true;render();status('原操作未生效。可明确用原编号再试，或放弃本次未生效编辑。');
      }else fail('HISTORY_UI_REFERENCE_MISMATCH');
    }catch(error){status(historyError(error));}finally{busy=false;if(node.open)lock();}
  }
  function render(){
    if(!node.open)return;
    const body=node.querySelector('.model-body');
    body.innerHTML='<p class="model-source">M1 历史窗口 · 离线会话</p><p data-model-status role="status"></p>';
    if(settings){
      const enabled=settings.effectivePolicy.authorization.history.enabled;
      body.insertAdjacentHTML('beforeend','<div class="model-selection"><strong>历史窗口</strong><span>'+(!settings.plugin.installed?'未安装':enabled?'本会话已授权':'已安装 · 本会话未授权')+'</span></div><p class="model-hint">当前实际策略：'+(settings.effectivePolicy.mode==='rolling-summary'?'自动总结优先，携带历史摘要和最近 1 回合；停用自动总结后恢复下方窗口。':settings.effectivePolicy.blocked?'其他上下文插件尚未接入，发送将阻断。':enabled?'最近 '+settings.history.config.turns+' 个完整回合'+(settings.history.config.includeInitialCharacter?'，另带开局原文。':'。'):'完整历史。')+'</p><p class="model-hint">历史原文始终保留。M1 不调用模型；安装与逐会话授权分别保存。</p>');
      body.insertAdjacentHTML('beforeend','<div class="model-selection"><label for="history-turns">保留回合数</label><select id="history-turns" data-edit>'+Array.from({length:50},(_,i)=>'<option value="'+(i+1)+'"'+(String(i+1)===draft.turns?' selected':'')+'>'+(i+1)+'</option>').join('')+'</select></div><p><label class="model-hint"><input type="checkbox" data-opening data-edit'+(draft.includeInitialCharacter?' checked':'')+'> 额外携带开局原文</label></p><p class="model-hint">本地 M1 范围为 1–50 回合；当前输入和 system 保留，已完成历史的前后置词不重复装配。</p>');
      node.querySelector('#history-turns').onchange=e=>{draft.turns=e.target.value;};
      node.querySelector('[data-opening]').onchange=e=>{draft.includeInitialCharacter=e.target.checked;};
      if(settings.suggestion!==null){
        body.insertAdjacentHTML('beforeend','<p class="model-hint">此封存版本建议 '+settings.suggestion+' 回合；采用建议只填写本次编辑。</p><button type="button" data-suggestion data-edit>填入作品建议</button>');
        node.querySelector('[data-suggestion]').onclick=()=>{draft.turns=String(settings.suggestion);node.querySelector('#history-turns').value=draft.turns;status('已填入建议，授权并保存设置后生效。');};
      }
      if(!settings.plugin.installed)body.insertAdjacentHTML('beforeend','<p><button type="button" data-action="install">安装历史窗口</button></p>');
      else if(!enabled){
        body.insertAdjacentHTML('beforeend','<fieldset class="model-evidence"><legend>本会话授权</legend><p>允许读取会话元信息并提出历史窗口配置；无网络、凭据或任意文件权限。</p><ul>'+settings.plugin.permissions.map(p=>'<li>'+escape(p)+'</li>').join('')+'</ul><label><input type="checkbox" data-consent data-edit> 同意以上权限用于本会话</label><p><button type="button" data-action="enable">授权并启用本会话</button></p><p class="model-hint">启用立即采用已保存的 '+settings.history.config.turns+' 回合设置；下方保存按钮才会应用本次编辑。</p></fieldset>');
        node.querySelector('[data-consent]').onchange=lock;
      }else body.insertAdjacentHTML('beforeend','<p><button type="button" data-action="disable">停用本会话</button></p>');
      if(settings.busy||settings.pending)body.insertAdjacentHTML('beforeend','<p class="model-warning">原剧情'+(settings.pending?'结果待核对':'正在处理')+'；可停用插件，但仍须核对原请求。</p>');
      if(settings.unconfirmed&&settings.unconfirmed.sessionId!==sessionId)body.insertAdjacentHTML('beforeend','<p class="model-warning">另一个离线会话有配置待核对，请回到该会话核对原操作。</p>');
      body.insertAdjacentHTML('beforeend','<details class="model-evidence"><summary>安装范围与版本记录</summary><p>安装适用于本独立服务；授权只属于当前会话。卸载会停用本服务各会话的 M1，保留设置和历史。</p><p>会话 '+escape(sessionId.slice(-10))+' · 设置版本 '+settings.history.revision+' · 注册表版本 '+settings.registryRevision+'</p>'+(settings.plugin.installed?'<button type="button" data-action="uninstall">卸载历史窗口（本服务）</button>':'')+'</details>');
    }
    if(pending){
      body.insertAdjacentHTML('beforeend','<div data-pending><p>配置结果待核对：'+escape(pending.operationId)+'</p><button type="button" data-recover>核对原操作</button>'+(retry?'<button type="button" data-retry>用原编号再试</button><button type="button" data-abandon>放弃未生效编辑</button>':'')+'</div>');
      node.querySelector('[data-recover]').onclick=recover;
      if(retry){node.querySelector('[data-retry]').onclick=()=>execute(pending);node.querySelector('[data-abandon]').onclick=()=>{try{store(pending,true);pending=pendingHistoryOperation(localStorage,sessionId);retry=false;render();status('已放弃本次未生效编辑，服务端操作记录保留。');}catch(e){status(historyError(e));}};}
    }
    node.querySelector('.model-footer').innerHTML='<button type="button" data-refresh>刷新状态</button><button type="button" data-cancel>取消</button>'+(settings?'<button type="button" class="primary" data-action="configure">保存设置</button>':'');
    node.querySelector('[data-cancel]').onclick=()=>node.close();
    node.querySelector('[data-refresh]').onclick=async()=>{if(busy)return;busy=true;lock();try{await refresh();render();status('已刷新，未保存编辑仍保留。');}catch(e){loadError=true;status(historyError(e));}finally{busy=false;if(node.open)lock();}};
    for(const b of node.querySelectorAll('[data-action]'))b.onclick=()=>{try{execute(historyIntent(settings,b.dataset.action,crypto.randomUUID(),draft));}catch(e){status(historyError(e));}};
    lock();
  }
  node.addEventListener('close',()=>onClose(),{once:true});
  render();status('正在读取会话插件…');
  refresh().then(()=>{if(node.open)render();}).catch(e=>{loadError=true;if(node.open){render();status(historyError(e));}});
  return node;
}
