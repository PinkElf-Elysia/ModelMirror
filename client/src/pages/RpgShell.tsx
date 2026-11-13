import type {ReactNode} from 'react';
import {Link,NavLink} from 'react-router-dom';
import {workbenchHref} from './rpgLibrary';
import './rpg-ui.css';
export default function RpgShell({children,compact=false,reader=false,library=false}:{children:ReactNode;compact?:boolean;reader?:boolean;library?:boolean}){
 const creator=workbenchHref(import.meta.env.VITE_RPG_AUTHORING_PORT,window.location);
 return <div className={'rpg-platform'+(compact?' rpg-platform--embedded':'')+(reader?' rpg-platform--reader':'')+(library?' rpg-platform--library':'')}>
  <a className="rpg-skip" href="#rpg-content">跳到内容</a>
  <header className="rpg-platform-nav"><Link to="/rpg" className="rpg-brand" aria-label="RPG 故事馆"><span aria-hidden="true">镜</span><b>RPG</b></Link><nav aria-label="RPG 导航"><NavLink to="/rpg" end>故事馆</NavLink><NavLink to="/rpg/plugins">插件市场</NavLink>{creator&&<a className="rpg-workbench-nav" href={creator}>创作工作台</a>}</nav><Link to="/studio" className="rpg-leave">返回模镜</Link></header>
  <main id="rpg-content" className={compact?'rpg-embedded':'rpg-content'}>{children}</main>
 </div>;
}
