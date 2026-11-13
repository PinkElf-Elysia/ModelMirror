import {useRef,useState} from 'react';
import {parse,serialize,MAX_BYTES} from '../../plugins/transcript.mjs';
import {Modal} from './modal';
import {archiveRoot} from './session-client';

const errors:Record<string,string>={
  TRANSCRIPT_TOO_LARGE:'文件超过 10 MB。',TRANSCRIPT_TOO_MANY:'文件超过 500 轮。',
  TRANSCRIPT_EMPTY:'没有完整的用户消息和 AI 回复，请检查模板。',TRANSCRIPT_UTF8:'请将文件另存为 UTF-8 编码。',
  TRANSCRIPT_BINARY:'文件必须是纯文本。',TRANSCRIPT_NAME:'请输入 1 至 80 字的会话名称。',
  PLUGIN_NOT_AUTHORIZED:'请重新确认当前会话的插件授权。'
};
function save(blob:Blob,name:string){
  const url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export function TranscriptTools({id,name,disabled,opened}:{id:string;name:string;disabled:boolean;opened:(id:string)=>void}){
  const [dialog,setDialog]=useState(false),[target,setTarget]=useState(''),[file,setFile]=useState<File|null>(null);
  const [preview,setPreview]=useState<{count:number;skipped:number}|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const version=useRef(0),op=useRef(''),lock=useRef(false);
  const path=archiveRoot()+'api/sessions/'+id+'/transcript/';
  async function select(f:File|null){
    const current=++version.current;setPreview(null);setFile(f);op.current=crypto.randomUUID();setError('');
    if(!f)return;
    try{
      if(!/\.txt$/i.test(f.name))throw Error('请选择 .txt 文件。');
      if(f.size>MAX_BYTES)throw Error('TRANSCRIPT_TOO_LARGE');
      const result=parse(new Uint8Array(await f.arrayBuffer()));
      if(version.current===current)setPreview({count:result.turns.length,skipped:result.skipped});
    }catch(e){if(version.current===current)setError(errors[(e as Error).message]||(e as Error).message);}
  }
  async function transfer(importing:boolean){
    if(lock.current||disabled)return;
    if(importing&&(!file||!preview||!target.trim()))return;
    lock.current=true;setBusy(true);setError('');
    try{
      const r=await fetch(path+(importing?'import':'export'),importing?{method:'POST',headers:{'content-type':'text/plain','x-import-id':op.current,'x-session-name':encodeURIComponent(target.trim())},body:file}:{});
      if(!r.ok){const code=(await r.json()).error;throw Error(errors[code]||'操作失败，请核对会话与插件状态。');}
      if(importing){const result=await r.json();setDialog(false);opened(result.id);}
      else save(await r.blob(),(name.replace(/[<>:"/\\|?*]/g,'_').slice(0,80)||'聊天记录')+'.txt');
    }catch(e){setError((e as Error).message);}
    finally{lock.current=false;setBusy(false);}
  }
  return <><button type="button" className="text-button" disabled={busy||disabled} onClick={()=>void transfer(false)}>导出记录</button>
  <button type="button" className="text-button" disabled={busy||disabled} onClick={()=>{setError('');setTarget('');void select(null);setDialog(true);}}>导入聊天记录</button>
  {!dialog&&error&&<span role="alert">{error}</span>}
  {dialog&&<Modal title="导入聊天记录" className="chat-dialog" close={()=>{if(!busy)setDialog(false);}}>
    <form className="branch-form" onSubmit={e=>{e.preventDefault();void transfer(true);}}>
      <label htmlFor="transcript-name">新会话名称</label><input id="transcript-name" autoFocus required maxLength={80} disabled={busy} value={target} onChange={e=>{op.current=crypto.randomUUID();setTarget(e.target.value);}}/>
      <label htmlFor="transcript-file">上传 TXT 文件</label><input id="transcript-file" type="file" accept=".txt,text/plain" disabled={busy} onChange={e=>void select(e.target.files?.[0]??null)}/>
      <p>选择 UTF-8 纯文本，最大 10 MB，最多 500 轮。每轮以 [USER]: 和 [AI]: 标记双方消息，分隔线见模板。</p>
      <button type="button" className="text-button" onClick={()=>save(new Blob([serialize([{input:'你好',raw:'你好，有什么可以帮你？'},{input:'我们继续吧。',raw:'好的。'}])],{type:'text/plain;charset=utf-8'}),'聊天记录模板.txt')}>下载模板</button>
      {preview&&<p role="status">将导入 {preview.count} 轮，跳过 {preview.skipped} 个残缺或格式错误的片段。</p>}
      {error&&<p role="alert">{error}</p>}
      <p>导入后打开新会话，可接着聊天。原会话保留。</p>
      <button type="button" className="secondary" disabled={busy} onClick={()=>setDialog(false)}>取消</button>
      <button className="chat-primary" disabled={busy||disabled||!preview||!target.trim()}>{busy?'正在导入…':'导入'}</button>
    </form></Modal>}</>;
}
