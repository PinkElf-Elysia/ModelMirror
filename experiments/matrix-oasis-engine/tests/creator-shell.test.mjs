import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const creatorRoot = path.join(moduleRoot, "apps", "creator-web");

async function read(relativePath) {
  return fs.readFile(path.join(creatorRoot, relativePath), "utf8");
}

test("Creator HTML retains R0/R2 identity and declares R3 parity", async () => {
  const html = await read("index.html");

  assert.match(html, /矩阵绿洲 · Matrix Oasis Engine/);
  assert.match(html, /MATRIX_OASIS_R0_ISOLATED_SHELL/);
  assert.match(html, /MATRIX_OASIS_R2_REFERENCE_SIMULATOR/);
  assert.match(html, /MATRIX_OASIS_R3_RUNTIME_PARITY/);
  assert.match(html, /lang="zh-CN"/);
});

test("Creator exposes R3 parity controls, observations, and explicit downloads", async () => {
  const source = await read(path.join("src", "App.tsx"));

  for (const expected of [
    "Creator 语义等价实验台",
    "加载本地 JSON",
    "重置当前会话",
    "可选操作",
    "会话状态",
    "最近 Transition",
    "本步 Cue",
    "编译前后锁步一致",
    "下载 Runtime Pack",
    "下载 Receipt",
    "只证明产物完整性",
    "父项目 API、网络或持久化服务",
  ]) {
    assert.match(source, new RegExp(expected));
  }
  assert.doesNotMatch(source, /R0 独立模块空壳/);
  assert.doesNotMatch(source, /Game Pack[\s\S]{0,80}未定义/);
});

test("Creator derives builtin names and descriptions from Pack inspection", async () => {
  const source = await read(path.join("src", "App.tsx"));

  assert.match(source, /BUILTIN_SESSIONS\[id\]\.inspection\.pack\.title/);
  assert.match(source, /BUILTIN_SESSIONS\[id\]\.inspection\.pack\.summary/);
  assert.equal((source.match(/last-train/g) ?? []).length, 1);
  assert.doesNotMatch(source, /末班地铁|node-carriage|ending-return|回声十三站/);
});

test("Creator replaces a local session only after the safe loader is ready", async () => {
  const source = await read(path.join("src", "App.tsx"));

  assert.match(source, /new LocalPackLoader\(\)/);
  assert.match(source, /loader\.loadCandidate\(file, baseSession\)/);
  assert.match(source, /result\.status === "stale"/);
  assert.match(source, /result\.status === "rejected"/);
  assert.match(source, /commitSession\(result\.candidate, baseSession\)/);
  assert.match(source, /当前会话未改变/);
  assert.match(source, /type="file"/);
  assert.match(source, /accept="\.json,application\/json"/);
});

test("Creator uses the public parity API for prepare, reset, and one-step actions", async () => {
  const [source, loader, transaction] = await Promise.all([
    read(path.join("src", "App.tsx")),
    read(path.join("src", "pack-loader.ts")),
    read(path.join("src", "session-transaction.ts")),
  ]);

  assert.match(loader, /prepareGamePackParityJson/);
  assert.match(loader, /createGamePackParitySession/);
  assert.match(source, /selectSessionCandidate/);
  assert.match(source, /activeSessionRef\.current !== baseSession/);
  assert.match(transaction, /applyGamePackParitySessionAction/);
  assert.match(transaction, /createGamePackParitySession/);
  assert.match(transaction, /artifact: baseSession\.artifact/);
  assert.match(transaction, /snapshot: applied\.snapshot/);
  assert.match(transaction, /inspection: applied\.inspection/);
  assert.match(transaction, /emittedCues: applied\.transition\.emittedCues/);
  assert.match(transaction, /PACK_PARITY_INTERNAL_ERROR/);
  assert.match(transaction, /catch \{/);
  assert.doesNotMatch(loader, /game-pack-simulator/);
  assert.doesNotMatch(loader, /runtime-pack-simulator/);
  assert.doesNotMatch(source, /inspectGamePackParitySession/);
});

test("Creator downloads only the current canonical artifact after an explicit click", async () => {
  const source = await read(path.join("src", "App.tsx"));

  assert.match(source, /new Blob\(\[text\]/);
  assert.match(source, /URL\.createObjectURL\(blob\)/);
  assert.match(source, /anchor\.download = fileName/);
  assert.match(source, /anchor\.click\(\)/);
  assert.match(source, /URL\.revokeObjectURL\(objectUrl\)/);
  assert.match(source, /runtimePackJson/);
  assert.match(source, /runtimePackReceiptJson/);
  assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB/);
  assert.doesNotMatch(source, /showSaveFilePicker|showOpenFilePicker/);
});

test("Creator keeps accessible status, focus, target, and mobile guards", async () => {
  const [source, styles] = await Promise.all([
    read(path.join("src", "App.tsx")),
    read(path.join("src", "styles.css")),
  ]);

  assert.match(source, /aria-live="polite"/);
  assert.match(source, /aria-atomic="true"/);
  assert.match(source, /aria-pressed=/);
  assert.match(source, /disabled=\{!action\.available\}/);
  assert.match(source, /locationHeadingRef\.current\?\.focus\(\)/);
  assert.match(source, /ref=\{locationHeadingRef\} tabIndex=\{-1\}/);
  assert.match(styles, /min-height:\s*44px/);
  assert.match(styles, /:focus-visible/);
  assert.match(styles, /overflow-x:\s*hidden/);
  assert.match(styles, /@media \(max-width:\s*640px\)/);
  assert.doesNotMatch(styles, /(?:linear|radial)-gradient/i);
  assert.doesNotMatch(styles, /backdrop-filter/i);
  assert.doesNotMatch(styles, /\banimation\s*:/i);
});

test("Creator config keeps build assets relative", async () => {
  const config = await read("vite.config.ts");
  assert.match(config, /base:\s*["']\.\/["']/);
});
