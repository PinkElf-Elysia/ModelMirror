// verify-help-images.mjs
// 校验帮助中心截图资产与引用是否真实、合规、可维护。
//
// 运行方式（在 client/ 目录）：
//   node scripts/verify-help-images.mjs
//
// 规则来源：docs/help-center/README.md「截图规则」与 AGENTS.md §2.6。
// 用 Markdown 正文与截图资产本身作为事实来源，不依赖已编译产物。

import assert from "node:assert/strict";
import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const clientRoot = resolve(__dirname, "..");
const articlesDir = join(clientRoot, "src", "content", "help-center", "articles");
const screenshotsRoot = join(clientRoot, "public", "help-center");

// --- 读取 Markdown 正文，提取图片引用与标题 ---
function readArticles() {
  const files = readdirSync(articlesDir).filter((f) => f.endsWith(".md"));
  return files.map((file) => {
    const content = readFileSync(join(articlesDir, file), "utf8");
    const images = [...content.matchAll(/!\[([^\]]*)\]\(([^)]+)\)/g)].map((m) => ({
      alt: m[1].trim(),
      src: m[2].trim(),
    }));
    return { file, content, images };
  });
}

// --- PNG 头与尺寸（不依赖第三方库） ---
function pngSize(buf) {
  // PNG 签名后是 IHDR chunk: 8 字节签名 + 4 长度 + 4 类型 + 宽(4) + 高(4)
  if (buf.length < 24) return null;
  const width = buf.readUInt32BE(16);
  const height = buf.readUInt32BE(20);
  return { width, height };
}

let failures = 0;
let warnings = 0;
function fail(msg) {
  failures += 1;
  console.error(`  ✗ ${msg}`);
}
function warn(msg) {
  warnings += 1;
  console.warn(`  ⚠ ${msg}`);
}

console.log("验证帮助中心截图资产…\n");
const articles = readArticles();

// 1. 每个引用路径指向真实存在、真实 PNG 的截图文件
console.log("1) 引用路径 → 真实 PNG 文件");
for (const article of articles) {
  for (const img of article.images) {
    if (!img.src.startsWith("/help-center/")) {
      fail(`${article.file}: 截图路径必须以 /help-center/ 开头，得到 "${img.src}"`);
      continue;
    }
    const rel = img.src.replace(/^\/help-center\//, "");
    const abs = join(screenshotsRoot, rel);
    if (img.src.includes("/PENDING/")) {
      // 待预览验证的草稿占位：合入正式版前必须替换为真实基线截图。
      warn(`${article.file}: 待预览验证的截图占位 ${img.src}（合入前必须替换为真实基线）`);
      continue;
    }
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
    if (!img.src.startsWith("/help-center/")) continue;
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

// 3. 截图只被引用、不出现孤儿资产（未被任何文章引用且非证据目录）
console.log("\n3) 未被引用的孤儿截图");
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
      fail(`孤儿截图（未被任何文章引用）: ${rel}`);
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

// 5. 截图路径基线覆盖一致性：每篇的 verifiedCommit 必须覆盖其引用的所有基线目录
console.log("\n5) 截图基线归属");
for (const article of articles) {
  if (!article.images.length) continue;
  // 从 index.ts 读取 verifiedCommit 更可靠，这里通过引用路径前缀推断
  const usedBaselines = [...new Set(article.images.map((i) => i.src.split("/")[2]))];
  for (const b of usedBaselines) {
    if (b === "PENDING") {
      warn(`${article.file}: 基线目录为 PENDING（待预览验证）`);
    } else if (!/^[0-9a-f]{8}$/.test(b)) {
      fail(`${article.file}: 基线目录名不是 8 位短提交哈希: ${b}`);
    }
  }
}

console.log("");

if (warnings > 0) {
  console.warn(`⚠ ${warnings} 处待预览验证占位（PENDING），合入正式版前必须替换为真实基线截图。`);
}
if (failures > 0) {
  console.error(`❌ 截图资产校验失败：${failures} 处问题。`);
  process.exit(1);
}
console.log("✅ 截图资产校验通过（PENDING 占位需在合入前替换）。");
