# MCP Wave 26A：离线文件与确定性工具

## 当前结论

本单元在隔离分支中为两个候选建立了文件 sidecar 兼容契约；用户验收后仅 Calculator
晋级，ImageSorcery 继续 **staged、默认拒绝**：

| 目录 ID | 上游冻结 | staged 工具 | 状态 |
| --- | --- | --- | --- |
| `githejie-mcp-server-calculator` | 0.2.1 / `3dcaedcd58867206627d121092b401728db202da` / MIT | `calculate` | 用户验收通过，目录晋级 ready，并加入精确文件 sidecar allowlist |
| `sunriseapps-imagesorcery-mcp` | 0.12.0 / `2f77957a0671a5cf30d90285c7024ae229d86917` / MIT | `get_metainfo`、`resize`、`crop`、`rotate` | staged 验收通过但暂不放行；目录继续 planned/default-deny |

两项都进入 `mcp-files` 镜像内部 builder 集合，但只有 Calculator 进入生产
`file_proxy.py`、Compose `MCP_FILE_ALLOWED_ADAPTERS`、目录可执行 manifest 与
`FILE_PROJECTS` 空工作区配置。ImageSorcery 仍未进入上述任何生产入口，并保留在
`STAGED_FILE_ADAPTERS` 中；普通目录连接会在子进程启动前拒绝它。

## 固定边界

### Calculator

- 保留上游 0.2.1 的单一 `calculate(expression)` 产品身份。
- 只允许常量、`+ - * / // % **`、正负号、`pi/e/tau` 与固定数学函数。
- 表达式最多 256 字符、64 个 AST 节点、16 层；幂指数、底数和结果绝对值均
  有界，NaN/Infinity fail closed。
- 属性访问、import、任意函数、列表/字典/集合、推导式、变量、文件、网络与
  子进程全部不可发现。
- 工具为纯 `read`，不会创建工作区文件或产物。

### ImageSorcery

- 保留上游核心的图像元数据、缩放、裁剪和旋转身份；明确标注为独立兼容契约，
  不宣称运行了上游包。
- 客户端只提交 opaque `file_id` 和受限数值参数，不接受 `input_path`、
  `output_path`、URL、Header、env、命令或动态配置。
- 仅接收单帧 JPEG/PNG/WebP，输入不超过 16 MiB；宽高不超过 8192，输入/输出
  不超过 2500 万像素，产物不超过 32 MiB。
- 源文件只读；变换结果总是以服务端独占创建的 PNG 新文件写入受控产物目录，
  绝不覆盖输入。
- detect/find/OCR、模型下载、遥测、持久配置、prompt/resource、overlay、任意
  绘制与路径工具全部不开放。
- 三个变换工具均为 `artifact-create`，管理器不得自动重发；结果不明确时仍按
  `unknown_outcome` 处理。

## 本单元未晋级项

- `nameetp-pdfmux`：GitHub v1.8.7 与 PyPI 最新 1.8.2 不一致，且产品身份依赖
  有明确版本约束的多引擎 PyMuPDF 自愈链。当前不安装、不兼容模拟，继续 planned。
- `aimino-tech-opendocswork-mcp`：无固定 release，GPL-3.0，Office 读写和技能面过宽；
  在无法冻结窄工具与产物行为前继续 planned。
- FunASR、Skill Seekers、APKTool、Apple Health 等分别涉及模型下载/执行、网络抓取
  与向量库、逆向工程或健康敏感数据，不进入本低风险单元。

## Schema 冻结

- Calculator：`fd720b0ecc719751f3d7fcf5702a3d2c1f7e77073de249cd812c3753bed35a9f`
- ImageSorcery：`cba6ba696a976b5815e66c31b8ab02b47cf48a6d5bf7604d64ad8bb86b4bbfae`

所有工具顶层 Schema 均为 `additionalProperties=false`。

## 验收与回退

验收必须使用重新构建的独立 `mcp-files` 镜像，并验证：真实 UDS
`initialize/tools/list`、Schema、两轮代表调用、确定性 PNG、输入哈希不变、危险
参数/工具拒绝、客户端 timeout 后进程组回收、默认拒绝和精确清理。容器必须
`network=none`、只读根、UID/GID 65532、`cap_drop=ALL`、NNP，并使用临时 volume；
不得启动共享栈。

回退不涉及数据迁移：Calculator 需要从目录 ready 判定、`file_proxy.py`、Compose 精确
allowlist 和空工作区登记中移除，再恢复为 planned；ImageSorcery 只需删除 staged builder、
contract smoke、测试与本说明。两者都没有持久状态，回退不会改变既有文件或数据库。

## 隔离验收证据

2026-08-12 从本工作树重新构建 `modelmirror-mcp-files:wave26a-staged`，镜像
manifest 为
`sha256:9a67f3b9994fe1f49401af85f0b7c465163663361e4ee3bb95796202c5254873`。
构建期对 Wave 18A、18B、20 与 26A 的所有冻结 Schema 契约均重新校验通过。

运行时使用 `network=none`、只读根、UID/GID 65532、`cap_drop=ALL`、NNP、
128 PID、1 GiB、1.5 CPU 与 512 MiB `noexec` 临时目录。两个适配器各在两个
全新 UDS/official SDK 会话完成 initialize、tools/list、Schema 与代表调用；
ImageSorcery 三个 PNG 在两轮中逐字节一致，源 PNG 哈希不变。未公开工具、路径、
URL、额外参数、代码执行表达式和资源越界均 fail closed；ImageSorcery 调用在
1 ms 客户端 timeout 后，UDS 关闭触发整个子进程组回收。精确 smoke 容器已由
`--rm` 删除且复核无同名残留。

成功行：

```text
adapter=githejie-mcp-server-calculator rounds=2 artifacts=0
adapter=sunriseapps-imagesorcery-mcp rounds=2 artifacts=3
wave26_file_runtime_smoke=ok network=none source_immutable=true deterministic=true default_deny=true timeout=verified cleanup=verified
```

补充回归结果：

- Wave 26A 定向测试：`8 passed`。
- 受影响的 Wave 18A/18B/20、目录与文件工作区集合：`120 passed, 2 skipped`。
- 按 CI 约束使用 Node 22 的完整后端命令：`2897 passed, 29 skipped`。
- Agency Worker：7 个桥接测试与全部上游兼容测试通过。
- 前端 `typecheck` 与生产构建通过；Vitest 为 `233 passed, 1 failed`，唯一失败是
  本基线已有的 `NodePalette.test.ts` 过期断言（实现包含
  `vision_understanding`，测试仍只接受两个知识节点），与本批文件 sidecar 变更无交集。
- `docker compose config --quiet` 通过。

以上证据与用户验收支持 Calculator 晋级 ready。ImageSorcery 当前是明确披露的窄
兼容契约而非上游包运行时，继续 planned/default-deny；不得加入生产 allowlist。
