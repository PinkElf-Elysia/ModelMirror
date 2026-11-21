import { join } from 'node:path';
import {startLiveBase,portAt} from './application.mjs';
import {LiveDesk} from './desk.mjs';
import {startPage} from './page.mjs';
import { attachMemory } from '../manual-memory/attach.mjs';

// Trusted composition entry. The memory desk reuses the original workbench's budget owner.
export async function startLiveWorkspace(options) {
  const app = await startLiveBase(options);
  try {
    const control = app.context;
    const {id,calls,microUsd} = control.authorization;
    const memory = await attachMemory({
      directory:join(options.directory,'manual-memory'), previous:app.longContext, lower:app.longContext.sources,
      provider:control.revision, parameters:control.parameters, guard:control.guard, coordinator:app.revisions.coordinator,
      authorization:{id,mode:'controlled',calls,microUsd}, recoverDeadOwner:options.recoverDeadOwner,
      charactersUrl:app.studio.origin+'/characters', groupsUrl:app.workspace.groupUrl,
      revisionsUrl:app.longPage.origin+'/revisions', port:portAt(options.port,4),
      legacyHosts:[
        {obj:app.workspace.single, method:'send', source:id=>({id,kind:'single'})},
        {obj:app.groupHost, method:'send', source:id=>({id,kind:'group'})},
        {obj:app.revisions.host, method:'generate', source:async id=>(await app.revisions.store.read(id)).source}
      ]
    });
    app.longPage.linkMemory(memory.page.origin+'/revisions');
    let desk;
    try {
      desk=await LiveDesk.open({directory:join(options.directory,'live-memory'),memory:memory.store,context:control,
        proposalLimit:options.proposalLimit,recoverDeadOwner:options.recoverDeadOwner});
      const livePage=await startPage({desk,memory:memory.store,back:memory.page.origin+'/revisions',executionLabel:options.executionLabel,port:portAt(options.port,5)});
      memory.page.linkLive(livePage.origin+'/live');
      return {...app,memory,desk,livePage,async close(){await livePage.close();await desk.close();await memory.close();await app.close();}};
    } catch(e){await desk?.close();await memory.close();throw e;}
  } catch(error){await app.close();throw error;}
}
