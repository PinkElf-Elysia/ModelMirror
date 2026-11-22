import {keys,isId,fail} from '../../authoring/contracts.mjs';
import {blankCard,validateCard} from '../card.mjs';
import {active} from '../conversation/contracts.mjs';
import {copy,check,digest,id} from '../revisions/contracts.mjs';

export const scopeRank = {session:0, role:1, card:2, player:3};
export const emptyBook = () => ({extensions:{},entries:[]});
export function validateDraft(title, book) {
  check(typeof title === 'string' && title.trim() && title.length <= 120,'BOOK_TITLE');
  const card = blankCard();
  card.spec = 'chara_card_v3';
  card.spec_version = '3.0';
  Object.assign(card.data,{group_only_greetings:[],character_book:book});
  try{validateCard(card);}catch(error){
    if(typeof error.code==='string'&&error.code.startsWith('CHARACTER_'))fail(error.code,error.path??'');
    throw error;
  }
  check(book && JSON.stringify(book).length <= 512000,'BOOK_SIZE');
}
export function pin(version) {
  return {bookId:version.bookId,bookVersionId:version.bookVersionId,hash:version.hash};
}
export function lookup(library, ref, allowArchived=true) {
  keys(ref,['bookId','bookVersionId','hash']);
  check(isId(ref.bookId)&&isId(ref.bookVersionId)&&Object.hasOwn(library,ref.bookId),'BOOK_VERSION_UNAVAILABLE');
  const book = library[ref.bookId], v = Object.hasOwn(book.versions,ref.bookVersionId)?book.versions[ref.bookVersionId]:null;
  check(v && (allowArchived || !book.archived),'BOOK_VERSION_UNAVAILABLE');
  const hash = digest({title:v.title,book:v.book});
  check(v.bookId===ref.bookId && v.hash===ref.hash && v.hash===hash &&
    v.bookVersionId===id('bv',v.bookId,v.number,hash) && ref.bookVersionId===v.bookVersionId,'BOOK_HASH');
  return v;
}
export function freezeGrants(library, grants, members, owner, previous=[]) {
  check(Array.isArray(grants)&&grants.length<=32,'BOOK_BINDING_LIMIT');
  return grants.map(g=>{
    keys(g,['ref','readers']);
    const allowed=members.filter(m=>m.kind==='character' && active(m)).map(m=>m.id);
    const readers=g.readers==='all'?allowed:g.readers;
    check(Array.isArray(readers)&&readers.every(r=>isId(r)&&allowed.includes(r)),'BOOK_READER');
    const existing=previous.some(b=>b.owner===owner && digest(b.ref)===digest(g.ref));
    lookup(library,g.ref,existing);
    return {owner,ref:copy(g.ref),readers:[...new Set(readers)]};
  }).filter(g=>g.readers.length);
}
// Resolve visibility before resolving any book, emitting diagnostics or checking conflicts.
export function resolve(library, bindings, member) {
  const rows=bindings.filter(b=>b.readers.includes(member)).sort((a,b)=>scopeRank[a.owner]-scopeRank[b.owner]);
  const unique=new Map(), seen=new Map();
  for(const b of rows) {
    const v=lookup(library,b.ref), k=b.owner+'/'+v.bookId;
    check(!seen.has(k)||seen.get(k)===v.bookVersionId,'BOOK_LAYER_CONFLICT');
    if(seen.has(k))continue;
    seen.set(k,v.bookVersionId);
    const source={owner:b.owner,...pin(v)};
    if(!unique.has(v.bookId))unique.set(v.bookId,{version:v,owner:b.owner,sources:[]});
    unique.get(v.bookId).sources.push(source);
  }
  return [...unique.values()];
}
