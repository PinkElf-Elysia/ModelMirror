import {modelSessionKey} from './session-selection.mjs';
import {storyProblem} from './joint-client.mjs';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Display preparation is read-only; late frames are released after navigation.
export function createPublishedFrames({api,changed}){
 let generation=0,frame=null;
 const release=value=>{if(value?.token)void api('/presentations/release',{token:value.token}).catch(()=>{});};
 return {clear(){generation++;release(frame);frame=null;},async load(_key,request){
  const gen=++generation;release(frame);frame=null;changed({loading:true});
  try{const value=await api('/joint/playback/prepare',{...request,role:'assistant'});
   if(gen!==generation){release(value);return;}frame=value;changed({loading:false,frames:[null,value]});
  }catch(error){if(gen===generation)changed({loading:false,error});}
 }};
}
export const isPlainPublishedReply=text=>typeof text==='string'&&!/[<>&*_`#[\]~]|(^|\n)\s*(?:[-+>]|[0-9]+[.)])\s|https?:\/\//m.test(text);
const sessionId=id=>/^session-[a-f0-9]{64}$/.test(id||'');
// Published works use the card chat layout and stylesheet. The authoring session
// controller still owns revisions, permissions, quotes and dispatch/recovery.
export function createPublishedChat({api,getWork,getInput,setInput,openModels,openOpening,notice,home}){
 const root=document.createElement('section');root.id='published-chat';root.className='chat-shell rpg-chat-workspace native-chat';root.hidden=true;root.dataset.sidebar=innerWidth>800?'open':'closed';
 root.innerHTML=`<aside class="chat-sidebar" aria-label="会话列表"><header><span data-world></span><button class="sidebar-close" aria-label="关闭会话列表">×</button></header><button class="sidebar-new" data-new>＋ 新的故事</button><p class="sidebar-label">当前版本的会话</p><div class="sidebar-sessions"></div><p data-list-error role="status"></p><footer><a data-home>← 故事馆</a></footer></aside><div class="chat-main"><header class="chat-header"><button class="secondary" data-menu aria-label="会话列表">☰</button><div class="chat-heading"><strong data-title>新的故事</strong><small data-subtitle></small></div><div class="chat-header-actions"><button class="model-chip" data-model>选择模型 ▾</button><button class="secondary" data-plugins>插件</button><button class="secondary" data-reading>阅读</button><button class="secondary" data-opening>角色开局</button></div></header><div class="chat-scroll"><div class="chat-reading"><div class="chat-page-tools"><button class="text-button" data-older>较早的回合</button><button class="text-button" data-latest>最新回合</button></div><div class="conversation" aria-live="polite"></div><p class="notice" data-status role="status" hidden></p></div></div><div class="chat-dock"><div class="chat-tools" aria-label="聊天工具"><button class="text-button" data-memory>记忆宫殿</button><button class="text-button" data-records>对话记录</button><button class="text-button" data-recover>核对记录</button></div><form class="composer"><textarea aria-label="对话输入" placeholder="接下来，你想做什么？" rows="2" maxlength="240000"></textarea><span data-count>0</span><button class="send" aria-label="发送">↑</button></form><p class="composer-hint"><span>Enter 发送 · Shift + Enter 换行</span><span data-cost></span></p></div></div>`;
 document.body.append(root);
 const $=s=>root.querySelector(s),input=$('textarea'),conversation=$('.conversation');let controller=null,unsubscribe=null,state=null,currentId=null,selectedPage=0,turnKey='',listGeneration=0,rows=[],shown=false,sidebar=$('.chat-sidebar'),compact=innerWidth<=800;
 let reading;try{reading=JSON.parse(localStorage.getItem('rpg-reading')||'{}');}catch{reading={};}
 const applyReading=()=>{root.dataset.reading=reading.dark?'dark':'light';root.dataset.width=reading.wide?'wide':'standard';root.style.setProperty('--reading-font',(Number.isInteger(reading.font)&&reading.font>=15&&reading.font<=20?reading.font:17)+'px');};applyReading();
 $('[data-home]').href=home;
 const recordKey=()=> 'modelmirror.authoring.player-sessions/'+getWork()?.id+'/'+getWork()?.publishedVersionId;
 const draftKey=id=>'modelmirror.authoring.player-input/'+getWork()?.id+'/'+getWork()?.publishedVersionId+'/'+(id||'new');
 const fail=e=>{const node=$('[data-status]');node.hidden=false;node.textContent=e?.message||'暂未读取到完整状态。已有记录与输入保留，请核对记录。';};
 const saveDraft=()=>{try{localStorage.setItem(draftKey(currentId),input.value);return true;}catch{fail(Error('无法保存输入草稿，暂不切换会话。请先复制输入内容。'));return false;}};
 function chooseSidebar(open){root.dataset.sidebar=open?'open':'closed';$('[data-menu]').setAttribute('aria-expanded',String(open));if(compact){if(open&&!sidebar.open)sidebar.showModal();else if(!open&&sidebar.open)sidebar.close();}else sidebar.hidden=!open;}
 function resize(){const next=innerWidth<=800;if(next!==compact||next&&sidebar.tagName!=='DIALOG'){
  const element=document.createElement(next?'dialog':'aside');element.className='chat-sidebar';element.setAttribute('aria-label','会话列表');element.append(...sidebar.childNodes);sidebar.replaceWith(element);sidebar=element;compact=next;sidebar.addEventListener('cancel',e=>{e.preventDefault();chooseSidebar(false);});chooseSidebar(!compact);
 }else chooseSidebar(root.dataset.sidebar==='open');}
 window.addEventListener('resize',()=>{resize();renderTurns();});resize();
 $('[data-menu]').onclick=()=>chooseSidebar(root.dataset.sidebar!=='open');$('.sidebar-close').onclick=()=>chooseSidebar(false);
 function syncInput(){if(input.value!==getInput())input.value=getInput();$('[data-count]').textContent=String(input.value.length);}
 input.oninput=()=>{setInput(input.value);$('[data-count]').textContent=String(input.value.length);saveDraft();};
 document.querySelector('#player-input').addEventListener('input',syncInput);
 $('.composer').onsubmit=e=>{e.preventDefault();if(state?.canSend)controller.send();};
 input.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('.composer').requestSubmit();}};
 const action=(selector,fn)=>{$(selector).onclick=fn;};
 action('[data-model]',()=>openModels());action('[data-plugins]',()=>controller?.open('plugins'));action('[data-memory]',()=>controller?.open('memory'));action('[data-records]',()=>controller?.open('records'));action('[data-recover]',()=>controller?.recover());
 action('[data-opening]',()=>{saveDraft();hide();openOpening();});
 action('[data-reading]',()=>{
  const d=document.createElement('dialog');d.className='chat-dialog reading-dialog';d.setAttribute('aria-label','阅读设置');d.innerHTML='<header><h2>阅读设置</h2><button class="icon" aria-label="关闭阅读设置">×</button></header><label><input type="checkbox" data-dark> 深色背景</label><label>正文字号 <input type="range" min="15" max="20" step="1"></label><label><input type="checkbox" data-wide> 宽版阅读</label>';root.append(d);d.querySelector('[data-dark]').checked=!!reading.dark;d.querySelector('[data-wide]').checked=!!reading.wide;d.querySelector('[type=range]').value=String(reading.font||17);
  d.onchange=()=>{reading={dark:d.querySelector('[data-dark]').checked,wide:d.querySelector('[data-wide]').checked,font:Number(d.querySelector('[type=range]').value)};applyReading();try{localStorage.setItem('rpg-reading',JSON.stringify(reading));}catch{notice('阅读设置仅在本页生效。');}};d.querySelector('button').onclick=()=>d.close();d.addEventListener('close',()=>d.remove(),{once:true});d.showModal();
 });
 action('[data-new]',()=>{
  if(!state?.canSwitch||!saveDraft())return;
  const d=document.createElement('dialog');d.className='chat-dialog';d.setAttribute('aria-label','新的故事');d.innerHTML='<header><h2>新的故事</h2></header><p>现有会话和输入会保留。回到角色开局，填写资料后选择模型创建新会话。</p><button data-cancel>取消</button><button class="chat-primary" data-confirm>创建新的故事</button>';root.append(d);d.querySelector('[data-cancel]').onclick=()=>d.close();d.querySelector('[data-confirm]').onclick=()=>{try{localStorage.removeItem(modelSessionKey(getWork().id,getWork().publishedVersionId));currentId=null;setInput(localStorage.getItem(draftKey(null))||'');controller.refresh();d.close();hide();openOpening();}catch(e){fail(e);}};d.onclose=()=>d.remove();d.showModal();
 });
 function knownSessions(){let ids;try{ids=JSON.parse(localStorage.getItem(recordKey())||'[]');}catch{throw Error('本机的会话目录无法读取，请保留浏览器数据。');}if(!Array.isArray(ids)||ids.some(id=>!sessionId(id)))throw Error('本机会话目录格式异常，原记录未改动。');return ids;}
 async function refreshList(){const gen=++listGeneration;if(!getWork())return;try{
  const ids=new Set(knownSessions());if(currentId)ids.add(currentId);for(const row of state?.routes||[]){ids.add(row.sessionId);ids.add(row.parentSessionId);}
  const results=await Promise.allSettled([...ids].filter(sessionId).map(id=>api('/joint/sessions/'+id)));
  if(gen!==listGeneration)return;
  rows=results.filter(r=>r.status==='fulfilled').map(r=>r.value.session).filter(s=>s.binding.workId===getWork().id&&s.binding.versionId===getWork().publishedVersionId);
  localStorage.setItem(recordKey(),JSON.stringify([...ids].filter(sessionId)));
  $('[data-list-error]').textContent=results.some(r=>r.status==='rejected')?'部分会话暂时无法读取，原记录已保留。':'';renderList();
 }catch(e){$('[data-list-error]').textContent=e.message;}}
 function renderList(){const area=$('.sidebar-sessions');area.replaceChildren();for(const row of rows){const b=document.createElement('button');b.className='sidebar-session-row';b.setAttribute('aria-current',row.id===currentId?'page':'false');b.disabled=!state?.canSwitch;b.innerHTML='<strong>'+esc(row.binding.openingText.slice(0,36)||'新的故事')+'</strong><small>'+row.turns.length+' 回合</small>';b.onclick=async()=>{if(!saveDraft())return;try{await controller.switchSession(row.id);if(state?.view?.session.id!==row.id)return;setInput(localStorage.getItem(draftKey(row.id))??(row.turns.length?'':row.binding.openingText));syncInput();if(compact)chooseSidebar(false);}catch(e){fail(e);}};area.append(b);}}
 const frames=new Map();function clearFrames(){for(const manager of frames.values())manager.clear();frames.clear();turnKey='';}
 function hide(){shown=false;root.hidden=true;chooseSidebar(false);clearFrames();}
 function renderTurns(){if(!shown)return;const s=state?.view?.session,turns=s?.turns||[],end=Math.max(0,turns.length-selectedPage*6),start=Math.max(0,end-6),visible=turns.slice(start,end);const key=JSON.stringify([s?.id,visible.map(t=>[t.number,t.assistant]),innerWidth<=600]);if(key===turnKey)return;clearFrames();turnKey=key;
  $('[data-older]').hidden=start===0;$('[data-latest]').hidden=selectedPage===0;
  conversation.innerHTML=visible.length?'':'<div class="chat-empty"><span aria-hidden="true">序</span><h2>故事，从这一刻开始。</h2><p>写下你的角色、故事开场，或想做的第一件事。</p>'+(!s?'<button class="secondary" data-setup>选择模型并创建会话</button>':'')+'</div>';
  const setup=$('[data-setup]');if(setup)setup.onclick=()=>openModels();
  for(const turn of visible){const article=document.createElement('article');article.innerHTML='<div class="player-message"></div><div class="turn-label">第 '+turn.number+' 回合 · 已保存</div><div class="model-output" data-output></div><div class="message-actions"><button class="text-button" data-copy>复制回复</button><button class="text-button" data-raw>查看原文</button><button class="text-button" data-branch>⑂ 分支</button></div><p data-frame-error role="status"></p>';article.querySelector('.player-message').textContent=turn.user;conversation.append(article);const output=article.querySelector('[data-output]');output.textContent=turn.assistant;output.classList.add('platform-prose');
   article.querySelector('[data-copy]').onclick=()=>navigator.clipboard.writeText(turn.assistant).then(()=>notice('已复制回复。')).catch(()=>notice('剪贴板不可用，请查看原文后复制。'));
   article.querySelector('[data-raw]').onclick=()=>{const d=document.createElement('dialog');d.className='chat-dialog';d.setAttribute('aria-label','保存的原文');d.innerHTML='<header><h2>保存的原文</h2><button aria-label="关闭原文">×</button></header><pre></pre>';d.querySelector('pre').textContent=turn.assistant;root.append(d);d.querySelector('button').onclick=()=>d.close();d.onclose=()=>d.remove();d.showModal();};
   article.querySelector('[data-branch]').disabled=!state?.canSwitch||!state?.settings?.plugins?.branch?.enabled;article.querySelector('[data-branch]').onclick=()=>controller.branch(turn.number);
   if(isPlainPublishedReply(turn.assistant))continue;
   const manager=createPublishedFrames({api,changed:result=>{if(!article.isConnected||root.hidden)return;if(result.loading)return;if(result.error){article.querySelector('[data-frame-error]').textContent='展示暂不可用，已保留完整原文。';return;}const p=result.frames[1];if(!p)return;const frame=document.createElement('iframe');frame.title='第 '+turn.number+' 回合剧情';frame.setAttribute('sandbox','allow-scripts');frame.referrerPolicy='no-referrer';frame.src=p.frameUrl;frame.height='500';output.classList.remove('platform-prose');output.replaceChildren(frame);}});frames.set(turn.number,manager);
   void manager.load(s.id+'|'+turn.number,{sessionId:s.id,turnNumber:turn.number,device:innerWidth<=600?'mobile':'desktop'});
  }
 }
 action('[data-older]',()=>{selectedPage++;renderTurns();$('.chat-scroll').scrollTop=0;});action('[data-latest]',()=>{selectedPage=0;renderTurns();$('.chat-scroll').scrollTop=$('.chat-scroll').scrollHeight;});
 function render(next){state=next;const s=state?.view?.session,id=s?.id||null,changed=currentId!==id;currentId=id;if(changed){selectedPage=0;try{const draft=localStorage.getItem(draftKey(id));if(draft!==null)setInput(draft);}catch{}void refreshList();}
  $('[data-world]').textContent=getWork()?.draft.metadata.name||'我的故事';$('[data-title]').textContent=s?.binding.openingText.slice(0,36)||'新的故事';$('[data-subtitle]').textContent=(s?.turns.length||0)+' 回合 · '+(getWork()?.draft.metadata.name||'RPG');
  $('[data-model]').textContent=(s?.models.current.story?.entry.name||'选择模型')+' ▾';$('[data-model]').disabled=!!state?.busy;for(const selector of ['[data-plugins]','[data-memory]','[data-records]','[data-recover]'])$(selector).disabled=!s||!!state?.busy;
  $('[data-new]').disabled=!state?.canSwitch;$('[aria-label="发送"]').disabled=!state?.canSend;input.disabled=!!state?.busy;$('[data-cost]').textContent=state?.controlled?'可用 '+(state.view?.budget.remaining??'待核对')+' 次':'离线测试 · 不调用模型';
  const last=s?.operations.at(-1),problem=storyProblem(last);const message=state?.error||(state?.reading?'正在读取会话…':state?.busy?'正在生成…':problem?'本轮未完成。原输入与记录保留，请核对记录。':state?.view?.pending?'有请求结果待核对，请先核对记录。':!controller?'会话服务尚未开启，暂时无法发送。':!s?'填写开局后选择模型，创建会话即可开始。':'');$('[data-status]').textContent=message;$('[data-status]').hidden=!message;
  syncInput();renderList();renderTurns();
 }
 function show(){shown=true;root.hidden=false;document.body.dataset.playerView='chat';syncInput();if(controller)controller.refresh();else fail(Error('当前服务尚未配置会话运行器。'));render(state);void refreshList();input.focus();}
 window.addEventListener('pagehide',()=>{saveDraft();clearFrames();unsubscribe?.();});
 window.addEventListener('authoring-turn-saved',()=>{if(shown){selectedPage=0;controller?.refresh();void refreshList();}});
 return {get visible(){return shown;},show,hide,workChanged(){render(state);if(shown)void refreshList();},connect(next){if(!next||next===controller)return;unsubscribe?.();controller=next;unsubscribe=controller.subscribe(render);}};
}
