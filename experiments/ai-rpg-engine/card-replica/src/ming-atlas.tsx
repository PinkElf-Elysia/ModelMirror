import {useEffect, useRef, useState} from 'react';
import type {PointerEvent} from 'react';
import type {FrontierView} from './ancient-frontier';
import atlas from './ming-atlas-geometry.json';
import rivers from './ming-atlas-rivers.json';
import ranges from './ming-atlas-ranges.json';
import outerLand from './ming-surroundings-land.json';
import {domains, domainCenter, referencePlaces} from './ming-atlas-places';
import './ming-atlas.css';

const cities = [
  {id:'beijing',name:'北京',x:652,y:325.8}, {id:'shanhaiguan',name:'山海关',x:697.1,y:316.2},
  {id:'ningyuan',name:'宁远',x:707.6,y:302.5}, {id:'jinzhou',name:'锦州',x:711.3,y:292.6}
];
const colors = ['#aabb98','#ddbb96','#d2bd78','#b8acbb','#a5bfc1'];
const country = {x:602,y:488,w:665};
const surroundings = {x:556,y:466,w:1080};
const northeast = {x:773,y:253,w:298};
const northwest = {x:260,y:301,w:430};
const extentCameras:Record<string,typeof country> = {neighbors:surroundings,northeast,northwest};
const otherRanges = ranges.filter(r=>r.id!=='ming'&&!domains.some(d=>d.id===r.id));
const regionalLandmarks = new Set(['pidao','yehe','ula','shenyang','hanseong','mukden-steppe','nengjiang','hurka','kashgar','aksu','khotan','karashahr','turpan','kumul','tarbagatai','ili']);
const corridor = {x:683,y:310,w:115};
const source = 'https://commons.wikimedia.org/wiki/File:China_Ming-Dynastie_zh-Hans.svg';
const period = 'https://www.loc.gov/item/2002626722/';
type Camera = typeof country;
const clamp = (n:number, lo:number, hi:number) => Math.max(lo,Math.min(n,hi));
const constrain = ({x,y,w}:Camera) => ({x:clamp(x,70,1050),y:clamp(y,100,860),w:clamp(w,75,surroundings.w)});
const regionalNotes:Record<string,string> = {
  guizhou:'万历二十九年（1601），平越军民府划归贵州。川黔之间的局部府界仅作示意。',
  sichuan:'万历二十九年（1601），遵义军民府隶四川，平越军民府隶贵州。此处仅示省级近似边界。'
};
const sourceNote = '区界取明代概略图形。虚线边段为示意，边地军政范围另列。';

export default function MingAtlas({view,close}:{view:FrontierView;close:()=>void}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{x:number;y:number;start:Camera;scale:number;moved:boolean}|null>(null);
  const moved = useRef(false);
  const extent = new URLSearchParams(window.location.search).get('extent')||'';
  const preset=extentCameras[extent];
  const [camera,move] = useState<Camera>(preset||country);
  const [political,setPolitical] = useState(Boolean(preset));
  const [selected,select] = useState(preset?'surroundings':'bei-zhili');
  const region = atlas.regions.find(r=>r.id===selected);
  const portIds:Record<string,string>={dutch:'tayouan',spanish:'keelung'};
  const registeredId=portIds[selected]??selected;
  const portEntry=(id:string)=>view.locations.find(p=>p.placeId===portIds[id]);
  const city = view.locations.find(p=>p.placeId===registeredId);
  const place = referencePlaces.find(p=>p.id===selected);
  const domain = domains.find(p=>p.id===(place?.group||selected));
  const selectedRange = ranges.find(r=>r.id===(place?.group||selected));
  const recent = view.known.filter(e=>e.placeId===registeredId);
  const settlement = [...recent].reverse().find(e=>e.kind==='settled');
  const local = camera.w < 260;
  const [canvas,setCanvas] = useState({width:800,height:600});
  const wide = camera.w>country.w;
  const height = camera.w*(wide && canvas.width>700 ? 0.65 : 0.75);

  const pixel = Math.max(camera.w / canvas.width, height / canvas.height);
  useEffect(()=>{
    const observer=new ResizeObserver(([entry])=>{
      const {width,height}=entry.contentRect;
      if(width>0 && height>0)setCanvas({width,height});
    });
    observer.observe(svg.current!);
    return ()=>observer.disconnect();
  },[]);
  useEffect(()=>{
    const el=dialog.current!;
    el.showModal();
    return ()=>el.close();
  },[]);
  function zoom(factor:number) {
    move(c=>constrain({...c,w:c.w*factor}));
  }
  function goLocal() {
    setPolitical(true);
    move(corridor);
    select('ningyuan');
  }
  function start(e:PointerEvent<SVGSVGElement>) {
    const scale=svg.current?.getScreenCTM()?.a;
    if(!scale || e.button!==0 || !e.isPrimary)return;
    moved.current=false;
    drag.current={x:e.clientX,y:e.clientY,start:camera,scale,moved:false};
  }
  function pan(e:PointerEvent<SVGSVGElement>) {
    const d=drag.current;
    if(!d || !e.isPrimary)return;
    const dx=e.clientX-d.x,dy=e.clientY-d.y;
    if(!d.moved && Math.hypot(dx,dy)<5)return;
    if(!d.moved)e.currentTarget.setPointerCapture(e.pointerId);
    d.moved=moved.current=true;
    move(constrain({...d.start,x:d.start.x-dx/d.scale,y:d.start.y-dy/d.scale}));
  }
  function choose(id:string) {
    select(id);
    setPolitical(!atlas.regions.some(r=>r.id===id));
    if(id==='surroundings'){move(surroundings);return;}
    const site=referencePlaces.find(p=>p.id===id);
    const polity=domains.find(p=>p.id===id);
    if(site)move({x:site.xy[0],y:site.xy[1],w:100});
    else if(polity){
      const [x,y]=domainCenter(polity);
      move({x,y,w:polity.width});
    }else if(otherRanges.some(r=>r.id===id)){
      const area=otherRanges.find(r=>r.id===id)!;
      move({x:area.at[0],y:area.at[1],w:160});
    }else move(cities.some(c=>c.id===id) ? corridor : country);
  }
  return <dialog ref={dialog} className="ming-atlas" aria-labelledby="atlas-heading" onCancel={e=>{e.preventDefault();close();}}>
    <div className="atlas-layout">
      <header className="atlas-head">
        <div><small>{view.date} · 京师所见</small><h2 id="atlas-heading">大明舆图</h2></div>
        <button type="button" onClick={close} autoFocus aria-label="关闭地图，返回对话">返回对话 <span aria-hidden="true">×</span></button>
      </header>
      <div className="atlas-body">
        <div className="atlas-sheet">
          <nav aria-label="地图范围">
            <button type="button" onClick={()=>{choose('bei-zhili');}}>全国总览</button>
            <button type="button" onClick={goLocal}>定位辽东</button>
            <button type="button" onClick={()=>{choose('surroundings');}}>周边形势</button>
          </nav>
          <svg ref={svg} viewBox={[camera.x-camera.w/2,camera.y-height/2,camera.w,height].join(' ')}
            aria-label="明末行政区划、周边势力与城镇" role="group" onPointerDown={start} onPointerMove={pan}
            onPointerUp={()=>{drag.current=null;}} onPointerCancel={()=>{drag.current=null;}}
            onClickCapture={e=>{if(moved.current){e.stopPropagation();moved.current=false;}}}>
            <defs>
              <pattern id="atlas-sea" width="12" height="7" patternUnits="userSpaceOnUse" patternTransform={'scale('+Math.min(1,pixel)+')'}>
                <path d="M1 3 Q3 1.9 5 3 T9 3" fill="none" stroke="#86a59a" strokeWidth=".35" opacity=".4"/>
              </pattern>
              <pattern id="atlas-grazing" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform={`scale(${pixel})`}>
                <path d="M-1 1L1 -1M0 6L6 0M5 7L7 5" fill="none" stroke="#604d37" strokeWidth=".7" opacity=".28"/>
              </pattern>
              <mask id="atlas-outer" maskUnits="userSpaceOnUse" x="-500" y="-500" width="2200" height="2100">
                <rect x="-500" y="-500" width="2200" height="2100" fill="white"/><rect width="964.396" height="732.027" fill="black"/>
              </mask>
              <mask id="atlas-ground" maskUnits="userSpaceOnUse" x="-500" y="-500" width="2200" height="2100">
                <g fill="white" fillRule="evenodd" mask={wide?undefined:"url(#atlas-outer)"}>{outerLand.map((d,i)=><path key={i} d={d}/>)}</g>
                <g fill="white">{atlas.land.map((d,i)=><path key={i} d={d}/>)}</g>
              </mask>
            </defs>
            <rect x="-500" y="-500" width="2200" height="2100" fill="url(#atlas-sea)" pointerEvents="none"/>
            <g fill="#e4decd" mask={wide?undefined:"url(#atlas-outer)"} fillRule="evenodd">{outerLand.map((d,i)=><path key={i} d={d}/>)}</g>
            <g fill="#e4decd">{atlas.land.map((d,i)=><path key={i} d={d}/>)}</g>
            <g mask="url(#atlas-ground)">
              {ranges.map(r=>{
              const hatched=r.kind==='活动地域'||r.kind==='地域示意';
              const registered=portEntry(r.id);
              const portChanged=registered?.control==='明';
              const name=portChanged?registered.name+' · 本档登记明':r.name;
              return <g key={r.id} data-range={r.id} data-holder={registered?.control} data-kind={r.kind} onClick={()=>choose(r.id==='ming'?'surroundings':r.id)} className="atlas-range">
                <title>{name} · {r.kind}</title>
                {r.paths.map((path,i)=><g key={i}>
                  <path d={path} fillRule="evenodd" fill={portChanged?"#c79e82":r.color} fillOpacity={hatched ? .79 : .89}
                    stroke="#6f6255" strokeOpacity=".7" strokeWidth={.9*pixel} strokeDasharray={hatched?`${2*pixel} ${3*pixel}`:undefined}/>
                  {hatched&&<path d={path} fill="url(#atlas-grazing)" fillRule="evenodd" pointerEvents="none"/>}
                </g>)}
              </g>;
            })}</g>
            <path d={atlas.liaodong} className="atlas-frontier-outline"/>
            {atlas.regions.map(r=><path key={r.id} data-region={r.id} d={r.path} fill={political?"transparent":colors[r.color]}
              className={'atlas-province'+(political?' atlas-division':'')+(r.id===selected?' chosen':'')} onClick={()=>select(r.id)}><title>{r.name}</title></path>)}
            {political && camera.w<550 && <g className="atlas-rivers">{Object.entries(rivers).map(([name,paths])=><path
              key={name} d={paths.join(' ')} strokeWidth={.95*pixel}><title>{name} · 河流位置参照</title></path>)}</g>}
            {political && !local && <text className="atlas-ming-name" x="593" y="476" textAnchor="middle" fontSize={(canvas.width<500?15:22)*pixel}>大 明</text>}
            {!local && (!political||!wide) && <g className="atlas-lettering">{atlas.regions.map(r=><text key={r.id} x={r.label[0]} y={r.label[1]}>{r.name}</text>)}</g>}
            <g className="atlas-geography">
              <text x="700" y="350" fontSize={local?3:8}>渤　海</text>
              <text x="785" y="463" fontSize="9">黄　海</text><text x="794" y="565" fontSize="10">东　海</text>
              <text x="355" y="478" fontSize="10">青 藏 高 原</text>
              <text x="540" y="295" fontSize="10">漠　南</text>
              <text x="732" y="286" fontSize={local?2:9}>辽 东</text>
            </g>
            {referencePlaces.map(p=>{
              const [x,y]=p.xy;
              const active=selected===p.id;
              const detailed=camera.w<265 && (p.group===domain?.id || p.group==='ming' || camera.w<120);
              const [dx,dy]=p.offset||[7,-7];
              const registered=view.locations.find(row=>row.placeId===p.id);
              const port=p.group==='dutch'||p.group==='spanish';
              return <g key={p.id} data-reference-city={p.id} data-holder={registered?.control} className="atlas-reference" transform={`translate(${x} ${y})`} onClick={()=>choose(p.id)}>
                <title>{p.name} · {registered?'本档登记：'+registered.control:'开局地理'}</title>
                <circle r={(local?6:3)*pixel} fill="transparent"/>
                {port&&<circle r={(wide?3:4)*pixel} fill="none" stroke={registered?.control==='明'?'#923c31':p.group==='dutch'?'#a05323':'#674783'} strokeWidth={1.4*pixel}/>}
                <circle r={(local?3.2:1.9)*pixel} fill={p.group==='ming'?'#665738':'#66534e'} stroke="#faf2df" strokeWidth={pixel*.75}/>
                {(active||detailed||(political&&camera.w<550&&regionalLandmarks.has(p.id)))&&<g className="atlas-place-label">
                  <line x2={dx*pixel*.82} y2={dy*pixel*.82} stroke="#897557" strokeWidth={pixel*.75}/>
                  <text x={dx*pixel} y={dy*pixel} textAnchor={dx<0?'end':dx===0?'middle':'start'} dominantBaseline="central" fontSize={12*pixel}>{p.name}{port&&registered?' · '+registered.control:''}</text>
                </g>}
              </g>;
            })}
            {camera.w>=265&&domains.map(d=>{
              const [x,y]=d.at;
              const [cx,cy]=domainCenter(d);
              const entry=portEntry(d.id);
              const size=(canvas.width<500?10.5:14)*pixel;
              return <g key={d.id} data-polity={d.id} className="atlas-polity" onClick={()=>choose(d.id)}>
                <title>{d.name} · 查看据点</title>
                <line x1={x} y1={y+size*.25} x2={cx} y2={cy} strokeWidth={pixel*.75}/>
                <text x={x} y={y} fontSize={size} textAnchor="middle">{entry?entry.name+' · '+entry.control:d.label}</text>
              </g>;
            })}
            {political && otherRanges.filter(r=>r.overview || camera.w<550).map(r=>{
              const [x,y]=r.at;
              if(x<camera.x-camera.w/2||x>camera.x+camera.w/2||y<camera.y-height/2||y>camera.y+height/2)return null;
              return <text key={r.id} data-area-label={r.id} className="atlas-secondary-label" x={x} y={y}
                fontSize={(canvas.width<500?9:12)*pixel} textAnchor="middle" onClick={()=>choose(r.id)}>{r.name}</text>;
            })}
            <polyline className="atlas-road" points={cities.map(c=>c.x+','+c.y).join(' ')}/>
            {cities.map((c,i)=>{
              const entry=view.locations.find(p=>p.placeId===c.id);
              const right=i>0;
              const size=local?5:wide?2.8:4;
              const unit=size*pixel;
              const isJin=entry?.control==='后金';
              return <g key={c.id} data-city={c.id} data-holder={entry?.control} className="atlas-city"
                onClick={()=>choose(c.id)} transform={'translate('+c.x+' '+c.y+')'}>
                <title>{c.name} · {entry?.control||'未登记'}</title>
                <circle r={13*pixel} fill="transparent"/>
                {selected===c.id&&<circle r={unit*1.95} fill="none" stroke="#613c21" strokeWidth={pixel}/>}
                {isJin?<path d={'M0 '+(-unit*1.5)+' l'+unit*1.5+' '+unit*1.5+' -'+unit*1.5+' '+unit*1.5+' -'+unit*1.5+' -'+unit*1.5+'Z'} fill="#294f66"/>:
                <circle r={unit} fill="#893724" stroke="#fff0ba" strokeWidth={pixel}/>}
                {(local||(!wide&&(i===0||i===3)))&&<text x={(right?1:-1)*8*pixel} y={-6*pixel} textAnchor={right?'start':'end'} fontSize={14*pixel}>{c.name}</text>}
              </g>;
            })}
          </svg>
          <div className="atlas-compass" aria-hidden="true">北<span>↑</span></div>
          <div className="atlas-zoom" aria-label="地图缩放"><button type="button" disabled={camera.w<=75} onClick={()=>zoom(.72)} aria-label="放大">＋</button><button type="button" disabled={camera.w>=surroundings.w} onClick={()=>zoom(1.4)} aria-label="缩小">−</button></div>
        </div>
        <aside className="atlas-record">
          <label>查阅地点<select aria-label="查阅地点" value={selected} onChange={e=>choose(e.target.value)}>
            <optgroup label="京辽四城">{cities.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</optgroup>
            <optgroup label="两京十三布政使司">{atlas.regions.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</optgroup>
            <option value="surroundings">周边形势总览</option>
            <optgroup label="周边势力与据点">{domains.map(d=><option key={d.id} value={d.id}>{d.name}</option>)}</optgroup>
            <optgroup label="毗邻势力与活动地域">{otherRanges.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</optgroup>
            <optgroup label="明境城镇">{referencePlaces.filter(p=>p.group==='ming').map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</optgroup>
            <optgroup label="域外城镇与地理参照">{referencePlaces.filter(p=>p.group!=='ming').map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</optgroup>
          </select></label>
          <h3>{region?.name||city?.name||place?.name||domain?.name||selectedRange?.name||'周边形势'}</h3>
          {selected==='surroundings'&&<><p>崇祯元年 · 七月</p><small>实色示开局势力，斜纹示活动地域。区域共边，虚线概示活动范围。留白处的势力归属待考。点选色块可查依据。</small>
            <div className="atlas-site-links"><button onClick={()=>move(northeast)}>详看东北</button><button onClick={()=>move(northwest)}>详看西北</button></div>
            <div className="atlas-site-links">{domains.map(d=><button key={d.id} onClick={()=>choose(d.id)}>{d.label}</button>)}</div></>}
          {place&&<p>{place.group==='ming'?'明境与驻点':selectedRange?.name} · 开局地理</p>}
          {place?.id==='pidao'&&<p>毛文龙东江镇的海上据点，朝鲜本土另行标色。</p>}
          {place?.id==='macao'&&<p>明代香山县地，葡商在此居留贸易。</p>}
          {selectedRange&&<><small className="atlas-range-caption"><i style={{background:selectedRange.color}}/>{selectedRange.kind} · 1628 年概略</small>
            <p>{selectedRange.note}</p>{domain&&<p>{domain.note}</p>}{domain?.id==='later-jin'&&<p>底色按开局宁锦防线修订。城旁符号随本档交割更新。</p>}
            <small><a href={selectedRange.source} target="_blank" rel="noreferrer">范围资料</a>
              {domain&&domain.source!==selectedRange.source&&<> · <a href={domain.source} target="_blank" rel="noreferrer">地方资料</a></>}</small>
            <div className="atlas-site-links">{referencePlaces.filter(p=>p.group===selectedRange.id).map(p=><button key={p.id} onClick={()=>choose(p.id)} aria-pressed={p.id===selected}>{p.name}</button>)}</div></>}
          {region&&<><p>{region.id.endsWith('zhili')?'直隶区域':'布政使司'} · {region.seat}</p><small>{regionalNotes[region.id] || sourceNote}</small>{regionalNotes[region.id]&&<p><a href="https://www.gzxw.gov.cn/wmxf/23765" target="_blank" rel="noreferrer">播州分置记载</a></p>}</>}
          {city&&<><p className="atlas-holder">登记守方 <b>{city.control}</b></p> {settlement && <p className="atlas-basis">第 {settlement.receivedAt} 刻确认 · {settlement.text}</p>}
            {city.sources.length>0 && <small>{city.sources.join('；')}</small>}<h4>京师获报</h4>
            {recent.length?<ol>{recent.map(e=><li key={e.id}>{e.text}<small>事发第 {e.occurredAt} 刻 · 京师第 {e.receivedAt} 刻获知</small></li>)}</ol>:<p>京师尚未收到关于{city.name}的新报。</p>}</>}
        </aside>
      </div>
      <footer><div className="atlas-key"><span><i/>{political?"开局势力":"行政区划"}</span><span>● 明</span><span>◆ 后金</span><span>· 地理点位</span><span>▨ 地域／活动区</span><span>留白 · 待考</span></div>
        <small>图形改绘自 <a href={source} target="_blank" rel="noreferrer">Furfur / Jason22</a> · <a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noreferrer">CC BY-SA 4.0</a> · <a href="https://www.naturalearthdata.com/about/terms-of-use/" target="_blank" rel="noreferrer">外围海岸 Natural Earth</a> · <a href="https://github.com/Seshat-Global-History-Databank/cliopatria/tree/v0.2.1" target="_blank" rel="noreferrer">疆域改绘 Seshat</a>（<a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noreferrer">CC BY 4.0</a>） · <a href={period} target="_blank" rel="noreferrer">明末图籍</a></small></footer>
    </div>
  </dialog>;
}
