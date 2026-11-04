import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {start} from '../studio/server.mjs';

test('studio serves both card routes and assembles the ancient opening offline', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'ancient-routes-'));
  const {server, plugins} = await start({port: 0, directory});
  t.after(async () => {
    await new Promise(resolve => server.close(resolve));
    await plugins.close();
    await rm(directory, {recursive: true, force: true});
  });
  const origin = 'http://127.0.0.1:' + server.address().port;
  const api = path => origin + '/rpg-app/' + path;
  const {cards} = await (await fetch(api('api/status'))).json();
  assert.equal(cards.filter(card => card.id === 'cultivation').length, 1);
  assert.equal(cards.filter(card => card.id === 'ancient').length, 1);
  const page = await fetch(api('ancient/'));
  assert.equal(page.status, 200);
  assert.match(page.headers.get('content-security-policy'), /sandbox allow-scripts;/);
  assert.match(await page.text(), /命运卷轴/);
  assert.equal((await fetch(api('ancient/landscape.jpg'))).headers.get('content-type'), 'image/jpeg');
  assert.equal((await fetch(api('ancient/system.txt'))).status, 404);
  const input = '  一份命书  ';
  const post = value => fetch(api('ancient/api/opening'), {
    method: 'POST', headers: {Origin: origin, 'Content-Type': 'application/json'},
    body: JSON.stringify({input: value}),
  });
  assert.equal((await post('  ')).status, 400);
  const response = await post(input);
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.providerEnabled, false);
  assert.equal(result.openingHash, createHash('sha256').update(input).digest('hex'));
});
