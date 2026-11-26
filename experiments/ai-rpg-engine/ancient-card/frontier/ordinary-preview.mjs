import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {start} from '../../studio/server.mjs';
import {installCustomConfig} from '../../plugins/custom-config/host.mjs';
export async function preview(directory,port,enabled,narrator){
  const app=await start({directory,host:'127.0.0.1',port,enabled:false,ancientOfflineAtlas:{enabled,narrator}});
  const origin='http://127.0.0.1:'+app.server.address().port;
  installCustomConfig(app.server,[origin],directory);
  return {origin,async close(){app.server.closeAllConnections();app.server.close();await app.closed;}};
}
if(process.argv[1] && pathToFileURL(resolve(process.argv[1])).href===import.meta.url){
  const [directory,port]=process.argv.slice(2);
  if(!directory||!port)throw Error('须指定独立数据目录和端口');
  const app=await preview(resolve(directory),Number(port),true);
  console.log(app.origin+'/rpg-app/ancient/chat/');
}
