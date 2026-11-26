import {check,copy,digest,derivedView,visible,prefixHash} from '../revisions/contracts.mjs';
import {isId,keys} from '../../authoring/contracts.mjs';

function describe(value,other) {
  if (value === undefined) return '（无）';
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  if(text.length<=320)return text;
  const opposite=typeof other==='string'?other:JSON.stringify(other)??'';
  let i=0;while(i<text.length&&i<opposite.length&&text[i]===opposite[i])i++;
  const start=Math.max(0,Math.min(i-60,text.length-240));
  return (start?'…':'')+text.slice(start,start+240)+(start+240<text.length?'…':'')+'（共 '+text.length+' 字符）';
}

export function compareConfigurations(before,after) {
  const changes=[];
  function compare(section,a={},b={}) {
    for (const key of new Set([...Object.keys(a),...Object.keys(b)])) {
      if (Object.hasOwn(a,key)&&Object.hasOwn(b,key)&&digest(a[key])===digest(b[key])) continue;
      changes.push({section,key,before:describe(a[key],b[key]),after:describe(b[key],a[key])});
    }
  }
  const a=before.document.character.card,b=after.document.character.card;
  compare('角色定义',a.data,b.data);
  const {data:oldData,...oldEnvelope}=a,{data:newData,...newEnvelope}=b;
  compare('交换格式',oldEnvelope,newEnvelope);
  const {character:oldCharacter,...oldDocument}=before.document,{character:newCharacter,...newDocument}=after.document;
  compare('作品设置',oldDocument,newDocument);
  const {card:oldCard,...oldPresentation}=oldCharacter,{card:newCard,...newPresentation}=newCharacter;
  compare('角色展示与来源',oldPresentation,newPresentation);
  compare('封存资源',{assets:before.assets??[]},{assets:after.assets??[]});
  return changes;
}

export async function sealed(works,ref) {
  keys(ref,['workId','versionId']);check(isId(ref.workId)&&isId(ref.versionId),'TEMPLATE_SEALED_REQUIRED');
  const config=await works.verifyConfiguration(ref.workId,ref.versionId);
  check(config.document.kind==='character','TEMPLATE_CHARACTER_REQUIRED');return config;
}

// Stateful rules formerly used (member, sealed reference, entry index). Retain that
// exact identity only for one-to-one, unchanged definitions under identical book settings.
export function planSwitch({member,before,after,versionId,previous,operationId,position}) {
  const oldRef=member.binding.reference,reference={workId:oldRef.workId,versionId};
  check(versionId!==oldRef.versionId&&digest(before)===member.binding.configurationHash,'TEMPLATE_BINDING_CHANGED');
  const priorCard=before.document.character.card,nextCard=after.document.character.card;
  const {entries:oldEntries=[],...oldSettings}=priorCard.data.character_book??{};
  const {entries:nextEntries=[],...nextSettings}=nextCard.data.character_book??{};
  const stable=priorCard.spec===nextCard.spec && digest(oldSettings)===digest(nextSettings);
  function indices(entries) {
    const map=new Map();entries.forEach((e,i)=>{const k=digest(e);map.set(k,map.has(k)?null:i);});return map;
  }
  const prior=indices(oldEntries),next=indices(nextEntries),matches=[];
  function duplicateIds(entries){
    const seen=new Set(),duplicates=new Set();
    for(const entry of entries)if(entry.id!==undefined){if(seen.has(entry.id))duplicates.add(entry.id);seen.add(entry.id);}
    return duplicates;
  }
  const ambiguous=new Set([...duplicateIds(oldEntries),...duplicateIds(nextEntries)]);
  const scopes=nextEntries.map((entry,index)=>{
    const k=digest(entry),i=prior.get(k);
    if(stable && !ambiguous.has(entry.id) && Number.isInteger(i) && next.get(k)===index){
      matches.push({from:i,to:index});return previous?.scopes[i]??{reference:oldRef,index:i};
    }
    return {reference:{...reference,transition:operationId},index};
  });
  const keptOld=new Set(matches.map(m=>m.from)),keptNew=new Set(matches.map(m=>m.to));
  return {memberId:member.id,position,binding:{reference,configurationHash:digest(after)},scopes,
    report:{matches,reset:nextEntries.map((_,i)=>i).filter(i=>!keptNew.has(i)),
      retired:oldEntries.map((_,i)=>i).filter(i=>!keptOld.has(i))}};
}

export function freezeMemoryProofs(branch){
  const proofs={};
  for(const member of branch.current.members){
    if(member.kind!=='character'||member.intervals.at(-1)?.until!==null)continue;
    const history=visible(branch,member.id);
    for(const note of derivedView(branch,member.id)){
      if(note.status==='valid'&&note.prefixHash)proofs[note.id]={length:history.length,hash:note.prefixHash};
    }
  }
  return proofs;
}

export function effectiveFamily(row,branchId,actor) {
  const transition=row.tracks[branchId].switches?.findLast(event=>event.memberId===actor);
  const track=row.tracks[branchId],original=row.f,branch=original.branches[branchId];
  let family=original;
  if(track.memoryProofs){
    const shown=visible(branch,actor);
    const derived=branch.derived.map(note=>{
      const proof=track.memoryProofs[note.id];
      if(note.ownerId!==actor||!proof||proof.hash!==note.prefixHash)return note;
      // Freeze only the validated prefix. Appended input must not invalidate it.
      if(proof.length>shown.length||prefixHash(shown.slice(0,proof.length))!==proof.hash)return note;
      return {...note,prefixHash:null};
    });
    family={...family,branches:{...family.branches,[branchId]:{...branch,derived}}};
  }
  if(!transition)return family;
  const config=row.templates[transition.binding.configurationHash];
  check(config&&digest(config)===transition.binding.configurationHash,'TEMPLATE_BINDING_CHANGED');
  return {...family,source:{...family.source,cards:{...family.source.cards,[actor]:config}}};
}

export function inheritSwitches(track,count,tip) {
  // A historical message is an anchor before any changes made after that message.
  return copy((track.switches??[]).filter(event=>tip||event.position<count));
}
