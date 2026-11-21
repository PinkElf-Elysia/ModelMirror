import {fileURLToPath} from 'node:url';
import {start} from '../studio/server.mjs';
import {ownLocalDirectory} from '../studio/local-owner.mjs';
import {installCustomConfig} from '../plugins/custom-config/host.mjs';
import {installNaruto} from './integration.mjs';

const directory = fileURLToPath(new URL('../.rpg04-work/naruto-preview/', import.meta.url));
const {server} = await start({directory, port:18979});
const origin = 'http://127.0.0.1:' + server.address().port;
installNaruto(server, {directory, ownDirectory:ownLocalDirectory, origins:[origin]});
installCustomConfig(server, [origin], directory);
console.log(origin + '/rpg-app/naruto/chat/');
