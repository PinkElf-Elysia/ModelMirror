import { useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import mechanicsPackText from "../../../examples/mechanics-conformance.authoring-game-pack.json?raw";
import integrationPackText from "../../../examples/last-train-r1.authoring-game-pack.json?raw";
import {
  LocalPackLoader,
  MAX_LOCAL_PACK_BYTES,
  prepareCreatorSession,
} from "./pack-loader";
import type { CreatorSessionBundle } from "./pack-loader";
import {
  applySessionActionCandidate,
  resetSessionCandidate,
  selectSessionCandidate,
} from "./session-transaction";
import {
  PROTOTYPE_BUILDER_MARKER,
  PROTOTYPE_RUN_STATES,
  PrototypeBuilderClient,
  PrototypeBuilderClientError,
} from "./prototype-builder";
import type {
  PrototypeBootstrap,
  PrototypeBuilderDiagnostic,
  PrototypeRun,
  PrototypeRunStatus,
} from "./prototype-builder";

const R0_MARKER = "MATRIX_OASIS_R0_ISOLATED_SHELL";
const R2_MARKER = "MATRIX_OASIS_R2_REFERENCE_SIMULATOR";
const R3_MARKER = "MATRIX_OASIS_R3_RUNTIME_PARITY";
const TERMINAL_RUN_STATES = new Set<PrototypeRunStatus>(["ready", "failed"]);

const RUN_STATE_LABELS: Readonly<Record<PrototypeRunStatus, string>> = {
  awaiting_model_approval: "等待模型审批",
  generating: "生成结构化原型",
  awaiting_asset_approval: "等待环境与资产审批",
  acquiring: "获取环境与资产",
  normalizing: "规范化 3D 资产",
  assembling: "组装 Scene Pack",
  ready: "原型可运行",
  failed: "本次生成失败",
};

const BUILTIN_PACKS = {
  neutral: mechanicsPackText,
  integration: integrationPackText,
} as const;

type BuiltinPackId = keyof typeof BUILTIN_PACKS;
type SessionAction = CreatorSessionBundle["inspection"]["actions"][number];

interface WorkspaceState {
  readonly session: CreatorSessionBundle;
}

interface DisplayDiagnostic {
  readonly code: string;
  readonly path: string;
  readonly message: string;
}

interface FeedbackState {
  readonly tone: "neutral" | "working" | "success" | "error";
  readonly message: string;
  readonly diagnostics?: readonly DisplayDiagnostic[];
}

async function createBuiltinSession(
  id: BuiltinPackId,
): Promise<CreatorSessionBundle> {
  const preparedResult = await prepareCreatorSession(BUILTIN_PACKS[id], {
    kind: "builtin",
    id,
  });
  if (!preparedResult.ok) {
    throw new Error("A bundled Pack failed validation.");
  }
  return preparedResult.candidate;
}

const [neutralSession, integrationSession] = await Promise.all([
  createBuiltinSession("neutral"),
  createBuiltinSession("integration"),
]);
const BUILTIN_SESSIONS = Object.freeze({
  neutral: neutralSession,
  integration: integrationSession,
});

function initialWorkspace(): WorkspaceState {
  return {
    session: BUILTIN_SESSIONS.neutral,
  };
}

function diagnosticsFeedback(
  message: string,
  diagnostics: readonly DisplayDiagnostic[],
): FeedbackState {
  return {
    tone: "error",
    message,
    diagnostics,
  };
}

function downloadArtifact(
  text: string,
  fileName: string,
): void {
  const blob = new Blob([text], { type: "application/json;charset=utf-8" });
  const objectUrl = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = fileName;
    anchor.hidden = true;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

function ModeSwitch({
  mode,
  onChange,
}: {
  readonly mode: "prototype" | "runtime";
  readonly onChange: (mode: "prototype" | "runtime") => void;
}) {
  return (
    <nav className="mode-switch" aria-label="Creator 模式">
      <button
        type="button"
        aria-pressed={mode === "prototype"}
        onClick={() => onChange("prototype")}
      >
        Prototype Builder
      </button>
      <button
        type="button"
        aria-pressed={mode === "runtime"}
        onClick={() => onChange("runtime")}
      >
        Runtime / Parity
      </button>
    </nav>
  );
}

function builderError(error: unknown): readonly PrototypeBuilderDiagnostic[] {
  const code =
    error instanceof PrototypeBuilderClientError
      ? error.code
      : "PROTOTYPE_BUILDER_CLIENT_ERROR";
  return Object.freeze([
    Object.freeze({
      phase: "host" as const,
      severity: "error" as const,
      code,
      path: "",
      message: code,
    }),
  ]);
}

function upsertReadyRun(
  runs: readonly PrototypeRun[],
  candidate: PrototypeRun,
): readonly PrototypeRun[] {
  if (candidate.status !== "ready" || !candidate.resultRunId) {
    return runs;
  }
  return Object.freeze([
    candidate,
    ...runs.filter((run) => run.resultRunId !== candidate.resultRunId),
  ]);
}

function PrototypeBuilderPanel() {
  const [bootstrap, setBootstrap] = useState<PrototypeBootstrap | null>(null);
  const [run, setRun] = useState<PrototypeRun | null>(null);
  const [successfulRuns, setSuccessfulRuns] = useState<readonly PrototypeRun[]>([]);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [diagnostics, setDiagnostics] = useState<readonly PrototypeBuilderDiagnostic[]>([]);
  const [announcement, setAnnouncement] = useState("正在连接本地原型宿主。");
  const clientRef = useRef(new PrototypeBuilderClient());
  const statusHeadingRef = useRef<HTMLHeadingElement>(null);
  const promptBytes = new TextEncoder().encode(prompt).byteLength;
  const active = run !== null && !TERMINAL_RUN_STATES.has(run.status);

  function commitRun(candidate: PrototypeRun) {
    setRun(candidate);
    setDiagnostics(candidate.diagnostics);
    setSuccessfulRuns((previous) => upsertReadyRun(previous, candidate));
    if (candidate.status === "ready" && candidate.resultRunId) {
      setCurrentRunId(candidate.resultRunId);
    }
    setAnnouncement(
      candidate.cacheHit && candidate.status === "ready"
        ? "已复用真实资格缓存，未读取凭据或发起外部请求。"
        : RUN_STATE_LABELS[candidate.status],
    );
  }

  useEffect(() => {
    let ignored = false;
    async function load() {
      try {
        const candidate = await clientRef.current.bootstrap();
        if (ignored) return;
        setBootstrap(candidate);
        setSuccessfulRuns(candidate.runs.filter((item) => item.status === "ready"));
        setCurrentRunId(candidate.currentRunId);
        const activeRun = candidate.runs.find((item) => !TERMINAL_RUN_STATES.has(item.status)) ?? null;
        setRun(activeRun);
        setDiagnostics(activeRun?.diagnostics ?? []);
        setAnnouncement(activeRun ? RUN_STATE_LABELS[activeRun.status] : "本地原型宿主已连接。");
      } catch (error) {
        if (ignored) return;
        setDiagnostics(builderError(error));
        setAnnouncement("本地宿主不可用，Runtime / Parity 模式仍可离线使用。");
      }
    }
    void load();
    return () => {
      ignored = true;
    };
  }, []);

  useEffect(() => {
    if (!run || TERMINAL_RUN_STATES.has(run.status)) return undefined;
    let ignored = false;
    let timeoutId: number | undefined;
    async function poll() {
      try {
        const candidate = await clientRef.current.getRun(run!.id);
        if (ignored) return;
        commitRun(candidate);
      } catch (error) {
        if (ignored) return;
        setDiagnostics(builderError(error));
        setAnnouncement("状态刷新失败，当前可运行原型未改变。");
      }
      if (!ignored) timeoutId = window.setTimeout(poll, 1_000);
    }
    timeoutId = window.setTimeout(poll, 1_000);
    return () => {
      ignored = true;
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
  }, [run?.id, run?.status]);

  useEffect(() => {
    if (run) statusHeadingRef.current?.focus();
  }, [run?.status]);

  async function perform(operation: () => Promise<PrototypeRun>, working: string) {
    setBusy(true);
    setDiagnostics([]);
    setAnnouncement(working);
    try {
      commitRun(await operation());
    } catch (error) {
      setDiagnostics(builderError(error));
      setAnnouncement("操作未完成，上一份可运行原型未改变。");
    } finally {
      setBusy(false);
    }
  }

  function createRun() {
    if (promptBytes < 1 || promptBytes > 32_768 || active) return;
    void perform(
      () => clientRef.current.createRun(prompt),
      "正在检查缓存与生成请求。",
    );
  }

  function approveModel() {
    if (!run?.modelApproval) return;
    void perform(
      () => clientRef.current.approveModel(run),
      "已提交本次模型上传审批。",
    );
  }

  function approveAssets() {
    if (!run?.assetApproval) return;
    void perform(
      () => clientRef.current.approveAssets(run),
      "已提交本次环境与资产物化审批。",
    );
  }

  async function launch(candidate: PrototypeRun | null) {
    if (!candidate || candidate.status !== "ready") return;
    setBusy(true);
    setDiagnostics([]);
    setAnnouncement("正在启动 Godot 原型预览。");
    try {
      await clientRef.current.launch(candidate);
      setAnnouncement("Godot 已启动；当前 run 与历史记录保持不变。");
    } catch (error) {
      setDiagnostics(builderError(error));
      setAnnouncement("Godot 未启动，上一份可运行原型仍可重试。");
    } finally {
      setBusy(false);
    }
  }

  const currentRun =
    successfulRuns.find((item) => item.resultRunId === currentRunId) ?? null;

  return (
    <>
      <section className="page-heading builder-heading" aria-labelledby="builder-title">
        <div>
          <h1 id="builder-title">自然语言 3D 原型生成器</h1>
          <p>
            输入一段纯文本，经内容绑定审批生成 panorama 环境、碰撞、静态资产和
            Scene Pack；失败不会替换当前可运行结果。
          </p>
        </div>
        <div className="builder-readiness" aria-label="本地配置状态">
          {(["model", "assets", "godot"] as const).map((name) => (
            <span key={name} data-ready={bootstrap?.readiness[name] === true}>
              {name === "model" ? "模型" : name === "assets" ? "环境与资产" : "Godot"}
              {bootstrap?.readiness[name] === true ? "已就绪" : "未就绪"}
            </span>
          ))}
        </div>
      </section>

      <section className="current-prototype" aria-labelledby="current-title">
        <div>
          <p className="context-line">当前可运行原型</p>
          <h2 id="current-title">
            {currentRunId ? "已验证 run 可随时重新启动" : "尚无可运行原型"}
          </h2>
          <p>
            {currentRunId
              ? "新候选只有在完整验证和原子发布成功后才会替换这里。"
              : "首次成功组装后，此处会成为失败保护的稳定回退点。"}
          </p>
        </div>
        {currentRun ? (
          <button
            className="primary-button"
            type="button"
            disabled={busy || bootstrap?.readiness.godot !== true}
            onClick={() => void launch(currentRun)}
          >
            重新启动当前原型
          </button>
        ) : null}
      </section>

      <div className="builder-layout">
        <section className="builder-workspace" aria-labelledby="prompt-title">
          <div className="builder-section-heading">
            <div>
              <h2 id="prompt-title">描述要生成的原型</h2>
              <p>仅接受文本。原始提示只驻留内存，不写入 run 目录。</p>
            </div>
            <span className={promptBytes > 32_768 ? "byte-count byte-count--error" : "byte-count"}>
              {promptBytes} / 32768 B
            </span>
          </div>
          <label className="prompt-field">
            <span>原型描述</span>
            <textarea
              rows={7}
              value={prompt}
              disabled={active}
              onChange={(event) => setPrompt(event.currentTarget.value)}
              placeholder="例如：一个可漫游的中性研究站，包含一台可检查的设备和一名静态引导员。"
            />
          </label>
          <div className="builder-submit-row">
            <button
              className="primary-button"
              type="button"
              disabled={busy || active || !bootstrap || promptBytes < 1 || promptBytes > 32_768}
              onClick={createRun}
            >
              生成原型
            </button>
            <p>缓存命中时不会读取 API Key，也不会出现外部审批。</p>
          </div>

          {run ? (
            <section className="run-progress" aria-labelledby="run-status-title">
              <div className="builder-section-heading">
                <div>
                  <p className="context-line">候选 {run.id}</p>
                  <h2 id="run-status-title" ref={statusHeadingRef} tabIndex={-1}>
                    {RUN_STATE_LABELS[run.status]}
                  </h2>
                </div>
                {run.cacheHit ? <span className="cache-label">已复用真实资格缓存</span> : null}
              </div>
              <ol className="stage-list" aria-label="生成阶段">
                {PROTOTYPE_RUN_STATES.filter((state) => state !== "failed").map((state) => {
                  const currentIndex = PROTOTYPE_RUN_STATES.indexOf(run.status);
                  const stateIndex = PROTOTYPE_RUN_STATES.indexOf(state);
                  const reached = run.status === "failed" ? false : stateIndex <= currentIndex;
                  return (
                    <li key={state} aria-current={run.status === state ? "step" : undefined} data-reached={reached}>
                      <span aria-hidden="true">{reached ? "✓" : "·"}</span>
                      {RUN_STATE_LABELS[state]}
                    </li>
                  );
                })}
              </ol>
            </section>
          ) : null}

          {run?.status === "awaiting_model_approval" && run.modelApproval ? (
            <section className="approval-panel" aria-labelledby="model-approval-title">
              <div>
                <p className="context-line">审批 1 / 2</p>
                <h2 id="model-approval-title">确认模型生成上传</h2>
                <p>以下当前提示将上传到所列模型；本次最多 3 个请求，费用硬上限 1 美元。</p>
              </div>
              <dl className="approval-facts">
                <div><dt>Endpoint host</dt><dd>{run.modelApproval.endpointHost}</dd></div>
                <div><dt>模型</dt><dd>{run.modelApproval.model}</dd></div>
                <div><dt>提示 SHA-256</dt><dd><code>{run.modelApproval.promptSha256}</code></dd></div>
              </dl>
              <blockquote>{run.modelApproval.prompt}</blockquote>
              <button className="primary-button" type="button" disabled={busy || bootstrap?.readiness.model !== true} onClick={approveModel}>
                批准模型生成
              </button>
            </section>
          ) : null}

          {run?.status === "awaiting_asset_approval" && run.assetApproval ? (
            <section className="approval-panel" aria-labelledby="asset-approval-title">
              <div>
                <p className="context-line">审批 2 / 2</p>
                <h2 id="asset-approval-title">确认环境与资产物化</h2>
                <p>审批绑定当前 Blueprint；内容或哈希变化后必须重新批准。</p>
              </div>
              <dl className="approval-facts">
                <div><dt>Blueprint SHA-256</dt><dd><code>{run.assetApproval.blueprintSha256}</code></dd></div>
                <div><dt>Marble</dt><dd>1 次创建 · 180 次有界轮询 · 2 次下载 · 1600 credits / 1.50 美元</dd></div>
                <div><dt>Meshy</dt><dd>{run.assetApproval.meshy.briefs.length} 个 brief · 最多 {run.assetApproval.meshy.maxTasks} 个任务 · {run.assetApproval.meshy.creditLimit} credits</dd></div>
              </dl>
              <div className="upload-summary">
                <strong>Marble 环境文本</strong>
                <p>{run.assetApproval.marble.environmentPrompt}</p>
                {run.assetApproval.meshy.briefs.length > 0 ? (
                  <ul>
                    {run.assetApproval.meshy.briefs.map((brief) => (
                      <li key={brief.id}><strong>{brief.kind}</strong><span>{brief.prompt}</span></li>
                    ))}
                  </ul>
                ) : <p>本次没有 Meshy 非环境 brief。</p>}
              </div>
              <button className="primary-button" type="button" disabled={busy || bootstrap?.readiness.assets !== true} onClick={approveAssets}>
                批准环境与资产物化
              </button>
            </section>
          ) : null}

          {run?.status === "ready" ? (
            <div className="ready-actions">
              <div>
                <strong>{run.cacheHit ? "缓存已复验" : "原型已组装"}</strong>
                <span>Scene Pack、环境、资产与 Runtime 身份均已通过离线复验。</span>
              </div>
              <button className="primary-button" type="button" disabled={busy || bootstrap?.readiness.godot !== true} onClick={() => void launch(run)}>
                启动预览
              </button>
            </div>
          ) : null}
        </section>

        <aside className="builder-sidebar" aria-label="状态与历史">
          <section aria-labelledby="feedback-title">
            <h2 id="feedback-title">运行反馈</h2>
            <p className="builder-announcement" aria-live="polite" aria-atomic="true">{announcement}</p>
            {diagnostics.length > 0 ? (
              <ul className="builder-diagnostics">
                {diagnostics.map((item) => (
                  <li key={`${item.code}:${item.path}`}><code>{item.code}</code>{item.path ? <span>{item.path}</span> : null}</li>
                ))}
              </ul>
            ) : null}
          </section>
          <section aria-labelledby="history-title">
            <div className="section-heading-row"><h2 id="history-title">成功记录</h2><span>{successfulRuns.length} 项</span></div>
            {successfulRuns.length > 0 ? (
              <ul className="run-history">
                {successfulRuns.map((item) => (
                  <li key={item.resultRunId ?? item.id} data-current={item.resultRunId === currentRunId}>
                    <div><strong>{item.resultRunId === currentRunId ? "当前" : "历史"}</strong><span>{item.cacheHit ? "缓存" : "新生成"}</span></div>
                    <code>{item.resultRunId}</code>
                    <button type="button" className="text-button" disabled={busy || bootstrap?.readiness.godot !== true} onClick={() => void launch(item)}>
                      启动此原型
                    </button>
                  </li>
                ))}
              </ul>
            ) : <p className="empty-state">尚无成功 run。失败候选不会写入此列表。</p>}
          </section>
          <p className="builder-scope-note">环境为 360° panorama 加独立 collider，不具备视差环境网格。浏览器仅访问同源 loopback 宿主。</p>
        </aside>
      </div>
    </>
  );
}

function App() {
  const [mode, setMode] = useState<"prototype" | "runtime">("prototype");
  const [workspace, setWorkspace] = useState<WorkspaceState>(initialWorkspace);
  const [feedback, setFeedback] = useState<FeedbackState>({
    tone: "neutral",
    message: `已加载 ${BUILTIN_SESSIONS.neutral.inspection.pack.title}，选择可用操作以执行一步。`,
  });
  const [focusRevision, setFocusRevision] = useState(0);
  const loaderRef = useRef(new LocalPackLoader());
  const activeSessionRef = useRef(workspace.session);
  const locationHeadingRef = useRef<HTMLHeadingElement>(null);

  const { session } = workspace;
  const { inspection } = session;
  const isEnded = inspection.status === "ended";
  const activeBuiltin = session.source.kind === "builtin" ? session.source.id : null;
  const artifactIdentity = session.snapshot.runtime.pack;
  const artifactBytes = new TextEncoder().encode(
    session.artifact.runtimePackJson,
  ).byteLength;
  const receiptBytes = new TextEncoder().encode(
    session.artifact.runtimePackReceiptJson,
  ).byteLength;

  useEffect(() => {
    if (focusRevision > 0) {
      locationHeadingRef.current?.focus();
    }
  }, [focusRevision]);

  if (mode === "prototype") {
    return (
      <div className="app-shell">
        <a className="skip-link" href="#main-content">跳到主要内容</a>
        <header className="topbar">
          <div className="brand-lockup" aria-label="矩阵绿洲实验模块">
            <span className="brand-mark" aria-hidden="true">MO</span>
            <span><strong>矩阵绿洲</strong><small>Matrix Oasis Engine</small></span>
          </div>
          <ModeSwitch mode={mode} onChange={setMode} />
          <div className="round-meta" aria-label="模块状态"><span>独立模块</span><strong>R10 初版闭环</strong></div>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1}>
          <span className="sr-only">{R0_MARKER} {R2_MARKER} {R3_MARKER} {PROTOTYPE_BUILDER_MARKER}</span>
          <PrototypeBuilderPanel />
        </main>
        <footer><span>Matrix Oasis Engine</span><span>Private · UNLICENSED · Parent integration: none</span></footer>
      </div>
    );
  }

  function commitSession(
    candidate: CreatorSessionBundle,
    expectedSession: CreatorSessionBundle = activeSessionRef.current,
  ): boolean {
    const decision = selectSessionCandidate(
      activeSessionRef.current,
      expectedSession,
      candidate,
    );
    if (!decision.committed) {
      return false;
    }
    activeSessionRef.current = decision.session;
    setWorkspace({ session: decision.session });
    return true;
  }

  function selectBuiltin(id: BuiltinPackId) {
    loaderRef.current = new LocalPackLoader();
    try {
      const candidate = BUILTIN_SESSIONS[id];
      commitSession(candidate);
      setFeedback({
        tone: "success",
        message: `已切换到 ${candidate.inspection.pack.title} 并创建新会话。`,
      });
      setFocusRevision((revision) => revision + 1);
    } catch {
      setFeedback({
        tone: "error",
        message: "内置 Pack 无法创建会话，当前会话未改变。",
      });
    }
  }

  async function selectLocalFile(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const file = input.files?.item(0);
    if (!file) {
      return;
    }

    const loader = loaderRef.current;
    const baseSession = activeSessionRef.current;
    setFeedback({
      tone: "working",
      message: "正在验证本地 JSON，当前会话保持可用。",
    });

    try {
      const result = await loader.loadCandidate(file, baseSession);
      if (loaderRef.current !== loader || result.status === "stale") {
        return;
      }
      if (result.status === "rejected") {
        setFeedback(
          diagnosticsFeedback(
            "候选 Pack 未通过加载或验证，当前会话未改变。",
            result.diagnostics,
          ),
        );
        return;
      }
      if (!commitSession(result.candidate, baseSession)) {
        return;
      }
      setFeedback({
        tone: "success",
        message: `已验证 ${result.candidate.inspection.pack.title}，并原子替换当前会话。`,
      });
      setFocusRevision((revision) => revision + 1);
    } catch {
      if (loaderRef.current === loader) {
        setFeedback({
          tone: "error",
          message: "本地候选加载意外中断，当前会话未改变。",
        });
      }
    } finally {
      input.value = "";
    }
  }

  function resetSession(baseSession: CreatorSessionBundle) {
    if (activeSessionRef.current !== baseSession) {
      return;
    }
    const result = resetSessionCandidate(baseSession);
    if (!result.ok) {
      setFeedback(
        diagnosticsFeedback("会话无法重置，当前状态未改变。", result.diagnostics),
      );
      return;
    }
    if (!commitSession(result.candidate, baseSession)) {
      return;
    }
    setFeedback({
      tone: "success",
      message: "已重置当前 Pack，会话回到入口节点。",
    });
    setFocusRevision((revision) => revision + 1);
  }

  function executeAction(
    baseSession: CreatorSessionBundle,
    action: SessionAction,
  ) {
    if (activeSessionRef.current !== baseSession) {
      return;
    }
    const result = applySessionActionCandidate(baseSession, action.id);
    if (!result.ok) {
      setFeedback(
        diagnosticsFeedback("该操作未执行，当前状态未改变。", result.diagnostics),
      );
      return;
    }
    if (!commitSession(result.candidate, baseSession)) {
      return;
    }
    setFeedback({
      tone: "success",
      message: `已执行操作“${action.label}”。`,
    });
    setFocusRevision((revision) => revision + 1);
  }

  function downloadCurrentArtifact(kind: "runtime" | "receipt") {
    try {
      if (kind === "runtime") {
        downloadArtifact(
          session.artifact.runtimePackJson,
          `${inspection.pack.id}.runtime-game-pack.json`,
        );
      } else {
        downloadArtifact(
          session.artifact.runtimePackReceiptJson,
          `${inspection.pack.id}.runtime-game-pack-receipt.json`,
        );
      }
      setFeedback({
        tone: "success",
        message:
          kind === "runtime"
            ? "已生成 Runtime Pack 下载。"
            : "已生成 Receipt 下载。",
      });
    } catch {
      setFeedback({
        tone: "error",
        message: "无法生成下载，当前会话未改变。",
      });
    }
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>

      <header className="topbar">
        <div className="brand-lockup" aria-label="矩阵绿洲实验模块">
          <span className="brand-mark" aria-hidden="true">
            MO
          </span>
          <span>
            <strong>矩阵绿洲</strong>
            <small>Matrix Oasis Engine</small>
          </span>
        </div>
        <ModeSwitch mode={mode} onChange={setMode} />
        <div className="round-meta" aria-label="模块状态">
          <span>独立模块</span>
          <strong>R3 运行语义等价</strong>
        </div>
      </header>

      <main id="main-content" className="main-content" tabIndex={-1}>
        <span className="sr-only">
          {R0_MARKER} {R2_MARKER} {R3_MARKER}
        </span>

        <section className="page-heading" aria-labelledby="page-title">
          <div>
            <h1 id="page-title">Creator 语义等价实验台</h1>
            <p>
              加载一个 Authoring Game Pack，同时推进参考模型与编译后 Runtime，
              只在两侧结果一致时提交下一步。
            </p>
          </div>
          <dl className="pack-summary" aria-label="当前 Pack">
            <div>
              <dt>当前输入</dt>
              <dd>{inspection.pack.title}</dd>
            </div>
            <div>
              <dt>Pack</dt>
              <dd>{inspection.pack.id}</dd>
            </div>
            <div>
              <dt>内容版本</dt>
              <dd>{inspection.pack.contentVersion}</dd>
            </div>
          </dl>
        </section>

        <section className="parity-strip" aria-labelledby="parity-title">
          <div>
            <p className="context-line">R2 Reference ↔ R3 Runtime</p>
            <h2 id="parity-title">编译前后锁步一致</h2>
            <p>
              当前会话的创建与每次操作均已通过可观察语义对照；Receipt
              只证明产物完整性，不是签名或来源认证。
            </p>
          </div>
          <dl className="artifact-summary" aria-label="当前运行产物">
            <div>
              <dt>Runtime Pack</dt>
              <dd>{artifactBytes} B</dd>
            </div>
            <div>
              <dt>Receipt</dt>
              <dd>{receiptBytes} B</dd>
            </div>
          </dl>
          <div className="artifact-actions" aria-label="下载当前运行产物">
            <button
              className="secondary-button"
              type="button"
              onClick={() => downloadCurrentArtifact("runtime")}
            >
              下载 Runtime Pack
            </button>
            <button
              className="secondary-button"
              type="button"
              onClick={() => downloadCurrentArtifact("receipt")}
            >
              下载 Receipt
            </button>
          </div>
          <details className="integrity-details">
            <summary>查看完整性标识</summary>
            <dl>
              <div>
                <dt>Source SHA-256</dt>
                <dd><code>{artifactIdentity.sourceSha256}</code></dd>
              </div>
              <div>
                <dt>Artifact SHA-256</dt>
                <dd><code>{artifactIdentity.artifactSha256}</code></dd>
              </div>
            </dl>
          </details>
        </section>

        <section className="input-panel" aria-labelledby="input-title">
          <div className="input-heading">
            <div>
              <h2 id="input-title">选择测试输入</h2>
              <p>
                切换输入会先编译并完成双侧创建；验证、编译或等价检查失败时不会替换当前会话。
              </p>
            </div>
            <button
              className="secondary-button"
              type="button"
              onClick={() => resetSession(session)}
            >
              重置当前会话
            </button>
          </div>

          <div className="source-controls" role="group" aria-label="内置 Pack">
            {(Object.keys(BUILTIN_PACKS) as BuiltinPackId[]).map((id) => (
              <button
                className="source-button"
                type="button"
                key={id}
                aria-pressed={activeBuiltin === id}
                onClick={() => selectBuiltin(id)}
              >
                <strong>{BUILTIN_SESSIONS[id].inspection.pack.title}</strong>
                <span>
                  {BUILTIN_SESSIONS[id].inspection.pack.summary ??
                    "该 Pack 未提供摘要。"}
                </span>
              </button>
            ))}
          </div>

          <div className="file-row">
            <label htmlFor="local-pack-file">加载本地 JSON</label>
            <input
              id="local-pack-file"
              type="file"
              accept=".json,application/json"
              aria-describedby="local-file-help"
              onChange={selectLocalFile}
            />
          <p id="local-file-help">
              仅在本机内存中读取，限制 {MAX_LOCAL_PACK_BYTES / 1_048_576} MiB，
              不上传、不自动保存。
            </p>
          </div>

          <div
            className={`feedback feedback--${feedback.tone}`}
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            <strong>{feedback.message}</strong>
            {feedback.diagnostics && feedback.diagnostics.length > 0 ? (
              <ul>
                {feedback.diagnostics.slice(0, 4).map((diagnostic, index) => (
                  <li key={`${diagnostic.code}:${diagnostic.path}:${index}`}>
                    <code>{diagnostic.code}</code>
                    <span>{diagnostic.path}</span>
                    <span>{diagnostic.message}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </section>

        <div className="workspace-layout">
          <section className="play-surface" aria-labelledby="location-title">
            <header className="surface-heading">
              <div>
                <p className="context-line">
                  {inspection.location.kind === "ending" ? "Ending" : "Node"} ·{" "}
                  {inspection.location.id}
                </p>
                <h2 id="location-title" ref={locationHeadingRef} tabIndex={-1}>
                  {inspection.location.title}
                </h2>
              </div>
              <span className={`session-status session-status--${inspection.status}`}>
                {isEnded ? "会话已结束" : "会话进行中"}
              </span>
            </header>

            <p className="location-text">
              {inspection.location.text ?? "当前位置未提供说明文本。"}
            </p>

            {inspection.location.entityIds.length > 0 ? (
              <div className="entity-line">
                <span>关联实体</span>
                <ul>
                  {inspection.location.entityIds.map((entityId) => (
                    <li key={entityId}>{entityId}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            <div className="actions-section">
              <div className="section-heading-row">
                <h3>可选操作</h3>
                <span>{inspection.actions.length} 项</span>
              </div>
              {inspection.actions.length > 0 ? (
                <div className="action-list">
                  {inspection.actions.map((action) => (
                    <button
                      className="action-button"
                      type="button"
                      key={action.id}
                      disabled={!action.available}
                      onClick={() => executeAction(session, action)}
                    >
                      <span>
                        <strong>{action.label}</strong>
                        <code>{action.id}</code>
                      </span>
                      <span>{action.available ? "执行一步" : "条件未满足"}</span>
                    </button>
                  ))}
                </div>
              ) : (
                <p className="empty-state">
                  当前位于 ending，没有后续操作。可重置会话或切换测试输入。
                </p>
              )}
            </div>
          </section>

          <aside className="state-panel" aria-label="会话状态与最近结果">
            <section aria-labelledby="step-title">
              <div className="section-heading-row">
                <h2 id="step-title">会话状态</h2>
                <span>
                  Step {inspection.stepCount} / {inspection.stepLimit}
                </span>
              </div>
              <dl className="variable-list">
                {inspection.variables.map((variable) => (
                  <div key={variable.id}>
                    <dt>
                      <span>{variable.id}</span>
                      <small>{variable.type}</small>
                    </dt>
                    <dd>{String(variable.value)}</dd>
                  </div>
                ))}
              </dl>
            </section>

            <section aria-labelledby="transition-title">
              <h2 id="transition-title">最近 Transition</h2>
              {session.transition ? (
                <dl className="transition-list">
                  <div>
                    <dt>操作</dt>
                    <dd>{session.transition.actionId}</dd>
                  </div>
                  <div>
                    <dt>来源</dt>
                    <dd>{session.transition.from.id}</dd>
                  </div>
                  <div>
                    <dt>目标</dt>
                    <dd>
                      {session.transition.to.kind}:{session.transition.to.id}
                    </dd>
                  </div>
                </dl>
              ) : (
                <p className="empty-state">尚未执行操作，当前为会话初始状态。</p>
              )}
            </section>

            <section aria-labelledby="cue-title">
              <div className="section-heading-row">
                <h2 id="cue-title">
                  {session.transition ? "本步 Cue" : "初始化 Cue"}
                </h2>
                <span>{session.emittedCues.length} 项</span>
              </div>
              {session.emittedCues.length > 0 ? (
                <ul className="cue-list">
                  {session.emittedCues.map((cue, index) => (
                    <li key={`${cue.id}:${index}`}>
                      <div>
                        <strong>{cue.id}</strong>
                        <span>{cue.channel}</span>
                      </div>
                      <p>{cue.intent}</p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="empty-state">本次状态变化没有发出 Cue。</p>
              )}
            </section>
          </aside>
        </div>

        <p className="boundary-note">
          此实验台只调用模块内 Validator、Compiler、参考模拟器、Runtime 模拟器与等价 Harness，
          不连接父项目 API、网络或持久化服务。
        </p>
      </main>

      <footer>
        <span>Matrix Oasis Engine</span>
        <span>Private · UNLICENSED · Parent integration: none</span>
      </footer>
    </div>
  );
}

export default App;
