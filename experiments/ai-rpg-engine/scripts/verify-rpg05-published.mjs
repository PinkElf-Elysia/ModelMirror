import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';

// Publication checkout verification is separate from the unchanged initial-HEAD gate.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(root, '../..');
const base = '81fc14f6e0dada2447a63c1e532ee55b1f914ded';
const prefix = 'experiments/ai-rpg-engine/';
const snapshotPath = prefix + 'docs/RPG05_PUBLICATION.json';
const researchManifest = 'docs/ai-rpg-experiment/MANIFEST.json';
const excluded = new Set([snapshotPath, researchManifest]);
const args = process.argv.slice(2);
if (new Set(args).size !== args.length || args.some(a => !['--candidate', '--check-only'].includes(a))) throw Error('UNKNOWN_ARGUMENT');
const candidate = args.includes('--candidate');
const read = p => JSON.parse(fs.readFileSync(path.join(repo, p), 'utf8').replace(/^\uFEFF/u, ''));
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
function git(...args) {
  const r = spawnSync('git', args, { cwd: repo, encoding: 'utf8', windowsHide: true, maxBuffer: 32 * 1024 * 1024 });
  if (r.status !== 0) throw Error('PUBLICATION_GIT_FAILED:' + args[0]);
  return r.stdout;
}
const allowed = p => p.startsWith(prefix) || p.startsWith('docs/ai-rpg-experiment/') || ['server/main.py', 'server/tests/test_provider_chat_structured_output.py'].includes(p);
const unsafe = p => !allowed(p) || p.includes('\\') || p.split('/').some(s => !s || ['.', '..', '.git', 'node_modules', 'dist', 'coverage', 'logs', 'test-reports', 'storage', '.local'].includes(s) || s.startsWith('.env') || /^\.rpg\d+-work$/u.test(s));
const snapshot = read(snapshotPath), baseline = read(prefix + 'docs/RPG05_BASELINE.json');
if (snapshot.format !== 'modelmirror.ai-rpg.rpg05-publication/1' || snapshot.base !== base || baseline.base !== base || git('merge-base', base, 'HEAD').trim() !== base) throw Error('PUBLICATION_BASE_DRIFT');
if (snapshot.excludedPaths?.length !== 2 || snapshot.excludedPaths.some(p => !excluded.has(p))) throw Error('PUBLICATION_EXCLUSION_DRIFT');
const artifacts = new Map(snapshot.artifacts.map(f => [f.path, f]));
if (artifacts.size !== snapshot.artifacts.length) throw Error('PUBLICATION_DUPLICATE');
const checkoutDifferences = [];
for (const [p, entry] of artifacts) {
  if (unsafe(p) || !/^[a-f0-9]{40}$/u.test(entry.gitBlob) || !/^[a-f0-9]{64}$/u.test(entry.checkoutSha256) || !['100644', '100755'].includes(entry.gitMode)) throw Error('PUBLICATION_PATH_OR_HASH_INVALID');
  let cursor = path.join(repo, p);
  while (cursor !== repo) { if (fs.lstatSync(cursor).isSymbolicLink()) throw Error('PUBLICATION_SYMLINK'); cursor = path.dirname(cursor); }
  if (!fs.statSync(path.join(repo, p)).isFile()) throw Error('PUBLICATION_NOT_FILE');
  if (git('hash-object', '--path', p, '--', p).trim() !== entry.gitBlob) throw Error('PUBLICATION_BYTES_DRIFT:' + p);
  if (sha(fs.readFileSync(path.join(repo, p))) !== entry.checkoutSha256) checkoutDifferences.push(p);
}
const current = new Set(git('ls-files', '--cached', '--others', '--exclude-standard', '-z', '--', prefix, 'docs/ai-rpg-experiment/', 'server/main.py', 'server/tests/test_provider_chat_structured_output.py').split('\0').filter(p => p && !excluded.has(p)));
if (current.size !== artifacts.size || [...current].some(p => !artifacts.has(p))) throw Error('PUBLICATION_FILE_SET_DRIFT');
// A candidate run cannot attest to a commit. Published mode binds HEAD and index.
if (!candidate) {
  const headTree = new Map(git('ls-tree', '-r', '-z', 'HEAD', '--', prefix, 'docs/ai-rpg-experiment/', 'server/main.py', 'server/tests/test_provider_chat_structured_output.py').split('\0').filter(Boolean).map(line => {
    const [meta, p] = line.split('\t'), [mode, , blob] = meta.split(' '); return [p, {mode, blob}];
  }));
  const index = new Map(git('ls-files', '--stage', '-z').split('\0').filter(Boolean).map(line => {
    const [meta, p] = line.split('\t'), [mode, blob, stage] = meta.split(' ');
    if (stage !== '0') throw Error('PUBLICATION_UNMERGED_INDEX'); return [p, {mode, blob}];
  }));
  if (headTree.size !== artifacts.size + excluded.size) throw Error('PUBLICATION_HEAD_SET_DRIFT');
  for (const p of [...artifacts.keys(), ...excluded]) {
    const expected = artifacts.get(p), head = headTree.get(p), staged = index.get(p);
    if (!head || !staged || head.mode !== staged.mode || head.blob !== staged.blob || (expected && (head.mode !== expected.gitMode || head.blob !== expected.gitBlob)) || git('hash-object', '--path', p, '--', p).trim() !== head.blob) throw Error('PUBLICATION_HEAD_OR_INDEX_DRIFT:' + p);
  }
}
const frozen = new Map(baseline.files.map(f => [f.path, f]));
const mutable = new Set(baseline.mutable);
const baseEntries = git('ls-tree', '-r', '-z', base, '--', prefix, 'docs/ai-rpg-experiment/').split('\0').filter(Boolean);
if (baseEntries.length !== frozen.size) throw Error('FROZEN_SET_DRIFT');
for (const line of baseEntries) {
  const [meta, p] = line.split('\t'), blob = meta.split(' ')[2];
  if (frozen.get(p)?.blob !== blob || (!mutable.has(p) && artifacts.get(p)?.gitBlob !== blob)) throw Error('FROZEN_BLOB_DRIFT:' + p);
}
const headChanges = git('diff', '--name-only', '--no-renames', base, 'HEAD', '-z').split('\0').filter(Boolean);
const indexChanges = git('diff', '--cached', '--name-only', '--no-renames', 'HEAD', '-z').split('\0').filter(Boolean);
const workingChanges = git('diff', '--name-only', '--no-renames', '-z').split('\0').filter(Boolean);
const untracked = git('ls-files', '--others', '--exclude-standard', '-z').split('\0').filter(Boolean);
const changes = new Set([...headChanges, ...indexChanges, ...workingChanges, ...untracked]);
if (!candidate && (indexChanges.length || workingChanges.length || untracked.length)) throw Error('PUBLICATION_UNCOMMITTED_CHANGES');
for (const p of changes) if (unsafe(p) || (!excluded.has(p) && !artifacts.has(p))) throw Error('PUBLICATION_SCOPE_DRIFT:' + p);
const gate = { result: 'passed', mode:candidate ? 'candidate_working_tree' : 'committed_head', commitVerified:!candidate, base, head: git('rev-parse', 'HEAD').trim(), artifacts: artifacts.size, frozenBaselineFiles: frozen.size, changedFiles: changes.size, checkoutDifferences, snapshotSha256: sha(fs.readFileSync(path.join(repo, snapshotPath))), scope: 'Git delivery bytes and original frozen blobs; not private runtime freeze, prototype source files, Provider or manual acceptance' };
console.log(JSON.stringify(gate));
if (args.includes('--check-only')) process.exit(0);
fs.mkdirSync(path.join(root, '.rpg04-work'), {recursive:true});
const evidence = fs.mkdtempSync(path.join(root, '.rpg04-work', 'rpg05-published-'));
const groups = [], names = fs.readdirSync(path.join(root, 'tests'));
function save(result) {
  fs.writeFileSync(path.join(evidence, 'receipt.json'), JSON.stringify({format:'modelmirror.ai-rpg.rpg05-published-verification/1', generatedAt:new Date().toISOString(), result, gate, groups, providerCalls:0, real:'not_run', manual:'not_run', independent:'not_run', independentHttpHarness:'not_run', scope:'publication snapshot plus offline/mock/build; not full acceptance'}, null, 2) + '\n', {flag:'wx'});
  console.log('Receipt: ' + evidence);
}
function run(id, argv, cwd = root) {
  const r = spawnSync(process.execPath, argv, {cwd, encoding:'utf8', windowsHide:true, maxBuffer:32*1024*1024});
  const log = (r.stdout ?? '') + (r.stderr ?? '') + (r.error ? '\nPROCESS_START_FAILED' : '');
  fs.writeFileSync(path.join(evidence, id + '.log'), log, {flag:'wx'});
  const item = {id, command:['node', ...argv], result:r.status === 0 ? 'passed' : 'failed', tests:Number(/(?:ℹ |# )tests (\d+)/u.exec(log)?.[1] ?? 0), logSha256:sha(log)};
  groups.push(item); console.log(JSON.stringify(item));
  if (r.status !== 0) { save('failed'); process.exit(1); }
}
const legacy = names.filter(n => /^(contracts|content-.*|archive-.*|world-source|worker-capture|worker-batch|runtime-contracts|runtime-store|runtime-core|runtime-adapter|runtime-plugin-host|runtime-cli)\.test\.mjs$/u.test(n)).sort();
const rpg04 = names.filter(n => /^(context-.*|rpg04-.*)\.test\.mjs$/u.test(n) && n !== 'context-http-integration.test.mjs').sort();
if (legacy.length !== 19 || rpg04.length !== 17) throw Error('REGRESSION_FILE_SET_DRIFT');
run('old216', ['--test', ...legacy.map(n => 'tests/' + n)]);
run('rpg04NonHttp', ['--test', ...rpg04.map(n => 'tests/' + n)]);
run('uiHost', ['--test', ...names.filter(n => /^ui-host.*\.test\.mjs$/u.test(n)).sort().map(n => 'tests/' + n)]);
run('ui', ['--test', ...fs.readdirSync(path.join(root, 'ui/tests')).filter(n => n.endsWith('.test.mjs')).sort().map(n => 'ui/tests/' + n)]);
run('typecheck', ['ui/node_modules/typescript/bin/tsc', '--noEmit', '-p', 'ui/tsconfig.json']);
run('build', ['node_modules/vite/bin/vite.js', 'build'], path.join(root, 'ui'));
save('passed');
