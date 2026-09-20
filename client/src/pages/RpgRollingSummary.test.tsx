import {ChatView} from '../../../experiments/ai-rpg-engine/card-replica/src/chat';
import React from 'react';
import {render,screen,fireEvent,waitFor} from '@testing-library/react';
import {it,expect,vi,afterEach,beforeEach} from 'vitest';
vi.mock('../../../experiments/ai-rpg-engine/card-replica/node_modules/react/index.js',async()=>await import('react'));
vi.mock('../../../experiments/ai-rpg-engine/card-replica/src/main',()=>({Modal:({title,children,close}:any)=><div role="dialog" aria-label={title}><button onClick={close}>关闭</button>{children}</div>}));
import {RollingSummaryDialog,useRollingSummary} from '../../../experiments/ai-rpg-engine/card-replica/src/rolling-summary';
const model={kind:'controlled' as const,model:'test-model',selectionId:'test',selectionRevision:'v1'};
const initial={compatible:true,enabled:true,busy:false,sessionRevision:3,summaryRevision:2,pendingOperationId:null,config:{timing:'before' as const,model},activeVersionId:'a',versions:[{id:'a',kind:'model',coveredThrough:1,effectiveText:'原摘要',raw:'原摘要'}],effectivePolicy:{revision:'a'.repeat(64),mode:'rolling-summary',needsUpdate:false},taskState:{blocked:null,operations:{}}};
beforeEach(()=>{localStorage.clear();window.history.replaceState({},'', '/rpg-app/earth/');});afterEach(()=>vi.unstubAllGlobals());
it('first explicit model required and cancel never saves',()=>{const save=vi.fn(),close=vi.fn();render(<RollingSummaryDialog status={{...initial,config:{...initial.config,model:null}}} sessionId="s" busy={false} pending={false} error="" save={save} update={()=>{}} recover={()=>{}} close={close}/>);expect(screen.getByRole('button',{name:'保存'})).toBeDisabled();fireEvent.click(screen.getByRole('button',{name:'取消'}));expect(close).toHaveBeenCalledOnce();expect(save).not.toHaveBeenCalled();});
it('manual text and timing save together with Unicode bound and retained raw',async()=>{const save=vi.fn(async()=>true);render(<RollingSummaryDialog status={initial} sessionId="s" busy={false} pending={false} error="" save={save} update={()=>{}} recover={()=>{}} close={()=>{}}/>);fireEvent.change(screen.getByRole('textbox'),{target:{value:'😀'.repeat(10001)}});expect(screen.getByRole('button',{name:'保存'})).toBeDisabled();fireEvent.change(screen.getByRole('textbox'),{target:{value:'人工修订'}});fireEvent.click(screen.getByRole('radio',{name:'回复后后台更新'}));expect(screen.getByText(/离开页面后/)).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'保存'}));await waitFor(()=>expect(save).toHaveBeenCalledWith({expectedSessionRevision:3,expectedSummaryRevision:2,timing:'after',selectionId:'test',catalogRevision:'v1',text:'人工修订'}));expect(screen.getByText('原摘要',{selector:'pre'})).toBeInTheDocument();});
it('unknown operation disables edits and only offers explicit recovery',()=>{const recover=vi.fn();render(<RollingSummaryDialog status={initial} sessionId="s" busy={false} pending={true} error="" save={vi.fn()} update={()=>{}} recover={recover} close={()=>{}}/>);expect(screen.getByRole('textbox')).toBeDisabled();expect(screen.getByRole('button',{name:'保存'})).toBeDisabled();fireEvent.click(screen.getByRole('button',{name:'恢复操作结果'}));expect(recover).toHaveBeenCalledOnce();});
it('unknown settings survive remount; recovery uses original ID without resending settings',async()=>{const posts:any[]=[],session:any={id:'s',revision:3,rollingSummary:{supported:true}};vi.stubGlobal('fetch',vi.fn(async(url:string,opts?:RequestInit)=>{const body=opts?.body?JSON.parse(opts.body as string):null;if(body){posts.push({url,body});if(url.endsWith('/settings'))throw Error('unknown');return {ok:true,json:async()=>({session,operation:{status:'complete'}})};}return {ok:true,json:async()=>url.endsWith('/rolling-summary')?initial:session};}));function Harness(){const h=useRollingSummary(session,()=>{});return <><button onClick={()=>h.perform('settings',{timing:'before',selectionId:'test',catalogRevision:'v1',text:null})}>保存</button><button onClick={()=>h.recover()}>恢复</button><p>{h.error}</p><span>{h.pending?'待确认':'已核对'}</span></>;}const v=render(<Harness/>);fireEvent.click(screen.getByRole('button',{name:'保存'}));await screen.findByText(/操作未完成/);const id=posts[0].body.operationId;v.unmount();render(<Harness/>);await screen.findByText('待确认');fireEvent.click(screen.getByRole('button',{name:'恢复'}));await screen.findByText('已核对');expect(posts).toHaveLength(2);expect(posts[1].url).toMatch(/recover$/);expect(posts[1].body.operationId).toBe(id);});

it('editing keeps its original revision and refuses an intervening summary update',()=>{const props={sessionId:'s',busy:false,pending:false,error:'',save:vi.fn(),update:()=>{},recover:()=>{},close:()=>{}};const v=render(<RollingSummaryDialog {...props} status={initial}/>);fireEvent.change(screen.getByRole('textbox'),{target:{value:'未保存修订'}});v.rerender(<RollingSummaryDialog {...props} status={{...initial,summaryRevision:3,sessionRevision:4}}/>);expect(screen.getByRole('textbox')).toHaveValue('未保存修订');expect(screen.getByRole('button',{name:'保存'})).toBeDisabled();expect(screen.getByText(/摘要已在其他操作中更新/)).toBeInTheDocument();});


it.each([['story','正在生成剧情'],['summary','正在更新摘要'],['compression','正在压缩摘要'],[null,'会话操作进行中']] as const)('busy %s shows its actual purpose without claiming another summary call', (purpose,label)=>{
 const status={...initial,busy:true,taskState:{blocked:null,operations:{op:{status:'pending',kind:'send',purpose}}}};
 render(<RollingSummaryDialog status={status} sessionId="s" busy={false} pending={true} error="" save={vi.fn()} update={()=>{}} recover={()=>{}} close={()=>{}}/>);
 expect(screen.getByText(new RegExp(label))).toBeInTheDocument();expect(screen.getByRole('button',{name:'保存'})).toBeDisabled();
 if(purpose!=='summary')expect(screen.queryByText(/正在更新摘要/)).not.toBeInTheDocument();
});
it('successful status refresh clears the temporary outage without a write or generation',async()=>{
 let unavailable=true;const calls:any[]=[];const session:any={id:'s',revision:3,rollingSummary:{supported:true}};
 vi.stubGlobal('fetch',vi.fn(async(url:string,opts?:RequestInit)=>{calls.push(opts);if(unavailable)throw Error('offline');return {ok:true,json:async()=>initial};}));
 function Harness(){const h=useRollingSummary(session,()=>{});return <><button onClick={()=>h.refresh()}>刷新</button><p>{h.error}</p><span>{h.status?'可读取':'不可读取'}</span></>;}
 render(<Harness/>);await screen.findByText(/状态暂不可用/);unavailable=false;fireEvent.click(screen.getByRole('button',{name:'刷新'}));await screen.findByText('可读取');expect(screen.queryByText(/状态暂不可用/)).not.toBeInTheDocument();expect(calls.every(o=>!o?.body)).toBe(true);
});
it('successful polling does not erase an unknown settings write or replay it',async()=>{
 const session:any={id:'s',revision:3,rollingSummary:{supported:true}},posts:any[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,opts?:RequestInit)=>{if(opts?.body){posts.push(opts.body);throw Error('unknown-write');}return {ok:true,json:async()=>initial};}));
 function Harness(){const h=useRollingSummary(session,()=>{});return <><button onClick={()=>h.perform('settings',{timing:'before',selectionId:'test',catalogRevision:'v1',text:null})}>写入</button><button onClick={()=>h.refresh()}>刷新</button><p>{h.error}</p><span>{h.pending?'未确认':'无待办'}</span></>;}
 render(<Harness/>);fireEvent.click(screen.getByRole('button',{name:'写入'}));await screen.findByText(/操作未完成/);fireEvent.click(screen.getByRole('button',{name:'刷新'}));await screen.findByText('未确认');expect(screen.getByText(/操作未完成/)).toBeInTheDocument();expect(posts).toHaveLength(1);
});
it('a delayed older status failure cannot replace the latest successful read',async()=>{
 const session:any={id:'s',revision:3,rollingSummary:{supported:true}};let rejectOld:(e:Error)=>void=()=>{},count=0;
 vi.stubGlobal('fetch',vi.fn(async()=>{if(++count===1)return new Promise((_resolve,reject)=>{rejectOld=reject;});return {ok:true,json:async()=>initial};}));
 function Harness(){const h=useRollingSummary(session,()=>{});return <><button onClick={()=>h.refresh()}>刷新</button><p>{h.error}</p><span>{h.status?'可读取':'不可读取'}</span></>;}
 render(<Harness/>);fireEvent.click(screen.getByRole('button',{name:'刷新'}));await screen.findByText('可读取');rejectOld(Error('old-outage'));await waitFor(()=>expect(screen.queryByText(/状态暂不可用/)).not.toBeInTheDocument());expect(screen.getByText('可读取')).toBeInTheDocument();
});

it('compression failure shows retained raw and a single explicit retry with server-proven cost',()=>{
 const update=vi.fn(),save=vi.fn();const status={...initial,updatePlan:{kind:'compression' as const,maxCalls:1 as const},effectivePolicy:{...initial.effectivePolicy,needsUpdate:true},taskState:{blocked:'op',operations:{op:{status:'failed',kind:'update',purpose:'compression' as const,overflow:true}}},compressionAttempts:[{operationId:'op',status:'failed',raw:'超长原文<script>不执行</script>',compressedRaw:'失败返回'}]};
 render(<RollingSummaryDialog status={status} sessionId="s" busy={false} pending={false} error="" save={save} update={update} recover={()=>{}} close={()=>{}}/>);
 expect(screen.getByText(/压缩未完成/)).toBeInTheDocument();expect(screen.getByText('超长原文<script>不执行</script>',{selector:'pre'})).toBeInTheDocument();expect(document.querySelector('script')).toBeNull();expect(update).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'再次压缩 · 1 次调用'}));expect(update).toHaveBeenCalledOnce();expect(save).not.toHaveBeenCalled();
});
it('ordinary update reports two-call maximum and unsaved edit prevents dispatch',()=>{
 const update=vi.fn();render(<RollingSummaryDialog status={{...initial,updatePlan:{kind:'summary',maxCalls:2},effectivePolicy:{...initial.effectivePolicy,needsUpdate:true}}} sessionId="s" busy={false} pending={false} error="" save={vi.fn()} update={update} recover={()=>{}} close={()=>{}}/>);
 expect(screen.getByRole('button',{name:'更新摘要 · 最多 2 次调用'})).toBeEnabled();fireEvent.change(screen.getByRole('textbox'),{target:{value:'未保存'}});expect(screen.getByRole('button',{name:'更新摘要 · 最多 2 次调用'})).toBeDisabled();expect(update).not.toHaveBeenCalled();
});
it('compressed effective version shows both originals; cancel during work only closes',()=>{
 const close=vi.fn(),update=vi.fn();render(<RollingSummaryDialog status={{...initial,versions:[{...initial.versions[0],compression:{raw:'压缩之前',rawHash:'a'.repeat(64)}}]}} sessionId="s" busy={true} pending={false} error="" save={vi.fn()} update={update} recover={()=>{}} close={close}/>);
 expect(screen.getByText('压缩之前',{selector:'pre'})).toBeInTheDocument();expect(screen.getByText('原摘要',{selector:'pre'})).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'取消'}));expect(close).toHaveBeenCalledOnce();expect(update).not.toHaveBeenCalled();
});

it('summary polling refreshes shared remaining quota without a new send or manual restore',async()=>{
 let remaining=3;const writes:any[]=[],session:any={id:'s',name:'额度验收',mode:'real',revision:3,rollingSummary:{supported:true},turns:[],requests:{},runtime:{compatible:true}};
 vi.stubGlobal('fetch',vi.fn(async(url:string,opts?:RequestInit)=>{if(opts?.body)writes.push(opts.body);return {ok:true,json:async()=>url.endsWith('/api/status')?{budget:{remaining}}:url.endsWith('/api/plugins')?{revision:0,plugins:[]}:url.endsWith('/rolling-summary')?structuredClone(initial):session};}));
 render(<ChatView sessionId="s" exit={()=>{}} opened={()=>{}}/>);await screen.findByText(/剩余 3 次/);remaining=0;
 await waitFor(()=>expect(screen.getByText(/本卡额度已用完/)).toBeInTheDocument(),{timeout:3500});expect(writes).toHaveLength(0);
});
