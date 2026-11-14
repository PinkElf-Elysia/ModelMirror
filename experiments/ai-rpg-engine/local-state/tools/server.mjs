import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {startServer} from '../server.mjs';
import {ToolsHost} from './host.mjs';
import {SCENARIOS} from './simulator.mjs';
export function startToolsServer({directory=fileURLToPath(new URL('../../.rpg03-work/local-state-tools-v2',import.meta.url)),port=18823,enabled=false}={}){
 return startServer({directory,port,enabled,hostFactory:options=>ToolsHost.open(options),scenarios:SCENARIOS});
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const app=await startToolsServer({enabled:process.argv.includes('--offline-preview')});
 console.log('Native tools OFFLINE preview: '+app.url);
 for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await app.close();process.exit(0);});
}
