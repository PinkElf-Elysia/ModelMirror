// @vitest-environment-options {"url":"http://127.0.0.1:5173/"}
import {act,fireEvent,render,screen} from '@testing-library/react';
import {MemoryRouter,Route,Routes,useLocation} from 'react-router-dom';
import {afterEach,expect,it,vi} from 'vitest';
import ArchiveCardPage from './ArchiveCardPage';
import {SavedOutput} from '../../../experiments/ai-rpg-engine/card-replica/src/chat';
import {SessionSidebar} from '../../../experiments/ai-rpg-engine/card-replica/src/session-sidebar';
import {SessionPlugins} from '../../../experiments/ai-rpg-engine/card-replica/src/session-plugins';
import {TranscriptTools} from '../../../experiments/ai-rpg-engine/card-replica/src/transcript';

const id='00000000-0000-4000-8000-000000000001';
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();window.history.replaceState({},'', '/');});
function Location(){const route=useLocation();return <output aria-label="当前路径" data-route-key={route.key}>{route.pathname}</output>;}

it.each([
 ['earth','/rpg-app/earth/'],['god','/rpg-app/god/'],['rpg05','/rpg-app/xingjian/'],
 ['ancient','/rpg-app/ancient/chat/'],['cultivation','/rpg-app/cultivation/chat/'],
 ['lord-of-mysteries','/rpg-app/lord-of-mysteries/chat/'],
 ['douluo','/rpg-app/douluo/chat/'],
] as const)('keeps the saved session URL and embedded host for %s', (card,root)=>{
 const {container}=render(<MemoryRouter initialEntries={['/rpg/'+card+'/chat/'+id]}><Routes>
  <Route path="/rpg/:card/chat/:sessionId" element={<ArchiveCardPage card={card}/>}/>
 </Routes></MemoryRouter>);
 expect(container.querySelector('iframe')).toHaveAttribute('src',root+'?session='+id);
});

it('only accepts navigation from its own same-origin card frame',()=>{
 const {container}=render(<MemoryRouter initialEntries={['/rpg/god']}><Location/><Routes>
  <Route path="/rpg/god" element={<ArchiveCardPage card="god"/>}/>
  <Route path="/rpg/god/chat/:sessionId" element={<ArchiveCardPage card="god"/>}/>
 </Routes></MemoryRouter>);
 const source=container.querySelector('iframe')!.contentWindow!;
 const notify=(origin:string,from:Window|null,card:string,value=id)=>act(()=>window.dispatchEvent(new MessageEvent('message',{origin,source:from,data:{type:'rpg-chat-route',card,id:value}})));
 notify('https://example.org',source,'god');
 notify(location.origin,window,'god');
 notify(location.origin,source,'earth');
 notify(location.origin,source,'god','../other');
 expect(screen.getByLabelText('当前路径')).toHaveTextContent(/^\/rpg\/god$/);
 notify(location.origin,source,'god');
 expect(screen.getByLabelText('当前路径')).toHaveTextContent('/rpg/god/chat/'+id);
 const key=screen.getByLabelText('当前路径').getAttribute('data-route-key');
 notify(location.origin,source,'god');
 expect(screen.getByLabelText('当前路径')).toHaveAttribute('data-route-key',key);
});

it('keeps author HTML and expanded details across unrelated parent renders',()=>{
 const html='<details><summary>主角信息</summary><p>原有排版</p></details>';
 const {container,rerender}=render(<SavedOutput raw={html} html={html}/>);
 const details=container.querySelector('details')!;
 fireEvent.click(screen.getByText('主角信息'));
 expect(details.open).toBe(true);
 rerender(<SavedOutput raw={html} html={html}/>);
 expect(container.querySelector('details')).toBe(details);
 expect(details.open).toBe(true);
 expect(details.innerHTML).toBe('<summary>主角信息</summary><p>原有排版</p>');
});

it.each(['branch','rewrite','import'])('opens persisted %s conversations through the regular chat route',prefix=>{
 const session=prefix+'-'+'a'.repeat(64);
 const {container}=render(<MemoryRouter initialEntries={['/rpg/ancient/chat/'+session]}><Routes>
  <Route path="/rpg/ancient/chat/:sessionId" element={<ArchiveCardPage card="ancient"/>}/>
 </Routes></MemoryRouter>);
 expect(container.querySelector('iframe')).toHaveAttribute('src','/rpg-app/ancient/chat/?session='+session);
});

it.each(['earth','god','xingjian','ancient','cultivation','lord-of-mysteries','douluo'])('exports from the correct %s chat host',async card=>{
 const root='/rpg-app/'+card+(['ancient','cultivation','lord-of-mysteries','douluo'].includes(card)?'/chat/':'/');
 window.history.replaceState({},'',root);
 const request=vi.fn().mockResolvedValue({ok:false,json:async()=>({error:'TRANSCRIPT_EMPTY'})});
 vi.stubGlobal('fetch',request);
 render(<TranscriptTools id={id} name="测试" disabled={false} opened={vi.fn()}/>);
 await act(async()=>fireEvent.click(screen.getByRole('button',{name:'导出记录'})));
 expect(request).toHaveBeenCalledWith(root+'api/sessions/'+id+'/transcript/export',{});
});

it('uses a modal drawer on mobile and lets Escape close it',()=>{
 vi.spyOn(window,'innerWidth','get').mockReturnValue(390);
 const show=vi.fn(function(this:HTMLDialogElement){this.setAttribute('open','');});
 // jsdom has no native modal implementation; the browser check covers focus trapping.
 Object.defineProperty(HTMLDialogElement.prototype,'showModal',{value:show,configurable:true});
 Object.defineProperty(HTMLDialogElement.prototype,'close',{value:function(this:HTMLDialogElement){this.removeAttribute('open');},configurable:true});
 const close=vi.fn();
 render(<SessionSidebar open close={close}><button>故事一</button></SessionSidebar>);
 const dialog=screen.getByRole('dialog',{name:'会话列表'});
 expect(show).toHaveBeenCalledTimes(1);
 fireEvent(dialog,new Event('cancel',{bubbles:true,cancelable:true}));
 expect(close).toHaveBeenCalledTimes(1);
});

it('does not offer installation or toggling while plugin state is unknown',()=>{
 render(<SessionPlugins market="/rpg/plugins" change={vi.fn()} open={vi.fn()} rows={[{id:'rpg.memory-palace',name:'记忆宫殿',enabled:false,installed:false,known:false,supported:true,locked:false}]}/>);
 expect(screen.getByText('状态待核对')).toBeVisible();
 expect(screen.queryByRole('switch')).not.toBeInTheDocument();
 expect(screen.queryByRole('link',{name:/去安装/})).not.toBeInTheDocument();
});

it('uses the host display projection for pure Markdown tables without replacing saved source',()=>{
 const raw='| 地点 | 状态 |\n| --- | --- |\n| 雾港 | 小雨 |';
 const html='<table><thead><tr><th>地点</th><th>状态</th></tr></thead><tbody><tr><td>雾港</td><td>小雨</td></tr></tbody></table>';
 const {container,rerender}=render(<SavedOutput raw={raw} html={html}/>);
 expect(screen.getByRole('table')).toHaveTextContent('雾港');
 expect(container).not.toHaveTextContent('| ---');
 const table=screen.getByRole('table');
 rerender(<SavedOutput raw={raw} html={html}/>);
 expect(screen.getByRole('table')).toBe(table);
 expect(raw).toContain('| ---');
});

it('does not show filtered source when the host intentionally returns an empty display',()=>{
 const {container}=render(<SavedOutput raw="<think>private block</think>" html=""/>);
 expect(container).not.toHaveTextContent('private block');
});
