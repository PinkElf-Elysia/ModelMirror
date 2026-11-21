import { boundary, check, copy, id, pending, visible, derivedView } from '../revisions/contracts.mjs';
import { keys } from '../../authoring/contracts.mjs';
import { participated } from '../conversation/contracts.mjs';

export const actions = ['draft', 'adopt', 'disable', 'tell'];
const normalize = value => value.normalize('NFKC').toLowerCase();
function string(value, limit) {
  check(typeof value === 'string' && value.isWellFormed() && value.trim() && value.length <= limit, 'MEMORY_TEXT');
  return value.trim();
}
function sources(branch, actor) {
  return new Map(visible(branch, actor).map(message => [message.id, message]));
}
function audience(branch, note, actor) {
  if (note.owner && note.owner !== actor) return false;
  const member = boundary(branch).members.find(m => m.id === actor);
  return note.refs.every(ref => participated(member, ref.position) &&
    (ref.visibility.kind === 'public' || ref.visibility.memberIds.includes(actor)));
}
function intact(branch, note) {
  return note.refs.every(ref => branch.messages[ref.position - 1]?.revisionId === ref.revisionId &&
    branch.messages[ref.position - 1]?.id === ref.id);
}
function latest(branch, actor) {
  // Filter the current version after resolving its status. A superseded version must not reappear.
  const entries = new Map();
  for (const event of branch.notes ?? []) {
    const entry = entries.get(event.noteId) ?? {};
    if (event.action === 'draft' || event.action === 'review') entry.draft = event;
    else if (event.action === 'adopt') { entry.live = event; delete entry.draft; }
    else if (event.action === 'disable') { entry.live = event; delete entry.draft; }
    entries.set(event.noteId, entry);
  }
  return [...entries.values()].flatMap(({live, draft}) => [live, draft].filter(n => n && audience(branch, n, actor))
    .map(n => ({...copy(n), status:n.action === 'disable' ? 'disabled' : !intact(branch,n) ? 'invalid' : n.action === 'review' ? 'needs-review' : n.action === 'draft' ? 'draft' : 'valid'})));
}
export function panel(branch, actor) {
  const messages = [...sources(branch,actor).values()];
  const records=latest(branch,actor).map(note=>({...note,refs:note.refs.map(({id,revisionId,position,speakerId})=>({id,revisionId,position,speakerId}))}));
  return {records, messages:messages.map(({id,revisionId,content,speakerId,visibility}) =>
    ({id,revisionId,content,speakerId,public:visibility.kind === 'public'}))};
}
export function matches(branch, actor, texts) {
  const scan = texts.map(normalize);
  return latest(branch, actor).filter(n => n.status === 'valid').map(n => ({
    ...n, hits:n.words.filter(word => scan.some(line => line.includes(normalize(word))))
  })).sort((a,b) => b.hits.length-a.hits.length || b.sequence-a.sequence || a.noteId.localeCompare(b.noteId));
}
export function inheritNotes(parent, child) {
  const cut = parent.noteCutoffs?.[child.messages.length - 1] ?? 0;
  child.noteCutoffs = (parent.noteCutoffs ?? []).slice(0,child.messages.length);
  child.notes = copy((parent.notes ?? []).filter(n => n.position <= child.messages.length && n.sequence <= cut));
}
export function applyNote(f, action, input) {
  check(input.expectedRevision === f.revision && !pending(f),'REVISION_CONFLICT');
  const p=input.payload;
  keys(p, action === 'draft' ? ['branchId','actor','noteId','owner','kind','content','origin','refs','mode','words'] :
    action === 'tell' ? ['branchId','actor','noteId','content'] : ['branchId','actor','noteId']);
  const b=f.branches[p.branchId]; check(b,'BRANCH_NOT_FOUND');
  const allowed=sources(b,p.actor);
  check(boundary(b).members.some(m => m.id === p.actor && m.kind === 'character'),'TARGET_INVALID');
  const versions=latest(b,p.actor).filter(n=>n.noteId===p.noteId);
  let note;
  if(action === 'draft') {
    check(p.noteId===null || versions.length,'MEMORY_NOT_FOUND');
    check(p.owner===null || p.owner===p.actor,'MEMORY_OWNER');
    check(versions.every(n=>n.owner===p.owner),'MEMORY_OWNER');
    check(['summary','memory'].includes(p.kind) && ['author','experience'].includes(p.origin) &&
      ['fixed','keyword'].includes(p.mode),'MEMORY_TYPE');
    check(Array.isArray(p.refs) && p.refs.length<=128 && (p.refs.length>0)===(p.origin==='experience'),'MEMORY_SOURCES');
    const refs=p.refs.map(ref=>{
      keys(ref,['id','revisionId']);
      const m=allowed.get(ref.id); check(m && m.revisionId===ref.revisionId,'MEMORY_SOURCE_CHANGED');
      check(p.owner!==null || m.visibility.kind==='public','MEMORY_PRIVATE_SOURCE');
      return {id:m.id, revisionId:m.revisionId, position:b.messages.indexOf(m)+1, visibility:copy(m.visibility),speakerId:m.speakerId};
    });
    check(new Set(refs.map(r=>r.id)).size===refs.length,'MEMORY_SOURCES');
    check(Array.isArray(p.words)&&p.words.length<=32,'MEMORY_WORDS');
    const words=[...new Set(p.words.map(w=>normalize(string(w,80))))];
    check(p.mode==='fixed'||words.length,'MEMORY_WORDS');
    note={noteId:p.noteId??id('note',f.id,input.operationId),owner:p.owner,kind:p.kind,origin:p.origin,
      refs,words,mode:p.mode,content:string(p.content,16000)};
  } else if(action==='tell') {
    const n=versions.find(n=>n.status==='valid');
    check(f.source.kind==='group'&&n&&n.owner===null&&n.origin==='experience','MEMORY_TELL');
    const state=copy(boundary(b));
    b.messages.push({id:id('message',f.id,input.operationId),revisionId:id('revision',f.id,input.operationId),
      content:string(p.content,16000),speakerId:state.members[0].id,visibility:{kind:'public'},addressedTo:[],
      generation:null,slotId:null,provenance:{kind:'player-input'},boundary:state});
  } else {
    note=versions.find(n=>action==='adopt'? n.status==='draft' : n.action==='adopt');
    check(note,'MEMORY_STATUS');
  }
  if(action==='tell') (b.noteCutoffs??=[]).push(f.revision+1);
  if(note) (b.notes??=[]).push({...note,action,actor:p.actor,position:b.messages.length,
    sequence:f.revision+1,version:id('note-version',f.id,input.operationId)});
  f.revision++;
}

export function importNotes(branch) {
  branch.notes=[];
  branch.noteCutoffs=branch.messages.map(()=>0);
  for (const owner of boundary(branch).members.filter(m=>m.kind==='character')) {
    if (!owner.intervals.length || owner.intervals.at(-1).until!==null) continue;
    for(const d of derivedView(branch,owner.id)) {
      const refs=branch.messages.flatMap((m,i)=>d.dependencies?.includes(m.revisionId)?
        [{id:m.id,revisionId:m.revisionId,position:i+1,speakerId:m.speakerId,visibility:copy(m.visibility)}]:[]);
      branch.notes.push({action:d.status==='valid'?'adopt':'review',noteId:d.id,version:id('legacy-note',d.id),
        position:d.availableAfter??branch.messages.length,actor:owner.id,owner:owner.id,kind:'memory',origin:'legacy',
        refs,mode:'fixed',words:[],sequence:0,content:d.content});
    }
  }
}
