import {fileURLToPath} from 'node:url';
import {start} from '../studio/server.mjs';
import {ownLocalDirectory} from '../studio/local-owner.mjs';
import {installCustomConfig} from '../plugins/custom-config/host.mjs';
import {installGenshin} from './integration.mjs';

const directory = fileURLToPath(new URL('../.rpg04-work/genshin-preview/', import.meta.url));
// Both standalone and story-library previews stay on loopback.
const origins = ['http://127.0.0.1:18572'];
const {server} = await start({directory, port:18572, origins});
const origin = 'http://127.0.0.1:' + server.address().port;
installGenshin(server, {directory, ownDirectory:ownLocalDirectory, origins});
installCustomConfig(server, origins, directory);
console.log(origin + '/rpg-app/genshin/chat/');
