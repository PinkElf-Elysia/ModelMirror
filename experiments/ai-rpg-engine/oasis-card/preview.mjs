import {fileURLToPath, pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {start} from '../studio/server.mjs';
import {ownLocalDirectory} from '../studio/local-owner.mjs';
import {installCustomConfig} from '../plugins/custom-config/host.mjs';
import {installOasis} from './integration.mjs';

const directory = fileURLToPath(new URL('../.rpg04-work/oasis-preview/', import.meta.url));
// Both standalone and story-library previews stay on loopback.
const origins = ['http://127.0.0.1:18995', 'http://127.0.0.1:18996'];
const {server} = await start({directory, port:18996, origins});
const origin = 'http://127.0.0.1:' + server.address().port;
installOasis(server, {directory, ownDirectory:ownLocalDirectory, origins});
installCustomConfig(server, origins, directory);
// The outer application supplies the same-origin iframe and story-library route.
const client = new URL('../../../client/', import.meta.url);
const requireClient = createRequire(new URL('package.json', client));
const {preview} = await import(pathToFileURL(requireClient.resolve('vite')).href);
try {
  await preview({root:fileURLToPath(client), configFile:false, preview:{
    host:'127.0.0.1', port:18995, strictPort:true, proxy:{'/rpg-app':origin},
  }});
  console.log(origins[0] + '/rpg/oasis');
} catch (error) {
  server.close();
  throw error;
}
