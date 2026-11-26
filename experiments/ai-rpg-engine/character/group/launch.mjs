import {RoundHost} from './round-host.mjs';
import {openPresentation,groupLookMembers} from '../presentation/store.mjs';
import {charxImports} from '../charx-import.mjs';
import {CharacterSources} from '../sources.mjs';
import {resolve,join} from 'node:path';import {readFile,mkdir} from 'node:fs/promises';
import {AuthoringStore} from '../../authoring/store.mjs';import {GroupStore} from './store.mjs';import {GroupHost,GroupGateway} from './host.mjs';import {ControlledProvider} from './gateway-transport.mjs';import {GroupFixtureProvider} from './fixture-provider.mjs';import {startGroupServer} from './server.mjs';
// No implicit real authorization. Controlled mode requires a separate local
// host configuration and service credential; neither is exposed by the page.
const directory=resolve(process.argv[2]??'C:/tmp/mm-cr2-data/preview'),port=Number(process.argv[3]??43122),recoverDeadOwner=process.argv.slice(4).includes('--recover-dead-owner');
await mkdir(directory,{recursive:true});const config=JSON.parse(await readFile(join(directory,'host.json'),'utf8'));
const works=await AuthoringStore.open(join(directory,'works'),{recoverDeadOwner}),store=await GroupStore.open({directory:join(directory,'groups'),works,models:config.models,authorization:config.authorization,recoverDeadOwner,narration:config.authorization.mode==='fixture'});await store.recover();
let provider=null,transport=null,host=null;
if(store.mode==='fixture')provider=await GroupFixtureProvider.open(join(directory,'fixture-provider'),{recoverDeadOwner});
if(store.mode==='controlled'){
 if(process.env.CHARACTER_GROUP_EXECUTION_ENABLED!=='true')throw Error('GROUP_EXECUTION_DISABLED');
 transport=await ControlledProvider.open({directory:join(directory,'transport'),origin:config.gatewayOrigin,serviceToken:process.env.CHARACTER_GROUP_SERVICE_TOKEN,recoverDeadOwner});provider=new GroupGateway(transport);
}
if(provider)host=new GroupHost(store,provider);
let archive;
const imports=charxImports({store:works,archive:()=>archive??=CharacterSources.open(join(directory,'character','charx-imports'),{recoverDeadOwner})});
const presentation=await openPresentation({directory:join(directory,'presentation'),works,imports,recoverDeadOwner,
  resolveMembers:(_kind,id)=>groupLookMembers(store,id)});
const rounds=store.mode==='fixture' ? await RoundHost.open({directory:join(directory,'rounds'),store,provider,recoverDeadOwner}) : null;
const app=await startGroupServer({store,works,host,port,presentation,rounds});console.log(JSON.stringify({url:app.origin+'/groups',mode:store.mode}));
for(const sig of ['SIGINT','SIGTERM'])process.once(sig,async()=>{await app.close();await rounds?.close();await presentation.close();if(archive)await (await archive).close();await store.close();await provider?.close?.();await transport?.close();await works.close();process.exit(0);});
