import {lazy, Suspense} from 'react';
import './ancient-frontier.css';

const MingAtlas = lazy(() => import('./ming-atlas'));

export type FrontierView = {
  date:string; observer:string;
  locations:{name:string; placeId:string|null; control:string; sources:string[]}[];
  known:{id:string; kind:string; placeId:string|null; text:string; occurredAt:number; receivedAt:number}[];
  actions:string[];
};

export function AncientFrontier({view,draft,disabled,open,setOpen}:{view:FrontierView;open:boolean;setOpen:(value:boolean)=>void;draft:(s:string)=>void;disabled:boolean}) {
  return <section className="frontier-register" aria-label="京师边务状态">
    <header><strong>京师边务簿</strong><small>{view.date} · {view.observer}</small></header>
    <p>宁远登记守方 <b>{view.locations.find(p=>p.name==='宁远')?.control}</b></p>
    {view.locations.some(p=>p.placeId==='tayouan')&&<p aria-label="台湾各地登记">{view.locations.filter(p=>['tayouan','keelung','taiwan-communities'].includes(p.placeId??'')).map(p=>p.name+' · '+p.control).join('　｜　')}</p>}
    <p>{view.known.at(-1)?.text || '御前现有开局城防登记，尚待新的递报。'}</p>
    <button type="button" className="atlas-open" onClick={()=>setOpen(true)}>展开全国舆图 <span aria-hidden="true">↗</span></button>
    {open && <Suspense fallback={<small role="status">正在展开舆图…</small>}>
      <MingAtlas view={view} close={()=>setOpen(false)}/>
    </Suspense>}
    <div className="frontier-choices" aria-label="带入对话">{view.actions.map(a=><button type="button" className="secondary" disabled={disabled} key={a} onClick={()=>draft(a)}>{a}</button>)}</div>
    <small>选择一句带入输入框，发送后生效。城防交割为本档推演。</small>
  </section>;
}
