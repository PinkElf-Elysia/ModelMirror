// Fixed display adapter, serialized into the opaque frame. It receives only
// validated commands and version-bound data URLs, never author JavaScript.
export function installDisplayPlatform(feedback, fatal) {
  const canvases=new Map(), contexts=new Map(), media=new Map();
  let closed=false, clock=0, layoutKeys=[];
  const element=key=>document.querySelector('[data-author-node="'+key+'"]');
  function canvas(key) {
    const node=element(key);if(node?.tagName!=='CANVAS')throw new Error('画布节点不存在');
    let record=canvases.get(key);
    if(record?.node!==node){record={node,context:node.getContext('2d'),gradients:new Map()};canvases.set(key,record);}
    if(!record.context)throw new Error('浏览器无法创建二维画布');return record;
  }
  function audio(id,op,args) {
    if(op==='context'){
      if(contexts.has(id))return;
      const Type=window.AudioContext||window.webkitAudioContext;if(!Type)throw new Error('浏览器不支持音效');
      const context=new Type(),master=context.createGain();master.gain.value=1;master.connect(context.destination);
      context.onstatechange=()=>{document.documentElement.dataset.displayAudioState=context.state;};
      contexts.set(id,{context,master,nodes:new Map(),edges:new Map()});return;
    }
    const record=contexts.get(id);if(!record)throw new Error('音效上下文不存在');
    const {context,nodes,edges,master}=record;
    const at=value=>context.currentTime+Math.max(0,value-clock/1000);
    if(['resume','suspend','close'].includes(op)){context[op]().catch(()=>{});return;}
    const [key,...rest]=args;
    if(op==='node'){
      if(nodes.size>=512)throw new Error('活动音效节点超过上限');
      const constructors={gain:()=>context.createGain(),oscillator:()=>context.createOscillator(),filter:()=>context.createBiquadFilter(),source:()=>context.createBufferSource()};
      const node=constructors[rest[0]]();nodes.set(key,node);
      if(rest[0]==='oscillator'||rest[0]==='source')node.onended=()=>{
        const prune=k=>{const n=nodes.get(k);n?.disconnect();nodes.delete(k);for(const target of edges.get(k)||[])if(target!==0&&!edges.get(target)?.has(0)&&!Array.from(edges).some(([other,links])=>other!==k&&nodes.has(other)&&links.has(target)))prune(target);edges.delete(k);};prune(key);
      };
      return;
    }
    const node=nodes.get(key);if(!node)throw new Error('音效节点不存在');
    if(op==='param'){
      const [name,method,value,time]=rest,parameter=node[name];if(!parameter)throw new Error('音效参数无效');
      if(method==='value')parameter.value=value;else parameter[method](value,at(time));
    }else if(op==='type')node.type=rest[0];
    else if(op==='buffer'){const buffer=context.createBuffer(1,rest[0].length,24000);buffer.copyToChannel(Float32Array.from(rest[0]),0);node.buffer=buffer;}
    else if(op==='connect'){const target=rest[0]===0?master:nodes.get(rest[0]);if(!target)throw new Error('音效连接无效');node.connect(target);if(!edges.has(key))edges.set(key,new Set());edges.get(key).add(rest[0]);}
    else if(op==='start'||op==='stop')node[op](at(rest[0]));
    else if(op==='disconnect'){node.disconnect();edges.delete(key);}
  }
  function apply(batch, phase='after') {
    if(closed||!batch)return;clock=batch.clock;layoutKeys=batch.layoutRequests;
    try{
      for(const [kind,id,op,...args] of batch.effects){
        if(kind==='reflow'){
          if(phase==='before'){const node=element(id);if(node){node.setAttribute('class',op);node.setAttribute('style',args[0]);void node.offsetWidth;}}
          continue;
        }
        if(phase==='before')continue;
        if(kind==='canvas'){
          const {context,gradients}=canvas(id);
          if(op==='set'){const [name,value]=args;context[name]=typeof value==='object'?gradients.get(value.gradient):value;}
          else if(op==='createRadialGradient'||op==='createLinearGradient'){
            if(gradients.size>=512)gradients.delete(gradients.keys().next().value);
            gradients.set(args[0],context[op](...args.slice(1)));
          }else if(op==='addColorStop'){const gradient=gradients.get(args[0]);if(!gradient)throw new Error('画布渐变已失效');gradient.addColorStop(...args.slice(1));}
          else context[op](...args);
        }else if(kind==='audio')audio(id,op,args);
        else if(kind==='media'){
          let record=media.get(id);
          if(op==='pause'){if(record){record.wanted=false;record.audio.pause();}continue;}
          const [source,loop,volume,receipt]=args;
          if(!/^data:audio\/mpeg;base64,[A-Za-z0-9+/]+=*$/.test(source)){feedback({id:receipt,ok:false});continue;}
          if(!record){record={audio:new Audio(source),wanted:false};record.audio.hidden=true;record.audio.dataset.displayMedia=String(id);document.head.append(record.audio);media.set(id,record);}
          if(record.audio.src!==source)record.audio.src=source;
          record.audio.loop=loop;record.audio.volume=volume;record.wanted=true;
          record.audio.play().then(()=>feedback({id:receipt,ok:true}),()=>feedback({id:receipt,ok:false}));
        }else{
          const node=element(id);if(!node)continue;
          if(kind==='scroll')node[op==='top'?'scrollTop':'scrollLeft']=args[0];
          else if(kind==='scrollIntoView')node.scrollIntoView(op);
          else if(kind==='focus')node.focus({preventScroll:true});
          else if(kind==='select')node.select?.();
          else if(kind==='selection')node.setSelectionRange?.(op,args[0]);
        }
      }
    }catch(error){fatal(String(error.message||error).slice(0,160));}
  }
  const unlock=event=>{if(!event.isTrusted||closed)return;for(const {context} of contexts.values())if(context.state==='suspended')context.resume().catch(()=>{});for(const record of media.values())if(record.wanted&&record.audio.paused)record.audio.play().catch(()=>{});};
  document.addEventListener('pointerdown',unlock,true);document.addEventListener('keydown',unlock,true);
  const stop=()=>{if(closed)return;closed=true;for(const {context} of contexts.values())context.close().catch(()=>{});for(const {audio} of media.values()){audio.pause();audio.removeAttribute('src');audio.load();audio.remove();}contexts.clear();media.clear();canvases.clear();};
  window.addEventListener('pagehide',stop);
  return {apply,stop,
    eventTarget:(target,ancestors)=>element(target)?target:ancestors.find(key=>element(key))||null,
    viewport:()=>({innerWidth:Math.max(1,Math.min(4096,innerWidth)),innerHeight:Math.max(1,Math.min(4096,innerHeight)),devicePixelRatio:Math.max(.5,Math.min(3,devicePixelRatio))}),
    layouts(target){
      const keys=new Set(layoutKeys);let node=target?element(target):null;
      for(;node;node=node.parentElement)if(node.dataset.authorNode)keys.add(node.dataset.authorNode);
      for(const item of document.querySelectorAll('[data-author-node]')){if(keys.size>=512)break;keys.add(item.dataset.authorNode);}
      return [...keys].slice(0,512).map(key=>{const item=element(key);if(!item)return null;const rect=item.getBoundingClientRect(),value={key};for(const name of ['x','y','left','right','top','bottom','width','height'])value[name]=rect[name];for(const name of ['scrollTop','scrollLeft','scrollHeight','scrollWidth','clientWidth','clientHeight','offsetWidth','offsetHeight'])value[name]=Number(item[name]||0);return value;}).filter(Boolean);
    },
  };
}
