# MCP 第 18B 批：受控文件分析

## 任务契约

- 目标：在现有 `mcp-files` sidecar 中适配 llm-context、Excel MCP 与 Dingo 的低风险本地子集。
- 单一验收单元：运行时 facade、目录状态、工作区格式、镜像、测试与本文档必须一起一致；因此会跨越
  5 个以上文件，但不包含无关重构。
- 不新增公共 API、数据迁移、通用 MCP 执行器、网络出口、任意命令、任意路径或动态 endpoint。
- 风险：中等。输入文件不可信，Excel 包可能含宏/外链/公式，Dingo 上游同时包含 LLM 和云数据源。
- 发布边界：三项已通过真实隔离镜像与用户验收，现已分别晋级 `ready` 并加入生产
  `MCP_FILE_ALLOWED_ADAPTERS` 的精确 ID 列表。

## 固定上游

| 目录 ID | 审阅上游 | 许可证 | 固定兼容边界 |
|---|---|---|---|
| `cyberchitta-llm-context-py` | llm-context 0.6.4 / `6de16c22458d0e145ac6c440ef732849e7ae3d9f` | Apache-2.0 | 仅封存工作区预览与 outline 产物 |
| `haris-musa-excel-mcp-server` | v0.1.8 / `f51340ecd5778952405044b203d3a2d4c8a46833` | MIT | 元数据、范围读取、输出副本写入 |
| `dataeval-dingo` | v2.5.0 / `c3674f903f88043aa24bf99d21d557fa966ab23f` | Apache-2.0 | 固定本地规则组，不加载 LLM/Agent/云数据源 |

## 安全边界

- llm-context 上游的 `root_path`、动态规则、`lc_missing`、`lc_changed`、剪贴板和项目初始化全部关闭。
  facade 只遍历当前封存工作区中的受支持文本/代码文件，并生成有界预览或 Markdown outline 产物。
- Excel 只接受 `.xlsx`。包内 VBA、宏内容类型、外部关系和 externalLinks 均 fail closed；读取只返回
  缓存值且不执行公式，生成输出副本时则拒绝任何源公式和客户端公式字符串。写入只基于源文件生成
  新的服务端登记副本，绝不覆盖输入。客户端不能提交 filepath、URI、图表、透视表、格式、宏或外部链接。
- Dingo 只接受本地 JSONL/JSON/CSV/TXT，固定实现已核对的 `RuleContentNull`、`RuleColonEnd` 与
  `RuleSpecialCharacter`。`evaluation_type`、LLM、Prompt、Agent、Hugging Face、S3、SQL、API Key、
  自定义规则和任意 kwargs 均不可发现。
- 三项完全断网，输入只读，产物有大小/记录数上限；未知工具、未知字段和 Schema 漂移必须拒绝。

## 验收与回退

验收必须包含固定版本/Schema、真实 UDS `initialize`/`tools/list`、代表调用、未知参数、恶意 Excel
包、LLM/路径/URL 不可发现、超时、断开、进程回收、产物登记和根目录清理。随后运行目录计数、后端
定向测试、前端构建、Compose 解析、生成器 `--check` 与 `git diff --check`。

回退不需要迁移：从 `BUILDERS`、工作区项目集合和镜像 COPY 中移除三个精确 ID/模块，恢复生成器的
原 planned 元数据并断开临时会话。现有 Wave 3、18A 文件工作区、共享卷格式和产物索引保持不变。

## 隔离验收证据

- staged 镜像：`modelmirror-mcp-files:wave18b-staged`，manifest list
  `sha256:09d99b2ab0960e2094b3dfaabb9902a70e840b21852a48933f5579b73a2b8402`。
- 固定 Schema SHA-256：llm-context `f4978faaad49bc6d1a0ae9a3ba8da07dd404419e17049ff4266192014e42ebc7`；
  Excel `81342cfe381afddab1f646ddab181b35d6eb767c4805f4191695f53f0be6f1f8`；
  Dingo `0a2f5e40c241efc77ba2b0728b550c904241b7e5ef2d080deca3e70ba9787b67`。
- 真实 runtime smoke 在 `network=none`、只读根、UID/GID 65532、`cap_drop=ALL`、
  `no-new-privileges` 下完成每项两轮 UDS `initialize`、`tools/list`、代表调用、未知参数拒绝、
  源文件不可变、产物字节确定、精确 allowlist 拒绝、超时与进程/根目录清理；最终输出为
  `wave18b_file_runtime_smoke=ok ... timeout=verified cleanup=verified`。
- 本批运行的是 ModelMirror 独立兼容 facade，不是三个上游 MCP 进程；该身份边界会保留在目录说明中。
- 用户已确认验收；三个精确 ID 已进入默认 allowlist，未扩大到任何其他文件或数据库适配器。
