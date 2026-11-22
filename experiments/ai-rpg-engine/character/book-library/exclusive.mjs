export const exclusiveField='modelmirror.exclusiveGroup';
export const exclusivePolicy='book-group-priority/1';
export const hasExclusive=entry=>Object.hasOwn(entry.extensions??{},exclusiveField);

export function groupName(value){
  if(typeof value!=='string'||value.length>64||/[\u0000-\u001f\u007f]/u.test(value))return null;
  return value.trim();
}

// One selector invocation owns the winners across its recursion passes.
export function exclusiveResolver(book){
  if(!book.entries.some(hasExclusive))return undefined;
  const winners=new Map();
  return (indices,rows)=>{
    const groups=new Map();
    for(const index of indices){
      const entry=book.entries[index];
      if(!hasExclusive(entry))continue;
      const name=groupName(entry.extensions[exclusiveField]);
      if(name===null){rows[index].status='invalid-exclusive';continue;}
      if(!name)continue;
      if(!groups.has(name))groups.set(name,[]);
      groups.get(name).push(index);
    }
    for(const [name,matches] of groups){
      if(!winners.has(name)){
        const priority=i=>book.entries[i].priority??0;
        winners.set(name,matches.reduce((best,i)=>priority(i)>priority(best)?i:best));
      }
      const winner=winners.get(name);
      for(const index of matches){
        rows[index].exclusive={name,winner};
        if(index!==winner)rows[index].status='exclusive-suppressed';
      }
    }
    return indices.filter(i=>!['exclusive-suppressed','invalid-exclusive'].includes(rows[i].status));
  };
}
