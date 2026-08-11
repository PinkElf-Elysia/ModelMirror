/**
 * 条件表达式求值
 *
 * 支持的语法:
 *   {{变量}} contains 关键词
 *   {{变量}} equals 关键词
 *   关键词可用引号包裹: {{var}} contains "bug fix"
 *
 * 大小写不敏感，自动 trim
 */
import { renderTemplate } from './template.js';

// Matches known operators (contains/equals) anchored to whole words
const KNOWN_OP_REGEX = /^(.+?)\s+(contains|equals)\s+(.+)$/is;
// Matches any word as operator to detect unsupported operators
const ANY_OP_REGEX = /^.+\s+(\w+)\s+.+$/is;

export function evaluateCondition(
  condition: string,
  context: Map<string, string>
): boolean {
  // 在「模板」(替换变量之前)上解析运算符：运算符位置由作者在 YAML 里写定，
  // 不能用替换后的字符串来切分——否则变量值(LLM 产出)里恰好出现 contains/equals
  // 会把条件从错误的位置切开，导致分支/循环退出被翻转。
  const match = condition.match(KNOWN_OP_REGEX);
  if (!match) {
    // Check if the format looks valid but with an unsupported operator
    const opMatch = condition.match(ANY_OP_REGEX);
    if (opMatch) {
      throw new Error(`不支持的条件运算符: "${opMatch[1]}"。支持 contains 和 equals`);
    }
    throw new Error(`条件格式错误: "${condition}"。支持的格式: <text> contains <keyword> 或 <text> equals <keyword>`);
  }

  // 仅对两侧操作数分别替换变量；换行替空格避免多行 LLM 输出干扰
  const left = renderTemplate(match[1], context).trim().replace(/\n/g, ' ').toLowerCase();
  const operator = match[2].toLowerCase();
  // 去掉引号包裹
  const right = renderTemplate(match[3], context).trim().replace(/^["']|["']$/g, '').toLowerCase();

  switch (operator) {
    case 'contains':
      return left.includes(right);
    case 'equals':
      return left === right;
    default:
      throw new Error(`不支持的条件运算符: "${operator}"。支持 contains 和 equals`);
  }
}
