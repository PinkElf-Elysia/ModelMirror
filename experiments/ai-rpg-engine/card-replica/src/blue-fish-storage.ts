const channel='blue-fish-editor-v1';
const valid=(v:unknown):v is Record<string,string>=>!!v&&typeof v==='object'&&!Array.isArray(v)&&Object.entries(v).length<128&&Object.entries(v).every(([k,x])=>k.length<128&&typeof x==='string')&&JSON.stringify(v).length<=4_000_000;
export function blueFishStorage(event:MessageEvent,frame:HTMLIFrameElement|null){
 if(!frame||event.source!==frame.contentWindow||event.origin!=='null'||event.data?.type!==channel)return false;
 const reply=(data:object)=>frame.contentWindow?.postMessage({type:channel,...data},'*');
 try{
  if(event.data.action==='download'){
   const {text,name}=event.data;
   if(typeof text!=='string'||text.length>4_000_000||typeof name!=='string'||name.length>180||/[\\/\x00-\x1f]/.test(name)||! /\.(md|json)$/.test(name))throw Error();
   const url=URL.createObjectURL(new Blob([text],{type:name.endsWith('.json')?'application/json;charset=utf-8':'text/markdown;charset=utf-8'}));
   const link=document.createElement('a');link.href=url;link.download=name;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }else if(event.data.action==='ready'){
   const local=JSON.parse(localStorage.getItem(channel)||'{}'),session=JSON.parse(sessionStorage.getItem(channel)||'{}');
   if(!valid(local)||!valid(session))throw Error();
   reply({action:'load',snapshot:{local,session}});
  }else if(event.data.action==='save'){
   const {kind,values}=event.data;if(!['local','session'].includes(kind)||!valid(values))throw Error();
   (kind==='local'?localStorage:sessionStorage).setItem(channel,JSON.stringify(values));
  }
 }catch{reply({action:'error'});}
 return true;
}
