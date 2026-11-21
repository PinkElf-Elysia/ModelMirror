import {useState} from 'react';
import './ancient-frontier.css';

export type FrontierView={
  date:string;observer:string;locations:{name:string;control:string;sources:string[]}[];
  known:{id:string;text:string;occurredAt:number;receivedAt:number}[];actions:string[];
};
export function AncientFrontier({view,draft,disabled}:{view:FrontierView;draft:(s:string)=>void;disabled:boolean}){
  const [selected,select]=useState('宁远');
  const current=view.locations.find(p=>p.name===selected)!;
  return <section className="frontier-register" aria-label="京师边务状态">
    <header><strong>京师边务簿</strong><small>{view.date} · {view.observer}</small></header>
    <p>宁远登记守方 <b>{view.locations.find(p=>p.name==='宁远')?.control}</b></p>
    <p>{view.known.at(-1)?.text||'御前现有开局城防登记，尚待新的递报。'}</p>
    <details><summary>查看京师—辽东地图</summary>
      <div className="frontier-route" aria-label="四地驿路示意">
        {view.locations.map((p,i)=><button type="button" key={p.name} style={{alignSelf:['end','end','start','start'][i]}} aria-pressed={selected===p.name} onClick={()=>select(p.name)}>{p.name}<small>{p.control}</small></button>)}
      </div>
      <p><strong>{selected}</strong> · {current.sources.join('；')}</p>
      {selected==='宁远'&&<ul>{view.known.map(e=><li key={e.id}>{e.text} <small>事发第 {e.occurredAt} 刻 · 京师第 {e.receivedAt} 刻获知</small></li>)}</ul>}
    </details>
    <div className="frontier-choices" aria-label="带入对话">{view.actions.map(a=><button type="button" className="secondary" disabled={disabled} key={a} onClick={()=>draft(a)}>{a}</button>)}</div>
    <small>选择一句带入输入框，发送后生效。城防交割为本档推演。</small>
  </section>;
}
