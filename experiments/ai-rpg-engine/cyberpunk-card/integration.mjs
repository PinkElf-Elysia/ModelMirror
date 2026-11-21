import {installArchiveCard} from '../studio/archive-card-mount.mjs';
import {createArchiveHost} from './session-host.mjs';
import {cyberpunkAsset} from './assets.mjs';
import {fixedSystem} from './assembly.mjs';

export function cyberpunkOptions(env) {
  return env.RPG_CYBERPUNK_CHAT_ENABLED === 'true' ? {enabled:true,limit:Number(env.RPG_CYBERPUNK_CHAT_CALL_LIMIT||0),baseURL:env.RPG_CONTROL_URL,serviceToken:env.RPG_S2S_TOKEN} : null;
}
export function installCyberpunk(server, options) {
  return installArchiveCard(server, {cardId:'cyberpunk',name:'赛博朋克大世界',serveAsset:cyberpunkAsset,createArchiveHost,systemPrompt:fixedSystem}, options);
}
