import {installArchiveCard} from '../studio/archive-card-mount.mjs';
import {createArchiveHost} from './session-host.mjs';
import {doupoAsset} from './host.mjs';
import {systemPrompt} from './assembly.mjs';

export function doupoOptions(env) {
  return env.RPG_DOUPO_CHAT_ENABLED === 'true' ? {enabled:true,limit:Number(env.RPG_DOUPO_CHAT_CALL_LIMIT||0),baseURL:env.RPG_CONTROL_URL,serviceToken:env.RPG_S2S_TOKEN} : null;
}
export function installDoupo(server, options) {
  return installArchiveCard(server, {cardId:'doupo',name:'斗破苍穹！',serveAsset:doupoAsset,createArchiveHost,systemPrompt:systemPrompt}, options);
}
