import {DailyStore,keyOf} from './store.mjs';
import {identity,check} from './identity.mjs';
import {keys} from '../../authoring/contracts.mjs';
export class DailyWorkspace {
 #queue=Promise.resolve();single=null;group=null;works=null;singleUrl=null;groupUrl=null;
 constructor(store){this.store=store;}
 static async open(directory,options){return new DailyWorkspace(await DailyStore.open(directory,options));}
 run(fn){const p=this.#queue.then(fn);this.#queue=p.catch(()=>{});return p;}
 async resolvePlayer(p){
  if('profileId' in p){keys(p,['profileId','revision']);return this.store.snapshot(p.profileId,p.revision);}
  if('description' in p){keys(p,['name','description','avatar']);if(p.avatar!==null)await this.store.avatar(p.avatar);return identity({format:'modelmirror.rpg.player-snapshot/1',profileId:null,profileRevision:null,...p});}
  keys(p,['id','name']);return p;
 }
 async transcript(kind,id){
  keyOf(kind,id);const host=kind==='single'?this.single:this.group;check(host,'HOST_UNAVAILABLE');
  const s=kind==='single'?await host.read(id):await host.view(id);
  const roleName=kind==='single'&&this.works?(await this.works.verifyConfiguration(s.binding.reference.workId,s.binding.reference.versionId)).document.character.card.data.name:'角色';
  const messages=kind==='single'?s.messages.map((m,i)=>({id:'single-message-'+i,speaker:m.role==='user'?s.binding.player.name:roleName,content:m.content})):s.messages.map(m=>({id:'message-'+m.id,speaker:(s.members.find(x=>x.id===m.speakerId)?.name??'成员')+' · '+m.speakerId.slice(-6),content:m.content}));
  const pending=kind==='single'?Object.values(s.operations).some(o=>['prepared','dispatching','unknown'].includes(o.state)):s.requests.some(r=>['prepared','dispatching','unknown'].includes(r.status));
  return {id,kind,revision:s.revision,pending,messages,names:kind==='single'?[s.binding.player.name,roleName]:s.members.map(m=>m.name),scene:kind==='group'?s.scene:'',url:kind==='single'?this.singleUrl+'?work='+s.binding.reference.workId+'&version='+s.binding.reference.versionId+'&session='+id:this.groupUrl+'?id='+id};
 }
 async list({query='',kind='all',archived=false}={}){
  check(typeof query==='string'&&query.length<=200&&['all','single','group'].includes(kind)&&typeof archived==='boolean','QUERY_INVALID');
  const refs=[];if(this.single&&kind!=='group')for(const r of await this.single.list())refs.push({kind:'single',id:r.id,unavailable:r.unavailable});
  if(this.group&&kind!=='single'){try{for(const r of await this.group.list())refs.push({kind:'group',id:r.id});}catch{refs.push({kind:'group',unavailable:true});}}
  const rows=[];let unavailable=0;const q=query.trim().toLocaleLowerCase();
  for(const ref of refs){if(ref.unavailable){unavailable++;continue;}try{const v=await this.transcript(ref.kind,ref.id),meta=await this.store.metadata(ref.kind,ref.id);if(meta.archived!==archived)continue;const title=meta.title||v.scene.slice(0,40)||v.names.join('、');const hit=v.messages.find(m=>m.content.toLocaleLowerCase().includes(q));if(q&&![title,...v.names,v.scene].some(x=>x.toLocaleLowerCase().includes(q))&&!hit)continue;let snippet='';if(q&&hit){const i=hit.content.toLocaleLowerCase().indexOf(q);snippet=hit.content.slice(Math.max(0,i-35),i+q.length+65);}rows.push({id:v.id,kind:v.kind,title,names:v.names,pending:v.pending,count:v.messages.length,url:v.url,...meta,title:meta.title||title,match:snippet?{messageId:hit.id,snippet}:null});}catch{unavailable++;}}
  rows.sort((a,b)=>(b.lastActivity??'').localeCompare(a.lastActivity??'')||a.id.localeCompare(b.id));return {rows,unavailable};
 }
 async manage(kind,id,input){const v=await this.transcript(kind,id);return this.store.manage(kind,id,input,v.pending);}
 async export(kind,id,format){check(['txt','md'].includes(format),'EXPORT_FORMAT');const v=await this.transcript(kind,id),m=await this.store.metadata(kind,id);const title=m.title||'会话记录',lines=[title,'类型：'+(kind==='group'?'群聊':'单角色'),''];for(const msg of v.messages)lines.push((format==='md'?'### ':'')+msg.speaker,'',msg.content,'');return {name:kind+'-'+id.slice(-8)+'.'+format,text:lines.join('\n')};}
 async api(route,input,url){
  if(route==='/profiles')return input?this.store.saveProfile(input):this.store.profiles();
  if(route==='/avatars')return this.store.putAvatar(input);
  if(route==='/sessions'&&!input)return this.list({query:url.searchParams.get('q')??'',kind:url.searchParams.get('kind')??'all',archived:url.searchParams.get('archived')==='true'});
  const match=route.match(/^\/sessions\/(single|group)\/((?:session|group)-[a-f0-9]{64})(?:\/(manage|export))?$/);check(match,'ROUTE_NOT_FOUND');
  const [,kind,id,action]=match;if(action==='manage'&&input)return this.manage(kind,id,input);if(action==='export'&&!input)return this.export(kind,id,url.searchParams.get('format')??'txt');if(!action&&!input)return this.transcript(kind,id);check(false,'ROUTE_NOT_FOUND');
 }
 async close(){await this.#queue;await this.store.close();}
}
