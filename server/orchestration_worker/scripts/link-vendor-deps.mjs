import { existsSync, lstatSync, symlinkSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dependencyRoot = resolve(packageRoot, 'node_modules');
const vendorDependencyRoot = resolve(
  packageRoot,
  '../vendor/agency-orchestrator/node_modules',
);

if (!existsSync(dependencyRoot)) {
  throw new Error('Run npm ci in server/orchestration_worker before linking vendor dependencies.');
}

if (existsSync(vendorDependencyRoot)) {
  const existing = lstatSync(vendorDependencyRoot);
  if (existing.isSymbolicLink()) {
    process.exit(0);
  }
  throw new Error(`Refusing to replace non-symlink path: ${vendorDependencyRoot}`);
}

symlinkSync(
  dependencyRoot,
  vendorDependencyRoot,
  process.platform === 'win32' ? 'junction' : 'dir',
);
