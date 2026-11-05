import {randomBytes} from 'node:crypto';
import {renderPanelTemplateBounded} from './variable-template.mjs';
import {compilePresentationBounded} from './presentation.mjs';
export async function prepareVariablePanel({config,snapshot,binding,origin,cache,assets,readAsset,status='initial',diagnostic=null}) {
  const html=await renderPanelTemplateBounded(config.template,snapshot.data),token=randomBytes(24).toString('hex');
  const input={mode:'reply',html,panel:true,origin,token,binding};
  const discovery=await compilePresentationBounded(input),resources=[];
  for(const alias of discovery.manifest.requestedAliases)if(assets.some(a=>a.alias===alias)){const value=await readAsset(alias);resources.push({...value.asset,bytes:value.bytes});}
  const compiled=await compilePresentationBounded({...input,resources});
  compiled.manifest.variablePanel={status,diagnostic,revision:snapshot.revision,dataHash:snapshot.dataHash,configurationHash:snapshot.configurationHash};
  return {...cache.put(compiled,token),frameUrl:'/rpg-app/api/authoring/presentations/frames/'+token,snapshot};
}
