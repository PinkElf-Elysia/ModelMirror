import {animatedFace} from './portrait.mjs';
// Images are requested from the current host using scope-bound handles.
export function createLookPanel(root,request,player=null) {
  const doc=root.ownerDocument;
  function node(tag,cls,text) {
    const n=doc.createElement(tag);
    if(cls)n.className=cls;
    if(text)n.textContent=text;
    return n;
  }
  const canvas=node('div','look-canvas'), cast=node('div','look-cast');
  const details=node('details','look-controls'), summary=node('summary',null,'表情与背景');
  const fields=node('div','look-fields'), status=node('p','look-status');
  status.setAttribute('role','status');
  function select(label) {
    const wrapper=node('label',null,label), s=node('select');wrapper.append(s);fields.append(wrapper);return s;
  }
  const who=select('角色'), emotion=select('表情'), background=select('场景背景');
  details.append(summary,fields);canvas.append(cast);root.append(canvas,details,status);
  if(player)root.append(player.element);
  let key='',endpoint='',stamp=0,view=null,readonly=false,waiting=false;
  const portraits=[];
  function clearPortraits(){for(const portrait of portraits.splice(0))portrait.dispose();}
  const title=(p,i)=>view.people.length>1?p.name+' · '+(i+1):p.name;
  function choices(s,items,selected) {
    s.replaceChildren(...items.map(([v,t])=>{const o=node('option',null,t);o.value=v;return o;}));s.value=selected||'';
  }
  function image(handle,alt,cls,fallback=null) {
    const img=node('img',cls);img.alt=alt;
    img.onerror=()=>{
      if(!root.contains(img))return;
      if(fallback)img.replaceWith(image(fallback,alt,cls));
      else img.remove();
      status.textContent='一张图片暂不可用，已显示默认画面。';
    };
    img.src=endpoint+'/image/'+handle;
    return img;
  }
  function paint() {
    clearPortraits();
    const previous=who.value;
    const person=view.people.find(p=>p.id===previous)??view.people[0];
    if (!person){player?.update([]);root.hidden=true;return;}
    const hasChoice=view.people.some(p=>p.expressions.length||p.backgrounds.length);
    const hasPortrait=view.people.some(p=>p.animation);
    player?.update(view.people);
    root.hidden=!hasChoice && !hasPortrait && (!player || player.element.hidden);
    canvas.hidden=!hasChoice && !hasPortrait;details.hidden=!hasChoice;
    if(canvas.hidden)return;
    choices(who,view.people.map((p,i)=>[p.id,title(p,i)]),person.id);
    who.parentElement.hidden=view.people.length===1;
    choices(emotion,[['','恢复角色肖像'],...person.expressions.map(o=>[o.key,o.name])],view.expressions[person.id]);
    const options=[['','清除背景']];
    view.people.forEach((p,i)=>p.backgrounds.forEach(o=>options.push([o.key,(view.people.length>1?title(p,i)+' · ':'')+o.name])));
    choices(background,options,view.background);
    cast.replaceChildren();
    view.people.forEach((p,i)=>{
      const fig=node('figure'),face=node('span','look-face',Array.from(p.name||'角')[0]);
      const h=view.expressions[p.id]||p.portrait?.key;
      if(h)face.append(image(h,p.name+'的当前肖像','',view.expressions[p.id]?p.portrait?.key:null));
      const caption=node('figcaption',null,title(p,i));fig.append(face,caption);cast.append(fig);
      if(p.animation && !view.expressions[p.id]){
        fig.className='animated-portrait';
        const controls=node('div','portrait-controls');caption.append(controls);
        portraits.push(animatedFace(face,controls,endpoint+'/animation/'+p.animation.key,p.animation,
           ()=>portraits.forEach(portrait=>portrait.stop()),
          endpoint.startsWith('/api/appearance/group/')?async signal=>{
            const latest=await request(endpoint,undefined,signal);
            return latest.people.some(member=>member.id===p.id && member.animation?.key===p.animation.key);
          }:null));
      }
    });
    canvas.querySelector('.look-landscape')?.remove();
    if(view.background)canvas.prepend(image(view.background,'','look-landscape'));
    who.disabled=waiting;emotion.disabled=waiting||!person.expressions.length;background.disabled=waiting||options.length===1;
  }
  async function change(role,memberId,selected) {
    if(waiting||!view)return;
    const current=stamp;
    const body={revision:view.revision,role,memberId,key:selected||null};
    if(readonly){
      if(role==='expression')view.expressions[memberId]=body.key;
      else view.background=body.key;
      paint();return;
    }
    waiting=true;paint();status.textContent='正在保存画面…';
    try {
      const next=await request(endpoint,body);
      if(current!==stamp)return;
      view=next;status.textContent='画面已保存';
    } catch {
      if(current!==stamp)return;
      status.textContent='画面未确认，正在重新读取。';
      try{const latest=await request(endpoint);if(current!==stamp)return;view=latest;status.textContent='已读取当前画面，请核对后再选择。';}
      catch{if(current===stamp){key='';view=null;canvas.hidden=true;details.hidden=true;status.textContent='暂时无法读取画面，请刷新重试。';}}
    } finally {if(current===stamp){waiting=false;if(view)paint();}}
  }
  who.onchange=paint;
  emotion.onchange=()=>change('expression',who.value,emotion.value);
  background.onchange=()=>{
    const owner=view.people.find(p=>p.backgrounds.some(b=>b.key===background.value));
    return change('background',owner?.id??who.value,background.value);
  };
  return async function show(path='',generation='',preview=false) {
    player?.scopeChanged(path);
    const identity=path+'|'+generation;
    if(key===identity)return;
    clearPortraits();key=identity;const current=++stamp;endpoint=path;readonly=preview;waiting=false;view=null;
    root.hidden=true;canvas.hidden=false;details.hidden=false;details.open=false;cast.replaceChildren();
    canvas.querySelector('.look-landscape')?.remove();status.textContent='';
    if(!path)return;
    try {const next=await request(path);if(current!==stamp)return;view=next;paint();}
    catch {if(current===stamp){player?.scopeChanged('');key='';root.hidden=false;canvas.hidden=true;details.hidden=true;status.textContent='角色画面暂不可用。';}}
  };
}
