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

const R0_MARKER = "MATRIX_OASIS_R0_ISOLATED_SHELL";
const R2_MARKER = "MATRIX_OASIS_R2_REFERENCE_SIMULATOR";
const R3_MARKER = "MATRIX_OASIS_R3_RUNTIME_PARITY";

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

function App() {
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
