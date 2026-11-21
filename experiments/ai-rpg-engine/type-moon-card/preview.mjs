import {tmpdir} from 'node:os';
import {mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {installReview} from './review.mjs';
import {start} from '../studio/server.mjs';
import {ownLocalDirectory} from '../studio/local-owner.mjs';
import {installCustomConfig} from '../plugins/custom-config/host.mjs';
import {installTypeMoon} from './integration.mjs';

const directory = await mkdtemp(join(tmpdir(), 'modelmirror-type-moon-preview-'));
const {server} = await start({directory, host: '127.0.0.1', port: Number(process.argv[2] || 0)});
const origin = 'http://127.0.0.1:' + server.address().port;
installTypeMoon(server, {directory, ownDirectory: process.platform === 'win32' ? ownLocalDirectory : undefined, origins: [origin]});
installCustomConfig(server, [origin], directory);
installReview(server, origin);
console.log(origin + '/rpg-app/type-moon/chat/');
console.log(origin + '/type-moon-review');
