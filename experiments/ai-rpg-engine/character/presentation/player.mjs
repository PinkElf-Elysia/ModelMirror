const formats={'audio/wav':'WAV','audio/mpeg':'MP3','video/mp4':'MP4'};
const clock=s=>Number.isFinite(s)?Math.floor(s/60)+':'+String(Math.floor(s%60)).padStart(2,'0'):'–:––';

// The native element and its source exist only between a user gesture and release.
export function createMediaPlayer(doc,readGroup=null) {
  const win=doc.defaultView;
  const element=doc.createElement('section');
  element.className='look-player';element.setAttribute('aria-label','角色包音视频');element.hidden=true;
  const label=doc.createElement('label');label.textContent='角色包音视频';
  const select=doc.createElement('select');label.append(select);
  const controls=doc.createElement('div');controls.className='playback-controls';
  function button(text){const b=doc.createElement('button');b.type='button';b.textContent=text;controls.append(b);return b;}
  const play=button('播放'),pause=button('暂停'),stop=button('停止');
  const position=doc.createElement('span');controls.append(position);
  const stage=doc.createElement('div'),status=doc.createElement('p');
  status.setAttribute('role','status');element.append(label,controls,stage,status);
  let scope='',tracks=[],current=null,timer=null,probe=null;

  function idle(message='') {
    win.clearTimeout(timer);timer=null;probe?.abort();probe=null;
    const previous=current;current=null;
    if(previous){
      const node=previous.node;
      node.onplaying=node.onpause=node.ontimeupdate=node.onended=node.onerror=null;
      node.pause();node.removeAttribute('src');node.load();node.remove();
    }
    play.textContent='播放';play.disabled=!select.value;pause.disabled=stop.disabled=true;
    position.textContent='';status.textContent=message;
  }
  function update(people) {
    tracks=people.flatMap((p,i)=>(p.media??[]).map(m=>({...m,member:p.id,
      label:(people.length>1?p.name+' · '+(i+1)+' / ':'')+m.name+' · '+formats[m.type]})));
    const last=select.value;
    if(current && !tracks.some(t=>t.key===current.track.key))idle('来源已离场，播放已停止。');
    select.replaceChildren(...tracks.map(t=>{const o=doc.createElement('option');o.value=t.key;o.textContent=t.label;return o;}));
    if(tracks.some(t=>t.key===last))select.value=last;
    element.hidden=!tracks.length;
    if(!current)play.disabled=!select.value;
  }
  function scopeChanged(path) {
    if(scope===path)return;
    scope=path;idle();update([]);
  }
  async function verify(active) {
    if(current!==active)return;
    const abort=new win.AbortController();probe=abort;
    const deadline=win.setTimeout(()=>abort.abort(),4000);
    try {
      const latest=await readGroup(scope,abort.signal);
      if(current!==active)return;
      if(!latest.people.some(p=>p.id===active.track.member && p.media?.some(m=>m.key===active.track.key)))update(latest.people);
    } catch {if(current===active)idle('无法核对在场状态，播放已停止。');}
    finally {win.clearTimeout(deadline);if(probe===abort)probe=null;}
    if(current===active)timer=win.setTimeout(()=>verify(active),1000);
  }
  function start() {
    const track=tracks.find(t=>t.key===select.value);
    if(!track || !scope)return;
    if(!current){
      const node=doc.createElement(track.type==='video/mp4'?'video':'audio');
      node.autoplay=false;node.loop=false;node.preload='none';node.playsInline=true;
      node.disableRemotePlayback=true;node.setAttribute('aria-label',track.label);
      const active={track,node};current=active;stage.replaceChildren(node);
      node.onplaying=()=>{if(current!==active)return;play.disabled=true;pause.disabled=false;status.textContent='播放中';};
      node.onpause=()=>{if(current!==active||node.ended)return;play.disabled=false;play.textContent='继续播放';pause.disabled=true;status.textContent='已暂停';};
      node.ontimeupdate=()=>{if(current===active)position.textContent=clock(node.currentTime)+' / '+clock(node.duration);};
      node.onended=()=>{if(current===active)idle('已播完');};
      node.onerror=()=>{if(current===active)idle('无法播放此文件，请检查浏览器解码支持或重新选择。');};
      node.src=scope+'/media/'+track.key;
      if(readGroup)timer=win.setTimeout(()=>verify(active),1000);
    }
    const active=current;
    status.textContent='正在载入…';play.disabled=true;pause.disabled=false;stop.disabled=false;
    active.node.play().catch(error=>{
      if(current===active && error.name!=='AbortError')idle('无法播放此文件，请检查浏览器解码支持或重新选择。');
    });
  }
  play.onclick=start;pause.onclick=()=>current?.node.pause();stop.onclick=()=>idle('已停止');
  select.onchange=()=>idle();
  win.addEventListener('pagehide',()=>idle());
  idle();
  return {element,update,scopeChanged,stop:()=>idle(),retain(ids){if(current&&!ids.includes(current.track.member))idle('来源已离场，播放已停止。');}};
}
