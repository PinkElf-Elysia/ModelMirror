import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

interface ManifestFile {
  upstream_path: string;
  local_path: string;
  blob_sha: string;
  sha256: string;
}

interface Manifest {
  revision: string;
  license: string;
  files: ManifestFile[];
}

const here = dirname(fileURLToPath(import.meta.url));
const vendorRoot = resolve(here, '../../vendor/agency-orchestrator');
const manifest = JSON.parse(
  readFileSync(join(vendorRoot, 'UPSTREAM_FILES.json'), 'utf8'),
) as Manifest;

function gitBlobSha(content: Buffer): string {
  const header = Buffer.from(`blob ${content.length}\0`, 'utf8');
  return createHash('sha1').update(header).update(content).digest('hex');
}

test('pins the approved upstream revision and Apache-2.0 license', () => {
  const revision = readFileSync(join(vendorRoot, 'UPSTREAM_REVISION'), 'utf8').trim();
  assert.equal(revision, '3b7c43042325a9091393de6ecfa7e9936b0c7932');
  assert.equal(manifest.revision, revision);
  assert.equal(manifest.license, 'Apache-2.0');
  assert.match(readFileSync(join(vendorRoot, 'LICENSE'), 'utf8'), /Apache License/);
});

test('verifies every copied file against its upstream Blob SHA and SHA-256', () => {
  assert.ok(manifest.files.length >= 20);
  for (const entry of manifest.files) {
    const content = readFileSync(join(vendorRoot, entry.local_path));
    assert.equal(gitBlobSha(content), entry.blob_sha, entry.upstream_path);
    assert.equal(createHash('sha256').update(content).digest('hex'), entry.sha256, entry.upstream_path);
  }
});

test('does not vendor provider, website, Electron, role or creative trees', () => {
  for (const relativePath of [
    'src/connectors/claude.ts',
    'src/connectors/openai-compatible.ts',
    'website',
    'web',
    'electron',
    'agency-agents',
    'creative',
    'integrations',
  ]) {
    assert.equal(existsSync(join(vendorRoot, relativePath)), false, relativePath);
  }
});
