// verify-help-images.mjs
// 校验帮助中心截图资产与引用是否真实、合规、可维护。
//
// 运行方式（在 client/ 目录）：
//   node scripts/verify-help-images.mjs
//
// 规则来源：docs/help-center/README.md「截图规则」与 AGENTS.md §2.6。
// 只校验 index.ts 已注册的正式文章；未注册的草稿文章不进入公开链路，不在此检查。
// 任何 PENDING 占位、缺图、非 PNG、超限或公开目录残留资产都会导致失败退出（供 CI 使用）。

import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const clientRoot = resolve(__dirname, "..");
const indexFile = join(clientRoot, "src", "content", "help-center", "index.ts");
const articlesDir = join(clientRoot, "src", "content", "help-center", "articles");
const screenshotsRoot = join(clientRoot, "public", "help-center");

// --- 从 index.ts 提取已注册的文章 slug 列表（不 import .md，纯解析） ---
function readRegisteredSlugs() {
  const source = readFileSync(indexFile, "utf8");
  // 匹配 helpArticles 数组里的 slug 字段
  const slugs = [...source.matchAll(/slug:\s*"([^"]+)"/g)].map((m) => m[1]);
  return new Set(slugs);
}

// --- 读取已注册文章的 Markdown 正文，提取图片引用 ---
function readRegisteredArticles(registeredSlugs) {
  const files = readdirSync(articlesDir).filter((f) => f.endsWith(".md"));
  const articles = [];
  for (const file of files) {
    const slug = file.replace(/\.md$/, "");
    if (!registeredSlugs.has(slug)) continue; // 跳过未注册的草稿文章
    const content = readFileSync(join(articlesDir, file), "utf8");
    const images = [...content.matchAll(/!\[([^\]]*)\]\(([^)]+)\)/g)].map((m) => ({
      alt: m[1].trim(),
      src: m[2].trim(),
    }));
    articles.push({ file, content, images });
  }
  return articles;
}

// --- PNG 头与尺寸（不依赖第三方库） ---
function pngSize(buf) {
  if (buf.length < 24) return null;
  const width = buf.readUInt32BE(16);
  const height = buf.readUInt32BE(20);
  return { width, height };
}

let failures = 0;
function fail(msg) {
  failures += 1;
  console.error(`  ✗ ${msg}`);
}

const registeredSlugs = readRegisteredSlugs();
const articles = readRegisteredArticles(registeredSlugs);
const markdownFiles = readdirSync(articlesDir).filter((file) => file.endsWith(".md"));
const skippedDrafts = markdownFiles.filter((file) => !registeredSlugs.has(file.replace(/\.md$/, ""))).length;

console.log("验证帮助中心截图资产（仅已注册文章）…");
console.log(`已注册文章 ${articles.length} 篇；跳过未注册草稿 ${skippedDrafts} 篇\n`);

// 1. 每个引用路径指向真实存在、真实 PNG 的截图文件；PENDING/缺图失败
console.log("1) 引用路径 → 真实 PNG 文件");
for (const article of articles) {
  for (const img of article.images) {
    if (!img.src.startsWith("/help-center/")) {
      fail(`${article.file}: 截图路径必须以 /help-center/ 开头，得到 "${img.src}"`);
      continue;
    }
    if (img.src.includes("/PENDING/")) {
      fail(`${article.file}: 公开文章包含 PENDING 占位截图 ${img.src}，禁止合入。请完成真实预览后替换为真实基线。`);
      continue;
    }
    const rel = img.src.replace(/^\/help-center\//, "");
    const abs = join(screenshotsRoot, rel);
    if (!existsSync(abs)) {
      fail(`${article.file}: 截图文件不存在 ${img.src}`);
      continue;
    }
    const buf = readFileSync(abs);
    if (buf.slice(0, 8).toString("hex") !== "89504e470d0a1a0a") {
      fail(`${article.file}: 不是真实 PNG 文件 ${img.src}`);
    }
  }
}

// 2. 尺寸与体积
console.log("\n2) 尺寸 750–1000px、体积 ≤250KB");
for (const article of articles) {
  for (const img of article.images) {
    if (!img.src.startsWith("/help-center/") || img.src.includes("/PENDING/")) continue;
    const rel = img.src.replace(/^\/help-center\//, "");
    const abs = join(screenshotsRoot, rel);
    if (!existsSync(abs)) continue;
    const buf = readFileSync(abs);
    const size = pngSize(buf);
    const kb = statSync(abs).size / 1024;
    if (size) {
      if (size.width < 750 || size.width > 1000) {
        fail(`${article.file}: ${img.src} 宽 ${size.width}px 超出 750–1000px`);
      }
    }
    if (kb > 250) {
      fail(`${article.file}: ${img.src} 体积 ${kb.toFixed(0)}KB 超过 250KB`);
    }
  }
}

// 3. 公开截图目录只保存当前文章实际引用的资产；历史证据应归档到 docs/help-center/evidence/
console.log("\n3) 公开目录残留资产");
const referenced = new Set(
  articles.flatMap((a) =>
    a.images.filter((i) => i.src.startsWith("/help-center/")).map((i) => i.src.replace(/^\/help-center\//, "")),
  ),
);
for (const baseline of readdirSync(screenshotsRoot)) {
  const dir = join(screenshotsRoot, baseline);
  if (!existsSync(dir) || !statSync(dir).isDirectory()) continue;
  for (const file of readdirSync(dir)) {
    const rel = `${baseline}/${file}`;
    if (!referenced.has(rel)) {
      fail(`公开目录残留资产（未被任何已注册文章引用）: ${rel}`);
    }
  }
}

// 4. alt 非空且不是占位
console.log("\n4) 替代文本有效");
for (const article of articles) {
  for (const img of article.images) {
    if (!img.alt) fail(`${article.file}: 图片缺少替代文本`);
    else if (/^(图片|image|截图|screenshot|img)$/i.test(img.alt)) {
      fail(`${article.file}: 替代文本太笼统: "${img.alt}"`);
    }
  }
}

// 5. 截图路径基线归属：不允许 PENDING，必须是 8 位短提交哈希
console.log("\n5) 截图基线归属");
for (const article of articles) {
  if (!article.images.length) continue;
  const usedBaselines = [...new Set(article.images.map((i) => i.src.split("/")[2]))];
  for (const b of usedBaselines) {
    if (b === "PENDING") {
      fail(`${article.file}: 基线目录为 PENDING，禁止合入`);
    } else if (!/^[0-9a-f]{8}$/.test(b)) {
      fail(`${article.file}: 基线目录名不是 8 位短提交哈希: ${b}`);
    }
  }
}

console.log("");

if (failures > 0) {
  console.error(`❌ 截图资产校验失败：${failures} 处问题。`);
  process.exit(1);
}
console.log("✅ 全部已注册文章的截图资产合规。");
