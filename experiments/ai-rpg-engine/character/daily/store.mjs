import {join} from 'node:path';
import {SessionRecords} from '../../authoring/session-storage.mjs';
import {keys,isId} from '../../authoring/contracts.mjs';
import {identity,check,digest} from './identity.mjs';
const clone=structuredClone;
export const keyOf=(kind,id)=>{check(['single','group'].includes(kind)&&new RegExp('^'+(kind==='single'?'session':'group')+'-[a-f0-9]{64}$').test(id),'SESSION_REFERENCE');return kind+':'+id;};
// Local owner metadata only. Never a model permission or a source of history.
export class DailyStore {
 #records;#queue=Promise.resolve();#ready=true;
 constructor(records){this.#records=records;}
 static async open(directory,{recoverDeadOwner=false,fault}={}){const r=await SessionRecords.open(join(directory,'daily'),{scope:'character-daily-v1',recoverDeadOwner,...(fault?{fault}:{})});try{await r.reconcile();return new DailyStore(r);}catch(e){await r.close();throw e;}}
 #serial(fn){const p=this.#queue.then(fn);this.#queue=p.catch(()=>{});return p;}
 async #load(){check(this.#ready,'RECOVERY_REQUIRED');const saved=await this.#records.get('workspace');const data=saved?.data??{format:'modelmirror.rpg.character-daily/1',profiles:{},metadata:{},operations:{},assets:{}};keys(data,['format','profiles','metadata','operations','assets']);check(data.format==='modelmirror.rpg.character-daily/1','FORMAT');validateStored(data);return {saved,data:clone(data)};}
 async #write(action,input,mutate){return this.#serial(async()=>{keys(input,['operationId','expectedRevision','payload']);check(isId(input.operationId),'OPERATION_INVALID');const {saved,data}=await this.#load(),hash=digest({action,...input}),prior=Object.hasOwn(data.operations,input.operationId)?data.operations[input.operationId]:null;if(prior){check(prior.hash===hash,'OPERATION_COLLISION');return clone(prior.result);}const result=await mutate(data);data.operations[input.operationId]={hash,result};check(Object.keys(data.operations).length<=10000,'CAPACITY');try{await this.#records.put('workspace',saved?.hash??null,data);}catch(e){this.#ready=false;throw e;}return clone(result);});}
 profiles(){return this.#serial(async()=>Object.values((await this.#load()).data.profiles).map(p=>clone(p.versions.at(-1))));}
 snapshot(id,revision){return this.#serial(async()=>{const p=(await this.#load()).data.profiles[id];check(p&&Number.isSafeInteger(revision)&&p.versions[revision],'PROFILE_NOT_FOUND');return clone(p.versions[revision]);});}
 saveProfile(input){return this.#write('profile',input,async d=>{const p=input.payload;keys(p,['id','name','description','avatar']);const id=p.id??'profile-'+digest({operationId:input.operationId});check(/^profile-[a-f0-9]{64}$/.test(id),'PROFILE_INVALID');const old=d.profiles[id];check(input.expectedRevision===(old?old.versions.length-1:null),'REVISION_CONFLICT');check(p.id===null?!old:!!old,'PROFILE_NOT_FOUND');const result=identity({format:'modelmirror.rpg.player-snapshot/1',profileId:id,profileRevision:old?.versions.length??0,name:p.name,description:p.description,avatar:p.avatar});check(p.avatar===null||d.assets[p.avatar],'AVATAR_NOT_FOUND');d.profiles[id]={versions:[...(old?.versions??[]),result]};check(Object.keys(d.profiles).length<=200,'CAPACITY');return result;});}
 metadata(kind,id){const key=keyOf(kind,id);return this.#serial(async()=>clone((await this.#load()).data.metadata[key]??{revision:null,title:'',archived:false,lastActivity:null,observedRevision:null}));}
 // Caller holds the trusted session coordinator and has checked pending state.
 manage(kind,id,input,pending){const key=keyOf(kind,id);return this.#write('metadata:'+key,input,d=>{keys(input.payload,['title','archived']);const old=d.metadata[key];check(input.expectedRevision===(old?.revision??null),'REVISION_CONFLICT');check(typeof input.payload.title==='string'&&input.payload.title.isWellFormed()&&input.payload.title.length<=120&&typeof input.payload.archived==='boolean','METADATA_INVALID');check(!input.payload.archived||!pending,'REQUEST_PENDING');const result={revision:(old?.revision??-1)+1,...input.payload,lastActivity:old?.lastActivity??null,observedRevision:old?.observedRevision??null};d.metadata[key]=result;return result;});}
 // Only locally observed operations establish activity time. Discovery is not activity.
 observe(kind,id,revision){const key=keyOf(kind,id);return this.#serial(async()=>{const {saved,data}=await this.#load(),old=data.metadata[key]??{revision:null,title:'',archived:false,lastActivity:null,observedRevision:null};if(old.observedRevision===revision)return;data.metadata[key]={...old,lastActivity:new Date().toISOString(),observedRevision:revision};try{await this.#records.put('workspace',saved?.hash??null,data);}catch(e){this.#ready=false;throw e;}});}
 putAvatar(input){return this.#write('avatar',input,d=>{keys(input.payload,['mediaType','base64']);check(input.expectedRevision===null,'REVISION_CONFLICT');const {mediaType,base64}=input.payload;check(['image/png','image/jpeg','image/webp'].includes(mediaType)&&typeof base64==='string'&&base64.length<=3*1024*1024&&/^[A-Za-z0-9+/]*={0,2}$/.test(base64),'AVATAR_INVALID');const bytes=Buffer.from(base64,'base64');check(bytes.toString('base64')===base64&&bytes.length<=2*1024*1024,'AVATAR_INVALID');validateAvatar(bytes,mediaType);const id=digest({mediaType,base64});d.assets[id]={mediaType,base64};check(Object.keys(d.assets).length<=200,'CAPACITY');return {id};});}
 avatar(id){check(/^[a-f0-9]{64}$/.test(id),'AVATAR_INVALID');return this.#serial(async()=>{const a=(await this.#load()).data.assets[id];check(a,'AVATAR_NOT_FOUND');return clone(a);});}
 async recover(){return this.#serial(async()=>{await this.#records.reconcile();this.#ready=true;});}
 async close(){await this.#queue;await this.#records.close();}
}
// The browser decodes/re-encodes uploads as PNG; host validates complete static PNG.
import {pngChunks} from '../png.mjs';
import {inflateSync} from 'node:zlib';
function validateAvatar(bytes,type){
 check(type==='image/png','AVATAR_CONVERT_TO_PNG');const chunks=pngChunks(bytes);const h=chunks.find(c=>c.type==='IHDR');check(h&&h.data.length===13,'AVATAR_INVALID');check(h.data.readUInt32BE(0)>0&&h.data.readUInt32BE(4)>0&&h.data.readUInt32BE(0)<=1024&&h.data.readUInt32BE(4)<=1024&&!chunks.some(c=>c.type==='acTL'),'AVATAR_DIMENSIONS');
 check(h.data[8]===8&&[2,6].includes(h.data[9])&&h.data[12]===0&&chunks.every(c=>['IHDR','IDAT','IEND','sRGB','gAMA','cHRM','pHYs'].includes(c.type)),'AVATAR_ENCODING');
 const row=h.data.readUInt32BE(0)*(h.data[9]===6?4:3)+1,expected=row*h.data.readUInt32BE(4);let decoded;try{decoded=inflateSync(Buffer.concat(chunks.filter(c=>c.type==='IDAT').map(c=>c.data)),{maxOutputLength:expected});}catch{check(false,'AVATAR_ENCODING');}check(decoded.length===expected,'AVATAR_ENCODING');for(let i=0;i<expected;i+=row)check(decoded[i]<=4,'AVATAR_ENCODING');
}

function validateStored(d){
 for(const field of ['profiles','metadata','operations','assets'])check(d[field]&&typeof d[field]==='object'&&!Array.isArray(d[field]),'STORE_CORRUPT');
 check(Object.keys(d.profiles).length<=200&&Object.keys(d.assets).length<=200&&Object.keys(d.operations).length<=10000,'CAPACITY');
 for(const [id,p] of Object.entries(d.profiles)){keys(p,['versions']);check(/^profile-[a-f0-9]{64}$/.test(id)&&Array.isArray(p.versions)&&p.versions.length>0,'STORE_CORRUPT');p.versions.forEach((v,i)=>{identity(v);check(v.profileId===id&&v.profileRevision===i&&(v.avatar===null||Object.hasOwn(d.assets,v.avatar)),'STORE_CORRUPT');});}
 for(const [key,m] of Object.entries(d.metadata)){const [kind,id]=key.split(':');check(keyOf(kind,id)===key,'STORE_CORRUPT');keys(m,['revision','title','archived','lastActivity','observedRevision']);check((m.revision===null||Number.isSafeInteger(m.revision)&&m.revision>=0)&&typeof m.title==='string'&&m.title.length<=120&&typeof m.archived==='boolean'&&(m.lastActivity===null||typeof m.lastActivity==='string'&&!Number.isNaN(Date.parse(m.lastActivity)))&&(m.observedRevision===null||Number.isSafeInteger(m.observedRevision)&&m.observedRevision>=0),'STORE_CORRUPT');}
 for(const [id,a] of Object.entries(d.assets)){keys(a,['mediaType','base64']);check(a.mediaType==='image/png'&&typeof a.base64==='string'&&id===digest(a),'STORE_CORRUPT');}
 for(const [id,o] of Object.entries(d.operations)){keys(o,['hash','result']);check(isId(id)&&/^[a-f0-9]{64}$/.test(o.hash),'STORE_CORRUPT');}
}
