import {installArchiveCard} from '../studio/archive-card-mount.mjs';
import {createArchiveHost} from './session-host.mjs';
import {oasisAsset} from './host.mjs';
import {systemPrompt} from './assembly.mjs';

export function oasisOptions(env) {
  return env.RPG_OASIS_CHAT_ENABLED === 'true' ? {enabled:true,limit:Number(env.RPG_OASIS_CHAT_CALL_LIMIT||0),baseURL:env.RPG_CONTROL_URL,serviceToken:env.RPG_S2S_TOKEN} : null;
}
export function installOasis(server, options) {
  return installArchiveCard(server, {cardId:'oasis',name:'OASIS！头号玩家！（游戏人生）',serveAsset:oasisAsset,createArchiveHost,systemPrompt}, options);
}
