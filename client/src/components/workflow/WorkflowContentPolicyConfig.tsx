import { useState } from "react";

import { nextStableId } from "./workflowTypedAiMigration";

interface ContentPolicyRule {
  id: string;
  label: string;
  detector: "literal_terms" | "email_address" | "phone_number" | "secret_pattern";
  action: "block" | "redact";
  terms: string[];
  caseSensitive: boolean;
}

const controlClass =
  "modelmirror-form-control w-full rounded-lg border border-white/10 bg-[#0f1728] px-3 py-2 text-sm text-white outline-none transition placeholder:text-slate-500 hover:border-white/20 focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10";

const defaultRule = (id: string): ContentPolicyRule => ({
  id,
  label: "疑似凭据",
  detector: "secret_pattern",
  action: "block",
  terms: [],
  caseSensitive: false,
});

function normalizeRules(value: unknown): ContentPolicyRule[] {
  if (!Array.isArray(value)) return [defaultRule("rule_1")];
  const rules = value.filter((item): item is Record<string, unknown> =>
    Boolean(item) && !Array.isArray(item) && typeof item === "object",
  ).map((item, index) => ({
    id: String(item.id ?? `rule_${index + 1}`),
    label: String(item.label ?? "内容规则"),
    detector: ["literal_terms", "email_address", "phone_number", "secret_pattern"].includes(String(item.detector))
      ? String(item.detector) as ContentPolicyRule["detector"]
      : "literal_terms",
    action: item.action === "redact" ? "redact" as const : "block" as const,
    terms: Array.isArray(item.terms) ? item.terms.filter((term): term is string => typeof term === "string") : [],
    caseSensitive: item.caseSensitive === true,
  }));
  return rules.length ? rules : [defaultRule("rule_1")];
}

export default function WorkflowContentPolicyConfig({
  config,
  onChange,
}: {
  config: Record<string, unknown> | undefined;
  onChange: (fieldName: string, value: unknown) => void;
}) {
  const [notice, setNotice] = useState("");
  const rules = normalizeRules(config?.rules);
  const updateRule = (id: string, patch: Partial<ContentPolicyRule>) =>
    onChange("rules", rules.map((rule) => rule.id === id ? { ...rule, ...patch } : rule));
  const addRule = () => {
    const id = nextStableId("rule", rules.map((rule) => rule.id), 20);
    if (!id) return;
    setNotice("");
    onChange("rules", [...rules, defaultRule(id)]);
  };
  const removeRule = (id: string) => {
    if (rules.length <= 1) {
      setNotice("内容策略至少需要一条规则。");
      return;
    }
    setNotice("");
    onChange("rules", rules.filter((rule) => rule.id !== id));
  };

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-emerald-300/20 bg-emerald-300/[0.07] px-3 py-2 text-xs leading-5 text-emerald-50">
        仅检查智能体的文本输入和最终可见输出。护栏只记录规则 ID；输入脱敏不会覆盖原工作流变量。
      </div>
      <label className="block">
        <span className="text-xs font-semibold text-slate-300">检查阶段</span>
        <select className={`${controlClass} mt-2`} onChange={(event) => onChange("phase", event.target.value)} value={String(config?.phase ?? "both")}>
          <option className="bg-slate-950" value="both">输入与输出</option>
          <option className="bg-slate-950" value="input">仅模型输入前</option>
          <option className="bg-slate-950" value="output">仅最终输出前</option>
        </select>
      </label>
      {notice ? <p className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs text-amber-50" role="status">{notice}</p> : null}
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-slate-300">策略规则（{rules.length}/20）</p>
        <button className="rounded-md border border-white/15 px-2.5 py-1 text-xs text-slate-200 hover:bg-white/10" disabled={rules.length >= 20} onClick={addRule} type="button">添加规则</button>
      </div>
      {rules.map((rule) => (
        <div className="space-y-2 rounded-lg border border-white/10 bg-white/[0.03] p-3" key={rule.id}>
          <div className="flex items-center justify-between text-[11px] text-slate-500"><span>{rule.id}</span><button className="text-rose-200" onClick={() => removeRule(rule.id)} type="button">删除</button></div>
          <input aria-label={`${rule.id} 规则名称`} className={controlClass} maxLength={100} onChange={(event) => updateRule(rule.id, { label: event.target.value })} placeholder="便于定位的规则名称" value={rule.label} />
          <div className="grid grid-cols-2 gap-2">
            <select aria-label={`${rule.id} 检测器`} className={controlClass} onChange={(event) => updateRule(rule.id, { detector: event.target.value as ContentPolicyRule["detector"], terms: event.target.value === "literal_terms" ? rule.terms : [] })} value={rule.detector}>
              <option className="bg-slate-950" value="literal_terms">指定字词</option>
              <option className="bg-slate-950" value="email_address">邮箱地址</option>
              <option className="bg-slate-950" value="phone_number">保守电话号码</option>
              <option className="bg-slate-950" value="secret_pattern">疑似凭据</option>
            </select>
            <select aria-label={`${rule.id} 动作`} className={controlClass} onChange={(event) => updateRule(rule.id, { action: event.target.value as ContentPolicyRule["action"] })} value={rule.action}>
              <option className="bg-slate-950" value="block">阻断执行</option>
              <option className="bg-slate-950" value="redact">替换为 [已脱敏]</option>
            </select>
          </div>
          {rule.detector === "literal_terms" ? (
            <>
              <textarea aria-label={`${rule.id} 字词列表`} className={`${controlClass} min-h-20 resize-none`} onChange={(event) => updateRule(rule.id, { terms: event.target.value.split(/[,\n]+/).map((term) => term.trim()).filter(Boolean) })} placeholder="每行一个字词，最多 20 个" value={rule.terms.join("\n")} />
              <label className="flex items-center gap-2 text-xs text-slate-300"><input checked={rule.caseSensitive} onChange={(event) => updateRule(rule.id, { caseSensitive: event.target.checked })} type="checkbox" />区分大小写</label>
            </>
          ) : (
            <p className="text-[11px] leading-5 text-slate-500">内置检测器使用固定保守规则，不需要填写匹配表达式。</p>
          )}
        </div>
      ))}
      <p className="text-[11px] leading-5 text-slate-500">文本超过 200,000 字符会安全失败。首版不检查图片、音频或附件内容。</p>
    </div>
  );
}
