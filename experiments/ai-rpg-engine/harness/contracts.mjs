import {createHash} from 'node:crypto';
export const clone=structuredClone;
export const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
export const hash=v=>createHash('sha256').update(typeof v==='string'?v:canonical(v)).digest('hex');
export const fail=(code,status=409)=>Object.assign(new Error(code),{code,status});
export const id=v=>typeof v==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9-]{0,79}$/.test(v);
export const integer=v=>Number.isSafeInteger(v)&&v>=0;
export function exact(v,keys){return !!v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===[...keys].sort().join(',');}
export function requireValue(ok,code='SHOP_INPUT_INVALID',status=400){if(!ok)throw fail(code,status);}
export function money(v,currency){requireValue(integer(v)&&integer(currency?.scale)&&currency.scale>0);return (v/currency.scale).toFixed(currency.decimals)+currency.label;}
