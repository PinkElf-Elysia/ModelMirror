import {fileURLToPath} from 'node:url';
import {start} from '../studio/server.mjs';
import {ownLocalDirectory} from '../studio/local-owner.mjs';
import {installCustomConfig} from '../plugins/custom-config/host.mjs';
import {installModernLife} from './integration.mjs';

const directory = fileURLToPath(new URL('../.rpg04-work/modern-life-preview/', import.meta.url));
// Both standalone and story-library previews stay on loopback.
const origins = ['http://127.0.0.1:18986', 'http://127.0.0.1:18987'];
const {server} = await start({directory, port:18986, origins});
const origin = 'http://127.0.0.1:' + server.address().port;
installModernLife(server, {directory, ownDirectory:ownLocalDirectory, origins});
installCustomConfig(server, origins, directory);
console.log(origin + '/rpg-app/modern-life/chat/');
