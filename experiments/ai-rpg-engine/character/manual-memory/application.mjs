import { join } from 'node:path';
import { startContextWorkspace } from '../long-context/application.mjs';
import { attachMemory } from './attach.mjs';

// Trusted composition entry. The memory desk reuses the original workbench's budget owner.
export async function startMemoryWorkspace(options) {
  const app = await startContextWorkspace(options);
  try {
    const control = app.context;
    const {id,calls,microUsd} = control.authorization;
    const memory = await attachMemory({
      directory:join(options.directory,'manual-memory'), previous:app.longContext, lower:app.longContext.sources,
      provider:control.revision, parameters:control.parameters, guard:control.guard, coordinator:app.revisions.coordinator,
      authorization:{id,mode:'controlled',calls,microUsd}, recoverDeadOwner:options.recoverDeadOwner,
      charactersUrl:app.studio.origin+'/characters', groupsUrl:app.workspace.groupUrl,
      revisionsUrl:app.longPage.origin+'/revisions', port:(options.port??43280)+4,
      legacyHosts:[
        {obj:app.workspace.single, method:'send', source:id=>({id,kind:'single'})},
        {obj:app.groupHost, method:'send', source:id=>({id,kind:'group'})},
        {obj:app.revisions.host, method:'generate', source:async id=>(await app.revisions.store.read(id)).source}
      ]
    });
    app.longPage.linkMemory(memory.page.origin+'/revisions');
    return {...app,memory,async close(){await memory.close();await app.close();}};
  } catch(error){await app.close();throw error;}
}
