import { Link } from "react-router-dom";
import FeaturedModelCard from "./FeaturedModelCard";

export const federationRouteId = "model-federation";
export const federationFallbackModelId = "openai/gpt-5.6-sol";

export default function FederationRouterCard() {
  return (
    <FeaturedModelCard
      badge="平台推荐"
      description="根据本轮任务、预算和已验证能力选择合适模型，不固定供应商。"
      footerAction={
          <Link
            className="inline-flex min-h-9 items-center justify-center rounded-md border border-hire-300/55 bg-hire-300/10 px-3 text-sm font-semibold text-hire-100 transition hover:bg-hire-300/18"
            to="/chat/auto"
          >
            交给智能路由
          </Link>
      }
      inputLabels={["文本", "图片", "音频", "文件"]}
      mark={<img alt="" className="h-full w-full object-cover" src="/logo.png" />}
      name="ModelMirror Router"
      pricingLabel="按调用模型计费"
      providerLabel="ModelMirror"
      subtitle="智能路由 · 自动择优 · 成本优化"
      taskLabels={["智能路由", "成本优化", "多模型会诊", "工具调度"]}
      topAction={
        <span className="rounded-full border border-brand-300/25 bg-brand-300/10 px-3 py-1 text-xs font-medium text-brand-100">
          首选路线
        </span>
      }
    />
  );
}
