import {join} from 'node:path';
import {BookWorkspace} from './store.mjs';
import {LegacySources} from '../revisions/sources.mjs';
import {keys,fail} from '../../authoring/contracts.mjs';

export function bookRoutes({workspace,store,directory,settings,recoverDeadOwner}){
  const groupDirectory=settings?.bookGroupDirectory??join(directory,'..','..','groups');
  const sources=new LegacySources({workspace,singleDirectory:join(directory,'sessions'),groupDirectory});
  let instance;
  const get=()=>(instance??=BookWorkspace.open({directory:join(directory,'independent-books'),works:store,profiles:workspace.store,sources,recoverDeadOwner}).catch(e=>{instance=null;throw e;}));
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
        case 'anchor':keys(args,['familyId','branchId','point']);return desk.anchor(args.familyId,args.branchId,args.point);
        case 'draft':keys(args,['bookId']);return desk.draft(args.bookId);
        case 'trial':keys(args,['title','book','input']);return desk.trial(args.title,args.book,args.input);
        case 'source':keys(args,['kind','sourceId']);return desk.sourceInfo(args.kind,args.sourceId);
        case 'view':keys(args,['familyId','branchId','targetId']);return desk.view(args.familyId,args.branchId,args.targetId);
        case 'preview':keys(args,['familyId','intent','operationId']);return desk.preview(args.familyId,args.intent,args.operationId);
        case 'recover':keys(args,['familyId','requestId']);return desk.recover(args.familyId,args.requestId);
        case 'inspect-open':case 'inspect-fork':return desk.command(action.slice(8),args,true);
        case 'save':case 'seal':case 'archive':case 'role-draft':case 'role':case 'profile':case 'open':case 'fork':case 'send':return desk.command(action,args);
        default:fail('CHARACTER_ROUTE_NOT_FOUND');
      }
    },
    async close(){if(instance)await (await instance).close();}
  };
}
