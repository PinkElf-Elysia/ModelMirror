import { ArrowLeft, FolderPlus, LoaderCircle } from "lucide-react";
import { FormEvent, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError, api } from "../api";
import { ErrorNotice, PageHeader } from "../components/Page";

export function ProjectNewPage() {
  const navigate = useNavigate();
  const idempotencyKey = useRef<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    const form = new FormData(event.currentTarget);
    const title = String(form.get("title") ?? "").trim();
    const researchQuestion = String(form.get("researchQuestion") ?? "").trim();
    setSubmitting(true);
    setError(null);
    idempotencyKey.current ??= `project:${crypto.randomUUID()}`;
    try {
      const project = await api.createProject(title, researchQuestion, idempotencyKey.current);
      idempotencyKey.current = null;
      navigate(`/projects/${project.projectId}`);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "创建请求未完成，请使用当前表单重试。 ");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="page page-narrow">
      <PageHeader eyebrow="New Research Project" title="创建研究项目" description="先定义一个明确的 AI/Agent 研究问题。本轮仅开放文献研究阶段，项目创建不会自动调用模型。" />
      <form className="section grid gap-5" onSubmit={submit} onChange={() => { idempotencyKey.current = null; }}>
        <label className="field-label">项目标题<input className="field" name="title" required maxLength={120} placeholder="例如：Agent 评测可复现性研究" /></label>
        <label className="field-label">研究问题<textarea className="field min-h-44 resize-y" name="researchQuestion" required maxLength={5000} placeholder="明确对象、范围与希望从文献中回答的问题。" /></label>
        <div className="constraint-panel">
          <p className="font-semibold text-[#dbe4e7]">固定研究配置</p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-[var(--muted)]"><li>领域固定为 AI / Agent</li><li>主检索使用 OpenAlex，学术工具限 OpenAlex、arXiv、Semantic Scholar</li><li>结果保留上游原文，scientificClaim=none</li></ul>
        </div>
        {error ? <ErrorNotice message={error} /> : null}
        <div className="flex flex-wrap justify-end gap-2 border-t border-[var(--border)] pt-4">
          <Link className="button" to="/projects"><ArrowLeft size={16} />返回项目</Link>
          <button className="button button-primary" type="submit" disabled={submitting}>{submitting ? <LoaderCircle className="animate-spin" size={16} /> : <FolderPlus size={16} />}{submitting ? "正在创建" : "创建项目"}</button>
        </div>
      </form>
    </div>
  );
}
