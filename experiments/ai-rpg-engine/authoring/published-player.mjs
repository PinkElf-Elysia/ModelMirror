import {createPublishedChat} from './published-chat.mjs';
// Dedicated player chrome. Version selection and runtime dispatch remain owned
// by the existing immutable-work/session controllers; opening never sends.
export function mountPublishedPlayer({openOpening,notice,api,getWork,getInput,setInput,openModels,getSessionPanel}) {
 const $=selector=>document.querySelector(selector);
 const header=$('.workspace-header'),home=$('.creator-brand');
 if(home?.href)home.textContent='← 返回故事馆';
 else if(home){home.href='/';home.textContent='← 返回创作工作台';}
 const tools=document.createElement('nav');tools.className='player-tools';tools.setAttribute('aria-label','游玩工具');
 const opening=document.createElement('button');opening.type='button';opening.textContent='角色开局';opening.onclick=openOpening;
 const chat=document.createElement('button');chat.type='button';chat.textContent='进入对话';chat.className='primary';
 const player=createPublishedChat({api,getWork,getInput,setInput,openModels,openOpening,notice,home:home.href});
 chat.onclick=()=>player.show();
 tools.append(opening,$('#model-button'),chat);header.append(tools);
 $('.preview-tabs').setAttribute('aria-label','游玩内容');
 $('#refresh-preview').textContent='重新加载开局';
 $('#player-input').placeholder='输入角色资料或下一步行动…';
 const edit=[...document.querySelectorAll('.top-actions a')].find(link=>link.textContent==='编辑作品');if(edit){edit.classList.add('player-edit');tools.append(edit);}
 // The page continues using the same dialogs and session bindings, but no
 // editor, sample-output toolbar or preview controls are exposed to players.
 $('.edit-pane').hidden=true;$('.edit-pane').inert=true;
 player.connect(getSessionPanel());
 if(location.hash==='#chat')player.show();
 return player;
}
