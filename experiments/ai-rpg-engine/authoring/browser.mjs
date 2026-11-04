import {createAuthoringApi} from './browser-api.mjs';
import {authoringEditor as editor} from './app.mjs';
import {createModelSettingsUI} from './model-ui.mjs';
import {recoveryTarget, blockedWorkRecord, resolveWorkRecovery, documentsEqual} from './recovery.mjs';
import {createMediaUI, mediaReference} from './media-ui.mjs';
import {chatBGMTrack,mountChatBGM} from './chat-bgm.mjs';
import {createWorldbookUI} from './worldbook-ui.mjs';
import {createDialogueUI} from './dialogue-ui.mjs';
import {createInputAssistanceUI} from './input-assistance-ui.mjs';
import {createDisplayRegexUI} from './display-regex-ui.mjs';
import {createCGUI} from './cg-ui.mjs';
import {LANGUAGES, AUDIENCES, TAG_LIMIT, TAG_CHAR_LIMIT, tagKey, tagIssue, tagCatalog, metadataIssues, METADATA_MESSAGES} from './metadata.mjs';

const $ = selector => document.querySelector(selector);
const esc = editor.escapeHtml;
const apiRoot = '/rpg-app/api/authoring';
const creatorMode = document.body.dataset.experience === 'creator';
const savedText = revision => creatorMode ? '已保存' : '已保存 · revision ' + revision;
const clone = value => structuredClone(value);
const button = (label, action, extra = '') => '<button type="button" data-b1="' + action + '" ' + extra + '>' + label + '</button>';
const field = (label, id, value = '', extra = '') => '<label class="form-label" for="' + id + '">' + label + '</label><input id="' + id + '" value="' + esc(value) + '" ' + extra + '>';
let work = null, blank = null, busy = false, pending = null, conflict = false, database = null;
let validation = null, uploadTarget = null, focusBeforeWrite = null, composing = false;
let sessionPanel = null, playbackPanel = null;
let draftWriteSettled = null;
const busyButtons = new Map();
const failureText = {
  NETWORK_READ_FAILED:'暂未读取到最新状态。已显示的记录和输入仍保留，请稍后刷新。',
  AUTHORING_NUMBER_INVALID:'数值为空、格式无效或超出范围。请修正后保存，旧版本保持不变。',
  AUTHORING_CG_REPLAY_CONFLICT:'原文或作品配置与已冻结回放不一致。需明确生成新的 CG 回放，旧结果不会重抽。',
  AUTHORING_CG_REPLAY_EXPIRED:'CG 回放已过期；旧结果不会重抽。可明确生成新的本轮回放。',
  AUTHORING_CG_REPLAY_NOT_FOUND:'服务中没有这份 CG 回放（可能已重启）。不会自动重新抽图。',
  AUTHORING_CG_REPLAY_PARENT:'上一份背景回放已过期或属于不同配置。重新生成时会从作品默认背景开始。',
  AUTHORING_CG_REPLAY_RESOURCE:'选中图片缺失或内容已变化，整份新画面未应用。请核对本地图库。',
  AUTHORING_CG_REPLAY_CAPACITY:'离线 CG 回放缓存已达上限，未自动删除或重新抽图。',
  AUTHORING_CG_OUTPUT_CAPACITY:'本轮 CG 超过32张或8 MiB，未截断或丢弃选图。',
  AUTHORING_CG_KEYWORDS:'CG需要至少一个非空关键词。未完成草稿仍可保留。',
  AUTHORING_CG_IMAGE_REQUIRED:'CG需要至少一张有效图片。',
  AUTHORING_WEIGHT_TOTAL_ZERO:'概率权重总和不能为零。',
  ...METADATA_MESSAGES,
  AUTHORING_DIALOGUE_AMBIGUOUS:'旧开场白与新开场白列表不能同时非空，请保留希望使用的版本。',
  AUTHORING_DIALOGUE_INCOMPLETE:'预设对话尚未完整，请检查标题、提问、回答及字数。',
  AUTHORING_SHORTCUT_INCOMPLETE:'快捷命令和内容不能为空，分别最多15字与100字。旧原文仍保留。',
  AUTHORING_SCRIPT_TIMEOUT:'作者脚本超过执行时限，已停止。上一份画面和玩家输入已保留。',
  AUTHORING_SCRIPT_RESOURCE_LIMIT:'作者脚本达到内存或调用栈上限，已停止。请检查源码。',
  AUTHORING_SCRIPT_EXCEPTION:'作者脚本发生错误，已停止。请检查下方诊断。',
  AUTHORING_DOM_UNSUPPORTED:'脚本使用了尚未支持的 DOM 能力，未继续执行。',
  AUTHORING_DOM_CAPACITY:'脚本生成的页面节点或层级超过上限，已停止。',
  AUTHORING_SCRIPT_SOURCE_UNSUPPORTED:'仅支持作品内普通脚本；外部脚本、模块及动态脚本标签未执行。',
  AUTHORING_SCRIPT_ASYNC_PENDING:'异步任务未能在本次事件内收束，已停止。未提交部分画面。',
  AUTHORING_SCRIPT_ASYNC_UNSUPPORTED:'本批仅支持同步开局脚本；async/await、动态模块、eval 和异步任务未执行。请使用同步表单事件及 rpg 交接接口。',
  AUTHORING_SCRIPT_GESTURE_REQUIRED:'初始化不能交接或复制文字。请改为由玩家操作按钮触发。',
  AUTHORING_SCRIPT_CLOSED:'脚本预览已停止或过期。画面和输入保留；需手动刷新预览重新运行。',
  AUTHORING_SCRIPT_CAPACITY:'脚本预览达到容量上限。最多4个活动实例，每个最多512次事件；可停止旧预览后重试。',
  AUTHORING_PRESENTATION_STALE:'准备期间编辑内容已变化。请刷新预览后重新核对。',
  AUTHORING_PRESENTATION_SIZE:'预览内容或引用图片超过本批展示容量，请减少单次预览内容。原文未截断。',
  AUTHORING_PRESENTATION_COMPLEX:'HTML 层级或节点过多，本次预览已停止。请检查结构。',
  AUTHORING_PRESENTATION_TIMEOUT:'HTML 解析超过时限，本次预览已停止。可以保留源码后检查结构。',
  AUTHORING_PRESENTATION_RESOURCE_LIMIT:'本次预览解析达到资源上限，已停止；编辑界面与作品仍保留。',
  AUTHORING_TEXT_INVALID:'字段文本无效或超过容量，请根据字段路径核对。',
  AUTHORING_ASSET_TYPE: '文件内容与所选媒体格式不符，未上传。原文和已有资源保持不变。',
  AUTHORING_MEDIA_TYPE: '请选择 PNG、JPG、WEBP、GIF 图片或 MP3 音频；编辑器仅接收图片。',
  AUTHORING_ASSET_SIZE: '文件超过本地图库容量限制，未上传；编辑器图片须在 5 MiB 以内。',
  AUTHORING_REVISION_CONFLICT: '作品已被其他标签页修改。本页草稿已保留；请导出备份，再载入服务器版本核对。',
  AUTHORING_RESULT_UNKNOWN: '保存结果尚未确认。已保留原操作编号和草稿，请核对原操作，不会自动重发。',
  AUTHORING_OPERATION_PENDING: '本地库有待核对操作。请先核对原操作，再继续写入。',
  AUTHORING_WRITE_FAILED: '本次落盘失败，旧作品保持不变，本页草稿仍保留。',
  AUTHORING_UNKNOWN_FIELD: '配置包含未知字段，未导入；请核对字段名称。',
  AUTHORING_JSON_DUPLICATE_KEY: 'JSON 包含重复字段，未导入。',
  AUTHORING_ASSET_MISSING: '缺少资源文件。请上传资源或导入包含资源的完整作品包。',
  AUTHORING_IMPORT_PREVIEW_CHANGED: '导入内容已变化，请重新预检。',
  AUTHORING_ORIGIN_REJECTED: '请求来源校验未通过，请从本地创作地址重新打开。',
  AUTHORING_STORAGE_UNAVAILABLE: '本地存储暂不可用。保留草稿，并核对原操作结果。',
};
const describe = error => (failureText[error.code] || '操作未完成，请核对配置或本地服务。') + (error.path ? ' 字段：' + error.path : '') + ' [' + (error.code || 'NETWORK_RESULT_UNKNOWN') + ']';

// B4-A previews are separate display snapshots, never model history or save operations.
let previewGeneration=0,activePresentation=null,previewReply='',previewWorkId=null,previewRole='assistant';
let previewCGUser='',previewCG=null,bgmMode='opening';
export function cgReplayContext(value){return JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.entries(item).sort(([a],[b])=>a<b?-1:a>b?1:0)):item);}
const cgReplayKey=()=> 'modelmirror.authoring.cg-replay/'+work?.id;
function keepCGReplay(){try{sessionStorage.setItem(cgReplayKey(),JSON.stringify({reply:previewReply,user:previewCGUser,record:previewCG}));}catch{editor.toast('浏览器无法保留此份回放引用，刷新前请复制原文。');}}
const previewMessages={SCRIPT_NOT_EXECUTED:'展示副本不执行此脚本，源码仍保留',RESOURCE_MISSING:'缺少本地资源',RESOURCE_REFERENCE_BLOCKED:'图片未配置本地资源映射；外部引用未加载',RESOURCE_INLINE_IMAGE_BLOCKED:'未加载内嵌 data 图片；请使用本地资源。内联静态 SVG 标签仍可展示',ELEMENT_REMOVED:'展示副本已移除不支持的元素',ATTRIBUTE_REMOVED:'展示副本已移除不支持的属性',LINK_BLOCKED:'外部跳转已阻止',INPUT_TYPE_REPLACED:'输入类型已改为普通文本'};
const playerDraftKey=()=> 'modelmirror.authoring.player-draft/'+work?.id;
function keepPlayerDraft(){try{if(work?.id&&$('#player-input').value.length<=240000)sessionStorage.setItem(playerDraftKey(),$('#player-input').value);}catch{editor.toast('浏览器未能保存玩家输入草稿，请自行复制保留。');}}
function releasePresentation(value){if(value?.token)api('/presentations/release',{token:value.token}).catch(()=>{});}
window.addEventListener('pagehide',()=>{
  chatBGM.destroy();
  if(activePresentation?.token)fetch(apiRoot+'/presentations/release',{method:'POST',headers:{'Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify({token:activePresentation.token}),keepalive:true}).catch(()=>{});
});
async function renderPreview(mode,createCG=false){
  const generation=++previewGeneration;
  const requestedWorkId=work?.id;
  // A local save can reach disk before its receipt updates the editor revision.
  while(draftWriteSettled){
    await draftWriteSettled;
    if(generation!==previewGeneration || work?.id!==requestedWorkId)return;
    if(pending || conflict){notice(describe({code:conflict?'AUTHORING_REVISION_CONFLICT':'AUTHORING_OPERATION_PENDING'}));return;}
  }
  const revisionAtStart=work?.revision;
  playbackPanel?.hide();$('#preview-content').hidden=false;
  bgmMode=mode;syncChatBGM(false);
  const previous=activePresentation;activePresentation=null;
  document.querySelectorAll('[data-preview]').forEach(element=>element.setAttribute('aria-pressed',String(element.dataset.preview===mode)));
  const root=$('#preview-content');
  if(previewWorkId!==work?.id){
    previewWorkId=work?.id;previewReply='';previewRole='assistant';previewCGUser='';previewCG=null;
    try{const saved=JSON.parse(sessionStorage.getItem(cgReplayKey())||'null');if(saved&&typeof saved.reply==='string'&&typeof saved.user==='string'){previewReply=saved.reply;previewCGUser=saved.user;previewCG=saved.record;}}catch{/* An unreadable reference never starts a new CG operation. */}
    try{$('#player-input').value=sessionStorage.getItem(playerDraftKey())||'';}catch{$('#player-input').value='';}
    $('#input-count').textContent=[...$('#player-input').value].length+' 字';
  }
  if(!work?.id){releasePresentation(previous);root.innerHTML='<p class="hint">新建或打开作品后预览。</p>';return;}
  root.onclick=null;
  if(mode==='dialogue'){releasePresentation(previous);dialogueUI.preview(root);return;}
  if(mode==='assembly'){
    releasePresentation(previous);
    root.innerHTML='<h2>提示词如何发送</h2><p>主提示词和命中的世界书组成系统设定；前置词、玩家输入、后置词组成本轮输入。已有历史不重复包裹前后置词。</p><ol class="assembly-steps"><li><strong>系统设定</strong><span>主提示词 → 世界书 → 已启用的摘要与记忆</span></li><li><strong>对话历史</strong><span>按本会话实际启用的历史策略选取</span></li><li><strong>本轮输入</strong><span>前置词 → 玩家输入 → 后置词</span></li></ol><p>开局页面和回复样式只负责展示，不会自动成为系统设定。</p><p class="hint">这是装配规则说明。已发送请求及其具体内容，请在“对话与记录”中查看。</p><a href="/creator-guide.html#prompt" target="_blank" rel="noopener">阅读提示词放置示例</a>';return;
  }
  const replyControl=mode==='reply'?'<div class="regex-preview-role" role="group" aria-label="回放来源"><span>回放来源</span>'+[['assistant','AI输出'],['user','用户输入']].map(([value,label])=>'<label><input type="radio" name="preview-role" value="'+value+'"'+(previewRole===value?' checked':'')+'>'+label+'</label>').join('')+'</div><label class="form-label" for="preview-reply-source">粘贴原始回复（Markdown / HTML）</label><textarea id="preview-reply-source" rows="5" placeholder="粘贴完整原始回复，然后点击渲染。">'+esc(previewReply)+'</textarea><button type="button" id="render-reply">渲染回复</button>':'';
  const cgControl=mode==='reply'?'<label class="form-label" for="preview-cg-user">本轮玩家原文（供 CG 匹配）</label><textarea id="preview-cg-user" rows="2">'+esc(previewCGUser)+'</textarea><button type="button" id="create-cg-replay">生成本轮 CG 回放</button><p class="hint">请粘贴完整回复。仅此按钮生成选图；渲染与刷新复用结果。同一配置内连续回放未命中时保留上一张背景。配置变化或记录过期后，新回放从作品默认背景开始。记录在服务内保留10分钟，重启后不自动重抽，不写入正式历史。</p>':'';
  if(!previous||previous.workId!==work.id||!$('#presentation-frame'))root.innerHTML='<div id="presentation-reply-controls"></div><p class="hint" id="presentation-status" role="status"></p><button type="button" id="stop-script" hidden>停止脚本</button><div id="presentation-proposal" class="data-block" hidden></div><div id="presentation-frame"></div><details class="help-disclosure"><summary>预览诊断与兼容说明</summary><div id="presentation-diagnostics"></div><p>作者 JS 在独立引擎内执行，只操作本作品的表单和展示节点。回复脚本禁止执行。编辑后点“刷新”更新预览，不保存或调用模型。</p><p>脚本可用 rpg.setInput(text)、rpg.saveDraft(text)、rpg.copyText(text) 提出交接候选，必须由玩家按钮触发，并在此界面采用。声明式 data-rpg-input、data-rpg-copy、data-rpg-draft 仍可用。异步复制仅在此处确认成功后继续；取消不会触发作者复制成功提示。</p><p>支持受限 DOM/SVG、定时器、动画帧、画布、布局快照、合成音效和本地 MP3。浏览器可能需点击后才能播放。预览最多运行10分钟；停止或关闭后释放脚本与声音。网络、存储、父窗口和直接模型调用仍不可用。不支持的调用会明确停止。</p></details>';
  if(!$('#presentation-suggestions'))$('#presentation-frame').insertAdjacentHTML('afterend','<div id="presentation-suggestions" class="assistance-choices" aria-label="下一步建议"></div>');
  $('#presentation-suggestions').replaceChildren();
  $('#presentation-reply-controls').innerHTML=replyControl+cgControl;
  $('#presentation-status').textContent='正在准备隔离展示…';$('#presentation-proposal').hidden=true;$('#stop-script').hidden=true;
  if(mode==='reply'){
    document.querySelectorAll('[name="preview-role"]').forEach(input=>input.onchange=()=>{previewRole=input.value;$('#create-cg-replay').disabled=previewRole!=='assistant';$('#presentation-status').textContent='来源已变化，点击渲染回复更新显示。';});
    $('#preview-reply-source').oninput=event=>{previewReply=event.target.value;};
    $('#render-reply').onclick=()=>renderPreview('reply');
    $('#preview-cg-user').oninput=event=>{previewCGUser=event.target.value;};
    $('#create-cg-replay').disabled=previewRole!=='assistant';
    $('#create-cg-replay').onclick=()=>renderPreview('reply',true);
  }
  try{
    const document=currentDocument(),documentText=JSON.stringify(document),revision=work.revision,workId=work.id,replyText=previewReply,messageRole=previewRole;
    if(mode==='reply'){$('#render-reply').disabled=true;$('#create-cg-replay').disabled=true;}
    const cgUser=previewCGUser;
    let cgRequest=null;
    if(mode==='reply'&&messageRole==='assistant'){
      const context=cgReplayContext({workId,revision,document});
      if(createCG){
        if(!replyText.trim())throw {code:'AUTHORING_TEXT_INVALID',path:'/reply'};
        const previousOperationId=previewCG?.context===context&&previewCG.expiresAt>Date.now()?previewCG.request.operationId:null;
        const request={action:'replay',operationId:crypto.randomUUID(),previousOperationId,user:cgUser,device:previousOperationId?previewCG.request.device:window.innerWidth<=600?'mobile':'desktop'};
        previewCG={context,request,expiresAt:0};keepCGReplay();
        cgRequest={...request,action:'create'};
      }else if(previewCG)cgRequest={...previewCG.request,action:'replay',user:cgUser};
    }
    const result=await api('/presentations/prepare',{workId,expectedRevision:revision,document,mode,reply:replyText,...(mode==='reply'?{messageRole}:{}),...(cgRequest?{cg:cgRequest}:{}),viewport:{innerWidth:Math.max(1,Math.min(4096,$('#presentation-frame').clientWidth)),innerHeight:Math.max(320,Math.min(800,window.innerHeight-300)),devicePixelRatio:Math.max(.5,Math.min(3,window.devicePixelRatio))}});
    if(generation!==previewGeneration||work?.id!==workId){releasePresentation(result);releasePresentation(previous);return;}
    if(work.revision!==revision||JSON.stringify(currentDocument())!==documentText||(mode==='reply'&&(previewReply!==replyText||previewRole!==messageRole||previewCGUser!==cgUser))){releasePresentation(result);throw {code:'AUTHORING_PRESENTATION_STALE'};}
    if(result.manifest.cg&&previewCG?.request.operationId===result.manifest.cg.plan.binding.operationId){previewCG.expiresAt=result.manifest.cg.expiresAt;keepCGReplay();}
    const frame=documentElement('iframe');
    frame.title=mode==='opening'?'开局隔离预览':'回复隔离预览';frame.width='100%';frame.height='420';frame.setAttribute('frameborder','0');
    // A reply may use 100vh/min-height. Feeding its measured content height
    // back into its viewport makes it grow forever. Keep a stable viewport;
    // long replies scroll inside it, with the author's own viewport CSS intact.
    frame.style.height='clamp(320px, calc(100dvh - 300px), 800px)';
    frame.setAttribute('sandbox','allow-scripts');frame.referrerPolicy='no-referrer';
    frame.setAttribute('allow',"camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'; fullscreen 'none'");
    releasePresentation(previous);
    activePresentation={...result,frame,mode,workId,revision,documentText,replyText,messageRole,generation,scriptSequence:0,proposals:[]};
    frame.src=result.frameUrl;$('#presentation-frame').replaceChildren(frame);
    $('#presentation-status').textContent=(mode==='opening'?'开局页面':'回复展示')+(creatorMode?' · '+(documentsEqual(document,work.draft)?'已保存草稿':'含未保存修改'):' · 当前编辑快照；未保存、未派发')+(result.manifest.cg?' · CG 已冻结，渲染不重抽':'');
    renderPresentationDiagnostics(result.manifest);
    syncChatBGM(true);
    renderSuggestionChoices(activePresentation);
    if(result.manifest.displayRules&&!result.manifest.displayRules.applied){$('#presentation-status').textContent='显示替换未生效：'+result.manifest.diagnostics.filter(d=>d.code.startsWith('DISPLAY_REGEX_')).map(d=>regexDiagnostic(d.code)).join('；')+'。整批替换已撤回，原始文本保留。';$('#presentation-status').classList.add('regex-status-error');}else $('#presentation-status').classList.remove('regex-status-error');
    $('#stop-script').hidden=!result.manifest.authorScriptsExecuted;
    $('#stop-script').onclick=()=>stopScript(activePresentation,'脚本已停止，当前画面和玩家输入保留。刷新预览可重新运行。');
  }catch(error){
    if(generation===previewGeneration){
      if(error.code==='AUTHORING_REVISION_CONFLICT' && work?.id===requestedWorkId && !conflict && (draftWriteSettled || work?.revision!==revisionAtStart)){
        error={code:'AUTHORING_PRESENTATION_STALE'};
      }
      if(previous?.workId===work?.id){activePresentation=previous;stopScript(previous,'新预览未生成，保留上一份画面。'+describe(error));}
      else{$('#presentation-status').textContent='预览未生成，源码已保留。'+describe(error);releasePresentation(previous);}
      if(error.details?.diagnostic)$('#presentation-diagnostics').textContent=error.details.diagnostic;
    }else releasePresentation(previous);
  }finally{
    if(generation===previewGeneration&&mode==='reply'&&$('#render-reply')){$('#render-reply').disabled=false;$('#create-cg-replay').disabled=previewRole!=='assistant';}
  }
}
const documentElement=tag=>document.createElement(tag);
function renderSuggestionChoices(active){
  const root=$('#presentation-suggestions');root.replaceChildren();
  const choices=active.manifest.suggestions||[];
  if(!choices.length)return;
  const hint=documentElement('p');hint.textContent='下一步建议 · 点击填入输入框，尚未发送';root.append(hint);
  for(const text of choices){
    const button=documentElement('button');button.type='button';button.textContent=text;
    button.onclick=()=>{
      if(!presentationCurrent(active)||previewReply!==active.replyText||previewRole!==active.messageRole){notice('建议对应的作品或回复已变化，请重新渲染后采用。');return;}
      editor.fillComposer(text);keepPlayerDraft();clearInputCheck();notice('已填入下一步建议，尚未发送。');
    };
    root.append(button);
  }
}
const regexDiagnostic=code=>({DISPLAY_REGEX_UNICODE:'替换会破坏 Unicode 字符',DISPLAY_REGEX_EMPTY:'匹配模式为空',DISPLAY_REGEX_SYNTAX:'正则语法无效',DISPLAY_REGEX_TIMEOUT:'正则执行超过1秒',DISPLAY_REGEX_RESOURCE_LIMIT:'正则达到内存或执行限制',DISPLAY_REGEX_CAPACITY:'替换结果超过2 MiB',DISPLAY_REGEX_MATCH_LIMIT:'匹配次数超过10万',DISPLAY_REGEX_BUSY:'同时执行的正则任务已达上限'}[code]||code);
function renderPresentationDiagnostics(manifest){
  $('#presentation-diagnostics').innerHTML=manifest.diagnostics.map(item=>'<p class="hint">'+esc(previewMessages[item.code]||regexDiagnostic(item.code))+(item.detail?'：'+esc(item.detail):'')+'（'+item.count+'）</p>').join('')+
    (manifest.cg?'<div class="regex-diagnostics"><p>普通 CG · '+manifest.cg.plan.chat.length+' 张聊天插图 · 背景'+esc({selected:'本轮切换',inherited:'沿用上一轮',default:'使用作品默认图',none:'无图片'}[manifest.cg.backgroundSource])+'</p>'+manifest.cg.plan.decisions.map(entry=>'<p>'+esc(entry.entryId)+' · '+esc({selected:'已选中',superseded:'被后续背景覆盖',miss:'未命中',disabled:'已停用'}[entry.status])+(entry.imageIndex!==null?' · 第 '+(entry.imageIndex+1)+' 张':'')+' · 命中角色 '+esc([...new Set(entry.hits.map(hit=>hit.role==='user'?'用户':'AI'))].join('、')||'无')+'</p>').join('')+'<p>CG 操作：'+esc(manifest.cg.plan.binding.operationId)+'</p><p>选图 hash：'+esc(manifest.cg.plan.sha256)+'</p><p>完整回合原文 hash：'+esc(manifest.cg.plan.binding.sourceHash)+'</p><p>服务内有效至 '+esc(new Date(manifest.cg.expiresAt).toLocaleTimeString())+'；仅此手动回放临时保留，实际对话的展示结果随会话保存。</p></div>':'')+
    (manifest.displayRules?'<div class="regex-diagnostics"><p>显示规则 · '+(manifest.displayRules.role==='user'?'用户输入':'AI输出')+' · '+(manifest.displayRules.applied?'完成':'整批撤回')+'</p>'+manifest.displayRules.entries.map(entry=>'<p>'+esc(entry.name||entry.id)+' · '+esc({disabled:'已停用',scope:'不在本次范围',pending:'未执行',applied:'已检查',failed:'失败','rolled-back':'已撤回'}[entry.status]||entry.status)+' · 匹配 '+entry.matches+' 次</p>').join('')+'<p>规则 hash：'+manifest.displayRules.rulesHash+'</p></div>':'')+'<p class="hint">来源 revision '+manifest.binding.revision+' · '+manifest.resources.length+' 个本地资源 · '+(manifest.authorScriptsExecuted?'隔离脚本事件 '+manifest.sequence:'静态展示')+'</p><p class="hint">原文 hash：'+manifest.sourceHash+'</p><p class="hint">展示 hash：'+manifest.renderHash+'</p>';
}
const presentationCurrent=active=>activePresentation===active&&work?.id===active.workId&&work.revision===active.revision&&JSON.stringify(currentDocument())===active.documentText;
const frameMessage=(active,type,payload={})=>active.frame.contentWindow?.postMessage({protocol:'modelmirror.rpg.presentation/1',token:active.token,type,...payload},'*');
function stopScript(active,message){
  if(!active)return;active.stopped=true;active.proposals=[];frameMessage(active,'stop');api('/presentations/stop',{token:active.token}).catch(()=>{});
  if(activePresentation===active){$('#stop-script').hidden=true;$('#presentation-status').textContent=message;$('#presentation-proposal').hidden=true;}
}
window.addEventListener('message',async event=>{
  const active=activePresentation,message=event.data;
  if(!active||event.source!==active.frame.contentWindow||event.origin!=='null'||!message||message.protocol!=='modelmirror.rpg.presentation/1'||message.token!==active.token)return;
  if(message.type==='height'&&Number.isFinite(message.payload))return;
  if(message.type==='blocked'){$('#presentation-status').textContent='展示中的外部资源或可执行内容已被隔离策略阻止；源码保持不变。';return;}
  if(message.type==='script-client-error'){stopScript(active,String(message.payload).slice(0,200));return;}
  if(message.type==='author-event'){
    if(active.stopped||!active.manifest.authorScriptsExecuted)return;
    if(!presentationCurrent(active)){stopScript(active,'编辑内容或资源版本已变化，请刷新预览后继续操作。');return;}
    const payload=message.payload;
    if(active.eventPending||payload?.sequence!==active.scriptSequence+1){stopScript(active,'脚本事件顺序不一致，已停止；画面和输入保留。');return;}
    active.eventPending=true;
    try{
      const result=await api('/presentations/event',{token:active.token,operationId:crypto.randomUUID(),sequence:payload.sequence,event:payload.event});
      if(!presentationCurrent(active)||active.stopped){stopScript(active,'预览已变化，迟到结果未应用。');return;}
      active.scriptSequence=result.sequence;active.manifest=result.manifest;active.proposals.push(...result.proposals);
      frameMessage(active,'patch',{sequence:result.sequence,payload:result.patch});renderPresentationDiagnostics(result.manifest);
    }catch(error){if(activePresentation===active){stopScript(active,describe(error)+' 不会自动重发事件。');if(error.details?.diagnostic)$('#presentation-diagnostics').textContent=error.details.diagnostic;}}
    finally{active.eventPending=false;}
    return;
  }
  if(message.type==='patch-applied'){
    if(message.payload===active.scriptSequence&&active.proposals.length&&$('#presentation-proposal').hidden)showProposal(active,active.proposals.shift());return;
  }
  if(active.mode!=='opening'||!['input','draft','copy'].includes(message.type)||typeof message.payload!=='string'||[...message.payload].length>120000)return;
  if(!presentationCurrent(active)){$('#presentation-status').textContent='编辑内容已变化，请刷新预览后重新交接。';return;}
  showProposal(active,message);
});
function showProposal(active,message){
  const proposal=$('#presentation-proposal');proposal.hidden=false;
  proposal.innerHTML='<strong>采用这段开局文字？</strong><label class="sr-only" for="handoff-text">交接内容</label><textarea id="handoff-text" rows="4" readonly>'+esc(message.payload)+'</textarea><div class="row"><button type="button" id="accept-handoff">'+(message.type==='copy'?'复制到剪贴板':'采用为玩家输入')+'</button><button type="button" id="cancel-handoff">取消</button></div><p class="hint">'+(message.type==='copy'?'复制到剪贴板；保留当前玩家输入。':'采用将替换当前输入；不会发送请求。')+'</p>';
  const clear=(restore=true,ok=false)=>{if(message.id&&!active.stopped)frameMessage(active,'feedback',{items:[{id:message.id,ok}]});proposal.hidden=true;proposal.replaceChildren();if(active.proposals.length){showProposal(active,active.proposals.shift());return;}if(restore)active.frame.focus();};
  $('#cancel-handoff').onclick=()=>clear();
  $('#accept-handoff').onclick=async()=>{
    if(activePresentation!==active||work?.id!==active.workId||work.revision!==active.revision||JSON.stringify(currentDocument())!==active.documentText){clear();editor.toast('预览已过期，请刷新后重新交接。');return;}
    if(message.type==='copy'){
      try{await navigator.clipboard.writeText(message.payload);editor.toast('已复制开局文字。');clear(true,true);}catch{editor.toast('复制未完成，请重试或取消；作者成功提示尚未触发。');}
    }else{editor.fillComposer(message.payload);keepPlayerDraft();clearInputCheck();clear(false);editor.toast('已填入玩家输入，尚未发送。');}
  };
  proposal.onkeydown=key=>{if(key.key==='Escape'){key.preventDefault();key.stopPropagation();clear();}};
  $('#accept-handoff').focus();
}
function notice(message) {
  if(!creatorMode)$('#save-status').textContent = message;
  editor.toast(message);
}
function problem(error) {
  notice(describe(error));
  editor.openDialog('操作提示', '<p>' + esc(describe(error)) + '</p>' + (pending ? '<p class="hint">操作：' + esc(recoveryTarget(pending).operationId) + '</p>' + button('核对原操作', 'recover') : '') +
    (conflict ? '<p class="hint">直接重新保存不会覆盖其他标签的修改。</p>' + button('导出本页草稿', 'backup') + button('载入服务器版本', 'reload') : ''), null, undefined, '关闭');
}
function run(action) { Promise.resolve().then(action).catch(problem); }
const api = createAuthoringApi(apiRoot);

function openJournal() {
  return new Promise((ok, no) => {
    const req = indexedDB.open('modelmirror-authoring-b1', 1);
    req.onupgradeneeded = () => req.result.createObjectStore('pending', {keyPath:'operationId'});
    req.onblocked = () => no({code:'LOCAL_JOURNAL_BLOCKED'});
    req.onerror = () => no({code:'LOCAL_JOURNAL_UNAVAILABLE'});
    req.onsuccess = () => {
      database = req.result;
      database.onversionchange = () => { database.close(); database = null; notice('草稿保护库需要更新，请刷新此页。'); };
      ok();
    };
  });
}
function journal(action, value) {
  if (!database) return Promise.reject({code:'LOCAL_JOURNAL_UNAVAILABLE'});
  return new Promise((ok, no) => {
    let result;
    const tx = database.transaction('pending', action === 'read' ? 'readonly' : 'readwrite');
    const store = tx.objectStore('pending');
    const req = action === 'read' ? store.getAll() : action === 'put' ? store.put(value) : store.delete(value);
    req.onsuccess = () => { result = req.result; };
    tx.oncomplete = () => ok(result);
    tx.onabort = tx.onerror = () => no({code:'LOCAL_JOURNAL_UNAVAILABLE'});
  });
}
function setBusy(value) {
  if (value && !busy) {
    const element = document.activeElement;
    focusBeforeWrite = {element,start:element?.selectionStart,end:element?.selectionEnd};
  }
  busy = value;
  $('.edit-pane').inert = value || !work;
  for (const id of ['save-button','publish-button','gallery-button']) $( '#' + id).disabled = value || !work || !!pending || conflict;
  $('#works-button').disabled = value;
  $('#export-button').disabled = value || !work;
  if (value) {
    for (const el of document.querySelectorAll('#dialog-actions button, #dialog-body button')) { busyButtons.set(el,el.disabled); el.disabled = true; }
  } else {
    for (const [el,disabled] of busyButtons) if (el.isConnected) el.disabled = disabled;
    busyButtons.clear();
    const focus = focusBeforeWrite; focusBeforeWrite = null;
    if (focus?.element?.isConnected && !$('#dialog').open) {
      focus.element.focus({preventScroll:true});
      if (typeof focus.start === 'number') focus.element.setSelectionRange(focus.start,focus.end);
    }
  }
}
function assetUrl(asset, version = null) {
  return apiRoot + '/works/' + work.id + '/assets/' + encodeURIComponent(asset.alias) + '?hash=' + asset.sha256 + (version ? '&version=' + version : '');
}
function images() {
  return (work?.assets || []).filter(a => a.mediaType.startsWith('image/')).map(a => ({id:a.alias,name:a.fileName,alias:a.alias,url:assetUrl(a)}));
}
function toView(doc) {
  worldUI.load(doc.worldbook,work?.id || "blank");
  dialogueUI.load(doc.enhancements);
  assistanceUI.load(doc.enhancements);
  regexUI.load(doc.enhancements.displayRules,work?.id||'blank');
  cgUI.load(doc.enhancements.cg,work?.id||'blank');
  const audio = work.assets.find(a => a.alias === doc.settings.media.bgm);
  return {
    schema:doc.format, name:doc.metadata.name, tags:clone(doc.metadata.tags), language:doc.metadata.language, audience:doc.metadata.audience,
    autosave:doc.settings.autosave, orders:clone(doc.settings.orders),
    intro:doc.sources.introduction, description:doc.sources.description, system:doc.sources.system,
    prefix:doc.sources.prefix, suffix:doc.sources.suffix, css:doc.sources.outputCss,
    startupHtml:doc.sources.startupHtml,startupCss:doc.sources.startupCss,startupJs:doc.sources.startupJs,
    cover:doc.settings.media.cover,desktop:doc.settings.media.desktop,mobile:doc.settings.media.mobile,
    bgmAutoplay:doc.settings.bgmAutoplay,
    bgm:audio ? {name:audio.fileName,alias:audio.alias,url:assetUrl(audio)} : doc.settings.media.bgm ? {name:doc.settings.media.bgm,alias:doc.settings.media.bgm,url:null} : null,
    // Legacy prototype-only fields stay inert; the worldbook editor owns the full schema.
    worldDefaults:{roles:['user'],rule:'or',targets:['system']},worldbooks:[],categories:[],
    blocked:[],defaultMemory:doc.settings.defaultMemory,suggestions:false,dialogueMode:'opening',
    opening:doc.enhancements.dialogue.opening,presets:[],shortcuts:[],regexRules:[],cg:[],
  };
}
function currentDocument() {
  if (!work) return null;
  const view = editor.read(), doc = clone(work.draft);
  doc.metadata = {name:view.name,tags:clone(view.tags),language:view.language,audience:view.audience};
  for (const [field, path] of Object.entries({intro:'introduction',description:'description',system:'system',prefix:'prefix',suffix:'suffix',css:'outputCss',startupHtml:'startupHtml',startupCss:'startupCss',startupJs:'startupJs'})) doc.sources[path] = view[field];
  doc.worldbook = worldUI.read();
  Object.assign(doc.enhancements,dialogueUI.read());
  Object.assign(doc.enhancements,assistanceUI.read());
  doc.enhancements.displayRules=regexUI.read();
  doc.enhancements.cg=cgUI.read();
  doc.settings.autosave = view.autosave; doc.settings.orders = clone(view.orders);
  for (const key of ['cover','desktop','mobile']) doc.settings.media[key] = view[key];
  // Missing audio references are retained so publication reports them instead of silently deleting them.
  doc.settings.media.bgm = mediaReference(view.bgm);
  if(typeof view.bgmAutoplay==='boolean')doc.settings.bgmAutoplay=view.bgmAutoplay;
  return doc;
}
function afterBasic() {
  metadataControls();
  $('#source-intro').closest('.field').querySelector('.hint').textContent='HTML/CSS 在隔离预览中展示；开局 JavaScript 在独立引擎内执行。编辑后点击右侧“刷新”，不会派发模型请求。';
  const auto = $('[data-bind="autosave"]').closest('.field').querySelector('.hint');
  auto.textContent = '默认关闭。开启后每 3 分钟保存到本地；弹窗或开场白编辑打开、冲突或待核对时暂停自动保存。';
  $('#panel-basic').insertAdjacentHTML('beforeend',
    '<details class="help-disclosure"><summary>独立开局源码字段</summary><p class="hint">HTML 非空时替代详细介绍进入开局预览；CSS 随展示快照隔离。JavaScript 在独立引擎内运行，支持受限 DOM 与表单；点击刷新执行，不会发送剧情请求。</p>' +
    editor.editor('startupHtml','开局 HTML') + editor.editor('startupCss','开局 CSS') + editor.editor('startupJs','开局 JavaScript') + '</details>');
  $('#name').maxLength = 120;
  $('[data-target="cover"].upload-box').closest('.field').outerHTML = media.field('封面','cover');
  media.bindFields();
  $('#panel-basic .help-disclosure p').textContent = 'HTML/CSS 可隔离预览；作者 JS、创作增强与实际请求按后续子批接入。';
  if(creatorMode){
    $('#source-intro').closest('.field').querySelector('.hint').textContent='写下作品介绍或可交互的开局页面，点击右侧“刷新”查看效果。';
    auto.textContent='每 3 分钟保存草稿。弹窗编辑、冲突或结果待核对时暂停。';
    for(const [id,hint] of [['system','设定世界、角色、玩法和回复格式，作为模型的系统提示词。'],['prefix','每轮添加到玩家输入之前，适合简短的固定资料。'],['suffix','每轮添加到玩家输入之后，适合持续生效的回复要求。']])$('#source-'+id).closest('.field').querySelector('.hint').textContent=hint;
    $('#panel-basic').insertAdjacentHTML('afterbegin','<nav class="creator-jumps" aria-label="基础设置快捷定位"><a href="#name">作品资料</a><a href="#source-intro">开局页面</a><a href="#source-system">提示词</a><a href="/creator-guide.html#start" target="_blank" rel="noopener">创作入门 ↗</a></nav>');
    const capability=$('#panel-basic .help-disclosure');capability.remove();
    const source=$('#panel-basic .help-disclosure');source.querySelector('summary').textContent='独立开局源码';
    source.querySelector('p').textContent='分别编辑 HTML、CSS 和 JavaScript。HTML 非空时替代上方详细介绍，作为开局页面。';
  }
}

// B2-B: metadata controls share validation with the host; drafts in dialogs stay local.
let metadataMenu = null, tagManagerLoading = false, tagRulesSeen = false;
const tagRulesPreference = 'modelmirror-authoring-tag-rules/1';
try { tagRulesSeen = localStorage.getItem(tagRulesPreference) === 'seen'; } catch { /* Browser preference is optional; never blocks authoring. */ }
function closeMetadataMenu(restore = false) {
  if (!metadataMenu) return;
  const {trigger, popup, events} = metadataMenu;
  metadataMenu = null; events.abort(); popup.remove(); trigger.setAttribute('aria-expanded','false');
  if (restore && trigger.isConnected) trigger.focus({preventScroll:true});
}
function openMetadataMenu(trigger, path, choices, initial = null) {
  if (metadataMenu?.trigger === trigger) { closeMetadataMenu(true); return; }
  closeMetadataMenu();
  const popup = document.createElement('div'), events = new AbortController(), signal = events.signal;
  popup.className = 'metadata-options'; popup.id = 'metadata-options-' + path;
  popup.setAttribute('role','listbox'); popup.setAttribute('aria-label',trigger.getAttribute('aria-label'));
  const current = editor.read()[path];
  popup.innerHTML = choices.map(([value,label],i) => '<button type="button" role="option" tabindex="-1" aria-selected="' + (value === current) + '" data-choice="' + i + '"><span>' + esc(label) + '</span><span aria-hidden="true">' + (value === current ? '✓' : '') + '</span></button>').join('');
  document.body.append(popup); trigger.setAttribute('aria-expanded','true');
  metadataMenu = {trigger,popup,events};
  const bounds = trigger.getBoundingClientRect();
  popup.style.minWidth = bounds.width + 'px';
  popup.style.left = Math.max(8,Math.min(bounds.left,innerWidth - popup.offsetWidth - 8)) + 'px';
  popup.style.top = (bounds.bottom + popup.offsetHeight + 8 <= innerHeight ? bounds.bottom + 4 : Math.max(8,bounds.top - popup.offsetHeight - 4)) + 'px';
  const controls = [...popup.querySelectorAll('button')], selected = Math.max(0,choices.findIndex(([key]) => key === current));
  controls[initial === 'last' ? controls.length - 1 : initial === 'first' ? 0 : selected].focus({preventScroll:true});
  popup.addEventListener('click', event => {
    const option = event.target.closest('[data-choice]'); if (!option) return;
    const value = choices[Number(option.dataset.choice)][0]; closeMetadataMenu();
    if (current !== value) editor.change(path,value);
    $('[data-metadata="' + path + '"]').focus({preventScroll:true});
  },{signal});
  popup.addEventListener('keydown',event => {
    const at = controls.indexOf(document.activeElement); let next = at;
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeMetadataMenu(true); return; }
    if (event.key === 'Tab') { closeMetadataMenu(true); return; }
    if (event.key === 'ArrowDown') next = (at + 1) % controls.length;
    else if (event.key === 'ArrowUp') next = (at - 1 + controls.length) % controls.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = controls.length - 1;
    else if (!event.isComposing && event.key.length === 1 && event.key !== ' ') {
      const found = choices.findIndex(([,label]) => label.toLowerCase().startsWith(event.key.toLowerCase()));
      if (found < 0) return; next = found;
    } else return;
    event.preventDefault(); controls[next].focus({preventScroll:true});
  },{signal});
  document.addEventListener('pointerdown',event => { if (!popup.contains(event.target) && !trigger.contains(event.target)) closeMetadataMenu(); },{capture:true,signal});
  document.addEventListener('focusin',event => { if (!popup.contains(event.target) && !trigger.contains(event.target)) closeMetadataMenu(); },{signal});
  window.addEventListener('resize',() => closeMetadataMenu(true),{signal});
  $('#edit-scroll').addEventListener('scroll',() => closeMetadataMenu(true),{signal});
}
function metadataControls() {
  closeMetadataMenu();
  for (const [path, choices] of Object.entries({language:LANGUAGES,audience:AUDIENCES})) {
    const select = $('[data-bind="' + path + '"]'), value = editor.read()[path], selected = choices.find(([key]) => key === value);
    const trigger = document.createElement('button');
    trigger.type = 'button'; trigger.className = 'metadata-select'; trigger.dataset.metadata = path;
    trigger.id = 'metadata-' + path; trigger.setAttribute('role','combobox');
    trigger.setAttribute('aria-haspopup','listbox'); trigger.setAttribute('aria-expanded','false');
    trigger.setAttribute('aria-controls','metadata-options-' + path); trigger.setAttribute('aria-label',select.getAttribute('aria-label'));
    trigger.innerHTML = '<span>' + esc(selected?.[1] || (value ? '旧值：' + value : '')) + '</span><span aria-hidden="true">⌄</span>';
    select.replaceWith(trigger);
    trigger.addEventListener('click',() => openMetadataMenu(trigger,path,choices));
    trigger.addEventListener('keydown',event => {
      if (['ArrowDown','ArrowUp','Home','End'].includes(event.key)) {
        event.preventDefault(); openMetadataMenu(trigger,path,choices,event.key === 'End' ? 'last' : event.key === 'Home' ? 'first' : null);
      }
    });
  }
  const manage = $('[data-action="tags"]');
  delete manage.dataset.action; manage.dataset.b1 = 'tags'; manage.id = 'manage-tags';
  const meta = editor.read();
  $('#panel-basic .tags').innerHTML = meta.tags.map((tag,index) => '<span class="tag' + (tagIssue(tag) ? ' metadata-invalid' : '') + '">' + esc(tagIssue(tag) ? '待修正标签 ' + (index + 1) : tag) + '</span>').join('');
  const issues = metadataIssues(meta);
  if (issues.length) manage.closest('.field').insertAdjacentHTML('beforeend','<div class="metadata-warning" role="status"><strong>旧资料已保留，保存前请修正</strong>' + [...new Set(issues.map(issue => METADATA_MESSAGES[issue.code]))].map(text => '<p>' + esc(text) + '</p>').join('') + '</div>');
}
function tagRules(onConfirm = null) {
  const returnTo = $('#tag-rules') || $('#manage-tags'), dialog = document.createElement('dialog');
  let confirmed = false;
  dialog.className = 'tag-rules-dialog'; dialog.setAttribute('aria-labelledby','tag-rules-title');
  dialog.innerHTML = '<header><h2 id="tag-rules-title">标签规则</h2><button type="button" class="icon-button" aria-label="关闭标签规则">×</button></header>' +
    '<div class="tag-rules-content"><p>使用清晰的题材和玩法分类，方便查找与复用。</p><ol>' +
    '<li>最多选择 ' + TAG_LIMIT + ' 个标签。复合含义尽量拆为多个标签，已有同义标签优先复用。</li>' +
    '<li>尽量使用 10 字以内的简短标签。本地硬上限为 ' + TAG_CHAR_LIMIT + ' 个 Unicode 字符，只接受文字、数字和空格。不接受表情、控制字符和标点。</li>' +
    '<li>不收录不当 NSFW 标签。创建、选择、保存与导入均执行同一套本地规则。</li>' +
    '<li>搜索框内输入名称，再点“创建新标签”。新增标签自动选中；保存标签后，还需保存作品草稿。</li>' +
    '<li>取消、关闭或 Esc 放弃本次标签编辑；规则窗口关闭后回到当前编辑。</li></ol>' +
    '<p class="hint">标签库由安全预置分类和本地已保存作品组成，不使用原站热度。过滤采用明确词表，不保证识别所有隐喻。</p></div><footer><button type="button" class="primary">确认</button></footer>';
  document.body.append(dialog);
  dialog.querySelector('header button').onclick = () => dialog.close();
  dialog.querySelector('footer button').onclick = () => {
    confirmed = true; tagRulesSeen = true;
    try { localStorage.setItem(tagRulesPreference,'seen'); } catch { /* In-memory acknowledgement still works. */ }
    dialog.close();
  };
  dialog.addEventListener('click',event => { if (event.target === dialog) { const b=dialog.getBoundingClientRect(); if(event.clientX<b.left||event.clientX>b.right||event.clientY<b.top||event.clientY>b.bottom)dialog.close(); } });
  dialog.addEventListener('close',() => { dialog.remove(); returnTo?.focus({preventScroll:true}); if (confirmed && onConfirm) run(onConfirm); },{once:true});
  dialog.showModal();
}
async function manageTags() {
  writable();
  if (!tagRulesSeen) { tagRules(manageTags); return; }
  if (tagManagerLoading) return;
  const currentId = work.id, trigger = $('#manage-tags');
  tagManagerLoading = true; trigger.disabled = true;
  let listing;
  try { listing = await api('/state'); }
  finally { tagManagerLoading = false; if (trigger.isConnected) trigger.disabled = false; }
  if (work?.id !== currentId || busy || !trigger.isConnected || $('#dialog').open) return;
  let selected = clone(editor.read().tags), available = tagCatalog([...listing.works.flatMap(item => item.tags || []),...selected]), query = '';
  editor.openDialog('管理标签','<div class="tag-manager"><div class="tag-search-row"><label class="tag-search"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="10" cy="10" r="7"></circle><path d="M15 15l6 6"></path></svg><input type="text" id="tag-search" aria-label="搜索或者创建标签" placeholder="搜索或者创建" autocomplete="off"></label><button type="button" class="primary" id="tag-create">创建新标签</button><button type="button" id="tag-rules">标签规则</button></div><p id="tag-error" class="danger" role="alert" hidden></p><section class="tag-selection"><h3 id="tag-selected-count"></h3><div id="tag-selected" class="tag-pills"></div></section><section class="tag-catalog"><h3 id="tag-all-count"></h3><div id="tag-all" class="tag-pills"></div></section><p class="hint tag-local-note">标签随作品草稿保存；取消不保留本次编辑。</p></div>',() => {
    const issue = metadataIssues({...editor.read(),tags:selected}).find(item => item.path.startsWith('/metadata/tags'));
    if (issue) { showError(issue.code); return false; }
    editor.closeDialog();
    if (JSON.stringify(selected) !== JSON.stringify(editor.read().tags)) editor.change('tags',clone(selected));
    $('#manage-tags').focus({preventScroll:true}); return false;
  });
  function showError(code) { $('#tag-error').hidden=false; $('#tag-error').textContent=METADATA_MESSAGES[code]; }
  function render() {
    $('#tag-selected-count').textContent = '已选择标签 (' + selected.length + '/' + TAG_LIMIT + ')';
    $('#tag-selected').innerHTML = selected.length ? selected.map((tag,index) => '<button type="button" data-remove-tag="' + index + '" class="selected-tag' + (tagIssue(tag) ? ' metadata-invalid' : '') + '" aria-label="移除' + esc(tagIssue(tag) ? '待修正标签 ' + (index+1) : tag) + '">' + esc(tagIssue(tag) ? '待修正标签 ' + (index+1) : tag) + '<span aria-hidden="true">×</span></button>').join('') : '<span class="hint">暂无</span>';
    const found = available.filter(tag => tagKey(tag).includes(tagKey(query)));
    $('#tag-all-count').textContent = '全部标签 (' + found.length + ')';
    $('#tag-all').innerHTML = found.length ? found.map(tag => '<button type="button" data-pick-tag="' + available.indexOf(tag) + '" aria-pressed="' + selected.some(item => tagKey(item) === tagKey(tag)) + '">' + esc(tag) + '</button>').join('') : '<span class="hint">暂无匹配标签，可在上方输入名称后创建。</span>';
  }
  $('#tag-search').addEventListener('input',event => { query=event.target.value; $('#tag-error').hidden=true; render(); });
  $('#tag-search').addEventListener('keydown',event => { if (event.key === 'Enter') { event.preventDefault(); if (!event.isComposing && !composing) createTag(); } });
  function createTag() {
    const value = $('#tag-search').value.trim(), issue = tagIssue(value);
    if (issue) { showError(issue); return; }
    if (available.some(tag => tagKey(tag) === tagKey(value))) { showError('AUTHORING_TAG_DUPLICATE'); return; }
    if (selected.length >= TAG_LIMIT) { showError('AUTHORING_TAG_LIMIT'); return; }
    available.push(value); selected.push(value); $('#tag-error').hidden=true; render(); $('#tag-search').focus();
  }
  $('#tag-create').onclick = createTag;
  $('#tag-rules').onclick = () => tagRules();
  $('.tag-manager').onclick = event => {
    const remove = event.target.closest('[data-remove-tag]'), pick = event.target.closest('[data-pick-tag]');
    if (!remove && !pick) return;
    let focusIndex;
    if (remove) { focusIndex=Number(remove.dataset.removeTag); selected.splice(focusIndex,1); }
    else {
      const tag=available[Number(pick.dataset.pickTag)], at=selected.findIndex(item => tagKey(item) === tagKey(tag));
      if(at>=0)selected.splice(at,1);
      else { if(selected.length>=TAG_LIMIT){showError('AUTHORING_TAG_LIMIT');return;} selected.push(tag); }
    }
    $('#tag-error').hidden=true; render();
    if(pick) $('[data-pick-tag="'+pick.dataset.pickTag+'"]').focus({preventScroll:true});
    else ($('#tag-selected button:nth-child(' + Math.min(focusIndex+1,selected.length) + ')') || $('#tag-search')).focus({preventScroll:true});
  };
  render(); $('#tag-search').focus();
}
$('#dialog').addEventListener('click',event => {
  if (event.target !== $('#dialog') || !$('.tag-manager')) return;
  const b=event.target.getBoundingClientRect();
  if(event.clientX<b.left||event.clientX>b.right||event.clientY<b.top||event.clientY>b.bottom)editor.closeDialog();
});

function renderWorld() { worldUI.render($("#panel-world")); }
function renderOther() {
  document.querySelector('.media-music audio')?.pause();
  $('#panel-other').innerHTML = media.field('背景图','desktop') + media.field('移动设备背景图','mobile') + media.music() +
    '<div id="blocked-editor"></div><div id="regex-editor"></div><div id="cg-editor"></div>'+editor.editor('css','内置 CSS','在回复隔离预览中应用；不会影响编辑界面或开局页面。源码原样保存。') +
    '<details class="help-disclosure"><summary>使用原站样式或外链图片</summary><p>回复支持 Markdown、HTML 和静态 SVG。导入原有样式时，可在配置 JSON 的 settings.presentation 中选择作用域，并把外部图片地址映射到已上传的资源。</p><a href="/creator-guide.html#assets" target="_blank" rel="noopener">查看资源引用示例</a></details>' +
    '<div id="dialogue-editor"></div><div id="suggestions-editor"></div><div class="field"><h3>完整作品配置</h3><p class="hint">需要调整兼容选项时，可在这里编辑配置 JSON。剧情、摘要与记忆模型在右侧模型设置中分别选择。</p>' + button('查看 / 编辑完整配置 JSON','json') + '</div>';
  editor.bindEditors(); media.bindFields(); dialogueUI.render($('#dialogue-editor'));assistanceUI.renderBlocked($('#blocked-editor'));assistanceUI.renderSuggestions($('#suggestions-editor'));regexUI.render($('#regex-editor'));cgUI.render($('#cg-editor'));
}
async function loadWork(id, preserved = null) {
  applyWork(await api('/works/' + id),preserved);
}
function applyWork(next, preserved = null) {
  chatBGM.destroy();
  clearInputCheck();
  const id = next.id;
  work = next; validation = null; conflict = false;
  modelUI?.workChanged();
  const doc = preserved || work.draft;
  editor.load(toView(doc), images());
  if (preserved) editor.dirty(); else editor.saved(savedText(work.revision));
  const url = new URL(location.href); url.searchParams.set('work',id); history.replaceState(null,'',url);
  sessionPanel?.workChanged();
  playbackPanel?.workChanged();
  if (work.pending && !pending) {
    pending = {...work.pending, document:doc, sourceId:id, serverOnly:true};
    notice('本地库有待核对操作，请在“更多”中核对原操作。');
  }
  setBusy(false);
}
function writable() {
  if (busy || pending) throw {code:'AUTHORING_OPERATION_PENDING'};
  if (conflict) throw {code:'AUTHORING_REVISION_CONFLICT'};
}
async function mutate(path, fields, {creating=false, follow=false, preserve=false, keepEditors=false} = {}) {
  writable();
  const body = {operationId:crypto.randomUUID(),expectedRevision:creating ? null : work.revision,...fields};
  const captured = currentDocument();
  const record = {operationId:body.operationId,workId:creating || follow ? null : work.id,sourceId:work?.id || null,document:fields.document || captured,revision:work?.revision ?? null,kind:path,at:new Date().toISOString()};
  setBusy(true);
  let finishDraftWrite;
  draftWriteSettled=new Promise(resolve=>{finishDraftWrite=resolve;});
  try {
    // Persist intent and unsaved source before any request is dispatched.
    await journal('put',record); pending = record;
    $('#save-status').textContent = '正在保存到本地，请等待回执…';
    const receipt = await api(path,body);
    if (receipt.operationId !== body.operationId || typeof receipt.workId !== 'string' || !Number.isInteger(receipt.revision)) throw {code:'NETWORK_RESULT_UNKNOWN'};
    const next = await api('/works/' + receipt.workId);
    await journal('delete',record.operationId); pending = null;
    if (next.revision !== receipt.revision) {
      conflict = true; throw {code:'AUTHORING_REVISION_CONFLICT'};
    }
    if (preserve && work?.id === next.id) {
      work = next; validation = null; editor.media(images(),{refresh:!keepEditors});
      const audio = work.assets.find(a => a.alias === editor.read().bgm?.alias);
      if (audio&&!keepEditors) editor.audio({name:audio.fileName,alias:audio.alias,url:assetUrl(audio)});
      if (editor.isDirty()) notice('资源已保存；正文仍有未保存修改。');
      else editor.saved(savedText(work.revision));
    } else if (work?.id === next.id && (path.endsWith('/save') || path.endsWith('/publish'))) {
      work = next; validation = null; editor.saved(savedText(work.revision));
    } else applyWork(next);
    syncChatBGM(false); // Asset replacement may change the track hash without a text edit.
    return receipt;
  } catch (error) {
    const definite = error.code === 'AUTHORING_WRITE_FAILED' || (error.httpStatus >= 400 && error.httpStatus < 500 && !['AUTHORING_RESULT_UNKNOWN','AUTHORING_OPERATION_PENDING'].includes(error.code));
    if (definite) {
      await journal('delete',record.operationId); pending = null;
      if (error.code === 'AUTHORING_REVISION_CONFLICT') conflict = true;
    }
    if (error.code === 'AUTHORING_OPERATION_PENDING' && error.details?.operationId) {
      const original = {...record,...error.details,serverOnly:true};
      if (error.details.scope === 'gallery') await journal('delete',record.operationId);
      if (error.details.scope === 'gallery') { pending = null; media.client.hydrate([original]); await journal('put',original); }
      else { pending = blockedWorkRecord(record,error.details); await journal('put',pending); }
    }
    throw error;
  } finally {
    draftWriteSettled=null;
    finishDraftWrite();
    setBusy(false);
  }
}
async function save(automatic=false) {
  if (automatic && (busy || pending || conflict || composing || !work || $('dialog[open]') || dialogueUI.editing())) return;
  if (!work) return;
  if(dialogueUI.editing()){notice('请先保存或取消页内开场白编辑，再保存作品。');return;}
  if (!editor.isDirty()) { notice('当前草稿已经保存。'); return; }
  const next = currentDocument(), issue = metadataIssues(next.metadata)[0];
  if (issue) throw issue;
  await mutate('/works/' + work.id + '/save',{document:next});
  notice((automatic ? '已自动保存' : '已保存') + (creatorMode ? '' : ' · revision ' + work.revision));
}
function guardSwitch(action) {
  writable();
  if (!editor.isDirty()) return run(action);
  editor.openDialog('有未保存修改','<p>切换将放弃本页未保存修改，服务器作品保持不变。</p>',() => {
    editor.closeDialog(); run(action); return false;
  },'放弃修改并继续','取消');
}
function download(value, name) {
  const blob = new Blob([JSON.stringify(value,null,2)],{type:'application/json'});
  const url = URL.createObjectURL(blob), anchor = document.createElement('a');
  anchor.href = url; anchor.download = name; anchor.click(); setTimeout(() => URL.revokeObjectURL(url),30000);
}
async function backup() {
  if (!work) return;
  const config = await api('/configurations/prepare',{document:JSON.stringify(currentDocument()),assets:work.assets});
  download(config,'authoring-unsaved-configuration.json');
  notice('已导出本页未保存配置；资源需仍在本地库，完整资源包另行导出。');
}

async function works() {
  const listing = await api('/state');
  editor.openDialog('作品管理','<p class="hint">打开已保存的作品继续编辑，或从空白作品开始。</p><div class="row">' +
    button('＋ 空白作品','new-work','class="primary"') + button('导入作品','import') + '</div><div class="stack">' +
    (listing.works.length ? listing.works.map(item => '<div class="validation-row"><strong>' + esc(item.name || '未命名作品') + '</strong><p class="hint">' + item.assets + ' 个资源 · ' + item.versions + ' 个版本</p>' + button(item.id === work?.id ? '当前作品' : '打开','open-work','data-id="' + item.id + '"') + '</div>').join('') : '<p class="hint">还没有作品，从空白作品开始。</p>') + '</div>',null,undefined,'关闭');
}
function newWork() {
  guardSwitch(() => {
    editor.openDialog('空白作品',field('作品名称','new-name','','maxlength="120"') + '<p class="hint">可先保存未完成的草稿，填写主提示词和面向群体后再发布本地版本。</p>',() => {
      const doc = clone(blank); doc.metadata.name = $('#new-name').value;
      run(async () => { await mutate('/works',{document:doc},{creating:true}); editor.closeDialog(); });
      return false;
    },'创建','取消');
  });
}
function copyWork() {
  if (!work) return;
  if (editor.isDirty()) return notice('请先保存当前修改，再复制作品。');
  editor.openDialog('复制作品',field('副本名称','copy-name',work.draft.metadata.name + ' 副本','maxlength="120"') +
    '<p class="hint">复制草稿和资源。不会复制版本历史、会话、插件授权或额度。</p>',() => {
      const name = $('#copy-name').value;
      run(async () => { await mutate('/works/' + work.id + '/copy',{name},{follow:true}); editor.closeDialog(); });
      return false;
    },'创建副本','取消');
}
async function exportWork(kind='configuration',version=null) {
  if (!work) return;
  const result = await api('/works/' + work.id + '/export?kind=' + kind + (version ? '&version=' + version : ''));
  download(result,'authoring-' + kind + (version ? '-' + version.slice(8,20) : '') + '.json');
  notice('已导出' + (version ? '封存版本' : '服务器已保存草稿') + (editor.isDirty() ? '；未保存修改未包含。' : '。'));
}
async function showJson() {
  if (!work) return;
  const target = work.id;
  editor.openDialog('完整作品配置 JSON','<p class="hint">编辑 document 的全部字段；资源通过图库管理，能力声明由宿主计算。配置包可通过“导入作品”往返。预检并确认后才替换当前草稿，取消不保存。</p><textarea id="config-json" rows="18" aria-label="完整配置 JSON" spellcheck="false">' +
    esc(JSON.stringify(currentDocument(),null,2)) + '</textarea>',() => {
      const text = $('#config-json').value, marker = $('#config-json'), media = clone(work.assets);
      run(async () => {
        const config = await api('/configurations/prepare',{document:text,assets:media});
        if (!marker.isConnected || !$('#dialog').open || work?.id !== target) return;
        await inspectImport(config,target);
      }); return false;
    },'预检修改','取消');
}
function importDialog() {
  editor.openDialog('导入作品','<p>配置 JSON 引用已有资源；完整作品包包含媒体字节。未知字段或资源问题会在写入前显示。</p>' +
    '<input id="import-file" type="file" accept="application/json,.json" aria-label="选择作品 JSON"><label class="form-label" for="import-source">或粘贴 JSON</label><textarea id="import-source" rows="9" aria-label="导入 JSON"></textarea>' +
    '<label class="form-label" for="import-target">导入到</label><select id="import-target"><option value="">新作品</option>' +
    (work ? '<option value="' + work.id + '">替换当前草稿（保留已封存版本）</option>' : '') + '</select>',() => {
      const payload = $('#import-source').value, target = $('#import-target').value || null;
      if (!payload.trim()) { notice('请选择文件或粘贴 JSON。'); return false; }
      run(() => inspectImport(payload,target)); return false;
    },'预检','取消');
  $('#import-file').onchange = event => run(async () => {
    const file = event.target.files[0]; if (!file) return;
    if (file.size > 128 * 1024 * 1024) throw {code:'AUTHORING_PACKAGE_SIZE'};
    $('#import-source').value = await file.text();
  });
}
function issueList(items) {
  return '<ul>' + items.map(item => '<li>' + esc((failureText[item.code] || item.code) + (item.path ? ' · ' + item.path : '')) + '</li>').join('') + '</ul>';
}
async function inspectImport(payload,target) {
  writable();
  const marker = $('#dialog-body').firstElementChild, revision = work?.revision ?? null;
  const report = await api('/imports/inspect',{payload});
  if (!$('#dialog').open || $('#dialog-body').firstElementChild !== marker) return;
  if (!report.valid) {
    const area = $('#dialog-body');
    area.querySelector('[data-import-errors]')?.remove();
    area.insertAdjacentHTML('afterbegin','<div class="validation-row danger" data-import-errors role="alert"><strong>未写入：导入校验失败</strong>' + issueList(report.issues) + '</div>');
    return;
  }
  editor.openDialog('确认导入','<p><strong>' + esc(report.name || '未命名作品') + '</strong> · ' + report.assets + ' 个资源</p><p>' +
    (target ? '替换当前草稿，已有版本保留。' : '创建一个新作品。') + '</p>' +
    (editor.isDirty() ? '<p class="danger">此操作会离开当前未保存修改，请确认已备份或准备放弃。</p>' : '') +
    (report.publicationIssues.length ? '<p class="hint">允许作为未完成草稿导入；封存前仍需修复：</p>' + issueList(report.publicationIssues) : '<p class="hint">结构和资源预检通过。</p>') +
    '<details><summary>预检标识</summary><p class="hint">' + report.payloadHash + '</p></details>',() => {
      run(async () => {
        if (target && (work?.id !== target || work.revision !== revision)) throw {code:'AUTHORING_IMPORT_PREVIEW_CHANGED'};
        await mutate('/imports',{workId:target,payload,expectedImportHash:report.payloadHash},{creating:target === null});
        editor.closeDialog(); notice('导入已保存，未触发模型调用。');
      }); return false;
    },'确认导入','取消');
}
async function versions() {
  if (!work) return;
  const current = await api('/works/' + work.id);
  validation = await api('/works/' + work.id + '/validation');
  editor.openDialog(creatorMode?'作品版本':'版本与本地封存',(creatorMode?'<p>发布本地版本后，可以选择它开始新会话。已有会话保留原来的设定，后续草稿修改不会影响它们。</p>':'<p>版本保存不可变的配置与资源。<strong>当前仅封存数据，尚不可游玩。</strong>通用运行能力在 B6 接入。</p>') +
    (editor.isDirty() ? '<p class="danger">请先保存未保存修改。</p>' : '') +
    (!validation.valid ? '<div class="validation-row"><strong>请先修复以下项目</strong>' + issueList(validation.issues) + '</div>' : '') +
    button(creatorMode?'发布当前草稿':'封存当前已保存草稿','seal','class="primary"' + ((!validation.valid || editor.isDirty() || conflict || pending) ? ' disabled' : '')) +
    '<h3>已发布版本</h3><div class="stack version-list">' +
    (current.versions.length ? current.versions.map(v => '<div class="validation-row"><strong>版本 ' + v.number + '</strong><p class="hint">' + esc(v.createdAt.replace('T',' ').slice(0,16)) + ' UTC</p>' + button('导出版本配置','export-version','data-version="' + v.id + '"') + button('导出版本完整包','package-version','data-version="' + v.id + '"') + '<details><summary>版本校验信息</summary><p class="hint">来源 revision ' + v.sourceRevision + '<br>' + v.sha256 + '</p></details></div>').join('') : '<p class="hint">还没有发布版本。完成校验后，发布第一版即可开始新会话。</p>') + '</div>',null,undefined,'关闭');
}
async function seal() {
  writable();
  if (editor.isDirty() || !validation?.valid || validation.revision !== work.revision) throw {code:'AUTHORING_REVISION_CONFLICT'};
  const revision = work.revision;
  editor.openDialog(creatorMode?'发布本地版本':'确认封存版本',creatorMode?'<p>将当前已保存的草稿与资源发布为固定版本。仅保存在本地，不会公开到外部网站，也不会调用模型。</p>':'<p>将当前 revision ' + revision + ' 封存为不可变数据快照。不会创建会话、游玩入口或调用模型。</p>',() => {
    run(async () => {
      if (work.revision !== revision) throw {code:'AUTHORING_REVISION_CONFLICT'};
      await mutate('/works/' + work.id + '/publish',{});
      await versions(); notice(creatorMode?'已发布本地版本，可在模型设置中开始新会话。':'版本数据已封存，运行能力尚未接入。');
    }); return false;
  },creatorMode?'发布版本':'确认封存','取消');
}
function workResources(target=null,audio=false) {
  if (!work) return;
  uploadTarget = target;
  const listed = target ? work.assets.filter(a => audio ? a.mediaType === 'audio/mpeg' : a.mediaType.startsWith('image/')) : work.assets;
  editor.openDialog(target ? '选择资源' : '创作图库','<p class="hint">资源保存到本地库。源码通过 asset://别名 引用；替换别名不会改变已封存版本。</p>' +
    '<div class="gallery">' + listed.map(a => '<div class="asset-meta">' +
      (a.mediaType.startsWith('image/') ? '<img class="sample-image" src="' + assetUrl(a) + '" alt="' + esc(a.fileName) + '">' : '<audio controls src="' + assetUrl(a) + '"></audio>') +
      '<p>' + esc(a.fileName) + '</p><small>asset://' + esc(a.alias) + ' · ' + Math.ceil(a.size/1024) + ' KiB</small><div class="row">' +
      (target ? button('使用','use-asset','data-alias="' + a.alias + '"') : '') +
      button('详情','asset-info','data-alias="' + a.alias + '"') + '</div></div>').join('') + '</div>' +
    (!listed.length ? '<p class="hint">暂无资源，可先上传。</p>' : '') +
    '<label class="form-label" for="upload-asset">上传' + (audio ? ' MP3' : '图片或 MP3') + '</label><input id="upload-asset" type="file" accept="' + (audio ? 'audio/mpeg' : 'image/png,image/jpeg,image/webp,image/gif,audio/mpeg') + '">',null,undefined,'关闭');
  $('#upload-asset').onchange = event => { const file=event.target.files[0]; if(file)run(() => prepareUpload(file,target)); };
}
function fileBase64(file) {
  return new Promise((ok,no) => { const reader = new FileReader(); reader.onerror = () => no({code:'AUTHORING_FILE_READ_FAILED'}); reader.onload = () => ok(String(reader.result).split(',')[1]); reader.readAsDataURL(file); });
}
async function prepareUpload(file,target=null,replaceAlias=null) {
  writable();
  const mediaType = file.type || (file.name.toLowerCase().endsWith('.mp3') ? 'audio/mpeg' : '');
  const audio = mediaType === 'audio/mpeg';
  if (!['image/png','image/jpeg','image/webp','image/gif','audio/mpeg'].includes(mediaType)) throw {code:'AUTHORING_MEDIA_TYPE'};
  if (file.size > (audio ? 10 : 5) * 1024 * 1024) throw {code:'AUTHORING_ASSET_SIZE'};
  if (target && target !== 'gallery' && ((target === 'bgm') !== audio)) throw {code:'AUTHORING_MEDIA_TYPE'};
  const base64 = await fileBase64(file);
  editor.openDialog(replaceAlias ? '替换资源' : '上传资源', '<p>' + esc(file.name) + ' · ' + Math.ceil(file.size/1024) + ' KiB</p>' +
    field('资源别名','asset-alias',replaceAlias || 'asset-' + crypto.randomUUID().slice(0,8),'maxlength="80"' + (replaceAlias ? ' readonly' : '')) +
    '<p class="hint">小写字母开头，可用小写字母、数字、点、横线和下划线。同别名替换需再次确认；旧版本不变。</p>',() => {
      const alias = $('#asset-alias').value;
      if (!/^[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)*$/.test(alias)) { notice('请填写有效的资源别名。'); return false; }
      const execute = async () => {
        await mutate('/works/' + work.id + '/assets',{alias,fileName:file.name,mediaType,base64},{preserve:true});
        if (target && target !== 'gallery') useAsset(alias,target);
        else { editor.closeDialog(); workResources(); }
      };
      if (!replaceAlias && work.assets.some(a => a.alias === alias)) {
        editor.openDialog('确认替换同名资源','<p>别名 ' + esc(alias) + ' 已存在。当前草稿将引用新文件，旧版本继续引用原文件。</p>',() => { run(execute); return false; },'替换','取消');
      } else run(execute);
      return false;
    },replaceAlias ? '确认替换' : '上传','取消');
}
async function uploadEditorImage(file) {
  writable();
  if(!work)throw {code:'AUTHORING_WORK_NOT_FOUND'};
  const target=work.id;
  if(!['image/png','image/jpeg','image/webp','image/gif'].includes(file.type))throw {code:'AUTHORING_MEDIA_TYPE'};
  if(file.size>5*1024*1024)throw {code:'AUTHORING_ASSET_SIZE'};
  const base64=await fileBase64(file);
  if(work?.id!==target)throw {code:'AUTHORING_REVISION_CONFLICT'};
  const alias='image-'+crypto.randomUUID();
  await mutate('/works/'+target+'/assets',{alias,fileName:file.name,mediaType:file.type,base64},{preserve:true,keepEditors:true});
  return {alias,label:file.name.replace(/[\r\n]/g,' ')};
}

function useAsset(alias,target=uploadTarget) {
  const asset = work.assets.find(a => a.alias === alias);
  if (!asset || !target) return;
  if (target?.kind === 'cg') { cgUI.receive(target,alias); return; }
  if (target === 'bgm') {
    if(typeof editor.read().bgmAutoplay!=='boolean')editor.change('bgmAutoplay',true);
    editor.change('bgm',{name:asset.fileName,alias,url:assetUrl(asset)});
  }
  else editor.change(target,alias);
  editor.closeDialog(); notice('资源已选用，请保存草稿使引用生效。');
}
function assetInfo(alias) {
  const asset = work.assets.find(a => a.alias === alias); if (!asset) return;
  editor.openDialog('资源详情','<p><strong>' + esc(asset.fileName) + '</strong></p><p>asset://' + esc(alias) + ' · ' + asset.size + ' bytes</p>' +
    '<details><summary>SHA-256</summary><p class="hint">' + asset.sha256 + '</p></details>' +
    '<p class="hint">替换或移除只影响当前草稿资源清单；缺失引用会阻止版本封存。</p>' +
    '<label class="form-label" for="replace-asset">替换文件</label><input id="replace-asset" type="file" accept="' + asset.mediaType + '">' +
    button('从当前资源清单移除','remove-asset','class="danger" data-alias="' + alias + '"'),null,undefined,'关闭');
  $('#replace-asset').onchange = event => { const file=event.target.files[0]; if(file)run(() => prepareUpload(file,null,alias)); };
}
function removeAsset(alias) {
  editor.openDialog('移除资源别名','<p>移除 ' + esc(alias) + ' 后，引用它的草稿字段不会被自动改写。请在封存前修复缺失引用。旧版本不变。</p>',() => {
    run(async () => { await mutate('/works/' + work.id + '/assets/remove',{alias},{preserve:true}); workResources(); }); return false;
  },'确认移除','取消');
}

async function recover() {
  if (busy) return;
  if (!pending) {
    const records = await journal('read');
    pending = records.find(item => item.scope !== 'gallery') || work?.pending;
    if (!pending && media.pending) { editor.closeDialog(); await media.open(); return; }
    if (!pending) { notice('没有待核对操作。'); return; }
  }
  const record = clone(pending);
  setBusy(true);
  try {
    const previousWorkId = work?.id || null;
    const local = currentDocument();
    const result = await resolveWorkRecovery({record,currentWorkId:previousWorkId,api});
    if (result.status === 'not_found') {
      notice('原操作尚无可确认回执，仍保留编号和草稿；不会新建操作重发。');
      editor.openDialog('仍待核对','<p>未找到原操作回执，不等同于已经失败。请保留记录，稍后再次核对。</p><p class="hint">' + esc(recoveryTarget(record).operationId) + '</p>',null,undefined,'关闭');
      return;
    }
    if (!['committed','not_committed'].includes(result.status)) throw {code:'NETWORK_RESULT_UNKNOWN'};
    await journal('delete',record.operationId); pending = null;
    if (result.status === 'committed' && result.view === 'stay') {
      if (work) work.pending = null;
      notice('原操作已核对；仍停留在当前作品，未保存草稿已保留。');
    } else if (result.status === 'committed') {
      const target = result.receipt.workId;
      applyWork(result.work);
      if (local && previousWorkId === target && !documentsEqual(local,work.draft)) {
        editor.load(toView(local),images()); editor.dirty();
        notice('原操作已提交；本页未保存草稿已保留，请核对后保存。');
      } else notice('原操作已提交并恢复；没有重发。');
    } else notice('已确认原操作未提交，草稿保留。可核对后明确再次保存。');
    editor.closeDialog();
  } finally { setBusy(false); }
}
function reloadServer() {
  if (!work || pending || busy) return notice('请先核对待确认操作。');
  editor.openDialog('载入服务器版本','<p>将放弃本页未保存修改，并载入服务器最新 revision。建议先导出本页草稿。</p>',() => {
    run(async () => { await loadWork(work.id); editor.closeDialog(); }); return false;
  },'放弃本页修改并载入','取消');
}
function scope() {
  if(creatorMode)return editor.openDialog('功能与帮助','<p>保存作品、编辑源码和资源，使用预览检查开局与回复样式。</p><p>'+ (document.body.dataset.execution==='controlled'?'点击发送才调用所选模型；保存、预览与发布不调用模型。':'当前入口为离线演练，发送使用固定样例。') +'模型配置不会自动启用插件。</p><p><a href="/creator-guide.html" target="_blank" rel="noopener">打开创作指南</a></p><details><summary>后续扩展</summary><p>特殊 CG 收藏、变量状态面板和并行模型对照将分别立项。</p></details>',null,undefined,'关闭');
  editor.openDialog('本批能力','<p>已接线：标签管理、五种语言、男性向／女性向、空白作品、草稿保存与刷新恢复、资源清单、分组图库与媒体选用、配置 / 完整包导入导出、作品复制、不可变版本数据封存、原操作核对。</p>' +
    '<p>图库删图/删组尚待原站交互核验，本批没有删除入口。聊天音乐自动播放在 B5 接入；媒体字段只试听。</p>' +
    '<p>B3 世界书编辑已验收。B4-B 在隔离展示上接入作者脚本引擎、DOM 与表单事件；脚本和声明式桥接均只提出开局候选，由你采用或取消。</p>' +
    '<p>B4-D 回复支持 Markdown / HTML / 静态 SVG、可选原站 CSS 作用域和精确 URL 到本地资源映射。图片映射使用既有配置 JSON 与资源清单。</p>' +
    '<p>B4-E 两卡完整开局源码与输出回放已验收。B5-A 支持页内开场白列表、预设对话与快捷指令编辑、保存和离线预览；点击选择只填入玩家输入。</p>' +
    '<p>开场白最多40项，单项最多200000个Unicode字符；预设最多10组，每组最多100对对话与100个开场白。后两项数量为本地容量约定。预设标题30字、提问1000字、回答10000字、预设开场白50字；快捷指令最多10项，命令15字、内容100字。旧超长快捷指令不截断，确认和封存前需人工修正。</p>' +
    '<p>B5-C 支持正则编辑、角色范围、排序、隔离执行和整批失败回退，始终只作用于显示副本。回复回放可选择 AI 或用户原文；所有生成和历史写入尚未接入。</p><p>B5-B 支持建议开关和屏蔽词编辑、保存恢复与离线核对。回复回放从展示副本提取建议，点击只填入输入框；“检查输入”按区分大小写的字面规则匹配当前原始输入，不调用模型。</p>' +
    '<p class="hint">回复脚本禁止执行。B5-D-C支持普通CG配置、分类、资源编辑及离线插图/背景回放；B5-E支持聊天音乐开关、循环播放、暂停和收起，关闭预览或切换作品即停止；真实发送的屏蔽、建议和预设上下文、受控模型与游玩在B6接入。此处不改旧卡片、共享栈和账本。</p>',null,undefined,'关闭');
}
function menu(action) {
  const handlers = {
    'download-json':() => exportWork(),
    'view-json':showJson,'import-json':importDialog,
    'restore-demo':reloadServer,'scope':scope,
  };
  if (!handlers[action]) return false;
  $('#more-menu').hidden = true; run(handlers[action]); return true;
}
const worldUI = createWorldbookUI({changed:() => editor.dirty(),api,toast:notice});
const cgUI=createCGUI({changed:()=>editor.dirty(),toast:notice,describe,assets:()=>work?.assets||[],assetUrl,gallery:ticket=>gallery(ticket),upload:uploadEditorImage});
const regexUI=createDisplayRegexUI({changed:()=>editor.dirty(),toast:notice});
const regexStyle=document.createElement('link');regexStyle.rel='stylesheet';regexStyle.href='/display-regex-ui.css';document.head.append(regexStyle);
const assistanceUI=createInputAssistanceUI({changed:()=>{editor.dirty();clearInputCheck();},toast:notice});
const dialogueUI=createDialogueUI({changed:()=>editor.dirty(),toast:notice,
  fillInput:text=>{editor.fillComposer(text);keepPlayerDraft();clearInputCheck();notice('已填入玩家输入，尚未发送。');},
  settled:()=>{if(work&&documentsEqual(currentDocument(),work.draft))editor.saved(savedText(work.revision));},
});
const dialogueStyle=document.createElement('link');dialogueStyle.rel='stylesheet';dialogueStyle.href='/dialogue-ui.css';document.head.append(dialogueStyle);
const assistanceStyle=document.createElement('link');assistanceStyle.rel='stylesheet';assistanceStyle.href='/input-assistance-ui.css';document.head.append(assistanceStyle);
$('.preview-tabs').insertAdjacentHTML('beforeend','<button data-preview="dialogue" aria-pressed="false">开场与指令</button>');
const worldStyle=document.createElement("link");worldStyle.rel="stylesheet";worldStyle.href="/worldbook-ui.css";document.head.append(worldStyle);
const bgmRoot=documentElement('section');$('.preview-tabs').after(bgmRoot);
const chatBGM=mountChatBGM(bgmRoot);
const bgmStyle=documentElement('link');bgmStyle.rel='stylesheet';bgmStyle.href='/chat-bgm.css';document.head.append(bgmStyle);
function syncChatBGM(enter=false){
  const pane=$('.preview-pane');
  const active=bgmMode==='reply'&&getComputedStyle(pane).display!=='none'&&!document.hidden;
  chatBGM.update(chatBGMTrack(work,currentDocument()),{active,enter});
}
new MutationObserver(()=>syncChatBGM(true)).observe($('.preview-pane'),{attributes:true,attributeFilter:['class']});
window.addEventListener('resize',()=>syncChatBGM(false));
document.addEventListener('visibilitychange',()=>{
  // Returning to a tab requires an explicit playback/preview action.
  if(document.hidden)chatBGM.close();
});
const mediaWrite = task => task().catch(error => { throw {...error,scope:'work'}; });
const media = createMediaUI({
  editor,api,journal,setBusy,writable,describe,base64:fileBase64,
  beforeAudio:()=>chatBGM.close(),
  workId:() => work?.id || '', assets:() => work?.assets || [], assetUrl,
  context:() => ({sourceId:work?.id || null,document:currentDocument()}),
  adopt:(image,galleryRevision) => mediaWrite(async()=>{
    writable();
    if (!work?.id || !image) throw {code:'AUTHORING_ASSET_MISSING'};
    const existing = work.assets.find(asset => asset.alias === image.id && asset.sha256 === image.asset.sha256);
    if (existing) return existing.alias;
    const alias = work.assets.some(asset => asset.alias === image.id) ? 'image-' + crypto.randomUUID() : image.id;
    await mutate('/works/'+work.id+'/assets/from-gallery',{imageId:image.id,galleryRevision,alias},{preserve:true,keepEditors:true});
    return alias;
  }),
  choose:useAsset,
  uploadImage:(file,target) => mediaWrite(async()=>{
    const alias='image-'+crypto.randomUUID(),base64=await fileBase64(file);
    await mutate('/works/'+work.id+'/assets',{alias,fileName:file.name,mediaType:file.type,base64},{preserve:true,keepEditors:true});
    useAsset(alias,target);
  }),
  uploadAudio:file => mediaWrite(async()=>{
    const alias='audio-'+crypto.randomUUID(),base64=await fileBase64(file);
    await mutate('/works/'+work.id+'/assets',{alias,fileName:file.name,mediaType:'audio/mpeg',base64},{preserve:true,keepEditors:true});
    useAsset(alias,'bgm');
  }),
});
const gallery = target => media.open(target).catch(problem);
editor.connect({
  afterBasic,renderWorld,renderOther,gallery,renderPreview,
  save:automatic => run(() => save(automatic)),
  upload:(file,target) => run(() => prepareUpload(file,target)),
  uploadEditorImage:file => uploadEditorImage(file).catch(error=>{problem(error);return null;}),
  menu,
  changed() { validation = null;syncChatBGM(false); },
});
if(creatorMode){
 $('#publish-button').textContent='作品版本';
 $('[data-preview="reply"]').textContent='样式回放';
 $('[data-preview="assembly"]').textContent='提示词装配';
 $('#player-input').placeholder='输入角色资料或下一步行动…';
 $('#works-button').title='管理与新建作品';
 // Expanded preview is a local workspace dialog; keep keyboard focus out of
 // the obscured editor without changing the preserved prototype behavior.
 const previewPane=$('.preview-pane'),previewBackground=[$('.workspace-header'),$('.edit-pane')];
 const previewFocusables=()=>[...previewPane.querySelectorAll('button,a[href],input,textarea,select,summary,iframe,[tabindex]')].filter(node=>!node.disabled&&node.tabIndex>=0&&node.getClientRects().length>0&&!node.closest('[hidden],[inert]'));
 const syncPreviewFocus=()=>{
   const expanded=previewPane.classList.contains('open');
   for(const node of previewBackground)node.inert=expanded;
   if(expanded){previewPane.setAttribute('role','dialog');previewPane.setAttribute('aria-modal','true');}
   else{previewPane.removeAttribute('role');previewPane.removeAttribute('aria-modal');$('#preview-button').focus();}
 };
 new MutationObserver(syncPreviewFocus).observe(previewPane,{attributes:true,attributeFilter:['class']});
 previewPane.addEventListener('keydown',event=>{
   if(event.key!=='Tab'||!previewPane.classList.contains('open')||document.querySelector('dialog[open]'))return;
   const nodes=previewFocusables(),first=nodes[0],last=nodes.at(-1);
   if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
   else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
 });
}
$('#mock-send').textContent='回复回放';$('#mock-send').title='打开原始回复展示，不派发模型请求';
$('.preview-footnote').textContent=creatorMode?(document.body.dataset.execution==='controlled'?'点击发送才调用模型。':'离线演练 · 使用固定回复，不产生模型费用。'):'离线开局预览；作者脚本隔离运行。采用开局文字只填入输入框，不派发。';
$('#player-input').addEventListener('input',()=>{keepPlayerDraft();clearInputCheck();});
const assistanceActions=documentElement('span');assistanceActions.className='assistance-composer-actions';
$('#mock-send').before(assistanceActions);assistanceActions.append($('#mock-send'));
const checkInputButton=documentElement('button');checkInputButton.type='button';checkInputButton.textContent='检查输入';assistanceActions.prepend(checkInputButton);
const inputResult=documentElement('p');inputResult.id='assistance-input-result';inputResult.setAttribute('role','status');inputResult.hidden=true;$('.composer').append(inputResult);
function clearInputCheck(){const result=$('#assistance-input-result');if(result){result.hidden=true;result.textContent='';}}
checkInputButton.onclick=()=>{if(!work){notice('请先新建或打开作品。');return;}const result=assistanceUI.check($('#player-input').value);inputResult.textContent=result.message;inputResult.dataset.blocked=String(!result.allowed);inputResult.hidden=false;};
$('#scope-button').onclick = scope;
$('#works-button').onclick = () => run(works);
$('#gallery-button').onclick = () => gallery();
$('#export-button').onclick = () => run(() => exportWork());
$('#save-button').onclick = () => run(() => save());
$('#publish-button').onclick = () => run(versions);
const modelUI=createModelSettingsUI({button:$('#model-button'),api,getWork:()=>work,getOpeningText:()=>$('#player-input').value,notice});
modelUI.initialize();
$('#more-menu').innerHTML =
  button('导入作品','import','role="menuitem"') + button('查看 / 编辑配置 JSON','json','role="menuitem"') +
  button('导出配置','configuration','role="menuitem"') + button('导出完整作品包','package','role="menuitem"') + button('复制当前作品','copy','role="menuitem"') +
  button('载入服务器版本','reload','role="menuitem"') + button('导出本页草稿备份','backup','role="menuitem"') +
  button('作品资源清单','work-assets','role="menuitem"') + button('核对原操作','recover','role="menuitem"') + button(creatorMode?'功能与帮助':'本批能力','scope','role="menuitem"') + (creatorMode?'<a href="/creator-guide.html" target="_blank" rel="noopener" role="menuitem">创作指南</a>':'');
document.addEventListener('click',event => {
  if(creatorMode&&event.target.closest('#more-menu a')){$('#more-menu').hidden=true;$('#more-button').setAttribute('aria-expanded','false');}
  const control = event.target.closest('[data-b1]'); if (!control) return;
  const action = control.dataset.b1;
  if (control.closest('#more-menu')) { $('#more-menu').hidden = true; $('#more-button').setAttribute('aria-expanded','false'); }
  const actions = {
    'tags':manageTags,
    'new-work':newWork,
    'open-work':() => guardSwitch(async () => { await loadWork(control.dataset.id); editor.closeDialog(); }),
    'json':showJson,'import':importDialog,'configuration':() => exportWork(),'package':() => exportWork('package'),
    'copy':copyWork,'reload':reloadServer,'backup':backup,'recover':recover,'scope':scope,'seal':seal,
    'export-version':() => exportWork('configuration',control.dataset.version),
    'package-version':() => exportWork('package',control.dataset.version),
    'audio':() => workResources('bgm',true),
    'work-assets':() => workResources(),
    'use-asset':() => useAsset(control.dataset.alias),
    'asset-info':() => assetInfo(control.dataset.alias),
    'remove-asset':() => removeAsset(control.dataset.alias),
    'clear-audio':() => editor.change('bgm',null),
  };
  if (actions[action]) run(actions[action]);
});
document.addEventListener('compositionstart',() => { composing = true; });
document.addEventListener('compositionend',() => { composing = false; });
window.addEventListener('keydown',event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault(); if (!$('dialog[open]')) run(() => save());
  }
});
window.addEventListener('beforeunload',event => { if (pending || busy || media.client.writing || media.pending) { event.preventDefault(); event.returnValue=''; } });

async function start() {
  $('.workspace').hidden = true;
  setBusy(true);
  try {
    await openJournal();
    const state = await api('/state'); blank = state.blank;
    const records = await journal('read');
    const target = new URL(location.href).searchParams.get('work');
    media.client.hydrate(records);
    const workRecords = records.filter(item => item.scope !== 'gallery');
    pending = workRecords.find(item => item.workId === target || item.sourceId === target) || workRecords[0] || null;
    if (target) await loadWork(target);
    else {
      // A blank editor is not persisted until the user explicitly creates a work.
      work = {id:null,draft:clone(blank),assets:[],versions:[],revision:0};
      editor.load(toView(blank),[]); work = null;
      editor.saved('请选择或创建作品'); await works();
    }
    if (!pending && media.pending) {
      const draft = media.pending.document;
      if (draft && work?.id === media.pending.sourceId) { editor.load(toView(draft),images()); editor.dirty(); }
      notice('图库有待核对操作。打开创作图库核对原操作，不会自动重发。');
    }
    if (pending) {
      const draft = pending.document;
      if (draft && work?.id === pending.sourceId) { editor.load(toView(draft),images()); editor.dirty(); }
      editor.openDialog('有操作结果待核对','<p>已保留原操作编号和提交前草稿。不会自动重放。</p><p class="hint">' + esc(recoveryTarget(pending).operationId) + '</p>' + button('核对原操作','recover'),null,undefined,'关闭');
      notice('有待核对操作，请在“更多”中核对原操作。');
    }
  } catch (error) {
    if (!work) {
      for (const id of ['panel-basic','panel-world','panel-other','preview-content']) $('#' + id).replaceChildren();
      $('#work-title').textContent = '作品库未就绪';
      editor.saved('未能读取作品库，请核对服务后刷新。');
    }
    throw error;
  } finally { $('.workspace').hidden = false; setBusy(false); }
}
run(start);

// Optional runtime extension, loaded only by a host that explicitly enables it.
export function attachSessionPanel(factory){
  if(sessionPanel)throw Error('Session panel already attached');
  sessionPanel=factory({api,getWork:()=>work,getInput:()=>$('#player-input').value,
    setInput(text){$('#player-input').value=text;$('#player-input').dispatchEvent(new Event('input',{bubbles:true}));},
    openModels(purpose){ return modelUI.open(purpose); },notice});
  sessionPanel.initialize();
}

// Only the new host flag loads persistent turn playback. Preserved hosts keep
// their existing module graph and draft preview behavior.
if(document.body.dataset.turnPlayback==='true'){
 const {createPlaybackPanel}=await import('./playback-ui.mjs');
 playbackPanel=createPlaybackPanel({api,getWork:()=>work,
  setInput(text){$('#player-input').value=text;$('#player-input').dispatchEvent(new Event('input',{bubbles:true}));},notice,
  activate(){++previewGeneration;const old=activePresentation;activePresentation=null;releasePresentation(old);bgmMode='playback';syncChatBGM(false);$('#preview-content').hidden=true;document.querySelectorAll('[data-preview]').forEach(e=>e.setAttribute('aria-pressed','false'));}
 });
 playbackPanel.initialize();playbackPanel.workChanged();
}
