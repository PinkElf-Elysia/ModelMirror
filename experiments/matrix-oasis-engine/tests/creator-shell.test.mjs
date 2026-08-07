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

test("Creator HTML exposes the stable R0 identity", async () => {
  const html = await read("index.html");

  assert.match(html, /矩阵绿洲 · Matrix Oasis Engine/);
  assert.match(html, /MATRIX_OASIS_R0_ISOLATED_SHELL/);
  assert.match(html, /lang="zh-CN"/);
});

test("Creator states describe only implemented R0 capability", async () => {
  const source = await read(path.join("src", "App.tsx"));

  for (const expected of [
    "R0 独立模块空壳",
    "父项目适配器",
    "未接入",
    "Game Pack",
    "未定义",
    "Godot Runtime",
    "未创建",
    "本页面仅验证独立工程边界，不代表引擎功能已完成",
  ]) {
    assert.match(source, new RegExp(expected));
  }
});

test("Creator config keeps build assets relative", async () => {
  const config = await read("vite.config.ts");
  assert.match(config, /base:\s*["']\.\/["']/);
});
