import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
if (process.argv.length > 2) throw Error('RPG05_VERIFY_NO_ARGUMENTS');
const evidence = path.join(root, '.rpg04-work', 'rpg05-verify-' + Date.now()); fs.mkdirSync(evidence, { recursive: true });
const groups = [], files = fs.readdirSync(path.join(root, 'tests'));
function run(id, args, cwd = root) {
  const result = spawnSync(process.execPath, args, { cwd, encoding: 'utf8', windowsHide: true, maxBuffer: 32 * 1024 * 1024 });
  const log = (result.stdout ?? '') + (result.stderr ?? '') + (result.error ? '\nPROCESS_START_FAILED' : '');
  const file = path.join(evidence, id + '.log'); fs.writeFileSync(file, log);
  const record = { id, command: ['node', ...args], result: result.status === 0 ? 'passed' : 'failed', log: path.relative(root, file).replaceAll('\\', '/'), logSha256: createHash('sha256').update(log).digest('hex'), tests: Number(/(?:ℹ |# )tests (\d+)/u.exec(log)?.[1] ?? 0) };
  groups.push(record); console.log(JSON.stringify(record));
  if (result.status !== 0) { save('failed'); process.exit(1); }
}
function save(status) {
  const receipt = { format: 'modelmirror.ai-rpg.rpg05-offline-verification', status, generatedAt: new Date().toISOString(), groups, scope: 'offline tests and build only; not full RPG05 acceptance', real: 'not_run', manual: 'not_run', independent: 'not_run', independentHttpHarness: 'not_run', frozenRpg04Aggregate: 'not_run: historical branch/private artifact gate retained' };
  fs.writeFileSync(path.join(evidence, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n'); console.log('Receipt: ' + path.join(evidence, 'receipt.json'));
}
run('boundary', ['scripts/check-boundary-rpg05.mjs']);
const legacy = files.filter(name => /^(contracts|content-.*|archive-.*|world-source|worker-capture|worker-batch|runtime-contracts|runtime-store|runtime-core|runtime-adapter|runtime-plugin-host|runtime-cli)\.test\.mjs$/u.test(name)).sort();
const current = files.filter(name => /^(context-.*|rpg04-.*)\.test\.mjs$/u.test(name) && name !== 'context-http-integration.test.mjs').sort();
if (legacy.length !== 19 || current.length !== 17) throw Error('REGRESSION_FILE_SET_DRIFT');
run('old216', ['--test', ...legacy.map(name => 'tests/' + name)]);
run('rpg04NonHttp', ['--test', ...current.map(name => 'tests/' + name)]);
run('uiHost', ['--test', ...files.filter(name => /^ui-host.*\.test\.mjs$/u.test(name)).sort().map(name => 'tests/' + name)]);
run('ui', ['--test', ...fs.readdirSync(path.join(root, 'ui/tests')).filter(name => name.endsWith('.test.mjs')).sort().map(name => 'ui/tests/' + name)]);
run('typecheck', ['ui/node_modules/typescript/bin/tsc', '--noEmit', '-p', 'ui/tsconfig.json']);
run('build', ['node_modules/vite/bin/vite.js', 'build'], path.join(root, 'ui'));
save('passed');
