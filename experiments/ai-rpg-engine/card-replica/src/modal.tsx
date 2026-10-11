import {useEffect,useRef} from 'react';
import type {ReactNode} from 'react';
export function Modal({title,close,children,className}:{title:string;close:()=>void;children:ReactNode;className?:string}){
 const ref=useRef<HTMLDialogElement>(null);
 useEffect(()=>{ref.current?.showModal();return()=>ref.current?.close();},[]);
 return <dialog ref={ref} className={className} aria-label={title} onCancel={e=>{e.preventDefault();close();}}><header><h2>{title}</h2><button className="icon" onClick={close} aria-label="关闭">×</button></header>{children}</dialog>;
}
