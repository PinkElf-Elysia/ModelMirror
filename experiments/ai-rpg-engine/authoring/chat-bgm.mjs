// Host-owned chat music. No model HTML or author scripts can issue playback commands.
export function chatBGMTrack(work, document) {
  const alias=document?.settings.media.bgm;
  if(!work||!alias)return null;
  const asset=work.assets.find(item=>item.alias===alias);
  const valid=asset?.mediaType==='audio/mpeg'&&/^[a-f0-9]{64}$/.test(asset.sha256);
  const autoplay=document.settings.bgmAutoplay===true;
  return {key:JSON.stringify([work.id,alias,asset?.sha256,asset?.mediaType,autoplay]),alias,
    name:asset?.fileName||alias,autoplay,
    url:valid?'/rpg-app/api/authoring/works/'+encodeURIComponent(work.id)+'/assets/'+encodeURIComponent(alias)+'?hash='+asset.sha256:null,
    error:valid?null:'缺少可播放的本地 MP3，引用仍保留。请核对作品资源。'};
}

export function createChatBGMController({createAudio,beforePlay=()=>{},changed=()=>{}}) {
  let track=null,audio=null,active=false,panel=false,collapsed=false,entered=false,status='idle',epoch=0,wanted=false;
  const snapshot=()=>({track:track?{...track}:null,active,panel,collapsed,status});
  const emit=()=>changed(snapshot());
  function release(){
    epoch++;wanted=false;
    if(audio){const old=audio;audio=null;old.onplaying=null;old.onpause=null;old.onerror=null;old.pause();old.removeAttribute('src');old.load();}
  }
  function ensureAudio(){
    if(audio)return audio;
    audio=createAudio();const element=audio;
    element.preload='metadata';element.loop=true;element.src=track.url;
    element.onplaying=()=>{if(audio!==element)return;if(!wanted||!active||!panel){element.pause();return;}status='playing';emit();};
    element.onpause=()=>{if(audio===element&&status==='playing'){wanted=false;epoch++;status='paused';emit();}};
    element.onerror=()=>{if(audio!==element)return;epoch++;wanted=false;element.pause();status='failed';emit();};
    return element;
  }
  async function play(){
    if(!active||!panel||!track?.url)return;
    const element=ensureAudio(),ticket=++epoch;wanted=true;status='loading';emit();
    try{
      beforePlay();await element.play();
      if(ticket!==epoch||audio!==element){if(audio!==element||!wanted)element.pause();return;}
      if(!active||!panel||!wanted){element.pause();return;}
      status='playing';emit();
    }catch(error){
      if(ticket!==epoch||audio!==element)return;
      wanted=false;status=error?.name==='NotAllowedError'?'blocked':'failed';emit();
    }
  }
  function pause(){epoch++;wanted=false;audio?.pause();status=track?.error?'missing':'paused';emit();}
  function open(){
    if(!active||!track)return;
    if(panel)return;
    panel=true;entered=true;status=track.error?'missing':'ready';
    if(track.url)ensureAudio();emit();if(track.autoplay)void play();
  }
  return {snapshot,play,pause,open,
    update(next,{active:enabled,enter=true}={}){
      if(next?.key!==track?.key){release();track=next?{...next}:null;panel=false;collapsed=false;entered=false;status=track?.error?'missing':'ready';}
      active=!!enabled&&!!track;
      if(!active){release();panel=false;entered=false;status=track?.error?'missing':'ready';}
      else if(enter&&!entered){entered=true;if(track.autoplay)open();}
      emit();
    },
    toggle(){if(status==='playing'||status==='loading')pause();else void play();},
    collapse(){collapsed=!collapsed;emit();},
    close(){release();panel=false;status=track?.error?'missing':'ready';emit();},
    destroy(){release();track=null;active=false;panel=false;entered=false;status='idle';emit();},
  };
}

const icons={music:'<path d="M9 18V5l12-2v13M9 8l12-2"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',play:'<path d="M8 5v14l11-7z"/>',pause:'<path d="M8 5v14M16 5v14"/>',close:'<path d="M6 6l12 12M6 18 18 6"/>',collapse:'<path d="m14 6-6 6 6 6"/>',expand:'<path d="m10 6 6 6-6 6"/>'};
const svg=name=>'<svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'+icons[name]+'</svg>';
const messages={ready:'待播放',loading:'正在加载音乐…',playing:'正在播放 · 循环',paused:'已暂停',blocked:'浏览器未允许自动播放，请点击播放。',failed:'音频无法播放，请检查本地文件后手动重试。'};
export function mountChatBGM(root) {
  const doc=root.ownerDocument;
  root.className='chat-bgm';root.setAttribute('aria-label','聊天背景音乐');
  root.innerHTML='<button type="button" class="bgm-launch" aria-label="打开背景音乐" aria-expanded="false">'+svg('music')+'背景音乐</button><div class="bgm-panel" hidden><button type="button" class="bgm-icon bgm-collapse" aria-label="收起音乐信息" aria-expanded="true">'+svg('collapse')+'</button><button type="button" class="bgm-icon bgm-disc" aria-label="播放背景音乐">'+svg('music')+'</button><div class="bgm-details"><div class="bgm-title"><div class="bgm-name"></div><div class="bgm-subtitle">未知艺术家</div></div><button type="button" class="bgm-icon bgm-play" aria-label="播放背景音乐">'+svg('play')+'</button></div><button type="button" class="bgm-icon bgm-close" aria-label="关闭背景音乐">'+svg('close')+'</button></div><p class="bgm-status" role="status" hidden></p><div class="bgm-audio" hidden></div>';
  const $=selector=>root.querySelector(selector);
  const controller=createChatBGMController({beforePlay:()=>{for(const element of doc.querySelectorAll('audio'))if(!root.contains(element))element.pause();},createAudio:()=>{const element=doc.createElement('audio');$('.bgm-audio').replaceChildren(element);return element;},changed(state){
    root.hidden=!state.active;root.dataset.status=state.status;
    $('.bgm-panel').hidden=!state.panel;$('.bgm-launch').setAttribute('aria-expanded',String(state.panel));
    $('.bgm-name').textContent=state.track?.name.replace(/\.mp3$/i,'')||'';$('.bgm-name').title=state.track?.name||'';
    $('.bgm-details').hidden=state.collapsed;
    $('.bgm-collapse').innerHTML=svg(state.collapsed?'expand':'collapse');$('.bgm-collapse').setAttribute('aria-label',state.collapsed?'展开音乐信息':'收起音乐信息');$('.bgm-collapse').setAttribute('aria-expanded',String(!state.collapsed));
    const playing=['playing','loading'].includes(state.status);
    for(const node of [$('.bgm-disc'),$('.bgm-play')]){node.disabled=!state.track?.url;node.setAttribute('aria-label',playing?'暂停背景音乐':'播放背景音乐');}
    $('.bgm-play').innerHTML=svg(playing?'pause':'play');
    $('.bgm-status').hidden=!state.panel;$('.bgm-status').textContent=state.track?.error||messages[state.status]||'';
    if(!state.panel||!state.active)$('.bgm-audio').replaceChildren();
  }});
  $('.bgm-launch').onclick=()=>controller.open();
  $('.bgm-disc').onclick=$('.bgm-play').onclick=()=>controller.toggle();
  $('.bgm-collapse').onclick=()=>controller.collapse();
  $('.bgm-close').onclick=()=>{controller.close();$('.bgm-launch').focus();};
  root.addEventListener('keydown',event=>{if(event.key==='Escape'&&controller.snapshot().panel){event.preventDefault();event.stopPropagation();controller.close();$('.bgm-launch').focus();}});
  controller.update(null,{active:false});return controller;
}
