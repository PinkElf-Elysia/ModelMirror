import {installArchiveCard} from '../studio/archive-card-mount.mjs';
import {createArchiveHost} from './session-host.mjs';
import {genshinAsset} from './assets.mjs';
import {fixedSystem} from './assembly.mjs';

export function genshinOptions(env) {
  return env.RPG_GENSHIN_CHAT_ENABLED === 'true' ? {enabled:true,limit:Number(env.RPG_GENSHIN_CHAT_CALL_LIMIT||0),baseURL:env.RPG_CONTROL_URL,serviceToken:env.RPG_S2S_TOKEN} : null;
}
export function installGenshin(server, options) {
  return installArchiveCard(server, {cardId:'genshin',name:'原神·提瓦特大陆',serveAsset:genshinAsset,createArchiveHost,systemPrompt:fixedSystem}, options);
}
