import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

interface ProposalListResponse {
  total: number;
}

export default function AuthoringProposalNotice({
  sourceId,
  sourceXpertId,
}: {
  sourceId?: string;
  sourceXpertId?: string;
}) {
  const [count, setCount] = useState(0);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    if (!sourceId && !sourceXpertId) return;
    const controller = new AbortController();
    const params = new URLSearchParams({ status: "pending", limit: "100" });
    if (sourceId) params.set("source_id", sourceId);
    if (sourceXpertId) params.set("source_xpert_id", sourceXpertId);
    fetch(`/api/runtime/authoring-proposals?${params}`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("proposal summary unavailable");
        return (await response.json()) as ProposalListResponse;
      })
      .then((payload) => {
        setCount(payload.total ?? 0);
        setUnavailable(false);
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setUnavailable(true);
      });
    return () => controller.abort();
  }, [sourceId, sourceXpertId]);

  if (unavailable || count === 0) return null;

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-violet-300/20 bg-violet-300/[0.07] px-3 py-2 text-xs">
      <span className="text-violet-100">
        有 {count} 条自编写提案等待人工审核，尚未写入草稿或安装。
      </span>
      <span className="flex items-center gap-2">
        <Link className="font-semibold text-violet-100 hover:text-white" to="/agents/studio">
          智能体提案
        </Link>
        <Link className="font-semibold text-violet-100 hover:text-white" to="/skills?tab=proposals">
          Skill 提案
        </Link>
      </span>
    </div>
  );
}
