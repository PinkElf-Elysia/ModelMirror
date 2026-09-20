// Reviewed synchronous proposals only. No I/O, credentials or Provider access.
export const POLICY_VERSION = 'rpg.rolling-summary/1';
export const MAX_SUMMARY_CHARACTERS = 10000;
const exact=(v,keys)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===keys.slice().sort().join(',');
const selection=v=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(v);
const fail=code=>{throw Object.assign(Error(code),{code,status:400});};
export const validText=v=>typeof v==='string'&&!!v.trim()&&Array.from(v).length<=MAX_SUMMARY_CHARACTERS;
export const validSettings=v=>exact(v,['timing','selectionId','catalogRevision'])&&['before','after'].includes(v.timing)&&selection(v.selectionId)&&selection(v.catalogRevision);
export const validEdit=v=>exact(v,['textHash','characters'])&&typeof v.textHash==='string'&&/^[a-f0-9]{64}$/.test(v.textHash)&&Number.isInteger(v.characters)&&v.characters>0&&v.characters<=MAX_SUMMARY_CHARACTERS;
export function planCoverage(history,coveredThrough=0){
 if(!Array.isArray(history)||history.length%2||history.some((m,i)=>!m||m.role!==(i%2?'assistant':'user')||typeof m.content!=='string'))fail('SUMMARY_HISTORY_INCOMPLETE');
 const completedTurns=history.length/2,targetThrough=Math.max(0,completedTurns-1);
 if(!Number.isSafeInteger(coveredThrough)||coveredThrough<0||coveredThrough>targetThrough)fail('SUMMARY_COVERAGE_INVALID');
 return {completedTurns,targetThrough,coveredThrough,needsUpdate:coveredThrough<targetThrough,
  newTurns:Array.from({length:targetThrough-coveredThrough},(_,i)=>coveredThrough+i+1),
  source:history.slice(coveredThrough*2,targetThrough*2).map(({role,content})=>({role,content})),
  recent:history.slice(Math.max(0,completedTurns-1)*2).map(({role,content})=>({role,content}))};
}
export function invoke({capability,session,input={}}){
 if(capability==='ui.summary-action'&&exact(input,[]))return {kind:'summary-action',action:'summary-settings',label:'自动总结'};
 if(capability==='session.summary.configure'&&validSettings(input))return {kind:'summary-configuration',sessionId:session.id,...input};
 if(capability==='session.summary.revise'&&validEdit(input))return {kind:'summary-revision',sessionId:session.id,...input};
 if(capability==='session.summary.request'&&exact(input,[]))return {kind:'summary-request',sessionId:session.id};
 throw Error('INVALID_ROLLING_SUMMARY_REQUEST');
}
