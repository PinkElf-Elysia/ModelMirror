import {useRef,useState} from 'react';
import {Modal} from './modal';
export function OutputEditor({turn,text,save,close}:{turn:number;text:string;save:(text:string,id:string)=>Promise<void>;close:()=>void}){
 const [draft,setDraft]=useState(text),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const request=useRef<{text:string;id:string}|null>(null);
 async function submit(){
  if(busy||!draft.trim())return;
  request.current??={text:draft,id:crypto.randomUUID()};
  setBusy(true);setError('');
  try{await save(request.current.text,request.current.id);close();}
  catch{setError('未确认保存结果。内容已保留，可用同一操作核对保存；若记录已变化，请关闭后恢复记录再编辑。');}
  finally{setBusy(false);}
 }
 return <Modal title="编辑回复" className="chat-dialog" close={()=>{if(!busy)close();}}>
  <form className="branch-form" onSubmit={e=>{e.preventDefault();void submit();}}>
   <label htmlFor="output-edit">第 {turn} 回合 · 回复原文</label>
   <textarea id="output-edit" autoFocus rows={18} maxLength={200000} value={draft} disabled={busy||!!request.current} onChange={e=>setDraft(e.target.value)}/>
   <p>保存会更新显示、复制、导出及后续历史上下文，不重新生成剧情，不调用模型。HTML 和 Markdown 按原文编辑。</p>
   <p>已有滚动总结、记忆及插图保留；后续请求会附上相关修订，请核对受影响的总结和记忆。已有后续剧情不会改写。</p>
   {error&&<p role="alert">{error}</p>}
   <div className="two"><button type="button" className="secondary" disabled={busy} onClick={close}>取消</button>
   <button className="chat-primary" disabled={busy||!draft.trim()}>{busy?'正在保存…':request.current?'核对保存':'保存'}</button></div>
  </form>
 </Modal>;
}
