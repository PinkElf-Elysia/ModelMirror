import {createHash,randomUUID} from 'node:crypto';
export const clone=structuredClone;
export function need(ok,code='CORE_INVALID'){if(!ok)throw Object.assign(Error(code),{code,status:400});}
export const id=x=>typeof x==='string'&&/^[a-zA-Z][a-zA-Z0-9-]{0,79}$/.test(x);
export const text=(x,max=200000)=>typeof x==='string'&&x.length<=max;
export const operationId=()=> 'op-'+randomUUID();
export function canonical(v){if(Array.isArray(v))return '['+v.map(canonical).join(',')+']';if(v&&typeof v==='object')return '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}';return JSON.stringify(v);}
export const hash=v=>createHash('sha256').update(typeof v==='string'?v:canonical(v)).digest('hex');
export function keys(v,allowed){need(v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).every(k=>allowed.includes(k)),'CORE_FIELDS');}
export function bounded(v,{bytes=256*1024,depth=16}={}){
 const visit=(x,n)=>{need(n<=depth,'CORE_DEPTH');if(x===null||typeof x==='boolean'||typeof x==='string')return;
 if(typeof x==='number'){need(Number.isFinite(x),'CORE_NUMBER');return;}
 need(x&&typeof x==='object'&&(Array.isArray(x)||Object.getPrototypeOf(x)===Object.prototype),'CORE_JSON');
 for(const [k,y] of Object.entries(x)){need(!['__proto__','constructor','prototype'].includes(k),'CORE_KEY');visit(y,n+1);}};
 visit(v,0);need(Buffer.byteLength(JSON.stringify(v))<=bytes,'CORE_CAPACITY');return v;
}
export function validateBinding(b){keys(b,['adapterId','adapterVersion','stateSchemaVersion','rulesVersion','sourceHash','effectiveContextHash','manifestHash']);
 need(id(b.adapterId)&&['adapterVersion','stateSchemaVersion','rulesVersion'].every(k=>text(b[k],100)&&b[k]),'CORE_BINDING');
 need(['sourceHash','effectiveContextHash','manifestHash'].every(k=>/^[a-f0-9]{64}$/.test(b[k])),'CORE_BINDING');return b;
}
export function validateAdapter(a){
 validateBinding(a.binding);need(a.id===a.binding.adapterId,'CORE_ADAPTER');
 for(const k of ['validateState','validateConfig','assemble','candidate','render','display','memory','initial','fixture'])need(typeof a[k]==='function','CORE_PORT_'+k);
 need(a.query?.tool?.type==='function'&&typeof a.query.validate==='function'&&typeof a.query.execute==='function','CORE_QUERY_PORT');
 need(a.ownership&&Object.keys(a.ownership).length,'CORE_OWNERSHIP');return a;
}
