import {modelSessionKey} from './session-selection.mjs';
import {mountVariableFrame,panelProblem} from './variable-frame-ui.mjs';
import {createSpecialCGGallery} from './special-cg-ui.mjs';
import {mountChatBGM} from './chat-bgm.mjs';
const endpoint='/joint/playback';
export const playbackPollDelay=view=>view?.busy?2500:null;
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const problem=e=>({TURN_DISPLAY_PREVIOUS_REQUIRED:'请先回到前一回合恢复展示，再核对本轮。',TURN_DISPLAY_PENDING:'这轮展示尚未收束。原始剧情已保存，可恢复本轮展示。',TURN_DISPLAY_MISSING:'这轮展示尚未建立。原始剧情已保存，可恢复本轮展示。',OFFLINE_REVISION_CONFLICT:'会话已更新，请刷新状态后再恢复展示。',AUTHORING_PRESENTATION_CAPACITY:'展示缓存已满。可关闭其他预览后重新加载显示。',AUTHORING_CG_OUTPUT_CAPACITY:'本轮 CG 超出展示容量，未应用部分图片。完整剧情已保留。',PLAYBACK_SESSION_REFERENCE_INVALID:'浏览器中的会话引用无法读取，请在模型设置中重新选择。',TURN_DISPLAY_SOURCE_CONFLICT:'展示记录与原回合不一致，请保留记录并核对。'}[e?.code]||'展示暂未完成，原始剧情和输入已保留。请刷新状态或查看诊断。');
export function playbackSessionId(storage,workId,versionId=null){
 let id;try{id=JSON.parse(storage.getItem(modelSessionKey(workId,versionId))||'null');}catch{throw {code:'PLAYBACK_SESSION_REFERENCE_INVALID'};}
 if(id!==null&&(typeof id!=='string'||!/^session-[a-f0-9]{64}$/.test(id)))throw {code:'PLAYBACK_SESSION_REFERENCE_INVALID'};
 return id;
}
// A saved-turn notification only changes the view for the selected work/session.
export function savedTurnMatches(event,workId,storage,versionId=null){
 try{return !!workId&&event?.workId===workId&&event.sessionId===playbackSessionId(storage,workId,versionId);}catch{return false;}
}
// Each change owns its frames. A late response is released, never attached to a
// different session/turn. Read-only polling reuses the exact same live frames.
export function createPlaybackFrames({api,changed}){
 let generation=0,key=null,frames=[],pending=null;
 const release=rows=>Promise.allSettled(rows.filter(Boolean).map(frame=>api('/presentations/release',{token:frame.token})));
 const clear=()=>{generation++;key=null;const old=frames;frames=[];pending=null;void release(old);};
 return {clear,get frames(){return frames;},load(nextKey,request,{panel=false}={}){
  if(key===nextKey)return pending||Promise.resolve();
  clear();key=nextKey;const gen=generation;changed({loading:true,frames:[],error:null});
  const task=(async()=>{
   const tasks=['user','assistant'].map(role=>api(endpoint+'/prepare',{...request,role}));
   if(panel)tasks.push(api(endpoint+'/panel',{sessionId:request.sessionId,turnNumber:request.turnNumber}));
   const rows=await Promise.allSettled(tasks);
   const successes=rows.filter(r=>r.status==='fulfilled').map(r=>r.value);
   if(gen!==generation){await release(successes);return;}
   const failed=rows.slice(0,2).find(r=>r.status==='rejected');
   if(failed){await release(successes);if(gen===generation)changed({loading:false,frames:[],error:failed.reason});return;}
   frames=successes;changed({loading:false,frames,error:null,panelError:rows[2]?.status==='rejected'?rows[2].reason:null});
  })();pending=task;return task.finally(()=>{if(gen===generation)pending=null;});
 }};
}
export function createPlaybackPanel({api,getWork,setInput,notice,activate}){
 const style=document.createElement('link');style.rel='stylesheet';style.href='/playback-ui.css';document.head.append(style);
 const pane=document.querySelector('.preview-pane'),tabs=document.querySelector('.preview-tabs');
 const trigger=document.createElement('button');trigger.type='button';trigger.textContent='实际对话';trigger.setAttribute('aria-pressed','false');trigger.setAttribute('aria-controls','turn-playback');tabs.prepend(trigger);
 const root=document.createElement('section');root.id='turn-playback';root.className='playback-view';root.hidden=true;root.setAttribute('aria-label','已保存的对话');
 root.innerHTML='<nav class="playback-toolbar" aria-label="对话回合"><strong data-turn-label>实际对话</strong><button type="button" data-prev aria-label="上一回合">←</button><button type="button" data-next aria-label="下一回合">→</button><button type="button" data-latest>最新</button><button type="button" data-refresh>刷新显示</button><button type="button" data-special-gallery>CG 画廊</button></nav><p class="playback-state" role="status"></p><div class="playback-recovery" hidden><button type="button" data-recover>恢复本轮展示</button></div><div class="playback-bgm-host"><div data-music></div></div><div class="playback-scroll"></div>';
 document.querySelector('#preview-content').after(root);
 const $=s=>root.querySelector(s),music=mountChatBGM($('[data-music]'));
 let panelCleanup=null;
 let active=false,workId=null,sessionId=null,view=null,selected=0,follow=true,timer=null,loading=false,generation=0,currentKey=null,displayStatus=null,track=null,recovering=false,dead=false,frameIssue=null,refreshAgain=false,forceAgain=false;
 const visible=()=>active&&!document.hidden&&getComputedStyle(pane).display!=='none';
 const device=()=>innerWidth<=600?'mobile':'desktop';
 const setStatus=(text,warning=false)=>{$('.playback-state').textContent=text;$('.playback-state').dataset.warning=String(warning);};
 const schedule=()=>{clearTimeout(timer);const delay=playbackPollDelay(view);if(visible()&&!dead&&delay!==null)timer=setTimeout(()=>refresh(),delay);};
 const manager=createPlaybackFrames({api,changed:renderFrames});
 const gallery=createSpecialCGGallery({api,getSession:()=>view?.session,notice});
 $('[data-special-gallery]').onclick=()=>void gallery.open();
 function renderFrames(state){
  if(!active||!currentKey)return;
  const hosts=[...root.querySelectorAll('[data-frame]')];
  if(state.loading){frameIssue=null;for(const host of hosts){host.innerHTML='<p class="playback-placeholder">正在载入这一回合…</p>';}return;}
  if(state.error){frameIssue=problem(state.error);setStatus(frameIssue,true);diagnostics(state.error.code);for(const host of hosts)host.innerHTML='<p class="playback-placeholder">暂未载入展示，可在下方查看完整原文。</p>';return;}
  state.frames.slice(0,2).forEach((p,i)=>{
   const frame=document.createElement('iframe');frame.title=i?'本轮剧情':'本轮玩家输入';frame.setAttribute('sandbox','allow-scripts');frame.referrerPolicy='no-referrer';frame.setAttribute('allow',"camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'; fullscreen 'none'");frame.src=p.frameUrl;hosts[i].replaceChildren(frame);
  });
  panelCleanup?.();panelCleanup=null;
  const panelHost=$('[data-variable-frame]');
  if(panelHost){panelHost.replaceChildren();panelHost.parentElement.hidden=!state.frames[2]&&!state.panelError;if(state.frames[2])panelCleanup=mountVariableFrame(panelHost,state.frames[2],{notice});else if(state.panelError)panelHost.textContent=panelProblem(state.panelError.code);}
  const assistant=state.frames[1],bgm=assistant.bgm;
  track=bgm?{key:sessionId+'|'+bgm.asset.sha256,name:bgm.asset.fileName||bgm.asset.alias,url:bgm.url,autoplay:bgm.autoplay,error:null}:null;
  music.update(track,{active:visible(),enter:true});
  const choices=$('[data-choices]');choices.replaceChildren();
  if(assistant.manifest.suggestions?.length){const hint=document.createElement('p');hint.textContent='接下来 · 点击填入输入框';choices.append(hint);for(const text of assistant.manifest.suggestions){const b=document.createElement('button');b.type='button';b.textContent=text;b.onclick=()=>{setInput(text);document.querySelector('#player-input').focus();notice('建议已填入，尚未发送。');};choices.append(b);}}
  const ds=assistant.manifest.diagnostics||[];diagnostics(ds.map(d=>d.code+(d.detail?'：'+d.detail:'')).join('\n'),assistant);
  frameIssue=ds.length?'部分展示内容需要核对，可展开“展示详情”查看。':null;if(frameIssue)setStatus(frameIssue,true);
 }
 function diagnostics(text='',prepared=null){
  const area=$('[data-diagnostics]');if(!area)return;
  area.textContent=text||'展示已载入。';if(prepared){const m=prepared.manifest;area.textContent+='\n原文 hash：'+m.sourceHash+'\n展示 hash：'+m.renderHash+(m.cg?'\n聊天插图：'+m.cg.plan.chat.length+' 张；背景：'+({selected:'本轮切换',inherited:'沿用上一回合',default:'作品默认',none:'无'}[m.cg.backgroundSource]||m.cg.backgroundSource):'');}
 }
 function prepareTurn(turn){
  const key=sessionId+'|'+turn.number;
  if(currentKey===key)return;
  currentKey=key;panelCleanup?.();panelCleanup=null;manager.clear();frameIssue=null;track=null;music.update(null,{active:false});
  const bgmHost=$('.playback-bgm-host');root.insertBefore(bgmHost,$('.playback-scroll'));
  $('.playback-scroll').innerHTML='<article class="playback-message" data-role="user"><details><summary>玩家输入</summary><div data-frame></div></details></article><article class="playback-message" data-role="assistant"><h3>剧情</h3><div data-frame></div><section class="playback-variable" hidden><h3>变量面板</h3><div data-variable-frame></div></section><div data-choices class="playback-choices" aria-label="下一步建议"></div><div class="playback-extras"><details class="playback-tools"><summary>查看完整原文</summary><h4>玩家输入</h4><pre class="playback-raw" data-raw-user></pre><h4>AI 回复</h4><pre class="playback-raw" data-raw-assistant></pre></details><details class="playback-tools"><summary>展示详情</summary><pre class="playback-raw" data-diagnostics></pre></details></div></article>';
  $('[data-raw-user]').textContent=turn.user;$('[data-raw-assistant]').textContent=turn.assistant;$('.playback-scroll').prepend($('.playback-bgm-host'));$('.playback-scroll').scrollTop=0;
 }
 function empty(text){panelCleanup?.();panelCleanup=null;const bgmHost=$('.playback-bgm-host');root.insertBefore(bgmHost,$('.playback-scroll'));currentKey=null;frameIssue=null;manager.clear();track=null;music.update(null,{active:false});$('.playback-scroll').innerHTML='<p class="playback-empty">'+esc(text)+'</p>';$('[data-recover]').parentElement.hidden=true;}
 function navigation(){
  $('[data-special-gallery]').disabled=!view?.session;
  const count=view?.session.turns.length||0;$('[data-turn-label]').textContent=count?'第 '+selected+' / '+count+' 回合':'实际对话';
  $('[data-prev]').disabled=selected<=1;$('[data-next]').disabled=selected>=count;$('[data-latest]').disabled=!count||selected===count;
 }
 async function refresh(force=false){
  if(!visible()||dead)return;if(loading){refreshAgain=true;forceAgain||=force;return;}clearTimeout(timer);loading=true;const gen=generation,w=getWork()?.id;
  try{
   if(!w){empty('新建或打开作品后，在此查看已保存的剧情。');return;}
   const id=playbackSessionId(localStorage,w,getWork()?.publishedVersionId);
   if(!id){gallery.close();view=null;sessionId=null;empty('先选择作品版本和模型，发送开局后，完整对话会保存在这里。');navigation();return;}
   if(id!==sessionId){gallery.close();view=null;empty('正在读取已保存的会话…');navigation();}
   const next=await api('/joint/sessions/'+id);
   if(gen!==generation||w!==getWork()?.id||!active)return;
   if(next.session.binding.workId!==w||(getWork()?.publishedVersionId&&next.session.binding.versionId!==getWork().publishedVersionId))throw {code:'TURN_DISPLAY_SOURCE_CONFLICT'};
   if(id!==sessionId){sessionId=id;selected=0;follow=true;currentKey=null;manager.clear();}
   view=next;const turns=view.session.turns;
   if(follow||selected<1)selected=turns.length;selected=Math.min(selected,turns.length);navigation();
   const sessionMessage=view.busy?'剧情已保存，后台整理中。':view.pending?'有请求结果待核对，请使用上方“恢复原操作”。':'';
   if(!turns.length){setStatus(sessionMessage);empty(view.busy?'正在等待第一份完整剧情。':'填写开局并发送，开始这段故事。');return;}
   const turn=turns[selected-1];prepareTurn(turn);
   const status=await api(endpoint+'/status/'+sessionId+'/'+selected);
   if(gen!==generation||currentKey!==id+'|'+turn.number)return;displayStatus=status;setStatus(sessionMessage);
   const canRecover=['missing','prepared'].includes(status.state);$('.playback-recovery').hidden=!canRecover;$('[data-recover]').disabled=recovering;
   if(canRecover){manager.clear();setStatus('剧情已保存，展示结果待核对。恢复只处理展示，不重新发送。',true);diagnostics(status.diagnostic||status.state);return;}
   if(force){frameIssue=null;manager.clear();}else if(frameIssue)setStatus(frameIssue,true);
   await manager.load(currentKey+'|'+device()+'|'+status.resultHash,{sessionId:id,turnNumber:turn.number,device:device()},{panel:!!view.session.variablePanel});
  }catch(e){if(gen===generation){setStatus(problem(e),true);diagnostics(e.code);}}
  finally{loading=false;if(refreshAgain){const forced=forceAgain;refreshAgain=false;forceAgain=false;void refresh(forced);}else schedule();}
 }
 async function recover(){
  if(recovering||!displayStatus||!view)return;
  const gen=generation,id=sessionId,n=selected;recovering=true;$('[data-recover]').disabled=true;
  try{const latest=await api(endpoint+'/status/'+id+'/'+n);await api(endpoint+'/recover',{sessionId:id,turnNumber:n,...latest.recovery});if(gen===generation){notice('展示记录已核对，没有重新发送剧情。');await refresh(true);}}
  catch(e){if(gen===generation){setStatus(problem(e),true);diagnostics(e.code);}}
  finally{recovering=false;if(gen===generation)$('[data-recover]').disabled=false;}
 }
 function hide(){panelCleanup?.();panelCleanup=null;gallery.close();active=false;generation++;clearTimeout(timer);trigger.setAttribute('aria-pressed','false');root.hidden=true;manager.clear();currentKey=null;music.destroy();}
 trigger.onclick=()=>{active=true;activate();root.hidden=false;trigger.setAttribute('aria-pressed','true');void refresh();};
 $('[data-prev]').onclick=()=>{if(selected>1){follow=false;selected--;generation++;void refresh();}};
 $('[data-next]').onclick=()=>{if(selected<(view?.session.turns.length||0)){selected++;follow=selected===view.session.turns.length;generation++;void refresh();}};
 $('[data-latest]').onclick=()=>{follow=true;generation++;void refresh();};
 $('[data-refresh]').onclick=()=>refresh(true);$('[data-recover]').onclick=recover;
 const visibility=()=>{music.update(track,{active:visible(),enter:false});if(visible())void refresh();else clearTimeout(timer);};
 const observer=new MutationObserver(visibility);observer.observe(pane,{attributes:true,attributeFilter:['class']});
 document.addEventListener('visibilitychange',visibility);window.addEventListener('resize',visibility);window.addEventListener('pageshow',e=>{if(e.persisted){dead=false;visibility();}});window.addEventListener('storage',e=>{if(e.key===modelSessionKey(workId,getWork()?.publishedVersionId)){generation++;void refresh();}});
 window.addEventListener('authoring-session-changed',()=>{gallery.close();generation++;void refresh();});
 window.addEventListener('authoring-turn-saved',event=>{if(!savedTurnMatches(event.detail,getWork()?.id,localStorage,getWork()?.publishedVersionId))return;follow=true;generation++;active=true;activate();root.hidden=false;trigger.setAttribute('aria-pressed','true');void refresh();});
 window.addEventListener('pagehide',()=>{dead=true;clearTimeout(timer);music.destroy();for(const frame of manager.frames)fetch('/rpg-app/api/authoring/presentations/release',{method:'POST',headers:{'Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify({token:frame.token}),keepalive:true}).catch(()=>{});manager.clear();});
 return {initialize(){navigation();},hide,workChanged(){const id=getWork()?.id;if(id!==workId){gallery.close();workId=id;view=null;sessionId=null;selected=0;follow=true;generation++;empty('正在读取作品的会话…');}if(active)void refresh();}};
}
