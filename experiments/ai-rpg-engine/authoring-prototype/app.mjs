import {selection, editSource, SourceHistory} from './editor.mjs';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const codePoints = value => [...String(value)].length;
const clone = value => structuredClone(value);
let sequence = 2;
let persistence = null;
const newId = () => 'prototype-' + sequence++;
const glyphs = {
  folder:'M3 7h6l2 2h10v11H3z M3 7V4h6l2 3', bold:'M7 3h6a4 4 0 0 1 0 8H7z M7 11h7a5 5 0 0 1 0 10H7z',
  italic:'M10 3h9 M5 21h9 M15 3 9 21', underline:'M6 3v7a6 6 0 0 0 12 0V3 M4 21h16',
  strike:'M4 12h16 M17 6c-1-4-10-4-10 1 0 2 3 3 5 3 M17 16c0 5-10 6-11 1',
  bullet:'M9 5h12 M9 12h12 M9 19h12 M3 5h1 M3 12h1 M3 19h1',
  ordered:'M10 5h11 M10 12h11 M10 19h11 M3 3h1v5 M3 11h3l-3 4h3 M3 18h3v4H3',
  quote:'M3 4h7v8H6c0 3-1 5-3 6 M14 4h7v8h-4c0 3-1 5-3 6',
  wrap:'M4 5h16 M4 10h13a4 4 0 0 1 0 8H9 M12 15l-3 3 3 3',
  code:'m8 6-6 6 6 6 m8-12 6 6-6 6', block:'m6 7-4 5 4 5 M15 3 9 21 m9-14 4 5-4 5',
  table:'M3 3h18v18H3z M3 9h18 M3 15h18 M9 3v18 M15 3v18',
  image:'M3 3h18v18H3z m0 15 6-6 4 4 3-3 5 5 M7 7h.1',
  link:'M10 8H7a4 4 0 0 0 0 8h3 M14 8h3a4 4 0 0 1 0 8h-3 M7 12h10',
  clear:'M3 6h18 M8 6V3h8v3 M6 6l1 15h10l1-15 M10 10v7 M14 10v7',
  undo:'M8 4 3 9l5 5 M3 9h10a7 7 0 0 1 7 7', redo:'m16 4 5 5-5 5 M21 9H11a7 7 0 0 0-7 7',
  full:'M3 9V3h6 M15 3h6v6 M21 15v6h-6 M9 21H3v-6', up:'m6 14 6-6 6 6', down:'m6 10 6 6 6-6',
  copy:'M8 8h13v13H8z M16 8V3H3v13h5', more:'M12 5h.1 M12 12h.1 M12 19h.1'
};
const icon = key => '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="' + glyphs[key] + '"/></svg>';
const button = (text, action, cls = '', extra = '') => '<button type="button" class="' + cls + '" data-action="' + action + '" ' + extra + '>' + text + '</button>';
const switchControl = (label, path, checked) => '<label class="switch-label"><span>' + label + '</span><input type="checkbox" role="switch" data-bind="' + path + '" aria-label="' + label + '"' + (checked ? ' checked' : '') + '></label>';
const choices = (group, options, selected, radio = false) => '<div class="row">' + options.map(([key,label]) => '<label class="choice"><input type="' + (radio ? 'radio' : 'checkbox') + '" name="' + group + '" value="' + key + '" data-choice="' + group + '"' + (selected.includes(key) ? ' checked' : '') + '>' + label + '</label>').join('') + '</div>';
const input = (label, id, value, type = 'text', extra = '') => '<label class="form-label" for="' + id + '">' + label + '</label><input id="' + id + '" type="' + type + '" value="' + escapeHtml(value) + '" ' + extra + '>';
const art = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="720" height="420" viewBox="0 0 720 420"><rect width="720" height="420" fill="#e7edf3"/><path d="M0 250H720V420H0z" fill="#a8c1d4"/><path d="M0 280 130 215 220 270 400 180 530 265 720 225V420H0z" fill="#6e8da6"/><path d="M0 340 160 310 350 355 520 305 720 340V420H0z" fill="#416584"/><circle cx="550" cy="105" r="28" fill="#fff"/></svg>');
const sourceSample = '<main class="my-opening">\n  <h1>雾港来信</h1>\n  <p>你收到一封没有署名的信。</p>\n  <label>你的名字 <input id="name" value="林舟"></label>\n  <button id="prepare">填写开局内容</button>\n</main>\n<style>\n.my-opening { max-width: 430px; margin: 40px auto; }\n</style>\n<!-- 作者可在此编辑交互脚本；B0 仅展示源码，不执行。 -->';
const defaultEntry = (name = '港口旧事') => ({id:newId(), name, enabled:true, category:'地点与线索', roles:['user'], rule:'or', regex:false, keywords:['雾港','灯塔'], targets:['system'], order:0, depth:0, chance:100, mode:'regular', body:'雾港的灯塔每晚九点熄灯。守塔人知道旧信的来历。', contents:[{text:'灯塔仍然亮着。',weight:1},{text:'海面传来钟声。',weight:1},{text:'门口留着一封新信。',weight:1}], collapsed:false});
let state = {
  schema:'modelmirror.authoring.b0-demo/1', name:'雾港来信', tags:['互动故事','现代幻想'], language:'简体中文', audience:'所有玩家', autosave:false,
  intro:sourceSample, description:'一封迟到十年的信，把你带回了雾港。', prefix:'', suffix:'', system:'你是互动故事的叙述者。\n保持已经发生的事实与人物关系。不要替玩家决定行动。',
  orders:{system:0,prefix:0,suffix:0}, cover:null, desktop:null, mobile:null, css:'.my-opening { color: #334155; }\ndetails { margin: 12px 0; }',
  worldDefaults:{roles:['user'],rule:'or',targets:['system']}, worldbooks:[defaultEntry()], categories:['地点与线索'], blocked:['示例屏蔽词'], defaultMemory:3, suggestions:true, dialogueMode:'opening',
  opening:'你站在雾港的码头，口袋里的旧信被海风吹得沙沙作响。', presets:[], shortcuts:[{name:'查看线索',text:'整理我目前已经知道的线索。'}],
  regexRules:[], cg:[{id:'cg-demo',name:'灯塔插图',keywords:['灯塔','雾港'],rule:'or',target:'chat',weighted:false,assets:['asset-demo'],weights:[1]}], bgm:null
};
let assets = [{id:'asset-demo',name:'雾港示例.svg',url:art,alias:'harbor',sample:true}];
let savedSnapshot = null, dirty = true, previewMode = 'opening', activeTab = 'basic', search = '', toastTimer, dialogSave = null, imageTarget = null, oldFocus = null, worldUndo = null, dragId = null, modelDraft = null;
const histories = new Map();
const getPath = path => path.split('.').reduce((value,key) => value?.[key],state);
const setPath = (path,value) => {const keys=path.split('.');let target=state;for(const key of keys.slice(0,-1))target=target[key];target[keys.at(-1)]=value;};
function markDirty(){dirty=true;$('#save-status').textContent='未保存修改';$('#work-title').textContent=state.name || '未命名作品';persistence?.changed?.();}
function toast(message){clearTimeout(toastTimer);$('#toast').textContent=message;$('#toast').hidden=false;toastTimer=setTimeout(()=>$('#toast').hidden=true,4200);}
function openDialog(title,body,onSave=null,saveText='保存',cancelText='取消'){
  closeEditorPopover(false);
  if($('#dialog').open)closeDialog();
  oldFocus=document.activeElement;dialogSave=onSave;
  $('#dialog-title').textContent=title;$('#dialog-body').innerHTML=body;
  $('#dialog-actions').innerHTML=button(cancelText,'dialog-cancel')+(onSave?'<button type="submit" class="primary">'+saveText+'</button>':'');
  $('#dialog').showModal();
}
function closeDialog(){const resume=editorDialogReturn;editorDialogReturn=null;dialogSave=null;$('#dialog').close();if(resume?.area.isConnected)restoreSelection(resume.area,resume.bookmark);else oldFocus?.focus();}
$('#dialog-close').addEventListener('click',closeDialog);
$('#dialog').addEventListener('cancel',event=>{event.preventDefault();closeDialog();});
$('#dialog-form').addEventListener('submit',event=>{event.preventDefault();if(dialogSave && dialogSave() !== false)closeDialog();});
function confirmAction(title,text,run){openDialog(title,'<p>'+text+'</p>',run,'确认');}
function editor(path,title,hint='',order=false){
  const tools=[['heading','标题'],['bold','加粗'],['italic','斜体'],['underline','下划线'],['strike','删除线'],['bullet','无序列表'],['ordered','有序列表'],['quote','引用'],['wrap','换行'],['code','行内代码'],['block','代码块'],['table','表格'],['image','图片'],['link','链接'],['clear','清空'],['undo','撤销'],['redo','重做'],['full','全屏']];
  return '<div class="field"><label for="source-'+path+'">'+title+'</label>'+(hint?'<p class="hint">'+hint+'</p>':'')+'<div class="editor" data-editor="'+path+'"><div class="editor-toolbar" role="toolbar" aria-label="'+title+'工具栏">'+tools.map(([key,name])=>'<button type="button" data-tool="'+key+'" aria-label="'+name+'" title="'+name+'"'+((key==='undo'||key==='redo')?' disabled':'')+'>'+(key==='heading'?'Tᴛ':icon(key))+'</button>').join('')+'</div><textarea id="source-'+path+'" data-source="'+path+'" aria-label="'+title+'源码" spellcheck="false">'+escapeHtml(getPath(path))+'</textarea><footer class="editor-footer"><span data-count="'+path+'">'+codePoints(getPath(path))+' 字</span><span>源码文本 · 原样保存</span></footer></div>'+(order?'<label class="order-control"><span>插入顺序</span><input aria-label="'+title+'插入顺序" type="number" min="0" max="9999" data-bind="orders.'+path+'" value="'+state.orders[path]+'"></label>':'')+'</div>';
}
function mediaField(title,path){
  const asset=assets.find(item=>item.id===state[path]);
  return '<div class="field"><div class="field-row"><span class="field-title">'+title+'</span>'+button('从图库选择','choose-asset','text-button','data-target="'+path+'"')+'</div><div class="asset-row"><button class="upload-box" aria-label="上传'+title+'" data-action="upload-image" data-target="'+path+'">'+(asset?'<img src="'+asset.url+'" alt="'+escapeHtml(asset.name)+'">':'<span class="plus">＋</span>')+'</button><div class="asset-meta">'+(asset?'<p class="hint">'+escapeHtml(asset.name)+'</p>'+button('移除','remove-media','text-button','data-target="'+path+'"'):'<p class="hint">PNG、JPG、JPEG、WEBP、GIF<br>上传后可在创作图库中复用</p>')+'</div></div></div>';
}
function renderBasic(){
  $('#panel-basic').innerHTML='<div class="field"><label for="name">作品名称</label><input class="wide" id="name" data-bind="name" type="text" value="'+escapeHtml(state.name)+'"></div><div class="field"><div class="field-row"><span>管理标签</span>'+button('管理标签','tags','primary')+'</div><div class="tags">'+state.tags.map(x=>'<span class="tag">'+escapeHtml(x)+'</span>').join('')+'</div></div>'+mediaField('封面','cover')+
  '<div class="field"><label class="inline-label">语言（必选）<select data-bind="language" aria-label="语言"><option>简体中文</option><option>繁體中文</option><option>English</option></select></label></div><div class="field"><label class="inline-label">作品面向群体（必选）<select data-bind="audience" aria-label="作品面向群体"><option>所有玩家</option><option>成年玩家</option></select></label></div>'+
  '<div class="field">'+switchControl('自动保存','autosave',state.autosave)+'<p class="hint">开启后每 3 分钟保存一次草稿。B0 只保存在当前页面，刷新即重置。</p></div>'+
  editor('intro','详细介绍 / 开局页面','编辑 HTML、CSS 和交互脚本。右侧当前为固定开局样例；可执行沙箱在 B4 接入。')+
  editor('description','简介','给你的作品写一句简介，不参与模型请求。')+
  editor('prefix','前置词','系统自动拼接在本轮玩家输入之前，可用于触发主提示词世界书。',true)+
  editor('suffix','后置词','系统自动拼接在本轮玩家输入之后，历史消息不重复包裹。',true)+
  editor('system','提示词','主提示词，用来实现世界观和设定；进入 API system。',true)+
  '<details class="help-disclosure"><summary>本轮范围与后续能力</summary><p>HTML 状态栏和普通 CG 本轮支持。变量数据骨架、模板与模型状态更新，后续作为“变量面板”插件立项。</p></details>';
  $('[data-bind="language"]').value=state.language;$('[data-bind="audience"]').value=state.audience;persistence?.afterBasic?.();bindEditors();
}
function weightedBodies(entry){
  const total=entry.contents.reduce((sum,item)=>sum+(Number.isFinite(Number(item.weight))?Number(item.weight):0),0);
  return entry.contents.map((item,index)=>'<div class="weighted-item"><div class="row"><span>内容 '+(index+1)+'</span><label class="weight-caption">权重<input type="number" min="0" step="any" data-weight="'+index+'" value="'+escapeHtml(item.weight)+'" aria-label="内容'+(index+1)+'权重"></label>'+button('×','remove-weight','','data-index="'+index+'" aria-label="删除内容'+(index+1)+'"')+'</div><textarea data-weight-text="'+index+'" aria-label="概率内容'+(index+1)+'">'+escapeHtml(item.text)+'</textarea><small>占比 '+(total>0?(Number(item.weight)/total*100).toFixed(2):'0.00')+'%</small></div>').join('');
}
function worldCard(entry,index){
  return '<article class="book-card" data-entry="'+entry.id+'"><header><button class="drag-handle" draggable="true" aria-label="拖动排序 '+escapeHtml(entry.name)+'" title="拖动排序；也可使用上移、下移">⠿</button><button class="card-name text-button" data-action="rename-entry">No.'+(index+1)+' · '+escapeHtml(entry.name)+'</button><label class="switch-label"><input type="checkbox" role="switch" data-entry-field="enabled" aria-label="启用 '+escapeHtml(entry.name)+'"'+(entry.enabled?' checked':'')+'></label><div class="compact-menu">'+button(icon('up'),'entry-up','icon-button','aria-label="上移 '+escapeHtml(entry.name)+'"')+button(icon('down'),'entry-down','icon-button','aria-label="下移 '+escapeHtml(entry.name)+'"')+button(icon('more'),'entry-menu','icon-button','aria-label="条目更多 '+escapeHtml(entry.name)+'"')+'</div></header><div class="card-body"'+(entry.collapsed?' hidden':'')+'>'+
  '<label>触发区域：</label>'+choices('roles-'+entry.id,[['user','用户'],['assistant','AI']],entry.roles)+
  '<div class="field-row"><span>格式：</span>'+button(entry.regex?'切换为关键词':'切换为正则','toggle-regex','text-button')+'</div>'+choices('rule-'+entry.id,[['and','与'],['or','或']],[entry.rule],true)+
  '<div class="row nowrap"><input class="keyword-input" aria-label="触发关键词" placeholder="'+(entry.regex?'输入正则表达式（B0 不执行）':'填写触发关键词后按回车或点击添加')+'" data-keyword-input><button class="primary" data-action="add-keyword">添加</button></div><div class="tags">'+entry.keywords.map((key,i)=>'<span class="tag">'+escapeHtml(key)+button('×','remove-keyword','','data-index="'+i+'" aria-label="移除关键词 '+escapeHtml(key)+'"')+'</span>').join('')+'</div>'+
  '<label>影响范围：</label>'+choices('targets-'+entry.id,[['system','提示词'],['prefix','前置词'],['suffix','后置词']],entry.targets)+
  '<p class="hint">前后置词及其世界书先拼接，可触发主提示词世界书；不递归扫描。</p>'+
  '<div class="numbers">'+[['order','插入顺序',9999],['depth','扫描深度',20],['chance','触发概率',100]].map(([key,label,max])=>'<label class="order-control"><span>'+label+'</span><input type="number" min="0" max="'+max+'" data-entry-field="'+key+'" aria-label="'+label+'" value="'+entry[key]+'"></label>').join('')+'</div>'+
  '<div class="field-row"><div class="segment"><button data-action="content-regular" aria-pressed="'+(entry.mode==='regular')+'">常规</button><button data-action="content-weighted" aria-pressed="'+(entry.mode==='weighted')+'">概率</button></div>'+(entry.mode==='weighted'?button('归一化','normalize','text-button'):'')+'</div>'+
  (entry.mode==='regular'?'<div class="body-editor"><textarea aria-label="世界书内容" data-entry-field="body">'+escapeHtml(entry.body)+'</textarea><small>'+codePoints(entry.body)+' 字</small></div>':'<p class="hint">触发后按权重只选一份正文。归一化合计为 100.00%，支持撤销。</p>'+weightedBodies(entry)+button('＋ 添加内容','add-weight','wide-action'))+
  '<p class="error" data-entry-error hidden></p></div></article>';
}
function renderWorld(){
  if(persistence)return persistence.renderWorld();
  const filtered=state.worldbooks.filter(entry=>(entry.name+' '+entry.body+' '+entry.keywords.join(' ')).toLowerCase().includes(search.toLowerCase()));
  $('#panel-world').innerHTML='<div class="book-tools"><div class="row end">'+button('下载世界书模板','world-template')+button('导入模板','world-import')+'</div><input class="search" id="world-search" type="search" aria-label="搜索世界书" placeholder="请输入触发关键词 / 内容" value="'+escapeHtml(search)+'"><p class="hint">扫描深度 0 仅查看本轮输入；1–20 额外查看完整历史回合。<br>触发概率与正文权重分别计算，恢复同一操作不重新抽签。</p><div class="row">'+button('全部收起','collapse-all','text-button')+button('全部展开','expand-all','text-button')+button('撤销条目操作','undo-world','text-button',worldUndo?'':'disabled')+'<span class="hint">'+state.worldbooks.length+' 个条目</span></div></div>'+
  worldDefaultsPanel()+
  (filtered.length?filtered.map(entry=>'<div class="category-label">'+escapeHtml(entry.category || '未分类')+'</div>'+worldCard(entry,state.worldbooks.indexOf(entry))).join(''):'<div class="empty-state">没有匹配条目</div>')+
  button('＋ 新增','add-entry','wide-action')+button('添加分类','add-category','wide-action')+button('删除全部','delete-entries','wide-action danger')+
  '<details class="help-disclosure"><summary>查看本地概率规则</summary><p>触发概率决定是否生效；内容权重决定选中哪一份正文。零权重不参与抽取，全部为零不能发布。按最大余数法保留两位小数，相同余数按列表顺序分配。</p><p>例：1 / 1 / 1 → 33.34 / 33.33 / 33.33。</p></details>';
}
function renderOther(){
  if(persistence)return persistence.renderOther();
  $('#panel-other').innerHTML=mediaField('背景图','desktop')+mediaField('移动设备背景图','mobile')+
  '<div class="field"><span class="field-title">背景音乐</span><p class="hint">MP3，10 MB 以内。由玩家点击后播放。</p>'+button(state.bgm?'更换音乐':'上传','upload-audio','primary')+(state.bgm?'<p class="hint">'+escapeHtml(state.bgm.name)+'</p><audio controls src="'+state.bgm.url+'"></audio>':'')+'</div>'+
  '<div class="field"><label>屏蔽词</label><p class="hint">玩家输入命中时阻止发送，保留输入草稿。</p><div class="row nowrap"><input id="block-input" placeholder="在此处填写触发关键词" aria-label="屏蔽词">'+button('添加','add-block','primary')+'</div><div class="tags">'+state.blocked.map((item,index)=>'<span class="tag">'+escapeHtml(item)+button('×','remove-block','','data-index="'+index+'" aria-label="移除屏蔽词 '+escapeHtml(item)+'"')+'</span>').join('')+'</div></div>'+
  '<div class="field"><label class="inline-label">默认记忆长度<input type="number" min="1" max="50" value="'+state.defaultMemory+'" data-bind="defaultMemory" aria-label="默认记忆长度"></label><p class="hint">作为 M1 开局建议。玩家明确授权后才生效；不会自动裁剪历史。</p></div>'+
  '<div class="field"><span class="field-title">正则脚本</span><p class="hint">只替换显示副本，原文仍用于历史、世界书及插件。规则在隔离环境执行并限时。</p>'+state.regexRules.map((rule,index)=>'<div class="quick-item"><span>'+escapeHtml(rule.name)+'</span>'+button('编辑','edit-regex','','data-index="'+index+'"')+'</div>').join('')+button('＋ 添加脚本','add-regex','wide-action')+'</div>'+
  '<div class="field"><div class="field-row"><span>CG 图片</span><span class="pill">普通 CG</span></div><p class="hint">完整回复后匹配关键词，选择聊天插图或背景切换；支持加权图片。刷新不重新抽图。B0 仅编辑示例配置，不执行抽图。</p>'+state.cg.map(cgCard).join('')+button('＋ 新增 CG','add-cg','wide-action')+'</div>'+
  editor('css','内置 CSS','作品级样式。模型回复不执行 JavaScript；正式通用隔离渲染在 B4 接入。')+
  '<div class="field"><div class="field-row"><span>快捷回复指令</span><small>'+state.shortcuts.length+'/10</small></div><p class="hint">点击后填入输入框，由玩家发送。</p>'+state.shortcuts.map((item,index)=>'<div class="quick-item"><span>'+escapeHtml(item.name)+'</span>'+button('试填','try-shortcut','','data-index="'+index+'"')+button('编辑','edit-shortcut','','data-index="'+index+'"')+'</div>').join('')+button('添加指令','add-shortcut','wide-action',state.shortcuts.length>=10?'disabled':'')+'</div>'+
  '<div class="field"><label>选择对话方式</label><div class="preset-choice"><button data-action="mode-preset" aria-pressed="'+(state.dialogueMode==='preset')+'">预设对话</button><button data-action="mode-opening" aria-pressed="'+(state.dialogueMode==='opening')+'">开场白</button></div><div class="quick-item"><span>'+(state.dialogueMode==='opening'?'对话开场白':'预设对话 · '+state.presets.length+'/10')+'</span>'+button('编辑',state.dialogueMode==='opening'?'edit-opening':'edit-presets')+'</div></div>'+
  '<div class="field">'+switchControl('下一步问题建议','suggestions',state.suggestions)+'<p class="hint">从回复中的 suggested_questions 区域提取建议，点击后填入输入框，不增加模型调用。</p><pre class="code-note">&lt;suggested_questions&gt;\n1. 前往灯塔\n2. 阅读旧信\n3. 询问船夫\n&lt;/suggested_questions&gt;</pre>'+button('查看回复样例','show-reply','text-button')+'</div>'+
  '<details class="help-disclosure"><summary>后续创作模块可选插件</summary><ul><li>CG 收藏：特殊 CG、解锁记录、画廊与剧情回顾。</li><li>变量面板：数据骨架、模板和模型状态更新。</li><li>创作对照评测：一次发送并行比较多个模型。</li></ul><p>这些能力本轮不提供可保存但无效的设置。</p></details>';
  bindEditors();
}

function renderPreview(){
  if(persistence?.renderPreview){persistence.renderPreview(previewMode);return;}
  $$('[data-preview]').forEach(el=>el.setAttribute('aria-pressed',el.dataset.preview===previewMode));
  let html='<p class="preview-label">独立示例 · 不使用 Earth / God 专用结构</p>';
  if(previewMode==='opening'){
    html+='<div class="story-opening"><p class="sample-title">开局交接演示</p><h2>'+escapeHtml(state.name)+'</h2><p>'+escapeHtml(state.description)+'</p><label for="sample-name">你的名字</label><div class="row nowrap"><input id="sample-name" value="林舟" maxlength="30">'+button('随机','random-name')+'</div><label for="sample-identity">身份</label><select id="sample-identity"><option>归乡旅人</option><option>见习守塔人</option><option>旧物修复师</option></select><details><summary>关于雾港</summary><p>每晚九点，港口灯塔都会熄灭。没有人记得这个习惯始于哪一年。</p></details>'+button('填写开局内容','handoff','primary')+'<p class="hint">只填入右下方输入框，不会自动发送。</p></div>';
  }else if(previewMode==='reply'){
    html+='<div class="reply-sample"><details open><summary>功能区</summary><p>直播：开启　·　建议：'+(state.suggestions?'开启':'关闭')+'</p></details><label class="flip-label"><input type="checkbox" aria-label="切换宏观微观视角">宏观 / 微观视角 ⇄</label><div class="flip-stage"><div class="flip-front">雾港在薄雾中醒来。远处的灯塔没有按时熄灭，码头上的船夫放下了手里的绳索。<br><br>这是十年来的第一次。</div><div class="flip-back">你展开旧信。纸上的字迹依然清晰：<br><br>“如果灯塔没有熄灭，就到旧码头找我。”</div></div><details><summary>世界日志</summary><p>你收到一封旧信。灯塔出现了异常。</p></details><div class="live-sample"><strong>直播主题：灯塔来信</strong><div class="danmu-lane"><div class="danmu-items"><span>灯怎么还亮着？</span><span>去旧码头看看</span><span>那封信来自哪里？</span><span>等一个答案</span></div></div><small>悬停可暂停；系统减少动态效果时静止显示。</small></div><details><summary>展开示例图片</summary><img class="sample-image" src="'+art+'" alt="雾港示例插图"></details><p>海风吹过，你把信收回口袋。旧码头就在前方。</p>'+(state.suggestions?'<div class="suggestions">'+['前往旧码头','仔细检查信封','向船夫询问灯塔'].map(text=>button(text,'suggestion','','data-text="'+text+'"')).join('')+'</div>':'')+'</div>';
  }else{
    html+='<p class="hint">这是装配位置说明，不是实际请求捕获。世界书匹配、M1/M2/M3 与冻结回执在 B3/B6 接入。</p><div class="data-block"><span class="label">system · 作者主提示词</span><pre>'+escapeHtml(state.system)+'</pre></div><div class="data-block"><span class="label">system 末尾 · 可选历史资料</span><pre>M2 历史摘要（如启用）\nM3 命中记忆（如启用）</pre></div><div class="data-block"><span class="label">历史 user / assistant</span><pre>按实际生效策略选择，原始内容保持不变。</pre></div><div class="data-block"><span class="label">本轮 user</span><pre>'+escapeHtml([state.prefix,'[开局资料或本轮玩家输入]',state.suffix].filter(Boolean).join('\n\n'))+'</pre></div>';
  }
  $('#preview-content').innerHTML=html;
}
function openPreview(){ $('.preview-pane').classList.add('open');$('#preview-button').setAttribute('aria-expanded','true');$('#close-preview').focus();}
function closePreview(){ $('.preview-pane').classList.remove('open');$('#preview-button').setAttribute('aria-expanded','false');$('#preview-button').focus();}
function fillComposer(text){$('#player-input').value=text;updateInputCount();openPreview();$('#player-input').focus();}
function updateInputCount(){$('#input-count').textContent=codePoints($('#player-input').value)+' 字';}
function snapshotWorld(){worldUndo=clone(state.worldbooks);}
function normalizeWeights(contents){
  const values=contents.map(item=>Number(item.weight));
  if(values.some(value=>!Number.isFinite(value)||value<0)||!values.some(value=>value>0))throw new Error('权重须为非负有限数，且至少一项大于 0。');
  const max=Math.max(...values),scaled=values.map(value=>value/max),total=scaled.reduce((a,b)=>a+b,0);
  const exact=scaled.map(value=>value/total*10000),units=exact.map(Math.floor);
  const ranked=exact.map((value,index)=>({index,remainder:value-units[index]})).sort((a,b)=>b.remainder-a.remainder||a.index-b.index);
  for(let i=0,remaining=10000-units.reduce((a,b)=>a+b,0);i<remaining;i++)units[ranked[i].index]++;
  return contents.map((item,index)=>({...item,weight:units[index]/100}));
}
function refreshAll(){closeEditorPopover(false);editorGeneration++;exitEditorFull();histories.clear();renderBasic();renderWorld();renderOther();renderPreview();$('#work-title').textContent=state.name;}
function saveInPage(automatic=false){if(persistence)return persistence.save(automatic);savedSnapshot=clone(state);dirty=false;$('#save-status').textContent=(automatic?'自动保存':'已保存')+' · 仅当前页面';toast('已保存本页演示快照；正式落盘与刷新恢复在 B1 接入。');}
const readSelection = area => selection(area.value,area.selectionStart,area.selectionEnd,area.selectionDirection);
const restoreSelection = (area,value) => {
  area.focus({preventScroll:true}); area.setSelectionRange(value.start,value.end,value.direction);
};
const beforeEdits = new WeakMap(), compositionTimers = new WeakMap();
let fullEditor = null, editorDialogReturn = null, editorPopover = null, editorGeneration = 0;
const imageUploads = new Set();
function bindEditors(){
  if(editorPopover&&!editorPopover.area.isConnected)closeEditorPopover(false);
  if (fullEditor && !fullEditor.area.isConnected) exitEditorFull();
  $$('[data-source]').forEach(area=>{
    const path=area.dataset.source;
    if(!histories.has(path))histories.set(path,new SourceHistory(readSelection(area)));
    const history=histories.get(path);
    if(history.current.text === area.value)area.setSelectionRange(history.current.start,history.current.end,history.current.direction);
    updateEditorButtons(path);
  });
}
function updateEditorButtons(path){
  const area=$('[data-source="'+path+'"]');if(!area)return;
  const history=histories.get(path),editorEl=area.closest('.editor');
  const locked=area.disabled||area.readOnly||!!history?.composition||imageUploads.has(path);
  $$('[data-tool]',editorEl).forEach(control=>{
    const tool=control.dataset.tool;
    control.disabled=locked||(tool==='undo'&&!history?.past.length)||(tool==='redo'&&!history?.future.length)||(tool==='clear'&&!area.value);
    control.setAttribute('aria-controls',(tool==='heading'||tool==='table')?'editor-tool-popover':area.id);
    if(tool==='heading'||tool==='table'){control.setAttribute('aria-haspopup',tool==='heading'?'menu':'grid');control.setAttribute('aria-expanded',String(editorPopover?.anchor===control));}
  });
  const enabled=$$('[data-tool]',editorEl).filter(control=>!control.disabled);
  const current=enabled.find(control=>control===document.activeElement)||enabled.find(control=>control.tabIndex===0)||enabled[0];
  $$('[data-tool]',editorEl).forEach(control=>control.tabIndex=control===current?0:-1);
  $('[data-count="'+path+'"]').textContent=codePoints(area.value)+' 字';
}
function syncSource(area){
  setPath(area.dataset.source,area.value);updateEditorButtons(area.dataset.source);markDirty();
}
function paintSource(area,next){
  const top=area.scrollTop,left=area.scrollLeft;
  area.value=next.text;restoreSelection(area,next);area.scrollTop=top;area.scrollLeft=left;
  syncSource(area);
  if(editorDialogReturn?.area===area)editorDialogReturn.bookmark=next;
}
function changeSource(area,next,before=readSelection(area)){
  const history=histories.get(area.dataset.source);
  if(history.composition||area.disabled||area.readOnly)return;
  if(history.commit(before,next))paintSource(area,next);
}
function editorUndo(area,redo=false){
  const history=histories.get(area.dataset.source);
  if(area.disabled||area.readOnly||history.composition)return;
  const next=redo?history.redo():history.undo(readSelection(area));
  if(next)paintSource(area,next);
}
function finishComposition(area){
  clearTimeout(compositionTimers.get(area));compositionTimers.delete(area);
  if(!area.isConnected){beforeEdits.delete(area);return;}
  const history=histories.get(area.dataset.source);
  if(history?.composition){history.finishComposition(readSelection(area));syncSource(area);}
  beforeEdits.delete(area);
}
function sourceInput(event){
  const area=event.target,history=histories.get(area.dataset.source);
  if(event.inputType==='historyUndo'||event.inputType==='historyRedo'){
    // Also handle non-cancelable native history input; never mix it with our transaction stack.
    area.value=history.current.text;restoreSelection(area,history.current);
    editorUndo(area,event.inputType==='historyRedo');beforeEdits.delete(area);return;
  }
  if(history.composition){syncSource(area);return;}
  history.commit(beforeEdits.get(area)||history.current,readSelection(area));
  beforeEdits.delete(area);syncSource(area);
}
function editorDialog(area,title,body,save=null,saveText='插入'){
  const bookmark=readSelection(area);
  openDialog(title,body+'<p class="error" id="editor-error" role="alert" hidden></p>',save?()=>{
    try {const result=save(bookmark);if(result===false)return false;}
    catch(error){$('#editor-error').textContent=error.message;$('#editor-error').hidden=false;return false;}
  }:null,saveText);
  editorDialogReturn={area,bookmark};
}
function exitEditorFull(){
  if(!fullEditor)return;
  const {area,container,inert,overflow}=fullEditor;
  fullEditor=null;container.classList.remove('fullscreen');container.removeAttribute('role');container.removeAttribute('aria-modal');container.removeAttribute('aria-label');
  for(const [element,previous] of inert)element.inert=previous;
  document.body.style.overflow=overflow;
  const control=$('[data-tool="full"]',container);control.setAttribute('aria-label','全屏');control.setAttribute('title','全屏');control.setAttribute('aria-pressed','false');
  if(area.isConnected)restoreSelection(area,readSelection(area));
}
function toggleEditorFull(area){
  if(fullEditor?.area===area){exitEditorFull();return;}
  exitEditorFull();
  const container=area.closest('.editor'),inert=[];
  let node=container;
  while(node.parentElement){
    for(const sibling of node.parentElement.children){
      if(sibling!==node&&!['dialog','toast'].includes(sibling.id)){inert.push([sibling,sibling.inert]);sibling.inert=true;}
    }
    node=node.parentElement;if(node===document.body)break;
  }
  fullEditor={area,container,inert,overflow:document.body.style.overflow};
  document.body.style.overflow='hidden';container.classList.add('fullscreen');
  container.setAttribute('role','dialog');container.setAttribute('aria-modal','true');container.setAttribute('aria-label',area.getAttribute('aria-label')+' · 全屏');
  const control=$('[data-tool="full"]',container);control.setAttribute('aria-label','退出全屏');control.setAttribute('title','退出全屏');control.setAttribute('aria-pressed','true');
  restoreSelection(area,readSelection(area));
}
function closeEditorPopover(restore = true) {
  if(!editorPopover)return;
  const {popup,anchor,area,before}=editorPopover;
  editorPopover=null;popup.remove();anchor.setAttribute('aria-expanded','false');
  if(restore&&area.isConnected)restoreSelection(area,before);
}
function placeEditorPopover() {
  if(!editorPopover)return;
  const {popup,anchor}=editorPopover,rect=anchor.getBoundingClientRect();
  if(!anchor.isConnected||rect.bottom<0||rect.top>innerHeight){closeEditorPopover(false);return;}
  const width=popup.offsetWidth,height=popup.offsetHeight;
  popup.style.left=Math.max(8,Math.min(rect.left,innerWidth-width-8))+'px';
  popup.style.top=Math.max(8,Math.min(rect.bottom+3,innerHeight-height-8))+'px';
}
function openEditorPopover(tool,area) {
  const anchor=$('[data-tool="'+tool+'"]',area.closest('.editor'));
  if(editorPopover?.anchor===anchor){closeEditorPopover();return;}
  closeEditorPopover();
  const before=readSelection(area),popup=document.createElement('div');
  popup.id='editor-tool-popover';popup.className='editor-popover '+(tool==='heading'?'editor-heading-menu':'editor-table-menu');
  popup.setAttribute('role',tool==='heading'?'menu':'grid');
  popup.setAttribute('aria-label',tool==='heading'?'标题级别':'表格尺寸');
  if(tool==='heading'){
    popup.innerHTML=[1,2,3,4,5,6].map(level=>'<button type="button" role="menuitem" data-level="'+level+'" tabindex="-1">H'+level+'</button>').join('');
  }else{
    popup.innerHTML=[1,2,3,4].map(row=>'<div role="row">'+[1,2,3,4,5,6].map(column=>'<button type="button" role="gridcell" data-columns="'+column+'" data-rows="'+row+'" aria-label="'+column+' 列 '+row+' 行" tabindex="-1"></button>').join('')+'</div>').join('');
  }
  document.body.append(popup);editorPopover={popup,anchor,area,before,tool};
  anchor.setAttribute('aria-expanded','true');
  const controls=$$('button',popup);
  function highlight(control) {
    controls.forEach(item=>{item.tabIndex=item===control?0:-1;if(tool==='table')item.classList.toggle('selected',Number(item.dataset.rows)<=Number(control.dataset.rows)&&Number(item.dataset.columns)<=Number(control.dataset.columns));});
  }
  popup.addEventListener('pointerover',event=>{const control=event.target.closest('button');if(control)highlight(control);});
  popup.addEventListener('focusin',event=>{if(event.target.matches('button'))highlight(event.target);});
  popup.addEventListener('click',event=>{
    const control=event.target.closest('button');if(!control)return;
    const options=tool==='heading'?{level:Number(control.dataset.level)}:{columns:Number(control.dataset.columns),rows:Number(control.dataset.rows)};
    closeEditorPopover(false);changeSource(area,editSource(before,tool,options),before);
  });
  popup.addEventListener('keydown',event=>{
    const at=controls.indexOf(document.activeElement);let next=at;
    if(event.key==='Escape'||event.key==='Tab'){event.preventDefault();event.stopPropagation();closeEditorPopover();return;}
    if(tool==='heading'){
      if(event.key==='ArrowDown')next=(at+1)%controls.length;
      else if(event.key==='ArrowUp')next=(at-1+controls.length)%controls.length;
      else if(event.key==='Home')next=0;
      else if(event.key==='End')next=controls.length-1;
      else return;
    }else{
      const row=Math.floor(at/6),column=at%6;
      if(event.key==='ArrowRight')next=row*6+Math.min(5,column+1);
      else if(event.key==='ArrowLeft')next=row*6+Math.max(0,column-1);
      else if(event.key==='ArrowDown')next=Math.min(3,row+1)*6+column;
      else if(event.key==='ArrowUp')next=Math.max(0,row-1)*6+column;
      else if(event.key==='Home')next=event.ctrlKey?0:row*6;
      else if(event.key==='End')next=event.ctrlKey?23:row*6+5;
      else return;
    }
    event.preventDefault();event.stopPropagation();controls[next].focus();
  });
  placeEditorPopover();controls[0].tabIndex=0;controls[0].focus({preventScroll:true});
}
function chooseEditorImage(area) {
  const before=readSelection(area),path=area.dataset.source,generation=editorGeneration;
  const chooser=document.createElement('input');
  chooser.type='file';chooser.accept='image/png,image/jpeg,image/webp,image/gif';chooser.hidden=true;
  document.body.append(chooser);
  const cancel=()=>{chooser.remove();if(area.isConnected)restoreSelection(area,before);};
  chooser.addEventListener('cancel',cancel,{once:true});
  chooser.addEventListener('change',async()=>{
    const file=chooser.files[0];chooser.remove();if(!file){cancel();return;}
    imageUploads.add(path);updateEditorButtons(path);area.setAttribute('aria-busy','true');
    try {
      let asset;
      if(persistence){
        asset=await persistence.uploadEditorImage(file);
      }else{
        if(!['image/png','image/jpeg','image/webp','image/gif'].includes(file.type)||file.size>5*1024*1024)throw new Error('请选择 5 MB 以内的 PNG、JPG、WEBP 或 GIF。');
        const alias='image-'+crypto.randomUUID();
        asset={alias,label:file.name.replace(/[\r\n]/g,' ')};
        assets.push({id:alias,alias,name:file.name,url:URL.createObjectURL(file)});
      }
      if(!asset)return;
      const current=$('[data-source="'+path+'"]');
      if(generation!==editorGeneration||!current||current.value!==before.text){
        toast('图片已保存到本地图库；编辑内容已变化，请从图库复制资源引用，未覆盖当前文字。');return;
      }
      changeSource(current,editSource(before,'image',asset),before);
      toast('图片已加入本地图库并插入源码，请保存草稿。');
    }catch(error){toast(error.message);}
    finally{imageUploads.delete(path);area.removeAttribute('aria-busy');updateEditorButtons(path);}
  },{once:true});
  chooser.click();
}
function applyTool(tool,area){
  if(!area||area.disabled||area.readOnly||histories.get(area.dataset.source)?.composition||imageUploads.has(area.dataset.source))return;
  if(tool==='heading'||tool==='table')return openEditorPopover(tool,area);
  closeEditorPopover(false);
  if(tool==='undo'||tool==='redo')return editorUndo(area,tool==='redo');
  if(tool==='full')return toggleEditorFull(area);
  if(tool==='image')return chooseEditorImage(area);
  if(tool==='clear'&&!window.confirm('您确定要清空所有内容吗？'))return;
  const before=readSelection(area);
  try{changeSource(area,editSource(before,tool),before);}catch(error){toast(error.message);}
}

function gallery(target=null){
  if(persistence)return persistence.gallery(target);
  openDialog(target?'选择资源':'创作图库','<p class="hint">B0 上传资源只保留在本页内存；正式资源清单与本地落盘在 B1 接入。</p><div class="gallery">'+assets.map(asset=>button('<img src="'+asset.url+'" alt="'+escapeHtml(asset.name)+'"><span>'+escapeHtml(asset.name)+'</span><small>asset:'+escapeHtml(asset.alias)+'</small>','pick-asset','','data-asset="'+asset.id+'"')).join('')+'</div>'+button('＋ 上传图片','upload-image','wide-action','data-target="'+(target||'gallery')+'"'),null,undefined,'关闭');
  $$('[data-action="pick-asset"]').forEach(el=>el.onclick=()=>{if(target){setPath(target,el.dataset.asset);markDirty();renderBasic();renderOther();closeDialog();}else{const asset=assets.find(item=>item.id===el.dataset.asset);openDialog('资源详情','<img class="sample-image" src="'+asset.url+'" alt="'+escapeHtml(asset.name)+'"><p class="hint">名称：'+escapeHtml(asset.name)+'<br>别名：asset:'+escapeHtml(asset.alias)+'</p>',null,undefined,'关闭');}});
}
function modelDialog(){
  const draft=modelDraft||{model:'示例模型 A',temperature:0.7,top_p:0.8,max_tokens:16384};
  openDialog('模型与参数','<p class="hint">B0 为控件示例，无真实目录、资格或调用。正式版只显示所选模型支持的参数，剧情模型不会更改摘要或记忆模型。</p><label class="form-label" for="mock-model">剧情模型</label><select id="mock-model"><option>示例模型 A</option><option>示例模型 B</option></select><div class="model-grid">'+[['temperature','温度',0,2,0.01],['top_p','Top P',0,1,0.01],['max_tokens','最大输出 tokens',1,32768,1]].map(([key,title,min,max,step])=>'<label>'+title+'<input id="param-'+key+'" type="number" min="'+min+'" max="'+max+'" step="'+step+'" value="'+draft[key]+'"></label>').join('')+'</div><div class="section-note">不支持的参数会明确报错；本原型不开放 Top K、思考预算等未经验证的控件。多模型并行对照后续单独立项。</div>',()=>{
    modelDraft={model:$('#mock-model').value,temperature:Number($('#param-temperature').value),top_p:Number($('#param-top_p').value),max_tokens:Number($('#param-max_tokens').value)};
    $('#model-button').textContent=modelDraft.model;toast('已选择演示参数，未派发任何请求。');
  },'采用演示参数');
  $('#mock-model').value=draft.model;
}
function editRegex(index=-1){
  const rule=state.regexRules[index]||{name:'显示规则',pattern:'<note>([\\s\\S]*?)</note>',flags:'g',replacement:'$1'};
  openDialog('显示用正则',input('规则名称','regex-name',rule.name)+input('匹配表达式','regex-pattern',rule.pattern)+input('标志','regex-flags',rule.flags)+input('替换文本','regex-replacement',rule.replacement)+'<p class="hint">只作用于显示副本。B0 保存示例配置但不执行正则，隔离执行与超时在 B5 验证。</p>',()=>{const next={name:$('#regex-name').value,pattern:$('#regex-pattern').value,flags:$('#regex-flags').value,replacement:$('#regex-replacement').value};if(index<0)state.regexRules.push(next);else state.regexRules[index]=next;markDirty();renderOther();},'保存示例');
}
function shortcutDialog(index=-1){
  const item=state.shortcuts[index]||{name:'',text:''};
  openDialog('快捷回复指令',input('指令名称','shortcut-name',item.name,'text','required maxlength="40"')+'<label class="form-label" for="shortcut-text">填入输入框的内容</label><textarea id="shortcut-text" required>'+escapeHtml(item.text)+'</textarea>',()=>{const next={name:$('#shortcut-name').value.trim(),text:$('#shortcut-text').value};if(!next.name||!next.text.trim())return false;if(index<0)state.shortcuts.push(next);else state.shortcuts[index]=next;markDirty();renderOther();});
}
function exportConfig(value=state,name='authoring-b0-demo.json'){
  const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
  const link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('已导出 B0 演示 JSON；不包含正式作品版本或资源包。');
}

window.addEventListener('resize',placeEditorPopover);
document.addEventListener('scroll',placeEditorPopover,true);
document.addEventListener('pointerdown',event=>{
  if(editorPopover&&!editorPopover.popup.contains(event.target)&&!editorPopover.anchor.contains(event.target))closeEditorPopover(false);
  if(event.pointerType==='mouse'&&event.button===0&&event.target.closest('[data-tool]'))event.preventDefault();
});
document.addEventListener('beforeinput',event=>{
  const area=event.target;if(!area.dataset.source)return;
  const history=histories.get(area.dataset.source);
  if(event.inputType==='historyUndo'||event.inputType==='historyRedo'){
    if(event.cancelable){event.preventDefault();editorUndo(area,event.inputType==='historyRedo');}return;
  }
  // The final IME input may be reported as insertText or an empty inputType.
  // Keep the composition transaction open until the scheduled settlement.
  if(!history.composition){history.remember(readSelection(area));beforeEdits.set(area,readSelection(area));}
});
document.addEventListener('compositionstart',event=>{
  const area=event.target;if(!area.dataset.source)return;
  if(compositionTimers.has(area))finishComposition(area);
  histories.get(area.dataset.source).startComposition(readSelection(area));updateEditorButtons(area.dataset.source);
});
document.addEventListener('compositionend',event=>{
  const area=event.target;if(!area.dataset.source)return;
  compositionTimers.set(area,setTimeout(()=>finishComposition(area),0));
});
for(const name of ['select','keyup','pointerup','focusout']){
  document.addEventListener(name,event=>{
    const area=event.target;if(area.dataset.source)histories.get(area.dataset.source)?.remember(readSelection(area));
  },true);
}

document.addEventListener('input',event=>{
  const el=event.target;
  if(el.dataset.source){sourceInput(event);return;}
  if(el.dataset.bind){setPath(el.dataset.bind,el.type==='checkbox'?el.checked:el.type==='number'?Number(el.value):el.value);markDirty();}
  if(el.id==='world-search'){search=el.value;const caret=el.selectionStart;renderWorld();$('#world-search').focus();$('#world-search').setSelectionRange(caret,caret);}
  if(el.id==='player-input')updateInputCount();
  const entry=state.worldbooks.find(item=>item.id===el.closest('[data-entry]')?.dataset.entry);
  if(entry){
    if(el.dataset.entryField){const key=el.dataset.entryField;entry[key]=el.type==='checkbox'?el.checked:el.type==='number'?Number(el.value):el.value;markDirty();if(key==='body')el.nextElementSibling.textContent=codePoints(el.value)+' 字';}
    if(el.dataset.weight!==undefined){entry.contents[Number(el.dataset.weight)].weight=el.value;markDirty();}
    if(el.dataset.weightText!==undefined){entry.contents[Number(el.dataset.weightText)].text=el.value;markDirty();}
  }
});
document.addEventListener('change',event=>{
  const el=event.target;
  if(el.dataset.choice){
    const entry=state.worldbooks.find(item=>item.id===el.closest('[data-entry]')?.dataset.entry);
    const key=el.dataset.choice.split('-')[0];
    const selected=$$('input[data-choice="'+el.dataset.choice+'"]:checked').map(inputEl=>inputEl.value);
    if(key==='roles'&&!selected.length){el.checked=true;toast('至少保留一个扫描角色。');return;}
    if(key==='targets'&&!selected.length){el.checked=true;toast('至少选择一个影响范围。');return;}
    entry[key]=key==='rule'?el.value:selected;markDirty();
  }
});
document.addEventListener('keydown',event=>{
  const el=event.target;
  const sourceArea=el.dataset.source?el:null;
  if(sourceArea&&(event.isComposing||event.keyCode===229||histories.get(sourceArea.dataset.source)?.composition)){
    if((event.ctrlKey||event.metaKey)&&['s','z','y','b','i','u'].includes(event.key.toLowerCase())){event.preventDefault();event.stopPropagation();}
    return;
  }
  if(event.key==='Escape'&&!$('#dialog').open){
    if(editorPopover){event.preventDefault();closeEditorPopover();return;}
    if(fullEditor){event.preventDefault();exitEditorFull();return;}
    if($('.preview-pane').classList.contains('open'))closePreview();
    $('#more-menu').hidden=true;$('#more-button').setAttribute('aria-expanded','false');
  }
  if(fullEditor&&event.key==='Tab'&&!$('#dialog').open){
    const focusables=$$('button:not(:disabled),textarea',fullEditor.container).filter(control=>control.tabIndex>=0);
    const next=event.shiftKey?focusables.at(-1):focusables[0];
    if((event.shiftKey&&el===focusables[0])||(!event.shiftKey&&el===focusables.at(-1))){event.preventDefault();next.focus();return;}
  }
  const toolbar=el.closest('.editor-toolbar');
  if(toolbar&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
    event.preventDefault();const controls=$$('button:not(:disabled)',toolbar),index=controls.indexOf(el);
    const next=event.key==='Home'?0:event.key==='End'?controls.length-1:(index+(event.key==='ArrowRight'?1:-1)+controls.length)%controls.length;
    controls.forEach((control,i)=>control.tabIndex=i===next?0:-1);controls[next]?.focus();return;
  }
  if(sourceArea&&(event.ctrlKey||event.metaKey)&&!event.altKey){
    if(event.key.toLowerCase()==='z'){event.preventDefault();editorUndo(el,event.shiftKey);}
    if(event.key.toLowerCase()==='y'){event.preventDefault();editorUndo(el,true);}
    if(['b','i','u'].includes(event.key.toLowerCase())){event.preventDefault();applyTool({b:'bold',i:'italic',u:'underline'}[event.key.toLowerCase()],el);}
  }
  if(el.hasAttribute('data-keyword-input')&&event.key==='Enter'&&!event.isComposing){event.preventDefault();$('[data-action="add-keyword"]',el.closest('[data-entry]')).click();}
  if(el.getAttribute('role')==='tab'&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
    event.preventDefault();const tabs=$$('[role="tab"]'),index=tabs.indexOf(el),next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].click();tabs[next].focus();
  }
});
document.addEventListener('click',event=>{
  const tool=event.target.closest('[data-tool]');if(tool){if(!tool.disabled)applyTool(tool.dataset.tool,$('textarea',tool.closest('.editor')));return;}
  const tab=event.target.closest('[data-tab]');
  if(tab){activeTab=tab.dataset.tab;$$('[role="tab"]').forEach(el=>{el.setAttribute('aria-selected',el===tab);el.tabIndex=el===tab?0:-1;});$$('[role="tabpanel"]').forEach(el=>el.hidden=el.id!=='panel-'+activeTab);$('#edit-scroll').scrollTop=0;return;}
  const preview=event.target.closest('[data-preview]');if(preview){previewMode=preview.dataset.preview;renderPreview();return;}
  const control=event.target.closest('[data-action]');if(!control)return;
  const action=control.dataset.action,index=Number(control.dataset.index),entry=state.worldbooks.find(item=>item.id===control.closest('[data-entry]')?.dataset.entry);
  if(action==='dialog-cancel')return closeDialog();
  if(action==='upload-image'){imageTarget=control.dataset.target;$('#image-file').click();return;}
  if(action==='choose-asset')return gallery(control.dataset.target);
  if(action==='remove-media'){setPath(control.dataset.target,null);markDirty();renderBasic();renderOther();return;}
  if(action==='upload-audio'){$('#audio-file').click();return;}
  if(action==='tags'){openDialog('管理标签',input('标签（逗号分隔）','tag-values',state.tags.join('，'))+'<p class="hint">本地作品分类，不连接原站标签或分发系统。</p>',()=>{state.tags=[...new Set($('#tag-values').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean))];markDirty();renderBasic();});return;}
  if(action==='random-name'){$('#sample-name').value=['林舟','许澄','沈遥','江白'][Math.floor(Math.random()*4)];return;}
  if(action==='handoff'){fillComposer('【角色】\n姓名：'+($('#sample-name').value||'旅人')+'\n身份：'+$('#sample-identity').value+'\n\n【开场】\n'+state.opening);toast('已填入开局内容，尚未发送。');return;}
  if(action==='suggestion'){fillComposer(control.dataset.text);toast('建议已填入输入框。');return;}
  if(action==='show-reply'){previewMode='reply';renderPreview();openPreview();return;}
  if(action==='add-entry'){snapshotWorld();state.worldbooks.push({...defaultEntry('新条目'),...clone(state.worldDefaults)});markDirty();renderWorld();return;}
  if(action==='undo-world'){if(worldUndo){const old=state.worldbooks;state.worldbooks=worldUndo;worldUndo=old;markDirty();renderWorld();toast('已撤销条目操作。');}return;}
  if(action==='collapse-all'||action==='expand-all'){state.worldbooks.forEach(item=>item.collapsed=action==='collapse-all');renderWorld();return;}
  if(action==='delete-entries'){confirmAction('删除全部世界书','将移除本页 '+state.worldbooks.length+' 个示例条目。可通过“撤销条目操作”恢复。',()=>{snapshotWorld();state.worldbooks=[];markDirty();renderWorld();});return;}
  if(action==='add-category'){openDialog('添加分类',input('分类名称','category-name','','text','required maxlength="60"'),()=>{const name=$('#category-name').value.trim();if(!name)return false;if(!state.categories.includes(name))state.categories.push(name);markDirty();toast('分类已加入条目的移动选项。');});return;}
  if(action==='world-template'){exportConfig({schema:'modelmirror.authoring.b0-worldbook/1',entries:[defaultEntry('模板条目')]},'worldbook-b0-template.json');return;}
  if(action==='world-import'){openDialog('导入世界书模板','<p class="hint">B0 只演示问题确认界面；正式模板校验与原子导入在 B3 接入。</p><textarea aria-label="世界书模板源码" placeholder="粘贴 JSON 查看原型流程"></textarea><div class="validation-row">导入前将列出格式、重复 ID、未知字段、容量和资源问题。</div>',null,undefined,'关闭');return;}
  if(entry){
    if(action==='rename-entry'){openDialog('条目名称',input('名称','entry-name',entry.name,'text','required maxlength="100"'),()=>{snapshotWorld();entry.name=$('#entry-name').value;markDirty();renderWorld();});return;}
    if(action==='entry-up'||action==='entry-down'){const at=state.worldbooks.indexOf(entry),to=at+(action==='entry-up'?-1:1);if(to<0||to>=state.worldbooks.length){toast('已到列表边界。');return;}snapshotWorld();[state.worldbooks[at],state.worldbooks[to]]=[state.worldbooks[to],state.worldbooks[at]];markDirty();renderWorld();return;}
    if(action==='entry-menu'){openDialog('条目操作','<div class="stack">'+button(entry.collapsed?'展开条目':'收起条目','entry-collapse')+button('复制条目','entry-copy')+'<label for="entry-category">移动到分类</label><select id="entry-category">'+state.categories.map(name=>'<option>'+escapeHtml(name)+'</option>').join('')+'</select>'+button('应用分类','entry-category')+button('删除条目','entry-delete','danger')+'</div>',null,undefined,'关闭');$('#entry-category').value=entry.category;$$('[data-action^="entry-"]',$('#dialog-body')).forEach(el=>el.onclick=()=>{snapshotWorld();if(el.dataset.action==='entry-collapse')entry.collapsed=!entry.collapsed;if(el.dataset.action==='entry-copy')state.worldbooks.push({...clone(entry),id:newId(),name:entry.name+' 副本'});if(el.dataset.action==='entry-delete')state.worldbooks=state.worldbooks.filter(item=>item!==entry);if(el.dataset.action==='entry-category')entry.category=$('#entry-category').value;markDirty();renderWorld();closeDialog();});return;}
    if(action==='toggle-regex'){snapshotWorld();entry.regex=!entry.regex;markDirty();renderWorld();return;}
    if(action==='add-keyword'){const area=$('[data-keyword-input]',control.closest('[data-entry]')),key=area.value.trim();if(key&&!entry.keywords.includes(key)){snapshotWorld();entry.keywords.push(key);markDirty();renderWorld();}return;}
    if(action==='remove-keyword'){snapshotWorld();entry.keywords.splice(index,1);markDirty();renderWorld();return;}
    if(action==='content-regular'||action==='content-weighted'){snapshotWorld();entry.mode=action==='content-regular'?'regular':'weighted';markDirty();renderWorld();return;}
    if(action==='add-weight'){snapshotWorld();entry.contents.push({text:'',weight:1});markDirty();renderWorld();return;}
    if(action==='remove-weight'){if(entry.contents.length===1){toast('至少保留一份正文。');return;}snapshotWorld();entry.contents.splice(index,1);markDirty();renderWorld();return;}
    if(action==='normalize'){try{const next=normalizeWeights(entry.contents);snapshotWorld();entry.contents=next;markDirty();renderWorld();toast('已归一化为 100.00%；可撤销。');}catch(error){const line=$('[data-entry-error]',control.closest('[data-entry]'));line.hidden=false;line.textContent=error.message;}return;}
  }
  if(action==='add-block'){const text=$('#block-input').value.trim();if(text&&!state.blocked.includes(text)){state.blocked.push(text);markDirty();renderOther();}return;}
  if(action==='remove-block'){state.blocked.splice(index,1);markDirty();renderOther();return;}
  if(action==='add-regex'||action==='edit-regex')return editRegex(action==='add-regex'?-1:index);
  if(action==='add-cg'){state.cg.push({id:newId(),name:'新 CG',keywords:[],rule:'or',target:'chat',weighted:false,assets:[],weights:[]});markDirty();renderOther();return;}
  if(action==='add-shortcut'||action==='edit-shortcut')return shortcutDialog(action==='add-shortcut'?-1:index);
  if(action==='try-shortcut'){fillComposer(state.shortcuts[index].text);return;}
  if(action==='mode-preset'||action==='mode-opening'){state.dialogueMode=action==='mode-preset'?'preset':'opening';markDirty();renderOther();return;}
  if(action==='edit-opening'){openDialog('对话开场白','<label class="form-label" for="opening-text">开场白内容</label><textarea id="opening-text">'+escapeHtml(state.opening)+'</textarea><p class="hint">保存不调用模型；本页演示开局交接时使用此内容。</p>',()=>{state.opening=$('#opening-text').value;markDirty();});return;}
  if(action==='edit-presets'){const preset=state.presets[0]||{title:'',question:'',answer:'',opening:''};openDialog('预设对话',input('对话标题（最多 30 字）','preset-title',preset.title,'text','maxlength="30"')+input('提问（最多 1000 字）','preset-question',preset.question,'text','maxlength="1000"')+'<label class="form-label" for="preset-answer">回答（最多 10000 字）</label><textarea id="preset-answer" maxlength="10000">'+escapeHtml(preset.answer)+'</textarea>'+input('开场选项（最多 50 字）','preset-opening',preset.opening,'text','maxlength="50"')+'<p class="hint">B0 展示单份编辑流程；多份增删、排序与运行装配在 B5 接入。</p>',()=>{state.presets=[{title:$('#preset-title').value,question:$('#preset-question').value,answer:$('#preset-answer').value,opening:$('#preset-opening').value}];markDirty();renderOther();});return;}
});
document.addEventListener('dragstart',event=>{const handle=event.target.closest('.drag-handle');if(!handle)return;dragId=handle.closest('[data-entry]').dataset.entry;event.dataTransfer.setData('text/plain',dragId);event.dataTransfer.effectAllowed='move';handle.closest('[data-entry]').classList.add('dragging');});
document.addEventListener('dragover',event=>{const card=event.target.closest('[data-entry]');if(card&&dragId){event.preventDefault();$$('.drag-over').forEach(el=>el.classList.remove('drag-over'));card.classList.add('drag-over');}});
document.addEventListener('drop',event=>{const card=event.target.closest('[data-entry]');if(!card||!dragId)return;event.preventDefault();const from=state.worldbooks.findIndex(item=>item.id===dragId),to=state.worldbooks.findIndex(item=>item.id===card.dataset.entry);if(from!==to&&from>=0&&to>=0){snapshotWorld();const [entry]=state.worldbooks.splice(from,1);state.worldbooks.splice(to,0,entry);markDirty();renderWorld();}dragId=null;});
document.addEventListener('dragend',()=>{dragId=null;$$('.dragging,.drag-over').forEach(el=>el.classList.remove('dragging','drag-over'));});
$('#image-file').addEventListener('change',async event=>{
  const file=event.target.files[0];if(!file)return;
  if(persistence){event.target.value='';return persistence.upload(file,imageTarget);}
  if(!['image/png','image/jpeg','image/webp','image/gif'].includes(file.type)||file.size>5*1024*1024){toast('请选择 5 MB 以内的 PNG、JPG、WEBP 或 GIF。');event.target.value='';return;}
  const id=newId();assets.push({id,name:file.name,url:URL.createObjectURL(file),alias:id});
  if(imageTarget&&imageTarget!=='gallery'){setPath(imageTarget,id);markDirty();renderBasic();renderOther();closeDialog();}else gallery();
  event.target.value='';
});
$('#audio-file').addEventListener('change',event=>{
  const file=event.target.files[0];if(!file)return;
  if(persistence){event.target.value='';return persistence.upload(file,'bgm');}
  if((file.type!=='audio/mpeg'&&!file.name.toLowerCase().endsWith('.mp3'))||file.size>10*1024*1024){toast('请选择 10 MB 以内的 MP3。');event.target.value='';return;}
  state.bgm={name:file.name,url:URL.createObjectURL(file)};markDirty();renderOther();event.target.value='';
});
$('#works-button').innerHTML=icon('folder');
$('#works-button').onclick=()=>openDialog('作品管理','<div class="validation-row"><strong>'+escapeHtml(state.name)+'</strong><p class="hint">当前 B0 演示作品，尚无正式发布版本。</p></div><p class="hint">空白创建、作品复制、刷新恢复与版本列表在 B1 接入。本批审阅布局与交互，不改动已有两卡。</p>',null,undefined,'关闭');
$('#more-menu').innerHTML=button('导入配置','import-json','','role="menuitem"')+button('查看配置 JSON','view-json','','role="menuitem"')+button('导出配置','download-json','','role="menuitem"')+button('恢复本页快照','restore-demo','','role="menuitem"')+button('能力与本地规则','scope','','role="menuitem"');
$('#more-button').onclick=()=>{const menu=$('#more-menu');menu.hidden=!menu.hidden;$('#more-button').setAttribute('aria-expanded',!menu.hidden);};
document.addEventListener('click',event=>{
  if(!event.target.closest('.menu-anchor')){$('#more-menu').hidden=true;$('#more-button').setAttribute('aria-expanded','false');}
  const action=event.target.closest('[data-action]')?.dataset.action;
  if(persistence?.menu(action))return;
  if(action==='download-json')exportConfig();
  if(action==='view-json'){openDialog('配置 JSON（只读预览）','<textarea aria-label="配置 JSON" readonly>'+escapeHtml(JSON.stringify(state,null,2))+'</textarea><p class="hint">这是 B0 演示 schema，正式往返与完整作品包在 B1 接入。</p>',null,undefined,'关闭');}
  if(action==='import-json'){openDialog('导入配置','<p>正式导入将在 B1 先校验全部字段、资源与版本，再展示问题并原子保存。</p><div class="validation-row">未知字段：明确报错，原作品保持不变。</div><div class="validation-row">资源缺失：列出引用与修复入口。</div><div class="validation-row">校验通过：确认后保存为新作品。</div><p class="hint">B0 不接收真实作品包，避免误认为已经完成安全导入。</p>',null,undefined,'关闭');}
  if(action==='restore-demo'){if(!savedSnapshot){toast('尚无本页保存快照。');return;}confirmAction('恢复本页快照','放弃当前未保存修改，恢复上次点击保存的示例内容。',()=>{state=clone(savedSnapshot);dirty=false;refreshAll();$('#save-status').textContent='已恢复 · 仅当前页面';});}
  if(action==='scope')showScope();
});
function showScope(){openDialog('B0 范围与验收','<p>本批交付控件清单、合同和桌面 / 390px 可交互原型。</p><ul class="scope-list"><li>可试用：18 项文本工具、弹窗取消、世界书编辑与排序、权重归一化及撤销、图库选用、开局交接、折叠、翻面和弹幕样例。</li><li>仅演示：保存快照留在当前页面；JSON 导出为 B0 配置。</li><li>尚未接入：正式存储、导入发布、可执行作者沙箱、实际世界书匹配、CG 抽图、真实模型与插件运行。</li></ul><p class="hint">B0 不调用任何模型。原型验收后才进入 B1。CG 收藏、变量状态引擎和并行多模型对照另行立项。</p>',null,undefined,'关闭');}
$('#scope-button').onclick=showScope;
$('#gallery-button').onclick=()=>gallery();
$('#export-button').onclick=()=>exportConfig();
$('#save-button').onclick=()=>saveInPage();
$('#publish-button').onclick=()=>openDialog('本地发布检查','<p>正式发布会生成不可变版本；已有会话继续使用原版本。</p><div class="validation-row">必填资料、提示词与参数校验</div><div class="validation-row">资源清单、缺失引用与执行能力校验</div><div class="validation-row">世界书概率、容量与非法正则诊断</div><div class="validation-row">发布版本确认与本地游玩入口</div><p class="hint">B0 尚未接入发布接口，不会创建可玩版本或增加额度。</p>',null,undefined,'关闭');
$('#preview-button').onclick=openPreview;$('#close-preview').onclick=closePreview;
$('#refresh-preview').onclick=()=>{renderPreview();toast(persistence?.renderPreview?'已读取当前编辑内容；不保存、不调用模型。':'已刷新固定样例，不影响输入草稿，不调用模型。');};
$('#model-button').onclick=modelDialog;
$('#mock-send').onclick=()=>{if(persistence?.renderPreview){previewMode='reply';renderPreview();return;}const value=$('#player-input').value;const matched=state.blocked.find(word=>word&&value.toLowerCase().includes(word.toLowerCase()));if(matched){toast('输入包含屏蔽词“'+matched+'”，草稿已保留。');return;}previewMode='reply';renderPreview();toast('已回放固定样例，没有派发模型请求。');};
setInterval(()=>{if(state.autosave&&dirty)saveInPage(true);},180000);
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
refreshAll();

function worldDefaultsPanel(){
  const row=(key,label,items,radio=false)=>'<p class="hint">'+label+'</p><div class="row">'+items.map(([value,text])=>'<label class="choice"><input type="'+(radio?'radio':'checkbox')+'" name="default-'+key+'" data-default="'+key+'" value="'+value+'"'+((radio?state.worldDefaults[key]===value:state.worldDefaults[key].includes(value))?' checked':'')+'>'+text+'</label>').join('')+'</div>';
  return '<details class="book-defaults"><summary>世界书默认样式</summary>'+row('roles','触发区域：',[['user','用户'],['assistant','AI']])+row('rule','格式：',[['and','与'],['or','或']],true)+row('targets','影响范围：',[['system','提示词'],['prefix','前置词'],['suffix','后置词']])+'<p class="hint">只应用于之后新增的条目，不改写现有条目。</p></details>';
}
function cgCard(item,index){
  const radio=(key,items)=>'<div class="row">'+items.map(([value,text])=>'<label class="choice"><input type="radio" name="cg-'+key+'-'+index+'" data-cg-field="'+key+'" value="'+value+'"'+(item[key]===value?' checked':'')+'>'+text+'</label>').join('')+'</div>';
  const action=(text,key,extra='')=>'<button type="button" data-cg-action="'+key+'" '+extra+'>'+text+'</button>';
  return '<article class="book-card" data-cg="'+index+'"><header><span class="card-name">No.'+(index+1)+' · '+escapeHtml(item.name)+'</span>'+action('⋮','more','class="icon-button" aria-label="CG 条目更多"')+'</header><div class="card-body"><label>触发区域：</label><span class="choice">◉ AI</span><label>格式：</label>'+radio('rule',[['and','与'],['or','或']])+'<label>区域映射：</label>'+radio('target',[['background','背景图片'],['chat','聊天']])+'<div class="row nowrap"><input class="keyword-input" data-cg-keyword aria-label="CG 触发关键词" placeholder="填写触发关键词后按回车或点击添加">'+action('添加','keyword','class="primary"')+'</div><div class="tags">'+item.keywords.map((word,i)=>'<span class="tag">'+escapeHtml(word)+action('×','remove-keyword','data-index="'+i+'" aria-label="移除 CG 关键词 '+escapeHtml(word)+'"')+'</span>').join('')+'</div><div class="segment"><button data-cg-action="regular" aria-pressed="'+!item.weighted+'">常规</button><button data-cg-action="weighted" aria-pressed="'+item.weighted+'">概率</button></div><label class="field-row"><span>图片：</span>'+action('从图库选择','asset','class="text-button"')+'</label><div class="stack">'+item.assets.map((id,i)=>{const asset=assets.find(value=>value.id===id);return '<div class="weighted-item"><img class="sample-image" src="'+asset.url+'" alt="'+escapeHtml(asset.name)+'"><div class="row"><small>'+escapeHtml(asset.name)+'</small>'+(item.weighted?'<label class="weight-caption">权重<input data-cg-weight="'+i+'" type="number" min="0" step="any" value="'+item.weights[i]+'" aria-label="CG 图片'+(i+1)+'权重"></label>':'')+action('移除','remove-asset','data-index="'+i+'" class="text-button"')+'</div></div>';}).join('')+'</div>'+(item.assets.length?'':'<p class="hint">尚未选择图片。</p>')+'</div></article>';
}
document.addEventListener('change',event=>{
  const el=event.target;
  if(el.dataset.default){const key=el.dataset.default,selected=$$('input[data-default="'+key+'"]:checked').map(inputEl=>inputEl.value);if(!selected.length){el.checked=true;toast('至少选择一项。');return;}state.worldDefaults[key]=key==='rule'?el.value:selected;markDirty();}
  const cg=state.cg[Number(el.closest('[data-cg]')?.dataset.cg)];
  if(cg&&el.dataset.cgField){cg[el.dataset.cgField]=el.value;markDirty();}
  if(cg&&el.dataset.cgWeight!==undefined){cg.weights[Number(el.dataset.cgWeight)]=Number(el.value);markDirty();}
});
document.addEventListener('keydown',event=>{if(event.target.hasAttribute('data-cg-keyword')&&event.key==='Enter'&&!event.isComposing){event.preventDefault();$('[data-cg-action="keyword"]',event.target.closest('[data-cg]')).click();}});
document.addEventListener('click',event=>{
  const buttonEl=event.target.closest('[data-cg-action]');if(!buttonEl)return;
  const card=buttonEl.closest('[data-cg]'),index=Number(card.dataset.cg),item=state.cg[index],action=buttonEl.dataset.cgAction;
  if(action==='regular'||action==='weighted'){item.weighted=action==='weighted';markDirty();renderOther();}
  if(action==='keyword'){const value=$('[data-cg-keyword]',card).value.trim();if(value&&!item.keywords.includes(value)){item.keywords.push(value);markDirty();renderOther();}}
  if(action==='remove-keyword'){item.keywords.splice(Number(buttonEl.dataset.index),1);markDirty();renderOther();}
  if(action==='remove-asset'){const at=Number(buttonEl.dataset.index);item.assets.splice(at,1);item.weights.splice(at,1);markDirty();renderOther();}
  if(action==='more'){
    openDialog('CG 条目操作',input('名称','cg-entry-name',item.name)+button('删除此 CG','delete-cg','danger'),()=>{item.name=$('#cg-entry-name').value;markDirty();renderOther();});
    $('[data-action="delete-cg"]').onclick=()=>{state.cg.splice(index,1);markDirty();renderOther();closeDialog();};
  }
  if(action==='asset'){
    openDialog('选择 CG 图片','<p class="hint">通过“创作图库”上传图片，再选择用于此 CG。</p><div class="gallery">'+assets.map(asset=>button('<img src="'+asset.url+'" alt=""><span>'+escapeHtml(asset.name)+'</span>','cg-select-asset','','data-asset="'+asset.id+'"')).join('')+'</div>',null,undefined,'取消');
    $$('[data-action="cg-select-asset"]').forEach(el=>el.onclick=()=>{if(!item.assets.includes(el.dataset.asset)){if(!item.weighted){item.assets=[];item.weights=[];}item.assets.push(el.dataset.asset);item.weights.push(1);markDirty();renderOther();}closeDialog();});
  }
});


// B1 host adapter: persistence is opt-in; the original B0 preview remains in-memory.
export const authoringEditor = {
  connect(handlers) { persistence = handlers; },
  read() { return clone(state); },
  isDirty() { return dirty; },
  load(value, media) { state = clone(value); assets = clone(media); dirty = false; refreshAll(); },
  media(value, {refresh=true} = {}) { assets = clone(value); if(refresh){renderBasic(); renderOther();} },
  audio(value) { state.bgm = clone(value); renderOther(); },
  change(path, value) { setPath(path, value); markDirty(); renderBasic(); renderOther(); },
  saved(message) { dirty = false; $('#save-status').textContent = message; },
  dirty: markDirty,
  fillComposer,
  editor, bindEditors, mediaField, openDialog, closeDialog, toast, escapeHtml,
};
