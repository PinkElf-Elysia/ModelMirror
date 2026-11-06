// Browser-only worldbook editing; no matching, model calls, or persistence authority.
export const TEMPLATE_FORMAT = 'modelmirror.rpg.worldbook-template/1';
const clone = value => structuredClone(value);
const size = text => [...text].length;
export function newWorldEntry(defaults, id, categoryId = null) {
  return {id,name:'',enabled:true,categoryId,...clone(defaults),regex:false,keywords:[],order:0,depth:0,chance:100,mode:'regular',body:'',contents:[]};
}
export function mergeWorldbook(book, incoming, nextId) {
  if (book.entries.length + incoming.entries.length > 2000 || book.categories.length + incoming.categories.length > 2000) throw Error('最多保留 2000 个条目和分类。');
  const result=clone(book), groups=new Map();
  for(const category of incoming.categories){const id=nextId();groups.set(category.id,id);result.categories.push({...category,id});}
  for(const entry of incoming.entries)result.entries.push({...clone(entry),id:nextId(),categoryId:entry.categoryId===null?null:groups.get(entry.categoryId)});
  return result;
}
export function moveWorldEntry(book, source, target) {
  const next=clone(book), from=next.entries.findIndex(e=>e.id===source), to=next.entries.findIndex(e=>e.id===target);
  if(from<0||to<0||next.entries[from].categoryId!==next.entries[to].categoryId)return next;
  const [entry]=next.entries.splice(from,1);next.entries.splice(to,0,entry);return next;
}
export function searchWorldEntry(entry, query) {
  const q=query.toLowerCase();return [entry.name,...entry.keywords,entry.body,...entry.contents.map(c=>c.text)].some(v=>v.toLowerCase().includes(q));
}
export function createWorldHistory(initial,{limit=24,bytes=32*1024*1024}={}) {
  let value=clone(initial), past=[], future=[];
  const pack=v=>JSON.stringify(v);
  function trim(){let total=0;past=past.slice(-limit).reverse().filter(s=>{total+=s.length*2;return total<=bytes;}).reverse();}
  return {
    read:()=>clone(value),
    replace(next){const old=pack(value);if(old===pack(next))return false;past.push(old);trim();future=[];value=clone(next);return true;},
    amend(next){value=clone(next);},
    checkpoint(before){if(pack(before)===pack(value))return;past.push(pack(before));trim();future=[];},
    undo(){if(!past.length)return false;future.push(pack(value));value=JSON.parse(past.pop());return true;},
    redo(){if(!future.length)return false;past.push(pack(value));trim();value=JSON.parse(future.pop());return true;},
    get canUndo(){return !!past.length;}, get canRedo(){return !!future.length;},
  };
}
const empty=()=>({defaults:{roles:['user'],rule:'or',targets:['system']},categories:[],entries:[]});
const html=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const uid=()=> 'wb-'+crypto.randomUUID();
const svg={normalize:'<svg viewBox="0 0 24 24"><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5ZM20 2v4M18 4h4"/></svg>',more:'<svg viewBox="0 0 24 24"><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></svg>',grip:'<svg viewBox="0 0 24 24"><path d="M8 5h.01M16 5h.01M8 12h.01M16 12h.01M8 19h.01M16 19h.01"/></svg>',full:'<svg viewBox="0 0 24 24"><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/></svg>',trash:'<svg viewBox="0 0 24 24"><path d="M4 6h16M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7"/></svg>'};
export function createWorldbookUI({changed,api,toast}) {
  let history=createWorldHistory(empty()), root=null, workKey='', folded=new Set(), groupFolded=new Set(), contentFolded=new Set(), selected=new Set(), search='', defaultFold=false, popup=null, focusEdit=null, serial=0, drag=null;
  const read=()=>history.read();
  const entry=id=>read().entries.find(e=>e.id===id);
  const messages={AUTHORING_WORLDBOOK_ZERO_WEIGHT:'全部权重均为零，无法归一化；请至少设置一个正权重。',AUTHORING_NUMBER_INVALID:'数值无效，请核对空值、负值和上下限。',AUTHORING_UNKNOWN_FIELD:'模板包含未识别的字段，整批未导入。',AUTHORING_WORLDBOOK_TEMPLATE_FORMAT:'请选择本地世界书模板；原站格式未进行自动转换。'};
  const safe=task=>Promise.resolve().then(task).catch(error=>toast(error.message||messages[error.code]||'操作未完成：'+(error.code||'未知结果')));
  const btn=(label,action,attr='')=>`<button type="button" data-wb="${action}" ${attr}>${label}</button>`;
  function preference(){try{localStorage.setItem('authoring-worldbook-view/'+workKey,JSON.stringify({folded:[...folded],groups:[...groupFolded],contents:[...contentFolded],defaultFold}));}catch{}}
  function load(book,key){closePopup();history=createWorldHistory(book);workKey=key;selected.clear();search='';focusEdit=null;serial++;folded=new Set();groupFolded=new Set();contentFolded=new Set();defaultFold=false;
    try{const v=JSON.parse(localStorage.getItem('authoring-worldbook-view/'+key)||'null');if(v){folded=new Set(v.folded);groupFolded=new Set(v.groups);contentFolded=new Set(v.contents);defaultFold=!!v.defaultFold;}}catch{}
  }
  function finishEdit(){if(focusEdit){history.checkpoint(focusEdit.before);focusEdit=null;updateUndo();}}
  function mutate(fn,{render=true}={}) {finishEdit();const book=read();fn(book);if(history.replace(book)){serial++;changed();}if(render)draw();else updateUndo();}
  function replace(book){finishEdit();if(history.replace(book)){serial++;changed();}draw();}
  function updateUndo(){if(!root)return;const editing=focusEdit&&JSON.stringify(focusEdit.before)!==JSON.stringify(read());root.querySelector('[data-wb="undo"]').disabled=!history.canUndo&&!editing;root.querySelector('[data-wb="redo"]').disabled=!history.canRedo||!!editing;}
  function closePopup(restore=false){if(!popup)return;const {element,trigger,events}=popup;events.abort();element.remove();popup=null;trigger?.setAttribute('aria-expanded','false');if(restore&&trigger?.isConnected)trigger.focus();}
  function menu(trigger,choices){closePopup();const element=document.createElement('div'),events=new AbortController();element.className='wb-menu';element.setAttribute('role','menu');
    element.innerHTML=choices.map((c,i)=>`<button type="button" role="menuitem" data-option="${i}">${html(c[0])}</button>`).join('');trigger.setAttribute('aria-expanded','true');document.body.append(element);const b=trigger.getBoundingClientRect();element.style.left=Math.max(8,Math.min(b.right-element.offsetWidth,innerWidth-element.offsetWidth-8))+'px';element.style.top=Math.max(8,Math.min(b.bottom+4,innerHeight-element.offsetHeight-8))+'px';popup={element,trigger,events};const buttons=[...element.querySelectorAll('button')];buttons[0].focus();
    element.onclick=e=>{const button=e.target.closest('[data-option]');if(button){const action=choices[+button.dataset.option][1];closePopup();safe(action);}};
    element.onkeydown=e=>{const i=buttons.indexOf(document.activeElement);if(e.key==='Escape'){e.preventDefault();closePopup(true);}else if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();buttons[e.key==='Home'?0:e.key==='End'?buttons.length-1:(i+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length].focus();}else if(e.key==='Tab')closePopup();};
    document.addEventListener('pointerdown',e=>{if(!element.contains(e.target)&&!trigger.contains(e.target))closePopup();},{signal:events.signal});
    document.addEventListener('scroll',()=>closePopup(),{capture:true,signal:events.signal});
  }
  function modal(title,body,confirm,onConfirm,{enabled=()=>true,confirmClass='primary'}={}) {
    closePopup();finishEdit();const returnTo=document.activeElement,dialog=document.createElement('dialog');dialog.className='wb-dialog';
    dialog.innerHTML=`<form><header><h2>${html(title)}</h2>${btn('×','close','aria-label="关闭"')}</header><div class="wb-dialog-body">${body}<p class="wb-error" role="alert" hidden></p></div><footer>${btn('取消','cancel')}<button type="submit" class="${confirmClass}">${html(confirm)}</button></footer></form>`;
    document.body.append(dialog);dialog.showModal();const submit=dialog.querySelector('[type="submit"]'),error=dialog.querySelector('.wb-error');
    const close=()=>{dialog.close();dialog.remove();if(returnTo?.isConnected)returnTo.focus();};const update=()=>{submit.disabled=!enabled(dialog);};dialog.addEventListener('input',update);dialog.addEventListener('change',update);
    dialog.querySelector('[data-wb=close]').onclick=close;dialog.querySelector('[data-wb=cancel]').onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
    dialog.querySelector('form').onsubmit=async e=>{e.preventDefault();if(submit.disabled)return;submit.disabled=true;try{await onConfirm(dialog);close();}catch(err){error.hidden=false;error.textContent=err.message||'未完成：'+err.code;update();}};update();return dialog;
  }
  function choices(value,field,options,id='defaults',radio=false){return `<div class="wb-choices">${options.map(([key,label])=>`<label class="wb-choice"><input type="${radio?'radio':'checkbox'}" data-wb-field="${field}" data-id="${id}" value="${key}" ${radio?`name="wb-${id}-${field}"`:''} ${radio?value===key?'checked':'':value.includes(key)?'checked':''}><span>${label}</span></label>`).join('')}</div>`;}
  const roles=v=>choices(v.roles,'roles',[['user','用户'],['assistant','AI']],v.id);
  const rule=v=>choices(v.rule,'rule',[['and','与'],['or','或']],v.id,true);
  const targets=v=>choices(v.targets,'targets',[['system','提示词'],['prefix','前置词'],['suffix','后置词']],v.id);
  function bodyField(e,index=null){const key=e.id+':'+(index??'body'),value=index===null?e.body:e.contents[index].text;
    return `<div class="wb-body-editor" data-editor="${key}"><header><span>内容</span>${btn(svg.full,'fullscreen',`class="wb-icon" aria-label="全屏${index===null?'正文':'内容'+(index+1)}"`)}</header><textarea aria-label="${index===null?'条目正文':'内容'+(index+1)+'正文'}" placeholder="在此处填写世界书内容" data-id="${e.id}" data-wb-field="${index===null?'body':'content'}" ${index===null?'':`data-index="${index}"`}>${html(value)}</textarea><footer><span data-wb-count>${size(value)}</span> / 40000</footer></div>`;
  }
  function card(e,n){const isFold=folded.has(e.id);return `<article class="wb-card" data-wb-card="${e.id}"><header class="wb-entry-header">${btn(svg.grip,'grip',`class="wb-icon wb-grip" aria-label="拖动 No.${n} 排序" title="拖动排序；Alt＋上下方向键也可移动" ${search?'disabled':''}`)}${btn('No.'+n,'fold',`class="wb-entry-title" aria-expanded="${!isFold}"`)}<span class="wb-keyword-preview" title="${html(e.name||e.keywords.join('、'))}">${html(e.name||e.keywords.join('、'))}</span><input type="checkbox" class="wb-select" aria-label="选择 No.${n}" data-wb-select="${e.id}" ${selected.has(e.id)?'checked':''}><label class="wb-switch"><input type="checkbox" role="switch" aria-label="启用 No.${n}" data-id="${e.id}" data-wb-field="enabled" ${e.enabled?'checked':''}><span></span></label>${btn(svg.more,'entry-menu','class="wb-icon" aria-label="条目菜单" aria-haspopup="menu" aria-expanded="false"')}</header>
    <div class="wb-entry-body" ${isFold?'hidden':''}><label class="wb-name">条目名称 <small>（本地可选备注）</small><input data-wb-field="name" data-id="${e.id}" aria-label="条目名称" value="${html(e.name)}"></label><p class="wb-label">触发区域：</p>${roles(e)}<div class="wb-label-row"><span>格式：</span>${btn(e.regex?'切换为选项':'切换为正则','regex','class="wb-text-button"')}</div>${e.regex?'':rule(e)}
    ${e.regex?`<div class="wb-regex">${(e.keywords.length?e.keywords:['']).map((p,i)=>`<input data-wb-field="pattern" data-id="${e.id}" data-index="${i}" aria-label="正则表达式${i+1}" placeholder="请输入正则表达式" value="${html(p)}">`).join('')}<p class="hint">不区分大小写；按${e.rule==='and'?'全部':'任一'}表达式命中。本页仅编辑源码；执行时隔离校验正则，不运行任意脚本。</p></div>`:`<div class="wb-keyword-row"><input placeholder="填写触发关键词后按回车或点击添加即可" aria-label="触发关键词" data-keyword="${e.id}">${btn('添加','keyword','class="primary"')}</div><div class="wb-keywords">${e.keywords.map((word,i)=>btn(`${html(word)} <span aria-hidden="true">×</span>`,'remove-keyword',`data-index="${i}" aria-label="移除关键词 ${html(word)}"`)).join('')}</div>`}
    <p class="wb-label">影响范围：</p>${targets(e)}<p class="hint">前后置词世界书先拼接，再作为用户输入触发提示词世界书；不递归触发。</p>
    <p class="wb-label">其他设置：</p><div class="wb-numbers">${[['order','插入顺序',9999,1],['depth','扫描深度',20,1],['chance','触发概率',100,'any']].map(([key,label,max,step])=>`<label><span>${label}</span><input type="number" min="0" max="${max}" step="${step}" aria-label="${label}" data-wb-field="${key}" data-id="${e.id}" value="${html(e[key])}"></label>`).join('')}</div>
    <div class="wb-mode-row">${choices(e.mode,'mode',[['regular','常规'],['weighted','概率']],e.id,true)}${e.mode==='weighted'?btn(svg.normalize+'归一化','normalize','class="wb-normalize"'):''}</div>
    ${e.mode==='regular'?bodyField(e):`<p class="hint">触发后按内容权重只选一份正文。零权重不参与；归一化合计 100.00%，不改变条目触发概率。</p>${e.contents.map((c,i)=>`<section class="wb-weighted"><header>${btn('内容'+(i+1),'content-fold',`data-index="${i}" aria-expanded="${!contentFolded.has(e.id+':'+i)}"`)}${btn(svg.trash,'remove-content',`class="wb-icon" data-index="${i}" aria-label="删除内容${i+1}"`)}</header><div ${contentFolded.has(e.id+':'+i)?'hidden':''}><label class="wb-weight">内容权重 <input type="number" min="0" step="any" data-wb-field="weight" data-id="${e.id}" data-index="${i}" aria-label="内容${i+1}权重" value="${html(c.weight)}"></label>${bodyField(e,i)}</div></section>`).join('')}${btn('＋','add-content','class="wb-wide" aria-label="添加概率正文"')}`}</div></article>`;
  }
  function draw(target=root){if(!target)return;root=target;closePopup();const book=read(),visible=book.entries.filter(e=>searchWorldEntry(e,search)),ids=new Set(book.entries.map(e=>e.id));selected=new Set([...selected].filter(id=>ids.has(id)));
    root.innerHTML=`<div class="wb-shell"><div class="wb-tools">${btn('下载世界书模板','template')}${btn('导入模板','import')}<input type="file" accept=".json,application/json" data-wb-file hidden></div><input class="wb-search" aria-label="搜索世界书" placeholder="请输入触发关键词/内容" value="${html(search)}"><p class="hint wb-limit">每部作品最多可保存 2000 个条目，每份正文最多 40000 个字符。概率条目每次触发只从候选正文中选取一份。</p><details class="wb-rules"><summary>触发与保存规则</summary><p>插入顺序可设为 0–9999，数值越小越靠前。扫描深度为 0 时只看当前输入，1–20 时额外查看相应数量的完整历史回合。触发概率决定条目是否生效；内容权重决定生效后选哪份正文。</p><p>使用这里下载的 JSON 模板导入世界书。默认样式只用于之后新增的条目；删除分类会把条目移回默认分类，正文仍保留。</p></details>
    ${btn(defaultFold?'展开':'⌃ 收起','defaults-fold',`class="wb-wide wb-muted" aria-expanded="${!defaultFold}"`)}<section class="wb-card wb-defaults" ${defaultFold?'hidden':''}><header>世界书默认样式</header><p class="wb-label">触发区域：</p>${roles(book.defaults)}<p class="wb-label">格式：</p>${rule(book.defaults)}<p class="wb-label">影响范围：</p>${targets(book.defaults)}</section>
    <div class="wb-local-tools"><span>${book.entries.length} 个条目${search?' · 命中 '+visible.length+' 个':''}</span>${btn('撤销','undo',history.canUndo?'':'disabled')}${btn('重做','redo',history.canRedo?'':'disabled')}${btn('导出当前世界书','export')}</div>
    <div class="wb-bulk"><label><input type="checkbox" data-wb-all ${visible.length&&visible.every(e=>selected.has(e.id))?'checked':''}> 选择当前结果</label><span>已选 ${selected.size}</span>${btn('启用','enable-selected',selected.size?'':'disabled')}${btn('停用','disable-selected',selected.size?'':'disabled')}${btn('复制','copy-selected',selected.size?'':'disabled')}</div>
    <div class="wb-list">${book.categories.map(g=>`<section class="wb-group" data-group="${g.id}"><header>${btn((groupFolded.has(g.id)?'›':'⌄')+' '+html(g.name),'group-fold',`aria-expanded="${!groupFolded.has(g.id)}"`)}${btn(svg.more,'group-menu','class="wb-icon" aria-label="分类菜单" aria-haspopup="menu" aria-expanded="false"')}</header><div ${groupFolded.has(g.id)&&!search?'hidden':''}>${visible.filter(e=>e.categoryId===g.id).map(e=>card(e,book.entries.indexOf(e)+1)).join('')}${btn('＋ 新增','new',`class="wb-wide" data-category="${g.id}"`)}</div></section>`).join('')}${book.categories.length?'<p class="wb-default-name">默认</p>':''}${visible.filter(e=>e.categoryId===null).map(e=>card(e,book.entries.indexOf(e)+1)).join('')}</div>${!visible.length?'<p class="wb-empty">'+(search?'没有匹配的条目':'还没有世界书条目')+'</p>':''}
    ${btn('删除全部','delete-all',`class="wb-wide danger" ${book.entries.length?'':'disabled'}`)}${btn('添加分类','category','class="wb-wide"')}${btn('＋ 新增','new','class="wb-wide"')}</div>`;
    bind();
  }
  function add(categoryId){const book=read();if(book.entries.length>=2000)return toast('条目已达 2000 个上限。');if(book.entries.some(e=>!e.keywords.length||e.keywords.some(k=>!k.trim())||(e.mode==='regular'?!e.body.trim():!e.contents.length||e.contents.some(c=>!c.text.trim()))))return toast('世界书关键词或内容不能有空值。请先补齐现有条目。');folded=new Set(book.entries.map(e=>e.id));const next=newWorldEntry(book.defaults,uid(),categoryId);mutate(b=>b.entries.push(next));preference();root.querySelector(`[data-wb-card="${next.id}"]`)?.scrollIntoView({block:'nearest'});}
  function category(id=null){const original=read().categories.find(g=>g.id===id);modal('创建文件夹',`<p class="hint">请输入文件夹名称（最多 10 个字符）</p><input aria-label="文件夹名称" value="${html(original?.name||'')}">`,'创建',d=>{const name=d.querySelector('input').value.trim();if(read().categories.some(g=>g.id!==id&&g.name===name))throw Error('已有同名分类。');mutate(b=>{if(id)b.categories.find(g=>g.id===id).name=name;else {if(b.categories.length>=2000)throw Error('分类已达上限。');b.categories.push({id:uid(),name});}});},{enabled:d=>{const s=d.querySelector('input').value.trim();return size(s)>0&&size(s)<=10;}});}
  function move(id){const options=[{id:null,name:'默认'},...read().categories].filter(g=>g.id!==entry(id).categoryId);if(!options.length)return;
    const dialog=modal('添加至',`<label>分类</label><div class="wb-category-select"><button type="button" role="combobox" aria-label="选择分类" aria-expanded="false" aria-controls="wb-category-options">请选择分类 <span>⌄</span></button><div id="wb-category-options" role="listbox" aria-label="分类选项" hidden>${options.map((g,i)=>`<button type="button" role="option" aria-selected="false" data-group-option="${i}">${html(g.name)}</button>`).join('')}</div><input type="hidden" value=""></div>`,'确认',d=>mutate(b=>{b.entries.find(e=>e.id===id).categoryId=options[+d.querySelector('input').value].id;}),{enabled:d=>d.querySelector('input').value!==''});
    const trigger=dialog.querySelector('[role=combobox]'),list=dialog.querySelector('[role=listbox]'),buttons=[...list.querySelectorAll('button')],value=dialog.querySelector('input');
    const close=()=>{list.hidden=true;trigger.setAttribute('aria-expanded','false');trigger.focus();};
    const open=()=>{list.hidden=false;trigger.setAttribute('aria-expanded','true');buttons[value.value===''?0:+value.value].focus();};trigger.onclick=()=>list.hidden?open():close();
    for(const button of buttons)button.onclick=()=>{value.value=button.dataset.groupOption;trigger.firstChild.textContent=button.textContent+' ';buttons.forEach(b=>b.setAttribute('aria-selected',String(b===button)));close();value.dispatchEvent(new Event('input',{bubbles:true}));};
    trigger.onkeydown=e=>{if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();open();}};
    list.onkeydown=e=>{const i=buttons.indexOf(document.activeElement);if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close();}else if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();buttons[e.key==='Home'?0:e.key==='End'?buttons.length-1:(i+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length].focus();}else if(e.key==='Tab'){list.hidden=true;trigger.setAttribute('aria-expanded','false');}};
    dialog.addEventListener('pointerdown',e=>{if(!list.contains(e.target)&&e.target!==trigger&&!trigger.contains(e.target)){list.hidden=true;trigger.setAttribute('aria-expanded','false');}});trigger.focus();}
  function download(book,name){const url=URL.createObjectURL(new Blob([JSON.stringify({format:TEMPLATE_FORMAT,worldbook:book},null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function fieldInput(element){const {wbField:field,id,index}=element.dataset;if(!field)return;const book=read(),value=id==='defaults'?book.defaults:book.entries.find(e=>e.id===id);if(!value)return;
    if(!focusEdit)focusEdit={before:read()};
    if(['roles','targets'].includes(field)){const next=[...element.closest('.wb-choices').querySelectorAll('input:checked')].map(i=>i.value);if(!next.length){element.checked=true;return toast('至少选择一项。');}value[field]=next;}
    else if(field==='enabled')value.enabled=element.checked;
    else if(field==='weight')value.contents[+index].weight=element.value!==''&&Number.isFinite(Number(element.value))?Number(element.value):element.value;
    else if(['order','depth','chance'].includes(field))value[field]=element.value!==''&&Number.isFinite(Number(element.value))?Number(element.value):element.value;
    else if(field==='content')value.contents[+index].text=element.value;
    else if(field==='pattern')value.keywords[+index]=element.value;
    else value[field]=element.value;
    if(field==='mode'&&value.mode==='weighted'&&!value.contents.length)value.contents=[{text:'',weight:100}];
    history.amend(book);serial++;changed();updateUndo();const count=element.closest('.wb-body-editor')?.querySelector('[data-wb-count]');if(count)count.textContent=size(element.value);if(element.matches('[type=checkbox],[type=radio]')){finishEdit();if(field==='mode'){draw();root.querySelector(`[data-id="${id}"][data-wb-field=mode][value="${value.mode}"]`)?.focus();}else updateUndo();}
  }
  function exitFullscreen(box){box.classList.remove('wb-fullscreen');box.removeAttribute('role');box.removeAttribute('aria-modal');box.removeAttribute('aria-label');const button=box.querySelector('button');button.setAttribute('aria-label',button.dataset.fullLabel||'全屏正文');button.focus();}
  function bind(){root.onfocusin=e=>{if(e.target.matches('[data-wb-field]')){finishEdit();focusEdit={before:read()};}};
    root.onfocusout=e=>{if(e.target.matches('[data-wb-field]'))finishEdit();};
    root.oninput=e=>{if(e.target.matches('[data-wb-field]'))fieldInput(e.target);};
    root.onchange=e=>{if(e.target.matches('[data-wb-select]')){const id=e.target.dataset.wbSelect;e.target.checked?selected.add(id):selected.delete(id);draw();}else if(e.target.matches('[data-wb-all]')){for(const v of read().entries.filter(e=>searchWorldEntry(e,search)))e.target.checked?selected.add(v.id):selected.delete(v.id);draw();}};
    root.querySelector('.wb-search').oninput=e=>{if(e.isComposing)return;const start=e.target.selectionStart;search=e.target.value;draw();const input=root.querySelector('.wb-search');input.focus();input.setSelectionRange(start,start);};
    root.querySelector('.wb-search').oncompositionend=e=>root.querySelector('.wb-search').oninput(e);
    root.querySelector('[data-wb-file]').onchange=e=>safe(async()=>{const file=e.target.files[0];e.target.value='';if(!file)return;if(file.size>16*1024*1024)throw Error('模板超过 16 MiB，未读取。');const version=serial, payload=await file.text();const result=await api('/worldbook/templates/inspect',{payload});if(serial!==version)throw Error('读取期间草稿已变化，请重新选择模板。');modal('导入世界书模板',`<p>已校验 ${result.worldbook.entries.length} 个条目、${result.worldbook.categories.length} 个分类。</p><p>将追加到当前草稿，现有内容保留。条目与分类使用新的独立编号；保存后才写入作品。</p>`,'导入',()=>{if(serial!==version)throw Error('草稿已变化，请重新预检。');replace(mergeWorldbook(read(),result.worldbook,uid));});});
    root.onclick=e=>{const button=e.target.closest('[data-wb]');if(!button||button.disabled)return;const action=button.dataset.wb,id=button.closest('[data-wb-card]')?.dataset.wbCard,group=button.closest('[data-group]')?.dataset.group;
      safe(async()=>{
        if(action==='new')return add(button.dataset.category||null);
        if(action==='category')return category();
        if(action==='group-fold'){groupFolded.has(group)?groupFolded.delete(group):groupFolded.add(group);preference();return draw();}
        if(action==='defaults-fold'){defaultFold=!defaultFold;preference();return draw();}
        if(action==='fold'){folded.has(id)?folded.delete(id):folded.add(id);preference();return draw();}
        if(action==='entry-menu')return menu(button,[[folded.has(id)?'展开':'折叠',()=>{folded.has(id)?folded.delete(id):folded.add(id);preference();draw();}],...read().categories.length?[['添加至',()=>move(id)]]:[],['复制（本地）',()=>{if(read().entries.length>=2000)throw Error('条目已达上限。');mutate(b=>b.entries.push({...clone(entry(id)),id:uid()}));}],['删除',()=>mutate(b=>{b.entries=b.entries.filter(v=>v.id!==id);})]]);
        if(action==='group-menu')return menu(button,[['重命名',()=>category(group)],['删除',()=>{mutate(b=>{b.categories=b.categories.filter(g=>g.id!==group);b.entries.forEach(e=>{if(e.categoryId===group)e.categoryId=null;});});toast('分类已移除，条目保留在默认分类；可撤销。');}]]);
        if(action==='regex')return mutate(b=>{const e=b.entries.find(v=>v.id===id);e.regex=!e.regex;});
        if(action==='keyword'){const input=button.previousElementSibling,text=input.value.trim();if(!text)return;if(size(text)>2000)throw Error('单个关键词最多 2000 字符。');if(entry(id).keywords.length>=200)throw Error('每条最多 200 个关键词。');if(entry(id).keywords.includes(text))return toast('关键词已存在。');mutate(b=>b.entries.find(v=>v.id===id).keywords.push(text));root.querySelector(`[data-keyword="${id}"]`).focus();return;}
        if(action==='remove-keyword')return mutate(b=>b.entries.find(v=>v.id===id).keywords.splice(+button.dataset.index,1));
        if(action==='add-content'){if(entry(id).contents.length>=200)throw Error('每条最多 200 份正文。');return mutate(b=>b.entries.find(v=>v.id===id).contents.push({text:'',weight:100}));}
        if(action==='remove-content')return mutate(b=>b.entries.find(v=>v.id===id).contents.splice(+button.dataset.index,1));
        if(action==='content-fold'){const key=id+':'+button.dataset.index;contentFolded.has(key)?contentFolded.delete(key):contentFolded.add(key);preference();return draw();}
        if(action==='normalize'){finishEdit();const version=serial,book=read();button.disabled=true;try{const result=await api('/worldbook/normalize',{worldbook:book,entryId:id});if(version!==serial)throw Error('计算期间草稿已变化，未覆盖新内容；请再次归一化。');mutate(b=>{b.entries.find(e=>e.id===id).contents=result.contents;});toast('已按两位小数归一化，可撤销。');}finally{if(button.isConnected)button.disabled=false;}return;}
        if(action==='undo'||action==='redo'){finishEdit();if(history[action]()){serial++;changed();draw();}return;}
        if(action==='fullscreen'){const box=button.closest('.wb-body-editor');if(box.classList.contains('wb-fullscreen'))exitFullscreen(box);else{button.dataset.fullLabel=button.getAttribute('aria-label');box.classList.add('wb-fullscreen');box.setAttribute('role','dialog');box.setAttribute('aria-modal','true');box.setAttribute('aria-label','世界书正文全屏编辑');button.setAttribute('aria-label','退出全屏');box.querySelector('textarea').focus();}return;}
        if(action==='delete-all')return modal('删除世界书','<p>确定要删除全部世界书吗？本地草稿可通过撤销恢复。</p><label>请输入“删除全部世界书”确认<input aria-label="删除确认文字"></label>','我确定',()=>mutate(b=>{b.entries=[];}),{enabled:d=>d.querySelector('input').value==='删除全部世界书',confirmClass:'danger'});
        if(action==='copy-selected'){const book=read(),copied=book.entries.filter(e=>selected.has(e.id));if(book.entries.length+copied.length>2000)throw Error('复制后超过 2000 个条目，未复制。');return mutate(b=>b.entries.push(...copied.map(e=>({...clone(e),id:uid()}))));}
        if(action==='enable-selected'||action==='disable-selected')return mutate(b=>b.entries.forEach(e=>{if(selected.has(e.id))e.enabled=action==='enable-selected';}));
        if(action==='import')return root.querySelector('[data-wb-file]').click();
        if(action==='export')return download(read(),'世界书-本地模板.json');
        if(action==='template'){const book=empty(),sample=newWorldEntry(book.defaults,'sample');sample.keywords=['雾港'];sample.body='在此填写世界书内容';book.entries=[sample];return download(book,'世界书模板-本地.json');}
      });
    };
    root.onkeydown=e=>{if(e.isComposing)return;const fullscreen=e.target.closest('.wb-fullscreen');if(fullscreen&&e.key==='Tab'){const controls=[...fullscreen.querySelectorAll('button,textarea')],i=controls.indexOf(document.activeElement);if((e.shiftKey&&i===0)||(!e.shiftKey&&i===controls.length-1)){e.preventDefault();controls[e.shiftKey?controls.length-1:0].focus();}}if(e.target.matches('[data-keyword]')&&e.key==='Enter'){e.preventDefault();e.target.nextElementSibling.click();}if(e.key==='Escape'&&e.target.closest('.wb-fullscreen')){e.preventDefault();e.stopPropagation();exitFullscreen(e.target.closest('.wb-fullscreen'));}if(e.altKey&&['ArrowUp','ArrowDown'].includes(e.key)&&e.target.matches('.wb-grip')){e.preventDefault();const id=e.target.closest('[data-wb-card]').dataset.wbCard,book=read(),peers=book.entries.filter(v=>v.categoryId===entry(id).categoryId),i=peers.findIndex(v=>v.id===id),target=peers[i+(e.key==='ArrowUp'?-1:1)];if(target){replace(moveWorldEntry(book,id,target.id));root.querySelector(`[data-wb-card="${id}"] .wb-grip`).focus();}}};
    root.onpointerdown=e=>{const grip=e.target.closest('.wb-grip');if(!grip||grip.disabled||e.button!==0)return;finishEdit();drag={id:grip.closest('[data-wb-card]').dataset.wbCard,startY:e.clientY,target:null,pointer:e.pointerId};grip.setPointerCapture(e.pointerId);};
    root.onpointermove=e=>{if(!drag||e.pointerId!==drag.pointer||Math.abs(e.clientY-drag.startY)<5)return;const card=document.elementFromPoint(e.clientX,e.clientY)?.closest('[data-wb-card]');root.querySelector('.wb-drop')?.classList.remove('wb-drop');if(card&&entry(card.dataset.wbCard)?.categoryId===entry(drag.id)?.categoryId){drag.target=card.dataset.wbCard;card.classList.add('wb-drop');}};
    root.onpointerup=e=>{if(!drag||e.pointerId!==drag.pointer)return;const {id,target}=drag;drag=null;root.querySelector('.wb-drop')?.classList.remove('wb-drop');if(target&&target!==id)replace(moveWorldEntry(read(),id,target));};root.onpointercancel=()=>{drag=null;root.querySelector('.wb-drop')?.classList.remove('wb-drop');};
  }
  return {load,read,render:draw,finish:finishEdit};
}
