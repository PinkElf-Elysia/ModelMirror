import {DIALOGUE_LIMITS as limits,openingOptions,withOpeningOptions,newPreset,newShortcut,moveOpening,presetIssues,shortcutIssues,dialoguePreview} from './dialogue.mjs';

const clone=value=>structuredClone(value);
const esc=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const trash='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7"/></svg>';
const action=(name,key,extra='',content=esc(name))=>`<button type="button" data-dialogue-action="${key}" ${extra}>${content}</button>`;
const remove=(name,key,extra='')=>action(name,key,`class="dialogue-trash" aria-label="${esc(name)}" ${extra}`,trash);
const count=(value,max)=>`<small class="dialogue-count${[...value].length>max?' danger':''}">${[...value].length}/${max}</small>`;
function field(label,kind,value,max,extra='',textarea=false) {
  return `<label class="dialogue-field"><span>${label}</span>${textarea?`<textarea rows="3"`:'<input type="text"'} data-dialogue-field="${kind}" maxlength="${max}" ${extra}${textarea?`>${esc(value)}</textarea>`:` value="${esc(value)}">`}${count(value,max)}</label>`;
}

export function createDialogueUI({changed,toast,fillInput,settled=()=>{}}) {
  let data=null,root=null,openingBefore=null,openShortcuts=new Set(),openPresets=new Set(),errors=new Map(),drag=null,selected=null;
  const read=()=>clone(data);
  const notify=()=>changed();
  const fail=(id,issues)=>{errors.set(id,issues.length>0);if(issues.length)toast(id==='opening'?'开场白超过本地容量，未保存；原文保留。':'请填写完整内容，并核对字数限制。');return !!issues.length;};
  const focus=selector=>root.querySelector(selector)?.focus();
  function openingSection() {
    const options=openingOptions(data.dialogue),editing=openingBefore!==null;
    return `<section class="dialogue-opening${editing?' editing':''}"><header><h3>◉ 对话开场白</h3><div class="row">${editing?action('取消','opening-cancel')+action('保存','opening-save','class="primary"'):action('编辑','opening-edit','class="text-button"')}</div></header>`+
      (editing?`<p>开场白 · ${options.length} / 40</p><div class="dialogue-opening-list">`+options.map((text,i)=>`<div class="dialogue-opening-row" data-opening-row="${i}">${action('拖动开场白 '+(i+1),'grip','class="dialogue-grip" title="拖动排序；Alt＋上下箭头也可排序" aria-label="拖动开场白 '+(i+1)+'"','⠿')}<input data-dialogue-field="opening" data-index="${i}" aria-label="开场白 ${i+1}" placeholder="在这里输入一段预设开场白供用户点击发送第一句话(选填，影响AI回复)" value="${esc(text)}">${remove('删除开场白 '+(i+1),'opening-remove',`data-index="${i}"`)}</div>`).join('')+`</div>${errors.get('opening')?'<p class="dialogue-error" role="alert">单项最多200000字符，内容尚未保存。</p>':''}${action('添加选项','opening-add',options.length>=limits.openings?'disabled':'')}<p class="hint">页内保存后，仍需顶部保存到作品；编辑期间暂停自动保存。</p>`:
        options.length?`<div class="dialogue-opening-summary">${options.map(t=>`<p>${esc(t)||'（空选项）'}</p>`).join('')}</div>`:'')+'</section>';
  }
  function shortcutSection() {
    return '<section class="field dialogue-section"><h3>快捷回复指令</h3><p class="hint">配置常用指令内容；预览时点击填入输入框，由玩家发送。</p>'+`<p class="hint">最多可添加 ${data.shortcuts.length}/10 个快捷命令</p><div class="dialogue-stack">`+data.shortcuts.map(s=>{
      const open=openShortcuts.has(s.id);
      return `<article class="dialogue-card" data-shortcut="${s.id}"><header>${action(open?'收起并保存':'展开快捷指令 '+s.name,'shortcut-toggle',`class="dialogue-chevron" aria-expanded="${open}" aria-label="${open?'收起并保存':'展开快捷指令 '+esc(s.name)}"`,open?'⌄':'›')}<span>${open?'点击收起图标自动保存并折叠':esc(s.name)||'未命名指令'}</span>${remove('删除快捷指令 '+(s.name||'未命名'),'shortcut-remove')}</header>`+(open?`<div class="dialogue-body">${field('快捷命令','shortcut-name',s.name,15,'placeholder="输入快捷命令，如：help"')}${field('输出内容','shortcut-text',s.text,100,'placeholder="输入要发送给AI的内容"',true)}${errors.get(s.id)?'<p class="dialogue-error" role="alert">命令和内容不能为空；分别最多15字、100字。</p>':''}<div class="dialogue-actions">${action('确认','shortcut-toggle','class="primary"')}</div></div>`:'')+'</article>';
    }).join('')+`</div><div class="dialogue-actions">${action('添加指令','shortcut-add','class="primary"'+(data.shortcuts.length>=10?' disabled':''))}</div></section>`;
  }
  function presetSection() {
    return `<section class="dialogue-section"><h3>预设对话</h3><p class="hint">配置预设对话，包括对话内容和开场白选择，用户可以快速开始对话。</p><p class="hint">当前数量: ${data.dialogue.presets.length}/10</p><div class="dialogue-stack">`+data.dialogue.presets.map(p=>{
      const open=openPresets.has(p.id);
      const heading=open?action('点击收起图标自动保存并折叠','preset-toggle','class="text-button"','⌄　点击收起图标自动保存并折叠'):action('展开预设 '+(p.title||'未命名'),'preset-toggle','class="dialogue-preset-title"',`›　<strong>${esc(p.title)||'未命名预设'}</strong><span>（${p.messages.length}个对话, ${p.openings.length}个开场白）</span>`);
      return `<article class="dialogue-card" data-preset="${p.id}"><header>${heading}${!open?remove('删除预设 '+(p.title||'未命名'),'preset-remove'):''}</header>`+(open?`<div class="dialogue-body"><div class="dialogue-title-field">${field('对话标题','preset-title',p.title,30)}${remove('删除预设 '+(p.title||'未命名'),'preset-remove')}</div><p>对话内容</p>`+p.messages.map((pair,i)=>`<div class="dialogue-pair" data-pair="${i}"><header><span>对话 ${i+1}</span>${p.messages.length>1?remove('删除对话 '+(i+1),'pair-remove',`data-index="${i}"`):''}</header>${field('提问','pair-user',pair.user,1000,`aria-label="对话 ${i+1} 提问"`)}${field('回答','pair-assistant',pair.assistant,10000,`aria-label="对话 ${i+1} 回答"`,true)}</div>`).join('')+action('＋ 添加对话','pair-add','class="primary"'+(p.messages.length>=limits.pairs?' disabled':''))+'<div class="dialogue-preset-openings">'+p.openings.map((s,i)=>`<div class="dialogue-choice-row">${field('开场白 '+(i+1),'preset-opening',s,50,`data-index="${i}"`)}${p.openings.length>1?remove('删除预设开场白 '+(i+1),'preset-opening-remove',`data-index="${i}"`):''}</div>`).join('')+'</div>'+action('＋ 添加开场白','preset-opening-add','class="primary"'+(p.openings.length>=limits.presetOpenings?' disabled':''))+(errors.get(p.id)?'<p class="dialogue-error" role="alert">请填写完整的对话信息：标题、每轮提问与回答不能为空。</p>':'')+`<div class="dialogue-actions">${action('确认','preset-toggle','class="primary"')}</div></div>`:'')+'</article>';
    }).join('')+`</div><div class="dialogue-actions">${action('新增预设对话','preset-add','class="primary"'+(data.dialogue.presets.length>=10?' disabled':''))}</div></section>`;
  }
  function render(element=root) {
    root=element;if(!root||!data)return;
    root.innerHTML=shortcutSection()+`<section class="field dialogue-section"><p>选择对话方式</p><div class="dialogue-mode">${['presets','opening'].map(mode=>action(mode==='presets'?'预设对话':'开场白','mode',`data-mode="${mode}" aria-pressed="${data.dialogue.mode===mode}"`)).join('')}</div>${data.dialogue.mode==='opening'?'<p class="hint dialogue-divider">聊天增强</p>'+openingSection():presetSection()}</section>`;
    root.onclick=event=>{
      const b=event.target.closest('[data-dialogue-action]');if(!b||b.disabled)return;
      const key=b.dataset.dialogueAction,index=Number(b.dataset.index);
      const shortcut=data.shortcuts.find(s=>s.id===b.closest('[data-shortcut]')?.dataset.shortcut);
      const preset=data.dialogue.presets.find(p=>p.id===b.closest('[data-preset]')?.dataset.preset);
      let target=null,modified=false;
      if(key==='mode'){
        if(openingBefore!==null){toast('请先保存或取消当前开场白编辑。');return;}
        data.dialogue.mode=b.dataset.mode;modified=true;
        if(data.dialogue.mode==='presets'&&!data.dialogue.presets.length){const p=newPreset(crypto.randomUUID());data.dialogue.presets.push(p);openPresets.add(p.id);}
      }
      if(key==='opening-edit'){openingBefore=clone(data.dialogue);target=openingOptions(data.dialogue).length?'[data-dialogue-field="opening"]':'[data-dialogue-action="opening-add"]';}
      if(key==='opening-cancel'){data.dialogue=openingBefore;openingBefore=null;errors.delete('opening');notify();settled();target='[data-dialogue-action="opening-edit"]';}
      if(key==='opening-save'){
        const options=openingOptions(data.dialogue);
        if(fail('opening',options.filter(s=>[...s].length>limits.openingText))) {render();focus(`[data-dialogue-field="opening"][data-index="${options.findIndex(s=>[...s].length>limits.openingText)}"]`);return;}
        data.dialogue=withOpeningOptions(data.dialogue,options);openingBefore=null;errors.delete('opening');modified=true;target='[data-dialogue-action="opening-edit"]';
      }
      if(key==='opening-add'){const options=openingOptions(data.dialogue);if(options.length>=40)return;options.push('');data.dialogue=withOpeningOptions(data.dialogue,options);modified=true;target=`[data-dialogue-field="opening"][data-index="${options.length-1}"]`;}
      if(key==='opening-remove'){const options=openingOptions(data.dialogue);options.splice(index,1);data.dialogue=withOpeningOptions(data.dialogue,options);modified=true;target=options.length?`[data-dialogue-field="opening"][data-index="${Math.min(index,options.length-1)}"]`:'[data-dialogue-action="opening-add"]';}
      if(key==='shortcut-add'){if(data.shortcuts.length>=10)return;const s=newShortcut(crypto.randomUUID());data.shortcuts.push(s);openShortcuts.add(s.id);modified=true;target=`[data-shortcut="${s.id}"] input`;}
      if(key==='shortcut-remove'){data.shortcuts=data.shortcuts.filter(s=>s!==shortcut);openShortcuts.delete(shortcut.id);modified=true;target='[data-dialogue-action="shortcut-add"]';}
      if(key==='shortcut-toggle'){
        if(openShortcuts.has(shortcut.id)){
          const issues=shortcutIssues(shortcut);
          if(fail(shortcut.id,issues)){render();focus(`[data-shortcut="${shortcut.id}"] [data-dialogue-field="shortcut-${issues[0].path.slice(1)}"]`);return;}
          openShortcuts.delete(shortcut.id);
        }else openShortcuts.add(shortcut.id);
        target=`[data-shortcut="${shortcut.id}"] button`;
      }
      if(key==='preset-add'){if(data.dialogue.presets.length>=10)return;const p=newPreset(crypto.randomUUID());data.dialogue.presets.push(p);openPresets.add(p.id);modified=true;target=`[data-preset="${p.id}"] input`;}
      if(key==='preset-remove'){data.dialogue.presets=data.dialogue.presets.filter(p=>p!==preset);openPresets.delete(preset.id);modified=true;target='[data-dialogue-action="preset-add"]';}
      if(key==='preset-toggle'){
        if(openPresets.has(preset.id)){
          const issues=presetIssues(preset);
          if(fail(preset.id,issues)){
            const [,field,pair,role]=issues[0].path.split('/');
            const selector=field==='title'?'[data-dialogue-field="preset-title"]':pair===undefined?'[data-dialogue-action="pair-add"]':`[data-pair="${pair}"] [data-dialogue-field="pair-${role}"]`;
            render();focus(`[data-preset="${preset.id}"] ${selector}`);return;
          }
          openPresets.delete(preset.id);
        }else openPresets.add(preset.id);
        target=`[data-preset="${preset.id}"] button`;
      }
      if(key==='pair-add'){if(preset.messages.length>=limits.pairs)return;preset.messages.push({user:'',assistant:''});modified=true;target=`[data-preset="${preset.id}"] [data-pair="${preset.messages.length-1}"] input`;}
      if(key==='pair-remove'&&preset.messages.length>1){preset.messages.splice(index,1);modified=true;target=`[data-preset="${preset.id}"] [data-pair="${Math.min(index,preset.messages.length-1)}"] input`;}
      if(key==='preset-opening-add'){if(preset.openings.length>=limits.presetOpenings)return;preset.openings.push('');modified=true;target=`[data-preset="${preset.id}"] [data-dialogue-field="preset-opening"][data-index="${preset.openings.length-1}"]`;}
      if(key==='preset-opening-remove'&&preset.openings.length>1){preset.openings.splice(index,1);modified=true;target=`[data-preset="${preset.id}"] [data-dialogue-field="preset-opening"][data-index="${Math.min(index,preset.openings.length-1)}"]`;}
      if(key==='grip')return;
      if(modified)notify();render();if(target)focus(target);
    };
    root.oninput=event=>{
      const el=event.target,key=el.dataset.dialogueField;if(!key)return;
      const shortcut=data.shortcuts.find(s=>s.id===el.closest('[data-shortcut]')?.dataset.shortcut),preset=data.dialogue.presets.find(p=>p.id===el.closest('[data-preset]')?.dataset.preset);
      if(key==='opening'){const options=openingOptions(data.dialogue);options[Number(el.dataset.index)]=el.value;data.dialogue=withOpeningOptions(data.dialogue,options);}
      if(key==='shortcut-name')shortcut.name=el.value;
      if(key==='shortcut-text')shortcut.text=el.value;
      if(key==='preset-title')preset.title=el.value;
      if(key.startsWith('pair-'))preset.messages[Number(el.closest('[data-pair]').dataset.pair)][key.slice(5)]=el.value;
      if(key==='preset-opening')preset.openings[Number(el.dataset.index)]=el.value;
      const counter=el.closest('.dialogue-field')?.querySelector('.dialogue-count');if(counter){counter.textContent=`${[...el.value].length}/${el.maxLength}`;counter.classList.toggle('danger',[...el.value].length>el.maxLength);}
      notify();
    };
    const move=(from,to)=>{data.dialogue=withOpeningOptions(data.dialogue,moveOpening(openingOptions(data.dialogue),from,to));notify();render();focus(`[data-opening-row="${to}"] .dialogue-grip`);};
    root.onkeydown=event=>{
      if(event.isComposing||!event.altKey||!['ArrowUp','ArrowDown'].includes(event.key)||!event.target.matches('.dialogue-grip'))return;
      event.preventDefault();const from=Number(event.target.closest('[data-opening-row]').dataset.openingRow),to=from+(event.key==='ArrowUp'?-1:1);if(to>=0&&to<openingOptions(data.dialogue).length)move(from,to);
    };
    root.onpointerdown=event=>{const grip=event.target.closest('.dialogue-grip');if(!grip||event.button!==0)return;drag={from:Number(grip.closest('[data-opening-row]').dataset.openingRow),to:null,start:event.clientY};grip.setPointerCapture(event.pointerId);};
    root.onpointermove=event=>{if(!drag||Math.abs(event.clientY-drag.start)<4)return;const target=document.elementFromPoint(event.clientX,event.clientY)?.closest('[data-opening-row]');root.querySelectorAll('.drag-target').forEach(el=>el.classList.remove('drag-target'));if(target&&root.contains(target)){drag.to=Number(target.dataset.openingRow);target.classList.add('drag-target');}};
    root.onpointerup=()=>{if(!drag)return;const {from,to}=drag;drag=null;root.querySelectorAll('.drag-target').forEach(el=>el.classList.remove('drag-target'));if(to!==null&&from!==to)move(from,to);};
    root.onpointercancel=()=>{drag=null;root.querySelectorAll('.drag-target').forEach(el=>el.classList.remove('drag-target'));};
  }
  function preview(element) {
    const snapshot=JSON.stringify(data),result=dialoguePreview(data,selected);
    const issueCount=data.shortcuts.filter(s=>shortcutIssues(s).length).length;
    element.innerHTML='<h2>开场与指令预览</h2><p class="hint">预览当前编辑内容。选择后填入玩家输入，点击发送才开始对话。</p>'+
      (data.dialogue.mode==='presets'?'<p>选择预设对话</p><div class="dialogue-stack">'+data.dialogue.presets.map(p=>`<button type="button" data-pick-preset="${p.id}" aria-pressed="${result.presetId===p.id}"${presetIssues(p).length?' disabled':''}>${esc(p.title)||'未完成预设'}</button>`).join('')+'</div>':'')+
      (result.messages.length?'<div class="dialogue-preview-history">'+result.messages.map(m=>`<div><strong>${m.role==='user'?'用户':'AI'} · 预设上下文</strong><pre>${esc(m.content)}</pre></div>`).join('')+'</div>':'')+
      `<p>选择一段开场白</p><div class="dialogue-stack">${result.choices.map((text,i)=>`<button type="button" data-pick-opening="${i}">${esc(text)}</button>`).join('')||'<p class="hint">暂无可选开场白。</p>'}</div><p>快捷回复指令</p><div class="row">${result.shortcuts.map(s=>`<button type="button" data-pick-shortcut="${s.id}" title="${esc(s.text)}">${esc(s.name)}</button>`).join('')||'<span class="hint">暂无可用指令。</span>'}</div>${issueCount?'<p class="dialogue-error">有未完成或超长指令，未展示为可用按钮；原文仍保留。</p>':''}<p class="hint">点击只替换输入框，不发送、不申请额度。空开场白不会成为选项。</p>`;
    element.onclick=event=>{
      const button=event.target.closest('button');if(!button||button.disabled)return;
      if(JSON.stringify(data)!==snapshot){toast('配置已变化，请刷新预览后再选择。');return;}
      if(button.dataset.pickPreset){selected=button.dataset.pickPreset;preview(element);return;}
      if(button.dataset.pickOpening!==undefined)fillInput(result.choices[Number(button.dataset.pickOpening)]);
      if(button.dataset.pickShortcut)fillInput(result.shortcuts.find(s=>s.id===button.dataset.pickShortcut).text);
    };
  }
  return {read,render,preview,editing:()=>openingBefore!==null,load(enhancements){data={dialogue:clone(enhancements.dialogue),shortcuts:clone(enhancements.shortcuts)};openingBefore=null;openPresets.clear();openShortcuts.clear();errors.clear();selected=null;}};
}
