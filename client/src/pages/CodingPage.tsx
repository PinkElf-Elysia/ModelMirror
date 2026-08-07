import {
  ArrowLeft,
  CheckCircle2,
  CircleAlert,
  Clock3,
  FilePenLine,
  FileSearch,
  FolderOpen,
  RefreshCw,
  Send,
  ShieldCheck,
  Square,
} from "lucide-react";
import {
  type FormEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import { Link } from "react-router-dom";
import remarkGfm from "remark-gfm";
import CodingChangesPanel from "../components/CodingChangesPanel";
import CodingHistoryPanel from "../components/CodingHistoryPanel";
import CodingProjectHostPanel from "../components/CodingProjectHostPanel";
import CodingRecoveryCard, {
  type CodingRecoveryAction,
} from "../components/CodingRecoveryCard";
import PageContainer from "../components/PageContainer";
import type {
  CodingApplyResult,
  CodingCapabilities,
  CodingCommandRequest,
  CodingCommitResult,
  CodingCycleHistory,
  CodingDraftChanges,
  CodingEvent,
  CodingPlanEntry,
  CodingProjectSummary,
  CodingPublishResult,
  CodingRecoveryStatus,
  CodingVerification,
} from "../types/coding";
import {
  applyCodingChanges,
  cancelCodingTurn,
  cancelCodingVerification,
  confirmCodingVerification,
  closeCodingSession,
  commitCodingChanges,
  continueCodingSession,
  CodingApiError,
  connectCodingEvents,
  createCodingSession,
  decideCodingCommand,
  discardCodingRecovery,
  discardCodingChanges,
  getCodingCapabilities,
  getCodingApplyStatus,
  getCodingChanges,
  getCodingCommitStatus,
  getPendingCodingCommand,
  getCodingHistory,
  getCodingPatch,
  getCodingProjects,
  getCodingPublishStatus,
  getCodingRecovery,
  getCodingRecoveryPatch,
  getCodingSessionStatus,
  getCodingVerification,
  startCodingTurn,
  startCodingVerification,
  resumeCodingRecovery,
  markCodingPublishReady,
  publishCodingChanges,
  revertCodingApply,
  undoCodingCommit,
  validateCodingChanges,
} from "../utils/codingApi";

type RunState = "idle" | "starting" | "running" | "stopping" | "error";
type CapabilityState = "loading" | "ready" | "error";

interface ToolActivity {
  id: string;
  kind: string;
  status: string;
  title: string;
}

interface StoredCodingSession {
  id: string;
  lastSeq: number;
  projectId: string;
}

const CODING_SESSION_STORAGE_KEY = "modelmirror.coding.session.v1";
const SAFE_SESSION_ID = /^[A-Za-z0-9_-]{1,128}$/;
const SAFE_PROJECT_ID = /^(?:modelmirror|local-[a-f0-9]{24}|hostgit_[a-f0-9]{32})$/;
const STREAM_RENDER_INTERVAL_MS = 80;
const BUILTIN_PROJECT: CodingProjectSummary = {
  branch: null,
  features: {
    apply: true,
    chat: true,
    commit: true,
    commands: false,
    diff: true,
    download: true,
    draft: true,
    publish: true,
    recovery: true,
    verification: true,
  },
  head: null,
  id: "modelmirror",
  kind: "builtin",
  name: "ModelMirror",
  reason: null,
  state: "available",
};

function readStoredCodingSession(): StoredCodingSession | null {
  if (typeof window === "undefined") return null;
  try {
    const value = JSON.parse(
      window.sessionStorage.getItem(CODING_SESSION_STORAGE_KEY) ?? "null",
    ) as Partial<StoredCodingSession> | null;
    if (
      !value ||
      typeof value.id !== "string" ||
      !SAFE_SESSION_ID.test(value.id) ||
      !Number.isInteger(value.lastSeq) ||
      (value.lastSeq ?? -1) < 0
    ) {
      return null;
    }
    const projectId =
      typeof value.projectId === "string" && SAFE_PROJECT_ID.test(value.projectId)
        ? value.projectId
        : "modelmirror";
    return { id: value.id, lastSeq: value.lastSeq as number, projectId };
  } catch {
    return null;
  }
}

function storeCodingSession(id: string, lastSeq: number, projectId: string) {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(
    CODING_SESSION_STORAGE_KEY,
    JSON.stringify({ id, lastSeq, projectId }),
  );
}

function clearStoredCodingSession() {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(CODING_SESSION_STORAGE_KEY);
}

function downloadFile(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

const capabilityReason: Record<string, string> = {
  disabled: "管理员尚未启用代码助手，其他功能不受影响。",
  not_configured: "代码服务已启动，但所需的模型连接信息尚未配置。",
  worker_unavailable: "代码服务当前不可用，请确认服务是否已启动。",
  mode_mismatch: "代码服务的运行模式与页面配置不一致，请检查服务配置。",
  projects_disabled: "本地项目选择尚未启用，仍可使用 ModelMirror。",
  project_source_not_configured: "本地项目目录尚未配置，仍可使用 ModelMirror。",
  project_source_unavailable: "暂时无法读取本地项目列表，仍可使用 ModelMirror。",
  project_writer_not_configured: "本地项目写入尚未配置，仍可查看和下载修改。",
  project_writer_unavailable: "本地项目写入服务未启动，仍可查看和下载修改。",
};

const errorMessage: Record<string, string> = {
  concurrency_limit: "代码助手已有一个未结束的会话，请回到原页面继续，或稍后重试。",
  turn_in_progress: "当前问题仍在处理，请先停止或等待完成。",
  prompt_too_long: "问题超过 20,000 字符，请缩短后重试。",
  session_not_found: "本次使用记录已经过期，请重新提交问题。",
  worker_unavailable: "无法连接代码服务，请检查服务状态。",
  agent_turn_failed: "代码助手未能完成本轮处理，请检查模型额度或配置后重试。",
  draft_policy_violation: "本轮修改超出安全范围，已自动撤销。",
  draft_busy: "代码助手仍在处理，请等待本轮结束。",
  validation_failed: "修改检查尚未通过，请先修正后再下载。",
  verification_in_progress: "项目验证仍在运行，请先等待完成或停止验证。",
  verifier_unavailable: "项目验证服务未启动，仍可查看和下载修改。",
  snapshot_mismatch: "项目版本已经变化，当前操作不能继续；已有修改仍可查看和下载。",
  verification_not_found: "本次项目验证记录已失效，请重新运行。",
  verification_confirmation_stale:
    "检查内容已经变化，请重新查看命令并再次确认。",
  command_request_not_found: "这项检查已结束或过期，无需再次处理。",
  command_decision_invalid: "没有识别到这次选择，请重新操作。",
  command_turn_inactive: "本轮处理已经结束，这项检查不会运行。",
  runner_unauthorized: "检查请求已失效，请让代码助手重新提出。",
  recovery_pending: "发现一份未完成的修改，请先选择继续、下载或放弃。",
  recovery_conflict: "外部内容后来发生变化，为避免覆盖人工内容，现在只允许查看或下载。",
  recovery_data_corrupt: "保存的修改无法安全读取，请下载可用内容或联系开发者处理。",
  recovery_storage_unavailable: "修改恢复服务暂时不可用，当前操作没有完成。",
  recovery_not_found: "这份保存记录已不存在，请刷新页面确认最新状态。",
  recovery_changed: "保存记录刚刚发生变化，请刷新页面后再试。",
  project_changed: "这个项目已经更新，当前草稿只能下载，不能继续处理。",
  project_dirty: "这个项目有尚未保存的改动，请先由开发者整理后再试。",
  project_not_found: "这个项目已不在可选列表中，请重新选择。",
  project_operation_unavailable:
    "此项目当前只支持准备和下载修改草稿，不提供项目验证或写入。",
  project_host_offline:
    "本地项目助手连接已断开，请在“连接本地项目助手”区域重新连接后再试。",
  project_host_unavailable:
    "本地项目助手当前不可用，请重新打开助手并恢复连接后再试。",
  project_host_snapshot_timeout:
    "等待本地项目助手读取项目的时间过长，请确认助手在线后重试。",
  snapshot_upload_failed:
    "本地项目内容未能传入临时工作区，请确认助手在线后重试。",
  project_writer_not_configured:
    "本地项目写入尚未配置，当前修改仍可查看和下载。",
  project_writer_timeout:
    "本地写入等待时间过长，结果暂时无法确认，请稍后查看状态。",
  project_writer_unavailable:
    "本地项目写入服务未启动，当前修改仍可查看和下载。",
  project_source_unavailable: "暂时无法读取本地项目，请稍后重试。",
};

const projectReason: Record<string, string> = {
  git_alternates_not_allowed: "此项目使用了共享版本存储，暂时不能安全读取。",
  git_inspection_failed: "暂时无法确认这个项目的状态。",
  git_submodule_not_allowed: "此项目包含子项目，本轮暂不支持。",
  git_symlink_not_allowed: "此项目包含链接文件，本轮暂不支持。",
  git_worktree_not_allowed: "此项目不是独立副本，请改用独立克隆。",
  project_dirty: "项目中有尚未保存的改动，请先整理为干净状态。",
  git_repository_dirty: "项目中有尚未保存的改动，请先整理为干净状态。",
  git_remote_not_allowed:
    "项目仍连接远程平台。移除远程连接后才能开放本地写入。",
  project_not_found: "项目已从清单中移除。",
  project_source_unavailable: "本地项目服务暂时不可用。",
  snapshot_limit_exceeded: "项目文件数量或大小超出本轮限制。",
  project_writer_not_configured:
    "本地项目写入尚未配置，仍可查看和下载修改。",
  project_writer_unavailable:
    "本地项目写入服务未启动，仍可查看和下载修改。",
  writeback_branch_required:
    "项目不在约定的本地分支，仍可准备草稿，但不能写入。",
  writeback_not_enabled:
    "此项目没有开放本地写入，仍可查看、检查和下载修改。",
};

function describeError(error: unknown) {
  if (error instanceof CodingApiError) {
    return errorMessage[error.code] ?? "代码助手暂时无法回答，请检查服务状态后重试。";
  }
  return "代码助手暂时无法回答，请检查服务状态后重试。";
}

function statusLabel(state: RunState) {
  if (state === "starting") return "正在准备分析";
  if (state === "running") return "正在分析";
  if (state === "stopping") return "正在停止";
  if (state === "error") return "本轮失败";
  return "等待问题";
}

function planStatusLabel(status: string) {
  if (status === "completed") return "完成";
  if (status === "in_progress") return "进行中";
  if (status === "cancelled") return "已取消";
  return "等待";
}

function toolKindLabel(kind: string) {
  if (kind === "read") return "读取文件";
  if (kind === "list") return "查看目录";
  if (kind === "glob") return "查找文件";
  if (kind === "grep") return "搜索内容";
  if (kind === "lsp") return "分析代码结构";
  if (kind === "edit") return "准备修改文件";
  return "查阅代码";
}

function formatArgv(argv: string[]) {
  return argv.map((argument) => JSON.stringify(argument)).join(" ");
}

function CodingSidebar({
  isDraft,
  localDraftOnly,
  localWriteback,
}: {
  isDraft: boolean;
  localDraftOnly: boolean;
  localWriteback: boolean;
}) {
  return (
    <div>
      <Link
        className="inline-flex items-center gap-2 text-sm font-semibold text-slate-300 transition hover:text-white"
        to="/studio"
      >
        <ArrowLeft aria-hidden="true" size={16} />
        返回 Studio
      </Link>
      <div className="mt-5">
        <p className="text-sm font-semibold text-white">
          {isDraft ? "修改范围清晰可控" : "你可以放心提问"}
        </p>
        <ul className="mt-3 space-y-3 text-xs leading-5 text-slate-400">
          <li>
            {localDraftOnly
              ? "所有修改只保存在临时副本；你可以查看并下载 Diff，本地项目不会被写入。"
              : localWriteback
                ? "所有修改先保存在临时副本，只有你确认后才会写入所选本地项目。"
              : isDraft
              ? "所有修改先保存在临时副本，只有你确认后才会写入专用项目副本。"
              : "只查看固定的 ModelMirror 项目代码。"}
          </li>
          <li>
            {localDraftOnly
              ? "项目检查和代码助手提出的命令都需要你确认，只会在临时副本中运行。"
              : localWriteback
                ? "项目检查和代码助手提出的命令都需要你确认，只会在临时副本中运行。"
              : isDraft
              ? "代码助手不会自行运行检查；项目验证只在你手动启动时执行固定步骤。"
              : "不会执行命令、运行测试或访问外部网站。"}
          </li>
          <li>
            {localDraftOnly
              ? "检查不能联网，运行产生的文件会被丢弃；不会创建本地提交或 GitHub PR。"
              : localWriteback
                ? "写入后可保存本地版本并安全撤销；不会自动上传，也不会创建 GitHub PR。"
              : isDraft
              ? "只有你再次确认，才会保存为本地提交；不会自动上传或合并，发布到 GitHub 还需单独确认。当前项目目录始终不受影响。"
              : "不会修改文件、生成变更或提交代码。"}
          </li>
          <li>
            {isDraft
              ? "修改草稿可以在重启后继续；此前的提问、回答和查阅过程不会保存。"
              : "问题与回答只临时保留，服务重启后清除。"}
          </li>
        </ul>
      </div>
    </div>
  );
}

function CodingCommandConfirmation({
  action,
  error,
  onDecision,
  request,
}: {
  action: "idle" | "allowing" | "rejecting";
  error: string;
  onDecision: (decision: "allow_once" | "reject") => Promise<void>;
  request: CodingCommandRequest;
}) {
  return (
    <section
      aria-live="polite"
      aria-labelledby="coding-command-title"
      className="order-2 rounded-lg border border-cyan-300/25 bg-cyan-300/[0.07] p-4"
    >
      <div className="flex items-start gap-3">
        <ShieldCheck
          aria-hidden="true"
          className="mt-0.5 shrink-0 text-cyan-200"
          size={19}
        />
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-white" id="coding-command-title">
            代码助手希望运行一项检查
          </h2>
          <p className="mt-1 text-sm font-medium text-cyan-100">
            {request.command.name}
          </p>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-300">
            只会在临时项目副本中运行，不能联网，也不会改变你的本地项目。
          </p>
          <details className="mt-3">
            <summary className="cursor-pointer text-xs font-semibold text-slate-300 outline-none hover:text-white focus-visible:ring-2 focus-visible:ring-cyan-300/70">
              查看命令
            </summary>
            <div className="mt-2 min-w-0 rounded-lg bg-black/25 p-3">
              <code className="block overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs leading-5 text-slate-300">
                {formatArgv(request.command.argv)}
              </code>
              {request.command.cwd !== "." ? (
                <p className="mt-1 text-[11px] text-slate-500">
                  运行位置：{request.command.cwd}
                </p>
              ) : null}
            </div>
          </details>
          <div className="mt-4 flex flex-col gap-2 sm:flex-row">
            <button
              className="inline-flex min-h-10 items-center justify-center rounded-lg border border-white/15 px-3 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300/60 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={action !== "idle"}
              onClick={() => void onDecision("reject")}
              type="button"
            >
              {action === "rejecting" ? "正在拒绝" : "暂不运行"}
            </button>
            <button
              className="inline-flex min-h-10 items-center justify-center rounded-lg bg-cyan-200 px-3 text-sm font-semibold text-slate-950 transition hover:bg-cyan-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-100 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={action !== "idle"}
              onClick={() => void onDecision("allow_once")}
              type="button"
            >
              {action === "allowing" ? "正在开始" : "允许本次运行"}
            </button>
          </div>
          <p aria-live="polite" className="mt-3 flex items-center gap-2 text-xs text-slate-400">
            <Clock3 aria-hidden="true" size={13} />
            等待你确认时，代码助手会暂停这项检查。
          </p>
          {error ? (
            <p className="mt-2 text-xs leading-5 text-rose-100" role="alert">
              {error}
            </p>
          ) : null}
        </div>
      </div>
    </section>
  );
}

export default function CodingPage() {
  const restoredSessionRef = useRef<StoredCodingSession | null>(
    readStoredCodingSession(),
  );
  const [capabilityState, setCapabilityState] =
    useState<CapabilityState>("loading");
  const [capabilities, setCapabilities] = useState<CodingCapabilities | null>(
    null,
  );
  const [projects, setProjects] = useState<CodingProjectSummary[]>([
    BUILTIN_PROJECT,
  ]);
  const [projectsError, setProjectsError] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState(
    restoredSessionRef.current?.projectId ?? "modelmirror",
  );
  const [runState, setRunState] = useState<RunState>("idle");
  const [prompt, setPrompt] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(
    restoredSessionRef.current?.id ?? null,
  );
  const [events, setEvents] = useState<CodingEvent[]>([]);
  const [error, setError] = useState("");
  const [transportWarning, setTransportWarning] = useState("");
  const [draftChanges, setDraftChanges] =
    useState<CodingDraftChanges | null>(null);
  const [draftLoading, setDraftLoading] = useState(false);
  const [draftError, setDraftError] = useState("");
  const [draftNotice, setDraftNotice] = useState("");
  const [verification, setVerification] =
    useState<CodingVerification | null>(null);
  const [verificationError, setVerificationError] = useState("");
  const [pendingCommand, setPendingCommand] =
    useState<CodingCommandRequest | null>(null);
  const [commandAction, setCommandAction] = useState<
    "idle" | "allowing" | "rejecting"
  >("idle");
  const [commandError, setCommandError] = useState("");
  const [applyResult, setApplyResult] = useState<CodingApplyResult | null>(
    null,
  );
  const [applyError, setApplyError] = useState("");
  const [commitResult, setCommitResult] = useState<CodingCommitResult | null>(
    null,
  );
  const [commitError, setCommitError] = useState("");
  const [publishResult, setPublishResult] = useState<CodingPublishResult | null>(
    null,
  );
  const [publishError, setPublishError] = useState("");
  const [cycleHistory, setCycleHistory] = useState<CodingCycleHistory | null>(null);
  const [recovery, setRecovery] = useState<CodingRecoveryStatus | null>(null);
  const [recoveryAction, setRecoveryAction] =
    useState<CodingRecoveryAction>("idle");
  const [recoveryError, setRecoveryError] = useState("");
  const [recoveryNotice, setRecoveryNotice] = useState("");
  const [recoveredState, setRecoveredState] = useState<string | null>(null);
  const [recoveryConflict, setRecoveryConflict] = useState<string | null>(null);
  const closeStreamRef = useRef<null | (() => void)>(null);
  const promptRef = useRef<HTMLTextAreaElement | null>(null);
  const sessionProbeInFlightRef = useRef(false);
  const lastSeqRef = useRef(restoredSessionRef.current?.lastSeq ?? 0);
  const pendingEventsRef = useRef<CodingEvent[]>([]);
  const pendingSessionStoreRef = useRef<StoredCodingSession | null>(null);
  const streamRenderTimerRef = useRef<number | null>(null);
  const initialDraftLoadSessionRef = useRef<string | null>(null);
  const isDraftMode = capabilities?.mode === "draft";
  const selectedProject =
    projects.find((project) => project.id === selectedProjectId) ??
    (recovery?.project?.id === selectedProjectId ? recovery.project : null) ??
    (selectedProjectId === "modelmirror" ? BUILTIN_PROJECT : null);
  const isLocalProject =
    selectedProject?.kind === "local_clone" || selectedProject?.kind === "host_git";
  const supportsVerification = selectedProject?.features.verification !== false;
  const supportsCommands = selectedProject?.features.commands === true;
  const supportsApply = selectedProject?.features.apply !== false;
  const supportsCommit = selectedProject?.features.commit !== false;
  const supportsPublish = selectedProject?.features.publish !== false;
  const localWriteback = Boolean(
    isLocalProject && supportsApply && supportsCommit,
  );
  const localDraftOnly = Boolean(isLocalProject && !localWriteback);
  const effectiveApplyCapability: CodingCapabilities["apply"] = localWriteback
    ? {
        allows_not_applicable: true,
        allows_quality_risk_confirmation: true,
        available: capabilities?.project_writeback?.available === true,
        configured: capabilities?.project_writeback?.configured === true,
        reason: capabilities?.project_writeback?.reason,
        requires_verification: false,
        supports_revert: true,
        target: "selected_local_repository",
      }
    : capabilities?.apply;
  const effectiveCommitCapability: CodingCapabilities["commit"] = localWriteback
    ? {
        available: capabilities?.project_writeback?.available === true,
        configured: capabilities?.project_writeback?.configured === true,
        max_message_chars: 2_000,
        reason: capabilities?.project_writeback?.reason,
        remote_operations: false,
        requires_apply: true,
        supports_undo: true,
        target: "selected_local_repository",
      }
    : capabilities?.commit;
  const verificationAvailable =
    supportsVerification &&
    (isLocalProject
      ? capabilities?.commands.available === true
      : capabilities?.verification.available === true);
  const serviceAvailable = capabilities?.available === true;
  const selectedProjectAvailable = Boolean(
    selectedProject &&
      (sessionId ||
        (selectedProject.state === "available" &&
          (!isLocalProject || capabilities?.projects.available === true))),
  );
  const workspaceAvailable = serviceAvailable && selectedProjectAvailable;
  const isBusy = ["starting", "running", "stopping"].includes(runState);
  const verificationRunning =
    verification?.state === "running" && verification.stale === false;
  const verificationActive =
    verification?.stale === false &&
    ["awaiting_confirmation", "running"].includes(
      verification?.state ?? "",
    );
  const publishRunning =
    publishResult?.state === "publishing" ||
    publishResult?.state === "marking_ready";
  const sessionFrozen = Boolean(
    recoveryConflict ||
      recoveredState === "applied" ||
      recoveredState === "reverted" ||
      (applyResult?.apply_id &&
        ["applied", "reverting", "reverted", "failed"].includes(
          applyResult.state,
        )) ||
      Boolean(publishResult?.publish_id),
  );
  const hasPendingRecovery = Boolean(
    recovery?.pending && (!sessionId || recoveryConflict),
  );
  const projectSelectionLocked = Boolean(sessionId || recovery?.pending || isBusy);

  const clearPendingStreamRender = useCallback(() => {
    if (streamRenderTimerRef.current !== null) {
      window.clearTimeout(streamRenderTimerRef.current);
      streamRenderTimerRef.current = null;
    }
    pendingEventsRef.current = [];
    pendingSessionStoreRef.current = null;
  }, []);

  const flushStreamRender = useCallback(() => {
    if (streamRenderTimerRef.current !== null) {
      window.clearTimeout(streamRenderTimerRef.current);
      streamRenderTimerRef.current = null;
    }

    const pendingEvents = pendingEventsRef.current;
    const pendingSession = pendingSessionStoreRef.current;
    pendingEventsRef.current = [];
    pendingSessionStoreRef.current = null;

    if (pendingSession) {
      storeCodingSession(
        pendingSession.id,
        pendingSession.lastSeq,
        pendingSession.projectId,
      );
    }
    if (!pendingEvents.length) return;

    setEvents((current) => {
      let turnStartIndex = -1;
      pendingEvents.forEach((event, index) => {
        if (event.type === "turn_started") turnStartIndex = index;
      });
      return turnStartIndex >= 0
        ? pendingEvents.slice(turnStartIndex)
        : [...current, ...pendingEvents];
    });
  }, []);

  const queueStreamEvent = useCallback(
    (event: CodingEvent, projectId: string) => {
      if (event.type === "turn_started") {
        pendingEventsRef.current = [event];
      } else {
        pendingEventsRef.current.push(event);
      }
      pendingSessionStoreRef.current = {
        id: event.session_id,
        lastSeq: event.seq,
        projectId,
      };

      if (
        event.type === "turn_started" ||
        event.type === "turn_completed" ||
        event.type === "cancelled" ||
        event.type === "failed"
      ) {
        flushStreamRender();
        return;
      }
      if (streamRenderTimerRef.current === null) {
        streamRenderTimerRef.current = window.setTimeout(
          flushStreamRender,
          STREAM_RENDER_INTERVAL_MS,
        );
      }
    },
    [flushStreamRender],
  );

  const resetExpiredSession = useCallback(
    (activeSessionId: string) => {
      if (sessionId !== activeSessionId) return false;
      closeStreamRef.current?.();
      closeStreamRef.current = null;
      clearPendingStreamRender();
      initialDraftLoadSessionRef.current = null;
      setSessionId(null);
      lastSeqRef.current = 0;
      clearStoredCodingSession();
      setEvents([]);
      setDraftChanges(null);
      setDraftLoading(false);
      setDraftError("");
      setDraftNotice("");
      setVerification(null);
      setVerificationError("");
      setPendingCommand(null);
      setCommandAction("idle");
      setCommandError("");
      setApplyResult(null);
      setApplyError("");
      setCommitResult(null);
      setCommitError("");
      setPublishResult(null);
      setPublishError("");
      setCycleHistory(null);
      setRecoveredState(null);
      setRecoveryConflict(null);
      setRunState("idle");
      setTransportWarning("");
      setError(
        "代码服务已重新启动。若有可继续的修改，页面会在上方提示；此前对话不会恢复。",
      );
      return true;
    },
    [clearPendingStreamRender, sessionId],
  );

  const loadRecovery = useCallback(async () => {
    setRecoveryError("");
    try {
      const result = await getCodingRecovery();
      setRecovery(result.pending ? result : null);
      if (result.pending && result.project?.id) {
        setSelectedProjectId(result.project.id);
        setProjects((current) => [
          ...current.filter((project) => project.id !== result.project?.id),
          result.project as CodingProjectSummary,
        ]);
      }
    } catch (requestError) {
      setRecoveryError(describeError(requestError));
    }
  }, []);

  const loadCapabilities = useCallback(async () => {
    initialDraftLoadSessionRef.current = null;
    setCapabilityState("loading");
    setError("");
    try {
      const result = await getCodingCapabilities();
      setCapabilities(result);
      try {
        const catalog = await getCodingProjects();
        setProjects(catalog.projects.length ? catalog.projects : [BUILTIN_PROJECT]);
        setProjectsError("");
      } catch (requestError) {
        setProjectsError(describeError(requestError));
      }
      if (result.recovery.pending) {
        await loadRecovery();
      } else {
        setRecovery(null);
      }
      if (result.mode !== "draft") {
        setDraftChanges(null);
        setDraftError("");
        setDraftNotice("");
        setVerification(null);
        setVerificationError("");
        setPendingCommand(null);
        setCommandAction("idle");
        setCommandError("");
        setApplyResult(null);
        setApplyError("");
        setCommitResult(null);
        setCommitError("");
        setPublishResult(null);
        setPublishError("");
      }
      setCapabilityState("ready");
    } catch {
      setCapabilities(null);
      setCapabilityState("error");
    }
  }, [loadRecovery]);

  const refreshProjectCatalog = useCallback(
    async (preferredProjectId?: string) => {
      await loadCapabilities();
      if (preferredProjectId) {
        setSelectedProjectId(preferredProjectId);
        setError("");
        setDraftError("");
        setDraftNotice("");
      }
    },
    [loadCapabilities],
  );

  useEffect(() => {
    void loadCapabilities();
    return () => {
      closeStreamRef.current?.();
      if (pendingSessionStoreRef.current) {
        const pendingSession = pendingSessionStoreRef.current;
        storeCodingSession(
          pendingSession.id,
          pendingSession.lastSeq,
          pendingSession.projectId,
        );
      }
      clearPendingStreamRender();
    };
  }, [clearPendingStreamRender, loadCapabilities]);

  useEffect(() => {
    if (!sessionId || projects.some((project) => project.id === selectedProjectId)) {
      return;
    }
    let active = true;
    void getCodingSessionStatus(sessionId)
      .then((result) => {
        if (!active || !result.project) return;
        setSelectedProjectId(result.project.id);
        setProjects((current) => [
          ...current.filter((project) => project.id !== result.project?.id),
          result.project as CodingProjectSummary,
        ]);
      })
      .catch((requestError) => {
        if (
          active &&
          requestError instanceof CodingApiError &&
          requestError.code === "session_not_found"
        ) {
          resetExpiredSession(sessionId);
        }
      });
    return () => {
      active = false;
    };
  }, [projects, resetExpiredSession, selectedProjectId, sessionId]);

  useEffect(() => {
    if (
      !projectSelectionLocked &&
      !projects.some((project) => project.id === selectedProjectId)
    ) {
      setSelectedProjectId("modelmirror");
    }
  }, [projectSelectionLocked, projects, selectedProjectId]);

  useEffect(() => {
    if (
      !isDraftMode ||
      !supportsVerification ||
      !sessionId ||
      !draftChanges?.files.length
    ) {
      setVerification(null);
      setVerificationError("");
      return;
    }
    let active = true;
    setVerificationError("");
    void getCodingVerification(sessionId, draftChanges.revision)
      .then((result) => {
        if (active) setVerification(result);
      })
      .catch((requestError) => {
        if (!active) return;
        if (
          requestError instanceof CodingApiError &&
          requestError.code === "session_not_found"
        ) {
          resetExpiredSession(sessionId);
          return;
        }
        setVerificationError(describeError(requestError));
      });
    return () => {
      active = false;
    };
  }, [
    draftChanges?.files.length,
    draftChanges?.revision,
    isDraftMode,
    resetExpiredSession,
    sessionId,
    supportsVerification,
    verificationAvailable,
  ]);

  useEffect(() => {
    const verificationRevision = verification?.revision;
    if (
      !sessionId ||
      !verificationRunning ||
      verificationRevision === undefined
    ) {
      return;
    }
    let active = true;
    let polling = false;
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const result = await getCodingVerification(
          sessionId,
          verificationRevision,
        );
        if (active) {
          setVerification(result);
          setVerificationError("");
        }
      } catch (requestError) {
        if (!active) return;
        if (
          requestError instanceof CodingApiError &&
          requestError.code === "session_not_found"
        ) {
          resetExpiredSession(sessionId);
          return;
        }
        setVerificationError(describeError(requestError));
      } finally {
        polling = false;
      }
    };
    const timer = window.setInterval(() => void poll(), 1_200);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [
    resetExpiredSession,
    sessionId,
    verification?.revision,
    verificationRunning,
  ]);

  useEffect(() => {
    if (
      !sessionId ||
      !isLocalProject ||
      !supportsCommands ||
      capabilities?.commands.available !== true
    ) {
      setPendingCommand(null);
      setCommandAction("idle");
      setCommandError("");
      return;
    }
    let active = true;
    void getPendingCodingCommand(sessionId)
      .then(({ pending }) => {
        if (!active) return;
        setPendingCommand(pending);
        setCommandError("");
      })
      .catch((requestError) => {
        if (!active) return;
        if (
          requestError instanceof CodingApiError &&
          requestError.code === "session_not_found"
        ) {
          resetExpiredSession(sessionId);
          return;
        }
        setCommandError(describeError(requestError));
      });
    return () => {
      active = false;
    };
  }, [
    capabilities?.commands.available,
    isLocalProject,
    resetExpiredSession,
    sessionId,
    supportsCommands,
  ]);

  const answer = useMemo(
    () =>
      events
        .filter((event) => event.type === "answer_delta")
        .map((event) => event.data.text ?? "")
        .join(""),
    [events],
  );

  const plan = useMemo<CodingPlanEntry[]>(() => {
    const latest = [...events].reverse().find((event) => event.type === "plan");
    return latest?.data.entries ?? [];
  }, [events]);

  const refreshDraftChanges = useCallback(
    async (activeSessionId: string, runValidation: boolean) => {
      setDraftLoading(true);
      setDraftError("");
      try {
        const result = runValidation
          ? await validateCodingChanges(activeSessionId)
          : await getCodingChanges(activeSessionId);
        setDraftChanges(result);
      } catch (requestError) {
        if (
          requestError instanceof CodingApiError &&
          requestError.code === "session_not_found"
        ) {
          resetExpiredSession(activeSessionId);
          return;
        }
        setDraftError(describeError(requestError));
      } finally {
        setDraftLoading(false);
      }
    },
    [resetExpiredSession],
  );

  const refreshCycleHistory = useCallback(async (activeSessionId: string) => {
    try {
      setCycleHistory(await getCodingHistory(activeSessionId));
    } catch (requestError) {
      if (
        !(requestError instanceof CodingApiError) ||
        !["session_not_found", "incremental_unavailable"].includes(requestError.code)
      ) {
        setDraftError(describeError(requestError));
      }
    }
  }, []);

  useEffect(() => {
    if (
      !isDraftMode ||
      !sessionId ||
      !serviceAvailable ||
      isBusy ||
      draftChanges !== null ||
      draftLoading ||
      initialDraftLoadSessionRef.current === sessionId
    ) {
      return;
    }
    initialDraftLoadSessionRef.current = sessionId;
    void refreshDraftChanges(sessionId, false);
  }, [
    draftChanges,
    draftLoading,
    isBusy,
    isDraftMode,
    refreshDraftChanges,
    serviceAvailable,
    sessionId,
  ]);

  useEffect(() => {
    if (
      !isDraftMode ||
      !supportsPublish ||
      !sessionId ||
      !draftChanges?.files.length ||
      commitResult?.state !== "committed" ||
      !commitResult.commit_id
    ) {
      setPublishResult(null);
      setPublishError("");
      return;
    }
    let active = true;
    setPublishError("");
    void getCodingPublishStatus(sessionId, draftChanges.revision)
      .then((result) => {
        if (active) setPublishResult(result);
      })
      .catch((requestError) => {
        if (!active) return;
        if (
          requestError instanceof CodingApiError &&
          requestError.code === "session_not_found"
        ) {
          resetExpiredSession(sessionId);
          return;
        }
        setPublishError(describeError(requestError));
      });
    return () => {
      active = false;
    };
  }, [
    commitResult?.commit_id,
    commitResult?.state,
    draftChanges?.files.length,
    draftChanges?.revision,
    isDraftMode,
    resetExpiredSession,
    sessionId,
    supportsPublish,
  ]);

  useEffect(() => {
    const publishRevision = publishResult?.revision;
    if (!sessionId || !publishRunning || publishRevision === undefined) {
      return;
    }
    let active = true;
    let polling = false;
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const result = await getCodingPublishStatus(sessionId, publishRevision);
        if (active) {
          setPublishResult(result);
          setPublishError("");
        }
      } catch (requestError) {
        if (!active) return;
        if (
          requestError instanceof CodingApiError &&
          requestError.code === "session_not_found"
        ) {
          resetExpiredSession(sessionId);
          return;
        }
        setPublishError(describeError(requestError));
      } finally {
        polling = false;
      }
    };
    const timer = window.setInterval(() => void poll(), 1_200);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [publishResult?.revision, publishRunning, resetExpiredSession, sessionId]);

  useEffect(() => {
    if (publishResult?.state === "draft") {
      setDraftNotice("草稿 PR 已创建；不会自动合并。你可以打开查看或标记为可审阅。");
    } else if (publishResult?.state === "ready") {
      setDraftNotice("PR 已标记为可审阅；系统不会自动合并。");
    } else if (publishResult?.state === "conflict") {
      setDraftNotice("GitHub 上的内容已发生变化，本次任务已停止远程操作。");
    } else if (publishResult?.state === "failed") {
      setDraftNotice("远程操作未完成，本地修改和本地版本仍安全保留。");
    }
  }, [publishResult?.state]);

  useEffect(() => {
    if (
      !isDraftMode ||
      !supportsCommit ||
      !sessionId ||
      capabilities?.incremental?.enabled !== true
    ) {
      setCycleHistory(null);
      return;
    }
    void refreshCycleHistory(sessionId);
  }, [
    capabilities?.incremental?.enabled,
    isDraftMode,
    refreshCycleHistory,
    sessionId,
    supportsCommit,
  ]);

  useEffect(() => {
    if (
      !isDraftMode ||
      !supportsApply ||
      !sessionId ||
      !draftChanges?.files.length
    ) {
      setApplyResult(null);
      setApplyError("");
      return;
    }
    let active = true;
    setApplyError("");
    void getCodingApplyStatus(sessionId, draftChanges.revision)
      .then((result) => {
        if (active) setApplyResult(result);
      })
      .catch((requestError) => {
        if (!active) return;
        if (
          requestError instanceof CodingApiError &&
          requestError.code === "session_not_found"
        ) {
          resetExpiredSession(sessionId);
          return;
        }
        setApplyError(describeError(requestError));
      });
    return () => {
      active = false;
    };
  }, [
    draftChanges?.files.length,
    draftChanges?.revision,
    isDraftMode,
    resetExpiredSession,
    sessionId,
    supportsApply,
  ]);

  useEffect(() => {
    if (
      !isDraftMode ||
      !supportsCommit ||
      !sessionId ||
      !draftChanges?.files.length ||
      applyResult?.state !== "applied" ||
      !applyResult.apply_id
    ) {
      setCommitResult(null);
      setCommitError("");
      return;
    }
    let active = true;
    setCommitError("");
    void getCodingCommitStatus(sessionId, draftChanges.revision)
      .then((result) => {
        if (active) setCommitResult(result);
      })
      .catch((requestError) => {
        if (!active) return;
        if (
          requestError instanceof CodingApiError &&
          requestError.code === "session_not_found"
        ) {
          resetExpiredSession(sessionId);
          return;
        }
        setCommitError(describeError(requestError));
      });
    return () => {
      active = false;
    };
  }, [
    applyResult?.apply_id,
    applyResult?.state,
    draftChanges?.files.length,
    draftChanges?.revision,
    isDraftMode,
    resetExpiredSession,
    sessionId,
    supportsCommit,
  ]);

  const recoverExpiredSession = useCallback(
    async (activeSessionId: string, submittedPrompt: string) => {
      if (sessionProbeInFlightRef.current) return;
      sessionProbeInFlightRef.current = true;
      try {
        await getCodingSessionStatus(activeSessionId);
      } catch (requestError) {
        if (
          requestError instanceof CodingApiError &&
          requestError.code === "session_not_found"
        ) {
          if (resetExpiredSession(activeSessionId)) {
            setPrompt(submittedPrompt);
          }
        }
      } finally {
        sessionProbeInFlightRef.current = false;
      }
    },
    [resetExpiredSession],
  );

  const tools = useMemo<ToolActivity[]>(() => {
    const byId = new Map<string, ToolActivity>();
    events
      .filter((event) => event.type === "tool_status")
      .forEach((event, index) => {
        const id = event.data.tool_call_id || `tool-${index}`;
        byId.set(id, {
          id,
          title:
            event.data.kind === "edit"
              ? "准备修改文件"
              : event.data.title || "代码读取",
          kind: event.data.kind || "read",
          status: event.data.status || "pending",
        });
      });
    return [...byId.values()];
  }, [events]);

  const handleCodingEvent = useCallback(
    (event: CodingEvent) => {
      if (event.seq <= lastSeqRef.current) return;
      lastSeqRef.current = event.seq;
      const eventProject = event.data.project;
      if (eventProject) {
        setSelectedProjectId(eventProject.id);
      }
      queueStreamEvent(event, eventProject?.id ?? selectedProjectId);
      setTransportWarning("");
      if (
        event.type === "command_requested" &&
        event.data.request_id &&
        event.data.command
      ) {
        setPendingCommand({
          command: event.data.command,
          created_at: event.created_at,
          expires_at: event.data.expires_at ?? null,
          request_id: event.data.request_id,
          result: null,
          state: "awaiting_confirmation",
        });
        setCommandAction("idle");
        setCommandError("");
      } else if (event.type === "command_resolved") {
        setPendingCommand((current) =>
          current?.request_id === event.data.request_id ? null : current,
        );
        setCommandAction("idle");
      } else if (event.type === "turn_completed") {
        initialDraftLoadSessionRef.current = event.session_id;
        setPendingCommand(null);
        setCommandAction("idle");
        setRunState("idle");
        if (isDraftMode) {
          setDraftNotice("本轮已完成，修改草稿和检查结果已更新。");
          window.setTimeout(
            () => void refreshDraftChanges(event.session_id, true),
            80,
          );
        }
      } else if (event.type === "cancelled") {
        initialDraftLoadSessionRef.current = event.session_id;
        setPendingCommand(null);
        setCommandAction("idle");
        setRunState("idle");
        if (isDraftMode) {
          setDraftNotice("本轮修改已撤销，此前保留的草稿不受影响。");
          window.setTimeout(
            () => void refreshDraftChanges(event.session_id, false),
            80,
          );
        }
      } else if (event.type === "failed") {
        initialDraftLoadSessionRef.current = event.session_id;
        setPendingCommand(null);
        setCommandAction("idle");
        closeStreamRef.current?.();
        setRunState("error");
        setError(
          errorMessage[event.data.code ?? ""] ??
            "本轮处理未完成，请查看提示后重试。",
        );
        if (isDraftMode) {
          setDraftNotice("本轮修改已撤销，此前保留的草稿不受影响。");
          window.setTimeout(
            () => void refreshDraftChanges(event.session_id, false),
            80,
          );
        } else {
          setSessionId(null);
          lastSeqRef.current = 0;
          clearStoredCodingSession();
        }
      } else if (event.type === "turn_started") {
        setRunState("running");
      }
    },
    [isDraftMode, queueStreamEvent, refreshDraftChanges, selectedProjectId],
  );

  const submitPrompt = async (event?: FormEvent) => {
    event?.preventDefault();
    const question = prompt.trim();
    if (
      !question ||
      !capabilities?.available ||
      !selectedProjectAvailable ||
      runState === "starting" ||
      runState === "running" ||
      runState === "stopping" ||
      verificationActive ||
      sessionFrozen ||
      hasPendingRecovery
    ) {
      return;
    }

    closeStreamRef.current?.();
    setRunState("starting");
    setError("");
    setTransportWarning("");
    setDraftError("");
    setDraftNotice("");
    setPendingCommand(null);
    setCommandAction("idle");
    setCommandError("");
    try {
      let activeSessionId = sessionId;
      if (!activeSessionId) {
        const session = await createCodingSession(selectedProjectId);
        activeSessionId = session.id;
        setSelectedProjectId(session.project.id);
        setSessionId(session.id);
        lastSeqRef.current = 0;
        storeCodingSession(session.id, 0, session.project.id);
        if (isDraftMode) {
          setDraftChanges(null);
          setApplyResult(null);
          setApplyError("");
          setCommitResult(null);
          setCommitError("");
          setPublishResult(null);
          setPublishError("");
        }
      }
      const after = lastSeqRef.current;
      await startCodingTurn(activeSessionId, question);
      setPrompt("");
      setRunState("running");
      closeStreamRef.current = connectCodingEvents(activeSessionId, after, {
        onEvent: handleCodingEvent,
        onTransportError: () => {
          setTransportWarning("回答连接暂时中断，页面正在自动恢复。");
          void recoverExpiredSession(activeSessionId, question);
        },
      });
    } catch (requestError) {
      if (
        !isDraftMode ||
        (requestError instanceof CodingApiError &&
          requestError.code === "session_not_found")
      ) {
        setSessionId(null);
        lastSeqRef.current = 0;
        clearStoredCodingSession();
        setDraftChanges(null);
        setApplyResult(null);
        setApplyError("");
        setCommitResult(null);
        setCommitError("");
        setPublishResult(null);
        setPublishError("");
      }
      setRunState("error");
      setError(describeError(requestError));
    }
  };

  const stopTurn = async () => {
    if (!sessionId || (runState !== "running" && runState !== "starting")) return;
    setRunState("stopping");
    setError("");
    try {
      const result = await cancelCodingTurn(sessionId);
      if (!result.accepted) {
        setRunState("idle");
      }
    } catch (requestError) {
      setRunState("error");
      setError(describeError(requestError));
    }
  };

  const handlePromptKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      void submitPrompt();
    }
  };

  const checkDraft = async () => {
    if (!sessionId) return;
    const result = await validateCodingChanges(sessionId);
    setDraftChanges(result);
    setDraftNotice("检查结果已更新。");
  };

  const discardDraft = async () => {
    if (!sessionId) return;
    const result = await discardCodingChanges(sessionId);
    setDraftChanges(result);
    setRecovery(null);
    setRecoveredState(null);
    setDraftNotice("修改草稿已放弃，临时副本已恢复到最初状态。");
  };

  const runVerification = async () => {
    if (!sessionId || !draftChanges) return;
    setVerificationError("");
    try {
      const result = await startCodingVerification(
        sessionId,
        draftChanges.revision,
      );
      setVerification(result);
    } catch (requestError) {
      setVerificationError(describeError(requestError));
    }
  };

  const stopVerification = async () => {
    if (!sessionId || !verification) return;
    setVerificationError("");
    try {
      const result = await cancelCodingVerification(
        sessionId,
        verification.revision,
      );
      setVerification(result);
    } catch (requestError) {
      setVerificationError(describeError(requestError));
    }
  };

  const confirmVerification = async () => {
    if (!sessionId || !verification?.confirmation_id) return;
    setVerificationError("");
    try {
      const result = await confirmCodingVerification(
        sessionId,
        verification.revision,
        verification.confirmation_id,
      );
      setVerification(result);
    } catch (requestError) {
      setVerificationError(describeError(requestError));
    }
  };

  const decidePendingCommand = async (
    decision: "allow_once" | "reject",
  ) => {
    if (!sessionId || !pendingCommand || commandAction !== "idle") return;
    setCommandAction(decision === "allow_once" ? "allowing" : "rejecting");
    setCommandError("");
    try {
      await decideCodingCommand(
        sessionId,
        pendingCommand.request_id,
        decision,
      );
      setPendingCommand(null);
    } catch (requestError) {
      setCommandError(describeError(requestError));
    } finally {
      setCommandAction("idle");
    }
  };

  const prepareVerificationFix = (nextPrompt: string) => {
    setPrompt(nextPrompt);
    setDraftNotice("问题摘要已填入输入框，你可以确认或补充后再提交。");
    window.requestAnimationFrame(() => {
      promptRef.current?.focus();
      promptRef.current?.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
        block: "center",
      });
    });
  };

  const downloadDraft = async () => {
    if (!sessionId || !draftChanges) return;
    const { blob, filename } = await getCodingPatch(
      sessionId,
      draftChanges.revision,
    );
    downloadFile(blob, filename);
    setDraftNotice("Diff 已下载，真实项目仍未发生改变。");
  };

  const applyDraft = async (confirmQualityRisks: boolean) => {
    if (!sessionId || !draftChanges) return;
    setApplyError("");
    try {
      const result = await applyCodingChanges(
        sessionId,
        draftChanges.revision,
        confirmQualityRisks,
      );
      setApplyResult(result);
      setRecoveredState("applied");
      setCommitResult(null);
      setCommitError("");
      setPublishResult(null);
      setPublishError("");
      setDraftNotice(
        localWriteback
          ? "修改已写入所选本地项目；尚未保存本地版本，也没有上传。"
          : "修改已写入专用项目副本；没有提交或上传，当前项目目录没有改变。",
      );
    } catch (requestError) {
      try {
        setApplyResult(
          await getCodingApplyStatus(sessionId, draftChanges.revision),
        );
      } catch {
        // Keep the original, safer error message.
      }
      throw requestError;
    }
  };

  const revertAppliedDraft = async () => {
    if (!sessionId || !draftChanges || !applyResult?.apply_id) return;
    setApplyError("");
    try {
      const result = await revertCodingApply(
        sessionId,
        draftChanges.revision,
        applyResult.apply_id,
      );
      setApplyResult(result);
      setRecoveredState("reverted");
      setCommitResult(null);
      setCommitError("");
      setPublishResult(null);
      setPublishError("");
      setDraftNotice(
        localWriteback
          ? "本次写入已撤销，所选本地项目已恢复。"
          : "本次应用已撤销，专用项目副本已恢复。",
      );
    } catch (requestError) {
      try {
        setApplyResult(
          await getCodingApplyStatus(sessionId, draftChanges.revision),
        );
      } catch {
        // Keep the original, safer error message.
      }
      throw requestError;
    }
  };

  const commitAppliedDraft = async (message: string) => {
    if (!sessionId || !draftChanges || !applyResult?.apply_id) return;
    setCommitError("");
    try {
      const result = await commitCodingChanges(
        sessionId,
        draftChanges.revision,
        applyResult.apply_id,
        message,
      );
      setCommitResult(result);
      setPublishResult(null);
      setPublishError("");
      await refreshCycleHistory(sessionId);
      setDraftNotice(
        localWriteback
          ? "已保存一个本地版本，目前只保存在所选项目中，不会自动上传。"
          : "已创建本地提交，目前只保存在专用项目副本中，不会自动上传。",
      );
    } catch (requestError) {
      try {
        setCommitResult(
          await getCodingCommitStatus(sessionId, draftChanges.revision),
        );
      } catch {
        // Keep the original, safer error message.
      }
      throw requestError;
    }
  };

  const publishCommittedDraft = async (title: string, body: string) => {
    if (!sessionId || !draftChanges || !commitResult?.commit_id) return;
    setPublishError("");
    try {
      const result = await publishCodingChanges(
        sessionId,
        draftChanges.revision,
        commitResult.commit_id,
        title,
        body,
      );
      setPublishResult(result);
      setDraftNotice("正在检查 GitHub 项目并创建草稿 PR，本地版本不会改变。");
    } catch (requestError) {
      try {
        setPublishResult(
          await getCodingPublishStatus(sessionId, draftChanges.revision),
        );
      } catch {
        // Keep the original, safer error message.
      }
      throw requestError;
    }
  };

  const markPublishedDraftReady = async () => {
    if (!sessionId || !draftChanges || !publishResult?.publish_id) return;
    setPublishError("");
    try {
      const result = await markCodingPublishReady(
        sessionId,
        draftChanges.revision,
        publishResult.publish_id,
      );
      setPublishResult(result);
      setDraftNotice("正在把草稿 PR 标记为可审阅；系统不会自动合并。");
    } catch (requestError) {
      try {
        setPublishResult(
          await getCodingPublishStatus(sessionId, draftChanges.revision),
        );
      } catch {
        // Keep the original, safer error message.
      }
      throw requestError;
    }
  };

  const continueAfterCommit = async () => {
    if (!sessionId || !draftChanges || !commitResult?.commit_id) return;
    setCommitError("");
    const history = await continueCodingSession(
      sessionId,
      draftChanges.revision,
      commitResult.commit_id,
    );
    setCycleHistory(history);
    setDraftChanges(await getCodingChanges(sessionId));
    setVerification(null);
    setApplyResult(null);
    setCommitResult(null);
    setPublishResult(null);
    setPublishError("");
    setRecoveredState(null);
    clearPendingStreamRender();
    setEvents([]);
    setDraftNotice(`已开始第 ${history.active_cycle} 轮修改，此前保存的本地版本不会改变。`);
    window.requestAnimationFrame(() => promptRef.current?.focus());
  };

  const downloadCumulativeDraft = async () => {
    if (!sessionId || !draftChanges) return;
    const { blob, filename } = await getCodingPatch(
      sessionId,
      draftChanges.revision,
      "cumulative",
    );
    downloadFile(blob, filename);
    setDraftNotice("已下载这项任务的全部修改。");
  };

  const undoAppliedCommit = async () => {
    if (
      !sessionId ||
      !draftChanges ||
      !applyResult?.apply_id ||
      !commitResult?.commit_id
    ) {
      return;
    }
    setCommitError("");
    try {
      const result = await undoCodingCommit(
        sessionId,
        draftChanges.revision,
        applyResult.apply_id,
        commitResult.commit_id,
      );
      setCommitResult(result);
      setPublishResult(null);
      setPublishError("");
      setApplyResult(
        await getCodingApplyStatus(sessionId, draftChanges.revision),
      );
      setDraftNotice(
        localWriteback
          ? "本地版本记录已撤销，文件修改仍保留在所选项目中。"
          : "本地提交已撤销，文件修改仍保留在专用项目副本中。",
      );
    } catch (requestError) {
      try {
        setCommitResult(
          await getCodingCommitStatus(sessionId, draftChanges.revision),
        );
      } catch {
        // Keep the original, safer error message.
      }
      throw requestError;
    }
  };

  const closeAppliedSession = async () => {
    if (!sessionId) return;
    await closeCodingSession(sessionId);
    closeStreamRef.current?.();
    closeStreamRef.current = null;
    clearPendingStreamRender();
    initialDraftLoadSessionRef.current = null;
    setSessionId(null);
    lastSeqRef.current = 0;
    clearStoredCodingSession();
    setEvents([]);
    setDraftChanges(null);
    setVerification(null);
    setApplyResult(null);
    setApplyError("");
    setCommitResult(null);
    setCommitError("");
    setPublishResult(null);
    setPublishError("");
    setDraftNotice("本次修改已结束，可以开始新的任务。");
    setRecovery(null);
    setRecoveredState(null);
    setRecoveryConflict(null);
    setRunState("idle");
    await loadCapabilities();
  };

  const resumeRecovery = async () => {
    if (!recovery?.pending || recoveryAction !== "idle") return;
    setRecoveryAction("resuming");
    setRecoveryError("");
    setRecoveryNotice("");
    setError("");
    try {
      const result = await resumeCodingRecovery();
      closeStreamRef.current?.();
      closeStreamRef.current = null;
      clearPendingStreamRender();
      initialDraftLoadSessionRef.current = null;
      setSessionId(result.id);
      setSelectedProjectId(result.project.id);
      setProjects((current) => [
        ...current.filter((project) => project.id !== result.project.id),
        result.project,
      ]);
      lastSeqRef.current = 0;
      storeCodingSession(result.id, 0, result.project.id);
      setEvents([]);
      setDraftChanges(null);
      setVerification(null);
      setApplyResult(null);
      setCommitResult(null);
      setPublishResult(null);
      setPublishError("");
      setRecoveredState(result.status);
      setRecoveryConflict(result.conflict);
      setRunState("idle");
      setDraftNotice(
        result.conflict
          ? "已安全恢复修改内容，但检测到外部变化。现在只允许查看或下载，不会覆盖人工内容。"
          : "已恢复上次保存的修改。此前对话没有保存，你可以重新说明接下来要做什么。",
      );
      if (result.conflict) {
        setRecovery((current) =>
          current
            ? {
                ...current,
                can_resume: false,
                reason: result.conflict,
                state: "conflict",
              }
            : current,
        );
      } else {
        setRecovery(null);
      }
    } catch (requestError) {
      setRecoveryError(describeError(requestError));
    } finally {
      setRecoveryAction("idle");
    }
  };

  const downloadRecovery = async () => {
    if (!recovery?.pending || recoveryAction !== "idle") return;
    setRecoveryAction("downloading");
    setRecoveryError("");
    setRecoveryNotice("");
    try {
      const { blob, filename } = await getCodingRecoveryPatch();
      downloadFile(blob, filename);
      setRecoveryNotice("Diff 已下载，这份保存记录仍会继续保留。");
    } catch (requestError) {
      setRecoveryError(describeError(requestError));
    } finally {
      setRecoveryAction("idle");
    }
  };

  const discardRecovery = async () => {
    if (!recovery?.pending || recoveryAction !== "idle") return;
    setRecoveryAction("discarding");
    setRecoveryError("");
    setRecoveryNotice("");
    try {
      await discardCodingRecovery();
      closeStreamRef.current?.();
      closeStreamRef.current = null;
      clearPendingStreamRender();
      initialDraftLoadSessionRef.current = null;
      setSessionId(null);
      lastSeqRef.current = 0;
      clearStoredCodingSession();
      setEvents([]);
      setDraftChanges(null);
      setVerification(null);
      setApplyResult(null);
      setCommitResult(null);
      setPublishResult(null);
      setPublishError("");
      setRecoveredState(null);
      setRecoveryConflict(null);
      setRecovery(null);
      setRunState("idle");
      setRecoveryNotice("已放弃这份恢复记录，可以开始新的修改任务。");
    } catch (requestError) {
      setRecoveryError(describeError(requestError));
    } finally {
      setRecoveryAction("idle");
    }
  };

  return (
    <PageContainer
      contentClassName="min-w-0"
      maxWidthClassName="max-w-[1360px]"
      sidebar={
        <CodingSidebar
          isDraft={isDraftMode}
          localDraftOnly={localDraftOnly}
          localWriteback={localWriteback}
        />
      }
    >
      <header className="mb-5 border-b border-white/10 pb-5">
        <Link
          className="mb-4 inline-flex items-center gap-2 text-sm font-semibold text-slate-300 transition hover:text-white xl:hidden"
          to="/studio"
        >
          <ArrowLeft aria-hidden="true" size={16} />
          返回 Studio
        </Link>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 rounded-full border border-cyan-300/30 bg-cyan-300/10 px-2.5 py-1 text-xs font-semibold text-cyan-100">
                {isDraftMode ? (
                  <FilePenLine aria-hidden="true" size={14} />
                ) : (
                  <ShieldCheck aria-hidden="true" size={14} />
                )}
                {isLocalProject
                  ? localWriteback
                    ? "修改草稿，确认后写入所选项目"
                    : "修改草稿，不会写入项目"
                  : isDraftMode
                    ? "修改草稿，确认后可应用"
                    : "只读实验"}
              </span>
              <span className="rounded-full border border-white/10 bg-white/[0.045] px-2.5 py-1 text-xs text-slate-300">
                当前项目：{selectedProject?.name ?? "正在读取"}
              </span>
            </div>
            <h1 className="mt-3 text-2xl font-semibold tracking-[-0.025em] text-white sm:text-3xl">
              {isDraftMode ? "代码协作工作台" : "代码问答工作台"}
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
              {isDraftMode
                ? isLocalProject
                  ? localWriteback
                    ? "描述希望调整的内容。代码助手会先在临时副本中准备修改；你可以逐个文件查看、运行检查，再决定是否写入所选本地项目并保存本地版本。"
                    : "描述希望调整的内容。代码助手会在隔离的临时副本中准备修改；你可以逐个文件查看并下载 Diff，本地项目不会被改变。"
                  : "描述希望调整的内容。代码助手会先在临时副本中准备修改；你可以逐个文件查看、运行项目验证，再决定下载 Diff、应用到专用项目副本或保存本地版本。"
                : "你可以询问功能如何实现、页面与服务如何配合，或某段代码的作用。代码助手只能查看项目并回答，不会修改文件或执行命令。"}
            </p>
          </div>
          <button
            className="inline-flex min-h-10 items-center justify-center gap-2 self-start rounded-lg border border-white/10 bg-white/[0.045] px-3 text-sm font-semibold text-slate-200 transition hover:border-cyan-300/35 hover:bg-cyan-300/10 hover:text-cyan-100 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={capabilityState === "loading" || isBusy}
            onClick={() => void loadCapabilities()}
            type="button"
          >
            <RefreshCw
              aria-hidden="true"
              className={
                capabilityState === "loading"
                  ? "animate-spin motion-reduce:animate-none"
                  : ""
              }
              size={16}
            />
            刷新服务状态
          </button>
        </div>
      </header>

      <section className="mb-5 rounded-lg border border-white/10 bg-ink-950/55 p-4">
        <div className="flex items-start gap-3">
          <FolderOpen
            aria-hidden="true"
            className="mt-0.5 shrink-0 text-cyan-200"
            size={19}
          />
          <div className="min-w-0 flex-1">
            <label
              className="text-sm font-semibold text-white"
              htmlFor="coding-project"
            >
              选择要处理的项目
            </label>
            <select
              aria-describedby="coding-project-help"
              className="mt-2 min-h-11 w-full max-w-xl rounded-lg border border-white/15 bg-surface-900 px-3 text-sm text-white outline-none transition focus:border-cyan-300/70 focus:ring-4 focus:ring-cyan-300/10 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={projectSelectionLocked}
              id="coding-project"
              onChange={(event) => {
                setSelectedProjectId(event.target.value);
                setError("");
                setDraftError("");
                setDraftNotice("");
              }}
              value={selectedProjectId}
            >
              {projects.map((project) => (
                <option
                  disabled={project.state !== "available"}
                  key={project.id}
                  value={project.id}
                >
                  {project.name}
                  {project.kind === "builtin"
                    ? "（完整功能）"
                    : project.features.apply && project.features.commit
                      ? "（可写入本地）"
                      : "（修改草稿）"}
                  {project.state !== "available" ? " — 暂不可用" : ""}
                </option>
              ))}
            </select>
            <p className="mt-2 max-w-3xl text-xs leading-5 text-slate-400" id="coding-project-help">
              {selectedProject?.state === "unavailable"
                ? projectReason[selectedProject.reason ?? ""] ??
                  "这个项目目前不能安全读取，请由开发者检查项目状态。"
                : isLocalProject
                  ? localWriteback
                    ? "可查看修改、确认离线检查、写入所选本地项目并保存本地版本；不会自动上传或创建 GitHub PR。"
                    : "可查看、准备修改、确认离线检查并下载 Diff；不会写入本地项目、创建提交或发布 PR。"
                  : "ModelMirror 提供修改审阅、项目验证、受控应用、本地提交和 GitHub 草稿 PR 的完整流程。"}
            </p>
            {projectSelectionLocked ? (
              <p className="mt-1 text-xs leading-5 text-amber-100/80">
                当前任务已绑定此项目。若本轮没有修改，可在下方结束当前任务；否则请先放弃或处理完现有修改。
              </p>
            ) : isLocalProject && localDraftOnly && selectedProject?.writeback_reason ? (
              <p className="mt-1 text-xs leading-5 text-amber-100/80">
                {projectReason[selectedProject.writeback_reason] ??
                  "此项目当前只开放修改草稿，仍可查看、检查和下载修改。"}
              </p>
            ) : projectsError ? (
              <p className="mt-1 text-xs leading-5 text-amber-100/80">
                {capabilityReason[capabilities?.projects.reason ?? ""] ??
                  "本地项目列表暂时不可用，仍可选择 ModelMirror。"}
              </p>
            ) : null}
            <CodingProjectHostPanel
              capability={capabilities?.project_host}
              locked={projectSelectionLocked}
              onProjectsChanged={refreshProjectCatalog}
              selectedProject={selectedProject}
            />
          </div>
        </div>
      </section>

      <section
        aria-live="polite"
        className={`mb-5 flex items-start gap-3 rounded-lg px-4 py-3 ${
          capabilityState === "loading"
            ? "bg-white/[0.045] text-slate-300"
            : workspaceAvailable
              ? "bg-emerald-300/10 text-emerald-100"
              : "bg-amber-300/10 text-amber-100"
        }`}
      >
        {capabilityState === "loading" ? (
          <Clock3 aria-hidden="true" className="mt-0.5 shrink-0" size={18} />
        ) : workspaceAvailable ? (
          <CheckCircle2 aria-hidden="true" className="mt-0.5 shrink-0" size={18} />
        ) : (
          <CircleAlert aria-hidden="true" className="mt-0.5 shrink-0" size={18} />
        )}
        <div>
          <p className="text-sm font-semibold">
            {capabilityState === "loading"
              ? "正在检查代码服务"
              : workspaceAvailable
                ? isDraftMode
                  ? "代码助手可以准备修改草稿"
                  : "代码助手可以使用"
                : serviceAvailable
                  ? "所选项目暂时不可用"
                  : "代码助手暂时不可用"}
          </p>
          <p className="mt-1 text-xs leading-5 opacity-80">
            {capabilityState === "loading"
              ? "确认代码服务安全可用后，输入框会自动开放。"
              : workspaceAvailable
                ? isDraftMode
                  ? isLocalProject
                    ? "修改只保存在临时副本。你可以审阅文件，并确认是否运行离线项目检查。"
                    : "修改会先保存在临时副本。回答结束后，页面会自动列出文件并检查常见问题。"
                  : "一次只处理一个问题，最长可输入 20,000 字符，闲置 30 分钟后会自动清理。"
                : serviceAvailable
                  ? projectReason[selectedProject?.reason ?? ""] ??
                    "所选项目暂时不可用，请重新选择或由开发者检查项目状态。"
                  : capabilityReason[capabilities?.reason ?? ""] ??
                    (error || "暂时无法确认代码服务状态。")}
          </p>
        </div>
      </section>

      {recovery?.pending && (!sessionId || recoveryConflict) ? (
        <CodingRecoveryCard
          action={recoveryAction}
          error={recoveryError}
          notice={recoveryNotice}
          onDiscard={discardRecovery}
          onDownload={downloadRecovery}
          onResume={resumeRecovery}
          recovery={recovery}
        />
      ) : recoveryNotice ? (
        <p
          aria-live="polite"
          className="mb-5 rounded-lg bg-cyan-300/10 px-4 py-3 text-sm text-cyan-100"
          role="status"
        >
          {recoveryNotice}
        </p>
      ) : recoveryError ? (
        <p
          aria-live="assertive"
          className="mb-5 rounded-lg bg-rose-300/10 px-4 py-3 text-sm text-rose-100"
          role="alert"
        >
          {recoveryError}
        </p>
      ) : null}

      <div className="grid min-w-0 gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex min-w-0 flex-col gap-5">
          <section
            className={`order-3 rounded-lg bg-ink-950/72 ${
              isDraftMode ? "" : "min-h-[360px]"
            }`}
          >
            <div className="flex items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
              <div>
                <h2 className="text-sm font-semibold text-white">分析回答</h2>
                <p className="mt-1 text-xs text-slate-500">
                  回答会在生成时逐步显示，内部运行记录不会出现在页面中。
                </p>
              </div>
              <span className="rounded-full bg-white/[0.055] px-2.5 py-1 text-xs text-slate-300">
                {answer && runState === "idle"
                  ? "回答完成"
                  : pendingCommand
                    ? "等待你确认"
                    : statusLabel(runState)}
              </span>
            </div>
            <div
              aria-live="polite"
              className={`${isDraftMode ? "min-h-24" : "min-h-[286px]"} [overflow-anchor:none] p-4 sm:p-5`}
            >
              {answer ? (
                <div className="max-w-none break-words text-sm leading-7 text-slate-200 [&_a]:text-cyan-200 [&_a]:underline [&_blockquote]:my-4 [&_blockquote]:border-l [&_blockquote]:border-white/20 [&_blockquote]:pl-4 [&_code]:text-cyan-100 [&_h1]:mb-4 [&_h1]:text-xl [&_h1]:font-semibold [&_h1]:text-white [&_h2]:mb-3 [&_h2]:mt-6 [&_h2]:text-lg [&_h2]:font-semibold [&_h2]:text-white [&_h3]:mb-2 [&_h3]:mt-5 [&_h3]:font-semibold [&_h3]:text-white [&_li]:my-1 [&_ol]:my-3 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:mb-3 [&_pre]:my-4 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-black/35 [&_pre]:p-4 [&_table]:my-4 [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto [&_td]:border [&_td]:border-white/10 [&_td]:px-3 [&_td]:py-2 [&_th]:border [&_th]:border-white/10 [&_th]:px-3 [&_th]:py-2 [&_ul]:my-3 [&_ul]:list-disc [&_ul]:pl-5">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{answer}</ReactMarkdown>
                  {runState === "running" && !pendingCommand ? (
                    <span
                      aria-label="回答生成中"
                      className="ml-1 inline-block h-4 w-1 bg-cyan-300/80 align-middle"
                    />
                  ) : null}
                </div>
              ) : isBusy ? (
                <div className="space-y-3 opacity-75" aria-label="代码分析中">
                  <div className="h-4 w-4/5 rounded bg-white/10" />
                  <div className="h-4 w-full rounded bg-white/10" />
                  <div className="h-4 w-2/3 rounded bg-white/10" />
                </div>
              ) : (
                <div
                  className={`flex flex-col items-center justify-center text-center ${
                    isDraftMode ? "min-h-24" : "min-h-[250px]"
                  }`}
                >
                  {isDraftMode ? (
                    <FilePenLine
                      aria-hidden="true"
                      className="text-cyan-200"
                      size={28}
                    />
                  ) : (
                    <FileSearch
                      aria-hidden="true"
                      className="text-cyan-200"
                      size={28}
                    />
                  )}
                  <p className="mt-4 text-sm font-semibold text-white">
                    {isDraftMode
                      ? "说清楚想调整什么"
                      : "从一个可验证的问题开始"}
                  </p>
                  <p className="mt-2 max-w-md text-sm leading-6 text-slate-400">
                    {isDraftMode
                      ? "例如：把代码助手页面的空白提示改得更容易理解，并说明修改原因。"
                      : "例如：说明聊天回答如何从服务端显示到页面，并指出出现错误时由哪里处理。"}
                  </p>
                </div>
              )}
            </div>
          </section>

          <form
            className="order-1 rounded-lg border border-white/10 bg-surface-900/88 p-4"
            onSubmit={(event) => void submitPrompt(event)}
          >
            <label className="text-sm font-semibold text-white" htmlFor="coding-prompt">
              {isDraftMode ? "描述希望调整的内容" : "提交代码问题"}
            </label>
            <textarea
              aria-describedby="coding-prompt-help"
              className="mt-3 min-h-32 w-full resize-y rounded-lg border border-white/10 bg-ink-950/75 px-3 py-3 text-sm leading-6 text-white outline-none transition placeholder:text-slate-500 hover:border-white/20 focus:border-cyan-300/70 focus:ring-4 focus:ring-cyan-300/10 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={
                !workspaceAvailable ||
                isBusy ||
                verificationActive ||
                sessionFrozen ||
                hasPendingRecovery
              }
              id="coding-prompt"
              maxLength={capabilities?.limits.max_prompt_chars ?? 20_000}
              onChange={(event) => setPrompt(event.target.value)}
              onKeyDown={handlePromptKeyDown}
              ref={promptRef}
              placeholder={
                isDraftMode
                  ? "用日常语言说明目标和期望结果，不需要填写命令。"
                  : "描述想了解的功能或问题，不需要填写路径和命令。"
              }
              value={prompt}
            />
            <div
              className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
              id="coding-prompt-help"
            >
              <div className="text-xs text-slate-500">
                Ctrl/⌘ + Enter 提交
                <span className="ml-3">
                  {prompt.length.toLocaleString("zh-CN")} /{" "}
                  {(capabilities?.limits.max_prompt_chars ?? 20_000).toLocaleString(
                    "zh-CN",
                  )}
                </span>
              </div>
              <div className="flex gap-2">
                {isBusy ? (
                  <button
                    className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-rose-300/35 bg-rose-300/10 px-4 text-sm font-semibold text-rose-100 transition hover:bg-rose-300/20 disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={runState === "stopping"}
                    onClick={() => void stopTurn()}
                    type="button"
                  >
                    <Square aria-hidden="true" fill="currentColor" size={13} />
                    停止分析
                  </button>
                ) : (
                  <button
                    className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-cyan-300 px-4 text-sm font-semibold text-ink-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
                    disabled={
                      !workspaceAvailable ||
                      !prompt.trim() ||
                      verificationActive ||
                      sessionFrozen ||
                      hasPendingRecovery
                    }
                    type="submit"
                  >
                    <Send aria-hidden="true" size={16} />
                    {isDraftMode ? "开始处理" : "提交问题"}
                  </button>
                )}
              </div>
            </div>
          </form>

          {error || transportWarning ? (
            <div
              aria-live={error ? "assertive" : "polite"}
              className={`order-2 rounded-lg px-4 py-3 text-sm leading-6 ${
                error
                  ? "bg-rose-300/10 text-rose-100"
                  : "bg-amber-300/10 text-amber-100"
              }`}
              role={error ? "alert" : "status"}
            >
              {error || transportWarning}
            </div>
          ) : null}

          {pendingCommand ? (
            <CodingCommandConfirmation
              action={commandAction}
              error={commandError}
              onDecision={decidePendingCommand}
              request={pendingCommand}
            />
          ) : null}

          {draftNotice && isDraftMode ? (
            <div
              aria-live="polite"
              className="order-2 flex items-start gap-2 rounded-lg bg-cyan-300/10 px-4 py-3 text-sm leading-6 text-cyan-100"
              role="status"
            >
              <CheckCircle2
                aria-hidden="true"
                className="mt-0.5 shrink-0"
                size={17}
              />
              {draftNotice}
            </div>
          ) : null}

          {draftError && isDraftMode ? (
            <div
              aria-live="assertive"
              className="order-2 flex items-start gap-2 rounded-lg bg-rose-300/10 px-4 py-3 text-sm leading-6 text-rose-100"
              role="alert"
            >
              <CircleAlert
                aria-hidden="true"
                className="mt-0.5 shrink-0"
                size={17}
              />
              {draftError}
            </div>
          ) : null}

          {isDraftMode ? (
            <div className="order-4">
              <CodingChangesPanel
                applyCapability={effectiveApplyCapability}
                applyError={applyError}
                applyResult={applyResult}
                changes={draftChanges}
                commitCapability={effectiveCommitCapability}
                commitError={commitError}
                commitResult={commitResult}
                disabled={isBusy}
                frozen={sessionFrozen}
                localDraftOnly={localDraftOnly}
                localWriteback={localWriteback}
                loading={draftLoading}
                readOnly={Boolean(recoveryConflict)}
                onApply={applyDraft}
                onClose={closeAppliedSession}
                onCommit={commitAppliedDraft}
                onContinue={
                  !isLocalProject &&
                  capabilities?.incremental?.available &&
                  cycleHistory?.can_continue
                    ? continueAfterCommit
                    : undefined
                }
                onDiscard={discardDraft}
                onDownload={downloadDraft}
                onCancelVerification={stopVerification}
                onConfirmVerification={confirmVerification}
                onMarkPublishReady={markPublishedDraftReady}
                onPublish={publishCommittedDraft}
                onRequestFix={prepareVerificationFix}
                onRunVerification={runVerification}
                onRevert={revertAppliedDraft}
                onUndoCommit={undoAppliedCommit}
                onValidate={checkDraft}
                publishCapability={capabilities?.publish}
                publishError={publishError}
                publishResult={publishResult}
                sessionId={sessionId}
                verification={verification}
                verificationAvailable={verificationAvailable}
                verificationError={verificationError}
              />
              {!isLocalProject ? (
                <CodingHistoryPanel
                  disabled={isBusy}
                  history={cycleHistory}
                  onDownloadAll={downloadCumulativeDraft}
                />
              ) : null}
            </div>
          ) : null}
        </div>

        <aside className="min-w-0 space-y-5">
          <section className="rounded-lg border border-white/10 bg-ink-950/72">
            <div className="border-b border-white/10 px-4 py-3">
              <h2 className="text-sm font-semibold text-white">分析计划</h2>
              <p className="mt-1 text-xs text-slate-500">
                如果问题较复杂，代码助手会在这里列出准备查看的内容。
              </p>
            </div>
            {plan.length ? (
              <ol className="space-y-3 p-4">
                {plan.map((entry, index) => (
                  <li className="flex gap-3 text-sm" key={`${entry.content}-${index}`}>
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/[0.065] text-xs font-semibold text-slate-300">
                      {index + 1}
                    </span>
                    <div className="min-w-0">
                      <p className="break-words leading-6 text-slate-200">
                        {entry.content}
                      </p>
                      <span className="mt-1 inline-block text-xs text-slate-500">
                        {planStatusLabel(entry.status)}
                      </span>
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="p-4 text-sm leading-6 text-slate-500">
                提交问题后，分析步骤会在需要时出现。简单问题可能直接返回回答。
              </p>
            )}
          </section>

          <section className="rounded-lg border border-white/10 bg-ink-950/72">
            <div className="border-b border-white/10 px-4 py-3">
              <h2 className="text-sm font-semibold text-white">查阅记录</h2>
              <p className="mt-1 text-xs text-slate-500">
                显示代码助手查看过的内容，不展示文件原文。
              </p>
            </div>
            {tools.length ? (
              <div className="divide-y divide-white/10">
                {tools.map((tool) => (
                  <details className="group px-4 py-3" key={tool.id}>
                    <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-sm">
                      <span className="min-w-0 truncate font-medium text-slate-200">
                        {tool.title}
                      </span>
                      <span
                        className={`shrink-0 rounded-full px-2 py-1 text-[11px] font-semibold ${
                          tool.status === "completed"
                            ? "bg-emerald-300/10 text-emerald-100"
                            : "bg-white/[0.06] text-slate-300"
                        }`}
                      >
                        {tool.status === "completed" ? "完成" : "进行中"}
                      </span>
                    </summary>
                    <dl className="mt-3 grid grid-cols-[72px_minmax(0,1fr)] gap-2 text-xs">
                      <dt className="text-slate-500">查阅方式</dt>
                      <dd className="break-words text-slate-300">
                        {toolKindLabel(tool.kind)}
                      </dd>
                    </dl>
                  </details>
                ))}
              </div>
            ) : (
              <p className="p-4 text-sm leading-6 text-slate-500">
                文件查看、目录浏览、内容搜索和代码结构分析会按步骤显示在这里。
              </p>
            )}
          </section>
        </aside>
      </div>
    </PageContainer>
  );
}
