/**
 * Skills（流程剧本）—— 给工作流步骤挂一套「怎么做」的方法论，注入该步的 system prompt。
 *
 * 内容直接用开源的 superpowers-zh（MIT，20 个 skill），不自己写。
 * 每个 skill = <skillsDir>/<name>/SKILL.md（frontmatter: name/description + 正文方法论）。
 *
 * skillsDir 解析优先级：AO_SKILLS_DIR > ./skills > ./superpowers-zh/skills >
 *   ../superpowers-zh/skills > node_modules/superpowers-zh/skills（cwd 与包自身）。
 * 与 angency-agents 角色库同理：可被 AO_SKILLS_DIR 覆盖成你自己的 skill 目录。
 */
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

export interface SkillDefinition {
  name: string;
  description: string;
  body: string;       // SKILL.md frontmatter 之后的方法论正文
}

let _cachedDir: string | null | undefined;

/** 解析 skills 目录（缓存）。找不到返回 null（skills 是可选增强，不报错）。 */
export function resolveSkillsDir(): string | null {
  if (_cachedDir !== undefined) return _cachedDir;
  const scriptDir = dirname(fileURLToPath(import.meta.url)); // dist/skills
  const candidates = [
    process.env.AO_SKILLS_DIR,
    './skills',
    './superpowers-zh/skills',
    '../superpowers-zh/skills',
    './node_modules/superpowers-zh/skills',
    join(scriptDir, '..', '..', 'node_modules', 'superpowers-zh', 'skills'),   // 包自身 node_modules
    join(scriptDir, '..', '..', '..', 'node_modules', 'superpowers-zh', 'skills'), // hoisted
    join(scriptDir, '..', '..', '..', 'superpowers-zh', 'skills'),             // sibling clone
  ].filter(Boolean) as string[];
  for (const c of candidates) {
    const full = resolve(c);
    if (existsSync(full)) { _cachedDir = full; return full; }
  }
  _cachedDir = null;
  return null;
}

/** 仅供测试：重置目录缓存。 */
export function _resetSkillsDirCache(): void { _cachedDir = undefined; }

function parseSkillFile(content: string, name: string): SkillDefinition {
  const m = content.match(/^---\s*\n([\s\S]*?)\n---\s*\n([\s\S]*)$/);
  if (!m) return { name, description: '', body: content.trim() };
  const fm: Record<string, string> = {};
  for (const line of m[1].split('\n')) {
    const i = line.indexOf(':');
    if (i > 0 && /^[a-zA-Z_]+$/.test(line.slice(0, i).trim())) {
      fm[line.slice(0, i).trim()] = line.slice(i + 1).trim().replace(/^["']|["']$/g, '');
    }
  }
  return { name: fm.name || name, description: fm.description || '', body: m[2].trim() };
}

/** 加载一个 skill；找不到返回 null（不抛错）。 */
export function loadSkill(name: string, dir = resolveSkillsDir()): SkillDefinition | null {
  if (!dir) return null;
  // 防路径穿越
  if (/[^a-zA-Z0-9_-]/.test(name)) return null;
  const file = join(dir, name, 'SKILL.md');
  if (!existsSync(file)) return null;
  try { return parseSkillFile(readFileSync(file, 'utf-8'), name); } catch { return null; }
}

/** 列出所有可用 skill。 */
export function listSkills(dir = resolveSkillsDir()): SkillDefinition[] {
  if (!dir || !existsSync(dir)) return [];
  const out: SkillDefinition[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry.startsWith('.')) continue;
    const skillFile = join(dir, entry, 'SKILL.md');
    try {
      if (statSync(join(dir, entry)).isDirectory() && existsSync(skillFile)) {
        out.push(parseSkillFile(readFileSync(skillFile, 'utf-8'), entry));
      }
    } catch { /* skip */ }
  }
  return out.sort((a, b) => a.name.localeCompare(b.name));
}

/** 把 step 的 skill / skills 字段归一成名字数组。 */
export function collectSkillNames(step: { skill?: string; skills?: string[] }): string[] {
  const out: string[] = [];
  if (step.skill) out.push(step.skill);
  if (Array.isArray(step.skills)) out.push(...step.skills);
  return [...new Set(out.filter(Boolean))];
}

/**
 * 把指定 skill 的方法论追加到 system prompt 末尾。找不到的 skill 跳过（返回 missing 名单）。
 * 不抛错——skills 是可选增强。
 */
export function injectSkills(systemPrompt: string, names: string[], dir = resolveSkillsDir()): { prompt: string; applied: string[]; missing: string[] } {
  const applied: string[] = [];
  const missing: string[] = [];
  const blocks: string[] = [];
  for (const n of names) {
    const sk = loadSkill(n, dir);
    if (!sk) { missing.push(n); continue; }
    applied.push(sk.name);
    blocks.push(`## 工作方法 / Skill：${sk.name}\n（完成本步骤时请严格遵循以下方法论）\n\n${sk.body}`);
  }
  if (!blocks.length) return { prompt: systemPrompt, applied, missing };
  const prompt = `${systemPrompt}\n\n---\n\n${blocks.join('\n\n---\n\n')}`;
  return { prompt, applied, missing };
}
