import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ExternalLink, Plus, RefreshCw, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";

import type {
  HttpRequestMethod,
  WorkflowEdge,
  WorkflowHttpBinding,
  WorkflowHttpParameter,
  WorkflowNode,
  WorkflowNodeData,
  WorkflowValue,
  WorkflowVariableDeclaration,
} from "../../types/workflow";
import type { WorkflowNodeContractProjection } from "./workflowNodeRegistry";
import WorkflowVariableField from "./WorkflowVariableField";
import WorkflowFailureRoutingConfig from "./WorkflowFailureRoutingConfig";
import type { WorkflowVariableFieldDescriptor } from "./workflowVariables";


const inputClass =
  "modelmirror-form-control w-full rounded-lg border border-white/10 bg-[#0f1728] px-3 py-2 text-sm text-white outline-none transition placeholder:text-slate-400 hover:border-white/20 focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10 disabled:cursor-not-allowed disabled:opacity-50";
const compactInputClass = `${inputClass} px-2.5 py-1.5 text-xs`;
const protectedHeaders = new Set([
  "authorization",
  "connection",
  "content-length",
  "cookie",
  "host",
  "proxy-authorization",
  "set-cookie",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);
const headerNamePattern = /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/;
const fullVariableTemplatePattern = /^{{\s*([A-Za-z_][A-Za-z0-9_]{0,63})\s*}}$/;
const sensitiveParameterNamePattern = /authorization|cookie|credential|password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token/i;

interface CredentialSummary {
  credential_id: string;
  name: string;
  kind: string;
  masked_value: string;
  status: "active" | "unavailable" | "revoked";
}

interface LegacyMigrationResult {
  canMigrate: boolean;
  reasons: string[];
  patch?: Partial<WorkflowNodeData>;
}

let parameterSequence = 0;

function parameterId(prefix: string) {
  parameterSequence += 1;
  return `${prefix}_${Date.now()}_${parameterSequence}`;
}

function literalBinding(value: WorkflowValue = ""): WorkflowHttpBinding {
  const valueType = value === null
    ? "null"
    : typeof value === "number"
      ? "number"
      : typeof value === "boolean"
        ? "boolean"
        : typeof value === "string"
          ? "text"
          : "json";
  return { source: "literal", valueType, value };
}

function literalOrigin(url: string) {
  const schemeEnd = url.indexOf("://");
  if (schemeEnd < 0) return url;
  const authorityStart = schemeEnd + 3;
  const suffix = url.slice(authorityStart);
  const boundary = suffix.search(/[/?#]/);
  return boundary < 0 ? url : url.slice(0, authorityStart + boundary);
}

export function analyzeLegacyHttpMigration(data: WorkflowNodeData): LegacyMigrationResult {
  const reasons: string[] = [];
  const headerItems: WorkflowHttpParameter[] = [];
  const seenHeaders = new Set<string>();
  const rawHeaders = String(data.headersJson ?? "").trim();
  if (rawHeaders) {
    try {
      const parsed = JSON.parse(rawHeaders) as unknown;
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
        reasons.push("请求头 JSON 必须是对象。");
      } else {
        const entries = Object.entries(parsed as Record<string, unknown>);
        if (entries.length > 20) reasons.push("请求头超过 20 项。");
        entries.forEach(([name, value], index) => {
          const normalizedName = name.toLowerCase();
          if (!headerNamePattern.test(name)) {
            reasons.push(`请求头 ${name || "（空名称）"} 的名称无效。`);
          }
          if (seenHeaders.has(normalizedName)) {
            reasons.push(`请求头 ${name} 与另一项名称重复。`);
          }
          seenHeaders.add(normalizedName);
          if (protectedHeaders.has(normalizedName)) {
            reasons.push(`请求头 ${name} 由运行器管理，不能迁移。`);
          }
          if (sensitiveParameterNamePattern.test(name)) {
            reasons.push(`请求头 ${name} 可能承载认证秘密，请改用加密凭据。`);
          }
          if (value !== null && !["string", "number", "boolean"].includes(typeof value)) {
            reasons.push(`请求头 ${name} 不是标量值。`);
            return;
          }
          const variableMatch = typeof value === "string"
            ? value.match(fullVariableTemplatePattern)
            : null;
          if (typeof value === "string" && /{{|}}/.test(value) && !variableMatch) {
            reasons.push(`请求头 ${name} 混合了固定文本和变量，无法无损迁移。`);
          }
          headerItems.push({
            id: `header_${index + 1}`,
            name,
            binding: variableMatch
              ? { source: "variable", variable: variableMatch[1] }
              : literalBinding(value === null ? null : value as WorkflowValue),
          });
        });
      }
    } catch {
      reasons.push("请求头 JSON 无法解析。");
    }
  }
  const url = String(data.url ?? "").trim();
  const origin = literalOrigin(url);
  if (/{{|}}/.test(origin)) reasons.push("URL 的协议、主机和端口包含变量。");
  if (!url || url.length > 2_048) reasons.push("URL 长度必须为 1 至 2048 个字符。");
  try {
    const parsed = new URL(url.replace(/{{\s*[A-Za-z_][A-Za-z0-9_]{0,63}\s*}}/g, "template"));
    if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname) {
      reasons.push("URL 必须是包含固定主机的 HTTP 或 HTTPS 地址。");
    }
    if (parsed.username || parsed.password) reasons.push("URL 不能内嵌用户名或密码。");
  } catch {
    reasons.push("URL 不是有效的 HTTP 或 HTTPS 地址。");
  }
  const method = String(data.method ?? "GET").toUpperCase() as HttpRequestMethod;
  if (reasons.length > 0) return { canMigrate: false, reasons };
  const bodyVariable = String(data.bodyVariable ?? "").trim();
  if (bodyVariable && ["GET", "DELETE"].includes(method)) {
    return { canMigrate: false, reasons: [`${method} 请求不能携带正文，无法无损迁移正文变量。`] };
  }
  return {
    canMigrate: true,
    reasons: [],
    patch: {
      contractVersion: 2,
      method,
      url,
      queryItems: [],
      headerItems,
      bodyMode: bodyVariable ? "text" : "none",
      bodyBinding: bodyVariable
        ? { source: "variable", variable: bodyVariable }
        : undefined,
      formFields: [],
      authType: "none",
      credentialId: "",
      apiKeyLocation: "header",
      apiKeyName: "X-API-Key",
      timeoutSeconds: 30,
      redirectLimit: 0,
      responseLimitBytes: 1_048_576,
      responseMode: "auto",
      statusPolicy: "success_only",
      outputVariable: data.outputVariable || "http_response",
      description: "调用公网 HTTP 接口，并把安全结构化响应写入变量。",
    },
  };
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-semibold text-slate-200">{label}</span>
      {children}
      {hint ? <span className="mt-1.5 block text-[11px] leading-5 text-slate-400">{hint}</span> : null}
    </label>
  );
}

function Section({ title, detail, children }: { title: string; detail: string; children: ReactNode }) {
  return (
    <section className="space-y-3 border-t border-white/10 pt-4">
      <div>
        <h3 className="text-xs font-semibold text-white">{title}</h3>
        <p className="mt-1 text-[11px] leading-5 text-slate-400">{detail}</p>
      </div>
      {children}
    </section>
  );
}

function BindingEditor({
  binding,
  fieldName,
  node,
  nodes,
  edges,
  declarations,
  contract,
  allowJson = false,
  descriptor,
  onChange,
}: {
  binding: WorkflowHttpBinding;
  fieldName: string;
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  declarations: WorkflowVariableDeclaration[];
  contract?: WorkflowNodeContractProjection | null;
  allowJson?: boolean;
  descriptor?: WorkflowVariableFieldDescriptor;
  onChange: (binding: WorkflowHttpBinding) => void;
}) {
  const [jsonDraft, setJsonDraft] = useState(
    binding.valueType === "json" ? JSON.stringify(binding.value ?? {}, null, 2) : "",
  );
  const [jsonError, setJsonError] = useState("");
  if (binding.source === "variable") {
    return (
      <div className="grid gap-2 sm:grid-cols-[108px_minmax(0,1fr)]">
        <select className={compactInputClass} onChange={(event) => onChange(event.target.value === "variable" ? binding : literalBinding())} value="variable">
          <option className="bg-slate-950" value="literal">固定值</option>
          <option className="bg-slate-950" value="variable">工作流变量</option>
        </select>
        <WorkflowVariableField contract={contract} declarations={declarations} descriptor={descriptor} edges={edges} fieldName={fieldName} inputClassName={compactInputClass} node={node} nodes={nodes} onChange={(value) => onChange({ source: "variable", variable: value })} value={binding.variable ?? ""} />
      </div>
    );
  }
  const valueType = binding.valueType ?? "text";
  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-[108px_108px_minmax(0,1fr)]">
        <select className={compactInputClass} onChange={(event) => onChange(event.target.value === "variable" ? { source: "variable", variable: "" } : binding)} value="literal">
          <option className="bg-slate-950" value="literal">固定值</option>
          <option className="bg-slate-950" value="variable">工作流变量</option>
        </select>
        <select
          className={compactInputClass}
          onChange={(event) => {
            const nextType = event.target.value as WorkflowHttpBinding["valueType"];
            const defaults: Record<string, WorkflowValue> = { text: "", number: 0, boolean: true, null: null, json: {} };
            onChange({ source: "literal", valueType: nextType, value: defaults[nextType ?? "text"] });
          }}
          value={valueType}
        >
          <option className="bg-slate-950" value="text">文本</option>
          <option className="bg-slate-950" value="number">数字</option>
          <option className="bg-slate-950" value="boolean">布尔值</option>
          <option className="bg-slate-950" value="null">空值</option>
          {allowJson ? <option className="bg-slate-950" value="json">JSON</option> : null}
        </select>
        {valueType === "boolean" ? (
          <select className={compactInputClass} onChange={(event) => onChange({ ...binding, value: event.target.value === "true" })} value={binding.value === false ? "false" : "true"}>
            <option className="bg-slate-950" value="true">是</option>
            <option className="bg-slate-950" value="false">否</option>
          </select>
        ) : valueType === "number" ? (
          <input className={compactInputClass} onChange={(event) => { const value = Number(event.target.value); if (Number.isFinite(value)) onChange({ ...binding, value }); }} step="any" type="number" value={typeof binding.value === "number" ? binding.value : 0} />
        ) : valueType === "null" ? (
          <div className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-slate-400">固定为空</div>
        ) : valueType === "json" ? (
          <span className="self-center text-xs text-slate-400">在下方编辑 JSON</span>
        ) : (
          <input className={compactInputClass} onChange={(event) => onChange({ ...binding, value: event.target.value })} value={typeof binding.value === "string" ? binding.value : ""} />
        )}
      </div>
      {valueType === "json" ? (
        <>
          <textarea
            className={`${compactInputClass} min-h-24 resize-y font-mono leading-5`}
            onBlur={() => {
              try {
                onChange({ ...binding, value: JSON.parse(jsonDraft) as WorkflowValue });
                setJsonError("");
              } catch {
                setJsonError("JSON 格式无效。修正后才能保存为请求正文。");
              }
            }}
            onChange={(event) => setJsonDraft(event.target.value)}
            value={jsonDraft}
          />
          {jsonError ? <p className="text-[11px] leading-5 text-rose-200" role="alert">{jsonError}</p> : null}
        </>
      ) : null}
    </div>
  );
}

function ParameterList({
  title,
  items,
  prefix,
  node,
  nodes,
  edges,
  declarations,
  contract,
  protectedNames = false,
  onChange,
}: {
  title: string;
  items: WorkflowHttpParameter[];
  prefix: "query" | "header" | "form";
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  declarations: WorkflowVariableDeclaration[];
  contract?: WorkflowNodeContractProjection | null;
  protectedNames?: boolean;
  onChange: (items: WorkflowHttpParameter[]) => void;
}) {
  const bindingDescriptor: WorkflowVariableFieldDescriptor = {
    nodeKind: "http_request",
    field: `${prefix}Items`,
    mode: "binding",
    fallbackTypes: ["text", "number", "boolean", "json", "unknown"],
  };
  return (
    <div className="space-y-3">
      {items.map((item, index) => {
        const protectedError = protectedNames && protectedHeaders.has(item.name.trim().toLowerCase());
        return (
          <div className="space-y-2 rounded-lg border border-white/10 bg-black/10 p-3" key={item.id}>
            <div className="flex gap-2">
              <input aria-label={`${title} ${index + 1} 名称`} className={compactInputClass} onChange={(event) => onChange(items.map((candidate) => candidate.id === item.id ? { ...candidate, name: event.target.value } : candidate))} placeholder="名称" value={item.name} />
              <button aria-label={`删除${title} ${index + 1}`} className="rounded-md border border-white/15 p-2 text-slate-300 transition hover:border-rose-300/40 hover:text-rose-100 focus:outline-none focus:ring-4 focus:ring-rose-300/10" onClick={() => onChange(items.filter((candidate) => candidate.id !== item.id))} type="button"><Trash2 size={14} /></button>
            </div>
            {protectedError ? <p className="text-[11px] leading-5 text-rose-200">此请求头由运行器管理，不能手动配置。</p> : null}
            <BindingEditor binding={item.binding} contract={contract} declarations={declarations} descriptor={bindingDescriptor} edges={edges} fieldName={`${prefix}Items.${item.id}`} node={node} nodes={nodes} onChange={(binding) => onChange(items.map((candidate) => candidate.id === item.id ? { ...candidate, binding } : candidate))} />
          </div>
        );
      })}
      <button className="inline-flex items-center gap-1.5 rounded-md border border-white/15 px-2.5 py-1.5 text-xs font-medium text-slate-200 transition hover:border-white/30 hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-40" disabled={items.length >= 20} onClick={() => onChange([...items, { id: parameterId(prefix), name: "", binding: literalBinding() }])} type="button"><Plus size={13} />添加{title}</button>
    </div>
  );
}

export default function WorkflowHttpRequestNodeConfig({
  contract,
  data,
  declarations,
  edges,
  node,
  nodes,
  onChange,
  onOpenVariableCenter,
}: {
  contract?: WorkflowNodeContractProjection | null;
  data: WorkflowNodeData;
  declarations: WorkflowVariableDeclaration[];
  edges: WorkflowEdge[];
  node: WorkflowNode;
  nodes: WorkflowNode[];
  onChange: (patch: Partial<WorkflowNodeData>) => void;
  onOpenVariableCenter: () => void;
}) {
  const isV2 = String(data.contractVersion ?? "1") === "2";
  const migration = analyzeLegacyHttpMigration(data);
  const [credentials, setCredentials] = useState<CredentialSummary[]>([]);
  const [credentialError, setCredentialError] = useState("");
  const [credentialLoading, setCredentialLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [credentialName, setCredentialName] = useState("");
  const [secretValue, setSecretValue] = useState("");
  const [basicUsername, setBasicUsername] = useState("");
  const [basicPassword, setBasicPassword] = useState("");

  const loadCredentials = useCallback(async () => {
    setCredentialLoading(true);
    try {
      const response = await fetch("/api/runtime/credentials");
      if (!response.ok) throw new Error("凭据列表加载失败。");
      const payload = await response.json() as { credentials?: CredentialSummary[] };
      setCredentials((payload.credentials ?? []).filter((item) => item.status === "active"));
      setCredentialError("");
    } catch (reason) {
      setCredentialError(reason instanceof Error ? reason.message : "凭据列表加载失败。");
    } finally {
      setCredentialLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isV2 && data.authType !== "none") void loadCredentials();
  }, [data.authType, isV2, loadCredentials]);

  async function createCredential() {
    const value = data.authType === "basic"
      ? JSON.stringify({ username: basicUsername, password: basicPassword })
      : secretValue;
    if (!credentialName.trim() || !value || (data.authType === "basic" && (!basicUsername || !basicPassword))) {
      setCredentialError("填写凭据名称和完整秘密值。");
      return;
    }
    setCredentialLoading(true);
    try {
      const response = await fetch("/api/runtime/credentials", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: credentialName.trim(), kind: "generic", value }),
      });
      if (!response.ok) throw new Error("凭据保存失败。");
      const created = await response.json() as CredentialSummary;
      onChange({ credentialId: created.credential_id });
      setCredentialName("");
      setSecretValue("");
      setBasicUsername("");
      setBasicPassword("");
      setCreating(false);
      await loadCredentials();
    } catch (reason) {
      setCredentialError(reason instanceof Error ? reason.message : "凭据保存失败。");
    } finally {
      setCredentialLoading(false);
    }
  }

  if (!isV2) {
    return (
      <div className="space-y-4">
        <div className="rounded-lg border border-amber-300/25 bg-amber-300/[0.08] px-3 py-2 text-xs leading-5 text-amber-50">
          这是旧版 HTTP 节点，只保留原有模拟运行能力，不能重新发布或激活。升级会保留节点、位置和连线。
        </div>
        {migration.canMigrate ? (
          <button className="w-full rounded-lg bg-cyan-300 px-3 py-2 text-sm font-semibold text-slate-950 transition hover:bg-cyan-200 focus:outline-none focus:ring-4 focus:ring-cyan-300/20" onClick={() => onChange(migration.patch ?? {})} type="button">迁移为安全 HTTP 请求</button>
        ) : (
          <div className="rounded-lg border border-rose-300/25 bg-rose-300/[0.07] px-3 py-2 text-xs leading-5 text-rose-50" role="alert">
            <p className="font-semibold">无法无损迁移</p>
            <ul className="mt-1 list-disc space-y-1 pl-4">{migration.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
          </div>
        )}
      </div>
    );
  }

  const method = (data.method ?? "GET") as HttpRequestMethod;
  const bodyAllowed = !["GET", "DELETE"].includes(method);
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-cyan-300/20 bg-cyan-300/[0.07] px-3 py-2 text-xs leading-5 text-cyan-50">
        只允许访问公网。协议、主机和端口必须固定；每次请求和重定向都会重新检查目标地址。
      </div>
      <div className="grid gap-3 sm:grid-cols-[120px_minmax(0,1fr)]">
        <Field label="请求方法">
          <select className={inputClass} onChange={(event) => { const next = event.target.value as HttpRequestMethod; onChange({ method: next, ...(["GET", "DELETE"].includes(next) ? { bodyMode: "none", bodyBinding: undefined, formFields: [] } : {}) }); }} value={method}>
            {(["GET", "POST", "PUT", "PATCH", "DELETE"] as HttpRequestMethod[]).map((value) => <option className="bg-slate-950" key={value} value={value}>{value}</option>)}
          </select>
        </Field>
        <Field label="请求 URL" hint="路径和查询值可插入变量，主机部分不能使用变量。">
          <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="url" node={node} nodes={nodes} onChange={(value) => onChange({ url: value })} value={data.url ?? ""} />
        </Field>
      </div>
      <Section detail="适合分页、筛选和请求标记；名称固定，值可来自变量。" title="查询参数">
        <ParameterList contract={contract} declarations={declarations} edges={edges} items={data.queryItems ?? []} node={node} nodes={nodes} onChange={(queryItems) => onChange({ queryItems })} prefix="query" title="查询参数" />
      </Section>
      <Section detail="认证头、Cookie、Host 和 Content-Length 由运行器保护。" title="请求头">
        <ParameterList contract={contract} declarations={declarations} edges={edges} items={data.headerItems ?? []} node={node} nodes={nodes} onChange={(headerItems) => onChange({ headerItems })} prefix="header" protectedNames title="请求头" />
      </Section>
      <Section detail={bodyAllowed ? "选择正文格式后绑定变量或填写固定值。" : "GET 和 DELETE 不允许请求正文。"} title="请求正文">
        <Field label="正文格式">
          <select className={inputClass} disabled={!bodyAllowed} onChange={(event) => { const mode = event.target.value as WorkflowNodeData["bodyMode"]; onChange({ bodyMode: mode, bodyBinding: mode === "json" || mode === "text" ? literalBinding(mode === "json" ? {} : "") : undefined, formFields: mode === "form" ? [{ id: parameterId("form"), name: "", binding: literalBinding() }] : [] }); }} value={bodyAllowed ? data.bodyMode ?? "none" : "none"}>
            <option className="bg-slate-950" value="none">无正文</option>
            <option className="bg-slate-950" value="json">JSON</option>
            <option className="bg-slate-950" value="text">文本</option>
            <option className="bg-slate-950" value="form">表单</option>
          </select>
        </Field>
        {data.bodyMode === "json" || data.bodyMode === "text" ? <BindingEditor allowJson={data.bodyMode === "json"} binding={data.bodyBinding ?? literalBinding(data.bodyMode === "json" ? {} : "")} contract={contract} declarations={declarations} descriptor={{ nodeKind: "http_request", field: "bodyBinding", mode: "binding", fallbackTypes: ["text", "number", "boolean", "json", "unknown"] }} edges={edges} fieldName="bodyBinding" node={node} nodes={nodes} onChange={(bodyBinding) => onChange({ bodyBinding })} /> : null}
        {data.bodyMode === "form" ? <ParameterList contract={contract} declarations={declarations} edges={edges} items={data.formFields ?? []} node={node} nodes={nodes} onChange={(formFields) => onChange({ formFields })} prefix="form" title="表单字段" /> : null}
      </Section>
      <Section detail="秘密值只进入加密凭据 Store，工作流定义只保存凭据 ID。" title="认证">
        <Field label="认证方式">
          <select className={inputClass} onChange={(event) => onChange({ authType: event.target.value as WorkflowNodeData["authType"], credentialId: "" })} value={data.authType ?? "none"}>
            <option className="bg-slate-950" value="none">无需认证</option>
            <option className="bg-slate-950" value="api_key">API Key</option>
            <option className="bg-slate-950" value="bearer">Bearer Token</option>
            <option className="bg-slate-950" value="basic">Basic 用户名和密码</option>
          </select>
        </Field>
        {data.authType === "api_key" ? <div className="grid gap-2 sm:grid-cols-[120px_minmax(0,1fr)]"><select className={inputClass} onChange={(event) => onChange({ apiKeyLocation: event.target.value as "header" | "query" })} value={data.apiKeyLocation ?? "header"}><option className="bg-slate-950" value="header">请求头</option><option className="bg-slate-950" value="query">查询参数</option></select><input className={inputClass} onChange={(event) => onChange({ apiKeyName: event.target.value })} placeholder="X-API-Key" value={data.apiKeyName ?? ""} /></div> : null}
        {data.authType !== "none" ? (
          <div className="space-y-2">
            <div className="flex gap-2">
              <select className={inputClass} disabled={credentialLoading} onChange={(event) => onChange({ credentialId: event.target.value })} value={data.credentialId ?? ""}><option className="bg-slate-950" value="">选择已加密凭据</option>{credentials.map((item) => <option className="bg-slate-950" disabled={item.status !== "active"} key={item.credential_id} value={item.credential_id}>{item.name} · {item.masked_value}{item.status === "active" ? "" : ` · ${item.status === "revoked" ? "已撤销" : "不可用"}`}</option>)}</select>
              <button aria-label="刷新凭据" className="rounded-lg border border-white/15 p-2.5 text-slate-300 transition hover:border-white/30 hover:text-white focus:outline-none focus:ring-4 focus:ring-brand-300/10" disabled={credentialLoading} onClick={() => void loadCredentials()} type="button"><RefreshCw className={credentialLoading ? "animate-spin" : ""} size={16} /></button>
            </div>
            <div className="flex flex-wrap items-center gap-3 text-[11px]"><button className="font-medium text-cyan-200 underline decoration-cyan-300/30 underline-offset-4 hover:text-cyan-100" onClick={() => setCreating((value) => !value)} type="button">{creating ? "取消创建凭据" : "创建一次性凭据"}</button><Link className="inline-flex items-center gap-1 text-slate-300 hover:text-white" to="/toolsets">打开凭据中心<ExternalLink size={12} /></Link></div>
            {creating ? <div className="space-y-2 rounded-lg border border-white/10 bg-black/10 p-3"><input className={compactInputClass} onChange={(event) => setCredentialName(event.target.value)} placeholder="凭据名称" value={credentialName} />{data.authType === "basic" ? <div className="grid gap-2 sm:grid-cols-2"><input autoComplete="username" className={compactInputClass} onChange={(event) => setBasicUsername(event.target.value)} placeholder="用户名" value={basicUsername} /><input autoComplete="new-password" className={compactInputClass} onChange={(event) => setBasicPassword(event.target.value)} placeholder="密码" type="password" value={basicPassword} /></div> : <input autoComplete="off" className={compactInputClass} onChange={(event) => setSecretValue(event.target.value)} placeholder="秘密值，只保存一次" type="password" value={secretValue} />}<button className="rounded-md bg-cyan-300 px-3 py-1.5 text-xs font-semibold text-slate-950 transition hover:bg-cyan-200 disabled:opacity-50" disabled={credentialLoading} onClick={() => void createCredential()} type="button">加密保存并选用</button></div> : null}
            {credentialError ? <p className="text-[11px] leading-5 text-rose-200" role="alert">{credentialError}</p> : null}
          </div>
        ) : null}
      </Section>
      <Section detail="非 2xx 默认停止工作流；捕获全部状态时可交给后续条件节点判断。" title="响应与失败策略">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="状态策略"><select className={inputClass} onChange={(event) => onChange({ statusPolicy: event.target.value as WorkflowNodeData["statusPolicy"] })} value={data.statusPolicy ?? "success_only"}><option className="bg-slate-950" value="success_only">仅 2xx 成功</option><option className="bg-slate-950" value="capture_all">捕获全部 HTTP 状态</option></select></Field>
          <Field label="响应解析"><select className={inputClass} onChange={(event) => onChange({ responseMode: event.target.value as WorkflowNodeData["responseMode"] })} value={data.responseMode ?? "auto"}><option className="bg-slate-950" value="auto">自动识别</option><option className="bg-slate-950" value="json">必须为 JSON</option><option className="bg-slate-950" value="text">按文本读取</option></select></Field>
          <Field label="超时（秒）"><input className={inputClass} max={60} min={1} onChange={(event) => onChange({ timeoutSeconds: Number(event.target.value) })} type="number" value={Number(data.timeoutSeconds ?? 30)} /></Field>
          <Field label="同源重定向次数"><input className={inputClass} max={3} min={0} onChange={(event) => onChange({ redirectLimit: Number(event.target.value) })} type="number" value={Number(data.redirectLimit ?? 0)} /></Field>
          <Field label="响应上限"><select className={inputClass} onChange={(event) => onChange({ responseLimitBytes: Number(event.target.value) })} value={Number(data.responseLimitBytes ?? 1_048_576)}><option className="bg-slate-950" value={1_024}>1 KiB</option><option className="bg-slate-950" value={65_536}>64 KiB</option><option className="bg-slate-950" value={262_144}>256 KiB</option><option className="bg-slate-950" value={1_048_576}>1 MiB</option><option className="bg-slate-950" value={2_097_152}>2 MiB</option></select></Field>
        </div>
      </Section>
      <Field label="结构化响应变量" hint="包含 statusCode、ok、contentType、headers、receivedBytes 和 body。">
        <WorkflowVariableField contract={contract} declarations={declarations} edges={edges} fieldName="outputVariable" node={node} nodes={nodes} onChange={(value) => onChange({ outputVariable: value })} value={data.outputVariable ?? ""} />
      </Field>
      <WorkflowFailureRoutingConfig
        contract={contract}
        data={data}
        declarations={declarations}
        edges={edges}
        node={node}
        nodes={nodes}
        onChange={onChange}
        onOpenVariableCenter={onOpenVariableCenter}
      />
      <div className="flex justify-end"><button className="text-[11px] font-medium text-cyan-200 underline decoration-cyan-300/30 underline-offset-4 hover:text-cyan-100" onClick={onOpenVariableCenter} type="button">管理全局变量</button></div>
    </div>
  );
}
