import { useEffect, useState } from "react";

import type {
  WorkflowClassifierCategory,
  WorkflowEdge,
  WorkflowExtractorField,
  WorkflowNode,
  WorkflowNodeData,
  WorkflowVariableDeclaration,
} from "../../types/workflow";
import WorkflowVariableField from "./WorkflowVariableField";
import type { WorkflowNodeContractProjection } from "./workflowNodeRegistry";
import { nextStableId } from "./workflowTypedAiMigration";

const controlClass =
  "modelmirror-form-control w-full rounded-lg border border-white/10 bg-[#0f1728] px-3 py-2 text-sm text-white outline-none transition placeholder:text-slate-500 hover:border-white/20 focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10";

function defaultExtractorSchema(outputShape: "object" | "object_list") {
  const item = { type: "object", properties: {}, additionalProperties: false };
  return outputShape === "object_list"
    ? { type: "array", items: item }
    : item;
}

function isEmptyExtractorSchema(schema: unknown) {
  return Boolean(
    schema
    && typeof schema === "object"
    && !Array.isArray(schema)
    && Object.keys(schema).length === 0,
  );
}

function isStarterExtractorSchema(schema: unknown) {
  const serialized = JSON.stringify(schema);
  return serialized === JSON.stringify(defaultExtractorSchema("object"))
    || serialized === JSON.stringify(defaultExtractorSchema("object_list"));
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs font-semibold text-slate-300">{label}</span>
      <div className="mt-2">{children}</div>
    </label>
  );
}

function Notice({ text }: { text: string }) {
  return text ? (
    <p className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-50" role="status">
      {text}
    </p>
  ) : null;
}

interface CommonProps {
  contract: WorkflowNodeContractProjection | null;
  data: WorkflowNodeData;
  declarations: WorkflowVariableDeclaration[];
  edges: WorkflowEdge[];
  models: Array<{ id: string; name: string }>;
  node: WorkflowNode;
  nodes: WorkflowNode[];
  onChange: (patch: Partial<WorkflowNodeData>) => void;
  onMigrate: () => string;
  onOpenVariableCenter: () => void;
}

function VariableField({
  fieldName,
  value,
  props,
}: {
  fieldName: string;
  value: string;
  props: CommonProps;
}) {
  return (
    <WorkflowVariableField
      contract={props.contract}
      declarations={props.declarations}
      edges={props.edges}
      fieldName={fieldName}
      node={props.node}
      nodes={props.nodes}
      onChange={(next) => props.onChange({ [fieldName]: next })}
      value={value}
    />
  );
}

function LegacyPanel({
  children,
  onMigrate,
}: {
  children: React.ReactNode;
  onMigrate: () => string;
}) {
  const [notice, setNotice] = useState("");
  return (
    <>
      <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-3 text-xs leading-5 text-amber-50">
        这是旧版配置，仍可打开、运行和发布。升级会先检查是否能无损转换，不会自动改写。
        <button
          className="mt-2 block rounded-md border border-amber-200/30 bg-amber-200/10 px-3 py-1.5 font-semibold text-amber-50 transition hover:bg-amber-200/20"
          onClick={() => setNotice(onMigrate())}
          type="button"
        >
          显式升级到 V2
        </button>
      </div>
      <Notice text={notice} />
      {children}
    </>
  );
}

export function ParameterExtractorConfig(props: CommonProps) {
  const { data, onChange } = props;
  const version2 = Number(data.contractVersion) === 2;
  const fields = data.fields ?? [];
  const [schemaText, setSchemaText] = useState(() =>
    JSON.stringify(
      data.jsonSchema ?? defaultExtractorSchema(data.outputShape ?? "object"),
      null,
      2,
    ),
  );
  const [schemaError, setSchemaError] = useState("");
  useEffect(() => {
    setSchemaText(JSON.stringify(
      data.jsonSchema ?? defaultExtractorSchema(data.outputShape ?? "object"),
      null,
      2,
    ));
    setSchemaError("");
  }, [props.node.id]);

  const updateField = (id: string, patch: Partial<WorkflowExtractorField>) =>
    onChange({
      fields: fields.map((field) => field.id === id ? { ...field, ...patch } : field),
    });
  const addField = () => {
    const id = nextStableId("field", fields.map((field) => field.id), 50);
    if (!id) return;
    const existingNames = new Set(fields.map((field) => field.name));
    let name = id;
    for (let index = 1; existingNames.has(name); index += 1) {
      name = `extracted_${index}`;
    }
    onChange({ fields: [...fields, {
      id,
      name,
      description: "",
      valueType: "string",
      required: true,
      nullable: false,
    }] });
  };

  return (
    <div className="space-y-4">
      {!version2 ? (
        <LegacyPanel onMigrate={props.onMigrate}>
          <Field label="待提取文本变量">
            <VariableField fieldName="inputVariable" props={props} value={data.inputVariable ?? ""} />
          </Field>
          <Field label="字段描述（旧版）">
            <textarea className={`${controlClass} min-h-28 resize-none`} onChange={(event) => onChange({ schema: event.target.value })} value={data.schema ?? ""} />
          </Field>
        </LegacyPanel>
      ) : (
        <>
          <div className="flex items-center justify-between rounded-lg border border-cyan-300/20 bg-cyan-300/[0.07] px-3 py-2 text-xs text-cyan-50">
            <span>V2 会输出经过完整 Schema 校验的真实 JSON 值。</span>
            <button className="font-semibold underline underline-offset-4" onClick={props.onOpenVariableCenter} type="button">变量中心</button>
          </div>
          <Field label="待提取文本变量">
            <VariableField fieldName="inputVariable" props={props} value={data.inputVariable ?? ""} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="配置方式">
              <select className={controlClass} onChange={(event) => {
                const schemaMode = event.target.value as "fields" | "json_schema";
                if (schemaMode === "json_schema" && (!data.jsonSchema || isEmptyExtractorSchema(data.jsonSchema))) {
                  const jsonSchema = defaultExtractorSchema(data.outputShape ?? "object");
                  setSchemaText(JSON.stringify(jsonSchema, null, 2));
                  setSchemaError("");
                  onChange({ schemaMode, jsonSchema });
                  return;
                }
                onChange({ schemaMode });
              }} value={data.schemaMode ?? "fields"}>
                <option className="bg-slate-950" value="fields">字段表</option>
                <option className="bg-slate-950" value="json_schema">高级 JSON Schema</option>
              </select>
            </Field>
            <Field label="输出形状">
              <select className={controlClass} onChange={(event) => {
                const outputShape = event.target.value as "object" | "object_list";
                if ((data.schemaMode ?? "fields") === "json_schema"
                  && (isEmptyExtractorSchema(data.jsonSchema) || isStarterExtractorSchema(data.jsonSchema))) {
                  const jsonSchema = defaultExtractorSchema(outputShape);
                  setSchemaText(JSON.stringify(jsonSchema, null, 2));
                  setSchemaError("");
                  onChange({ outputShape, jsonSchema });
                  return;
                }
                onChange({ outputShape });
              }} value={data.outputShape ?? "object"}>
                <option className="bg-slate-950" value="object">单个对象</option>
                <option className="bg-slate-950" value="object_list">对象列表</option>
              </select>
            </Field>
          </div>
          {(data.schemaMode ?? "fields") === "fields" ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold text-slate-300">提取字段（{fields.length}/50）</p>
                <button className="rounded-md border border-white/15 px-2.5 py-1 text-xs text-slate-200 hover:bg-white/10" disabled={fields.length >= 50} onClick={addField} type="button">添加字段</button>
              </div>
              {fields.map((field) => (
                <div className="space-y-2 rounded-lg border border-white/10 bg-white/[0.03] p-3" key={field.id}>
                  <div className="flex items-center justify-between text-[11px] text-slate-500"><span>{field.id}</span><button className="text-rose-200 disabled:opacity-40" disabled={fields.length <= 1} onClick={() => onChange({ fields: fields.filter((item) => item.id !== field.id) })} type="button">删除</button></div>
                  <input aria-label={`${field.id} 字段名`} className={controlClass} maxLength={64} onChange={(event) => updateField(field.id, { name: event.target.value })} placeholder="字段名，例如 order_id" value={field.name} />
                  <textarea aria-label={`${field.id} 字段说明`} className={`${controlClass} min-h-16 resize-none`} maxLength={500} onChange={(event) => updateField(field.id, { description: event.target.value })} placeholder="用自然语言说明要提取的内容" value={field.description} />
                  <div className="grid grid-cols-2 gap-2">
                    <select aria-label={`${field.id} 字段类型`} className={controlClass} onChange={(event) => updateField(field.id, { valueType: event.target.value as WorkflowExtractorField["valueType"] })} value={field.valueType}>
                      <option className="bg-slate-950" value="string">文本</option><option className="bg-slate-950" value="number">数字</option><option className="bg-slate-950" value="boolean">是/否</option><option className="bg-slate-950" value="string_array">文本列表</option><option className="bg-slate-950" value="number_array">数字列表</option>
                    </select>
                    <div className="flex items-center gap-3 rounded-lg border border-white/10 px-3 text-xs text-slate-300">
                      <label><input checked={field.required} className="mr-1" onChange={(event) => updateField(field.id, { required: event.target.checked })} type="checkbox" />必填</label>
                      <label><input checked={field.nullable} className="mr-1" onChange={(event) => updateField(field.id, { nullable: event.target.checked })} type="checkbox" />可为空</label>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Field label="JSON Schema（最大 64 KiB）">
              <textarea
                className={`${controlClass} min-h-48 resize-y font-mono text-xs`}
                onChange={(event) => {
                  const text = event.target.value;
                  setSchemaText(text);
                  try {
                    const parsed = JSON.parse(text) as unknown;
                    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error();
                    setSchemaError("");
                    onChange({ jsonSchema: parsed as Record<string, unknown> });
                  } catch {
                    setSchemaError("当前内容不是 JSON 对象；修正前不会覆盖已保存的 Schema。 ");
                  }
                }}
                value={schemaText}
              />
              <Notice text={schemaError} />
              <p className="mt-2 text-[11px] leading-5 text-slate-500">根类型必须与输出形状一致：单个对象使用 object，对象列表使用 array 且 items 为 object。</p>
            </Field>
          )}
        </>
      )}
      <Field label="调用模型">
        <select className={controlClass} onChange={(event) => onChange({ modelId: event.target.value })} value={data.modelId ?? ""}>
          <option className="bg-slate-950" value="">请选择模型</option>
          {props.models.map((model) => <option className="bg-slate-950" key={model.id} value={model.id}>{model.name}</option>)}
        </select>
      </Field>
      {version2 ? (
        <Field label="校验失败时修复">
          <select className={controlClass} onChange={(event) => onChange({ repairAttempts: Number(event.target.value) })} value={Number(data.repairAttempts ?? 0)}>
            <option className="bg-slate-950" value={0}>不追加调用（默认）</option>
            <option className="bg-slate-950" value={1}>最多追加一次同模型修复</option>
          </select>
        </Field>
      ) : null}
      <Field label="输出变量">
        <VariableField fieldName="outputVariable" props={props} value={data.outputVariable ?? ""} />
      </Field>
    </div>
  );
}

export function QuestionClassifierConfig(props: CommonProps) {
  const { data, edges, node, onChange } = props;
  const version2 = Number(data.contractVersion) === 2;
  const categories = data.categoriesV2 ?? [];
  const [notice, setNotice] = useState("");
  const mode = data.classificationMode ?? "rules_only";
  const updateCategory = (id: string, patch: Partial<WorkflowClassifierCategory>) =>
    onChange({ categoriesV2: categories.map((category) => category.id === id ? { ...category, ...patch } : category) });
  const move = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= categories.length) return;
    const next = [...categories];
    [next[index], next[target]] = [next[target], next[index]];
    onChange({ categoriesV2: next });
  };
  const remove = (category: WorkflowClassifierCategory) => {
    if (edges.some((edge) => edge.source === node.id && edge.sourceHandle === category.id)) {
      setNotice(`出口 ${category.id} 仍有连线，请先删除连线再删除类别。`);
      return;
    }
    if (categories.length <= 2) {
      setNotice("V2 分类器至少需要两个类别。");
      return;
    }
    setNotice("");
    onChange({ categoriesV2: categories.filter((item) => item.id !== category.id) });
  };
  const add = () => {
    const id = nextStableId("category", categories.map((category) => category.id), 8);
    if (!id) return;
    const baseLabel = `分类 ${id.split("_")[1]}`;
    const label = categories.some((category) => category.label === baseLabel)
      ? `${baseLabel}（新）`
      : baseLabel;
    onChange({ categoriesV2: [...categories, { id, label, description: "", keywords: [], matchMode: "contains_any" }] });
  };

  return (
    <div className="space-y-4">
      {!version2 ? (
        <LegacyPanel onMigrate={props.onMigrate}>
          <Field label="输入文本变量"><VariableField fieldName="inputVariable" props={props} value={data.inputVariable ?? ""} /></Field>
          <Field label="分类规则 JSON"><textarea className={`${controlClass} min-h-36 resize-none font-mono text-xs`} onChange={(event) => onChange({ categories: event.target.value })} value={data.categories ?? ""} /></Field>
          <Field label="未命中时的类别"><input className={controlClass} onChange={(event) => onChange({ defaultCategory: event.target.value })} value={data.defaultCategory ?? "未分类"} /></Field>
          <Field label="关键词匹配方式">
            <select className={controlClass} onChange={(event) => onChange({ matchMode: event.target.value })} value={data.matchMode ?? "contains_any"}>
              <option className="bg-slate-950" value="contains_any">任一关键词</option>
              <option className="bg-slate-950" value="contains_all">全部关键词</option>
            </select>
          </Field>
          <label className="flex items-center gap-2 text-xs text-slate-300"><input checked={data.caseSensitive === true || data.caseSensitive === "true"} onChange={(event) => onChange({ caseSensitive: event.target.checked })} type="checkbox" />关键词区分大小写</label>
          <label className="flex items-center gap-2 text-xs text-slate-300"><input checked={data.useLlmFallback === true || data.useLlmFallback === "true"} onChange={(event) => onChange({ useLlmFallback: event.target.checked })} type="checkbox" />规则未命中时调用模型</label>
          {data.useLlmFallback === true || data.useLlmFallback === "true" ? (
            <>
              <Field label="回退模型">
                <select className={controlClass} onChange={(event) => onChange({ modelId: event.target.value })} value={data.modelId ?? ""}>
                  <option className="bg-slate-950" value="">请选择模型</option>
                  {props.models.map((model) => <option className="bg-slate-950" key={model.id} value={model.id}>{model.name}</option>)}
                </select>
              </Field>
              <Field label="回退分类说明"><textarea className={`${controlClass} min-h-20 resize-none`} onChange={(event) => onChange({ llmFallbackPrompt: event.target.value })} value={data.llmFallbackPrompt ?? ""} /></Field>
            </>
          ) : null}
        </LegacyPanel>
      ) : (
        <>
          <div className="flex items-center justify-between rounded-lg border border-violet-300/20 bg-violet-300/[0.07] px-3 py-2 text-xs text-violet-50">
            <span>按顺序首个命中；类别 ID 与出口连线保持稳定。仅规则模式下每类至少填写一个关键词。</span>
            <button className="font-semibold underline underline-offset-4" onClick={props.onOpenVariableCenter} type="button">变量中心</button>
          </div>
          <Notice text={notice} />
          <Field label="输入文本变量"><VariableField fieldName="inputVariable" props={props} value={data.inputVariable ?? ""} /></Field>
          <Field label="分类方式">
            <select className={controlClass} onChange={(event) => onChange({ classificationMode: event.target.value as WorkflowNodeData["classificationMode"] })} value={mode}>
              <option className="bg-slate-950" value="rules_only">只使用关键词规则</option>
              <option className="bg-slate-950" value="rules_then_model">规则未命中时调用模型</option>
              <option className="bg-slate-950" value="model_only">只使用模型</option>
            </select>
          </Field>
          {mode !== "rules_only" ? (
            <Field label="分类模型">
              <select className={controlClass} onChange={(event) => onChange({ modelId: event.target.value })} value={data.modelId ?? ""}>
                <option className="bg-slate-950" value="">请选择模型</option>
                {props.models.map((model) => <option className="bg-slate-950" key={model.id} value={model.id}>{model.name}</option>)}
              </select>
            </Field>
          ) : null}
          <label className="flex items-center gap-2 text-xs text-slate-300"><input checked={data.caseSensitive === true} onChange={(event) => onChange({ caseSensitive: event.target.checked })} type="checkbox" />关键词区分大小写</label>
          <div className="flex items-center justify-between"><p className="text-xs font-semibold text-slate-300">类别（{categories.length}/8）</p><button className="rounded-md border border-white/15 px-2.5 py-1 text-xs text-slate-200 hover:bg-white/10" disabled={categories.length >= 8} onClick={add} type="button">添加类别</button></div>
          {categories.map((category, index) => (
            <div className="space-y-2 rounded-lg border border-white/10 bg-white/[0.03] p-3" key={category.id}>
              <div className="flex items-center justify-between text-[11px] text-slate-500"><span>{category.id}</span><div className="flex gap-2"><button disabled={index === 0} onClick={() => move(index, -1)} type="button">上移</button><button disabled={index === categories.length - 1} onClick={() => move(index, 1)} type="button">下移</button><button className="text-rose-200" onClick={() => remove(category)} type="button">删除</button></div></div>
              <input aria-label={`${category.id} 标签`} className={controlClass} onChange={(event) => updateCategory(category.id, { label: event.target.value })} placeholder="用户可见标签" value={category.label} />
              <textarea aria-label={`${category.id} 描述`} className={`${controlClass} min-h-16 resize-none`} onChange={(event) => updateCategory(category.id, { description: event.target.value })} placeholder="模型分类时使用的简短说明" value={category.description} />
              <textarea aria-label={`${category.id} 关键词`} className={`${controlClass} min-h-16 resize-none`} onChange={(event) => updateCategory(category.id, { keywords: event.target.value.split(/[,\n]+/).map((item) => item.trim()).filter(Boolean) })} placeholder="关键词，逗号或换行分隔" value={category.keywords.join("\n")} />
              <select aria-label={`${category.id} 匹配模式`} className={controlClass} onChange={(event) => updateCategory(category.id, { matchMode: event.target.value as WorkflowClassifierCategory["matchMode"] })} value={category.matchMode}><option className="bg-slate-950" value="contains_any">任一关键词</option><option className="bg-slate-950" value="contains_all">全部关键词</option></select>
            </div>
          ))}
          <Field label="默认出口标签"><input className={controlClass} onChange={(event) => onChange({ defaultLabel: event.target.value })} value={data.defaultLabel ?? "未分类"} /></Field>
        </>
      )}
      <Field label="输出变量"><VariableField fieldName="outputVariable" props={props} value={data.outputVariable ?? ""} /></Field>
      {!version2 ? (
        <p className="text-xs leading-5 text-slate-500">旧版继续使用单出口和类别名称字符串，不受 V2 稳定出口规则影响。</p>
      ) : null}
    </div>
  );
}
