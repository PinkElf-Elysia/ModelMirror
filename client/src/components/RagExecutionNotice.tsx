export type RagExecutionMode = "managed" | "legacy" | "local_non_model";

export function RagExecutionNotice({
  executionMode,
}: {
  executionMode?: RagExecutionMode;
}) {
  if (executionMode !== "local_non_model") return null;

  return (
    <div
      className="mb-3 rounded-lg border border-amber-300/30 bg-amber-300/10 px-3 py-2 text-amber-100"
      role="status"
    >
      <p className="text-xs font-semibold">本地非模型降级</p>
      <p className="mt-1 text-xs leading-5">
        Managed Provider 调用未成功，已按控制面策略使用本地抽取式结果；这不代表模型调用成功。
      </p>
    </div>
  );
}
