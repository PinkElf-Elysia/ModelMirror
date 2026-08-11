/**
 * acceptance（验收标准）测试：
 * 注入 prompt 末尾 → StepResult/metadata 存档 → 步骤文件头展示 → validate 校验 → compose 提示词教学
 */
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parseWorkflow, validateWorkflow } from '../src/core/parser.js';
import { buildDAG } from '../src/core/dag.js';
import { executeDAG } from '../src/core/executor.js';
import { saveResults } from '../src/output/reporter.js';
import { buildComposeSystemPrompt } from '../src/cli/compose.js';
import type { LLMConnector, LLMResult, LLMConfig, WorkflowDefinition } from '../src/types.js';

let passed = 0, failed = 0;
function assert(c: boolean, m: string): void { if (c) { console.log(`  ✅ ${m}`); passed++; } else { console.log(`  ❌ ${m}`); failed++; } }

console.log('\n─── acceptance（验收标准）───');

// 临时角色库 + 临时工作流（不依赖外部角色包）
const dir = mkdtempSync(join(tmpdir(), 'ao-acceptance-'));
mkdirSync(join(dir, 'x'), { recursive: true });
writeFileSync(join(dir, 'x', 'y.md'), `---
name: 测试角色
description: 测试用
---
你是测试角色。
`, 'utf-8');

const wfPath = join(dir, 'wf.yaml');
writeFileSync(wfPath, `name: acceptance-test
agents_dir: ${dir}
llm:
  provider: deepseek
  model: deepseek-chat
inputs:
  - name: topic
    required: true
steps:
  - id: a
    role: x/y
    task: 围绕 {{topic}} 写一段介绍
    acceptance: |
      1. 必须提到 {{topic}}
      2. 不超过 200 字
    output: out_a
  - id: b
    role: x/y
    task: 基于 {{out_a}} 润色
    output: out_b
    depends_on: [a]
`, 'utf-8');

class MockConnector implements LLMConnector {
  callLog: { system: string; user: string }[] = [];
  async chat(systemPrompt: string, userMessage: string, _config: LLMConfig): Promise<LLMResult> {
    this.callLog.push({ system: systemPrompt, user: userMessage });
    return { content: 'mock 产出', usage: { input_tokens: 1, output_tokens: 1 } };
  }
}

const wf = parseWorkflow(wfPath);
assert(validateWorkflow(wf).length === 0, '合法 acceptance 通过 validate');

const mock = new MockConnector();
const result = await executeDAG(buildDAG(wf), {
  connector: mock,
  agentsDir: dir,
  llmConfig: wf.llm,
  concurrency: 1,
  inputs: new Map([['topic', '长城']]),
});
result.name = wf.name;

// 1) 注入：a 的 user message 末尾带验收标准且变量已渲染；b 不带
const callA = mock.callLog.find(c => c.user.includes('写一段介绍'));
const callB = mock.callLog.find(c => c.user.includes('润色'));
assert(!!callA && callA.user.includes('交付验收标准') && callA.user.includes('必须提到 长城'), 'acceptance 注入 prompt 末尾且 {{topic}} 已渲染');
assert(!!callA && callA.user.indexOf('交付验收标准') > callA.user.indexOf('写一段介绍'), 'acceptance 位于任务描述之后（最后指令）');
assert(!!callB && !callB.user.includes('交付验收标准'), '无 acceptance 的步骤不注入');

// 2) StepResult / metadata / 步骤文件
const stepA = result.steps.find(s => s.id === 'a');
assert(stepA?.acceptance?.includes('必须提到 长城') === true, 'StepResult.acceptance 为渲染后的文本');
assert(result.steps.find(s => s.id === 'b')?.acceptance === undefined, '无 acceptance 的 StepResult 不带该字段');

const outDir = saveResults(result, join(dir, 'out'));
const stepFile = readFileSync(join(outDir, 'steps', '1-a.md'), 'utf-8');
assert(stepFile.includes('✅ 验收标准') && stepFile.includes('必须提到 长城'), '步骤文件头部展示验收标准');
assert(stepFile.indexOf('✅ 验收标准') < stepFile.indexOf('\n---\n'), '验收标准在头部区（web 截头后不混入正文）');
const meta = JSON.parse(readFileSync(join(outDir, 'metadata.json'), 'utf-8'));
assert(meta.steps[0].acceptance?.includes('必须提到 长城'), 'metadata.json 步骤带 acceptance');

// 3) validate：坏变量 / 非字符串
const badVar: WorkflowDefinition = { ...wf, steps: wf.steps.map(s => s.id === 'a' ? { ...s, acceptance: '要求包含 {{no_such}}' } : s) };
assert(validateWorkflow(badVar).some(e => e.includes('no_such')), 'acceptance 里的未定义变量被 validate 报出');
const badType = { ...wf, steps: wf.steps.map(s => s.id === 'a' ? { ...s, acceptance: ['a', 'b'] as unknown as string } : s) };
assert(validateWorkflow(badType).some(e => e.includes('acceptance 必须是字符串')), '非字符串 acceptance 被 validate 报出');

// 4) compose 提示词：中英都教 acceptance + 高风险 approval（任务 B3/C）
for (const lang of ['zh', 'en'] as const) {
  const p = buildComposeSystemPrompt('catalog', { lang });
  assert(p.includes('acceptance'), `compose ${lang} 提示词含 acceptance 字段教学`);
  assert(/approval/.test(p) && (lang === 'zh' ? p.includes('金融') : /finance/i.test(p)), `compose ${lang} 提示词含高风险 approval 规则`);
}

rmSync(dir, { recursive: true, force: true });
console.log(`\n  结果: ${passed} 通过, ${failed} 失败\n`);
if (failed > 0) process.exit(1);
