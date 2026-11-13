import {sessionLifecycleHttp} from './session-lifecycle.mjs';
import {canonical,sha,fail} from '../plugins/catalog.mjs';
import {forkMessage,storyProgress} from './message-tools.mjs';

// Uses the same reviewed implementations and the selected card's own store/grants.
// Transcript streams are handled before the server's JSON body reader.
export async function archivePluginHttp({route,req,res,data,send,cardId,host,control,features,illustrations}) {
  const {store,plugins}=host;
  if(await sessionLifecycleHttp({route,method:req.method,data,store,send}))return true;
  const picture=route.match(/^api\/sessions\/([a-zA-Z0-9-]{1,80})\/illustrations\/([a-zA-Z0-9-]+)$/);
  if(picture&&features.illustrations){
    await illustrations({card:cardId,id:picture[1],action:picture[2],method:req.method,data,plugins,store,res,send});
    return true;
  }
  const tools=route.match(/^api\/sessions\/([a-zA-Z0-9-]{1,80})\/message-tools\/(fork|progress)$/);
  if(!tools||!features.messageTools)return false;
  const [,id,action]=tools;
  if(action==='fork'&&req.method==='POST'){
    send(200,await forkMessage(store,plugins,id,data));return true;
  }
  if(action==='progress'&&req.method==='GET'){
    const grant=await plugins.sessionStatus(id,'rpg.message-tools');
    if(!grant.enabled)throw fail('PLUGIN_NOT_AUTHORIZED',403);
    const query=new URL(req.url,'http://rpg').searchParams;
    const key=query.get('request'),child=query.get('fork');
    if(child&&(!key||(child!==store.archive.identity(id,key)&&child!=='rewrite-'+sha(canonical([id,key])))))
      throw fail('MESSAGE_FORK_BINDING_INVALID',403);
    send(200,await storyProgress(store,child||id,key,control,null));return true;
  }
  send(405,{error:'METHOD_NOT_ALLOWED'});return true;
}
