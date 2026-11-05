import {mountVariableFrame,panelProblem} from './variable-frame-ui.mjs';
const defaults=()=>({enabled:false,skeleton:'{\n  "地点": "雾港码头",\n  "体力": 80,\n  "背包": ["航海图"],\n  "任务完成": false\n}',template:'<style>\n.panel { padding: 20px; border: 1px solid #bdd3dc; border-radius: 14px; background: #f2f8fa; color: #183847; font-family: system-ui; }\n.panel h2 { margin-top: 0; }\n.panel [data-copied] { color: #19815a; }\n</style>\n<section class="panel">\n  <h2>{{地点}}</h2>\n  <p>体力：{{体力}}</p>\n  <ul>{{#each 背包}}<li>{{this}}</li>{{/each}}</ul>\n  {{#if 任务完成}}<p>任务已完成</p>{{else}}<p>任务进行中</p>{{/if}}\n  <button data-panel-copy="{{地点}}">复制地点</button>\n</section>',instructions:''});
export function createVariablePanelUI({editor,api,getWork,notice}) {
 let value=null,host=null,prepared=null,cleanup=null,generation=0,endFullscreen=null;
 const clear=()=>{generation++;cleanup?.();cleanup=null;if(prepared){void api('/presentations/release',{token:prepared.token}).catch(()=>{});prepared=null;}};
 const invalidate=()=>{clear();if(host){host.querySelector('[data-panel-preview]')?.replaceChildren();const status=host.querySelector('[data-panel-status]');if(status)status.textContent='';}};
 const changed=()=>{editor.dirty();invalidate();};
 function codeField(key,label,language) {
  return '<div class="variable-code" data-panel-code="'+key+'"><header><label for="variable-'+key+'">'+label+'</label><span>'+language+'</span><button type="button" data-panel-full aria-label="'+label+'全屏" title="全屏">⛶</button></header><div class="variable-code-body"><pre aria-hidden="true" data-panel-lines></pre><textarea id="variable-'+key+'" aria-label="'+label+'" spellcheck="false"></textarea></div><footer><span data-panel-count></span><span>原样保存</span></footer></div>';
 }
 function render(target) {
  endFullscreen?.();clear();host=target;const active=value?.enabled??false;
  host.innerHTML='<div class="field"><div class="field-row"><div><h3>使用变量面板</h3><p class="hint">用数据记录角色与世界状态，在对话中展示可更新的面板。</p></div><label class="switch-label"><input type="checkbox" role="switch" aria-label="使用变量面板"'+(active?' checked':'')+'></label></div><div data-panel-fields'+(active?'':' hidden')+'><p class="hint">先填写初始数据，再设计面板皮肤。每轮剧情完成后更新，数据异常时保留上一份状态。</p>'+codeField('skeleton','数据骨架','JSON')+'<p class="hint">JSON 对象定义初始状态；字段名清晰，尽量减少嵌套。</p>'+codeField('template','面板皮肤','HTML / Handlebars')+'<p class="hint">支持字段、条件、列表和比较。CSS 写在皮肤中；图片使用 asset://别名。<a href="/creator-guide.html#variable-panel" target="_blank" rel="noopener">模板与复制用法 ↗</a></p>'+editor.editor('panelInstructions','变量提示词','说明哪些事件会改变变量，只记录已经发生的变化。')+'<div class="variable-actions"><button type="button" data-panel-render>预览初始面板</button><span role="status" data-panel-status></span></div><div data-panel-preview></div></div></div>';
  const state=value??defaults();
  host.querySelector('[role="switch"]').onclick=()=>{
   value=value??defaults();value.enabled=host.querySelector('[role="switch"]').checked;changed();host.querySelector('[data-panel-fields]').hidden=!value.enabled;
  };
  for(const key of ['skeleton','template']) {
   const area=host.querySelector('#variable-'+key),box=area.closest('.variable-code'),lines=box.querySelector('[data-panel-lines]'),count=box.querySelector('[data-panel-count]');area.value=state[key];
   const update=()=>{lines.textContent=Array.from({length:area.value.split('\n').length},(_,i)=>i+1).join('\n');count.textContent=[...area.value].length+' 字';};update();
   area.addEventListener('input',()=>{value=value??defaults();value[key]=area.value;update();changed();});area.addEventListener('scroll',()=>{lines.scrollTop=area.scrollTop;});
   const button=box.querySelector('[data-panel-full]');
   const full=enabled=>{box.classList.toggle('variable-fullscreen',enabled);button.textContent=enabled?'×':'⛶';button.setAttribute('aria-label',key==='skeleton'?(enabled?'退出数据骨架全屏':'数据骨架全屏'):(enabled?'退出面板皮肤全屏':'面板皮肤全屏'));button.title=enabled?'退出全屏':'全屏';if(!enabled)button.focus();else area.focus();};
   const originalFull=full;
   const toggleFull=enabled=>{if(enabled){endFullscreen?.();const hidden=[];for(let node=box;node.parentElement&&node!==document.body;node=node.parentElement)for(const sibling of node.parentElement.children)if(sibling!==node){hidden.push([sibling,sibling.inert]);sibling.inert=true;}box.setAttribute('role','dialog');box.setAttribute('aria-label',key==='skeleton'?'数据骨架全屏':'面板皮肤全屏');box.setAttribute('aria-modal','true');endFullscreen=()=>{for(const [node,inert]of hidden)node.inert=inert;box.removeAttribute('role');box.removeAttribute('aria-modal');box.removeAttribute('aria-label');originalFull(false);endFullscreen=null;};originalFull(true);}else endFullscreen?.();};
   button.onclick=()=>toggleFull(!box.classList.contains('variable-fullscreen'));
   box.onkeydown=event=>{if(!box.classList.contains('variable-fullscreen'))return;if(event.key==='Escape'){event.preventDefault();event.stopPropagation();toggleFull(false);}if(event.key==='Tab'&&((event.shiftKey&&document.activeElement===button)||(!event.shiftKey&&document.activeElement===area))){event.preventDefault();(event.shiftKey?area:button).focus();}};
  }
  host.querySelector('[data-panel-render]').onclick=async()=>{
   clear();const gen=generation,w=getWork(),config=read();if(!w||!config)return;
   const button=host.querySelector('[data-panel-render]'),status=host.querySelector('[data-panel-status]');button.disabled=true;status.textContent='正在预览…';
   try{
    const result=await api('/variable-panel/preview',{workId:w.id,expectedRevision:w.revision,config});
    if(gen!==generation||JSON.stringify(read())!==JSON.stringify(config)){void api('/presentations/release',{token:result.token});return;}
    prepared=result;cleanup=mountVariableFrame(host.querySelector('[data-panel-preview]'),result,{notice});status.textContent='';
   }catch(e){if(gen===generation)status.textContent=panelProblem(e.code);}finally{if(button.isConnected)button.disabled=false;}
  };
 }
 function read(){return value?{...structuredClone(value),instructions:editor.read().panelInstructions??''}:null;}
 return {load(config){endFullscreen?.();clear();value=config?structuredClone(config):null;},read,render,invalidate};
}
