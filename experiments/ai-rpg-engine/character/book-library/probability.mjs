import {digest} from '../revisions/contracts.mjs';

export const probabilityPolicy='stable-member-draw/1';
export const probabilityField='modelmirror.probability';
export const hasProbability=e=>Object.hasOwn(e.extensions??{},probabilityField);

// Rejection sampling maps the deterministic hash to 1..100 without modulo bias.
function percentile(key){
  for(let block=0;;block++){
    const hash=digest([probabilityPolicy,key,block]);
    for(let i=0;i<hash.length;i+=8){
      const word=parseInt(hash.slice(i,i+8),16);
      if(word<4294967200)return word%100+1;
    }
  }
}

export function probabilityGate(book,turn,scope){
  if(!book.entries.some(hasProbability))return undefined;
  return index=>{
    const entry=book.entries[index];
    if(!hasProbability(entry))return null;
    const percent=entry.extensions[probabilityField];
    if(!Number.isInteger(percent)||percent<0||percent>100)return {status:'invalid-probability'};
    const roll=percentile([turn,scope,index]);
    return {probability:{percent,roll},...(roll>percent?{status:'probability-miss'}:{})};
  };
}
