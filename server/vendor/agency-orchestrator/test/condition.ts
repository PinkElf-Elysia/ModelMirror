/**
 * 条件表达式求值测试
 */
import { evaluateCondition } from '../src/core/condition.js';

let passed = 0;
let failed = 0;

function test(name: string, fn: () => void): void {
  try {
    fn();
    console.log(`  ✅ ${name}`);
    passed++;
  } catch (err) {
    console.log(`  ❌ ${name}: ${err instanceof Error ? err.message : err}`);
    failed++;
  }
}

function assert(condition: boolean, msg: string): void {
  if (!condition) throw new Error(msg);
}

console.log('\n=== Condition Evaluator ===');

// contains 运算符
test('contains: 匹配子串', () => {
  const ctx = new Map([['category', 'this is a bug_fix issue']]);
  assert(evaluateCondition('{{category}} contains bug', ctx) === true, '应匹配 bug');
});

test('contains: 大小写不敏感', () => {
  const ctx = new Map([['type', 'BUG_FIX']]);
  assert(evaluateCondition('{{type}} contains bug', ctx) === true, '应忽略大小写');
});

test('contains: 不匹配时返回 false', () => {
  const ctx = new Map([['category', 'new_feature']]);
  assert(evaluateCondition('{{category}} contains bug', ctx) === false, '不应匹配');
});

test('contains: 关键词有空格（引号包裹）', () => {
  const ctx = new Map([['msg', 'this is a bug fix']]);
  assert(evaluateCondition('{{msg}} contains "bug fix"', ctx) === true, '应匹配带空格的关键词');
});

// equals 运算符
test('equals: 精确匹配', () => {
  const ctx = new Map([['answer', 'yes']]);
  assert(evaluateCondition('{{answer}} equals yes', ctx) === true, '应精确匹配');
});

test('equals: 大小写不敏感', () => {
  const ctx = new Map([['answer', 'YES']]);
  assert(evaluateCondition('{{answer}} equals yes', ctx) === true, '应忽略大小写');
});

test('equals: trim 后匹配', () => {
  const ctx = new Map([['answer', '  yes  ']]);
  assert(evaluateCondition('{{answer}} equals yes', ctx) === true, '应 trim 后匹配');
});

test('equals: 不匹配时返回 false', () => {
  const ctx = new Map([['answer', 'maybe yes']]);
  assert(evaluateCondition('{{answer}} equals yes', ctx) === false, 'equals 不应做子串匹配');
});

// 边界情况
test('变量替换后再求值', () => {
  const ctx = new Map([['feedback', '文案质量不错，通过']]);
  assert(evaluateCondition('{{feedback}} contains 通过', ctx) === true, '应处理中文');
});

// 回归：变量值里恰好出现运算符词不应翻转判定（运算符须从模板解析）
test('变量值含 "contains" 不干扰运算符解析', () => {
  const ctx = new Map([['out', 'the spec contains details']]);
  assert(evaluateCondition('{{out}} contains 通过', ctx) === false, '右操作数应为模板里的「通过」，不应被值里的 contains 误导');
  const ctx2 = new Map([['out', 'the spec contains the word 通过 somewhere']]);
  assert(evaluateCondition('{{out}} contains 通过', ctx2) === true, '左值含 通过 应匹配');
});

test('变量值含 "equals" 不干扰运算符解析', () => {
  const ctx = new Map([['out', 'x equals y']]);
  assert(evaluateCondition('{{out}} equals approved', ctx) === false, '应按模板的 equals approved 比较，而非值里的 equals');
});

test('未知运算符抛错', () => {
  const ctx = new Map([['x', 'hello']]);
  try {
    evaluateCondition('{{x}} matches hello', ctx);
    throw new Error('应该抛错');
  } catch (err) {
    assert((err as Error).message.includes('不支持的条件运算符'), '应提示不支持');
  }
});

test('格式错误抛错', () => {
  const ctx = new Map([['x', 'hello']]);
  try {
    evaluateCondition('bad format', ctx);
    throw new Error('应该抛错');
  } catch (err) {
    assert((err as Error).message.includes('条件格式错误'), '应提示格式错误');
  }
});

// 结果
console.log('\n' + '='.repeat(50));
console.log(`  Condition 测试: ${passed} 通过, ${failed} 失败 (共 ${passed + failed} 项)`);
if (failed === 0) console.log('  全部通过!');
else process.exit(1);
console.log('='.repeat(50) + '\n');
