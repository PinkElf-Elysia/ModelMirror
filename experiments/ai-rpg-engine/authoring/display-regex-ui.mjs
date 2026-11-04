const esc=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clone=value=>structuredClone(value);
const labels=[['assistant','AI输出'],['user','用户输入'],['all','全部']];
const flags=[['g','全部匹配'],['i','忽略大小写'],['m','多行边界'],['s','点匹配换行'],['u','Unicode']];
export function createDisplayRegexUI({changed,toast}){
  let rules=[],root=null,opened=null,help=false,popup=null,drag=null,deleted=null,workKey=null;
  const item=id=>rules.find(rule=>rule.id===id);
  function closeMenu(restore=false){if(!popup)return;const old=popup;popup=null;old.events.abort();old.element.remove();old.trigger.setAttribute('aria-expanded','false');if(restore&&old.trigger.isConnected)old.trigger.focus();}
  function draw(){
    if(!root)return;closeMenu();
    root.innerHTML='<section class="field regex-editor"><h3>正则脚本</h3><p class="hint">将匹配的文字替换为文本或 HTML，仅用于显示。<button type="button" class="text-button" data-regex-action="help" aria-expanded="'+help+'">'+(help?'折叠':'展开')+'</button></p><div class="regex-help"'+(help?'':' hidden')+'><p>匹配模式只填写正则本体，不带 /…/g。替换可用 $1 / $2、$&amp; 或 HTML；留空表示删除，填空格表示替换为空格。</p><p>本地始终仅渲染，不改变原始输入、回复或历史。按列表顺序执行一次；会匹配 HTML 标签与属性，请先在“回复回放”检查。</p><p>本地规则：单次替换限时1秒；输入和每步结果最多2 MiB，单规则最多10万次匹配。出错或超限时整批替换不生效，保留替换前显示并给出诊断。</p><p>抓手可拖动排序，也支持 Alt＋上下方向键。新规则默认 g / 全部；未带范围的旧规则按 AI 输出执行。原站内部 flags 与排序算法未确认。</p></div><div class="regex-list">'+rules.map((rule,index)=>card(rule,index)).join('')+'</div><button type="button" class="regex-add" data-regex-action="add"'+(rules.length>=100?' disabled':'')+'>＋ 添加脚本</button><div class="regex-footer"><span class="hint">'+rules.length+'/100 条 · 保存作品后持久化</span>'+(deleted?'<button type="button" data-regex-action="undo">撤销删除</button>':'')+'</div></section>';
  }
  function card(rule,index){
    const n=String(index+1).padStart(2,'0'),expanded=opened===rule.id;
    return '<article class="regex-card" data-regex-id="'+rule.id+'"><header class="regex-header"><button type="button" class="regex-grip" data-regex-action="grip" aria-label="拖动正则 '+n+' 排序" title="拖动；Alt＋上下方向键">⠿</button><button type="button" class="regex-title" data-regex-action="fold" aria-expanded="'+expanded+'" title="'+esc(rule.name)+'">No.'+n+'</button><button type="button" class="regex-more" data-regex-action="menu" aria-label="正则 '+n+' 菜单" aria-haspopup="menu" aria-expanded="false">⋮</button><label class="regex-switch"><input type="checkbox" role="switch" aria-label="启用正则 '+n+'" data-regex-field="enabled"'+(rule.enabled?' checked':'')+'><span></span></label></header><div class="regex-content"'+(expanded?'':' hidden')+'>'+[['name','标题','为脚本起一个名字'],['pattern','匹配模式','请输入正则表达式模式'],['replacement','替换文本','可留空（删除匹配内容），也可填空格或其他文本']].map(([key,label,placeholder])=>'<label>'+label+'<input data-regex-field="'+key+'" aria-label="正则 '+n+' '+label+'" placeholder="'+placeholder+'" value="'+esc(rule[key])+'" spellcheck="false"></label>').join('')+'<fieldset><legend>影响范围</legend><div class="regex-options">'+labels.map(([value,label])=>'<label><input type="radio" name="regex-scope-'+rule.id+'" data-regex-field="scope" value="'+value+'"'+((rule.scope??'assistant')===value?' checked':'')+'>'+label+'</label>').join('')+'</div></fieldset><p class="regex-render-only">✓ 仅渲染 · 不影响原始内容或历史记忆</p><fieldset><legend>匹配标志（本地明确设置）</legend><div class="regex-flags">'+flags.map(([value,label])=>'<label><input type="checkbox" data-regex-flag="'+value+'"'+(rule.flags.includes(value)?' checked':'')+'><code>'+value+'</code> '+label+'</label>').join('')+'</div></fieldset></div></article>';
  }
  function menu(trigger,id){
    closeMenu();const element=document.createElement('div'),events=new AbortController();element.className='regex-menu';element.setAttribute('role','menu');
    element.innerHTML='<button type="button" role="menuitem" data-choice="fold">'+(opened===id?'折叠':'展开')+'</button><button type="button" role="menuitem" data-choice="delete">删除</button>';
    document.body.append(element);trigger.setAttribute('aria-expanded','true');popup={element,trigger,events};const b=trigger.getBoundingClientRect();element.style.left=Math.max(8,Math.min(b.right-element.offsetWidth,innerWidth-element.offsetWidth-8))+'px';element.style.top=Math.max(8,Math.min(b.bottom+4,innerHeight-element.offsetHeight-8))+'px';
    const buttons=[...element.querySelectorAll('button')];buttons[0].focus();
    element.onclick=e=>{const choice=e.target.closest('[data-choice]')?.dataset.choice;if(!choice)return;closeMenu();if(choice==='fold'){opened=opened===id?null:id;draw();root.querySelector('[data-regex-id="'+id+'"] .regex-title')?.focus();}else{deleted={rule:clone(item(id)),index:rules.findIndex(rule=>rule.id===id)};rules=rules.filter(rule=>rule.id!==id);if(opened===id)opened=null;changed();draw();root.querySelector('[data-regex-action="undo"]').focus();toast('已移除显示规则，可撤销删除；尚未保存作品。');}};
    element.onkeydown=e=>{const index=buttons.indexOf(document.activeElement);if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closeMenu(true);}else if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();buttons[e.key==='Home'?0:e.key==='End'?buttons.length-1:(index+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length].focus();}else if(e.key==='Tab')closeMenu();};
    document.addEventListener('pointerdown',e=>{if(!element.contains(e.target)&&!trigger.contains(e.target))closeMenu();},{signal:events.signal});
  }
  function move(id,target){
    const from=rules.findIndex(rule=>rule.id===id),to=rules.findIndex(rule=>rule.id===target);if(from<0||to<0||from===to)return;
    const [rule]=rules.splice(from,1);rules.splice(to,0,rule);changed();draw();root.querySelector('[data-regex-id="'+id+'"] .regex-grip')?.focus();
  }
  function bind(){
    root.onclick=e=>{
      const button=e.target.closest('[data-regex-action]');if(!button)return;const action=button.dataset.regexAction,id=button.closest('[data-regex-id]')?.dataset.regexId;
      if(action==='help'){help=!help;draw();root.querySelector('[data-regex-action="help"]').focus();}
      else if(action==='add'&&rules.length<100){const rule={id:crypto.randomUUID(),name:'',enabled:true,pattern:'',flags:'g',replacement:'',scope:'all'};rules.push(rule);opened=rule.id;changed();draw();root.querySelector('[data-regex-id="'+rule.id+'"] [data-regex-field="name"]').focus();}
      else if(action==='fold'){opened=opened===id?null:id;draw();root.querySelector('[data-regex-id="'+id+'"] .regex-title').focus();}
      else if(action==='menu')menu(button,id);
      else if(action==='undo'&&deleted&&rules.length<100){rules.splice(Math.min(deleted.index,rules.length),0,deleted.rule);opened=deleted.rule.id;deleted=null;changed();draw();}
    };
    root.oninput=e=>{const key=e.target.dataset.regexField;if(['name','pattern','replacement'].includes(key)){item(e.target.closest('[data-regex-id]').dataset.regexId)[key]=e.target.value;changed();}};
    root.onchange=e=>{const rule=item(e.target.closest('[data-regex-id]')?.dataset.regexId);if(!rule)return;const key=e.target.dataset.regexField;if(key==='enabled')rule.enabled=e.target.checked;else if(key==='scope')rule.scope=e.target.value;else if(e.target.dataset.regexFlag){const flag=e.target.dataset.regexFlag;rule.flags=e.target.checked?rule.flags+flag:rule.flags.replace(flag,'');}else return;changed();};
    root.onkeydown=e=>{if(e.isComposing||!e.altKey||!['ArrowUp','ArrowDown'].includes(e.key)||!e.target.matches('.regex-grip'))return;e.preventDefault();const id=e.target.closest('[data-regex-id]').dataset.regexId,i=rules.findIndex(rule=>rule.id===id),target=rules[i+(e.key==='ArrowUp'?-1:1)];if(target)move(id,target.id);};
    root.onpointerdown=e=>{const grip=e.target.closest('.regex-grip');if(!grip||e.button!==0)return;drag={id:grip.closest('[data-regex-id]').dataset.regexId,y:e.clientY,pointer:e.pointerId,target:null};grip.setPointerCapture(e.pointerId);};
    root.onpointermove=e=>{if(!drag||e.pointerId!==drag.pointer||Math.abs(e.clientY-drag.y)<5)return;const card=document.elementFromPoint(e.clientX,e.clientY)?.closest('[data-regex-id]');root.querySelector('.regex-drop')?.classList.remove('regex-drop');drag.target=card?.dataset.regexId||null;if(card&&root.contains(card)&&drag.target!==drag.id)card.classList.add('regex-drop');};
    const finish=e=>{if(!drag||drag.pointer!==e.pointerId)return;const old=drag;drag=null;root.querySelector('.regex-drop')?.classList.remove('regex-drop');if(e.type==='pointerup'&&old.target)move(old.id,old.target);};
    root.onpointerup=finish;root.onpointercancel=finish;
  }
  return {load(value,key){closeMenu();rules=clone(value);if(workKey!==key){opened=null;help=false;}if(!item(opened))opened=null;deleted=null;workKey=key;},read:()=>clone(rules),render(element){root=element;draw();bind();}};
}
