export type RequestRecord={input:string;requestId:string;revision:number};
export type BranchRecord={operationId:string;expectedSessionRevision:number;turn:number;name:string};
export type ClientState={draft:string;request:RequestRecord|null;branch:BranchRecord|null};
export type Session={id:string;name:string;characterText:string;revision:number;mode:string;parentId?:string;branchTurn?:number;runtime?:{compatible:boolean;code:string|null};turns:{requestId:string;input:string;raw:string;html:string;at:string}[];requests:Record<string,{status:string;error?:string}>};
export class ApiError extends Error{constructor(message:string,public status:number){super(message);}}
export async function api(path:string,body?:unknown){const response=await fetch((location.pathname.startsWith('/rpg-app/earth/')?'/rpg-app/earth/api/':'/api/')+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw new ApiError(data.error||'本地请求未完成',response.status);return data;}
export async function catalogApi(){const r=await fetch('/rpg-app/api/plugins');if(!r.ok)throw Error('插件目录暂不可用，聊天仍可使用。');return r.json();}
export const emptyClientState=():ClientState=>({draft:'',request:null,branch:null});
const key=(id:string)=>'earth-chat-v1:'+id;
export function readClientState(id:string,storage:Pick<Storage,'getItem'>=localStorage):ClientState{
 const raw=storage.getItem(key(id));if(!raw)return emptyClientState();const x=JSON.parse(raw);
 if(!x||typeof x.draft!=='string'||x.draft.length>20000||!(x.request===null||typeof x.request?.input==='string'&&typeof x.request?.requestId==='string'&&Number.isInteger(x.request?.revision))||!(x.branch===null||typeof x.branch?.operationId==='string'&&typeof x.branch?.name==='string'&&Number.isInteger(x.branch?.turn)&&Number.isInteger(x.branch?.expectedSessionRevision)))throw Error('本地草稿记录无法读取，请保留浏览器数据并核对。');return x;
}
export function writeClientState(id:string,state:ClientState,storage:Pick<Storage,'setItem'>=localStorage){storage.setItem(key(id),JSON.stringify(state));}
export function reconcileClientState(state:ClientState,s:Pick<Session,'requests'>):ClientState{const req=state.request;if(!req)return state;const status=s.requests[req.requestId]?.status;if(!['complete','failed','cancelled'].includes(status||''))return state;return {...state,draft:status==='complete'&&state.draft===req.input?'':state.draft,request:null};}
export function sendBlocked(s:Session|null,local:ClientState,busy:boolean,remaining?:number){return !s||busy||!local.draft.trim()||!!local.request||!!local.branch||s.runtime?.compatible===false||Object.values(s.requests).some(r=>r.status==='pending')||(s.mode==='real'&&(remaining===undefined||remaining<=0));}
