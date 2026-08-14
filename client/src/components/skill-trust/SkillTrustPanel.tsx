import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  FileCode2,
  FolderPen,
  Globe2,
  PackageCheck,
  ShieldCheck,
  SquareTerminal,
  X,
} from "lucide-react";
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
  network: "访问网络",
  credentials: "读取凭据",
  fileWrite: "写入工作区",
  hostFilesystem: "访问宿主文件",
  browser: "控制浏览器",
  mcp: "调用 MCP",
  shell: "执行系统命令",
  packageManager: "使用包管理器",
  desktopControl: "控制桌面",
  destructive: "执行高影响操作",
  securitySensitive: "访问安全敏感能力",
};

const CAPABILITY_ICONS: Record<string, typeof ShieldCheck> = {
  network: Globe2,
  fileWrite: FolderPen,
  shell: SquareTerminal,
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
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const busyRef = useRef(busy);
  const onCancelRef = useRef(onCancel);
  busyRef.current = busy;
  onCancelRef.current = onCancel;

  const requiredCapabilities = Object.entries(receipt.capabilities)
    .filter(([, required]) => required)
    .map(([capability]) => ({
      icon: CAPABILITY_ICONS[capability] ?? ShieldCheck,
      key: capability,
      label: CAPABILITY_LABELS[capability] ?? capability,
    }));
  const source = receipt.source;
  const localSource = source.kind === "local_import";

  useEffect(() => setConfirmed(false), [receipt.trustFingerprint]);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => closeButtonRef.current?.focus());

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !busyRef.current) {
        event.preventDefault();
        onCancelRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), summary, a[href], [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus();
    };
  }, [receipt.trustFingerprint]);

  const content = (
    <div
      className="fixed inset-0 z-[100] flex items-end justify-center bg-ink-950/80 px-0 pt-8 backdrop-blur-[2px] sm:items-center sm:p-5"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onCancel();
      }}
    >
      <div
        aria-labelledby="skill-trust-panel-title"
        aria-modal="true"
        className="flex max-h-[calc(100dvh-2rem)] w-full max-w-3xl flex-col overflow-hidden rounded-t-2xl border border-amber-300/30 bg-surface-900 shadow-[0_24px_80px_rgba(0,0,0,0.55)] sm:max-h-[min(760px,calc(100dvh-2.5rem))] sm:rounded-xl"
        id="skill-trust-panel"
        ref={dialogRef}
        role="dialog"
      >
        <header className="flex shrink-0 items-start gap-3 border-b border-white/10 px-4 py-4 sm:px-6">
          <div className="mt-0.5 grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-amber-300/25 bg-amber-300/10 text-amber-100">
            <PackageCheck aria-hidden="true" size={21} />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-semibold text-amber-100">
              {action === "inspect" ? "信任凭据" : "核对权限后安装"}
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-semibold text-white sm:text-xl" id="skill-trust-panel-title">{title}</h2>
              <SkillTrustBadge summary={receipt} />
            </div>
            <p className="mt-1.5 text-xs leading-5 text-slate-400">
              固定版本的本机扫描结果；不会自动授予工具权限，也不代表来源获得官方认证。
            </p>
          </div>
          <button
            aria-label="关闭信任凭据"
            className="grid h-10 w-10 shrink-0 place-items-center rounded-full border border-white/10 text-slate-300 transition hover:border-white/25 hover:bg-white/[0.06] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-200"
            disabled={busy}
            onClick={onCancel}
            ref={closeButtonRef}
            type="button"
          >
            <X aria-hidden="true" size={19} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6 sm:py-5">
          <div className="grid gap-5 md:grid-cols-[0.9fr_1.1fr] md:divide-x md:divide-white/10">
            <section aria-labelledby="skill-trust-capabilities-title" className="md:pr-5">
              <h3 className="text-sm font-semibold text-white" id="skill-trust-capabilities-title">需要的权限</h3>
              <div className="mt-3 space-y-2">
                {(requiredCapabilities.length ? requiredCapabilities : [{ icon: CheckCircle2, key: "none", label: "无额外宿主能力声明" }]).map(({ icon: Icon, key, label }) => (
                  <div className="flex items-center gap-3 rounded-lg border border-white/10 bg-white/[0.035] px-3 py-2.5" key={key}>
                    <Icon aria-hidden="true" className="shrink-0 text-cyan-200" size={18} />
                    <span className="text-sm text-slate-200">{label}</span>
                  </div>
                ))}
              </div>
              {receipt.allowedTools.length ? <p className="mt-3 break-words text-xs leading-5 text-slate-400">所需工具：{receipt.allowedTools.join("、")}</p> : null}
              {receipt.commands.length ? <p className="mt-1.5 break-words text-xs leading-5 text-slate-400">命令：{receipt.commands.join("、")}</p> : null}
            </section>

            <section aria-labelledby="skill-trust-findings-title" className="md:pl-5">
              <h3 className="text-sm font-semibold text-white" id="skill-trust-findings-title">安装前须知</h3>
              <ul className="mt-3 space-y-3">
                {receipt.findings.length ? receipt.findings.slice(0, 5).map((finding, index) => (
                  <li className="flex gap-2.5 text-sm leading-5 text-slate-300" key={`${finding.code}-${finding.path ?? ""}-${index}`}>
                    <AlertTriangle aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" size={16} />
                    <span>
                      {finding.message}
                      {finding.path ? <span className="mt-0.5 block break-all text-xs text-slate-500">{finding.path}{finding.line ? `:${finding.line}` : ""}</span> : null}
                      <code className="sr-only">{finding.code}</code>
                    </span>
                  </li>
                )) : (
                  <li className="flex gap-2.5 text-sm leading-5 text-emerald-100">
                    <CheckCircle2 aria-hidden="true" className="mt-0.5 shrink-0" size={16} />
                    未发现需要额外确认的静态风险。
                  </li>
                )}
                {receipt.summary.scriptCount ? (
                  <li className="flex gap-2.5 text-sm leading-5 text-slate-300">
                    <FileCode2 aria-hidden="true" className="mt-0.5 shrink-0 text-slate-400" size={16} />
                    包含 {receipt.summary.scriptCount} 个本地脚本；安装不会自动执行脚本。
                  </li>
                ) : null}
              </ul>
            </section>
          </div>

          <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-3 border-y border-white/10 py-4 text-xs sm:grid-cols-4">
            <div><dt className="text-slate-500">兼容性</dt><dd className="mt-1 font-semibold text-slate-100">{receipt.compatibilityStatus === "portable" ? "可移植" : receipt.compatibilityStatus === "conditional" ? "条件兼容" : "不支持"}</dd></div>
            <div><dt className="text-slate-500">文件</dt><dd className="mt-1 font-semibold text-slate-100">{receipt.summary.fileCount} 个</dd></div>
            <div><dt className="text-slate-500">脚本</dt><dd className="mt-1 font-semibold text-slate-100">{receipt.summary.scriptCount} 个</dd></div>
            <div><dt className="text-slate-500">Router</dt><dd className="mt-1 font-semibold text-slate-100">{receipt.routerEligible ? "可按需发现" : "不纳入自动发现"}</dd></div>
          </dl>

          <details className="group mt-4 rounded-lg border border-white/10 bg-black/15 text-xs text-slate-400">
            <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 px-3 font-semibold text-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-amber-200">
              来源与固定版本
              <ChevronDown aria-hidden="true" className="transition group-open:rotate-180" size={16} />
            </summary>
            <div className="space-y-1 border-t border-white/10 px-3 py-3 font-mono text-[11px] leading-5">
              {localSource ? (
                <>
                  <p className="break-all">来源：本地{source.transportKind === "zip" ? " ZIP" : "文件夹"}导入</p>
                  <p className="break-all">导入 ID：{source.importId}</p>
                  <p className="break-all">传输摘要：{source.transportDigest}</p>
                </>
              ) : (
                <>
                  <p className="break-all">来源：{source.repoUrl} / {source.subPath || "."}</p>
                  <p className="break-all">固定 SHA：{source.verifiedCommit}</p>
                </>
              )}
              <p className="break-all">凭据：{receipt.trustFingerprint}</p>
            </div>
          </details>

          {action !== "inspect" ? (
            <label className="mt-4 flex items-start gap-3 rounded-lg border border-amber-300/20 bg-amber-300/[0.07] p-3 text-sm leading-6 text-slate-200">
              <input checked={confirmed} className="mt-1 h-4 w-4 shrink-0 rounded border-white/20 bg-slate-950 text-amber-300" onChange={(event) => setConfirmed(event.target.checked)} type="checkbox" />
              <span>我已核对固定版本、所需权限和安装须知；凭据变化后需要重新确认。</span>
            </label>
          ) : null}
        </div>

        <footer className="flex shrink-0 flex-col-reverse gap-2 border-t border-white/10 bg-surface-900 px-4 py-4 sm:flex-row sm:justify-end sm:px-6">
          <button className="min-h-11 rounded-full border border-white/15 px-5 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/35" disabled={busy} onClick={onCancel} type="button">{action === "inspect" ? "关闭" : "暂不安装"}</button>
          {action !== "inspect" ? (
            <button className="min-h-11 rounded-full bg-amber-300 px-5 text-sm font-semibold text-ink-950 transition hover:bg-amber-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-100 disabled:cursor-not-allowed disabled:opacity-45" disabled={busy || !confirmed} onClick={onConfirm} type="button">{busy ? "处理中…" : action === "install" ? "接受风险并安装" : "确认此版本可激活"}</button>
          ) : null}
        </footer>
      </div>
    </div>
  );

  return createPortal(content, document.body);
}
