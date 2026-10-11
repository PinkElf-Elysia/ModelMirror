import React from 'react';
import {render,screen,fireEvent,waitFor,cleanup} from '@testing-library/react';
import {MemoryRouter,Routes,Route} from 'react-router-dom';
import {it,expect,vi,beforeEach,afterEach} from 'vitest';
vi.mock('../../../experiments/ai-rpg-engine/card-replica/node_modules/react/index.js',()=>React);
vi.mock('../../../experiments/ai-rpg-engine/card-replica/node_modules/react-dom/client.js',()=>({createRoot:()=>({render:()=>{}})}));
import {GodApp} from '../../../experiments/ai-rpg-engine/card-replica/src/god-main';
import {useMemoryPalace} from '../../../experiments/ai-rpg-engine/card-replica/src/memory-palace';
import {useRollingSummary} from '../../../experiments/ai-rpg-engine/card-replica/src/rolling-summary';
import {ChatView,SavedOutput} from '../../../experiments/ai-rpg-engine/card-replica/src/chat';
import {api,writeClientState,readClientState,emptyClientState} from '../../../experiments/ai-rpg-engine/card-replica/src/session-client';
import RpgPage from './RpgPage';
import RpgPluginsPage from './RpgPluginsPage';
beforeEach(()=>{localStorage.clear();window.history.replaceState({},'', '/rpg-app/god/');HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};});
afterEach(()=>vi.unstubAllGlobals());
const session={id:'god-session',name:'虚构神明',characterText:'【天书】\n姓名：虚构神明\n完整服务器快照',mode:'offline',revision:0,runtime:{compatible:true},requests:{},turns:[]};
function mockHost(extra?: (url:string,opts?:RequestInit)=>unknown){
 const posts:{url:string;body:any}[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,opts?:RequestInit)=>{
  const body=opts?.body?JSON.parse(opts.body as string):undefined;
  if(body){posts.push({url,body});if(url.endsWith('/send'))return Response.json({...session,revision:1,turns:[{requestId:body.requestId,input:body.input,raw:'离线样例',html:'离线样例'}],requests:{[body.requestId]:{status:'complete'}}});}
  const other=extra?.(url,opts);if(other)return other;
  if(url.endsWith('/api/plugins'))return Response.json({revision:0,plugins:[]});
  if(url.endsWith('/status'))return Response.json({providerEnabled:false,offlineBudget:{remaining:100},cards:[]});
  if(url.endsWith('/sessions'))return Response.json(body?session:[]);
  return Response.json(session);
 }));return posts;
}
it('God API and persisted requests never reuse Earth namespace; Earth legacy keys remain readable',async()=>{
 mockHost();writeClientState('same',{...emptyClientState(),draft:'God草稿'});
 await api('status');expect(fetch).toHaveBeenCalledWith('/rpg-app/god/api/status',{});
 window.history.replaceState({},'', '/rpg-app/earth/');
 expect(readClientState('same').draft).toBe('');writeClientState('same',{...emptyClientState(),draft:'Earth草稿'});
 window.history.replaceState({},'', '/rpg-app/god/');
 expect(readClientState('same').draft).toBe('God草稿');expect(localStorage.getItem('earth-chat-v1:same')).toContain('Earth草稿');
});
it('first God request is the exact server snapshot and waits for player send',async()=>{
 const posts=mockHost();render(<ChatView sessionId={session.id} exit={()=>{}} opened={()=>{}}/>);
 const input=await screen.findByRole('textbox',{name:'对话输入'});await waitFor(()=>expect(input).toHaveValue(session.characterText));
 expect(input).toHaveAttribute('readonly');expect(posts).toHaveLength(0);
 fireEvent.click(screen.getByRole('button',{name:'发送'}));
 await waitFor(()=>expect(posts).toHaveLength(1));expect(posts[0].url).toBe('/rpg-app/god/api/sessions/god-session/send');expect(posts[0].body.input).toBe(session.characterText);
 await screen.findByText('第 1 回合 · 已保存 · 离线样例');expect(input).not.toHaveAttribute('readonly');
});
it('setup bridge ignores other origins and windows, creates only once, and never sends automatically',async()=>{
 const posts=mockHost();render(<GodApp/>);const frame=screen.getByTitle('神临凡尘作者配置界面') as HTMLIFrameElement;
 const data={type:'god-setup-create',setup:{fixture:'server validates this'}};
 fireEvent(window,new MessageEvent('message',{origin:'https://example.com',source:frame.contentWindow,data}));
 fireEvent(window,new MessageEvent('message',{origin:location.origin,source:window,data}));expect(posts).toHaveLength(0);
 fireEvent(window,new MessageEvent('message',{origin:location.origin,source:frame.contentWindow,data}));
 fireEvent(window,new MessageEvent('message',{origin:location.origin,source:frame.contentWindow,data}));
 await screen.findByText('开始你的第一次聊天吧');
 expect(posts).toHaveLength(1);expect(posts[0]).toEqual({url:'/rpg-app/god/api/sessions',body:{setup:data.setup,mode:'offline'}});
 expect(readClientState(session.id).draft).toBe(session.characterText);expect(location.search).toContain(session.id);
});
it('God entry appears only when service advertises it; routed iframe is God-specific',async()=>{
 mockHost(url=>url.endsWith('/status')?Response.json({cards:[{id:'god',enabled:false,model:null,remaining:0}]}):null);
 render(<MemoryRouter><RpgPage/></MemoryRouter>);await screen.findByText('神临凡尘 · 上帝模拟器');expect(screen.getByText('离线展示样例 · 不调用真实模型')).toBeInTheDocument();cleanup();
 render(<MemoryRouter initialEntries={['/rpg/god']}><Routes><Route path="/rpg/:cardId" element={<RpgPage/>}/></Routes></MemoryRouter>);
 expect(screen.getByTitle('神临凡尘配置与会话')).toHaveAttribute('src','/rpg-app/god/');
});
it('market displays God compatibility from the reviewed manifest without implying enablement',async()=>{
 mockHost(url=>url.endsWith('/api/plugins')?Response.json({revision:0,plugins:[{id:'rpg.branch-save',name:'分支存档',version:'1.1.0',compatibleCards:['earth','god'],permissions:[],installed:false}]}):null);
 render(<MemoryRouter><RpgPluginsPage/></MemoryRouter>);
 await screen.findByText('地球 OL / 神临凡尘 · 兼容的新版本会话');expect(screen.getByRole('button',{name:'安装插件'})).toBeInTheDocument();expect(screen.getByRole('link',{name:'进入神临凡尘 →'})).toHaveAttribute('href','/rpg/god');
});

it('saved HTML keeps player disclosure state across unrelated chat renders',()=>{
 const raw='<details open><summary>当前情况</summary>样例</details>';
 const view=render(<SavedOutput raw={raw} html={raw}/>);
 const details=view.container.querySelector('details')!;details.open=false;
 view.rerender(<SavedOutput raw={raw} html={raw}/>);
 expect(view.container.querySelector('details')).toBe(details);expect(details.open).toBe(false);
 view.rerender(<SavedOutput raw={raw+'更新'} html={raw+'更新'}/>);
 expect(view.container).toHaveTextContent('更新');
});

it.each([['memoryPalace',useMemoryPalace],['rollingSummary',useRollingSummary]] as const)('waits for current %s policy after session/model revision changes',async(field,usePolicy)=>{
 let revision=1;
 vi.stubGlobal('fetch',vi.fn(async()=>Response.json({compatible:true,enabled:false,busy:false,sessionRevision:revision,config:{model:null},operations:{},versions:[],taskState:{operations:{},blocked:null}})));
 function Harness({current}:{current:number}){const h=usePolicy({id:'policy-test',revision:current,[field]:{}} as any,()=>{});return <><button disabled={h.blocked}>发送门禁</button><button onClick={()=>h.refresh()}>刷新策略</button></>;}
 const v=render(<Harness current={1}/>);
 await waitFor(()=>expect(screen.getByRole('button',{name:'发送门禁'})).toBeEnabled());
 v.rerender(<Harness current={2}/>);
 expect(screen.getByRole('button',{name:'发送门禁'})).toBeDisabled();
 revision=2;fireEvent.click(screen.getByRole('button',{name:'刷新策略'}));
 await waitFor(()=>expect(screen.getByRole('button',{name:'发送门禁'})).toBeEnabled());
 expect(fetch).not.toHaveBeenCalledWith(expect.anything(),expect.objectContaining({method:'POST'}));
});

it('real God host creates a real session without generating and displays real budget',async()=>{
 const posts=mockHost(url=>url.endsWith('/status')?Response.json({providerEnabled:true,mode:'controlled-real',budget:{remaining:4},cards:[]}):null);
 render(<GodApp/>);await screen.findByText('正式调用 · 点击发送才生成');
 const frame=screen.getByTitle('神临凡尘作者配置界面') as HTMLIFrameElement;
 fireEvent(window,new MessageEvent('message',{origin:location.origin,source:frame.contentWindow,data:{type:'god-setup-create',setup:{fixture:true}}}));
 await waitFor(()=>expect(posts).toHaveLength(1));expect(posts[0].body.mode).toBe('real');expect(posts[0].url).not.toContain('/send');
});

// Source-only regressions: run after the user's second manual test has finished.
it.each([
 {label:'all remaining calls reserved',budget:{limit:9,used:7,reserved:2,remaining:0},exhausted:false,disabled:true},
 {label:'story consumed and memory reserved',budget:{limit:9,used:8,reserved:1,remaining:0},exhausted:false,disabled:true},
 {label:'all calls consumed',budget:{limit:9,used:9,reserved:0,remaining:0},exhausted:true,disabled:true},
 {label:'reservation released',budget:{limit:9,used:8,reserved:0,remaining:1},exhausted:false,disabled:false},
 {label:'legacy counters incomplete',budget:{remaining:0},exhausted:false,disabled:true},
 {label:'budget unavailable',budget:null,exhausted:false,disabled:true},
])('budget UI: $label (God and Earth)',async({budget,exhausted,disabled})=>{
 for(const card of ['god','earth']){
  window.history.replaceState({},'',`/rpg-app/${card}/`);
  writeClientState(session.id,{...emptyClientState(),draft:'保留草稿'});
  const posts=mockHost(url=>url.endsWith('/status')?Response.json({budget}):url.endsWith('/sessions/'+session.id)?Response.json({...session,mode:'real'}):null);
  render(<ChatView sessionId={session.id} exit={()=>{}} opened={()=>{}}/>);
  await screen.findByText(new RegExp(`可用 ${budget?.remaining??'待核对'} 次`));
  expect(!!screen.queryByText('本卡额度已用完。仍可查看、创建分支和切换。')).toBe(exhausted);
  if(budget&&'reserved' in budget&&budget.reserved)expect(screen.getByText(/已预留.*尚未计入已用/)).toBeInTheDocument();
  expect((screen.getByRole('button',{name:'发送'}) as HTMLButtonElement).disabled).toBe(disabled);
  expect(posts).toHaveLength(0);expect(readClientState(session.id).draft).toBe('保留草稿');cleanup();
 }
});
it.each([
 {label:'reserved by a task',reserved:1,busy:false,pending:false,warning:false},
 {label:'background task running',reserved:0,busy:true,pending:false,warning:false},
 {label:'unknown task result',reserved:0,busy:false,pending:true,warning:false},
 {label:'idle and insufficient for next group',reserved:0,busy:false,pending:false,warning:true},
])('memory budget warning: $label',async({reserved,busy,pending,warning})=>{
 const memory={compatible:true,enabled:true,busy,sessionRevision:0,memoryRevision:0,config:{model:{kind:'controlled',model:'synthetic/test'}},entries:[],processedThrough:0,totalTurns:0,pendingOperationId:pending?'test-operation':null,blocked:null,operations:{},originalOutputs:[],effectivePolicy:{revision:'test'},maxCalls:2,updateMaxCalls:1,lastRecall:null};
 const posts=mockHost(url=>url.endsWith('/status')?Response.json({budget:{limit:9,used:8,reserved,remaining:1-reserved}}):url.endsWith('/memory-palace')?Response.json(memory):url.endsWith('/sessions/'+session.id)?Response.json({...session,mode:'real',memoryPalace:{}}):null);
 render(<ChatView sessionId={session.id} exit={()=>{}} opened={()=>{}}/>);
 await screen.findByText(/本次发送最多 2 次调用/);
 await screen.findByText(new RegExp(`可用 ${1-reserved} 次`));
 expect(!!screen.queryByText(/下一次发送的可用额度不足/)).toBe(warning);
 expect(screen.queryByText('本卡额度已用完。仍可查看、创建分支和切换。')).not.toBeInTheDocument();
 expect(screen.getByRole('button',{name:'发送'})).toBeDisabled();expect(posts).toHaveLength(0);
});
