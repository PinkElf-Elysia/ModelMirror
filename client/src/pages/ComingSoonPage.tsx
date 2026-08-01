import { useEffect } from "react";
import ComingSoon from "../components/ComingSoon";
import PageContainer from "../components/PageContainer";
import {
  resourceComingSoonCopy,
  resourceNavItems,
  type ResourceKey,
} from "../theme/resources";

interface ComingSoonPageProps {
  resource: Exclude<ResourceKey, "models" | "agents" | "runtime">;
}

export default function ComingSoonPage({ resource }: ComingSoonPageProps) {
  const item = resourceNavItems.find((navItem) => navItem.key === resource);
  const copy = resourceComingSoonCopy[resource];

  useEffect(() => {
    document.title = `模镜 - ${item?.title ?? "新展区"} 即将上线`;
  }, [item?.title]);

  return (
    <PageContainer
      activeResource={resource}
      sidebar={
        <div>
          <p className="text-sm font-semibold text-white">展区预留位</p>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            这里未来可以承载该资源类型自己的快捷筛选、收藏夹和最近浏览。
          </p>
          <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-xs text-slate-400">当前状态</p>
            <p className="mt-1 text-sm font-semibold text-hire-100">
              即将开张
            </p>
          </div>
        </div>
      }
    >
      <ComingSoon
        actionHint={copy.actionHint}
        description={copy.description}
        icon={item?.icon ?? "新"}
        title={copy.title}
      />
    </PageContainer>
  );
}
