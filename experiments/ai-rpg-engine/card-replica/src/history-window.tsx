import {useState} from 'react';
import {Modal} from './main';
import type {HistoryConfig} from './session-client';
export function HistoryWindowDialog({config,busy,pending,error,save,recover,close}:{config:HistoryConfig;busy:boolean;pending:boolean;error:string;save:(v:HistoryConfig)=>void;recover:()=>void;close:()=>void}){
 const [turns,setTurns]=useState(String(config.turns)),[include,setInclude]=useState(config.includeInitialCharacter);
 const n=Number(turns),valid=turns.trim()!==''&&Number.isInteger(n)&&n>=1&&n<=50;
 return <Modal title="历史窗口" className="chat-dialog history-dialog" close={close}><form onSubmit={e=>{e.preventDefault();if(valid&&!busy&&!pending)save({turns:n,includeInitialCharacter:include});}}>
 <label className="history-label" htmlFor="history-turns">保留最近完整回合</label><div className="history-stepper"><button type="button" aria-label="减少回合" disabled={busy||pending||!valid||n<=1} onClick={()=>setTurns(String(n-1))}>−</button><input autoFocus id="history-turns" type="number" inputMode="numeric" min={1} max={50} step={1} required value={turns} disabled={busy||pending} aria-describedby="history-range" onChange={e=>setTurns(e.target.value)}/><button type="button" aria-label="增加回合" disabled={busy||pending||!valid||n>=50} onClick={()=>setTurns(String(n+1))}>+</button></div><p className="dialog-hint" id="history-range">1–50 个完整回合；当前输入不计入。</p>
 {!valid&&<p role="alert">请输入 1–50 的整数。</p>}<label className="history-check"><input type="checkbox" checked={include} disabled={busy||pending} onChange={e=>setInclude(e.target.checked)}/>补入开局角色资料</label><p className="dialog-hint">开局资料可能已随剧情变化；开启后会再次提供原始设定。</p><p className="history-explanation dialog-hint">历史仍完整保存；此功能不自动总结。保存只影响后续发送。</p>
 {error&&<p role="alert">{error}</p>}{pending&&!busy&&<p className="dialog-hint">上次设置结果待核对，请先恢复。不会重新应用未保存的设置。</p>}
 <div className="two"><button type="button" className="secondary" disabled={busy} onClick={close}>取消</button>{pending&&!busy?<button type="button" className="chat-primary" disabled={busy} onClick={recover}>恢复设置结果</button>:<button className="chat-primary" disabled={busy||!valid}>{busy?'正在保存…':'保存'}</button>}</div></form></Modal>;
}
