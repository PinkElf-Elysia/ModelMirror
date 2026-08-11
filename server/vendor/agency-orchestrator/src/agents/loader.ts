/**
 * 加载 agency-agents 的 .md 文件，提取角色定义
 *
 * 文件格式:
 * ---
 * name: 角色名
 * description: 描述
 * emoji: 🔧
 * ---
 * # 角色标题
 * ...system prompt 内容...
 */
import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { join, resolve, relative, sep } from 'node:path';
import { homedir } from 'node:os';
import type { AgentDefinition } from '../types.js';

// 枚举角色时跳过的非角色目录
const SKIP_DIRS = new Set(['node_modules', 'scripts', 'integrations', 'examples']);

/**
 * 用户自建角色的固定分类前缀：`my/<id>` 一律解析到用户角色目录。
 * 与 AO_AGENTS_DIR（整体替换内置库）不同，用户角色目录是「叠加」——
 * 内置 267 个角色照常可用，自己攒的专家放 ~/.ao/roles，CLI 与 Studio 共用。
 */
export const USER_ROLE_CATEGORY = 'my';

/** 用户角色目录：AO_USER_ROLES_DIR 可覆盖，默认 ~/.ao/roles（与 ~/.ao/teams、~/.ao/prompts 同体系）。 */
export function userRolesDir(): string {
  return process.env.AO_USER_ROLES_DIR
    ? resolve(process.env.AO_USER_ROLES_DIR)
    : join(homedir(), '.ao', 'roles');
}

/** 一个 .md 是否是「角色」：必须有带 name 的 frontmatter（排除 README / 攻略 / 模板等文档）。 */
function isAgentFile(fullPath: string): boolean {
  try {
    const m = readFileSync(fullPath, 'utf-8').match(/^---\s*\n([\s\S]*?)\n---/);
    return !!m && /^\s*name\s*:/m.test(m[1]);
  } catch {
    return false;
  }
}

/** 递归收集所有角色路径（如 "engineering/x"、"game-development/unity/unity-architect"）。 */
function collectRolePaths(baseDir: string): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      if (e.name.startsWith('.') || SKIP_DIRS.has(e.name)) continue;
      const full = join(dir, e.name);
      if (e.isDirectory()) walk(full);
      else if (e.isFile() && e.name.endsWith('.md') && isAgentFile(full)) {
        out.push(relative(baseDir, full).split(sep).join('/').replace(/\.md$/, ''));
      }
    }
  };
  // 只从顶层「部门」子目录往下走，忽略根目录散落的 .md（README 等）
  for (const dept of readdirSync(baseDir, { withFileTypes: true })) {
    if (!dept.isDirectory() || dept.name.startsWith('.') || SKIP_DIRS.has(dept.name)) continue;
    walk(join(baseDir, dept.name));
  }
  return out;
}

/**
 * 加载指定角色的定义
 * @param agentsDir agency-agents 的 agents 目录路径
 * @param rolePath 角色路径，如 "engineering/engineering-sre"
 */
export function loadAgent(agentsDir: string, rolePath: string): AgentDefinition {
  // 防止路径穿越攻击（如 ../../etc/passwd）
  if (/\.\.[/\\]/.test(rolePath) || /[^a-zA-Z0-9_\-/]/.test(rolePath)) {
    throw new Error(`非法角色路径: ${rolePath}\n角色路径只能包含字母、数字、下划线、连字符和斜杠`);
  }

  const fullPath = resolve(agentsDir, `${rolePath}.md`);
  const resolvedDir = resolve(agentsDir);
  if (!fullPath.startsWith(resolvedDir)) {
    throw new Error(`角色路径越界: ${rolePath}`);
  }

  if (!existsSync(fullPath)) {
    // `my/<id>` 兜底到用户角色目录（agents_dir 里真有 my/ 子目录时以 agents_dir 优先）
    if (rolePath.startsWith(`${USER_ROLE_CATEGORY}/`)) {
      const userDir = userRolesDir();
      const userPath = resolve(userDir, `${rolePath.slice(USER_ROLE_CATEGORY.length + 1)}.md`);
      if (userPath.startsWith(userDir + sep) && existsSync(userPath)) {
        return parseAgentFile(readFileSync(userPath, 'utf-8'), rolePath);
      }
    }
    throw new Error(`角色文件不存在: ${fullPath}\n请确认 agents_dir 和 role 路径正确`);
  }

  const content = readFileSync(fullPath, 'utf-8');
  return parseAgentFile(content, rolePath);
}

/**
 * 列出用户角色目录（~/.ao/roles）里的自建角色，rolePath 统一为 "my/<id>"。
 * 目录不存在返回空数组；只认带 name frontmatter 的 .md（与内置库同规则）。
 */
export function listUserAgents(): AgentDefinition[] {
  const dir = userRolesDir();
  if (!existsSync(dir)) return [];
  const agents: AgentDefinition[] = [];
  for (const f of readdirSync(dir)) {
    if (!f.endsWith('.md')) continue;
    const full = join(dir, f);
    if (!isAgentFile(full)) continue;
    try {
      const rolePath = `${USER_ROLE_CATEGORY}/${f.replace(/\.md$/, '')}`;
      const agent = parseAgentFile(readFileSync(full, 'utf-8'), rolePath);
      agent.rolePath = rolePath;
      agents.push(agent);
    } catch {
      // 跳过无法解析的文件
    }
  }
  return agents;
}

/**
 * 解析 agent .md 文件内容
 */
function parseAgentFile(content: string, rolePath: string): AgentDefinition {
  const frontmatterMatch = content.match(/^---\s*\n([\s\S]*?)\n---\s*\n([\s\S]*)$/);

  if (!frontmatterMatch) {
    // 没有 frontmatter，整个文件当 system prompt
    return {
      name: rolePath,
      description: '',
      systemPrompt: content.trim(),
    };
  }

  const frontmatterRaw = frontmatterMatch[1];
  const body = frontmatterMatch[2];

  // 简单解析 frontmatter（不用 js-yaml 避免循环依赖，frontmatter 结构简单）
  const meta: Record<string, string> = {};
  for (const line of frontmatterRaw.split('\n')) {
    const colonIdx = line.indexOf(':');
    if (colonIdx > 0) {
      const key = line.slice(0, colonIdx).trim();
      const value = line.slice(colonIdx + 1).trim().replace(/^["']|["']$/g, '');
      meta[key] = value;
    }
  }

  return {
    name: meta.name || rolePath,
    description: meta.description || '',
    emoji: meta.emoji,
    tools: meta.tools,
    systemPrompt: body.trim(),
  };
}

/**
 * 列出所有可用角色
 * @param includeUser true 时叠加用户角色目录（~/.ao/roles）的 my/* 角色；
 *                    默认 false 保持纯目录语义（安装计数、测试 fixture 等不受用户机器状态影响）
 */
export function listAgents(agentsDir: string, includeUser = false): AgentDefinition[] {
  const dir = resolve(agentsDir);
  if (!existsSync(dir)) {
    throw new Error(`agents 目录不存在: ${dir}`);
  }

  const agents: AgentDefinition[] = [];
  // 递归收集（含 game-development/unity/* 等嵌套角色），只取带 name frontmatter 的真角色
  for (const rolePath of collectRolePaths(dir)) {
    try {
      const agent = loadAgent(agentsDir, rolePath);
      agent.rolePath = rolePath;
      agents.push(agent);
    } catch {
      // 跳过无法解析的文件
    }
  }

  if (includeUser) {
    // agents_dir 里真有 my/ 子目录时以 agents_dir 优先（与 loadAgent 解析次序一致）
    const seen = new Set(agents.map(a => a.rolePath));
    for (const ua of listUserAgents()) {
      if (!seen.has(ua.rolePath)) agents.push(ua);
    }
  }

  return agents;
}

/**
 * 轻量列出所有角色路径（如 "engineering/engineering-sre"），不解析文件内容。
 * 用于 validate 报错时给"你是不是想用 X"建议，避免 listAgents 全量解析的开销。
 */
export function listRolePaths(agentsDir: string): string[] {
  const dir = resolve(agentsDir);
  if (!existsSync(dir)) return [];
  const paths: string[] = collectRolePaths(dir);
  return paths;
}

/** Levenshtein 编辑距离（用于角色名模糊匹配） */
function editDistance(a: string, b: string): number {
  const m = a.length, n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;
  let prev = Array.from({ length: n + 1 }, (_, i) => i);
  let cur = new Array(n + 1);
  for (let i = 1; i <= m; i++) {
    cur[0] = i;
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
    }
    [prev, cur] = [cur, prev];
  }
  return prev[n];
}

/**
 * 在给定的候选路径集合里，找出最接近 badPath 的若干个（纯函数，不读盘）。
 * 优先子串包含（按 leaf 名匹配），再按编辑距离兜底；只返回足够接近的。
 * 供 compose 复用——它要在"实际提供给 LLM 的目录"里建议，而非全盘所有角色。
 */
export function suggestFromPaths(badPath: string, allPaths: string[], limit = 3): string[] {
  if (allPaths.length === 0) return [];
  const leaf = (badPath.split('/').pop() || badPath).toLowerCase();
  const target = badPath.toLowerCase();

  const scored = allPaths.map(p => {
    const pl = p.toLowerCase();
    const pleaf = (p.split('/').pop() || p).toLowerCase();
    // 子串命中给大幅加分（排到前面）
    const substr = pl.includes(leaf) || pleaf.includes(leaf) || leaf.includes(pleaf);
    const dist = editDistance(target, pl);
    return { p, dist, substr };
  });

  scored.sort((a, b) =>
    (a.substr === b.substr ? 0 : a.substr ? -1 : 1) || a.dist - b.dist
  );

  // 只保留"够接近"的：子串命中，或编辑距离不超过 leaf 长度的一半 + 4
  const threshold = Math.ceil(leaf.length / 2) + 4;
  return scored
    .filter(s => s.substr || s.dist <= threshold)
    .slice(0, limit)
    .map(s => s.p);
}

/**
 * 给一个拼错的角色路径，返回最接近的若干真实角色（"你是不是想用…"）。
 */
export function suggestRoles(badPath: string, agentsDir: string, limit = 3): string[] {
  return suggestFromPaths(badPath, listRolePaths(agentsDir), limit);
}

/**
 * 按关键词过滤角色：匹配 rolePath / name / description（不区分大小写）。
 * 空关键词返回原列表。供 `ao roles <keyword>` 使用，方便手动找专家。
 */
export function filterAgentsByKeyword<T extends { name?: string; rolePath?: string; description?: string }>(
  agents: T[],
  keyword: string,
): T[] {
  const kw = keyword.trim().toLowerCase();
  if (!kw) return agents;
  return agents.filter(a =>
    (a.rolePath || '').toLowerCase().includes(kw) ||
    (a.name || '').toLowerCase().includes(kw) ||
    (a.description || '').toLowerCase().includes(kw)
  );
}
