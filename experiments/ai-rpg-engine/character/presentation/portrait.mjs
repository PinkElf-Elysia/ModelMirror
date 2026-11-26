// The static img stays underneath the canvas and remains available without WebCodecs.
export function animatedFace(face,controls,url,info,beforePlay,stillPresent=null) {
  const doc=face.ownerDocument,win=doc.defaultView;
  const button=doc.createElement('button'),still=doc.createElement('button'),status=doc.createElement('span');
  button.type=still.type='button';button.textContent='播放肖像';still.textContent='静态肖像';
  status.setAttribute('role','status');controls.append(button,still,status);still.hidden=true;
  const preference=win.matchMedia?.('(prefers-reduced-motion: reduce)');
  let active=null,disposed=false;
  function release(message=''){
    const previous=active;active=null;
    if(previous){
      win.clearTimeout(previous.timer);win.clearTimeout(previous.checkTimer);previous.check?.abort();previous.controller.abort();previous.decoder?.close();
      if(previous.canvas){previous.canvas.width=previous.canvas.height=0;previous.canvas.remove();}
    }
    button.textContent='播放肖像';button.disabled=false;still.hidden=true;status.textContent=message;
  }
  async function frame(play){
    if(active!==play || play.paused)return;
    try {
      const {image}=await play.decoder.decode({frameIndex:play.index});
      let delay;
      try{
        if(active!==play || play.paused)return;
        const c=play.canvas;
        if(c.width!==image.displayWidth || c.height!==image.displayHeight){c.width=image.displayWidth;c.height=image.displayHeight;}
        const ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);ctx.drawImage(image,0,0);
        delay=Math.max(50,(image.duration??100000)/1000);
      }finally{image.close();}
      play.index++;
      if(play.index===info.frames){
        play.index=0;
        if(info.plays && ++play.loops>=info.plays){
          play.decoder.close();play.decoder=null;play.finished=true;button.textContent='重新播放肖像';status.textContent='已播完';return;
        }
      }
      play.timer=win.setTimeout(()=>tick(play),delay);
    }catch{if(active===play)release('动画解码失败，已显示静态肖像。');}
  }
  async function verify(play){
    if(active!==play)return;
    const check=new win.AbortController();play.check=check;
    const deadline=win.setTimeout(()=>check.abort(),4000);
    try{
      if(!await stillPresent(check.signal) && active===play)release('来源成员已离场。');
    }catch{if(active===play)release('无法核对来源，已恢复静态肖像。');}
    finally{win.clearTimeout(deadline);}
    if(active===play)play.checkTimer=win.setTimeout(()=>verify(play),1000);
  }
  async function start(){
    beforePlay();release();
    const play={controller:new win.AbortController(),index:0,loops:0,paused:false};active=play;
    button.disabled=true;still.hidden=false;status.textContent='载入动画中';
    if(stillPresent)play.checkTimer=win.setTimeout(()=>verify(play),1000);
    try{
      const response=await win.fetch(url,{signal:play.controller.signal,mode:'same-origin'});
      if(!response.ok)throw Error();
      const bytes=await response.arrayBuffer();
      if(active!==play)return;
      play.decoder=new win.ImageDecoder({type:'image/png',data:bytes,preferAnimation:true});
      await play.decoder.tracks.ready;
      if(active!==play)return;
      if(play.decoder.tracks.selectedTrack?.frameCount!==info.frames)throw Error();
      play.canvas=doc.createElement('canvas');play.canvas.setAttribute('aria-hidden','true');face.append(play.canvas);
      button.disabled=false;button.textContent='暂停肖像';status.textContent='';
      await tick(play);
    }catch{if(active===play)release('此浏览器无法播放该动画，已显示静态肖像。');}
  }
  button.onclick=()=>{
    if(!active || active.finished)return start();
    if(active.paused){active.paused=false;button.textContent='暂停肖像';status.textContent='';if(!active.pending)tick(active);}
    else {active.paused=true;win.clearTimeout(active.timer);button.textContent='继续播放肖像';status.textContent='已暂停';}
  };
  // Serialize decodes if a user pauses and resumes during an outstanding frame.
  async function tick(play){
    if(play.pending)return;
    play.pending=true;
    try{await frame(play);}finally{play.pending=false;}
  }
  still.onclick=()=>release();
  const reduce=()=>{if(preference.matches)release('已按减少动态效果设置显示静态肖像。');};
  const hide=()=>release();
  preference?.addEventListener('change',reduce);win.addEventListener('pagehide',hide);
  if(preference?.matches)reduce();
  if(typeof win.ImageDecoder!=='function'){button.hidden=true;status.textContent='当前浏览器使用静态肖像。';}
  return {stop:()=>release(),dispose(){
    if(disposed)return;disposed=true;release();
    preference?.removeEventListener('change',reduce);win.removeEventListener('pagehide',hide);
  }};
}
