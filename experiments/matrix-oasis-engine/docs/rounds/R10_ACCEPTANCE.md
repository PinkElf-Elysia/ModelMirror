# R10 验收记录

状态：R10.4 已验证，等待本地提交。

固定基线：`09f4cca4f1e02fe275ada17535597437cac3778d`

## 批次

- [x] R10.1 治理迁移（`60ce32eb49949f58ea1a67ee0cf8665e4ec588f4`）
- [x] R10.2 Marble环境Pipeline（`61f293ea3f5c3a3ecc457173d01b45e21162e0fd`）
- [x] R10.3 自动组装与缓存导入（`2d26d0018e4b83f2b2fc3778f6837d848cdb443b`）
- [x] R10.4 本地宿主与审批状态机（本批提交；SHA在R10.5记录）
- [ ] R10.5 Creator与Godot一键预览
- [ ] R10.6 真实资格与验收收口

## R10.1 证据

- 从`origin/main@09f4cca4f1e02fe275ada17535597437cac3778d`创建独立分支`codex/matrix-oasis-r10-prototype-builder`与worktree`C:\tmp\modelmirror-matrix-oasis-r10`；该主线包含R9合并提交。
- schema v10、active round、固定BASE、两个新workspace与R10 Godot前缀、精确Creator/host/test文件allowlist已同步；R1–R9与未知模块路径继续fail-closed。
- R10固定Marble `marble-1.1`、panorama/collider上限、loopback宿主、两道审批和不持久化prompt；只有三个精确provider adapter可联网。
- `npm.cmd ci --offline --ignore-scripts --no-audit --no-fund`安装110个锁定包；`npm.cmd prefix`与`npm.cmd ls --all`退出0。
- scope/boundary正反测试137/137通过；真实round/parent scope均checked=20/changed=20，boundary checked=931/tracked=926，`git diff --check`通过。
- 首次完整verify只因未注入`GODOT_BIN`在doctor前置按预期阻断；使用R4–R9已核验的仓外Godot 4.6.3 console executable后原命令15/15通过，Node 606/606、Godot R4–R7、Creator 247 modules build与HTTP smoke全绿。
- 本批未调用真实模型、Meshy或Marble，未读取供应商凭据，未启动父服务、Docker或共享栈。

## R10.2 证据

- 新增私有`@matrix-oasis/prototype-environment-pipeline@0.1.0-r10`，公开计划、Marble provider、环境物化和Bundle复验；使用不透明计划句柄，公开Bundle/report不保存prompt、operation/world ID、URL、密钥或原始响应。
- Provider固定`marble-1.1`纯文本payload、一次create、最多180次poll、一次world get与两次下载；只使用Node 24原生Fetch，不读环境变量、不redirect、不重试，正式资产host受固定候选约束。
- Bundle闭合绑定Scene/Blueprint/prompt SHA，固定两个相对文件；panorama检查PNG签名、chunk/CRC、2:1与尺寸上限，collider复用冻结R7 GLB安全门并记录结构/三角指标。
- `npm.cmd run verify:prototype-environment` 6/6通过，覆盖审批前零请求、text-only payload、timeout、429/402、redirect、响应/下载超限、恶意host、180次poll上限、PNG/GLB损坏、脱敏和20次字节稳定。
- 首次全量测试在受限沙箱中因既有R5-R9夹具无法写`C:\tmp`而失败；提升到既定一次性临时目录权限后，仅因shell未注入Godot前置出现doctor阻断。注入已核验的仓外Godot 4.6.3后，doctor 7/7且同一`npm.cmd test` 612/612通过。
- `npm.cmd ci --offline --ignore-scripts --no-audit --no-fund`安装111个锁定包；`npm.cmd ls --all`、boundary checked=940/tracked=931、round/parent scope及`git diff --check`通过。
- 最新14文件树执行完整`npm.cmd run verify`，15/15步骤通过：Node 612/612、Godot R4–R7全门、Creator 247 modules build与HTTP marker smoke均通过；R1–R9、Creator、Godot、examples与父仓相对本批HEAD零差异。
- 本批无真实模型、Meshy或Marble调用，无API Key读取，无供应商费用；没有修改R1-R9、Creator、Godot、examples或父仓。

## R10.3 证据

- 新增私有`@matrix-oasis/prototype-assembler@0.1.0-r10`；只调用冻结的Generation、Asset、Environment、Runtime与Scene Pack验证入口。Marble collider固定成为环境asset，R9 Kenney环境物化不进入输出；Meshy文件按Blueprint placement和zone声明顺序映射到固定四区4×2槽位。
- profile覆盖0/1/2个非环境brief成功与3个拒绝、1/4个zone成功与5个拒绝、32/33 placement及每zone 8/9边界；Scene Pack经公开R7 validator复验。20次并行组装的Scene Pack和report字节一致，输入Map/bytes不变且成功结果深冻结。
- cache importer绑定prompt SHA、模型、Blueprint、Asset/Environment Bundle和assembler版本；FileHandle、bigint identity、size/mtime/ctime、junction、换身、并发同目录、发布失败窄清理、rename后全文件回读和`current.json`最后替换均有回归。run不含prompt；Kenney规范化文件只作为R9 Asset Bundle复验证据保留，Scene Pack不引用。
- `npm.cmd run verify:prototype-assembly` 14/14通过，包含最大长度Blueprint placement ID到固定短Scene Pack ID的映射、真实Node CLI workspace解析与一次性`C:\tmp`事务发布；`node --check`、严格d.ts解析、`npm.cmd ls --all`、boundary checked=947/tracked=940、round/parent scope及`git diff --check`通过。
- 最新12文件树执行完整`npm.cmd run verify`，15/15步骤通过：Node 626/626、Godot R4–R7全门、Creator 247 modules build与HTTP marker smoke均通过。
- 当前真实R8/R9缓存已只读核对文件名、canonical脱敏report和hash结构；Marble真实Bundle尚不存在，完整三缓存导入按计划留到R10.6一次性资格之后。本批全部功能/事务测试使用本地合成输入，不调用模型、Meshy或Marble，不读取API Key。

## R10.4 证据

- 新增固定`127.0.0.1:43110`宿主、exact same-origin/JSON/HttpOnly SameSite会话门、64 KiB body和32 KiB纯文本prompt上限；API只公开readiness、静态diagnostic、审批摘要、状态和run标识，不返回凭据、供应商ID/URL或异常正文。
- 模型审批绑定prompt SHA、endpoint host、model、3次请求和1美元；环境/资产审批绑定Blueprint SHA、Marble固定有界操作及Meshy精确brief/任务/credits。状态`awaiting_model_approval→generating→awaiting_asset_approval→acquiring→normalizing→assembling→ready|failed`逐阶段可观察，重复/过期审批与并发run均fail closed。
- 内存发布入口不把prompt写盘；cache hit和重启恢复会重新复验Generation/Asset/Environment/Runtime/Scene Pack、reports、全部hash与GLB。损坏run被排除，失败保持上一份current；恢复run获得新的会话run ID并可沿同一路由launch。
- `npm.cmd run verify:prototype-host` 13/13、`npm.cmd run verify:prototype-assembly` 15/15、boundary正反74/74通过；真实无配置入口只启动loopback且readiness全false，未读取API Key、未调用模型/Meshy/Marble。
- 最新12文件树执行完整`npm.cmd run verify`，15/15步骤通过：Node 642/642、Godot R4–R7全门、Creator 247 modules build与HTTP marker smoke均通过；round scope checked=49/changed=40、boundary checked=950/tracked=947。
- R10.4实际Godot launch有意保持not-ready，留待R10.5 wrapper；普通测试全部使用注入式假操作、loopback和本地合成GLB，无外部费用或共享栈。

## 回退

本轮六批均可按逆序`git revert`。Git回退不删除仓外run、真实供应商资产或远程Marble world。
