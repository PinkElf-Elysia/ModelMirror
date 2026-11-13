// Isolated, offline preview. No environment credentials or provider flags are read.
import {resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {start} from '../studio/server.mjs';
import {installCustomConfig} from '../plugins/custom-config/host.mjs';
const root=fileURLToPath(new URL('../',import.meta.url)),port=Number(process.argv[2]||18763);
if(!Number.isInteger(port)||port<1024||port>65535)throw Error('INVALID_PORT');
const profile=process.argv[3]||'legacy';
if(!['legacy','repair-v2'].includes(profile))throw Error('INVALID_PREVIEW_PROFILE');
const directory=resolve(root,'.rpg04-work/'+(profile==='repair-v2'?'harness-shop-preview-v2':'harness-shop-preview'));
const app=await start({port,directory,shop:{recoverDeadOwner:true},enabled:false,limit:0,origins:['http://127.0.0.1:'+port,'http://localhost:'+port]});
installCustomConfig(app.server,['http://127.0.0.1:'+port,'http://localhost:'+port],join(directory,'custom-config'));
console.log('Offline shop preview: http://127.0.0.1:'+port+'/rpg-app/earth/?session=shop-start');
async function close(){await app.shop.close();await new Promise(resolve=>app.server.close(resolve));await app.closed;process.exit(0);}
process.once('SIGINT',close);process.once('SIGTERM',close);
