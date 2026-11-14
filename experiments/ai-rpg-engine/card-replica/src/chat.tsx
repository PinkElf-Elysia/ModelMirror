import {pluginFailure,needsPluginUpgrade} from './plugin-version';
import type {PluginVersion} from './plugin-version';
import {ShopChat,ShopEntry} from './shop';
import {OutputEditor} from './output-editor';
import {SessionHistoryDialog,SessionChangeDialog} from './session-history';
import type {HistoryAction} from './session-history';
import type {LifecycleRecord} from './session-client';
import {StreamOutput} from './stream-output';
import {PersistentRequirements,requirementsPending} from './persistent-requirements';
import {unconfirmedOutsideComparison} from '../../authoring/comparison-client.mjs';
import {ModelComparison} from './model-comparison';
import {GenerationParameters} from './generation-parameters';
import {ModPanel} from './mod-panel';
import {SessionPlugins} from './session-plugins';
import {SessionSidebar} from './session-sidebar';
import {worlds,worldId} from './presentation';
import './chat-workspace.css';
import {customScope} from './session-client';
import {ConfigTools,ConfigText,useConfigView} from './custom-config';
import {useIllustrations} from './illustrations';
import {TranscriptTools} from './transcript';
import {useMessageDisplay,wireRequest,sendFork,copyReply} from './message-tools';
import {useMemoryPalace,MemoryPalaceDialog,memoryActivity,memoryFailureNotice} from './memory-palace';
import {useRollingSummary,RollingSummaryDialog,SummaryV2Dialog,summaryActivity} from './rolling-summary';
import {memo,useEffect,useRef,useState} from 'react';
import {HistoryWindowDialog} from './history-window';
import type {HistoryStatus,HistoryConfig,HistoryOperation} from './session-client';
import {ModelPicker} from './model-picker';
import type {ModelItem} from './model-picker';
import ReactMarkdown from 'react-markdown';
import './card-presentation.css';
import {Modal} from './modal';
import {archiveRoot,localCatalog,cardStoragePrefix,inStudioCard,api,ApiError,catalogApi,emptyClientState,readClientState,writeClientState,reconcileClientState,sendBlocked} from './session-client';
import type {Session,ClientState,BranchRecord,SelectionRecord} from './session-client';
export const SavedOutput=memo(function SavedOutput({raw,html}:{raw:string;html:string}){return typeof html==='string'?<div className={/<[a-z][a-z0-9-]*(?:\s|>)/i.test(raw)?"model-output":"model-output platform-prose"} dangerouslySetInnerHTML={{__html:html}}/>:<div className="model-output platform-prose"><ReactMarkdown components={{img:()=> <span>图片未加载</span>,a:({children})=><span>{children}</span>}} urlTransform={()=>''}>{raw}</ReactMarkdown></div>;});

type BudgetSnapshot={remaining?:number;reserved?:number;used?:number;limit?:number};
const budgetCount=(value:unknown):number|undefined=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=0?value:undefined;

type Listed={id:string;name:string;turns:number;mode:string;parentId?:string;branchTurn?:number};
type Plugin=PluginVersion;
type Catalog={revision:number;plugins:Plugin[]};
type Grant={reason?:string|null;enabled:boolean;installed:boolean;sessionRevision:number;registryRevision:number};
export function ChatView(props:{sessionId:string;exit:()=>void;opened:(id:string)=>void}){
 return props.sessionId.startsWith('shop-')?<ShopChat key={props.sessionId} {...props}/>:<>{cardStoragePrefix()==='earth'&&<ShopEntry sessionId={props.sessionId} opened={props.opened}/>}<LegacyChatView {...props}/></>;
}
function LegacyChatView({sessionId,exit,opened}:{sessionId:string;exit:()=>void;opened:(id:string)=>void}){
 const [sidebar,setSidebar]=useState(()=>window.innerWidth>800),[listError,setListError]=useState('');
 const [reading,setReading]=useState(()=>{try{const v=JSON.parse(localStorage.getItem('rpg-reading')||'{}');return {dark:v.dark===true,font:[15,16,17,18,19,20].includes(v.font)?v.font:17,wide:v.wide===true};}catch{return {dark:false,font:17,wide:false};}});
 useEffect(()=>{try{localStorage.setItem('rpg-reading',JSON.stringify(reading));}catch{/* Reading preferences must never block the session. */}},[reading]);
 const world=worlds.find(w=>w.id===worldId(cardStoragePrefix()));
 useEffect(()=>{if(inStudioCard()&&window.parent!==window)window.parent.postMessage({type:'rpg-chat-route',card:cardStoragePrefix(),id:sessionId},location.origin);},[sessionId]);
 const configScope=customScope(sessionId);const configView=useConfigView<HTMLElement>(configScope);
 const [initial]=useState(()=>{try{return {state:readClientState(sessionId),error:''};}catch{return {state:emptyClientState(),error:'无法读取本地草稿或请求记录。请保留浏览器数据，暂不发送或切换。'};}});
 const [local,setLocal]=useState(initial.state),[storageError,setStorageError]=useState(initial.error),[s,setS]=useState<Session|null>(null),[working,setBusy]=useState(false),[generating,setGenerating]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[raw,setRaw]=useState<string|null>(null),[copied,setCopied]=useState(false),[panel,setPanel]=useState(''),[list,setList]=useState<Listed[]>([]),[catalog,setCatalog]=useState<Catalog|null>(null),[grant,setGrant]=useState<Grant|null>(null),[pluginError,setPluginError]=useState(''),[branchTurn,setBranchTurn]=useState<number|null>(null),[branchName,setBranchName]=useState(''),[budget,setBudget]=useState<BudgetSnapshot|null>(null);
 const [grants,setGrants]=useState<Record<string,Grant>>({}),[modelCanSelect,setModelCanSelect]=useState(false),[modelList,setModelList]=useState<ModelItem[]>([]),[modelLoading,setModelLoading]=useState(false),[modelError,setModelError]=useState('');
 const [requirementsBlocked,setRequirementsBlocked]=useState(()=>requirementsPending(sessionId));
 const [comparisonBlocked,setComparisonBlocked]=useState(false),[comparisonOperations,setComparisonOperations]=useState<string[]>([]);
 const [history,setHistory]=useState<HistoryStatus|null>(null),[historyError,setHistoryError]=useState('');
 const remaining=budgetCount(budget?.remaining),reserved=budgetCount(budget?.reserved),used=budgetCount(budget?.used),limit=budgetCount(budget?.limit);
 // Available quota excludes reservations; only consumed quota proves exhaustion.
 const budgetExhausted=used!==undefined&&limit!==undefined&&used>=limit;
 const budgetLoad=useRef(0);
 const historyLoad=useRef(0);
 const modelLoad=useRef(0);
 const lock=useRef(false),live=useRef(true),client=useRef(local),composer=useRef<HTMLTextAreaElement>(null);
 const studio=inStudioCard(),god=cardStoragePrefix()!=='earth';
 function persist(next:ClientState){if(initial.error)return false;client.current=next;setLocal(next);try{writeClientState(sessionId,next);setStorageError('');return true;}catch{setStorageError('浏览器未能保存草稿或请求记录。请复制输入内容；恢复存储前暂不发送或切换。');return false;}}
 function accept(v:Session){setS(v);const next=reconcileClientState(client.current,v);if(next!==client.current)persist(next);}
 const summary=useRollingSummary(s,accept),memory=useMemoryPalace(s,accept),busy=working||summary.working||memory.working;
 async function loadPlugin(){try{const c=await catalogApi();const entries=await Promise.allSettled(c.plugins.map(async(p:Plugin)=>{try{return [p.id,await api('sessions/'+sessionId+'/plugins/'+p.id)];}catch(e){if(['rpg.history-window','rpg.rolling-summary','rpg.memory-palace'].includes(p.id)&&e instanceof ApiError&&['HISTORY_RUNTIME_INCOMPATIBLE','SUMMARY_RUNTIME_INCOMPATIBLE','MEMORY_RUNTIME_INCOMPATIBLE'].includes(e.message))return [p.id,{enabled:false,installed:p.installed,sessionRevision:0,registryRevision:c.revision}];throw e;}}));if(live.current){const gs=Object.fromEntries(entries.flatMap(x=>x.status==='fulfilled'?[x.value]:[]));setCatalog(c);setGrants(gs);setGrant(gs['rpg.branch-save']||null);setPluginError(entries.some(x=>x.status==='rejected')?'部分插件状态暂不可用；其他插件和聊天仍可使用。':'');}}catch{if(live.current){setGrant(null);setGrants({});setPluginError('插件状态暂不可用，可稍后重新打开。核心聊天不受影响。');}}}
 async function loadModels(){const version=++modelLoad.current;setModelLoading(true);setModelError('');try{const state=await api('sessions/'+sessionId+'/model-selection');if(!live.current||version!==modelLoad.current)return;setModelCanSelect(state.canSelect);if(!state.canSelect){setModelList([]);return;}const result=await api('sessions/'+sessionId+'/model-catalog');if(live.current&&version===modelLoad.current)setModelList(result.models);}catch{if(live.current&&version===modelLoad.current){setModelList([]);setModelError('模型列表暂不可用');}}finally{if(live.current&&version===modelLoad.current)setModelLoading(false);}}
 useEffect(()=>{if(studio&&s?.modelSelection)void loadModels();else setModelCanSelect(false);return()=>{modelLoad.current++;};},[s?.revision,s?.modelSelection?.revision,grants['rpg.model-selector']?.enabled]);
 async function selectModel(m?:ModelItem){if(lock.current||!s?.modelSelection||storageError)return;const request:SelectionRecord=client.current.selection||{operationId:crypto.randomUUID(),expectedSessionRevision:s.revision,selectionRevision:s.modelSelection.revision,selectionId:m!.selectionId,catalogRevision:m!.selectionRevision};if(!persist({...client.current,selection:request}))return;lock.current=true;setBusy(true);setError('');try{const result=await api('sessions/'+sessionId+'/model-selection',request);if(!live.current)return;accept(result.session);if(!persist({...client.current,selection:null}))return;setPanel('');setNotice('下一条消息：'+result.session.modelSelection.current.model);}catch(e){if(live.current){if(e instanceof ApiError&&e.status<500&&e.message!=='REQUEST_FAILED')persist({...client.current,selection:null});setError('模型选择未完成，请恢复记录核对。当前输入已保留。');setPanel('');}}finally{if(live.current){lock.current=false;setBusy(false);await loadModels();}}}
 async function loadHistory(){const version=++historyLoad.current;try{const h=await api('sessions/'+sessionId+'/history-window');if(live.current&&version===historyLoad.current){setHistory(h);setHistoryError(previous=>previous.startsWith('历史窗口状态')?'':previous);}}catch{if(live.current&&version===historyLoad.current){setHistory(null);setHistoryError('历史窗口状态暂不可用，请恢复记录后再试。');}}}
 useEffect(()=>{if(s?.historyWindow)void loadHistory();return()=>{historyLoad.current++;};},[s?.revision,grants['rpg.history-window']?.enabled]);
 async function saveHistory(config?:HistoryConfig){if(lock.current||!s||storageError)return;lock.current=true;setBusy(true);setHistoryError('');try{
  let request:HistoryOperation|undefined=client.current.historySetting||undefined;
  if(!config&&!request){const id=history?.pendingOperationId||s.historyWindow?.pendingOperationId;if(!id)throw Error('请恢复记录后核对设置。');const r=await api('sessions/'+sessionId+'/history-window?operationId='+encodeURIComponent(id));request=r.operation?.input;if(!request)throw Error('未找到原设置，请保留记录并联系维护者。');}
  if(!request){if(!config||!history?.enabled||history.sessionRevision!==s.revision)throw Error('设置已变化，请恢复记录后重新打开。');request={...config,operationId:crypto.randomUUID(),expectedSessionRevision:s.revision,expectedConfigRevision:history.configRevision};}
  if(!persist({...client.current,historySetting:request}))return;
  const result=await api('sessions/'+sessionId+'/history-window',request);if(!live.current)return;accept(result.session);if(!persist({...client.current,historySetting:null}))return;
  setPanel('');setNotice(result.operation.status==='not-applied'?'已核对：上次设置未保存，原设置保留。':'历史窗口已保存，只影响后续发送。');
 }catch(e){if(live.current){if(e instanceof ApiError&&e.status<500&&e.message!=='REQUEST_FAILED')persist({...client.current,historySetting:null});setHistoryError('设置未完成，请恢复设置结果或重新读取状态核对；不会自动重试。');}}finally{if(live.current){lock.current=false;setBusy(false);await loadHistory();}}}
 async function loadBudget(){const version=++budgetLoad.current;try{const v=await api('status');if(live.current&&version===budgetLoad.current)setBudget((god?(v.budget||v.offlineBudget):v.budget)??null);}catch{if(live.current&&version===budgetLoad.current)setBudget(null);}}
 useEffect(()=>{if(studio&&s?.rollingSummary)void loadBudget();},[summary.status,memory.status]);
 useEffect(()=>{live.current=true;api('sessions/'+sessionId).then(v=>live.current&&accept(v)).catch(e=>{if(live.current){if(e instanceof ApiError&&e.status===410)setTrashed(true);setError(e.status===410?'此会话在回收站中，可从左侧恢复。':e.message);}});void loadBudget();if(studio)void loadPlugin();return()=>{live.current=false;};},[sessionId]);
 async function restore(){if(client.current.lifecycle){await changeSession();return;}try{if(s?.memoryPalace&&client.current.request){const request=client.current.request;const outcome=await memory.recover(request.requestId);if(outcome==='not-found'&&client.current.request?.requestId===request.requestId){await changeSession({action:'dismiss-request',id:sessionId,requestId:request.requestId,input:client.current.draft||request.input});return;}}else if(s?.rollingSummary&&client.current.request)await summary.recover(client.current.request.requestId);if(!god&&!s?.rollingSummary){
   const pendingId=client.current.request?.requestId??Object.entries(s?.requests??{}).find(([,r])=>r.status==='pending')?.[0];
   if(pendingId)await api('sessions/'+sessionId+'/recover-story',{requestId:pendingId});
  }accept(await api('sessions/'+sessionId));setError('');setNotice('已恢复保存的记录；不会自动发送。');await loadBudget();if(studio){await loadPlugin();if(s?.historyWindow)await loadHistory();if(s?.modelSelection)await loadModels();}}catch(e){setError((e as Error).message);}}
 function navigate(action:()=>void){if(busy||client.current.lifecycle||storageError||!persist(client.current))return;action();}
 async function refreshList(){setListError('');try{const items=await api('sessions');if(!Array.isArray(items))throw Error('Invalid session list');if(live.current)setList(items.filter(item=>item&&typeof item.id==='string'&&typeof item.name==='string'));}catch{if(live.current)setListError('暂时没有读到存档，请重新读取。');}}
 useEffect(()=>{void refreshList();},[sessionId,s?.revision]);
 function showList(){setSidebar(v=>!v);void refreshList();}
 function returnToCard(){navigate(()=>{if(studio&&window.parent!==window)window.parent.postMessage({type:'rpg-chat-route',card:cardStoragePrefix(),id:''},location.origin);exit();});}
 async function send(){if(trashed||requirementsBlocked||comparisonBlocked||lock.current||storageError||memory.blocked||memoryBudgetBlocked||summary.blocked||historyUnavailable||modelUnavailable||sendBlocked(s,client.current,busy,remaining)||!s)return;const req={input:client.current.draft,requestId:crypto.randomUUID(),revision:s.revision,...(s.generationParameters?{expectedGeneration:s.generationParameters.expected}:{}),...(s.memoryPalace?{expectedMemoryRevision:memory.status!.memoryRevision}:{}),...(s.modelSelection?{expectedSelectionRevision:s.modelSelection.revision}:{}),...(s.rollingSummary?{expectedContextPolicyRevision:(s.memoryPalace?memory.status!:summary.status!).effectivePolicy.revision}:{}),...(s.historyWindow?{expectedHistoryPolicyRevision:history!.historyPolicyRevision}:{})};if(!persist({...client.current,failed:null,request:req}))return;lock.current=true;setBusy(true);setGenerating(true);setError('');setNotice('');try{const next=await api('sessions/'+sessionId+'/send',wireRequest(s,req));if(!live.current)return;accept(next);const record=next.requests[req.requestId];if(record?.status!=='complete')setError(record?.status==='cancelled'?'已取消，迟到结果不会进入历史。':record?.status==='failed'?memoryFailureNotice(record.error):record?.error||'结果尚未确认，请恢复记录核对。');}catch(e){if(live.current){persist({...client.current,...(e instanceof ApiError&&e.status<500&&e.message!=='REQUEST_FAILED'?{request:null}:{}),failed:{requestId:req.requestId,input:req.input,partial:partialText.current,error:(e as Error).message}});try{accept(await api('sessions/'+sessionId));}catch{/* Retain the original request until explicitly resolved. */}setError(memoryFailureNotice((e as Error).message));}}finally{if(live.current){lock.current=false;setBusy(false);setGenerating(false);await loadBudget();await summary.refresh();await memory.refresh();composer.current?.focus();}}}
 async function changePlugin(p:Plugin){const g=grants[p.id];if(lock.current||!s||!catalog||!g)return false;lock.current=true;setBusy(true);setPluginError('');const action=g.enabled?'disable':'enable';try{await api('sessions/'+sessionId+'/plugins/'+p.id+'/'+action,{operationId:crypto.randomUUID(),pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:catalog.revision,sessionId,expectedSessionRevision:s.revision,...(action==='enable'?{permissions:p.permissions}:{})});accept(await api('sessions/'+sessionId));await loadPlugin();if(s.historyWindow)await loadHistory();await summary.refresh();await memory.refresh();if(p.id==='rpg.memory-palace'&&action==='enable')setPanel('memory');if(p.id==='rpg.rolling-summary'&&action==='enable')setPanel('summary');return true;}catch(e){await loadPlugin();try{accept(await api('sessions/'+sessionId));}catch{/* Keep the last confirmed session. */}setPluginError(pluginFailure(e));return false;}finally{lock.current=false;setBusy(false);}}
 async function createBranch(recover=false){if(lock.current||!s||storageError)return;const request:BranchRecord=recover&&client.current.branch?client.current.branch:{operationId:crypto.randomUUID(),expectedSessionRevision:s.revision,turn:branchTurn!,name:branchName.trim()};if(!request.name||!request.turn)return;if(!persist({...client.current,branch:request}))return;lock.current=true;setBusy(true);setError('');try{const result=await api('sessions/'+sessionId+'/branches',request);if(!live.current)return;if(!persist({...client.current,branch:null}))return;opened(result.session.id);}catch(e){setError((e as Error).message+'；可用同一操作恢复创建结果，不会重复建立分支。');}finally{lock.current=false;setBusy(false);}}
 const pictures=useIllustrations(sessionId,!!grants['rpg.illustrations']?.enabled);
 const [outputEdit,setOutputEdit]=useState<{turn:number;text:string;revision:number}|null>(null);
 const toolsEnabled=!!grants['rpg.message-tools']?.enabled;
 const [rewriteWatch,setRewriteWatch]=useState<{key:string}|null>(null);
 const display=useMessageDisplay(sessionId,toolsEnabled,rewriteWatch?.key??local.request?.requestId,undefined);
 const partialText=useRef(display.text);partialText.current=display.text;
 const [edit,setEdit]=useState<{turn:number;text:string;original:string}|null>(null);
 const [sessionChange,setSessionChange]=useState<HistoryAction|null>(null),[trashed,setTrashed]=useState(false);
 function chooseSessionChange(target:HistoryAction){setPanel('');setSessionChange(target);}
 function rewrite(turn:number,input:string){if(!rewriteBlocked&&toolsEnabled&&input.trim()){setEdit(null);chooseSessionChange({action:'rewind',id:sessionId,turn,input});}}
 async function changeSession(target?:HistoryAction){
  if(lock.current||storageError)return;
  lock.current=true;setBusy(true);setError('');setNotice('');
  let record:LifecycleRecord|undefined=client.current.lifecycle||undefined;
  let dispatched=false;
  try{
   if(!record){
    if(!target)return;
    const state=await api('sessions/'+target.id+'/lifecycle');
    if(target.id===sessionId&&s&&!trashed&&JSON.stringify(state.turnIds)!==JSON.stringify(s.turns.map(t=>t.requestId)))throw Error('会话剧情已变化，请核对记录后重新选择');
    record={id:target.id,body:{action:target.action,expectedRevision:state.revision,operationId:crypto.randomUUID(),...(target.turn?{turn:target.turn}:{}),...(target.versionId?{versionId:target.versionId}:{}),...(target.requestId?{requestId:target.requestId}:{})}};
    if(!persist({...client.current,lifecycle:record,...(target.action==='rewind'?{draft:target.input!}: {})}))return;
   }
   dispatched=true;
   await api('sessions/'+record.id+'/lifecycle',record.body);
   if(!persist({...client.current,lifecycle:null}))return;
   setSessionChange(null);
   if(record.id===sessionId){
    if(record.body.action==='delete'){setTrashed(true);setS(null);setNotice('此会话已移入回收站，可从左侧回收站恢复。');}
    else{setTrashed(false);accept(await api('sessions/'+sessionId));setError('');await loadPlugin();}
   }
   await refreshList();
   if(record.body.action==='dismiss-request'){
    memory.clearNotice();
    const request=client.current.request;
    persist({...client.current,failed:null,...(!request||request.requestId===record.body.requestId?{request:null}:{}),draft:target?.discard?'':target?.input??client.current.draft});
   }
   if(target?.action==='rewind'||target?.action==='dismiss-request'&&target.retry){
    const requestId=crypto.randomUUID();setRewriteWatch({key:requestId});setGenerating(true);
    const result=await sendFork(sessionId,target.input!,requestId);
    if(live.current){client.current=readClientState(sessionId);setLocal(client.current);accept(result);setNotice('已在当前会话重新生成，原内容可从历史版本恢复。');}
   }else setNotice(record.body.action==='dismiss-request'?'本次请求已关闭，可以修改输入或继续发送。':record.body.action==='delete-turn'?'已删除本轮，原内容可从历史版本恢复。':record.body.action==='delete'?'会话已移入回收站。':record.body.action==='rewind'?'已核对回退结果，输入已保留。请确认后手动发送。':'已恢复保存的内容。');
  }catch(e){
   if(live.current){
    if(dispatched&&e instanceof ApiError&&e.status<500&&e.message!=='REQUEST_FAILED')persist({...client.current,lifecycle:null});
    try{client.current=readClientState(sessionId);setLocal(client.current);}catch{setStorageError('无法读取操作记录，请保留浏览器数据。');}
    setSessionChange(null);setError('操作未完成：'+(e as Error).message+'。请核对记录；不会自动重新生成。');
   }
  }finally{
   lock.current=false;
   if(live.current){setBusy(false);setGenerating(false);setRewriteWatch(null);await loadBudget();if(!trashed){await summary.refresh();await memory.refresh();}}
  }
 }
 async function saveOutput(text:string,operationId:string){
   if(lock.current||!outputEdit)throw Error('MESSAGE_EDIT_BUSY');
   lock.current=true;setBusy(true);
   try{
     await api('sessions/'+sessionId+'/message-tools/edit-output',{operationId,revision:outputEdit.revision,turn:outputEdit.turn,text});
     accept(await api('sessions/'+sessionId));
     setNotice('回复已保存，未重新生成。已有总结、记忆和插图请按需核对。');
   }finally{lock.current=false;setBusy(false);}
 }
 async function copyMessage(value:string){
   try{await copyReply(value);setNotice('已复制回复原文。');}
   catch{setRaw(value);setNotice('请在原文窗口手动复制。');}
 }
 const pending=requirementsBlocked||trashed||!!local.lifecycle||!!local.request||!!s&&Object.values(s.requests).some(r=>r.status==='pending'),incompatible=s?.runtime?.compatible===false;
 const requestNotice=unconfirmedOutsideComparison(s?.requests,comparisonOperations,!!local.request);
 const memoryNotice=memory.unconfirmed&&unconfirmedOutsideComparison(memory.status?.operations,comparisonOperations,!!memory.pending||!!memory.status?.pendingOperationId);
 const summaryNotice=summary.unconfirmed&&unconfirmedOutsideComparison(summary.status?.taskState.operations,comparisonOperations,!!summary.pending||!!summary.status?.pendingOperationId);
 const historyPending=!!local.historySetting||!!s?.historyWindow?.pendingOperationId||!!history?.pendingOperationId;
 const historyUnavailable=!!s?.historyWindow&&(!history?.compatible||history.sessionRevision!==s.revision||historyPending);
 const memoryBudgetBlocked=!!s?.memoryPalace&&(s.mode==='real'||god)&&(remaining===undefined||remaining<(memory.status?.maxCalls??1));
 const budgetOperationPending=busy||pending||!!summary.status?.busy||!!memory.status?.busy||summary.unconfirmed||memory.unconfirmed||(reserved??0)>0;
 const showMemoryBudgetWarning=memoryBudgetBlocked&&!budgetOperationPending;
 const branchBlocked=memory.blocked||summary.blocked||busy||historyPending||!!local.selection||pending||incompatible||!!local.branch||!!storageError||!grant?.enabled;
 const modelUnavailable=s?.modelSelection?.current.kind==='controlled'&&modelCanSelect&&!modelLoading&&!modelError&&!modelList.some(m=>m.selectionId===s.modelSelection?.current.selectionId&&m.available&&m.selectionRevision===s.modelSelection?.current.selectionRevision);
 const deleteBlocked=busy||pending||historyPending||!!local.selection||!!local.branch||incompatible||!!storageError||memory.blocked||summary.blocked;
 const rewriteBlocked=deleteBlocked||budgetExhausted;
 const failureRows=Object.entries(s?.requests??{}).filter(([,r])=>!r.dismissed&&!!r.input&&r.status!=='complete'&&(!busy||r.status!=='pending')).map(([requestId,r])=>({requestId,input:r.input!,partial:r.partial||(local.failed?.requestId===requestId?local.failed.partial:undefined),error:r.error||'REQUEST_FAILED'}));
 if(!busy&&local.request&&!failureRows.some(r=>r.requestId===local.request!.requestId))failureRows.push({requestId:local.request.requestId,input:local.request.input,partial:display.text,error:'REQUEST_FAILED'});
 if(local.failed&&!failureRows.some(r=>r.requestId===local.failed!.requestId))failureRows.push({...local.failed,partial:local.failed.partial,error:local.failed.error||'REQUEST_FAILED'});
 function resolveFailure(row:typeof failureRows[number],action:'retry'|'edit'|'delete'){void changeSession({action:'dismiss-request',id:sessionId,requestId:row.requestId,input:action==='edit'&&local.draft?local.draft:row.input,retry:action==='retry',discard:action==='delete'});composer.current?.focus();}
 const deleteTurn=(turn:number)=>chooseSessionChange({action:'delete-turn',id:sessionId,turn});
 const close=()=>{if(!busy){setPanel('');setBranchTurn(null);}};
 return <section ref={configView} className="chat-shell rpg-chat-workspace" data-reading={reading.dark?'dark':'light'} data-sidebar={sidebar?'open':'closed'} data-width={reading.wide?'wide':'standard'} style={{'--reading-font':reading.font+'px'} as import('react').CSSProperties}>
 <SessionSidebar open={sidebar} close={()=>setSidebar(false)}>
  <header><span>{world?.name||'我的故事'}</span><button className="sidebar-close" onClick={()=>setSidebar(false)} aria-label="关闭会话列表">×</button></header>
  <button className="sidebar-new" disabled={busy||!!storageError} onClick={returnToCard}>＋ 新的故事</button>
  <p className="sidebar-label">已保存的会话</p><div className="sidebar-sessions">{list.map(item=><div className="sidebar-session-row" key={item.id}><button aria-current={item.id===sessionId?'page':undefined} disabled={busy||!!local.lifecycle||!!storageError} onClick={()=>navigate(()=>{if(item.id!==sessionId)opened(item.id);if(window.innerWidth<=800)setSidebar(false);})}><strong>{item.name}</strong><small>{item.turns} 回合{item.parentId?' · 分支':''}</small></button><button className="session-trash-button" aria-label={'删除会话：'+item.name} title="移入回收站" disabled={busy||!!local.lifecycle||!!storageError||pending&&!trashed} onClick={()=>chooseSessionChange({action:'delete',id:item.id,name:item.name})}>⌫</button></div>)}</div>
  <button className="text-button" disabled={busy||!!local.lifecycle} onClick={()=>setPanel('trash')}>回收站</button>
  {listError?<p role="status">{listError}<button onClick={()=>void refreshList()}>重新读取</button></p>:!list.length&&<p className="sidebar-label">还没有其他存档</p>}
  <footer><a href="/rpg" target="_top">← 故事馆</a><a href="/help/play-rpg-cards" target="_top">使用帮助 ↗</a></footer>
 </SessionSidebar>
 <div className="chat-main"><header className="chat-header">
  <button className="secondary" aria-label="会话列表" aria-expanded={sidebar} disabled={busy||!!storageError} onClick={showList}>☰</button><div className="chat-heading"><strong title={s?.name}>{s?.name||'正在读取故事…'}</strong><small>{s?.turns.length??0} 回合 · {world?.name||'RPG'}</small></div>
  <div className="chat-header-actions">{s?.modelSelection&&<button className="model-chip" aria-label={'选择模型：'+s.modelSelection.current.model} aria-haspopup="dialog" disabled={memory.blocked||summary.blocked||!modelCanSelect||busy||pending||historyPending||!!local.branch||!!local.selection||!!storageError} title={modelCanSelect?'选择下一条消息使用的模型':'请先在插件中启用模型选择'} onClick={()=>{setPanel('models');void loadModels();}}>{s.modelSelection.current.model} ▾</button>}{studio&&<button className="secondary" disabled={busy} onClick={()=>{setPanel('plugins');void loadPlugin();}}>插件</button>}<button className="secondary" onClick={()=>setPanel('reading')}>阅读</button></div>
 </header><div className="chat-scroll"><div className="chat-reading">
 {s?.parentId&&<p className="route-origin">从原对话第 {s.branchTurn} 回合延续的分支</p>}
 <div className="conversation" aria-live="polite">{s?.importedTurns?.map((t,i)=><article key={'import-'+i}><div className="player-message"><ConfigText scope={configScope} role="user" source={t.input}>{t.input}</ConfigText></div><div className="turn-label">第 {i+1} 回合 · 导入记录</div><ConfigText scope={configScope} role="assistant" source={t.raw}><div style={{whiteSpace:'pre-wrap'}}>{t.raw}</div></ConfigText></article>)}{s&&!pending&&!failureRows.length&&!s.turns.length&&!s.importedTurns?.length&&<div className="chat-empty"><span aria-hidden="true">序</span><h2>故事，从这一刻开始。</h2><p>写下你的角色、故事开场，或想做的第一件事。</p></div>}{s?.turns.map((t,i)=><article key={t.requestId}><div className="player-message"><ConfigText scope={configScope} role="user" source={t.input}>{t.input}</ConfigText></div>{toolsEnabled&&<button type="button" className="text-button" disabled={rewriteBlocked} title="编辑此回合的输入" onClick={()=>setEdit({turn:i+1,text:t.input,original:t.input})}>✎ 编辑输入</button>}{studio&&<button type="button" className="text-button" disabled={deleteBlocked} onClick={()=>deleteTurn(i+1)}>删除本轮</button>}<div className="turn-label">第 {i+1+(s.importedTurns?.length||0)} 回合 · {t.origin==='import'?'导入记录':t.outputEdited?'已编辑':'已保存'} · {t.origin==='import'?'外部文本':s.mode==='real'?(t.model?.actualModel||'模型未回报'):'离线样例'}</div><ConfigText scope={configScope} role="assistant" source={t.raw} render={t.origin==='import'?undefined:value=>value===t.raw&&cardStoragePrefix()!=='douluo'?<SavedOutput raw={t.raw} html={t.html}/>:<StreamOutput raw={value} card={cardStoragePrefix()}/>} >{t.origin==='import'?<div style={{whiteSpace:'pre-wrap'}}>{t.raw}</div>:<SavedOutput raw={t.raw} html={t.html}/>}</ConfigText>{pictures.gallery(t.requestId)}<div className="message-actions">{pictures.button(t.requestId)}{toolsEnabled&&<><button className="text-button" onClick={()=>void copyMessage(t.raw)}>复制回复</button>{['earth','god'].includes(cardStoragePrefix())&&<button className="text-button" disabled={trashed||!!local.lifecycle||busy||pending||historyPending||!!local.selection||!!local.branch||incompatible||!!storageError||summary.unconfirmed||memory.unconfirmed} onClick={()=>setOutputEdit({turn:i+1,text:t.raw,revision:s.revision})}>✎ 编辑回复</button>}<button className="text-button" disabled={rewriteBlocked} title="在当前会话重新生成，原内容保留为历史版本" onClick={()=>void rewrite(i+1,t.input)}>↻ 重新生成</button></>}{studio&&<button className="text-button" disabled={deleteBlocked} onClick={()=>deleteTurn(i+1)}>删除本轮</button>}<button className="text-button" onClick={()=>{setRaw(t.raw);setCopied(false);}}>查看原文</button>{studio&&<button className="text-button" disabled={branchBlocked} title={grant?.enabled?'从此回合创建新对话':'请在本会话插件设置中启用分支存档'} onClick={()=>{setBranchTurn(i+1);setBranchName(s.name+'-分支');setError('');}}>⑂ 分支</button>}</div></article>)}</div>{modelUnavailable&&<p className="notice" role="status">当前模型暂不可用，请选择其他模型。</p>}{s?.historyWindow&&panel!=='history'&&historyError&&<p className="notice" role="alert">{historyError}</p>}{historyPending&&panel!=='history'&&<p className="notice">历史窗口设置结果待核对。<button disabled={busy||!!storageError} onClick={()=>saveHistory()}>恢复设置结果</button></p>}{s?.rollingSummary&&summary.error&&panel!=='summary'&&<p className="notice" role="alert">{summary.error}</p>}{s?.rollingSummary&&!s?.memoryPalace&&summaryNotice&&!busy&&<p className="notice">{summary.status?.busy?summaryActivity(summary.status)+'。':'有未确认的会话操作。'}<button disabled={summary.status?.busy} onClick={()=>summary.recover()}>恢复操作结果</button></p>}{s?.rollingSummary&&summary.status?.enabled&&!summary.status.v2&&!summary.status.config.model&&<p className="notice">请在自动总结设置中选择摘要模型。</p>}{s?.memoryPalace&&memory.error&&panel!=='memory'&&<p className="notice" role="alert">{memory.error}</p>}{s?.memoryPalace&&memoryNotice&&!busy&&!failureRows.length&&<p className="notice">{memory.status?memoryActivity(memory.status):'会话结果待核对'}。<button disabled={memory.status?.busy} onClick={()=>memory.recover()}>恢复操作结果</button></p>}{memory.status?.enabled&&!memory.status.config.model&&<p className="notice">请在记忆宫殿中选择记忆模型。</p>}{memory.status?.blocked&&!memory.unconfirmed&&<p className="notice">记忆更新未完成。请在宫殿中核对后明确更新，或停用插件。</p>}{local.selection&&<p className="notice">模型选择结果待核对。<button disabled={busy||!!storageError} onClick={()=>selectModel()}>恢复选择结果</button></p>}{(storageError||error&&!failureRows.some(row=>error===row.error||error===memoryFailureNotice(row.error)))&&<p className="notice" role="alert">{storageError||error}</p>}{notice&&<p role="status">{notice}</p>}{local.lifecycle&&<p className="notice">会话管理操作结果待核对。<button disabled={busy||!!storageError} onClick={()=>void changeSession()}>核对会话操作</button></p>}{trashed&&<p className="notice">此会话在回收站中。<button disabled={busy} onClick={()=>setPanel('trash')}>打开回收站</button></p>}{requestNotice&&!busy&&!failureRows.length&&<p className="notice">上次发送的结果还未确认。请先核对记录，避免重复生成。</p>}{local.branch&&<p className="notice">分支创建结果待核对。<button disabled={busy||!!storageError} onClick={()=>createBranch(true)}>恢复创建结果</button></p>}{incompatible&&<p className="notice">此存档与当前版本不兼容，已有内容仍可阅读。请创建新会话继续游玩。</p>}{s?.mode==='real'&&budgetExhausted&&<p className="notice">本卡额度已用完。仍可查看、创建分支和切换。</p>}
 {memory.status?.enabled&&<p className="memory-fee">本次发送最多 {memory.status.maxCalls} 次调用（含剧情、摘要与记忆）；未派发的额度会释放。{showMemoryBudgetWarning?(remaining===undefined?'额度状态待核对，请恢复记录后再试。':'下一次发送的可用额度不足，请先核对配置。'):''}</p>}
 {failureRows.map(row=><section key={row.requestId} className="failed-turn" aria-label="未完成的回合"><div className="player-message">{row.input}</div><p className="notice" role="status">{memoryFailureNotice(row.error)}</p>{row.partial&&<StreamOutput raw={row.partial} card={cardStoragePrefix()}/>}<div className="message-actions"><button className="secondary" disabled={busy||!!local.lifecycle} onClick={()=>resolveFailure(row,'retry')}>重新发送</button><button className="text-button" disabled={busy||!!local.lifecycle} onClick={()=>resolveFailure(row,'edit')}>修改输入</button><button className="text-button" disabled={busy||!!local.lifecycle} onClick={()=>resolveFailure(row,'delete')}>删除本轮</button></div><p className="dialog-hint">部分输出未写入剧情和记忆。重新发送会发起新请求；原请求可能已计费。</p></section>)}
 {toolsEnabled&&<div className="message-tools-bar">{s?.mode==='real'&&<button className="secondary" type="button" aria-pressed={display.stream} onClick={display.toggle}>{display.stream?'流式输出':'完整输出'}</button>}{display.warning&&<span role="status">{display.warning}</span>}{display.stream&&display.text&&(local.request||rewriteWatch)&&<section key={display.operationKey} className="message-stream" aria-label="正在生成的回复"><p className="turn-label" role="status">正在生成…</p><ConfigText scope={configScope} role="assistant" source={display.text} render={value=><StreamOutput raw={value} card={cardStoragePrefix()}/>}/></section>}</div>}
 {outputEdit&&<OutputEditor turn={outputEdit.turn} text={outputEdit.text} save={saveOutput} close={()=>setOutputEdit(null)}/>}
 {edit&&<Modal title="编辑输入" className="chat-dialog" close={()=>setEdit(null)}><form className="branch-form" onSubmit={e=>{e.preventDefault();void rewrite(edit.turn,edit.text);}}><label htmlFor="message-edit">第 {edit.turn} 回合</label><textarea id="message-edit" autoFocus rows={8} maxLength={20000} value={edit.text} onChange={e=>setEdit({...edit,text:e.target.value})}/><p>在当前会话重新生成。原回复和后续剧情将保存为可恢复的历史版本。</p><button type="button" className="secondary" onClick={()=>setEdit(null)}>取消</button><button className="chat-primary" disabled={rewriteBlocked||!edit.text.trim()}>保存并重新生成</button></form></Modal>}
 {pictures.gallery(null)}{pictures.modal}
 </div></div><div className="chat-dock">{s?.generationParameters&&catalog?.plugins.some(p=>p.id==='rpg.model-comparison')&&<ModelComparison sessionId={sessionId} enabled={!requirementsBlocked&&!!grants['rpg.model-comparison']?.enabled} getInput={()=>client.current.draft} setInput={value=>{persist({...client.current,draft:value});}} blocked={(value,ids)=>{setComparisonBlocked(value);setComparisonOperations(old=>JSON.stringify(old)===JSON.stringify(ids)?old:ids);}} changed={async()=>{accept(await api('sessions/'+sessionId));await loadBudget();await summary.refresh();await memory.refresh();}}/>}<div className="chat-tools" aria-label="聊天工具">
 {s?.generationParameters&&<button className="text-button" disabled={busy||pending||!grants['rpg.generation-parameters']?.enabled} onClick={()=>setPanel('parameters')}>生成参数</button>}{s?.requirements&&<button className="text-button" disabled={busy||(pending&&!requirementsBlocked)} onClick={()=>setPanel('requirements')}>持续要求{requirementsBlocked?' · 待核对':''}</button>}
 {summary.status?.enabled&&<button className="text-button" disabled={busy} onClick={()=>setPanel('summary')}>自动总结</button>}{memory.status?.enabled&&<button className="text-button" disabled={busy||!memory.status?.compatible} onClick={()=>setPanel('memory')}>记忆宫殿</button>}{grants['rpg.history-window']?.enabled&&<button className="text-button" disabled={busy||historyUnavailable} onClick={()=>setPanel('history')}>历史窗口</button>}
 {pictures.toolbar}{s&&grants['rpg.transcript']?.enabled&&<TranscriptTools id={s.id} name={s.name} disabled={busy||pending||!!storageError} opened={opened}/>}{studio&&<ModPanel card={cardStoragePrefix()} session={sessionId} busy={busy||pending}/>} {configScope&&<ConfigTools scope={configScope}/>}
 <button className="text-button" disabled={busy||!!local.lifecycle||trashed} onClick={()=>setPanel('versions')}>历史版本</button><button className="text-button" disabled={busy} onClick={restore}>核对记录</button>
 </div><form className="composer" onSubmit={e=>{e.preventDefault();void send();}}><textarea ref={composer} aria-label="对话输入" placeholder="接下来，你想做什么？" value={local.draft} disabled={busy||trashed||!!local.lifecycle||incompatible||!!initial.error} maxLength={20000} onChange={e=>persist({...client.current,draft:e.target.value})} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void send();}}}/><span>{local.draft.length}</span>{generating?<button type="button" className="send" aria-label="取消生成" onClick={()=>api('sessions/'+sessionId+'/cancel',{}).catch(e=>setError(e.message))}>■</button>:<button className="send" aria-label="发送" disabled={trashed||requirementsBlocked||comparisonBlocked||!!storageError||memory.blocked||memoryBudgetBlocked||summary.blocked||historyUnavailable||modelUnavailable||sendBlocked(s,local,busy,remaining)}>↑</button>}</form><p className="composer-hint"><span>Enter 发送 · Shift + Enter 换行</span><span>{s?.mode==='real'?`可用 ${remaining??'待核对'} 次${reserved?` · 预留 ${reserved} 次`:''}`:'离线预览 · 不调用模型'}</span></p></div></div>
 {(panel==='versions'||panel==='trash')&&<SessionHistoryDialog kind={panel} id={sessionId} busy={busy||pending&&!trashed} choose={chooseSessionChange} close={close}/>}
 {sessionChange&&<SessionChangeDialog target={sessionChange} busy={busy} confirm={()=>void changeSession(sessionChange)} close={()=>{if(!busy)setSessionChange(null);}}/>}
 {panel==='reading'&&<Modal title="阅读设置" className="chat-dialog reading-dialog" close={close}><fieldset><legend>阅读背景</legend><div className="two"><button className="secondary" aria-pressed={!reading.dark} onClick={()=>setReading({...reading,dark:false})}>浅色</button><button className="secondary" aria-pressed={reading.dark} onClick={()=>setReading({...reading,dark:true})}>深色</button></div></fieldset><label>正文字号 <output>{reading.font}px</output><input aria-label="正文字号" type="range" min={15} max={20} step={1} value={reading.font} onChange={e=>setReading({...reading,font:Number(e.target.value)})}/></label><label className="history-check"><input type="checkbox" checked={reading.wide} onChange={e=>setReading({...reading,wide:e.target.checked})}/>宽版阅读</label><p className="dialog-hint">仅调整平台文字的阅读方式。角色卡自带的排版保持原样。</p></Modal>}
 {panel==='list'&&<Modal title="对话列表" className="chat-dialog" close={close}><p className="dialog-hint">未发送的输入按对话保留，切换后自动恢复。</p><div className="history-list">{list.map(item=><button className="option" key={item.id} disabled={busy||!!storageError} onClick={()=>navigate(()=>{if(item.id===sessionId)close();else opened(item.id);})}><span><strong>{item.name}</strong><small>{item.turns} 回合{item.parentId?` · 从「${list.find(p=>p.id===item.parentId)?.name||'原对话'}」第 ${item.branchTurn} 回合分支`:''}</small></span><span>{item.id===sessionId?'当前':'打开 →'}</span></button>)}</div>{error&&<p role="alert">{error}</p>}</Modal>}
 {panel==='requirements'&&s?.requirements&&<PersistentRequirements sessionId={sessionId} close={close} blocked={setRequirementsBlocked} changed={async()=>{accept(await api('sessions/'+sessionId));await summary.refresh();await memory.refresh();}}/>}
 {panel==='parameters'&&s?.generationParameters&&<GenerationParameters sessionId={sessionId} close={close} changed={async()=>{accept(await api('sessions/'+sessionId));await loadPlugin();}}/>}
 {panel==='models'&&s?.modelSelection&&<ModelPicker current={s.modelSelection.current} models={modelList} loading={modelLoading} error={modelError||(!modelCanSelect?'当前会话暂不能切换模型':'')} locked={busy} refresh={loadModels} choose={m=>void selectModel(m)} close={close}/>}
 {panel==='plugins'&&<Modal title="会话插件" className="chat-dialog plugin-drawer" close={close}>
 <SessionPlugins market={'/rpg/plugins?card='+worldId(cardStoragePrefix())} open={setPanel} change={id=>{const p=catalog?.plugins.find(p=>p.id===id);if(p)void changePlugin(p);}} rows={(catalog?.plugins||[]).map(p=>{
  const g=grants[p.id],window=p.id==='rpg.history-window',model=p.id==='rpg.model-selector',mem=p.id==='rpg.memory-palace',sum=p.id==='rpg.rolling-summary',parameters=p.id==='rpg.generation-parameters',comparison=p.id==='rpg.model-comparison';
  const supported=parameters||comparison?!!s?.generationParameters:window?!!s?.historyWindow:model?!!s?.modelSelection:mem?!!s?.memoryPalace:sum?!!s?.rollingSummary:true;
  const locked=busy||(!g?.enabled&&(pending||historyPending||incompatible||!!local.selection||!!local.branch||(mem&&(memory.blocked||summary.blocked))||(sum&&summary.blocked)));
  const setting=parameters?{label:'设置参数',panel:'parameters',disabled:busy||pending||!g?.enabled}:mem?{label:'打开宫殿',panel:'memory',disabled:busy||!memory.status?.compatible}:sum?{label:'查看总结',panel:'summary',disabled:busy||!g?.enabled||!summary.status?.compatible}:window?{label:'设置',panel:'history',disabled:busy||pending||historyPending||!!local.selection||!!local.branch||!g?.enabled||historyUnavailable}:model?{label:'选择模型',panel:'models',disabled:busy||!modelCanSelect||pending||historyPending||!!local.selection||!!local.branch||memory.blocked||summary.blocked||!!storageError}:undefined;
  const presentation=sum&&!summary.status?.v2?{name:'自动总结',description:'整理较早的剧情，让故事保持连贯。',cost:'按摘要策略计费，发送前显示上限',detail:'由当前会话的摘要策略决定整理时机；可选择独立模型，完整原文保留。'}:undefined;
  return {id:p.id,name:p.name,enabled:!!g?.enabled,installed:!!g?.installed,known:!!g,supported,locked,setting,presentation,upgradeRequired:needsPluginUpgrade(p),version:p.version,installedVersion:p.installedVersion,reason:g?.reason};
 })}/>{pluginError&&<p role="alert">{pluginError}<button onClick={()=>void loadPlugin()}>重新读取</button></p>}
 </Modal>}
 {panel==='memory'&&memory.status?.compatible&&<MemoryPalaceDialog status={memory.status} sessionId={sessionId} busy={busy} pending={memory.unconfirmed} error={memory.error} perform={memory.perform} recover={()=>void memory.recover()} close={close}/>}
 {panel==='summary'&&summary.status?.compatible&&(summary.status.v2?<SummaryV2Dialog status={summary.status} sessionId={sessionId} busy={busy} pending={summary.unconfirmed} error={summary.error||pluginError} perform={summary.perform} disable={()=>{const plugin=catalog?.plugins.find(p=>p.id==='rpg.rolling-summary');return plugin&&grants[plugin.id]?.enabled?changePlugin(plugin):Promise.resolve(false);}} recover={()=>void summary.recover()} close={close}/>:<RollingSummaryDialog status={summary.status} sessionId={sessionId} busy={busy} pending={summary.unconfirmed} error={summary.error} save={v=>summary.perform('settings',v)} update={()=>void summary.perform('update')} recover={()=>void summary.recover()} close={close}/>)}
 {panel==='history'&&s?.historyWindow&&<HistoryWindowDialog hasCharacter={!!s.characterText} config={history?.config||s.historyWindow.config} busy={busy} pending={historyPending} error={historyError} save={v=>void saveHistory(v)} recover={()=>void saveHistory()} close={close}/>}
 {branchTurn!==null&&<Modal title="创建分支" className="chat-dialog" close={close}><form className="branch-form" onSubmit={e=>{e.preventDefault();void createBranch();}}><p>保留截至第 {branchTurn} 回合的记录，在新对话中尝试另一种选择。</p><label htmlFor="branch-name">新对话名称</label><input autoFocus id="branch-name" value={branchName} maxLength={80} disabled={busy||!!local.branch} onChange={e=>setBranchName(e.target.value)}/><p className="dialog-hint">创建不会调用模型。原对话{local.draft?'及未发送的输入':''}会保留。</p>{error&&<p role="alert">{error}</p>}<div className="two"><button type="button" className="secondary" disabled={busy} onClick={close}>取消</button>{local.branch?<button className="chat-primary" type="button" disabled={busy} onClick={()=>createBranch(true)}>恢复创建结果</button>:<button className="chat-primary" disabled={busy||!branchName.trim()||!!storageError}>{busy?'正在创建…':'创建并进入'}</button>}</div></form></Modal>}
 {raw!==null&&<Modal title="保存的原文" className="chat-dialog" close={()=>setRaw(null)}><pre className="raw-card">{raw}</pre><button className="chat-primary full" onClick={()=>navigator.clipboard.writeText(raw).then(()=>setCopied(true)).catch(()=>setError('剪贴板不可用'))}>{copied?'已复制':'复制原文'}</button></Modal>}
 </section>;
}
