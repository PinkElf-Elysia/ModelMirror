import {fail} from './contracts.mjs';

export function validatePlatform(value) {
  const bad=()=>fail('AUTHORING_PLATFORM_LIMIT');
  if(!value||Object.keys(value).sort().join()!=='clock,effects,layoutRequests,scheduled'||!Array.isArray(value.effects)||value.effects.length>12000||typeof value.scheduled!=='boolean'||!Number.isFinite(value.clock)||value.clock<0||value.clock>1e9)bad();
  const key=v=>typeof v==='string'&&/^n[1-9][0-9]{0,5}$/.test(v),num=v=>typeof v==='number'&&Number.isFinite(v)&&Math.abs(v)<=1e7;
  const audioId=v=>Number.isSafeInteger(v)&&v>=1&&v<=50000,time=v=>num(v)&&v>=0&&v<=1e6;
  if(!Array.isArray(value.layoutRequests)||value.layoutRequests.length>512||value.layoutRequests.some(v=>!key(v)))bad();
  const canvasMethods={clearRect:4,fillRect:4,strokeRect:4,beginPath:0,closePath:0,arc:5,moveTo:2,lineTo:2,fill:0,stroke:0,save:0,restore:0,setTransform:6,translate:2,rotate:1,scale:2,createLinearGradient:5,createRadialGradient:7};
  let audioSamples=0;
  for(const c of value.effects){
    if(!Array.isArray(c)||c.length<2||c.length>10)bad();const [kind,id,op,...args]=c;
    if(kind==='canvas'){
      if(!key(id))bad();
      if(op==='set'){
        if(args.length!==2||!['fillStyle','strokeStyle','globalAlpha','lineWidth','lineCap','lineJoin','globalCompositeOperation'].includes(args[0]))bad();
        const v=args[1];if(!(num(v)||typeof v==='string'&&v.length<=256||v&&Object.keys(v).join()==='gradient'&&Number.isSafeInteger(v.gradient)&&v.gradient>0&&v.gradient<=1e7))bad();
      }else if(op==='addColorStop'){if(args.length!==3||!num(args[0])||!num(args[1])||args[1]<0||args[1]>1||typeof args[2]!=='string'||args[2].length>256)bad();}
      else if(!Object.hasOwn(canvasMethods,op)||args.length!==canvasMethods[op]&&!(op==='arc'&&args.length===6)||args.some((v,i)=>op==='arc'&&i===5?typeof v!=='boolean':!num(v)))bad();
    }else if(kind==='audio'){
      if(!Number.isSafeInteger(id)||id<1||id>50000)bad();
      if(['context','resume','suspend','close'].includes(op)){if(args.length)bad();}
      else if(op==='node'){if(args.length!==2||!Number.isSafeInteger(args[0])||args[0]<1||args[0]>50000||!['gain','oscillator','filter','source'].includes(args[1]))bad();}
      else if(op==='param'){if(!audioId(args[0])||!['gain','frequency'].includes(args[1])||!['value','setValueAtTime','exponentialRampToValueAtTime','linearRampToValueAtTime'].includes(args[2])||args.length!==(args[2]==='value'?4:5)||!num(args[3])||args[3]<0||args[3]>(args[1]==='gain'?1:24000)||args.length===5&&!time(args[4]))bad();}
      else if(op==='type'){if(args.length!==2||!audioId(args[0])||!['sine','triangle','square','sawtooth','lowpass','highpass','bandpass'].includes(args[1]))bad();}
      else if(op==='buffer'){if(args.length!==2||!audioId(args[0])||!Array.isArray(args[1])||!args[1].length||args[1].length>96000||args[1].some(v=>!num(v)||Math.abs(v)>1))bad();audioSamples+=args[1].length;if(audioSamples>192000)bad();}
      else if(op==='connect'){if(args.length!==2||!audioId(args[0])||args[1]!==0&&!audioId(args[1]))bad();}
      else if(['start','stop'].includes(op)){if(args.length!==2||!audioId(args[0])||!time(args[1]))bad();}
      else if(op==='disconnect'){if(args.length!==1||!audioId(args[0]))bad();}else bad();
    }else if(kind==='media'){
      if(!Number.isSafeInteger(id)||id<1||id>1e7)bad();
      if(op==='pause'){if(args.length)bad();}
      else if(op!=='play'||args.length!==4||typeof args[0]!=='string'||args[0].length>2048||typeof args[1]!=='boolean'||!num(args[2])||args[2]<0||args[2]>1||!/^media[1-9][0-9]{0,8}$/.test(args[3]))bad();
    }else{
      if(!key(id))bad();
      if(kind==='reflow'){if(c.length!==4||typeof op!=='string'||op.length>240000||typeof args[0]!=='string'||args[0].length>240000)bad();}
      else if(kind==='scroll'){if(c.length!==4||!['top','left'].includes(op)||!num(args[0]))bad();}
      else if(kind==='scrollIntoView'){if(c.length!==3||!op||Object.keys(op).sort().join()!=='behavior,block'||!['smooth','instant'].includes(op.behavior)||!['start','center','end','nearest'].includes(op.block))bad();}
      else if(['focus','select'].includes(kind)){if(c.length!==2)bad();}
      else if(kind==='selection'){if(c.length!==4||!num(op)||!num(args[0]))bad();}else bad();
    }
  }
  return value;
}

// Pure guest-side platform. Serialized into QuickJS; never receives host objects.
export function installPlatform(bridge, options) {
  const timers=new Map(), layouts=new Map(), requested=new Set(), clips=new Map(), canvases=new Map();
  const NativePromise=Promise;
  let now=0, serial=0, effects=[], errors=[], viewport=options.viewport, gesture=false;
  const bad=()=>{throw new Error('AUTHORING_PLATFORM_LIMIT');};
  const number=(n,min=-1e7,max=1e7)=>{n=Number(n);if(!Number.isFinite(n)||n<min||n>max)bad();return n;};
  const text=(s,max=2048)=>{s=String(s);if(s.length>max)bad();return s;};
  const emit=(...command)=>{if(effects.length>=12000)bad();effects.push(command);};
  const track=value=>{if(value instanceof NativePromise)value.then(undefined,error=>errors.push(String(error?.message||error).slice(0,200)));return value;};
  const schedule=(callback,delay,args,interval=false,frame=false)=>{
    if(typeof callback!=='function')throw new Error('AUTHORING_SCRIPT_SOURCE_UNSUPPORTED');
    if(timers.size>=128)bad();delay=Math.max(frame?0:4,number(delay||0,0,86400000));
    const id=++serial;timers.set(id,{callback,args,due:now+delay,delay,interval,frame});return id;
  };
  Object.assign(globalThis,{
    setTimeout:(cb,delay,...args)=>schedule(cb,delay,args),clearTimeout:id=>timers.delete(id),
    setInterval:(cb,delay,...args)=>schedule(cb,delay,args,true),clearInterval:id=>timers.delete(id),
    requestAnimationFrame:cb=>schedule(cb,0,[],false,true),cancelAnimationFrame:id=>timers.delete(id),
    performance:Object.freeze({now:()=>now}),
    isSecureContext:true,
  });
  for(const key of ['innerWidth','innerHeight','devicePixelRatio'])Object.defineProperty(globalThis,key,{get:()=>viewport[key]});
  Date.now=()=>options.epoch+Math.floor(now);
  const clipboard={writeText(value){
    if(!gesture)throw new Error('AUTHORING_SCRIPT_GESTURE_REQUIRED');
    if(clips.size>=4)bad();const id='clip'+(++serial),payload=text(value,240000);
    const promise=new NativePromise((resolve,reject)=>clips.set(id,{resolve,reject}));
    bridge.propose({type:'copy',payload,id});return promise;
  }};
  Object.defineProperty(globalThis,'navigator',{value:Object.freeze({clipboard:Object.freeze(clipboard),userAgent:'ModelMirror isolated authoring'})});
  function canvas(key) {
    if(canvases.has(key))return canvases.get(key);
    if(canvases.size>=4)bad();let gradient=0;
    const properties=new Set(['fillStyle','strokeStyle','globalAlpha','lineWidth','lineCap','lineJoin','globalCompositeOperation']);
    const sizes={clearRect:4,fillRect:4,strokeRect:4,beginPath:0,closePath:0,arc:5,moveTo:2,lineTo:2,fill:0,stroke:0,save:0,restore:0,setTransform:6,translate:2,rotate:1,scale:2};
    const state={fillStyle:'#000',strokeStyle:'#000',globalAlpha:1,lineWidth:1};
    const api=new Proxy({}, {
      get(_,name){
        if(properties.has(name))return state[name];
        if(name==='createRadialGradient'||name==='createLinearGradient')return (...args)=>{
          if(args.length!==(name==='createRadialGradient'?6:4)||++gradient>1e7)bad();
          const id=gradient;emit('canvas',key,name,id,...args.map(n=>number(n)));
          return Object.freeze({gradient:id,addColorStop(offset,color){emit('canvas',key,'addColorStop',id,number(offset,0,1),text(color,256));}});
        };
        if(Object.hasOwn(sizes,name))return (...args)=>{
          if(args.length!==sizes[name]&&!(name==='arc'&&args.length===6))bad();
          emit('canvas',key,name,...args.map((n,i)=>name==='arc'&&i===5?!!n:number(n)));
        };
        throw new Error('AUTHORING_DOM_UNSUPPORTED: canvas.'+String(name));
      },
      set(_,name,value){if(!properties.has(name))bad();state[name]=value;emit('canvas',key,'set',name,typeof value==='object'?{gradient:number(value?.gradient,1,1e7)}:typeof value==='number'?number(value):text(value,256));return true;}
    });canvases.set(key,api);return api;
  }
  // Web Audio is a finite command graph, with no device input or network nodes.
  let audioSerial=0,contextCount=0;
  class AudioContext {
    constructor(){if(++contextCount>2)bad();this.id=++audioSerial;this.sampleRate=24000;this.destination={id:0};this.state='running';emit('audio',this.id,'context');}
    get currentTime(){return now/1000;}
    resume(){emit('audio',this.id,'resume');this.state='running';return NativePromise.resolve();}
    suspend(){emit('audio',this.id,'suspend');this.state='suspended';return NativePromise.resolve();}
    close(){emit('audio',this.id,'close');this.state='closed';return NativePromise.resolve();}
    node(type){
      const context=this.id,id=++audioSerial;if(audioSerial>50000)bad();emit('audio',context,'node',id,type);
      const param=name=>{let current=name==='gain'?1:440;const maximum=name==='gain'?1:24000;return new Proxy({}, {get:(_,key)=>{
        if(key==='value')return current;
        if(['setValueAtTime','exponentialRampToValueAtTime','linearRampToValueAtTime'].includes(key))return (value,time)=>{current=number(value,0,maximum);emit('audio',context,'param',id,name,key,current,number(time,0,1e6));};
        throw new Error('AUTHORING_DOM_UNSUPPORTED: AudioParam.'+String(key));
      },set:(_,key,value)=>{if(key!=='value')bad();current=number(value,0,maximum);emit('audio',context,'param',id,name,'value',current);return true;}});};
      const target={id,gain:param('gain'),frequency:param('frequency'),buffer:null,
        connect(other){emit('audio',context,'connect',id,number(other.id,0,50000));return other;},
        disconnect(){emit('audio',context,'disconnect',id);},
        start(time=now/1000){if(this.buffer)emit('audio',context,'buffer',id,Array.from(this.buffer.data));emit('audio',context,'start',id,number(time,0,1e6));},
        stop(time=now/1000){emit('audio',context,'stop',id,number(time,0,1e6));}};
      return new Proxy(target,{set(obj,key,value){if(key==='type'){emit('audio',context,'type',id,text(value,32));obj[key]=value;}else if(key==='buffer')obj[key]=value;else bad();return true;}});
    }
    createGain(){return this.node('gain');}createOscillator(){return this.node('oscillator');}
    createBiquadFilter(){return this.node('filter');}createBufferSource(){return this.node('source');}
    createBuffer(channels,length,rate){if(channels!==1||rate!==24000)bad();const data=new Float32Array(number(length,1,96000));return {data,getChannelData:channel=>{if(channel!==0)bad();return data;}};}
  }
  Object.assign(globalThis,{AudioContext,webkitAudioContext:AudioContext});
  const media=new Map();
  globalThis.Audio=class {
    constructor(src=''){if(media.size>=4)bad();this.id=++serial;this.src=text(src);this.loop=false;this.volume=1;this.paused=true;media.set(this.id,this);}
    play(){if(clips.size>=8)bad();const id='media'+(++serial);emit('media',this.id,'play',this.src,!!this.loop,number(this.volume,0,1),id);return new NativePromise((resolve,reject)=>{clips.set(id,{resolve:()=>{this.paused=false;resolve();},reject});});}
    pause(){this.paused=true;emit('media',this.id,'pause');}
  };
  return {
    track,canvas,emit,
    layout(key){requested.add(key);if(requested.size>512)bad();return layouts.get(key)||{x:0,y:0,left:0,top:0,right:0,bottom:0,width:0,height:0,scrollTop:0,scrollLeft:0,scrollHeight:0,scrollWidth:0,clientWidth:0,clientHeight:0,offsetWidth:0,offsetHeight:0};},
    begin(event){
      effects=[];errors=[];gesture=['click','submit','keydown','keyup'].includes(event.type);
      if(event.viewport)viewport=event.viewport;
      if(event.layouts){layouts.clear();for(const item of event.layouts)layouts.set(item.key,item);}
      // Dismissing a host copy proposal is not a browser copy failure. Do not
      // run author fallbacks (which sometimes report success unconditionally).
      if(event.type==='feedback')for(const item of event.feedback||[]){const pending=clips.get(item.id);if(pending){clips.delete(item.id);if(item.ok)pending.resolve();else if(item.id.startsWith('media'))pending.reject(new Error('播放未完成'));}}
      now+=Math.min(1000,Math.max(0,event.elapsed||0));
    },
    advance(){let count=0;const due=[...timers].filter(([,v])=>v.frame||v.due<=now);
      for(const [id,timer] of due){if(!timers.has(id))continue;if(++count>64)bad();if(timer.interval)timer.due=now+timer.delay;else timers.delete(id);track(timer.callback(...(timer.frame?[now]:timer.args)));}
    },
    take(){if(errors.length)throw new Error(errors[0]);gesture=false;const result={effects,layoutRequests:[...requested],scheduled:timers.size>0,clock:now};effects=[];return result;},
  };
}
