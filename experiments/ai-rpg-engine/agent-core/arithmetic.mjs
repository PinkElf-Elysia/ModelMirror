import {need} from './contracts.mjs';
// Reject insufficient inventory; clamp only when an explicit rule requests it.
export function integerDelta(before,delta,{min=0,max=Number.MAX_SAFE_INTEGER,mode='reject'}={}){
 need(before!==null&&before!==undefined,'STATE_INITIAL_VALUE_REQUIRED');
 need(Number.isSafeInteger(before)&&Number.isSafeInteger(delta)&&delta!==0,'STATE_DELTA_INTEGER');
 need(Number.isSafeInteger(min)&&Number.isSafeInteger(max)&&min<=max&&['reject','clamp'].includes(mode),'CORE_BOUNDS');
 const sum=before+delta;need(Number.isSafeInteger(sum),'STATE_NUMBER_OVERFLOW');
 if(mode==='reject'){need(sum>=min,'STATE_INSUFFICIENT');need(sum<=max,'STATE_BOUND_EXCEEDED');}
 const after=mode==='clamp'?Math.max(min,Math.min(max,sum)):sum;
 return {before,after,requestedDelta:delta,actualDelta:after-before};
}
