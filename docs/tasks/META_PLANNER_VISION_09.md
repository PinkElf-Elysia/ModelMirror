# META-PLANNER-VISION-09 实施记录

## 范围与基线

- 分支：`codex/meta-planner-vision-09`。
- 固定起点：`648808017445cecf801bcc6bb9f174ce99468f85`，包含已合并的 PR #360。
- PR #360 Backend quality 通过；AI Research 范围检查失败另行归档，不声称全部 CI 通过。
- 仅新增 Planner `vision_understanding` 能力，18 类增至 19 类。Graph IR 保持 V3。
- 修改范围：视觉服务与运行适配、节点契约、Meta Planner/Headless、FileAsset/Evaluator、对应前端、测试和文档。
- 禁止范围：共享栈、RAG 主线、写节点、公共 App、Structure Evolution、通用附件委托、依赖升级和存储迁移。

## 分批门禁

1. 核心已实现并通过重点测试：视觉 V2 的 Managed-only、严格输出、页数拒绝、调用预算和旧契约回归。
2. 核心已实现并通过重点测试：可信单附件输入与用户固定视觉模型。
3. 已集成并通过重点测试：Adapter、Graph IR、Headless 往返与 TOCTOU。
4. 已实现并通过底层与 Store 测试：评测专用附件、不可变版本引用、固定夹具与删除保护。
5. 已集成并通过模拟 Provider 全链路测试及授权真实复测：视觉路径指标、安全回执、不确定请求恢复。真实复测详情见下方。
6. 前端安全投影、生产构建、Linux 全量验证和帮助中心附件准备教程重放通过；用户已确认真实复测通过并授权收尾后提交 PR。

每批再按一个可验证目标拆分文件集。跨契约集成可能超过五个文件时，分别验证核心和调用侧，不以单个大批次隐藏风险。

## 协作

Astra 负责契约、作用域、计量、集成和验收。Sol 分别在独立 Adapter、附件夹具与评测前端子工作树完成限定文件任务，产出经主智能体复核后集成。不得同时操作同一工作树、浏览器或容器。

## 验收与回退

- 先执行确定性单元与证伪测试，再执行受影响模块、全量 `server/tests/`、语法、前端生产构建和敏感信息扫描。
- 重点拒绝：资产 ID 伪造、跨作用域、内容篡改、Binding 漂移、未执行视觉却答对、页数或并发超限、恢复重复派发。
- 自动化模拟不能证明真实 Provider 就绪；真实 Planner、视觉和下游 Agent 调用分别等待本轮明确授权。
- 不自动批准 Proposal、不发布 Xpert；人工验收通过后才进入 PR 门禁。
- 回退时关闭新增 Planner Adapter 与视觉评测入口，保留 V2 节点和已发布附件版本读取兼容。

## 当前证据

- 已核验干净工作树与远端基线。
- 重点测试：Planner/Adapter/Vision V2 94 条通过；旧视觉/RAG/Managed 路由 29 条通过；最终附件夹具与下载 14 条、Store/API/恢复 15 条通过。各命令均最终 exit 0。
- 新增全链路与证据攻击测试 33 条通过，包含真实本地图片/PDF 解码，但 Provider 为模拟，不代表外部模型验收。
- 最后预检/Planner/Headless 回归 84 条通过；最终前端六组重点测试共 93 条通过；`npm.cmd run build` 通过，保留既有大 bundle warning。
- Windows 扩展回归中，FileAsset 的 11 个失败和 2 个错误已在干净 `64880801` 基线复现：文本换行差异、Office sidecar 短超时和超长 pytest 参数超过 Windows 环境变量上限。另两条能力计数断言已随 19 类能力更新并复测通过。
- Windows 全量测试未跑完，已停止本轮所属进程；用户单独授权后，改用无共享挂载、无外网的 Linux 容器继续全量验证。早期尝试因测试插件、离线 worker 构建产物和进程/线程额度问题失败或中断，失败回执保留；环境修正不改变生产依赖。
- 最终 Linux r4：`5875 passed, 29 skipped, 6 warnings`，耗时 1008.40 秒，最终 exit 0。使用 Python 3.12.14、pytest 9.1.0、pytest-asyncio 1.4.0、Pydantic 2.13.4、Pillow 11.3.0、pypdfium2 4.30.0，并固定 BLAS/OMP 单线程。
- r4 来源为固定基线加 overlay SHA-256 `4645c07d16679fbc8bf2936e56bddced11a8fb6ef8c5312c708397d4a91155a6`；当前变更中的 44 个后端 Python 文件与已测容器逐一 hash 比对完全一致。其后仅修正文档的保存顺序和验收记录。
- 独立预览完成自制 PNG/PDF 上传、断言、保存、发布、草稿解除引用后的版本保留、重启恢复和原件下载；无 Managed Binding 时视觉默认禁用。帮助中心教程和两张截图同步，详见 [实操证据](../help-center/evidence/meta-planner-vision-64880801.md)。
- 最终语法检查覆盖 45 个 Python 文件；Diff 检查、15 篇已注册帮助文章的截图校验均通过。71 个变更文本文件的敏感模式扫描仅命中基线已有的攻击测试哨兵，无新增密钥或本机绝对路径；21 个本轮 pytest 临时目录已清理，Runtime、构建产物和回执不纳入提交。
- 外部模型仅按本轮逐项授权调用；未批准 Proposal、未发布 Xpert、未操作共享栈。提交状态以实际 Git/PR 记录为准。

## 真实验收与流完整性修复

- 使用自有两页扫描 PDF，固定 Dataset v2、候选 r1、Gemini `google/gemini-2.5-flash` 和下游 `deepseek/deepseek-v4-flash-0731`，未使用 Judge、工具或模型自动重试。
- 首次 Planner 范围缺少 JSON 序列化桥接，视觉对象无法连接 Agent 字符串输入，候选被拒绝；追加授权后允许现有 `json_serialize`，得到通过预检的候选。未放宽端口类型。
- 首次真实评测 `xeval_run_98ebe501722e4df7a74c3731872370ad`：视觉两页及 2/2 锚点通过，但答案遗漏第二页，综合 75%。工作流结束正文与报告相同，排除报告截断。
- 离线证伪发现旧文本流未区分正常 stop、length、流内错误与缺少终止标记。最小修复增加 fail-closed 结束校验及不含正文的成功 checkpoint；不改 Prompt、token 上限或评分断言，不新增重试。
- 修复后复测 `xeval_run_db6724f73066450ca190c538ae1be259`：1/1 完成、0 失败、contains 和 workflow_vision_match 均 100%；两页数量 42/17 全部进入最终答案；Gemini 2 次、DeepSeek 1 次、工具 0 次，视觉实际 token 4234，不确定派发 0，耗时 33576 ms。
- 原始失败没有文本流终止回执，不能倒推其直接原因必然是断流；本次成功也不保证外部模型重复运行确定性。
- 累计授权调用 13 次：认证 1 次、两次 Planner 实例各 3 次、两次评测各 3 次。预算护栏保留原账本，未清零或超额。
- 修复后本地回归：文本流 30 条、Agent/Managed/视觉执行 86 条通过；预算护栏 MockTransport 测试通过。首次沙箱内测试启动无输出，停止后在正常独立环境重跑成功。最终全量复跑回执另行记录，不沿用修复前全绿结论。

## 最终验证命令

提交前最终验证（2026-09-07）：

- 已整合 `origin/main@1b280ed2`，测试源码提交 `a791881f`。帮助中心清单冲突保留视觉与 RAG 两篇文章；没有覆盖其他任务修改。
- Linux r6 全量：`6149 passed, 29 skipped, 7 warnings`，921.27 秒，exit 0。此前 r5 为整合主线主动中断，保留回执，不计通过。
- 合并后前端生产构建通过，保留既有大 bundle warning；视觉、工作流转换、附件能力、评测页和帮助中心 5 组共 59 条测试通过。
- 16 篇已注册帮助文章截图校验通过；最终 Diff 与提交范围检查排除 Runtime、SQLite、凭据、调用账本和构建产物。
- Sol 独立补充审查因额度限制中止，不计为通过的独立审查；已完成的主智能体审查与测试证据不以此替代。
- 最终回执：`.tmp-vision-linux/receipt-r6-final.json` 和 `output-r6-final.log`，完整源码归档 `final-r6.tar`；后续收尾仅修改本记录，不改变已测生产源码。

```text
python -m pytest -p pytest_asyncio.plugin -p anyio.pytest_plugin -p no:cacheprovider server/tests/ -q --tb=short -o faulthandler_timeout=180 --basetemp /workspace-r4/.pytest-full
npm.cmd run build
node scripts/verify-help-images.mjs
git diff --check
```

全量命令在本轮授权的独立离线 Linux 测试容器运行，前端命令在 `client` 目录运行。
最终完整回执与日志保存在本轮忽略目录 `.tmp-vision-linux/receipt-r4-final.json` 和
`.tmp-vision-linux/output-r4-final.log`，不纳入提交。
