import { useEffect, useState } from "react";
import type {
  SkillTrustReceipt,
  SkillTrustReceiptSummary,
  SkillTrustGateMode,
  SkillTrustRiskLevel,
} from "../../data/skillTrustIndex";

const RISK_LABELS: Record<SkillTrustRiskLevel, string> = {
  low: "低风险",
  medium: "中风险",
  high: "高风险",
  critical: "重点审查",
};

const RISK_STYLES: Record<SkillTrustRiskLevel, string> = {
  low: "border-emerald-300/25 bg-emerald-300/10 text-emerald-100",
  medium: "border-sky-300/25 bg-sky-300/10 text-sky-100",
  high: "border-amber-300/25 bg-amber-300/10 text-amber-100",
  critical: "border-rose-300/25 bg-rose-300/10 text-rose-100",
};

const CAPABILITY_LABELS: Record<string, string> = {
  network: "网络",
  credentials: "凭据",
  fileWrite: "文件写入",
  hostFilesystem: "宿主文件",
  browser: "浏览器",
  mcp: "MCP",
  shell: "Shell",
  packageManager: "包管理器",
  desktopControl: "桌面控制",
  destructive: "破坏性动作",
  securitySensitive: "安全敏感",
};

export function SkillTrustBadge({
  summary,
}: {
  summary: SkillTrustReceiptSummary | null;
}) {
  if (!summary) {
    return (
      <span className="rounded-full border border-white/15 bg-white/[0.055] px-2.5 py-1 text-xs font-semibold text-slate-300">
        信任状态未知
      </span>
    );
  }
  return (
    <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${RISK_STYLES[summary.riskLevel]}`}>
      {RISK_LABELS[summary.riskLevel]} · {summary.installPolicy === "allow" ? "可安装" : summary.installPolicy === "confirm" ? "需确认" : "已阻断"}
    </span>
  );
}

export function SkillTrustSummaryLine({
  gateMode,
  onInspect,
  summary,
}: {
  gateMode: SkillTrustGateMode;
  onInspect: () => void;
  summary: SkillTrustReceiptSummary | null;
}) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-white/10 pt-3">
      <SkillTrustBadge summary={summary} />
      {summary ? (
        <span className="text-xs text-slate-400">
          {summary.summary.scriptCount} 个脚本 · {summary.summary.opaqueResourceCount} 个被动资源 · {summary.compatibilityStatus === "portable" ? "可移植" : summary.compatibilityStatus === "conditional" ? "条件兼容" : "不兼容"}
        </span>
      ) : gateMode === "enforce" ? (
        <span className="text-xs text-rose-200">凭据缺失时不提供安装。</span>
      ) : (
        <span className="text-xs text-amber-100/85">
          {gateMode === "audit" ? "审计模式" : "信任门已关闭"}：按旧行为安装，Server 仍记录来源。
        </span>
      )}
      {summary ? (
        <button
          className="ml-auto min-h-9 rounded-full border border-white/15 px-3 text-xs font-semibold text-slate-200 transition hover:border-white/30 hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300"
          onClick={onInspect}
          type="button"
        >
          查看信任凭据
        </button>
      ) : null}
    </div>
  );
}

export default function SkillTrustPanel({
  action,
  busy = false,
  onCancel,
  onConfirm,
  receipt,
  title,
}: {
  action: "inspect" | "install" | "acknowledge";
  busy?: boolean;
  onCancel: () => void;
  onConfirm?: () => void;
  receipt: SkillTrustReceipt;
  title: string;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const requiredCapabilities = Object.entries(receipt.capabilities)
    .filter(([, required]) => required)
    .map(([capability]) => CAPABILITY_LABELS[capability] ?? capability);
  const source = receipt.source;
  const localSource = source.kind === "local_import";

  useEffect(() => setConfirmed(false), [receipt.trustFingerprint]);

  return (
    <section
      aria-labelledby="skill-trust-panel-title"
      className="mb-6 scroll-mt-4 rounded-lg border border-amber-300/25 bg-surface-900/90 p-5 shadow-prism"
      id="skill-trust-panel"
    >
      <div className="flex flex-col gap-3 border-b border-white/10 pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-semibold text-amber-100">
            {localSource ? "本地导入 Skill 信任凭据" : "第三方 Skill 信任凭据"}
          </p>
          <h2 className="mt-1 text-xl font-semibold text-white" id="skill-trust-panel-title">{title}</h2>
          <p className="mt-2 text-sm leading-6 text-slate-300">
            这是本机确定性扫描结果，不代表来源获得官方认证，也不会自动授予任何工具权限。
          </p>
        </div>
        <SkillTrustBadge summary={receipt} />
      </div>

      <dl className="mt-4 grid gap-3 text-xs sm:grid-cols-2 xl:grid-cols-4">
        <div><dt className="text-slate-500">规范兼容性</dt><dd className="mt-1 font-semibold text-slate-100">{receipt.compatibilityStatus === "portable" ? "可移植" : receipt.compatibilityStatus === "conditional" ? "条件兼容" : "不支持"}</dd></div>
        <div><dt className="text-slate-500">文件与脚本</dt><dd className="mt-1 font-semibold text-slate-100">{receipt.summary.fileCount} 个文件 · {receipt.summary.scriptCount} 个脚本</dd></div>
        <div><dt className="text-slate-500">许可证声明</dt><dd className="mt-1 break-words font-semibold text-slate-100">{receipt.license || "未声明"}</dd></div>
        <div><dt className="text-slate-500">Router</dt><dd className="mt-1 font-semibold text-slate-100">{receipt.routerEligible ? "可按需发现" : "不纳入自动发现"}</dd></div>
      </dl>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <section className="rounded-lg border border-white/10 bg-white/[0.04] p-4">
          <h3 className="text-xs font-semibold text-slate-300">能力与依赖</h3>
          <div className="mt-2 flex flex-wrap gap-2">
            {(requiredCapabilities.length ? requiredCapabilities : ["无额外宿主能力声明"]).map((item) => (
              <span className="rounded-full border border-white/10 bg-ink-950/75 px-2.5 py-1 text-xs text-slate-200" key={item}>{item}</span>
            ))}
          </div>
          {receipt.allowedTools.length ? <p className="mt-3 break-words text-xs leading-5 text-slate-400">所需工具：{receipt.allowedTools.join("、")}</p> : null}
          {receipt.dependencies.length ? <p className="mt-2 break-words text-xs leading-5 text-slate-400">依赖：{receipt.dependencies.join("、")}</p> : null}
          {receipt.commands.length ? <p className="mt-2 break-words text-xs leading-5 text-slate-400">命令：{receipt.commands.join("、")}</p> : null}
        </section>
        <section className="rounded-lg border border-white/10 bg-white/[0.04] p-4">
          <h3 className="text-xs font-semibold text-slate-300">主要发现</h3>
          {receipt.findings.length ? (
            <ul className="mt-2 space-y-2">
              {receipt.findings.slice(0, 8).map((finding, index) => (
                <li className="text-xs leading-5 text-slate-300" key={`${finding.code}-${finding.path ?? ""}-${index}`}>
                  <code className="text-amber-100">{finding.code}</code> · {finding.message}
                  {finding.path ? <span className="block break-all text-slate-500">{finding.path}{finding.line ? `:${finding.line}` : ""}</span> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-xs leading-5 text-emerald-100">未发现需要提示的静态风险。</p>
          )}
        </section>
      </div>

      <div className="mt-4 rounded-lg border border-white/10 bg-black/15 p-3 text-[11px] leading-5 text-slate-400">
        {localSource ? (
          <>
            <p className="break-all">来源：本地{source.transportKind === "zip" ? " ZIP" : "文件夹"}导入</p>
            <p className="mt-1 break-all font-mono">导入 ID：{source.importId}</p>
            <p className="mt-1 break-all font-mono">传输摘要：{source.transportDigest}</p>
          </>
        ) : (
          <>
            <p className="break-all">来源：{source.repoUrl} / {source.subPath || "."}</p>
            <p className="mt-1 break-all font-mono">固定 SHA：{source.verifiedCommit}</p>
          </>
        )}
        <p className="mt-1 break-all font-mono">凭据：{receipt.trustFingerprint}</p>
      </div>

      {action !== "inspect" ? (
        <label className="mt-4 flex items-start gap-3 rounded-lg border border-amber-300/20 bg-amber-300/[0.07] p-3 text-sm leading-6 text-slate-200">
          <input checked={confirmed} className="mt-1 h-4 w-4 shrink-0 rounded border-white/20 bg-slate-950 text-amber-300" onChange={(event) => setConfirmed(event.target.checked)} type="checkbox" />
          <span>我已核对固定版本、能力要求和主要发现，理解确认只适用于此凭据；升级或凭据变化后需要重新确认。</span>
        </label>
      ) : null}

      <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <button className="min-h-11 rounded-full border border-white/15 px-4 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06]" disabled={busy} onClick={onCancel} type="button">{action === "inspect" ? "关闭" : "暂不确认"}</button>
        {action !== "inspect" ? (
          <button className="min-h-11 rounded-full bg-amber-300 px-4 text-sm font-semibold text-ink-950 transition hover:bg-amber-200 disabled:cursor-not-allowed disabled:opacity-45" disabled={busy || !confirmed} onClick={onConfirm} type="button">{busy ? "处理中…" : action === "install" ? "确认风险并安装" : "确认此版本可激活"}</button>
        ) : null}
      </div>
    </section>
  );
}
