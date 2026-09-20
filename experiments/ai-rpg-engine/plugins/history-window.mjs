// Reviewed, synchronous proposal/selection code. No I/O, network, model or storage access.
export const POLICY_VERSION = 'rpg.history-window/1';
export const DEFAULT_CONFIG = Object.freeze({turns:3,includeInitialCharacter:false});
const exact=(v,keys)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===keys.slice().sort().join(',');
export const validConfig=v=>exact(v,['turns','includeInitialCharacter'])&&Number.isInteger(v.turns)&&v.turns>=1&&v.turns<=50&&typeof v.includeInitialCharacter==='boolean';
const invalid=code=>{throw Object.assign(Error(code),{code,status:400});};
export function selectHistory({history,characterText,enabled,config=DEFAULT_CONFIG}) {
 if(typeof enabled!=='boolean'||!validConfig(config))invalid('HISTORY_CONFIGURATION_INVALID');
 if(!Array.isArray(history)||history.length%2||history.some((m,i)=>!m||m.role!==(i%2?'assistant':'user')||typeof m.content!=='string'))invalid('HISTORY_INCOMPLETE');
 if(typeof characterText!=='string'||!characterText.trim())invalid('HISTORY_CHARACTER_INVALID');
 const totalTurns=history.length/2,start=enabled?Math.max(0,totalTurns-config.turns):0;
 const selected=history.slice(start*2).map(({role,content})=>({role,content}));
 const initialSnapshotAdded=enabled&&config.includeInitialCharacter&&start>0;
 if(initialSnapshotAdded)selected[0].content='开局角色资料（原始快照）\n'+characterText+'\n\n'+selected[0].content;
 return {history:selected,record:{policyVersion:POLICY_VERSION,enabled,config:{...config},totalTurns,selectedTurns:Array.from({length:totalTurns-start},(_,i)=>start+i+1),initialSnapshotAdded}};
}
export function invoke({capability,session,input={}}) {
 if(capability==='ui.history-action'&&exact(input,[]))return {kind:'history-action',action:'history-settings',label:'历史窗口'};
 if(capability==='session.history.configure'&&validConfig(input))return {kind:'history-configuration',sessionId:session.id,...input};
 throw Error('INVALID_HISTORY_WINDOW_REQUEST');
}
