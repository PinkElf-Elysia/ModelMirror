export interface ProviderQualificationSummary {
  series_id?: string | null;
  interval_id?: string | null;
  expires_at?: string | null;
  renewal_reason?: string | null;
  valid: boolean;
  reason_code: string;
}

const REASONS: Record<string, string> = {
  provider_chat_certification_expiry_unknown:
    "历史到期时间未知：旧认证记录仍保留，但不继承资格、批准或门禁样本。需核验历史证据或重新认证。",
  provider_chat_certification_expired: "资格已过期：到期后的认证会建立新有效区间，不补齐中间空档。",
  provider_chat_certification_invalidated: "资格已失效：配置变化或硬失败已中断有效区间，原批准不能直接复用。",
  provider_chat_certification_not_passed: "本次认证未形成有效资格；失败或不确定结果不会自动重试。",
  provider_chat_certification_time_invalid: "资格时间记录无法验证，当前失败关闭。",
};

export function ProviderQualificationStatus({ qualification }: {
  qualification?: ProviderQualificationSummary | null;
}) {
  if (!qualification) return null;
  const expiry = qualification.expires_at ? new Date(qualification.expires_at) : null;
  const expiryText = expiry && Number.isFinite(expiry.getTime()) ? expiry.toLocaleString() : null;
  return <div className="mt-2 text-xs leading-5 text-slate-300" aria-label="持续资格状态">
    <p className={qualification.valid ? "text-emerald-200" : "text-amber-100"}>
      {qualification.valid ? "当前资格有效" : REASONS[qualification.reason_code] ?? "资格当前不可用，请核查认证与策略。"}
    </p>
    {expiryText ? <p>已保存到期时间：<time dateTime={qualification.expires_at!}>{expiryText}</time></p> : null}
    {qualification.renewal_reason === "provider_qualification_renewed" ? <p>同配置按时续期，连续有效区间已保留。</p> : null}
    <p>新认证默认有效 30 天，显式配置的更短期限仍生效。健康检查不续期，认证不自动启用路由。</p>
  </div>;
}
