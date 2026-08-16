import { useEffect, useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import WorkflowEditor from "../components/workflow/WorkflowEditor";

function workflowIdFromParam(value: string | undefined) {
  if (!value || value === "new") return "draft";
  return value;
}

export default function WorkflowClassicPage() {
  const { id } = useParams();
  const workflowId = useMemo(() => workflowIdFromParam(id), [id]);

  useEffect(() => {
    document.title = "模镜 - 经典工作流";
  }, []);

  return (
    <PageContainer
      activeResource="agents"
      maxWidthClassName="max-w-[1760px]"
      sidebar={
        <div>
          <p className="text-sm font-semibold text-white">经典画布</p>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            这里保留模镜最初的 React Flow 工作流编辑器，适合草拟流程、做低成本原型，或者和新的应用工坊并排参考。
          </p>
          <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-xs text-slate-400">当前草稿</p>
            <p className="mt-1 text-sm font-semibold text-hire-100">{workflowId}</p>
          </div>
          <Link
            className="mt-4 inline-flex rounded-full border border-hire-300/30 bg-hire-300/10 px-3 py-1.5 text-xs font-semibold text-hire-100 transition hover:bg-hire-300/20"
            to="/workflow"
          >
            打开工作流
          </Link>
        </div>
      }
    >
      <header className="mb-6 overflow-hidden rounded-lg border border-white/10 bg-[#0d1424] p-6 shadow-md">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-sm font-semibold text-hire-100">经典工作流模式</p>
            <h1 className="mt-3 text-3xl font-semibold text-white sm:text-4xl">
              模镜本地草稿编辑器
            </h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300">
              这个页面保留旧版拖拽连线编辑器，方便你继续做本地原型，或拿来对照新式应用工坊的布局与配置。
            </p>
          </div>
          <Link
            className="w-fit rounded-full border border-white/10 bg-white/[0.045] px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
            to="/workflow"
          >
            打开工作流
          </Link>
        </div>
      </header>

      <WorkflowEditor workflowId={workflowId} />
    </PageContainer>
  );
}

