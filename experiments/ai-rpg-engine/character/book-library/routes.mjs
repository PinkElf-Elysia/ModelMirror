import {lstat} from 'node:fs/promises';
import {join} from 'node:path';
import {displayReceipt} from './display-receipt.mjs';
import {BookWorkspace} from './store.mjs';
import {LegacySources} from '../revisions/sources.mjs';
import {keys,fail} from '../../authoring/contracts.mjs';

export function bookRoutes({workspace,store,directory,settings,recoverDeadOwner}){
  const groupDirectory=settings?.bookGroupDirectory??join(directory,'..','..','groups');
  const sources=new LegacySources({workspace,singleDirectory:join(directory,'sessions'),groupDirectory});
  sources.templateGuard=async id=>{
    if(typeof workspace.groupRoundStatus!=='function')fail('REVISION_TEMPLATE_ROUND_UNKNOWN');
    const group=await workspace.group.read(id);
    if(group.notes?.length)fail('REVISION_TEMPLATE_NARRATION_UNSUPPORTED');
    if(!workspace.groupRoundsEnabled){
      try{await lstat(join(groupDirectory,'rounds','records',id+'.json'));fail('REVISION_TEMPLATE_ROUND_UNKNOWN');}
      catch(e){if(e.code!=='ENOENT')throw e;}
    }
    const round=await workspace.groupRoundStatus(id);
    if(round&&!['completed','stopped','failed','blocked'].includes(round.status))fail('REVISION_TEMPLATE_ROUND_PENDING');
  };
  let instance;
  const get=()=>(instance??=BookWorkspace.open({directory:join(directory,'independent-books'),works:store,profiles:workspace.store,sources,recoverDeadOwner,now:settings?.bookClock}).catch(e=>{instance=null;throw e;}));
  return {
    async publish(workId,input){return (await get()).publishSaved(workId,input);},
    async post(input){
      const desk=await get();
      keys(input,['action','args']);const {action,args}=input;
      switch(action){
        case 'catalog':{
          keys(args,[]);
          const roles=[];
          for(const item of await store.list())if((await store.read(item.id)).draft.kind==='character')roles.push({id:item.id,name:item.name,revision:item.revision});
          return {...await desk.list(),roles,profiles:(await workspace.store.profiles()).map(p=>({id:p.profileId,name:p.name,revision:p.profileRevision})),sessions:await sources.list()};
        }
        case 'recommendations':keys(args,['profileId','profileRevision']);return desk.recommendations(args.profileId,args.profileRevision);
        case 'template-options':keys(args,['familyId','branchId','point','memberId']);return desk.versionOptions(args.familyId,args.branchId,args.point,args.memberId);
        case 'compare-templates':keys(args,['workId','from','to']);return desk.serial(()=>desk.compare(args.workId,args.from,args.to));
        case 'anchor':keys(args,['familyId','branchId','point']);return desk.anchor(args.familyId,args.branchId,args.point);
        case 'draft':keys(args,['bookId']);return desk.draft(args.bookId);
        case 'trial':keys(args,['title','book','input',...(Object.hasOwn(args,'oncePolicy')?['oncePolicy']:[]),...(Object.hasOwn(args,'depthPolicy')?['depthPolicy']:[]),...(Object.hasOwn(args,'count')?['count']:[]),...(Object.hasOwn(args,'fallbackPolicy')?['fallbackPolicy']:[]),...(Object.hasOwn(args,'keepPolicy')?['keepPolicy']:[]),...(Object.hasOwn(args,'cooldownPolicy')?['cooldownPolicy']:[]),...(Object.hasOwn(args,'exclusivePolicy')?['exclusivePolicy']:[]),...(Object.hasOwn(args,'probabilityPolicy')?['probabilityPolicy']:[])]);return desk.trial(args.title,args.book,args.input,args.oncePolicy,args.depthPolicy,args.count,args.fallbackPolicy,args.keepPolicy,args.cooldownPolicy,args.probabilityPolicy,args.exclusivePolicy);
        case 'source':keys(args,['kind','sourceId']);return desk.sourceInfo(args.kind,args.sourceId);
        case 'display-receipt':return displayReceipt(desk,args);
        case 'view':keys(args,['familyId','branchId','targetId']);return desk.view(args.familyId,args.branchId,args.targetId);
        case 'preview':keys(args,['familyId','intent','operationId']);return desk.preview(args.familyId,args.intent,args.operationId);
        case 'recover':keys(args,['familyId','requestId']);return desk.recover(args.familyId,args.requestId);
        case 'inspect-open':case 'inspect-fork':return desk.command(action.slice(8),args,true);
        case 'save':case 'seal':case 'archive':case 'role-draft':case 'role':case 'profile':case 'open':case 'fork':case 'set-variable':case 'send':return desk.command(action,args);
        default:fail('CHARACTER_ROUTE_NOT_FOUND');
      }
    },
    async close(){if(instance)await (await instance).close();}
  };
}
