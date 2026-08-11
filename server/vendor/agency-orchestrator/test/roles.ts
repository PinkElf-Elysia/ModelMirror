/**
 * filterAgentsByKeyword 单测（ao roles <keyword> 搜索）+ 用户自建角色（my/*，~/.ao/roles）解析。
 */
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { filterAgentsByKeyword, listAgents, listUserAgents, loadAgent } from '../src/agents/loader.js';

let passed = 0, failed = 0;
function assert(c: boolean, msg: string): void { if (!c) throw new Error(msg); }
function test(name: string, fn: () => void): void {
  try { fn(); console.log(`  ✅ ${name}`); passed++; }
  catch (e) { console.log(`  ❌ ${name}: ${e instanceof Error ? e.message : e}`); failed++; }
}

const agents = [
  { name: 'SEO Specialist', rolePath: 'marketing/marketing-seo-specialist', description: 'search engine optimization' },
  { name: 'Backend Architect', rolePath: 'engineering/engineering-backend-architect', description: 'APIs and databases' },
  { name: 'Financial Analyst', rolePath: 'finance/finance-financial-analyst', description: '财务建模与分析' },
];

console.log('\n=== ao roles 关键词搜索 ===');

test('按 rolePath 匹配', () => {
  assert(filterAgentsByKeyword(agents, 'seo').length === 1, '应命中 1 个 seo');
});
test('按 description 匹配', () => {
  assert(filterAgentsByKeyword(agents, 'database').length === 1, '应命中 backend');
});
test('按 name 匹配且不区分大小写', () => {
  assert(filterAgentsByKeyword(agents, 'BACKEND').length === 1, '大写也应命中');
});
test('中文描述可被中文关键词命中', () => {
  assert(filterAgentsByKeyword(agents, '财务').length === 1, '应命中财务');
});
test('空关键词返回全部', () => {
  assert(filterAgentsByKeyword(agents, '  ').length === 3, '空应返回全部');
});
test('无匹配返回空', () => {
  assert(filterAgentsByKeyword(agents, 'zzznope').length === 0, '应为空');
});

// ── 用户自建角色：my/<id> 解析到 AO_USER_ROLES_DIR（默认 ~/.ao/roles），与 agents_dir 叠加 ──
console.log('\n=== 用户自建角色（my/*）===');

const tmp = mkdtempSync(join(tmpdir(), 'ao-user-roles-'));
const userDir = join(tmp, 'roles');
const agentsDir = join(tmp, 'agents');
mkdirSync(userDir, { recursive: true });
mkdirSync(join(agentsDir, 'engineering'), { recursive: true });
writeFileSync(join(userDir, 'my-expert.md'), '---\nname: 我的专家\ndescription: 自建\n---\n\n你是我的专家。\n');
writeFileSync(join(userDir, 'notes.md'), '没有 frontmatter 的笔记，不是角色\n');
writeFileSync(join(agentsDir, 'engineering', 'dev.md'), '---\nname: Dev\n---\n\nYou are a dev.\n');
const prevEnv = process.env.AO_USER_ROLES_DIR;
process.env.AO_USER_ROLES_DIR = userDir;

try {
  test('listUserAgents 枚举用户目录，只认带 name frontmatter 的角色', () => {
    const ua = listUserAgents();
    assert(ua.length === 1, `应只有 1 个（实际 ${ua.length}）`);
    assert(ua[0].rolePath === 'my/my-expert' && ua[0].name === '我的专家', 'rolePath 应为 my/my-expert');
  });
  test('loadAgent 对 my/<id> 兜底到用户角色目录', () => {
    const a = loadAgent(agentsDir, 'my/my-expert');
    assert(a.systemPrompt === '你是我的专家。', 'system prompt 应来自用户目录文件');
  });
  test('loadAgent 非 my/ 路径不受影响', () => {
    assert(loadAgent(agentsDir, 'engineering/dev').name === 'Dev', '内置角色照常解析');
  });
  test('my/ 下不存在的角色仍然报错', () => {
    let threw = false;
    try { loadAgent(agentsDir, 'my/nope'); } catch { threw = true; }
    assert(threw, '应抛「角色文件不存在」');
  });
  test('listAgents includeUser=true 叠加用户角色，默认不叠加', () => {
    assert(listAgents(agentsDir).length === 1, '默认只有内置 1 个');
    const merged = listAgents(agentsDir, true);
    assert(merged.length === 2 && merged.some(a => a.rolePath === 'my/my-expert'), '叠加后应含 my/my-expert');
  });
  test('my/ 路径穿越被拦截', () => {
    let threw = false;
    try { loadAgent(agentsDir, 'my/../secret'); } catch { threw = true; }
    assert(threw, '应拦截 ../');
  });
} finally {
  if (prevEnv === undefined) delete process.env.AO_USER_ROLES_DIR; else process.env.AO_USER_ROLES_DIR = prevEnv;
  rmSync(tmp, { recursive: true, force: true });
}

console.log(`\n  结果: ${passed} 通过, ${failed} 失败\n`);
if (failed > 0) process.exit(1);
