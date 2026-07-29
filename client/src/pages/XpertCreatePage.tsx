import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import { createXpert } from "../utils/xpertApi";

function splitLines(value: string) {
  return value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
}

export default function XpertCreatePage() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [starters, setStarters] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    document.title = "模镜 - 创建智能体";
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setSubmitting(true);
    setError("");
    try {
      const created = await createXpert({
        name: name.trim(),
        slug: slug.trim() || undefined,
        description: description.trim(),
        tags: splitLines(tags),
        starters: starters.split("\n").map((item) => item.trim()).filter(Boolean),
      });
      navigate(`/agents/studio/${created.id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "智能体创建失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <PageContainer activeResource="agents" maxWidthClassName="max-w-[1200px]">
      <div className="mb-5 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold text-hire-100">Agent Studio</p>
          <h1 className="mt-2 text-2xl font-semibold text-white">创建智能体</h1>
        </div>
        <Link className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-slate-300" to="/agents/studio">
          返回列表
        </Link>
      </div>

      <form className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]" onSubmit={(event) => void submit(event)}>
        <section className="space-y-5 rounded-lg border border-white/10 bg-ink-950/72 p-5">
          <label className="block">
            <span className="text-xs font-semibold text-slate-300">名称 *</span>
            <input className="mt-2 h-11 w-full rounded-lg border border-white/10 bg-white/[0.055] px-3 text-sm text-white outline-none focus:border-hire-300/60" maxLength={120} onChange={(event) => setName(event.target.value)} placeholder="例如：研究计划助手" value={name} />
          </label>
          <label className="block">
            <span className="text-xs font-semibold text-slate-300">Slug</span>
            <input className="mt-2 h-11 w-full rounded-lg border border-white/10 bg-white/[0.055] px-3 text-sm text-white outline-none focus:border-hire-300/60" maxLength={64} onChange={(event) => setSlug(event.target.value)} placeholder="留空自动生成；仅小写字母、数字、-、_" value={slug} />
          </label>
          <label className="block">
            <span className="text-xs font-semibold text-slate-300">说明</span>
            <textarea className="mt-2 min-h-28 w-full resize-y rounded-lg border border-white/10 bg-white/[0.055] px-3 py-2 text-sm leading-6 text-white outline-none focus:border-hire-300/60" maxLength={2000} onChange={(event) => setDescription(event.target.value)} placeholder="说明这个智能体解决什么任务、适合谁使用。" value={description} />
          </label>
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <span className="text-xs font-semibold text-slate-300">标签</span>
              <textarea className="mt-2 min-h-24 w-full resize-y rounded-lg border border-white/10 bg-white/[0.055] px-3 py-2 text-sm text-white outline-none focus:border-hire-300/60" onChange={(event) => setTags(event.target.value)} placeholder="研究, 规划, 团队" value={tags} />
            </label>
            <label className="block">
              <span className="text-xs font-semibold text-slate-300">开场问题</span>
              <textarea className="mt-2 min-h-24 w-full resize-y rounded-lg border border-white/10 bg-white/[0.055] px-3 py-2 text-sm text-white outline-none focus:border-hire-300/60" onChange={(event) => setStarters(event.target.value)} placeholder={"每行一个，例如：\n帮我拆解这个项目"} value={starters} />
            </label>
          </div>
          {error ? <p className="rounded-lg border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-sm text-rose-100">{error}</p> : null}
          <button className="rounded-full bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-60" disabled={submitting || !name.trim()} type="submit">
            {submitting ? "创建中..." : "创建并进入 Studio"}
          </button>
        </section>

        <aside className="rounded-lg border border-white/10 bg-white/[0.045] p-4">
          <h2 className="text-sm font-semibold text-white">默认执行内核</h2>
          <p className="mt-2 text-xs leading-5 text-slate-400">创建后会得到一条可直接编辑和发布的默认链路。</p>
          <ol className="mt-4 space-y-3 text-xs text-slate-300">
            <li className="rounded-lg border border-white/10 bg-ink-950/55 px-3 py-2">1. input(user_input)</li>
            <li className="rounded-lg border border-hire-300/20 bg-hire-300/10 px-3 py-2 text-hire-100">2. workflow_agent(agent_output)</li>
            <li className="rounded-lg border border-white/10 bg-ink-950/55 px-3 py-2">3. output(agent_output)</li>
          </ol>
          <p className="mt-4 text-xs leading-5 text-slate-500">发布前会执行变量、聊天契约和节点能力预检。草稿修改不会影响已经发布的版本。</p>
        </aside>
      </form>
    </PageContainer>
  );
}
