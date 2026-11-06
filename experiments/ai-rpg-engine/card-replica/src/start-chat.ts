import {api,cardStoragePrefix,emptyClientState,writeClientState} from './session-client';

// Creating a chat never sends the archive or invokes a model. Only Send moves
// the player's current, editable draft into a persisted story request.
export async function createChatDraft(draft='',params?:Record<string,number>){
 if(typeof draft!=='string'||draft.length>20000)throw Error('档案超过 20000 字，请缩短后再带入聊天，或先复制保存。');
 const card=cardStoragePrefix();
 let configuration:unknown={opening:''};
 if(card==='earth'||card==='god'){
  const status=await api('status');
  const mode=status.providerEnabled===true?'real':'offline';
  configuration=card==='earth'?{characterText:'',world:'表世界',params:params||{},mode}:{setup:null,mode};
 }
 const session=await api('sessions',configuration);
 try{writeClientState(session.id,{...emptyClientState(),draft});}
 catch{throw Error('会话已创建，但草稿未能写入浏览器。请复制档案保留内容，再从会话历史打开。');}
 return session.id as string;
}
