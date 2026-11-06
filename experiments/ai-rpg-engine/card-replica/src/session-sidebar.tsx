import {useEffect,useRef,useState} from 'react';
import type {ReactNode} from 'react';

/** A native modal on narrow screens keeps focus inside the session drawer. */
export function SessionSidebar({open,close,children}:{open:boolean;close:()=>void;children:ReactNode}){
 const [compact,setCompact]=useState(()=>window.innerWidth<=800);
 const ref=useRef<HTMLDialogElement>(null);
 useEffect(()=>{
  const resize=()=>setCompact(window.innerWidth<=800);
  window.addEventListener('resize',resize);
  return()=>window.removeEventListener('resize',resize);
 },[]);
 useEffect(()=>{
  const dialog=ref.current;
  if(!dialog)return;
  if(open&&!dialog.open)dialog.showModal();
  else if(!open&&dialog.open)dialog.close();
  return()=>dialog.close();
 },[open,compact]);
 if(compact)return <dialog ref={ref} className="chat-sidebar" aria-label="会话列表" onCancel={event=>{event.preventDefault();close();}} onClick={event=>{if(event.target===event.currentTarget){const r=event.currentTarget.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)close();}}}>{children}</dialog>;
 return <aside className="chat-sidebar" aria-label="会话列表" hidden={!open}>{children}</aside>;
}
