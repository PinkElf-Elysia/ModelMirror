/**
 * {{变量}} 模板引擎
 * 简单的字符串替换，不需要复杂的模板语法
 */

/**
 * 替换字符串中的 {{变量名}} 为上下文中的值
 */
export function renderTemplate(template: string, context: Map<string, string>): string {
  return template.replace(/\{\{(\w+)\}\}/g, (_match, varName: string) => {
    const value = context.get(varName);
    if (value === undefined) {
      throw new Error(`模板变量未定义: {{${varName}}}`);
    }
    return value;
  });
}

/**
 * 提取模板中引用的所有变量名
 */
export function extractVariables(template: string): string[] {
  const vars: string[] = [];
  const regex = /\{\{(\w+)\}\}/g;
  let match;
  while ((match = regex.exec(template)) !== null) {
    if (!vars.includes(match[1])) {
      vars.push(match[1]);
    }
  }
  return vars;
}
