(() => {
 const channel='blue-fish-editor-v1';
 let started=false, timer;
 const notify=(text)=>{let el=document.getElementById('storage-notice');if(!el){el=document.createElement('div');el.id='storage-notice';el.setAttribute('role','alert');document.body.appendChild(el);}el.textContent=text;};
 function begin(snapshot){
  if(started)return;started=true;clearInterval(timer);
  const state={local:Object.assign(Object.create(null),snapshot?.local),session:Object.assign(Object.create(null),snapshot?.session)};
  for(const [name,kind] of [['localStorage','local'],['sessionStorage','session']]){
   const data=state[kind];
   const save=()=>{if(window.parent===window){try{nativeStorage[kind].setItem(channel,JSON.stringify(data));}catch{notify('草稿未能保存，请导出文件备份。');}}else window.parent.postMessage({type:channel,action:'save',kind,values:{...data}},'*');};
   Object.defineProperty(window,name,{configurable:true,value:{
    getItem:key=>Object.hasOwn(data,String(key))?data[String(key)]:null,
    setItem(key,value){data[String(key)]=String(value);save();},
    removeItem(key){delete data[String(key)];save();},
    clear(){for(const key of Object.keys(data))delete data[key];save();},
    key:i=>Object.keys(data)[i]??null,get length(){return Object.keys(data).length;}
   }});
  }
  const script=document.createElement('script');script.src='/rpg-app/blue-fish/app.js';script.onerror=()=>notify('编辑器未能加载，请刷新重试。');document.head.appendChild(script);
 }
 const nativeStorage={};
 if(window.parent===window){
  let snapshot={};for(const [name,kind] of [['localStorage','local'],['sessionStorage','session']]){try{nativeStorage[kind]=window[name];snapshot[kind]=JSON.parse(window[name].getItem(channel)||'{}');}catch{notify('浏览器存储不可用，请及时导出文件。');}}
  begin(snapshot);
 }else{
  window.addEventListener('message',e=>{if(e.source!==window.parent||e.data?.type!==channel)return;if(e.data.action==='load')begin(e.data.snapshot);if(e.data.action==='error')notify('草稿未能保存，请导出文件备份。');});
  let attempts=0;
  const request=()=>{window.parent.postMessage({type:channel,action:'ready'},'*');if(++attempts===20){clearInterval(timer);notify('编辑器存储连接未就绪，请刷新页面。');}};
  timer=setInterval(request,250);request();
 }
})();
