const moduleStates = [
  {
    name: "Creator",
    status: "可运行",
    tone: "ready",
    detail: "仅提供 R0 独立模块空壳。",
  },
  {
    name: "父项目适配器",
    status: "未接入",
    tone: "inactive",
    detail: "白名单为空；任何接入均需人工审批。",
  },
  {
    name: "Game Pack",
    status: "未定义",
    tone: "inactive",
    detail: "Schema、Validator 与 Compiler 留待后续轮次。",
  },
  {
    name: "Godot Runtime",
    status: "未创建",
    tone: "inactive",
    detail: "R0 只诊断未来所需的 Godot 4.6.x。",
  },
] as const;

function App() {
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
        <span className="round-badge">内部实验 · R0</span>
      </header>

      <main id="main-content" className="main-content" tabIndex={-1}>
        <section className="intro" aria-labelledby="page-title">
          <p className="eyebrow">Isolation baseline</p>
          <h1 id="page-title">R0 独立模块空壳</h1>
          <p className="summary">
            当前页面用于验证 Creator 可以脱离模镜前端、后端和基础设施独立构建与运行。
          </p>
        </section>

        <section className="status-panel" aria-labelledby="status-title">
          <div className="panel-heading">
            <div>
              <p className="section-label">能力清单</p>
              <h2 id="status-title">当前真实状态</h2>
            </div>
            <span className="boundary-state">父项目交互：none</span>
          </div>

          <div className="table-scroll" tabIndex={0} role="region" aria-label="模块状态表">
            <table>
              <thead>
                <tr>
                  <th scope="col">模块</th>
                  <th scope="col">状态</th>
                  <th scope="col">说明</th>
                </tr>
              </thead>
              <tbody>
                {moduleStates.map((item) => (
                  <tr key={item.name}>
                    <th scope="row">{item.name}</th>
                    <td>
                      <span className={`status status--${item.tone}`}>
                        <span aria-hidden="true">{item.tone === "ready" ? "✓" : "—"}</span>
                        {item.status}
                      </span>
                    </td>
                    <td>{item.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <aside className="scope-note" aria-labelledby="scope-note-title">
          <h2 id="scope-note-title">本轮说明</h2>
          <p>本页面仅验证独立工程边界，不代表引擎功能已完成。</p>
        </aside>
      </main>

      <footer>
        <span>Matrix Oasis Engine</span>
        <span>Private · UNLICENSED</span>
      </footer>
    </div>
  );
}

export default App;
