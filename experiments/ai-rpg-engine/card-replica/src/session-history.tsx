import {useEffect,useState} from 'react';
import {api} from './session-client';
import {Modal} from './modal';
import './session-history.css';
export type HistoryAction={action:'rewind'|'delete-turn'|'dismiss-request'|'restore-version'|'delete'|'restore-trash';id:string;name?:string;turn?:number;requestId?:string;versionId?:string;input?:string;retry?:boolean;discard?:boolean};
type Row={id:string;name:string;turns:number;reason?:string;at?:string;deletedAt?:string};
export function SessionHistoryDialog({kind,id,busy,choose,close}:{kind:'versions'|'trash';id:string;busy:boolean;choose:(action:HistoryAction)=>void;close:()=>void}){
 const [rows,setRows]=useState<Row[]>([]),[error,setError]=useState(''),[loading,setLoading]=useState(true);
 useEffect(()=>{let live=true;api(kind==='trash'?'session-trash':'sessions/'+id+'/lifecycle').then(v=>{if(live)setRows(kind==='trash'?v:v.versions.slice().reverse());}).catch(()=>{if(live)setError('暂时无法读取，请关闭后重试。');}).finally(()=>{if(live)setLoading(false);});return()=>{live=false;};},[kind,id]);
 return <Modal title={kind==='trash'?'回收站':'历史版本'} className="chat-dialog" close={close}>
 <p className="dialog-hint">{kind==='trash'?'移入回收站的会话和插件配置会保留，可随时恢复。':'重新生成前的回复、后续剧情和记忆保存在这里。恢复版本不会调用模型，当前内容也会保留为历史版本。'}</p>
 {loading?<p role="status">正在读取…</p>:error?<p role="alert">{error}</p>:!rows.length?<p>暂时没有{kind==='trash'?'已删除会话':'历史版本'}。</p>:<div className="session-version-list">{rows.map(row=><div key={row.id}><div><strong>{row.name}</strong><small>{row.turns} 回合 · {new Date(row.deletedAt||row.at||'').toLocaleString()}{row.reason==='delete-turn'?' · 删除回合前':row.reason==='dismiss-request'?' · 关闭失败请求前':row.reason==='rewind'?' · 重新生成前':row.reason==='restore-version'?' · 恢复版本前':''}</small></div><button className="secondary" disabled={busy} onClick={()=>choose(kind==='trash'?{action:'restore-trash',id:row.id,name:row.name}:{action:'restore-version',id,versionId:row.id,name:row.name})}>恢复</button></div>)}</div>}
 </Modal>;
}
export function SessionChangeDialog({target,busy,confirm,close}:{target:HistoryAction;busy:boolean;confirm:()=>void;close:()=>void}){
 const drop=target.action==='delete-turn',rewind=target.action==='rewind',remove=target.action==='delete';
 return <Modal title={drop?'删除本轮':rewind?'重新生成此回合':remove?'移入回收站':target.action==='restore-trash'?'恢复会话':'恢复历史版本'} className="chat-dialog" close={close}>
 <p>{drop?`将删除第 ${target.turn} 回合的输入与回复，并回到该回合开始前。该回合及后续剧情会保留为历史版本，总结与记忆同步回退，不调用模型。`:rewind?`将在当前会话重新生成第 ${target.turn} 回合。原回复和后续剧情会保存为历史版本，总结与记忆回到该回合开始时的状态。`:remove?`将「${target.name||'此会话'}」移入回收站，之后仍可恢复。`:'将恢复所选内容及对应的总结与记忆。此操作不会调用模型。'}</p>
 {rewind&&<p className="dialog-hint">继续后会调用当前选中的模型及已启用插件。会话地址保持不变。</p>}
 <div className="two"><button className="secondary" disabled={busy} onClick={close}>取消</button><button className="chat-primary" disabled={busy} onClick={confirm}>{busy?'正在处理…':drop?'保留版本并删除本轮':rewind?'保留版本并重新生成':remove?'移入回收站':'确认恢复'}</button></div>
 </Modal>;
}
