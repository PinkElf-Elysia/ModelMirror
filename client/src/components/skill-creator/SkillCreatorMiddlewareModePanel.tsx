export default function SkillCreatorMiddlewareModePanel({
  legacy,
  onUpgrade,
}: {
  legacy: boolean;
  onUpgrade: () => void;
}) {
  if (legacy) {
    return (
      <div className="rounded-lg border border-amber-300/30 bg-amber-300/10 p-3 text-xs leading-5 text-amber-50">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-amber-200/30 bg-amber-200/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-100">
            Legacy
          </span>
          <p className="font-semibold">此节点仍使用旧的一次性提案路径</p>
        </div>
        <p className="mt-2 text-amber-100/80">
          旧路径依赖 Runtime 工具模式，生成质量与 Creator 质量门不一致。升级只修改当前画布节点，不会迁移其他工作流。
        </p>
        <button
          className="mt-3 min-h-10 rounded-full border border-amber-200/35 bg-amber-200/10 px-3 text-xs font-semibold text-amber-50 transition hover:bg-amber-200/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-200/50"
          onClick={onUpgrade}
          type="button"
        >
          确认升级为 Creator V2
        </button>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-indigo-300/25 bg-indigo-300/10 p-3 text-xs leading-5 text-indigo-50">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full border border-indigo-200/30 bg-indigo-200/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-indigo-100">
          Creator V2
        </span>
        <p className="font-semibold">分析需求并创建 Creator 会话</p>
      </div>
      <p className="mt-2 text-indigo-100/80">
        工作流只做需求梳理，不会直接生成或安装 Skill。完成后，用户会在 Creator 中检查分析、确认素材并生成方案。请用紫色端口绑定一个 workflow_agent；V2 不要求工具模式。
      </p>
    </div>
  );
}
